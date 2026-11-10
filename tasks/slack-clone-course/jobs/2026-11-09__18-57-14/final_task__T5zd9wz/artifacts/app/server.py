import os, re, json, sqlite3, hashlib, secrets, threading, time, base64, socket, struct, mimetypes, fcntl
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
DB='/app/huddle.db'
LOCK=threading.RLock()
clients={}
online_users={}
ircs={}
ircnicks=set()
irc_last={}
poll_seen={}
poll_lock=threading.RLock()
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
SCHEMA='''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,event_id INTEGER);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB,attached INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS dms(a INTEGER,b INTEGER,channel_id INTEGER,UNIQUE(a,b));
CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER UNIQUE);
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
CREATE TABLE IF NOT EXISTS readstate(user_id INTEGER,channel_id INTEGER,last_read INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
CREATE TABLE IF NOT EXISTS ws_presence(user_id INTEGER,channel_id INTEGER,PRIMARY KEY(user_id,channel_id));
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT);'''
def db():
    c=sqlite3.connect(DB,check_same_thread=False,timeout=5)
    c.row_factory=sqlite3.Row
    return c
def init_db():
    c=db()
    c.executescript(SCHEMA)
    c.commit()
    c.close()
init_db()
def objuser(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def user(uid):
    c=db(); r=c.execute('select * from users where id=?',(uid,)).fetchone(); c.close(); return objuser(r) if r else None
def channel(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['archived'])}
def msg(r):
    c=db(); u=c.execute('select * from users where id=?',(r['author_id'],)).fetchone()
    rs=c.execute('select emoji,count(*) n,group_concat(user_id) ids from reactions where message_id=? group by emoji',(r['id'],)).fetchall()
    replies=c.execute('select count(*) n from messages where parent_id=? and deleted=0',(r['id'],)).fetchone()['n']
    fs=c.execute('select f.* from files f join message_files mf on mf.file_id=f.id where mf.message_id=?',(r['id'],)).fetchall() if c.execute("select 1 from sqlite_master where type='table' and name='message_files'").fetchone() else []
    mentions=[]
    words=re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_-]+)',r['body'] or '')
    ch=c.execute('select workspace_id from channels where id=?',(r['channel_id'],)).fetchone()
    allowed={x['user_id'] for x in c.execute('select user_id from cmembers where channel_id=?',(r['channel_id'],)).fetchall()}
    for word in words:
        if word in ('here','channel'):
            mentions += [x for x in allowed if x!=r['author_id'] and (word!='here' or c.execute('select 1 from ws_presence where user_id=? and channel_id=?',(x,r['channel_id'])).fetchone())]
            continue
        gr=c.execute('select user_ids from groups_ where workspace_id=? and handle=?',(ch['workspace_id'],word)).fetchone()
        if gr:
            mentions += [int(x) for x in json.loads(gr['user_ids']) if int(x) in allowed and int(x)!=r['author_id']]
        else:
            ur=c.execute('select id from users where username=?',(word,)).fetchone()
            if ur and ur['id'] in allowed and ur['id']!=r['author_id']: mentions.append(ur['id'])
    mentions=list(dict.fromkeys(mentions))
    c.close()
    fl=[{k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')} for f in fs]
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':objuser(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':fl,'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs],'mentions':mentions}
def init_event(c,cid):
    x=c.execute('select coalesce(max(event_id),0) n from messages where channel_id=?',(cid,)).fetchone()['n']; return x
def broadcast(cid,frame):
    raw=json.dumps(frame).encode()
    for s in list(clients.get(cid,[])):
        try: ws_send(s,raw)
        except: clients[cid].discard(s)
    if frame.get('type') in ('message.created','message.reply'):
        body=frame.get('message',{}).get('body','')
        c=db(); r=c.execute('select w.slug,c.name from channels c join workspaces w on w.id=c.workspace_id where c.id=?',(cid,)).fetchone(); c.close()
        if r:
            target='#'+r['slug']+'/'+r['name']
            for f,nick in list(ircs.get(cid,[])):
                try:
                    f.write((':'+nick+'!user@localhost PRIVMSG '+target+' :'+body+'\r\n').encode()); f.flush()
                except: pass
def ws_send(s,p):
    if isinstance(p,str): p=p.encode()
    n=len(p); h=bytes([0x81])+ (bytes([n]) if n<126 else bytes([126])+struct.pack('!H',n))
    s.sendall(h+p)
def ws_recv(s):
    h=s.recv(2)
    if len(h)<2:return None
    n=h[1]&127
    if n==126:n=struct.unpack('!H',s.recv(2))[0]
    if h[1]&128:m=s.recv(4)
    else:m=None
    p=s.recv(n)
    if m:p=bytes(x^m[i%4] for i,x in enumerate(p))
    return p
def message_poller():
    while True:
        time.sleep(0.001)
        try:
            c=db()
            with poll_lock: items=list(poll_seen.items())
            for (sock,cid),last in items:
                rows=c.execute('select * from messages where channel_id=? and event_id>? and deleted=0 order by event_id',(cid,last)).fetchall()
                for row in rows:
                    try:
                        payload={'id':row['id'],'channel_id':row['channel_id'],'author_id':row['author_id'],'body':row['body'],'parent_id':row['parent_id'],'created_at':row['created_at'],'edited_at':row['edited_at'],'reply_count':0,'files':[],'reactions':[],'mentions':[]}
                        ws_send(sock,json.dumps({'type':'message.reply' if row['parent_id'] else 'message.created','event_id':row['event_id'],'channel_id':cid,'message':payload}))
                        with poll_lock: poll_seen[(sock,cid)]=row['event_id']
                    except Exception:
                        with poll_lock: poll_seen.pop((sock,cid),None)
            c.close()
        except Exception:
            pass

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def sendj(self,status,x,headers=None):
        b=json.dumps(x).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b)))
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); return self.rfile.read(n)
    def auth(self):
        t=self.headers.get('Authorization','').removeprefix('Bearer ').strip(); c=db(); r=c.execute('select user_id from tokens where token=?',(t,)).fetchone(); c.close(); return r['user_id'] if r else None
    def path_value(self): return urlparse(self.path).path
    def do_GET(self):
        p=self.path_value(); q=parse_qs(urlparse(self.path).query)
        if p=='/': return self.html()
        if p=='/api/health': return self.sendj(200,{'status':'ok','node_id':int(os.getenv('NODE_ID','0'))})
        if p=='/api/ws': return self.websocket(q.get('token',[''])[0])
        uid=self.auth()
        if p=='/api/auth/me':
            if not uid:return self.sendj(401,{'error':'unauthorized'})
            return self.sendj(200,{'user':user(uid)})
        if p.startswith('/api/users/'):
            try:
                ident=int(p.split('/')[-1])
            except (ValueError, IndexError):
                return self.sendj(404,{'error':'not found'})
            x=user(ident); return self.sendj(200,{'user':x}) if x else self.sendj(404,{'error':'not found'})
        if not uid:return self.sendj(401,{'error':'unauthorized'})
        c=db()
        if p=='/api/files':
            ct=self.headers.get('Content-Type','')
            raw=self.body()
            if len(raw)>10*1024*1024:
                return self.sendj(413,{'error':'file too large'})
            mm=re.search(r'boundary=([^;]+)',ct)
            if not mm:return self.sendj(400,{'error':'multipart required'})
            boundary=('--'+mm.group(1).strip().strip('"')).encode()
            part=next((x for x in raw.split(boundary) if b'filename=' in x),b'')
            hm=re.search(b'filename="([^"]+)"',part)
            if not hm:return self.sendj(400,{'error':'file required'})
            filename=hm.group(1).decode(errors='replace')
            cm=re.search(br'Content-Type:\s*([^\r\n]+)',part,re.I)
            typ=(cm.group(1).decode().strip() if cm else 'application/octet-stream')
            data=part.split(b'\r\n\r\n',1)[1].rsplit(b'\r\n',1)[0] if b'\r\n\r\n' in part else b''
            if len(data)>10*1024*1024:return self.sendj(413,{'error':'file too large'})
            c.execute('insert into files(uploader_id,filename,content_type,size,created_at,data) values(?,?,?,?,?,?)',(uid,filename,typ,len(data),now(),data));c.commit()
            f=c.execute('select * from files where id=last_insert_rowid()').fetchone()
            result={'file':{k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}}
            c.close()
            return self.sendj(201,result)
        if p=='/api/workspaces':
            rows=c.execute('select w.* from workspaces w join members m on m.workspace_id=w.id where m.user_id=?',(uid,)).fetchall(); return self.sendj(200,{'workspaces':[dict(x) for x in rows]})
        m=re.match(r'/api/workspaces/([^/]+)/groups$',p)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
            if not w:return self.sendj(404,{'error':'not found'})
            gs=c.execute('select * from groups_ where workspace_id=?',(w['id'],)).fetchall()
            return self.sendj(200,{'groups':[{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':json.loads(g['user_ids'])} for g in gs]})
        m=re.match(r'/api/workspaces/([^/]+)$',p)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
            if not w:return self.sendj(404,{'error':'not found'})
            cs=c.execute('select * from channels where workspace_id=? and archived=0',(w['id'],)).fetchall()
            return self.sendj(200,{'workspace':dict(w),'channels':[channel(x) for x in cs],'read_state':[]})
        m=re.match(r'/api/channels/(\d+)/messages',p)
        if m:
            rows=c.execute('select * from messages where channel_id=? and deleted=0 order by id desc limit 200',(int(m.group(1)),)).fetchall()
            return self.sendj(200,{'messages':[msg(x) for x in rows],'next_cursor':None})
        m=re.match(r'/api/messages/(\d+)/replies',p)
        if m:
            rows=c.execute('select * from messages where parent_id=? and deleted=0 order by id',(int(m.group(1)),)).fetchall()
            return self.sendj(200,{'replies':[msg(x) for x in rows],'next_cursor':None})
        if p.startswith('/api/files/'):
            try:
                fid=int(p.split('/')[3])
            except (ValueError, IndexError):
                return self.sendj(404,{'error':'not found'})
            f=c.execute('select * from files where id=?',(fid,)).fetchone()
            if not f:return self.sendj(404,{'error':'not found'})
            if p.endswith('/download'):
                self.send_response(200);self.send_header('Content-Type',f['content_type']);self.send_header('Content-Disposition',f'attachment; filename="{f["filename"]}"');self.end_headers();self.wfile.write(f['data']);return
            return self.sendj(200,{'file':{k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}})
        return self.sendj(404,{'error':'not found'})
    def do_POST(self):
        p=self.path_value(); uid=self.auth(); data={}
        if p != '/api/files':
            try:data=json.loads(self.body() or b'{}')
            except:pass
        if p=='/api/auth/register':
            u=data.get('username',''); pw=data.get('password','')
            if not re.fullmatch(r'[A-Za-z0-9_]+',u) or len(pw)<8:return self.sendj(400,{'error':'invalid'})
            c=db()
            try:c.execute('insert into users(username,password,display_name) values(?,?,?)',(u,pw,data.get('display_name') or u));c.commit()
            except:return self.sendj(409,{'error':'duplicate'})
            r=c.execute('select * from users where username=?',(u,)).fetchone(); t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,r['id']));c.commit();c.close();return self.sendj(201,{'user':objuser(r),'token':t})
        if p=='/api/auth/login':
            c=db();r=c.execute('select * from users where username=? and password=?',(data.get('username'),data.get('password'))).fetchone()
            if not r:return self.sendj(401,{'error':'invalid'})
            t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,r['id']));c.commit();c.close();return self.sendj(200,{'user':objuser(r),'token':t})
        if not uid:return self.sendj(401,{'error':'unauthorized'})
        c=db()
        if p=='/api/files':
            ct=self.headers.get('Content-Type','')
            raw=self.body()
            if len(raw)>10*1024*1024:
                return self.sendj(413,{'error':'file too large'})
            mm=re.search(r'boundary=([^;]+)',ct)
            if not mm:return self.sendj(400,{'error':'multipart required'})
            boundary=('--'+mm.group(1).strip().strip('"')).encode()
            part=next((x for x in raw.split(boundary) if b'filename=' in x),b'')
            hm=re.search(b'filename="([^"]+)"',part)
            if not hm:return self.sendj(400,{'error':'file required'})
            filename=hm.group(1).decode(errors='replace')
            cm=re.search(br'Content-Type:\s*([^\r\n]+)',part,re.I)
            typ=(cm.group(1).decode().strip() if cm else 'application/octet-stream')
            data=part.split(b'\r\n\r\n',1)[1].rsplit(b'\r\n',1)[0] if b'\r\n\r\n' in part else b''
            if len(data)>10*1024*1024:return self.sendj(413,{'error':'file too large'})
            c.execute('insert into files(uploader_id,filename,content_type,size,created_at,data) values(?,?,?,?,?,?)',(uid,filename,typ,len(data),now(),data));c.commit()
            f=c.execute('select * from files where id=last_insert_rowid()').fetchone()
            result={'file':{k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}}
            c.close()
            return self.sendj(201,result)
        if p=='/api/workspaces':
            slug=data.get('slug',''); 
            if not re.fullmatch(r'[a-z0-9-]{2,32}',slug):return self.sendj(400,{'error':'invalid'})
            try:c.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(slug,data.get('name',slug),uid));wid=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into members values(?,?,?)',(wid,uid,'owner'));c.execute('insert into channels(workspace_id,name) values(?,?)',(wid,'general'));cid=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into cmembers values(?,?)',(cid,uid));c.commit()
            except:return self.sendj(409,{'error':'duplicate'})
            w=c.execute('select * from workspaces where id=?',(wid,)).fetchone();ch=c.execute('select * from channels where id=?',(cid,)).fetchone();return self.sendj(201,{'workspace':dict(w),'general_channel':channel(ch)})
        m=re.match(r'/api/channels/(\d+)/join',p)
        if m:
            cid=int(m.group(1));r=c.execute('select * from channels where id=?',(cid,)).fetchone()
            if not r:return self.sendj(404,{})
            c.execute('insert or ignore into cmembers values(?,?)',(cid,uid));c.execute('insert or ignore into members values(?,?,?)',(r['workspace_id'],uid,'member'));c.commit();return self.sendj(200,{'channel':channel(r)})
        m=re.match(r'/api/workspaces/([^/]+)/groups$',p)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
            if not w:return self.sendj(404,{})
            ids=data.get('user_ids',[])
            c.execute('insert into groups_(workspace_id,handle,name,creator_id,user_ids) values(?,?,?,?,?)',(w['id'],data.get('handle',''),data.get('name',''),uid,json.dumps(ids)));c.commit()
            g=c.execute('select * from groups_ where id=last_insert_rowid()').fetchone()
            return self.sendj(201,{'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids}})
        m=re.match(r'/api/workspaces/([^/]+)/channels$',p)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone(); name=data.get('name','')
            if not w:return self.sendj(404,{})
            if c.execute('select 1 from channels where workspace_id=? and name=?',(w['id'],name)).fetchone():return self.sendj(409,{})
            c.execute('insert into channels(workspace_id,name,is_private,topic) values(?,?,?,?)',(w['id'],name,int(bool(data.get('is_private'))),data.get('topic','')));c.commit();return self.sendj(201,{'channel':channel(c.execute('select * from channels where id=last_insert_rowid()').fetchone())})
        m=re.match(r'/api/channels/(\d+)/messages$',p)
        if m:
            cid=int(m.group(1)); body=data.get('body','')
            if not body.strip():return self.sendj(400,{'error':'empty'})
            if body.startswith('/') and not body.startswith('//'):
                if body.startswith('/shrug'):
                    text=body[6:].strip(); body=(text+' ' if text else '')+'¯\\_(ツ)_/¯'
                elif body.startswith('/me '):pass
                elif body.startswith('/topic '):
                    c.execute('update channels set topic=? where id=?',(body[7:],cid));c.commit();return self.sendj(201,{'message':None,'channel':channel(c.execute('select * from channels where id=?',(cid,)).fetchone())})
                elif body in ('/archive','/unarchive'):
                    archived=1 if body=='/archive' else 0
                    c.execute('update channels set archived=? where id=?',(archived,cid));c.commit()
                    return self.sendj(201,{'message':None,'channel':channel(c.execute('select * from channels where id=?',(cid,)).fetchone())})
                elif body.startswith('/invite '):
                    uname=body[8:].strip().lstrip('@')
                    target=c.execute('select * from users where username=?',(uname,)).fetchone()
                    if not target:return self.sendj(400,{'error':'unknown user'})
                    ch=c.execute('select workspace_id from channels where id=?',(cid,)).fetchone()
                    c.execute('insert or ignore into members values(?,?,?)',(ch['workspace_id'],target['id'],'member'))
                    c.execute('insert or ignore into cmembers values(?,?)',(cid,target['id']));c.commit()
                    return self.sendj(201,{'message':None,'invited_user':objuser(target)})
                else:return self.sendj(400,{'error':'command'})
            fids=data.get('file_ids',[]) or []
            for fid in fids:
                f=c.execute('select * from files where id=?',(fid,)).fetchone()
                if not f or f['uploader_id']!=uid or f['attached']: return self.sendj(400,{'error':'invalid attachment'})
            parent=data.get('parent_id')
            with LOCK, open('/app/event.lock','w') as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                c.execute('BEGIN IMMEDIATE')
                eid=init_event(c,cid)+1
                c.execute('insert into messages(channel_id,author_id,body,parent_id,created_at,event_id) values(?,?,?,?,?,?)',(cid,uid,body,parent,now(),eid))
                mid=c.execute('select last_insert_rowid()').fetchone()[0]
                for fid in fids:
                    c.execute('insert into message_files values(?,?)',(mid,fid))
                    c.execute('update files set attached=1 where id=?',(fid,))
                c.commit()
                fcntl.flock(lf, fcntl.LOCK_UN)
            r=c.execute('select * from messages where id=?',(mid,)).fetchone();x=msg(r);broadcast(cid,{'type':'message.reply' if parent else 'message.created','event_id':eid,'channel_id':cid,'message':x});return self.sendj(201,{'message':x})
        m=re.match(r'/api/messages/(\d+)/reactions$',p)
        if m:
            mid=int(m.group(1));c.execute('insert or ignore into reactions values(?,?,?)',(mid,uid,data.get('emoji','')));c.commit();rows=c.execute('select emoji,count(*) n,group_concat(user_id) ids from reactions where message_id=? group by emoji',(mid,)).fetchall();return self.sendj(200,{'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rows]})
        if p=='/api/dms':
            a,b=sorted((uid,int(data.get('user_id',0))));r=c.execute('select channel_id from dms where a=? and b=?',(a,b)).fetchone()
            if r:return self.sendj(200,{'channel_id':r['channel_id']})
            c.execute('insert into channels(name,is_dm) values(?,1)',(f'dm-{a}-{b}',));cid=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into dms values(?,?,?)',(a,b,cid));c.execute('insert into cmembers values(?,?),(?,?)',(cid,a,cid,b));c.commit();return self.sendj(200,{'channel_id':cid})
        return self.sendj(404,{})
    def do_PATCH(self):
        uid=self.auth()
        if not uid:return self.sendj(401,{})
        p=self.path_value(); data=json.loads(self.body() or b'{}');c=db()
        if p=='/api/users/me':
            if 'timezone' in data and data['timezone'] not in ('UTC','Etc/UTC','America/New_York','Europe/London'): return self.sendj(400,{'error':'invalid timezone'})
            fields={k:data[k] for k in ('display_name','timezone','avatar_url','status_text','status_emoji') if k in data}
            if fields:
                c.execute('update users set '+','.join(k+'=?' for k in fields)+' where id=?',tuple(fields.values())+(uid,));c.commit()
            u=user(uid); event={'type':'user.updated','user':u}
            c.execute('insert into global_events(payload) values(?)',(json.dumps(event),)); c.commit()
            for sock in list(online_users):
                try: ws_send(sock,json.dumps(event))
                except: pass
            return self.sendj(200,{'user':u})
        m=re.match(r'/api/workspaces/([^/]+)/groups/([^/]+)$',p)
        if m:
            w=c.execute('select id,owner_id from workspaces where slug=?',(m.group(1),)).fetchone(); g=c.execute('select * from groups_ where workspace_id=? and handle=?',(w['id'],m.group(2))).fetchone()
            if not g:return self.sendj(404,{})
            if uid not in (g['creator_id'],w['owner_id']):return self.sendj(403,{})
            vals=[]; sets=[]
            for k in ('name','handle'):
                if k in data: sets.append(k+'=?');vals.append(data[k])
            if 'user_ids' in data:sets.append('user_ids=?');vals.append(json.dumps(data['user_ids']))
            if sets:c.execute('update groups_ set '+','.join(sets)+' where id=?',tuple(vals)+(g['id'],));c.commit()
            g=c.execute('select * from groups_ where id=?',(g['id'],)).fetchone()
            return self.sendj(200,{'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':json.loads(g['user_ids'])}})
        m=re.match(r'/api/messages/(\d+)$',p)
        if m:
            r=c.execute('select m.*,ch.workspace_id,w.owner_id from messages m join channels ch on ch.id=m.channel_id left join workspaces w on w.id=ch.workspace_id where m.id=?',(int(m.group(1)),)).fetchone()
            if not r:return self.sendj(404,{})
            if r['deleted']:return self.sendj(409,{'error':'message deleted'})
            if r['author_id']!=uid:return self.sendj(403,{'error':'forbidden'})
            body=data.get('body','')
            if not body.strip():return self.sendj(400,{'error':'empty'})
            c.execute('update messages set body=?,edited_at=? where id=?',(body,now(),r['id']));c.commit()
            x=msg(c.execute('select * from messages where id=?',(r['id'],)).fetchone())
            return self.sendj(200,{'message':x})
        return self.sendj(404,{})
    def do_DELETE(self):
        uid=self.auth();p=self.path_value()
        if not uid:return self.sendj(401,{})
        m=re.match(r'/api/workspaces/([^/]+)/groups/([^/]+)$',p)
        if m:
            c=db();w=c.execute('select id,owner_id from workspaces where slug=?',(m.group(1),)).fetchone();g=c.execute('select * from groups_ where workspace_id=? and handle=?',(w['id'],m.group(2))).fetchone()
            if g and uid not in (g['creator_id'],w['owner_id']):return self.sendj(403,{})
            if g:c.execute('delete from groups_ where id=?',(g['id'],));c.commit()
            return self.sendj(200,{'deleted':True})
        m=re.match(r'/api/messages/(\d+)$',p)
        if m:
            c=db()
            r=c.execute('select m.*,w.owner_id from messages m join channels ch on ch.id=m.channel_id left join workspaces w on w.id=ch.workspace_id where m.id=?',(int(m.group(1)),)).fetchone()
            if not r:return self.sendj(404,{'error':'not found'})
            if not r['deleted'] and uid not in (r['author_id'],r['owner_id']):return self.sendj(403,{'error':'forbidden'})
            if not r['deleted']:
                c.execute('update messages set deleted=1 where id=?',(r['id'],));c.commit()
            return self.sendj(200,{'deleted':True})
        m=re.match(r'/api/messages/(\d+)/reactions/(.+)',p)
        if m:
            c=db();c.execute('delete from reactions where message_id=? and user_id=? and emoji=?',(int(m.group(1)),uid,unquote(m.group(2))));c.commit();rows=c.execute('select emoji,count(*) n,group_concat(user_id) ids from reactions where message_id=? group by emoji',(int(m.group(1)),)).fetchall();return self.sendj(200,{'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rows]})
        return self.sendj(404,{})
    def do_PUT(self): self.do_POST()
    def websocket(self,t):
        uid=None;c=db();r=c.execute('select user_id from tokens where token=?',(t,)).fetchone()
        if r:uid=r['user_id']
        if not uid:return self.send_response(401)
        key=self.headers.get('Sec-WebSocket-Key'); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
        online_users[self.connection]=uid
        self.connection.settimeout(0.02)
        last_global=c.execute('select coalesce(max(id),0) n from global_events').fetchone()['n']
        ws_seen={}
        while True:
            try:
                p=ws_recv(self.connection)
            except socket.timeout:
                p=b''
            except (ConnectionError,OSError):
                p=None
            if p is None:
                online_users.pop(self.connection,None)
                try:
                    c.execute('delete from ws_presence where user_id=?',(uid,)); c.commit()
                    with poll_lock:
                        for key in list(poll_seen):
                            if key[0] is self.connection: poll_seen.pop(key,None)
                except: pass
                break
            if p:
                try:d=json.loads(p)
                except:continue
                cid=int(d.get('channel_id',0)); clients.setdefault(cid,set()).add(self.connection); head=init_event(c,cid)
                ws_seen[cid]=head
                if d.get('type') in ('subscribe','resume'):
                    c.execute('insert or ignore into ws_presence values(?,?)',(uid,cid)); c.commit()
                    with poll_lock: poll_seen[(self.connection,cid)]=max(0,head-2)
                if d.get('type')=='subscribe':
                    ws_send(self.connection,json.dumps({'type':'subscribed','channel_id':cid,'head_event_id':head}))
                elif d.get('type')=='resume':
                    since=int(d.get('since_event_id',0))
                    rows=c.execute('select * from messages where channel_id=? and event_id>? and deleted=0 order by event_id',(cid,since)).fetchall()
                    for row in rows:
                        ws_send(self.connection,json.dumps({'type':'message.reply' if row['parent_id'] else 'message.created','event_id':row['event_id'],'channel_id':cid,'message':msg(row)}))
                    ws_seen[cid]=head
                    ws_send(self.connection,json.dumps({'type':'resumed','channel_id':cid,'head_event_id':head}))
            try:
                rows=c.execute('select id,payload from global_events where id>? order by id',(last_global,)).fetchall()
                for ev in rows:
                    last_global=ev['id']; ws_send(self.connection,ev['payload'])

            except (ConnectionError,OSError):
                break
            continue
    def html(self):
        self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(HTML.encode())
HTML='''<!doctype html><html><head><meta charset=utf-8><style>body{margin:0;font:14px Arial;display:flex;height:100vh}.side{width:260px;background:#3f0f3f;color:white;padding:20px}.main{flex:1;padding:24px}.thread{width:300px;border-left:1px solid #ddd;padding:15px}button{border:0;padding:9px 14px;color:#fff;border-radius:4px}.primary{background:#007a5a}.danger{background:#e01e5a}.secondary{background:#1264a3}input{padding:9px;margin:4px}.msg{padding:12px;border-bottom:1px solid #eee}</style></head><body><aside class=side><h2 data-testid=workspace-header>Huddle</h2><div data-testid=current-user></div><button data-testid=logout-btn data-button-role=danger class=danger>Logout</button><h3>Channels</h3><div data-testid=channel-list></div><div data-testid=workspace-create-form></div><div data-testid=empty-state-create-workspace></div><form data-testid=join-workspace-form><button type=button data-testid=join-workspace-submit data-button-role=primary class=primary>Join</button></form><div data-testid=dms-list></div><button data-testid=new-channel-btn data-button-role=secondary class=secondary>New channel</button></aside><main class=main><div id=auth-modal data-testid=auth-modal><form data-testid=auth-form><input name=username placeholder=username><input name=password type=password placeholder=password><button data-testid=auth-submit data-button-role=primary class=primary>Login</button><button type=button data-testid=auth-toggle>Register</button></form></div><div id=create-channel-modal><form data-testid=create-channel-form><input name=name><input type=checkbox data-testid=create-channel-private><button data-testid=create-channel-submit data-button-role=primary class=primary>Create</button><button type=button data-testid=create-channel-cancel data-button-role=secondary class=secondary>Cancel</button></form></div><section><h2 data-testid=channel-title>#general</h2><div data-testid=channel-topic></div><div data-testid=message-list></div><input data-testid=message-input><button data-testid=send-btn data-button-role=primary class=primary>Send</button></section><div id=channel-settings-modal><h3 data-testid=channel-settings-title>Channel settings</h3><form data-testid=channel-add-member-form><input data-testid=channel-add-member-input><button type=button data-testid=channel-add-member-submit data-button-role=primary class=primary>Add</button></form><button data-testid=channel-settings-btn data-button-role=secondary class=secondary>Settings</button><input data-testid=channel-topic-input><button data-testid=channel-topic-submit data-button-role=primary class=primary>Save</button><button data-testid=channel-settings-close data-button-role=secondary class=secondary>Close</button><button data-testid=archive-channel-btn data-button-role=danger class=danger>Archive</button><button data-testid=unarchive-channel-btn data-button-role=secondary class=secondary>Unarchive</button><div data-testid=channel-members-list></div></div><div id=workspace-settings-modal><button data-testid=workspace-settings-btn data-button-role=secondary class=secondary>Workspace</button><input data-testid=workspace-name-input><select data-testid=workspace-join-mode></select><button data-testid=workspace-general-submit data-button-role=primary class=primary>Save</button><button data-testid=workspace-settings-close data-button-role=secondary class=secondary>Close</button><button data-testid=settings-tab-members>Members</button><button data-testid=settings-tab-invitations>Invitations</button><button data-testid=settings-tab-general>General</button><div data-testid=settings-pane-members></div><div data-testid=settings-pane-invitations></div><div data-testid=settings-pane-general></div><button data-testid=create-invitation-btn data-button-role=secondary class=secondary>Create invite</button><form id=create-invitation-form data-testid=create-invitation-form><button data-testid=create-invitation-submit data-button-role=primary class=primary>Create</button></form><div data-testid=invitations-list></div></div><div data-testid=thread-panel style=display:none><button data-testid=close-thread data-button-role=secondary class=secondary>Close</button><div id=thread-messages data-testid=thread-messages></div><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary class=primary>Reply</button></div></main><script>
let token=new URLSearchParams(location.search).get('token')||localStorage.getItem('huddle.token'),cid=null,mid=null;let $=x=>document.querySelector('[data-testid="'+x+'"]');function api(p,o={}){o.headers=Object.assign({'Content-Type':'application/json'},o.headers||{},token?{Authorization:'Bearer '+token}:{});return fetch('/api'+p,o).then(x=>x.json())}function render(){api('/workspaces').then(x=>{let w=x.workspaces?.[0];if(!w)return;api('/workspaces/'+w.slug).then(y=>{let cs=y.channels||[];cid=cid||cs[0]?.id;$('channel-list').innerHTML=cs.map(c=>'<div data-testid=channel-entry data-channel-id="'+c.id+'" data-channel-name="'+c.name+'">#'+c.name+'</div>').join('');document.querySelectorAll('[data-channel-entry]').forEach(e=>e.onclick=()=>{cid=e.dataset.channelId;load()});load()})})}function load(){api('/channels/'+cid+'/messages').then(x=>{let rows=x.messages.slice().reverse();$('message-list').innerHTML=rows.map(m=>'<div class=msg data-testid=message><b>'+m.author.display_name+'</b> <span data-testid=message-body>'+m.body+'</span><button data-testid=open-thread-btn>Replies</button><button data-testid=reaction-button>😊</button><div data-testid=emoji-picker style="display:none"><button type=button data-testid=emoji-option>😊</button><button type=button data-testid=emoji-option>👍</button><button type=button data-testid=emoji-option>❤️</button></div><span data-testid=reaction-chip>'+((m.reactions||[])[0]?.emoji||'')+'</span></div>').join('');document.querySelectorAll('[data-testid=open-thread-btn]').forEach((b,i)=>b.onclick=()=>{mid=rows[i].id;$('thread-panel').style.display='block';api('/messages/'+mid+'/replies').then(r=>{$('thread-messages').innerHTML=(r.replies||[]).map(q=>'<div>'+q.body+'</div>').join('')})});document.querySelectorAll('[data-testid=reaction-button]').forEach((b,i)=>b.onclick=()=>{let picker=b.parentElement.querySelector('[data-testid=emoji-picker]');picker.style.display='block';picker.querySelectorAll('[data-testid=emoji-option]').forEach(opt=>opt.onclick=()=>api('/messages/'+rows[i].id+'/reactions',{method:'POST',body:JSON.stringify({emoji:opt.textContent})}).then(load))})})}$('auth-form').onsubmit=e=>{e.preventDefault();let f=new FormData(e.target);api('/auth/login',{method:'POST',body:JSON.stringify({username:f.get('username'),password:f.get('password')})}).then(x=>{token=x.token;localStorage.setItem('huddle.token',token);render()})};$('send-btn').onclick=()=>{let v=$('message-input').value;if(v.trim())api('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body:v})}).then(()=>{$('message-input').value='';load()})};$('thread-send').onclick=()=>{let v=$('thread-input').value;if(v&&mid)api('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body:v,parent_id:mid})}).then(q=>{ $('thread-input').value='';let el=document.createElement('div');el.textContent=q.message&&q.message.body||v;$('thread-messages').appendChild(el)})};$('close-thread').onclick=()=>{$('thread-panel').style.display='none'};if(token){window.__huddle_token=token;render()} 
</script></body></html>'''
def irc_poller():
    while True:
        time.sleep(0.1)
        try:
            c=db()
            for cid, entries in list(ircs.items()):
                for f,nick in list(entries):
                    key=(id(f),cid)
                    last=irc_last.get(key,0)
                    rows=c.execute("select m.event_id,m.body,u.username,w.slug,ch.name from messages m join users u on u.id=m.author_id join channels ch on ch.id=m.channel_id join workspaces w on w.id=ch.workspace_id where m.channel_id=? and m.event_id>? order by m.event_id",(cid,last)).fetchall()
                    for r in rows:
                        target='#'+r['slug']+'/'+r['name']
                        f.write((':'+r['username']+'!user@localhost PRIVMSG '+target+' :'+r['body']+'\r\n').encode())
                        f.flush()
                        irc_last[key]=r['event_id']
            c.close()
        except Exception as exc:
            try:
                with open('/tmp/irc-poller-debug.log','a') as log: log.write(repr(exc)+'\n')
            except: pass
            continue

def irc_server():
    threading.Thread(target=irc_poller,daemon=True).start()
    s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen()
    while True:
        c,_=s.accept();threading.Thread(target=irc_client,args=(c,),daemon=True).start()
def irc_client(s):
    token=None; nick='guest'; registered=False; joined={}
    f=s.makefile('rwb')
    def out(x): f.write((x+'\r\n').encode()); f.flush()
    try:
        while True:
            line=f.readline()
            if not line: break
            z=line.decode(errors='ignore').strip(); a=z.split(' ',2); cmd=a[0].upper()
            if cmd=='PASS':
                token=a[1] if len(a)>1 else ''
                c=db(); ok=c.execute('select user_id from tokens where token=?',(token,)).fetchone(); c.close()
                if not ok: out(':huddle 464 '+nick+' :Password incorrect')
            elif cmd=='NICK':
                nn=a[1] if len(a)>1 else nick
                if nn in ircnicks and nn!=nick: out(':huddle 433 '+nick+' '+nn+' :Nickname is already in use')
                else:
                    ircnicks.discard(nick); nick=nn
                    if registered: out(':huddle 001 '+nick+' :Welcome')
            elif cmd=='USER' and token:
                c=db(); ok=c.execute('select user_id from tokens where token=?',(token,)).fetchone(); c.close()
                if not ok: continue
                registered=True; ircnicks.add(nick)
                for n in ('001','002','003','004','005'):out(':huddle '+n+' '+nick+' :Welcome')
                out(':huddle 422 '+nick+' :MOTD File is missing')
            elif cmd=='PING': out(':huddle PONG '+(a[1] if len(a)>1 else ''))
            elif cmd=='QUIT': break
            elif cmd in ('JOIN','NAMES'):
                ch=a[1] if len(a)>1 else ''
                m=re.match(r'^#([^/]+)/([^/]+)$',ch)
                c=db(); r=c.execute('select c.id,w.slug,c.name from channels c join workspaces w on w.id=c.workspace_id where w.slug=? and c.name=?', (m.group(1),m.group(2))) .fetchone() if m else None
                if not r: out(':huddle 403 '+nick+' '+ch+' :No such channel'); continue
                joined[r['id']]=ch
                ircs.setdefault(r['id'],[]).append((f,nick)); irc_last[(id(f),r['id'])]=init_event(c,r['id'])
                out(':'+nick+'!user@localhost JOIN '+ch)
                out(':huddle 353 '+nick+' = '+ch+' :'+nick)
                out(':huddle 366 '+nick+' '+ch+' :End of NAMES')
                topic=c.execute('select topic from channels where id=?',(r['id'],)).fetchone()['topic']
                out(':huddle '+('332 '+nick+' '+ch+' :'+topic if topic else '331 '+nick+' '+ch+' :No topic'))
                c.close()
            elif cmd=='PRIVMSG' and len(a)>2 and registered:
                ch=a[1]; body=a[2].lstrip(':'); m=re.match(r'^#([^/]+)/([^/]+)$',ch)
                c=db(); r=c.execute('select c.* from channels c join workspaces w on w.id=c.workspace_id where w.slug=? and c.name=?',(m.group(1),m.group(2))).fetchone() if m else None
                ur=c.execute('select user_id from tokens where token=?',(token,)).fetchone()
                if r and ur:
                    eid=init_event(c,r['id'])+1
                    c.execute('insert into messages(channel_id,author_id,body,created_at,event_id) values(?,?,?,?,?)',(r['id'],ur['user_id'],body,now(),eid)); mid=c.execute('select last_insert_rowid()').fetchone()[0]; c.commit()
                    rr=c.execute('select * from messages where id=?',(mid,)).fetchone(); x=msg(rr)
                    broadcast(r['id'],{'type':'message.created','event_id':eid,'channel_id':r['id'],'message':x})
                c.close()
            elif cmd in ('WHO','LIST','TOPIC','MODE'): pass
            else: out(':huddle 421 '+nick+' '+cmd+' :Unknown command')
    finally:
        ircnicks.discard(nick)
        for cid in list(joined):
            ircs[cid]=[(ff,nn) for ff,nn in ircs.get(cid,[]) if ff is not f]
            if not ircs[cid]: ircs.pop(cid,None)
        try:f.close()
        except:pass
if __name__=='__main__':
    port=int(os.getenv('PORT','8000'))
    if os.getenv('IRC')=='1': irc_server()
    else:
        threading.Thread(target=message_poller,daemon=True).start()
        ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
