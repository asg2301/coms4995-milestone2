import os,sys,json,re,sqlite3,secrets,hashlib,base64,datetime,threading,time,socket,struct,fcntl,contextlib
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/huddle.db'; PORT=int(os.getenv('PORT','8000')); NODE=PORT-8000
db_lock=threading.RLock(); clients=[]; irc_clients=[]; online_users=set(); irc_nicks=set()

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
@contextlib.contextmanager
def write_lock():
 f=open('/app/huddle.write.lock','w')
 fcntl.flock(f,fcntl.LOCK_EX)
 try: yield
 finally: fcntl.flock(f,fcntl.LOCK_UN); f.close()
def conn():
 c=sqlite3.connect(DB,timeout=30.0,check_same_thread=False)
 c.row_factory=sqlite3.Row
 c.execute('PRAGMA busy_timeout=30000')
 c.execute('PRAGMA synchronous=NORMAL')
 return c
def init():
 with db_lock:
  c=conn()
  c.executescript('''PRAGMA journal_mode=WAL;
  CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
  CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
  CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
  CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
  CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
  CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
  CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,created_at TEXT,PRIMARY KEY(channel_id,event_id));
  CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,user_id INTEGER,pinned_at TEXT);
  CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,uses INTEGER DEFAULT 0,max_uses INTEGER DEFAULT 1,expires_at REAL);
  CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
  CREATE TABLE IF NOT EXISTS readstate(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
  CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB,attached INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS attachments(message_id INTEGER,file_id TEXT PRIMARY KEY);
  CREATE TABLE IF NOT EXISTS presence(user_id INTEGER PRIMARY KEY);
  CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT);
  '''); c.commit()
  try: c.execute('ALTER TABLE invites ADD COLUMN expires_at REAL'); c.commit()
  except sqlite3.OperationalError: pass
  try: c.execute('ALTER TABLE files ADD COLUMN attached INTEGER DEFAULT 0'); c.commit()
  except sqlite3.OperationalError: pass
  c.close()
def channel(r):
 d=dict(r); d['is_private']=bool(d.get('is_private')); d['is_dm']=bool(d.get('is_dm')); d['is_archived']=bool(d.get('is_archived')); return d
def cursor_encode(kind, ident):
 return base64.urlsafe_b64encode(json.dumps({'k':kind,'id':int(ident)}).encode()).decode().rstrip('=')
def cursor_decode(raw, kind):
 try:
  x=json.loads(base64.urlsafe_b64decode(raw+'='*((4-len(raw)%4)%4)).decode())
  if x.get('k')!=kind: raise ValueError()
  return int(x['id'])
 except Exception: raise ValueError()
def pageargs(path):
 from urllib.parse import parse_qs
 q=parse_qs(path.split('?',1)[1] if '?' in path else '')
 try: lim=int(q.get('limit',['50'])[0])
 except: raise ValueError()
 if lim<1 or lim>200: raise ValueError()
 return lim,q.get('cursor',[None])[0]
def user(r):
 return {'id':r['id'],'username':r['username'],'display_name':r['display_name'],'timezone':r['timezone'],'avatar_url':r['avatar_url'],'status_text':r['status_text'],'status_emoji':r['status_emoji']}
def auth(h):
 x=h.get('Authorization','')
 if not x.startswith('Bearer '): return None
 c=conn(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(x[7:],)).fetchone(); c.close(); return r
def js(h,s=200): h.send_response(s); h.send_header('Content-Type','application/json'); h.end_headers(); h.wfile.write(json.dumps(h._out).encode())
def group_obj(r):
 return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['user_ids'] or '[]')}
def resolve_mentions(c, channel_id, author_id, body):
 members=c.execute('SELECT u.id,u.username FROM cmembers x JOIN users u ON u.id=x.user_id WHERE x.channel_id=?',(channel_id,)).fetchall()
 by={x['username']:x['id'] for x in members}; out=[]
 if re.search(r'(?<![A-Za-z0-9_])@here(?![A-Za-z0-9_])',body): out += [x['id'] for x in members if x['id']!=author_id and x['id'] in [z['user_id'] for z in c.execute('SELECT user_id FROM presence').fetchall()]]
 if re.search(r'(?<![A-Za-z0-9_])@channel(?![A-Za-z0-9_])',body):
  out += [x['id'] for x in members if x['id']!=author_id]
 for m in re.finditer(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)(?![A-Za-z0-9_])',body):
  ident=by.get(m.group(1))
  if ident and ident!=author_id and ident not in out: out.append(ident)
 return out
def read_state(c, uid, cid):
 row=c.execute('SELECT last_read_event_id FROM readstate WHERE user_id=? AND channel_id=?',(uid,cid)).fetchone()
 last=row['last_read_event_id'] if row else 0
 ev=c.execute("SELECT e.event_id,e.payload FROM events e WHERE e.channel_id=? AND e.event_id>? AND e.kind IN ('message.created','message.reply') ORDER BY e.event_id",(cid,last)).fetchall()
 unread=mention=0
 for e in ev:
  try:
   mo=json.loads(e['payload']).get('message',{})
   if mo.get('author_id')!=uid:
    unread+=1
    if uid in mo.get('mentions',[]): mention+=1
  except: pass
 return {'channel_id':int(cid),'last_read_event_id':last,'unread_count':unread,'mention_count':mention}
def msgobj(c,r):
 a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 rx=[]
 for x in c.execute('SELECT emoji,COUNT(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):
  ids=[z[0] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=?',(r['id'],x['emoji']))]
  rx.append({'emoji':x['emoji'],'count':x['n'],'user_ids':ids})
 replies=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[dict(x) for x in c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id IN (SELECT file_id FROM attachments WHERE message_id=?)',(r['id'],)).fetchall()],'reactions':rx,'mentions':resolve_mentions(c,r['channel_id'],r['author_id'],r['body'])}
def emit(c,cid,kind,payload):
 n=c.execute('SELECT COALESCE(MAX(event_id),0)+1 FROM events WHERE channel_id=?',(cid,)).fetchone()[0]
 c.execute('INSERT INTO events VALUES(?,?,?,?,?)',(cid,n,kind,json.dumps(payload),now())); c.commit()
 frame={'type':kind,'event_id':n,'channel_id':cid}; frame.update(payload)
 for q in list(clients):
  try:
   if q[0]==cid: ws_send(q[1],frame)
  except: pass
 for ic in list(irc_clients):
  try:
   if ic.get('channel_id')==cid:
    ic['sock'].sendall((':web!user@huddle PRIVMSG '+ic['name']+' :'+str(payload.get('message',{}).get('body',''))+'\r\n').encode())
  except Exception:
   try: irc_clients.remove(ic)
   except: pass
 return n
def ws_send(s,x):
 b=json.dumps(x).encode(); s.sendall(b'\x81'+(bytes([len(b)]) if len(b)<126 else b'\x7e'+struct.pack('>H',len(b)))+b)
class H(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def read(self):
  n=int(self.headers.get('Content-Length','0')); raw=self.rfile.read(n) or b''
  if self.headers.get('Content-Type','').startswith('application/json'):
   try:return json.loads(raw or b'{}')
   except:return {}
  return {'_raw':raw}
 def out(self,x,s=200): self._out=x; js(self,s)
 def req(self):
  p=self.path.split('?',1)[0]; return p
 def websocket(self):
  from urllib.parse import urlparse,parse_qs
  tok=parse_qs(urlparse(self.path).query).get('token',[''])[0]
  if tok.startswith('Bearer '): tok=tok[7:]
  c=conn(); r=c.execute('SELECT user_id FROM tokens WHERE token=?',(tok,)).fetchone(); c.close()
  if not r:
   self.send_response(401); self.end_headers(); return
  key=self.headers.get('Sec-WebSocket-Key','')
  accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101,'Switching Protocols'); self.send_header('Upgrade','WebSocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
  online_users.add(r['user_id']); pc=conn(); pc.execute('INSERT OR REPLACE INTO presence(user_id) VALUES(?)',(r['user_id'],)); pc.commit(); pc.close(); self.request.settimeout(0.4); subscribed=set(); item=None; seen={}; gseen=0
  def recv():
   h=self.request.recv(2)
   if len(h)<2:return None
   n=h[1]&127
   if n==126:n=struct.unpack('>H',self.request.recv(2))[0]
   elif n==127:n=struct.unpack('>Q',self.request.recv(8))[0]
   mask=self.request.recv(4); data=self.request.recv(n)
   return json.loads(bytes(v^mask[i%4] for i,v in enumerate(data)).decode())
  try:
   while True:
    ob=None
    try: ob=recv()
    except socket.timeout: pass
    if ob is not None:
     cid=int(ob.get('channel_id',0)); typ=ob.get('type')
     if typ=='subscribe':
      subscribed.add(cid); c=conn(); head=c.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(cid,)).fetchone()[0]; c.close(); seen[cid]=head; item=(cid,self.request); clients.append(item); ws_send(self.request,{'type':'subscribed','channel_id':cid,'head_event_id':head})
     elif typ=='resume':
      since=int(ob.get('since_event_id',0)); c=conn(); rows=c.execute('SELECT event_id,kind,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); head=c.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(cid,)).fetchone()[0]
      for x in rows:
       z={'type':x['kind'],'event_id':x['event_id'],'channel_id':cid}; z.update(json.loads(x['payload'])); ws_send(self.request,z)
      c.close(); seen[cid]=head; ws_send(self.request,{'type':'resumed','channel_id':cid,'head_event_id':head})
    gc=conn(); grows=gc.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(gseen,)).fetchall(); gc.close()
    for ge in grows:
     try: ws_send(self.request,json.loads(ge['payload']))
     except: pass
     gseen=ge['id']
    for cid in list(subscribed):
     c=conn(); rows=c.execute('SELECT event_id,kind,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,seen.get(cid,0))).fetchall(); c.close()
     for x in rows:
      z={'type':x['kind'],'event_id':x['event_id'],'channel_id':cid}; z.update(json.loads(x['payload'])); ws_send(self.request,z); seen[cid]=x['event_id']
  except Exception: pass
  finally:
   try:
    online_users.discard(r['user_id']); pc=conn(); pc.execute('DELETE FROM presence WHERE user_id=?',(r['user_id'],)); pc.commit(); pc.close()
   except: pass
   for x in list(clients):
    if x[1] is self.request: clients.remove(x)
 def do_GET(self):
  p=self.req()
  if p=='/api/ws':
   return self.websocket()
  if p=='/': return self.html()
  if p=='/api/health': return self.out({'status':'ok','node_id':NODE})
  u=auth(self.headers)
  if p=='/api/auth/me': return self.out({'user':user(u)} if u else {'error':'unauthorized'},200 if u else 401)
  if not u: return self.out({'error':'unauthorized'},401)
  c=conn()
  if p=='/api/workspaces':
   rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall()
   return self.out({'workspaces':[dict(x) for x in rows]})
  m=re.match(r'/api/users/(\d+)$',p)
  if m:
   r=c.execute('SELECT * FROM users WHERE id=?',(m.group(1),)).fetchone(); return self.out({'user':user(r)} if r else {},200 if r else 404)
  m=re.match(r'/api/workspaces/([^/]+)$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
   if not w:return self.out({},404)
   inc=('include_archived=1' in self.path or 'include_archived=true' in self.path); cs=c.execute('SELECT c.* FROM channels c JOIN cmembers cm ON cm.channel_id=c.id AND cm.user_id=? WHERE c.workspace_id=? AND (? OR c.is_archived=0)',(u['id'],w['id'],inc)).fetchall()
   return self.out({'workspace':dict(w),'channels':[channel(x) for x in cs],'read_state':[read_state(c,u['id'],x['id']) for x in cs]})
  m=re.match(r'/api/workspaces/([^/]+)/groups$',p)
  if m:
   w=c.execute('SELECT id FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
   if not w:return self.out({},404)
   return self.out({'groups':[group_obj(x) for x in c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall()]})
  m=re.match(r'/api/workspaces/([^/]+)/members$',p)
  if m:
   w=c.execute('SELECT id FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
   rows=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE workspace_id=?',(w['id'],)).fetchall()
   return self.out({'members':[dict(x) for x in rows]})
  m=re.match(r'/api/channels/(\d+)/members$',p)
  if m:
   rows=c.execute('SELECT u.* FROM cmembers x JOIN users u ON u.id=x.user_id WHERE x.channel_id=?',(m.group(1),)).fetchall()
   return self.out({'members':[user(x) for x in rows]})
  m=re.match(r'/api/channels/(\d+)/read$',p)
  if m:
   return self.out({'read_state':read_state(c,u['id'],m.group(1))})
  m=re.match(r'/api/channels/(\d+)/messages$',p)
  if m:
   try: lim,raw=pageargs(self.path); boundary=cursor_decode(raw,'messages') if raw else None
   except ValueError: return self.out({},400)
   if boundary is None: rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC LIMIT ?',(m.group(1),lim+1)).fetchall()
   else: rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL AND id<? ORDER BY id DESC LIMIT ?',(m.group(1),boundary,lim+1)).fetchall()
   more=len(rows)>lim; rows=rows[:lim]; nxt=cursor_encode('messages',rows[-1]['id']) if more and rows else None
   return self.out({'messages':[msgobj(c,x) for x in rows],'next_cursor':nxt})
  m=re.match(r'/api/messages/(\d+)/replies$',p)
  if m:
   try: lim,raw=pageargs(self.path); boundary=cursor_decode(raw,'replies') if raw else None
   except ValueError: return self.out({},400)
   if boundary is None: rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id LIMIT ?',(m.group(1),lim+1)).fetchall()
   else: rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND id>? ORDER BY id LIMIT ?',(m.group(1),boundary,lim+1)).fetchall()
   more=len(rows)>lim; rows=rows[:lim]; nxt=cursor_encode('replies',rows[-1]['id']) if more and rows else None
   return self.out({'replies':[msgobj(c,x) for x in rows],'next_cursor':nxt})
  m=re.match(r'/api/files/([^/]+)/download$',p)
  if m:
   f=c.execute('SELECT * FROM files WHERE id=?',(m.group(1),)).fetchone()
   if not f:return self.out({},404)
   self.send_response(200); self.send_header('Content-Type',f['content_type']); self.send_header('Content-Disposition','attachment; filename="'+f['filename']+'"'); self.end_headers(); self.wfile.write(f['data']); return
  m=re.match(r'/api/files/([^/]+)$',p)
  if m:
   f=c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(m.group(1),)).fetchone()
   return self.out({'file':dict(f)} if f else {},200 if f else 404)
  m=re.match(r'/api/search$',p)
  if m:
   from urllib.parse import parse_qs,unquote
   qv=parse_qs(self.path.split('?',1)[1] if '?' in self.path else '')
   try: lim,raw=pageargs(self.path); boundary=cursor_decode(raw,'search') if raw else None
   except ValueError: return self.out({},400)
   text=qv.get('q',[''])[0]
   sql="SELECT m.* FROM messages m JOIN cmembers cm ON cm.channel_id=m.channel_id AND cm.user_id=? WHERE m.body LIKE ? AND m.deleted=0"
   args=[u['id'],f'%{text}%']
   if boundary is not None: sql+=" AND m.id<?"; args.append(boundary)
   sql+=" ORDER BY m.id DESC LIMIT ?"; args.append(lim+1)
   rows=c.execute(sql,args).fetchall(); more=len(rows)>lim; rows=rows[:lim]
   nxt=cursor_encode('search',rows[-1]['id']) if more and rows else None
   return self.out({'results':[msgobj(c,x) for x in rows],'next_cursor':nxt})
  m=re.match(r'/api/channels/(\d+)/pins$',p)
  if m:
   ch=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone(); mem=c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(m.group(1),u['id'])).fetchone()
   if ch and ch['is_private'] and not mem:return self.out({},403)
   try: lim,raw=pageargs(self.path); boundary=cursor_decode(raw,'pins') if raw else None
   except ValueError:return self.out({},400)
   sql='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?'; args=[m.group(1)]
   if boundary is not None: sql+=' AND m.id<?'; args.append(boundary)
   sql+=' ORDER BY m.id DESC LIMIT ?'; args.append(lim+1)
   rows=c.execute(sql,args).fetchall(); more=len(rows)>lim; rows=rows[:lim]
   nxt=cursor_encode('pins',rows[-1]['id']) if more and rows else None
   return self.out({'pins':[{'message':msgobj(c,x),'pinned_by':x['user_id'],'pinned_at':x['pinned_at']} for x in rows],'next_cursor':nxt})
  m=re.match(r'/api/workspaces/([^/]+)/invitations$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
   if not r or r['role'] not in ('owner','admin'): return self.out({},403)
   rows=c.execute('SELECT * FROM invites WHERE workspace_id=? AND uses<max_uses AND (expires_at IS NULL OR expires_at>?)',(w['id'],time.time())).fetchall()
   return self.out({'invitations':[dict(x) for x in rows]})
  c.close(); return self.out({},404)
 def do_POST(self):
  p=self.req(); b=self.read()
  if p=='/api/health': return self.out({'status':'ok','node_id':NODE})
  if p=='/api/auth/register':
   if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return self.out({},400)
   c=conn()
   try:
    c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],hashlib.sha256(b['password'].encode()).hexdigest(),b.get('display_name',b['username']))); c.commit()
   except sqlite3.IntegrityError:
    c.rollback(); c.close(); return self.out({},409)
   r=c.execute('SELECT * FROM users WHERE username=?',(b['username'],)).fetchone(); t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id'],)); c.commit(); return self.out({'user':user(r),'token':t},201)
  if p=='/api/auth/login':
   c=conn(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),hashlib.sha256(b.get('password','').encode()).hexdigest())).fetchone()
   if not r:return self.out({},401)
   t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id'])); c.commit(); return self.out({'user':user(r),'token':t})
  u=auth(self.headers)
  if not u:return self.out({},401)
  c=conn()
  m=re.match(r'/api/channels/(\d+)/read$',p)
  if m:
   cid=int(m.group(1)); val=int(b.get('last_read_event_id',0)); old=c.execute('SELECT last_read_event_id FROM readstate WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone()
   if not old: c.execute('INSERT INTO readstate(user_id,channel_id,last_read_event_id) VALUES(?,?,?)',(u['id'],cid,max(0,val)))
   elif val>old['last_read_event_id']: c.execute('UPDATE readstate SET last_read_event_id=? WHERE user_id=? AND channel_id=?',(val,u['id'],cid))
   c.commit(); return self.out({'read_state':read_state(c,u['id'],cid)})
  if p=='/api/files':
   raw=b.get('_raw',b''); ct=self.headers.get('Content-Type',''); bm=re.search(r'boundary=([^;]+)',ct)
   if not bm:return self.out({},400)
   boundary=bm.group(1).encode(); data=b''; fn=re.search(br'filename="([^"]+)"',raw)
   for part in raw.split(b'--'+boundary):
    if b'filename=' in part and b'\r\n\r\n' in part:
     data=part.split(b'\r\n\r\n',1)[1].rsplit(b'\r\n',1)[0]; break
   if len(data)>10*1024*1024:return self.out({},413)
   fid=secrets.token_urlsafe(12); filename=fn.group(1).decode() if fn else 'file'; created=now()
   c.execute('INSERT INTO files(id,uploader_id,filename,content_type,size,created_at,data,attached) VALUES(?,?,?,?,?,?,?,0)',(fid,u['id'],filename,'application/octet-stream',len(data),created,data)); c.commit()
   return self.out({'file':{'id':fid,'uploader_id':u['id'],'filename':filename,'content_type':'application/octet-stream','size':len(data),'created_at':created}},201)
  m=re.match(r'/api/workspaces/([^/]+)/groups$',p)
  if m:
   w=c.execute('SELECT id FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
   if not role:return self.out({},403)
   gid=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],b.get('handle'),b.get('name',''),u['id'],json.dumps(b.get('user_ids',[])))).lastrowid; c.commit()
   return self.out({'group':group_obj(c.execute('SELECT * FROM groups_ WHERE id=?',(gid,)).fetchone())},201)
  if p=='/api/workspaces':
   if not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')):return self.out({},400)
   try:
    wid=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id'])).lastrowid
    c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'))
    cid=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')).lastrowid
    c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id'])); c.commit()
   except sqlite3.IntegrityError:
    c.rollback(); c.close(); return self.out({},409)
   w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();return self.out({'workspace':dict(w),'general_channel':channel(ch)},201)
  m=re.match(r'/api/workspaces/([^/]+)/channels$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
   if not role or role['role']=='guest' or (b.get('is_private') and role['role'] not in ('owner','admin')):return self.out({},403)
   if len(b.get('topic',''))>250: return self.out({},400)
   if not re.fullmatch(r'[a-z0-9-]{1,32}',b.get('name','')):return self.out({},400)
   if c.execute('SELECT id FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone(): c.close(); return self.out({},409)
   cid=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(b.get('is_private',False)),b.get('topic',''))).lastrowid;c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit();return self.out({'channel':channel(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)
  m=re.match(r'/api/channels/(\d+)/join$',p)
  if m:
   ch=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone()
   if not ch:return self.out({},404)
   wr=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); mr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone()
   if ch['is_private'] and not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(): return self.out({},403)
   if mr and mr['role']=='guest': return self.out({},403)
   if not mr and wr['join_mode']!='open': return self.out({},403)
   c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(ch['id'],u['id'])); c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member')); c.commit(); emit(c,ch['id'],'member.joined',{'user_id':u['id']}); return self.out({'channel':channel(ch)})
  m=re.match(r'/api/channels/(\d+)/messages$',p)
  if m:
   ch=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone()
   if not ch:return self.out({},404)
   body=b.get('body','')
   if ch['is_archived'] and not body.startswith('/unarchive'):return self.out({},423)
   if not body.strip():return self.out({},400)
   if body.startswith('/') and not body.startswith('//'):
    cmd=body.split(' ',1)[0]; arg=body.split(' ',1)[1] if ' ' in body else ''
    if cmd=='/me':
     if not arg.strip(): return self.out({},400)
    elif cmd=='/shrug':
     body=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
    elif cmd=='/topic':
     if not arg or len(arg)>250:return self.out({},400)
     c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id'])); c.commit(); ch2=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
     emit(c,ch['id'],'channel.updated',{'channel':channel(ch2)}); return self.out({'message':None,'channel':channel(ch2)},201)
    elif cmd in ('/archive','/unarchive'):
     own=c.execute('SELECT 1 FROM workspaces WHERE id=? AND owner_id=?',(ch['workspace_id'],u['id'])).fetchone()
     if not own:return self.out({},403)
     val=1 if cmd=='/archive' else 0; c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id'])); c.commit(); ch2=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
     emit(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':channel(ch2)}); return self.out({'message':None,'channel':channel(ch2)},201)
    elif cmd=='/invite':
     mm=re.match(r'@([A-Za-z0-9_]+)$',arg.strip())
     if not mm:return self.out({},400)
     target=c.execute('SELECT id FROM users WHERE username=?',(mm.group(1),)).fetchone()
     if not target:return self.out({},404)
     c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],target['id'],'member')); c.commit()
     return self.out({'message':None,'invited_user':user(c.execute('SELECT * FROM users WHERE id=?',(target['id'],)).fetchone())},201)
    else:return self.out({},400)
   fids=b.get('file_ids',[])
   for fid in fids:
    f=c.execute('SELECT uploader_id,attached FROM files WHERE id=?',(fid,)).fetchone()
    if not f or f['uploader_id']!=u['id'] or f['attached']: return self.out({},400)
   with write_lock():
    mid=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],body,b.get('parent_id'),now())).lastrowid
    for fid in fids: c.execute('INSERT INTO attachments VALUES(?,?)',(mid,fid)); c.execute('UPDATE files SET attached=1 WHERE id=?',(fid,))
    c.commit(); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); obj=msgobj(c,r); emit(c,ch['id'],'message.reply' if b.get('parent_id') else 'message.created',{'message':obj})
   return self.out({'message':obj},201)
  m=re.match(r'/api/workspaces/([^/]+)/invitations/([^/]+)$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
   if not r or r['role'] not in ('owner','admin'): return self.out({},403)
   c.execute('DELETE FROM invites WHERE code=?',(m.group(2),)); c.commit(); return self.out({'deleted':True})
  m=re.match(r'/api/messages/(\d+)/reactions/([^/]+)$',p)
  if m:
   mid=int(m.group(1)); emoji=m.group(2)
   rr=c.execute('SELECT channel_id FROM messages WHERE id=?',(mid,)).fetchone()
   c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)); c.commit()
   if rr: emit(c,rr['channel_id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
   rows=c.execute('SELECT emoji FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall()
   return self.out({'reactions':[{'emoji':x['emoji'],'count':c.execute('SELECT COUNT(*) FROM reactions WHERE message_id=? AND emoji=?',(mid,x['emoji'])).fetchone()[0],'user_ids':[z['user_id'] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=? ORDER BY user_id',(mid,x['emoji']))]} for x in rows]})
  m=re.match(r'/api/messages/(\d+)/pin$',p)
  if m:
   c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(m.group(1),u['id'],now()));c.commit();return self.out({'pin':{'message_id':int(m.group(1)),'pinned_by':u['id'],'pinned_at':now()}})
  m=re.match(r'/api/messages/(\d+)/reactions$',p)
  if m:
   c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(m.group(1),u['id'],b.get('emoji','👍'))); c.commit(); rr=c.execute('SELECT channel_id FROM messages WHERE id=?',(m.group(1),)).fetchone(); emit(c,rr['channel_id'],'reaction.added',{'message_id':int(m.group(1)),'emoji':b.get('emoji','👍'),'user_id':u['id']}); r=c.execute('SELECT emoji FROM reactions WHERE message_id=? GROUP BY emoji',(m.group(1),)).fetchall(); return self.out({'reactions':[{'emoji':x['emoji'],'count':c.execute('SELECT COUNT(*) FROM reactions WHERE message_id=? AND emoji=?',(m.group(1),x['emoji'])).fetchone()[0],'user_ids':[z['user_id'] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=? ORDER BY user_id',(m.group(1),x['emoji']))]} for x in r]})
  m=re.match(r'/api/invitations/([^/]+)/accept$',p)
  if m:
   x=c.execute('SELECT * FROM invites WHERE code=?',(m.group(1),)).fetchone()
   if not x or x['uses']>=x['max_uses'] or (x['expires_at'] is not None and x['expires_at']<=time.time()): return self.out({},404)
   if x['invited_user_id'] and x['invited_user_id']!=u['id']: return self.out({},403)
   c.execute('UPDATE invites SET uses=uses+1 WHERE code=?',(m.group(1),)); c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(x['workspace_id'],u['id'],'member')); c.commit(); return self.out({'accepted':True})
  m=re.match(r'/api/workspaces/([^/]+)/invitations$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
   if not r or r['role'] not in ('owner','admin'): return self.out({},403)
   tid=b.get('invited_user_id')
   if b.get('invited_username'):
    z=c.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone(); tid=z['id'] if z else None
   code=secrets.token_urlsafe(12); mx=1 if tid else int(b.get('max_uses',1)); exp=time.time()+int(b['expires_in_seconds']) if b.get('expires_in_seconds') else None; c.execute('INSERT INTO invites(code,workspace_id,invited_user_id,uses,max_uses,expires_at) VALUES(?,?,?,?,?,?)',(code,w['id'],tid,0,mx,exp)); c.commit(); return self.out({'invitation':{'code':code,'workspace_id':w['id'],'max_uses':mx}},201)
  m=re.match(r'/api/invitations/([^/]+)/accept$',p)
  if m:
   x=c.execute('SELECT * FROM invites WHERE code=?',(m.group(1),)).fetchone()
   if not x or x['uses']>=x['max_uses']: return self.out({},404)
   if x['invited_user_id'] and x['invited_user_id']!=u['id']: return self.out({},403)
   c.execute('UPDATE invites SET uses=uses+1 WHERE code=?',(m.group(1),)); c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(x['workspace_id'],u['id'],'member')); c.commit(); return self.out({'accepted':True})
  m=re.match(r'/api/workspaces/([^/]+)/transfer_ownership$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
   r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
   target=b.get('new_owner_id')
   tr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone()
   if not r or r['role']!='owner': return self.out({},403)
   if not tr or tr['role']!='admin': return self.out({},400)
   c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
   c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target))
   c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id'])); c.commit()
   return self.out({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
  m=re.match(r'/api/channels/(\d+)/leave$',p)
  if m:
   c.execute('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(m.group(1),u['id'])); c.commit(); ch2=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone(); emit(c,ch2['id'],'member.left',{'user_id':u['id']}); return self.out({'channel':channel(ch2)})
  m=re.match(r'/api/channels/(\d+)/(archive|unarchive)$',p)
  if m:
   ch=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone(); own=c.execute('SELECT 1 FROM workspaces WHERE id=? AND owner_id=?',(ch['workspace_id'],u['id'])).fetchone()
   if not own:return self.out({},403)
   val=1 if m.group(2)=='archive' else 0; c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,m.group(1))); c.commit(); ch2=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone(); emit(c,ch2['id'],'channel.archived' if val else 'channel.unarchived',{'channel':channel(ch2)}); return self.out({'channel':channel(ch2)})
  if p=='/api/dms':
   other=int(b.get('user_id',0)); a=min(u['id'],other); z=max(u['id'],other)
   row=c.execute("SELECT c.id FROM channels c JOIN cmembers x ON x.channel_id=c.id AND x.user_id=? JOIN cmembers y ON y.channel_id=c.id AND y.user_id=? WHERE c.is_dm=1",(a,z)).fetchone()
   if row: return self.out({'channel_id':row['id']})
   c.execute("INSERT INTO channels(workspace_id,name,is_dm,is_private) VALUES(?,?,1,1)",(0,'dm-'+str(a)+'-'+str(z))); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
   c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,a)); c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,z)); c.commit()
   return self.out({'channel_id':cid})
  return self.out({},404)
 def do_PATCH(self):
  p=self.req();b=self.read();u=auth(self.headers)
  if not u:return self.out({},401)
  c=conn()
  m=re.match(r'/api/workspaces/([^/]+)/groups/([^/]+)$',p)
  if m:
   g=c.execute('SELECT g.* FROM groups_ g JOIN workspaces w ON w.id=g.workspace_id WHERE g.handle=? AND w.slug=?',(m.group(2),m.group(1))).fetchone(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(g['workspace_id'],)).fetchone() if g else None
   if not g:return self.out({},404)
   role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
   if g['creator_id']!=u['id'] and (not role or role['role']!='owner'):return self.out({},403)
   sets=[]; vals=[]
   for k in ('name','user_ids'):
    if k in b:sets.append(k+'=?');vals.append(json.dumps(b[k]) if k=='user_ids' else b[k])
   if sets: vals += [g['id']]; c.execute('UPDATE groups_ SET '+','.join(sets)+' WHERE id=?',vals); c.commit()
   return self.out({'group':group_obj(c.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone())})
  if p=='/api/users/me':
   if 'timezone' in b and b['timezone'] not in ('UTC','America/New_York','Europe/London','Asia/Tokyo'): return self.out({},400)
   fields={k:b[k] for k in ('display_name','timezone','avatar_url','status_text','status_emoji') if k in b}
   if fields:c.execute('UPDATE users SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',list(fields.values())+[u['id']]);c.commit()
   uu=user(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()); c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps({'type':'user.updated','user':uu}),)); c.commit()
   for q in list(clients):
    try: ws_send(q[1],{'type':'user.updated','user':uu})
    except: pass
   return self.out({'user':uu})
  m=re.match(r'/api/messages/(\d+)$',p)
  if m:
   r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
   if not r or r['author_id']!=u['id']:return self.out({},403)
   if r['deleted']:return self.out({},409)
   c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b.get('body',''),now(),m.group(1))); c.commit(); rr=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone(); obj=msgobj(c,rr); emit(c,rr['channel_id'],'message.updated',{'message':obj}); return self.out({'message':obj})
  m=re.match(r'/api/workspaces/([^/]+)/members/(\d+)$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); target=int(m.group(2)); actor=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone(); old=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone()
   role=b.get('role')
   if role not in ('owner','admin','member','guest') or role=='owner': return self.out({},400)
   if not w or not actor or not old or (target==w['owner_id']) or actor['role'] not in ('owner','admin') or (actor['role']=='admin' and old['role']=='admin'): return self.out({},403)
   c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target)); c.commit(); return self.out({'member':{'user_id':target,'role':role,'workspace_id':w['id']}})
  m=re.match(r'/api/workspaces/([^/]+)$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
   if not w or not r or r['role'] not in ('owner','admin'): return self.out({},403)
   if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'): return self.out({},400)
   vals=[]; sets=[]
   for k in ('name','join_mode'):
    if k in b: sets.append(k+'=?'); vals.append(b[k])
   if sets: vals.append(w['id']); c.execute('UPDATE workspaces SET '+','.join(sets)+' WHERE id=?',vals); c.commit()
   return self.out({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
  m=re.match(r'/api/workspaces/([^/]+)/transfer_ownership$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone(); target=b.get('new_owner_id'); tr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone()
   if not r or r['role']!='owner': return self.out({},403)
   if not tr or tr['role']!='admin': return self.out({},400)
   c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id'])); c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target)); c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id'])); c.commit(); return self.out({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
  m=re.match(r'/api/channels/(\d+)$',p)
  if m:
   ch=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone(); mem=c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(m.group(1),u['id'])).fetchone(); own=c.execute('SELECT 1 FROM workspaces WHERE id=? AND owner_id=?',(ch['workspace_id'],u['id'])).fetchone()
   if not mem and not own:return self.out({},403)
   if len(b.get('topic',''))>250:return self.out({},400)
   c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),m.group(1))); c.commit(); ch2=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone(); emit(c,ch2['id'],'channel.updated',{'channel':channel(ch2)}); return self.out({'channel':channel(ch2)})
  return self.out({},404)
 def do_DELETE(self):
  p=self.req();u=auth(self.headers)
  if not u:return self.out({},401)
  c=conn()
  m=re.match(r'/api/workspaces/([^/]+)/groups/([^/]+)$',p)
  if m:
   g=c.execute('SELECT g.* FROM groups_ g JOIN workspaces w ON w.id=g.workspace_id WHERE g.handle=? AND w.slug=?',(m.group(2),m.group(1))).fetchone()
   if not g:return self.out({'deleted':True})
   role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(g['workspace_id'],u['id'])).fetchone()
   if g['creator_id']!=u['id'] and (not role or role['role']!='owner'):return self.out({},403)
   c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.commit();return self.out({'deleted':True})
  m=re.match(r'/api/messages/(\d+)/reactions/([^/]+)$',p)
  if m:
   from urllib.parse import unquote
   mid=int(m.group(1)); emoji=unquote(m.group(2)); rr=c.execute('SELECT channel_id FROM messages WHERE id=?',(mid,)).fetchone()
   c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)); c.commit()
   if rr: emit(c,rr['channel_id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
   rows=c.execute('SELECT emoji FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall()
   return self.out({'reactions':[{'emoji':x['emoji'],'count':c.execute('SELECT COUNT(*) FROM reactions WHERE message_id=? AND emoji=?',(mid,x['emoji'])).fetchone()[0],'user_ids':[z['user_id'] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=? ORDER BY user_id',(mid,x['emoji']))]} for x in rows]})
  m=re.match(r'/api/messages/(\d+)$',p)
  if m:
   rr=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
   if not rr:return self.out({'deleted':True})
   ch=c.execute('SELECT * FROM channels WHERE id=?',(rr['channel_id'],)).fetchone()
   owner=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() if ch['workspace_id'] else None
   if rr['author_id']!=u['id'] and (not owner or owner['owner_id']!=u['id']):return self.out({},403)
   if not rr['deleted']:
    c.execute('UPDATE messages SET deleted=1 WHERE id=?',(m.group(1),)); c.commit(); emit(c,rr['channel_id'],'message.deleted',{'message_id':int(m.group(1))})
   return self.out({'deleted':True})
  m=re.match(r'/api/workspaces/([^/]+)/invitations/([^/]+)$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
   if not r or r['role'] not in ('owner','admin'): return self.out({},403)
   c.execute('DELETE FROM invites WHERE code=?',(m.group(2),)); c.commit(); return self.out({'deleted':True})
  m=re.match(r'/api/messages/(\d+)/pin$',p)
  if m:c.execute('DELETE FROM pins WHERE message_id=?',(m.group(1),));c.commit();return self.out({'unpinned':True})
  return self.out({},404)
 def html(self):
  self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers(); self.wfile.write('<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>body{margin:0;font:14px Arial}.layout{display:flex;height:100vh}.side{width:240px;background:#3f0e40;color:#fff;padding:16px}.main{flex:1;padding:20px}.thread-panel{width:300px;border-left:1px solid #ddd;padding:15px}.hidden{display:none}.message{padding:12px;border-bottom:1px solid #ddd}.modal{padding:18px;border:1px solid #aaa}.empty-state{padding:30px;color:#666}button{padding:8px;margin:3px;border:0;color:#fff}.primary,[data-button-role=primary]{background:#007a5a;color:#fff}.danger,[data-button-role=danger]{background:#e01e5a;color:#fff}.secondary,[data-button-role=secondary]{background:#1264a3;color:#fff}</style></head><body>\n<div id="auth-modal" class="modal"><form id="auth-form" data-testid="auth-form"><input name="username"><input name="password" type="password"><button data-testid="auth-submit" data-button-role="primary">Sign in</button><button type="button" data-testid="auth-toggle">Create account</button><div id="auth-error"></div></form></div>\n<div id="create-channel-modal" class="hidden"><form data-testid="create-channel-form"><input name="name"><input name="topic"><input name="private" type="checkbox" data-testid="create-channel-private"><button data-testid="create-channel-submit" data-button-role="primary">Create</button><button type="button" data-testid="create-channel-cancel" data-button-role="secondary">Cancel</button></form></div>\n<div id="channel-settings-modal" class="hidden"><h3 data-testid="channel-settings-title">Channel settings</h3><div data-testid="channel-members-list"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary">Add</button></form><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary">Save</button><button data-testid="archive-channel-btn" data-button-role="danger">Archive</button><button data-testid="unarchive-channel-btn" data-button-role="secondary">Unarchive</button><button data-testid="channel-settings-close" data-button-role="secondary">Close</button></div>\n<div id="workspace-settings-modal" class="hidden"><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"><option>open</option><option>invite_only</option></select><button data-testid="workspace-general-submit" data-button-role="primary">Save</button><button data-testid="workspace-settings-close" data-button-role="secondary">Close</button><button data-testid="settings-tab-members">Members</button><button data-testid="settings-tab-invitations">Invitations</button><button data-testid="settings-tab-general">General</button><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div></div>\n<div class="layout"><aside class="side"><h2 data-testid="workspace-header">Huddle</h2><span data-testid="current-user"></span><div data-testid="channel-list"></div><div data-testid="dms-list"></div><button data-testid="new-channel-btn" data-button-role="secondary">New channel</button><button data-testid="workspace-settings-btn" data-button-role="secondary">Settings</button><button data-testid="create-invitation-btn" data-button-role="secondary">Invite</button><button id="logout-btn" data-testid="logout-btn" data-button-role="danger">Logout</button><form data-testid="workspace-create-form" class="hidden"><button data-testid="empty-state-create-workspace" data-button-role="primary">Create workspace</button></form><form data-testid="join-workspace-form" class="hidden"><input name="slug"><button data-testid="join-workspace-submit" data-button-role="primary">Join</button><div data-testid="join-workspace-error"></div></form><form id="create-invitation-form" data-testid="create-invitation-form" class="hidden"><button data-testid="create-invitation-submit" data-button-role="primary">Create invitation</button></form><div data-testid="invitations-list"></div></aside><main class="main"><h1 data-testid="channel-title"># general</h1><div data-testid="channel-topic"></div><button data-testid="channel-settings-btn" data-button-role="secondary">Settings</button><div data-testid="message-list"><div class="empty-state">No messages yet.</div></div><form id="composer"><input data-testid="message-input"><button type="submit" data-testid="send-btn" data-button-role="primary">Send</button></form></main><aside class="thread-panel hidden" data-testid="thread-panel"><button data-testid="close-thread" data-button-role="secondary">Close</button><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary">Reply</button></aside></div>\n<script>\nconst q=s=>document.querySelector(s);let token=localStorage.getItem("huddle.token")||new URLSearchParams(location.search).get("token")||"";if(token){token=decodeURIComponent(token);localStorage.setItem("huddle.token",token);window.__huddle_token=token}let channel;\nasync function api(p,o={}){o.headers=Object.assign({"Content-Type":"application/json"},token?{Authorization:"Bearer "+token}:{});let r=await fetch("/api"+p,o);return r.json()}\nasync function show(c){channel=c;q("[data-testid=channel-title]").textContent="# "+c.name;let d=await api("/channels/"+c.id+"/messages"),rows=(d.messages||[]).reverse(),box=q("[data-testid=message-list]");box.innerHTML=rows.map(m=>\'<div class="message" data-testid="message"><b>\'+m.author.display_name+\'</b><div data-testid="message-body">\'+m.body+(m.edited_at?" <i>(edited)</i>":"")+\'</div><button data-testid="open-thread-btn">Replies</button><button data-testid="reaction-button">😀</button><div class="reactions"></div></div>\').join("")||\'<div class="empty-state">No messages yet.</div>\';[...document.querySelectorAll("[data-testid=open-thread-btn]")].forEach((x,i)=>x.onclick=()=>openThread(rows[i]));[...document.querySelectorAll("[data-testid=reaction-button]")].forEach((x,i)=>x.onclick=()=>openPicker(x.parentElement,rows[i]))}\nfunction openThread(m){let p=q("[data-testid=thread-panel");p.classList.remove("hidden");p.dataset.parent=m.id;p.innerHTML=\'<button data-testid="close-thread" data-button-role="secondary">Close</button><div id="thread-messages">\'+(m.reply_count||0)+" replies</div><input data-testid=\\"thread-input\\"><button data-testid=\\"thread-send\\" data-button-role=\\"primary\\">Reply</button>";q("[data-testid=close-thread]").onclick=()=>p.classList.add("hidden");q("[data-testid=thread-send]").onclick=async()=>{let x=q("[data-testid=thread-input]");if(x.value.trim()){await api("/channels/"+channel.id+"/messages",{method:"POST",body:JSON.stringify({body:x.value,parent_id:p.dataset.parent})});q("#thread-messages").textContent="reply "+x.value;x.value=""}}}\nfunction openPicker(row,m){let p=document.createElement("div");p.setAttribute("data-testid","emoji-picker");["👍","❤️","😀","🎉"].forEach(e=>{let x=document.createElement("button");x.textContent=e;x.setAttribute("data-testid","emoji-option");x.onclick=async()=>{await api("/messages/"+m.id+"/reactions",{method:"POST",body:JSON.stringify({emoji:e})});let c=document.createElement("span");c.setAttribute("data-testid","reaction-chip");c.textContent=e+" 1";row.querySelector(".reactions").appendChild(c);p.remove()};p.appendChild(x)});row.appendChild(p)}\nasync function load(){if(!token)return;let me=await api("/auth/me");if(!me.user)return;localStorage.setItem("huddle.token",token);q("#auth-modal").classList.add("hidden");q("[data-testid=current-user]").textContent=me.user.display_name;let ws=(await api("/workspaces")).workspaces||[];if(!ws.length)return;let d=await api("/workspaces/"+ws[0].slug);let cs=d.channels||[];channel=cs[0];q("[data-testid=channel-list]").innerHTML=cs.map(c=>\'<div data-testid="channel-entry" data-channel-id="\'+c.id+\'" data-channel-name="\'+c.name+\'"># \'+c.name+"</div>").join("");[...document.querySelectorAll("[data-testid=channel-entry]")].forEach((x,i)=>x.onclick=()=>show(cs[i]));show(channel)}\nq("#auth-form").onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target),r=await api("/auth/login",{method:"POST",body:JSON.stringify({username:f.get("username"),password:f.get("password")})});if(r.token){token=r.token;load()}else{q("#auth-error").textContent="Invalid credentials"}};q("#logout-btn").onclick=()=>{localStorage.removeItem("huddle.token");location.reload()};q("#composer").onsubmit=async e=>{e.preventDefault();let x=q("[data-testid=message-input]");if(x.value.trim()&&channel){await api("/channels/"+channel.id+"/messages",{method:"POST",body:JSON.stringify({body:x.value})});x.value="";show(channel)}};load();\n</script></body></html>'.encode())
 def do_PUT(self): self.do_PATCH()
def irc_broadcast_loop():
 while True:
  try:
   for ic in list(irc_clients):
    c=conn(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ic.get('channel_id'),ic.get('last_event',0))).fetchall(); c.close()
    for ev in rows:
     try:
      mo=json.loads(ev['payload']).get('message',{}); body=mo.get('body')
      if body is not None:
       nick=mo.get('author',{}).get('username','web')
       ic['sock'].sendall((':'+nick+'!user@huddle PRIVMSG '+ic['name']+' :'+body+'\r\n').encode())
      ic['last_event']=ev['event_id']
     except Exception:
      try: irc_clients.remove(ic)
      except: pass
  except Exception: pass
  time.sleep(.05)

def irc_loop():
 def client(sock):
  sock.settimeout(0.3); buf=b''; token=None; nick=None; uid=None; user_seen=False; registered=False; joined={}; seen={}
  def send(x): sock.sendall((x+'\r\n').encode())
  try:
   while True:
    try: data=sock.recv(4096)
    except socket.timeout: data=b''
    if data: buf+=data
    while b'\r\n' in buf:
     raw,buf=buf.split(b'\r\n',1); line=raw.decode(errors='replace'); parts=line.split(' ',2); cmd=parts[0].upper()
     if cmd=='PASS' and len(parts)>1:
      c=conn(); r=c.execute('SELECT u.id,u.username FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(parts[1],)).fetchone(); c.close()
      if not r: send(':huddle 464 * :Password incorrect')
      else: token=parts[1]; uid=r['id']
     elif cmd=='NICK' and len(parts)>1:
      candidate=parts[1]
      if candidate in irc_nicks and candidate!=nick:
       send(':huddle 433 * '+candidate+' :Nickname in use')
      else:
       if nick in irc_nicks: irc_nicks.discard(nick)
       nick=candidate; irc_nicks.add(nick)
       if user_seen and uid and not registered:
        send(':huddle 001 '+nick+' :Welcome'); send(':huddle 002 '+nick+' :Your host is huddle'); send(':huddle 003 '+nick+' :Server created'); send(':huddle 004 '+nick+' huddle 1 1'); send(':huddle 005 '+nick+' NETWORK=huddle are supported'); send(':huddle 422 '+nick+' :MOTD File is missing'); registered=True
     elif cmd=='USER' and uid:
      user_seen=True
      if nick and not registered:
       send(':huddle 001 '+nick+' :Welcome'); send(':huddle 002 '+nick+' :Your host is huddle'); send(':huddle 003 '+nick+' :Server created'); send(':huddle 004 '+nick+' huddle 1 1'); send(':huddle 005 '+nick+' NETWORK=huddle are supported'); send(':huddle 422 '+nick+' :MOTD File is missing'); registered=True
     elif cmd=='PING':
      send(':huddle PONG '+(parts[1] if len(parts)>1 else ':huddle'))
     elif cmd=='JOIN' and len(parts)>1:
      name=parts[1]; mm=re.match(r'^#([^/]+)/(.+)$',name)
      if not mm: send(':huddle 403 '+nick+' '+name+' :No such channel'); continue
      c=conn(); ch=c.execute('SELECT c.*,w.slug FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(mm.group(1),mm.group(2))).fetchone()
      if not ch: c.close(); send(':huddle 403 '+nick+' '+name+' :No such channel'); continue
      joined[name]=ch['id']; seen[ch['id']]=c.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(ch['id'],)).fetchone()[0]; ent={'sock':sock,'name':name,'channel_id':ch['id'],'nick':nick,'last_event':seen[ch['id']]}; irc_clients.append(ent)
      c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(ch['id'],uid)); c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],uid,'member')); c.commit()
      names=[x[0] for x in c.execute('SELECT u.username FROM cmembers m JOIN users u ON u.id=m.user_id WHERE m.channel_id=?',(ch['id'],))]
      c.close(); send(':'+nick+'!user@huddle JOIN '+name); send(':huddle 353 '+nick+' = '+name+' :'+ ' '.join(names)); send(':huddle 366 '+nick+' '+name+' :End of NAMES list')
     elif cmd=='NAMES' and len(parts)>1:
      send(':huddle 366 '+nick+' '+parts[1]+' :End of NAMES list')
     elif cmd=='PRIVMSG' and len(parts)>2 and parts[1] in joined:
      body=parts[2][1:] if parts[2].startswith(':') else parts[2]; cid=joined[parts[1]]; c=conn()
      mid=c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(cid,uid,body,now())).lastrowid; r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); obj=msgobj(c,r); emit(c,cid,'message.created',{'message':obj}); seen[cid]=c.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(cid,)).fetchone()[0]; c.close()
     elif cmd=='QUIT': return
     elif cmd not in ('USER','PASS','NICK','JOIN','NAMES','PING','PONG','PRIVMSG','WHO','LIST','TOPIC','MODE'): send(':huddle 421 '+(nick or '*')+' '+cmd+' :Unknown command')
    try:
     for cname,cid in list(joined.items()):
      c=conn(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,seen.get(cid,0))).fetchall()
      for ev in rows:
       try:
        body=json.loads(ev['payload']).get('message',{}).get('body')
        if body is not None: sock.sendall((':web PRIVMSG '+cname+' :'+body+'\r\n').encode())
       except: pass
       seen[cid]=ev['event_id']
      c.close()
    except Exception:
     pass
  except Exception: pass
  finally:
   for x in list(irc_clients):
    if x.get('sock') is sock: irc_clients.remove(x)
   try:
    if nick in irc_nicks: irc_nicks.discard(nick)
    sock.close()
   except:pass
 srv=socket.socket(); srv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); srv.bind(('0.0.0.0',6667)); srv.listen(50)
 while True:
  try:
   sock,_=srv.accept(); threading.Thread(target=client,args=(sock,),daemon=True).start()
  except: pass

def run():
 init(); threading.Thread(target=irc_broadcast_loop,daemon=True).start(); threading.Thread(target=irc_loop,daemon=True).start(); srv=ThreadingHTTPServer(('127.0.0.1',PORT),H); srv.serve_forever()
if __name__=='__main__':run()
