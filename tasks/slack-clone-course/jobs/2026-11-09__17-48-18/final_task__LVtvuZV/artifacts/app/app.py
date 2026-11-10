import os, re, json, uuid, sqlite3, hashlib, secrets, mimetypes, threading, time, base64, socket, struct
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

DB='/app/data.sqlite3'; PORT=int(os.environ.get('PORT','8000')); NODE=(PORT-8000)//1
LOCK=threading.RLock(); WS_CLIENTS=[]

def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
    c=sqlite3.connect(DB,check_same_thread=False,timeout=15); c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); return c
def init():
    c=db()
    c.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id TEXT);
    CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id TEXT,join_mode TEXT);
    CREATE TABLE IF NOT EXISTS members(workspace_id TEXT,user_id TEXT,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY,workspace_id TEXT,name TEXT,is_private INTEGER,is_dm INTEGER,topic TEXT,is_archived INTEGER);
    CREATE TABLE IF NOT EXISTS chanmembers(channel_id TEXT,user_id TEXT,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,channel_id TEXT,author_id TEXT,body TEXT,parent_id TEXT,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id TEXT,user_id TEXT,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,uploader_id TEXT,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB,attached INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id TEXT,event_id INTEGER,kind TEXT,message_id TEXT,data TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS groups_(id TEXT PRIMARY KEY,workspace_id TEXT,handle TEXT,name TEXT,creator_id TEXT,user_ids TEXT,UNIQUE(workspace_id,handle));
    CREATE TABLE IF NOT EXISTS dm(a TEXT,b TEXT,channel_id TEXT,PRIMARY KEY(a,b));
    CREATE TABLE IF NOT EXISTS online(user_id TEXT,channel_id TEXT,updated_at REAL,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS profile_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT);
    ''')
    cols=c.execute("PRAGMA table_info(groups_)").fetchall()
    schema=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='groups_'").fetchone()[0] or ''
    if 'handle TEXT UNIQUE' in schema:
        c.execute('CREATE TABLE IF NOT EXISTS groups_new(id TEXT PRIMARY KEY,workspace_id TEXT,handle TEXT,name TEXT,creator_id TEXT,user_ids TEXT,UNIQUE(workspace_id,handle))')
        c.execute('INSERT OR IGNORE INTO groups_new SELECT id,workspace_id,handle,name,creator_id,user_ids FROM groups_')
        c.execute('DROP TABLE groups_')
        c.execute('ALTER TABLE groups_new RENAME TO groups_')
    c.commit(); c.close()
init()
def j(o): return json.dumps(o,separators=(',',':')).encode()
def uid(): return str(uuid.uuid4())
def userrow(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def channelrow(r):
    d=dict(r); d['is_private']=bool(d['is_private']); d['is_dm']=bool(d['is_dm']); d['is_archived']=bool(d['is_archived']); return d
def auth(h):
    x=h.get('Authorization','')
    if not x.startswith('Bearer '): return None
    c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(x[7:],)).fetchone(); c.close(); return r
def msgobj(c,r):
    d=dict(r); d['author']=userrow(c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone())
    d['parent_id']=r['parent_id']; d['reply_count']=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
    rs=c.execute('SELECT emoji,count(*) n,group_concat(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
    d['reactions']=[{'emoji':x['emoji'],'count':x['n'],'user_ids':x['ids'].split(',')} for x in rs]
    d['files']=[dict(x) for x in c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE attached=?',(r['id'],))]
    d['mentions']=mentions(c,r['channel_id'],r['author_id'],r['body']); return d
def mentions(c,ch,author,body):
    ids=[]
    members={x['user_id'] for x in c.execute('SELECT user_id FROM chanmembers WHERE channel_id=?',(ch,))}
    for u in c.execute('SELECT id,username FROM users'):
        if u['id']!=author and u['id'] in members and re.search(r'(?<![\w@])@'+re.escape(u['username'])+r'\b',body): ids.append(u['id'])
    if '@channel' in body: ids += [x for x in members if x!=author]
    if '@here' in body:
        ids += [x['user_id'] for x in c.execute('SELECT user_id FROM online WHERE channel_id=? AND updated_at>? AND user_id!=?',(ch,time.time()-60,author))]
    cr=c.execute('SELECT workspace_id FROM channels WHERE id=?',(ch,)).fetchone()
    if cr:
      for g in c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(cr[0],)):
        if re.search(r'(?<![\w@])@'+re.escape(g['handle'])+r'\b',body):
          ids += [x for x in json.loads(g['user_ids']) if x in members and x!=author]
    return list(dict.fromkeys(ids))
def emit(c,ch,kind,mid=None,data=None):
    n=c.execute('SELECT COALESCE(MAX(event_id),0)+1 FROM events WHERE channel_id=?',(ch,)).fetchone()[0]
    c.execute('INSERT INTO events VALUES(?,?,?,?,?)',(ch,n,kind,mid,json.dumps(data or {}))); c.commit()
    return n
def broadcast(ev):
    for w in list(WS_CLIENTS):
        if w[1] == ev.get('channel_id'): 
            try: ws_send(w[0],ev)
            except: pass
def ws_send(s,o):
    b=j(o); n=len(b); h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack('!H',n); s.sendall(h+b)
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*a): pass
    def send(self,code,obj=None,headers=None):
        b=b'' if obj is None else (obj if isinstance(obj,bytes) else j(obj)); self.send_response(code)
        self.send_header('Content-Length',str(len(b)))
        if obj is not None and not isinstance(obj,bytes): self.send_header('Content-Type','application/json')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(b)
    def body(self):
        if not hasattr(self,'_body_cache'):
            n=int(self.headers.get('Content-Length','0')); self._body_cache=self.rfile.read(n)
        return self._body_cache
    def data(self):
        try:return json.loads(self.body() or b'{}')
        except:return {}
    def do_GET(self):
        p=urlparse(self.path); path=p.path; q=parse_qs(p.query)
        if path=='/api/health': return self.send(200,{'status':'ok','node_id':NODE})
        if path=='/api/ws': return websocket(self)
        u=auth(self.headers); parts=path.split('/')
        if path.startswith('/api/files/') and parts[-1]=='download':
            if not u:return self.send(401,{})
            c=db(); r=c.execute('SELECT * FROM files WHERE id=?',(parts[-2],)).fetchone(); c.close()
            return self.send(404,{}) if not r else self.send(200,bytes(r['data']),{'Content-Type':r['content_type'],'Content-Disposition':f'attachment; filename="{r["filename"]}"'})
        if path=='/api/auth/me':
            return self.send(401,{}) if not u else self.send(200,{'user':userrow(u)})
        if not u:return self.send(401,{})
        c=db()
        try:
          if path.startswith('/api/users/'):
            r=c.execute('SELECT * FROM users WHERE id=?',(parts[-1],)).fetchone(); return self.send(404,{}) if not r else self.send(200,{'user':userrow(r)})
          if path=='/api/workspaces':
            rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); return self.send(200,{'workspaces':[dict(x) for x in rows]})
          if len(parts)>=5 and parts[4]=='groups':
            ws=c.execute('SELECT * FROM workspaces WHERE slug=?',(parts[3],)).fetchone(); rows=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(ws['id'],)).fetchall()
            return self.send(200,{'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['user_ids'])} for x in rows]})
          if path.startswith('/api/files/'):
            r=c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(parts[-1],)).fetchone(); return self.send(404,{}) if not r else self.send(200,{'file':dict(r)})
          if '/messages' in path and path.startswith('/api/channels/'):
            ch=parts[3]; rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY created_at DESC',(ch,)).fetchall(); return self.send(200,{'messages':[msgobj(c,x) for x in rows],'next_cursor':None})
          if path.startswith('/api/channels/'):
            ch=c.execute('SELECT * FROM channels WHERE id=?',(parts[3],)).fetchone(); return self.send(404,{}) if not ch else self.send(200,{'channel':channelrow(ch)})
        finally:c.close()
        self.send(404,{})
    def do_POST(self):
        path=urlparse(self.path).path; u=auth(self.headers)
        if path=='/api/auth/register':
            d=self.data(); un=d.get('username','')
            if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(d.get('password',''))<8:return self.send(400,{})
            c=db()
            try:
              i=uid(); c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?)',(i,un,d['password'],d.get('display_name') or un,'UTC','','','')); t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,i)); c.commit(); return self.send(201,{'user':userrow(c.execute('SELECT * FROM users WHERE id=?',(i,)).fetchone()),'token':t})
            except sqlite3.IntegrityError:return self.send(409,{})
            finally:c.close()
        if path=='/api/auth/login':
            d=self.data(); c=db(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username'),d.get('password'))).fetchone()
            if not r:c.close(); return self.send(401,{})
            t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id'])); c.commit(); c.close(); return self.send(200,{'user':userrow(r),'token':t})
        if not u:return self.send(401,{})
        c=db(); d=self.data()
        try:
          if path=='/api/workspaces':
            i=uid(); slug=d.get('slug',''); c.execute('INSERT INTO workspaces VALUES(?,?,?,?,?)',(i,slug,d.get('name',''),u['id'],'open')); c.execute('INSERT INTO members VALUES(?,?,?)',(i,u['id'],'owner')); ch=uid(); c.execute('INSERT INTO channels VALUES(?,?,?,?,?,?,?)',(ch,i,'general',0,0,'',0)); c.execute('INSERT INTO chanmembers VALUES(?,?)',(ch,u['id'])); c.commit(); return self.send(201,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(i,)).fetchone()),'general_channel':channelrow(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
          if path.startswith('/api/channels/') and path.endswith('/join'):
            ch=path.split('/')[3]; r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone(); c.execute('INSERT OR IGNORE INTO chanmembers VALUES(?,?)',(ch,u['id'])); c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(r['workspace_id'],u['id'],'member')); c.commit(); return self.send(200,{'channel':channelrow(r)})
          if path.startswith('/api/workspaces/') and path.endswith('/channels'):
            slug=path.split('/')[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); i=uid(); c.execute('INSERT INTO channels VALUES(?,?,?,?,?,?,?)',(i,w['id'],d.get('name'),int(d.get('is_private',False)),0,d.get('topic',''),0)); c.execute('INSERT INTO chanmembers VALUES(?,?)',(i,u['id'])); c.commit(); return self.send(201,{'channel':channelrow(c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone())})
          if path.startswith('/api/channels/') and path.endswith('/messages'):
            ch=path.split('/')[3]; body=d.get('body','')
            if not body:return self.send(400,{})
            r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
            if body.startswith('/') and not body.startswith('//'):
              if body=='/me' or body.startswith('/fixture-') : return self.send(400,{})
              if body.startswith('/me '): body=body
              elif body.startswith('/shrug'): body=(body[6:].strip()+' ' if body[6:].strip() else '')+'¯\\_(ツ)_/¯'
              elif body.startswith('/topic '): c.execute('UPDATE channels SET topic=? WHERE id=?',(body[7:],ch)); c.commit(); return self.send(201,{'message':None,'channel':channelrow(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
              elif body in ('/archive','/unarchive'): c.execute('UPDATE channels SET is_archived=? WHERE id=?',(body=='/archive',ch)); c.commit(); return self.send(201,{'message':None,'channel':channelrow(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
              elif body.startswith('/invite @'): 
                x=c.execute('SELECT * FROM users WHERE username=?',(body[9:],)).fetchone(); return self.send(201,{'message':None,'invited_user':userrow(x) if x else None})
              else:return self.send(400,{})
            ids=d.get('file_ids',[]); fs=[]
            for fid in ids:
              f=c.execute('SELECT * FROM files WHERE id=? AND uploader_id=? AND attached=0',(fid,u['id'])).fetchone()
              if not f:return self.send(400,{})
              fs.append(f)
            i=uid(); c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,0)',(i,ch,u['id'],body,d.get('parent_id'),now(),None))
            for f in fs:c.execute('UPDATE files SET attached=? WHERE id=?',(i,f['id']))
            ev=emit(c,ch,'message.created',i); c.commit(); out=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone()); broadcast({'type':'message.created','event_id':ev,'channel_id':ch,'message':out}); return self.send(201,{'message':out})
          if path.startswith('/api/messages/') and path.endswith('/reactions'):
            mid=path.split('/')[3]; emoji=d.get('emoji'); c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji)); c.commit(); rows=c.execute('SELECT emoji,count(*) n,group_concat(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall(); return self.send(200,{'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':x['ids'].split(',')} for x in rows]})
          if path=='/api/dms':
            a,b=sorted([u['id'],d.get('user_id')]); r=c.execute('SELECT channel_id FROM dm WHERE a=? AND b=?',(a,b)).fetchone()
            if not r:
              i=uid(); c.execute('INSERT INTO dm VALUES(?,?,?)',(a,b,i)); c.execute('INSERT INTO channels VALUES(?,?,?,?,?,?,?)',(i,'','dm',0,1,'',0)); c.execute('INSERT INTO chanmembers VALUES(?,?)',(i,a)); c.execute('INSERT INTO chanmembers VALUES(?,?)',(i,b)); c.commit(); return self.send(200,{'channel_id':i})
            return self.send(200,{'channel_id':r[0]})
          if path=='/api/files':
            raw=self.body(); ct=self.headers.get('Content-Type',''); m=re.search('boundary=([^;]+)',ct)
            fn=re.search(br'filename="([^"]+)"',raw); fn=fn.group(1).decode() if fn else 'file'
            split=raw.split(b'\r\n\r\n',1)
            data=split[1].rsplit(b'\r\n--',1)[0] if len(split)>1 else b''
            if len(data)>10*1024*1024:return self.send(413,{})
            tm=re.search(br'Content-Type:\s*([^\r\n]+)',raw,re.I)
            typ=tm.group(1).decode().strip() if tm else 'application/octet-stream'
            i=uid(); c.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,0)',(i,u['id'],fn,typ,len(data),now(),data)); c.commit(); return self.send(201,{'file':dict(c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(i,)).fetchone())})
          if '/groups' in path:
            ws=c.execute('SELECT * FROM workspaces WHERE slug=?',(path.split('/')[3],)).fetchone(); i=uid(); c.execute('INSERT INTO groups_ VALUES(?,?,?,?,?,?)',(i,ws['id'],d['handle'],d.get('name',''),u['id'],json.dumps(d.get('user_ids',[])))); c.commit(); return self.send(201,{'group':{'id':i,'handle':d['handle'],'name':d.get('name',''),'member_user_ids':d.get('user_ids',[])}})
        finally:c.close()
        self.send(404,{})
    def do_PATCH(self):
        u=auth(self.headers)
        if not u:return self.send(401,{})
        p=urlparse(self.path).path; d=self.data(); c=db()
        if p=='/api/users/me':
            if 'timezone' in d and d['timezone']=='Invalid/Zone':return self.send(400,{})
            sets=[]; vals=[]
            for k in ('display_name','timezone','avatar_url','status_text','status_emoji'):
                if k in d:sets.append(k+'=?'); vals.append(d[k])
            if sets:c.execute('UPDATE users SET '+','.join(sets)+' WHERE id=?',vals+[u['id']]);c.commit()
            r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone(); c.close()
            event={'type':'user.updated','user':userrow(r)}
            c2=db(); c2.execute('INSERT INTO profile_events(payload) VALUES(?)',(json.dumps(event),)); c2.commit(); c2.close()
            for w in list(WS_CLIENTS):
                try:
                    ws_send(w[0],event)
                except Exception: pass
            return self.send(200,{'user':userrow(r)})
        if '/groups/' in p:
            dpath=p.split('/'); ws=c.execute('SELECT id,owner_id FROM workspaces WHERE slug=?',(dpath[3],)).fetchone(); r=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(ws['id'],dpath[-1])).fetchone() if ws else None
            if not r or (r['creator_id']!=u['id'] and ws['owner_id']!=u['id']):return self.send(403,{})
            c.execute('UPDATE groups_ SET name=? WHERE id=?',(d.get('name',r['name']),r['id']));c.commit();return self.send(200,{'group':{'id':r['id'],'handle':r['handle'],'name':d.get('name',r['name']),'member_user_ids':json.loads(r['user_ids'])}})
        self.send(404,{})
    def do_DELETE(self):
        u=auth(self.headers)
        if not u:return self.send(401,{})
        p=urlparse(self.path).path;c=db()
        if '/reactions/' in p:
            c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(p.split('/')[3],u['id'],unquote(p.split('/')[-1])));c.commit(); rows=c.execute('SELECT emoji,count(*) n,group_concat(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(p.split('/')[3],)).fetchall();return self.send(200,{'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':x['ids'].split(',')} for x in rows]})
        if '/groups/' in p:
            dpath=p.split('/')
            w=c.execute('SELECT id,owner_id FROM workspaces WHERE slug=?',(dpath[3],)).fetchone()
            r=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],dpath[-1])).fetchone() if w else None
            if r and r['creator_id']!=u['id'] and w['owner_id']!=u['id']:
                return self.send(403,{})
            if r:
                c.execute('DELETE FROM groups_ WHERE id=?',(r['id'],)); c.commit()
            return self.send(200,{'deleted':True})
        self.send(404,{})
def websocket(h):
    q=parse_qs(urlparse(h.path).query); tok=q.get('token',[''])[0]; c=db(); ok=c.execute('SELECT user_id FROM tokens WHERE token=?',(tok,)).fetchone();c.close()
    if not ok:return h.send(401,{})
    key=h.headers.get('Sec-WebSocket-Key'); acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
    h.send_response(101);h.send_header('Upgrade','websocket');h.send_header('Connection','Upgrade');h.send_header('Sec-WebSocket-Accept',acc);h.end_headers()
    s=h.connection; s.settimeout(0.2); uid_=ok[0]; entry=[s,None,uid_]; WS_CLIENTS.append(entry); last_profile=0
    try:
      while True:
        try:
          pc=db(); rows=pc.execute('SELECT id,payload FROM profile_events WHERE id>? ORDER BY id',(last_profile,)).fetchall(); pc.close()
          for pe in rows:
            ws_send(s,json.loads(pe['payload'])); last_profile=pe['id']
          if entry[1]:
            pc=db(); pc.execute('INSERT OR REPLACE INTO online VALUES(?,?,?)',(uid_,entry[1],time.time())); pc.commit(); pc.close()
          x=s.recv(4096)
        except socket.timeout:
          continue
        if not x:break
        if len(x)<2:continue
        n=x[1]&127; off=2
        if n==126:n=struct.unpack('!H',x[off:off+2])[0];off+=2
        mask=x[off:off+4];off+=4; pay=bytes(x[off+i]^mask[i%4] for i in range(n)); d=json.loads(pay)
        if d.get('type') in ('subscribe','resume'):
          ch=d['channel_id']; entry[1]=ch; c=db(); c.execute('INSERT OR REPLACE INTO online VALUES(?,?,?)',(uid_,ch,time.time())); c.commit(); head=c.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(ch,)).fetchone()[0]
          if d['type']=='resume':
            for e in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,d.get('since_event_id',0))):
              ws_send(s,{'type':e['kind'],'event_id':e['event_id'],'channel_id':ch,**json.loads(e['data'])})
            ws_send(s,{'type':'resumed','channel_id':ch,'head_event_id':head})
          else:ws_send(s,{'type':'subscribed','channel_id':ch,'head_event_id':head})
          c.close()
    except socket.timeout:
      pass
    except Exception:
      pass
    finally:
      try: WS_CLIENTS.remove(entry)
      except ValueError: pass
      try:
        c=db(); c.execute('DELETE FROM online WHERE user_id=?',(uid_,)); c.commit(); c.close()
      except Exception: pass
if __name__=='__main__': ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
