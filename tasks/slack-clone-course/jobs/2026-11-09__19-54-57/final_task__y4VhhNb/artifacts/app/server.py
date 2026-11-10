#!/usr/bin/env python3
import base64, hashlib, json, os, re, secrets, socket, socketserver, sqlite3, struct, threading, time, uuid, sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote
DB='/app/chat.db'
LOCK=threading.RLock()
DB_READY=False
WSCLIENTS={}
IRCCLIENTS=[]
IRC_NICKS=set()
ONLINE_USERS=set()
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def uid(prefix=''): return prefix+secrets.token_hex(10)
def db():
    global DB_READY
    c=sqlite3.connect(DB,timeout=30,check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=30000')
    if not DB_READY:
        with LOCK:
            if not DB_READY:
                c.execute('PRAGMA journal_mode=WAL')
                c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id TEXT);
    CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id TEXT,join_mode TEXT);
    CREATE TABLE IF NOT EXISTS members(workspace_id TEXT,user_id TEXT,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY,workspace_id TEXT,name TEXT,is_private INTEGER,is_dm INTEGER,topic TEXT,is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS cmembers(channel_id TEXT,user_id TEXT,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,channel_id TEXT,author_id TEXT,body TEXT,parent_id TEXT,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,event_id INTEGER);
    CREATE TABLE IF NOT EXISTS reactions(message_id TEXT,user_id TEXT,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id TEXT PRIMARY KEY,pinned_by TEXT,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,uploader_id TEXT,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB,attached INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS groups_(id TEXT PRIMARY KEY,workspace_id TEXT,handle TEXT UNIQUE,name TEXT,creator_id TEXT,user_ids TEXT);
    CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,workspace_id TEXT,invited_user_id TEXT,uses INTEGER,max_uses INTEGER,expires REAL);
    CREATE TABLE IF NOT EXISTS seq(channel_id TEXT PRIMARY KEY,n INTEGER);
    CREATE TABLE IF NOT EXISTS readstate(channel_id TEXT,user_id TEXT,last_read_event_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS eventlog(channel_id TEXT,event_id INTEGER,frame TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS message_files(message_id TEXT,file_id TEXT,PRIMARY KEY(message_id,file_id));
                ''')
                c.commit()
                DB_READY=True
    return c

def userobj(r):
    return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def channel(r):
    return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ['id','workspace_id','name','is_private','is_dm','topic','is_archived']}
def msg(c,r):
    a=c.execute('select * from users where id=?',(r['author_id'],)).fetchone()
    rs=[]
    for x in c.execute('select emoji,count(*) n from reactions where message_id=? group by emoji',(r['id'],)):
        ids=[z['user_id'] for z in c.execute('select user_id from reactions where message_id=? and emoji=?',(r['id'],x['emoji']))]
        rs.append({'emoji':x['emoji'],'count':x['n'],'user_ids':ids})
    rc=c.execute('select count(*) n from messages where parent_id=? and deleted=0',(r['id'],)).fetchone()['n']
    fs=[dict(x) for x in c.execute('select id,uploader_id,filename,content_type,size,created_at from files where id in (select file_id from message_files where message_id=?)',(r['id'],))]
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':rc,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':fs,'reactions':rs,'mentions':mentions(c,r['body'],r['author_id'],r['channel_id'])}
def mentions(c,body,author,cid):
    ch=c.execute('select workspace_id from channels where id=?',(cid,)).fetchone(); out=[]
    for name in re.findall(r'(?<![\w@])@([A-Za-z0-9_-]+)',body):
        if name=='channel': ids=[x['user_id'] for x in c.execute('select user_id from cmembers where channel_id=?',(cid,))]
        elif name=='here': ids=[x['user_id'] for x in c.execute('select user_id from cmembers where channel_id=?',(cid,)) if x['user_id'] in ONLINE_USERS]
        else:
            g=c.execute('select user_ids from groups_ where workspace_id=? and handle=?',(ch['workspace_id'],name)).fetchone()
            if g:
                try: ids=json.loads(g['user_ids'])
                except: ids=[]
            else:
                x=c.execute('select id from users where username=?',(name,)).fetchone(); ids=[x['id']] if x else []
        for i in ids:
            if i!=author and c.execute('select 1 from cmembers where channel_id=? and user_id=?',(cid,i)).fetchone() and i not in out: out.append(i)
    return out
def auth(c,h):
    x=h.get('Authorization','')
    if not x.startswith('Bearer '): return None
    r=c.execute('select u.* from tokens t join users u on u.id=t.user_id where t.token=?',(x[7:],)).fetchone()
    return r
def allowed(c,uid,cid):
    return bool(c.execute('select 1 from cmembers where channel_id=? and user_id=?',(cid,uid)).fetchone())
def event(c,cid,typ,payload):
    c.execute('BEGIN IMMEDIATE')
    nrow=c.execute('select n from seq where channel_id=?',(cid,)).fetchone()
    n=nrow['n']+1 if nrow else 1
    c.execute('insert or replace into seq values(?,?)',(cid,n))
    e={'type':typ,'event_id':n,'channel_id':cid}; e.update(payload)
    c.execute('insert or replace into eventlog values(?,?,?)',(cid,n,json.dumps(e)))
    c.commit()
    for w in list(WSCLIENTS.get(cid,[])):
        try: w.send(e)
        except Exception:
            try: WSCLIENTS[cid].remove(w)
            except (ValueError,KeyError): pass
    ch=c.execute('select * from channels where id=?',(cid,)).fetchone()
    name='#'+c.execute('select slug from workspaces where id=?',(ch['workspace_id'],)).fetchone()['slug']+'/'+ch['name'] if ch and ch['workspace_id'] else ''
    if typ=='message.created' and name:
        for ir in list(IRCCLIENTS):
            if name in ir.joined:
                ir.sendraw(':web!user@host PRIVMSG '+name+' :'+payload['message']['body'])
    return e

class W:
    def __init__(self,s): self.s=s; self.lock=threading.Lock()
    def send(self,o):
        b=json.dumps(o).encode(); n=len(b)
        if n<126: h=bytes([129,n])
        elif n<65536: h=bytes([129,126])+struct.pack('>H',n)
        else: h=bytes([129,127])+struct.pack('>Q',n)
        with self.lock:
            previous=self.s.gettimeout()
            try:
                self.s.settimeout(0.25); self.s.sendall(h+b)
            finally:
                self.s.settimeout(previous)
    def recv(self):
        h=self.s.recv(2)
        if not h:return None
        n=h[1]&127
        if n==126:n=struct.unpack('>H',self.s.recv(2))[0]
        elif n==127:n=struct.unpack('>Q',self.s.recv(8))[0]
        mask=self.s.recv(4); b=self.s.recv(n)
        return json.loads(bytes(x^mask[i%4] for i,x in enumerate(b)))
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def sendj(self,status,o,headers=None):
        b=json.dumps(o).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b)))
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers();self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
    def do_GET(self):
        p=urlparse(self.path); path=p.path; q=parse_qs(p.query); c=db()
        if path=='/':
            html=HTML.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(html)));self.end_headers();self.wfile.write(html);return
        if path=='/api/health': return self.sendj(200,{'status':'ok','node_id':int(self.server.server_port)-8000})
        if path=='/api/ws':
            tok=q.get('token',[''])[0]; u=c.execute('select u.* from tokens t join users u on u.id=t.user_id where t.token=?',(tok.replace('Bearer ',''),)).fetchone()
            if not u:return self.sendj(401,{})
            key=self.headers.get('Sec-WebSocket-Key'); acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers()
            w=W(self.connection); subscribed={}; stop=threading.Event()
            def poll():
                while not stop.wait(.2):
                    try:
                        for cid,last in list(subscribed.items()):
                            cc=db()
                            for row in cc.execute('select event_id,frame from eventlog where channel_id=? and event_id>? order by event_id',(cid,last)):
                                frame=json.loads(row['frame']); w.send(frame); subscribed[cid]=row['event_id']
                    except Exception: return
            threading.Thread(target=poll,daemon=True).start()
            try:
                while True:
                    x=w.recv()
                    if not x:break
                    cid=str(x.get('channel_id')); head=c.execute('select n from seq where channel_id=?',(cid,)).fetchone(); head=head['n'] if head else 0
                    if x['type']=='subscribe':
                        ONLINE_USERS.add(u['id']); WSCLIENTS.setdefault(cid,[]).append(w); subscribed[cid]=head
                        w.send({'type':'subscribed','channel_id':cid,'head_event_id':head})
                    elif x['type']=='resume':
                        since=int(x.get('since_event_id',0))
                        for r in c.execute('select frame from eventlog where channel_id=? and event_id>? order by event_id',(cid,since,)):
                            w.send(json.loads(r['frame']))
                        subscribed[cid]=head
                        w.send({'type':'resumed','channel_id':cid,'head_event_id':head})
            except: pass
            stop.set()
            return
        u=auth(c,self.headers)
        if path.startswith('/api/') and path not in ['/api/health'] and not u:return self.sendj(401,{})
        if path=='/api/auth/me':return self.sendj(200,{'user':userobj(u)})
        if path=='/api/workspaces':
            rows=[] 
            for r in c.execute('select w.* from workspaces w join members m on m.workspace_id=w.id where m.user_id=?',(u['id'],)):rows.append(dict(r))
            return self.sendj(200,{'workspaces':rows})
        m=re.match(r'/api/workspaces/([^/]+)/members$',path)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
            if not w:return self.sendj(404,{})
            if not c.execute('select 1 from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone():return self.sendj(403,{})
            return self.sendj(200,{'members':[{'user_id':x['user_id'],'role':x['role']} for x in c.execute('select user_id,role from members where workspace_id=?',(w['id'],))]})
        m=re.match(r'/api/workspaces/([^/]+)/invitations$',path)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone(); r=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone() if w else None
            if not w:return self.sendj(404,{})
            if not r or r['role'] not in ('owner','admin'):return self.sendj(403,{})
            rows=[dict(x) for x in c.execute('select code,workspace_id,invited_user_id,uses,max_uses,expires from invites where workspace_id=? and uses<max_uses and expires>?',(w['id'],time.time()))]
            return self.sendj(200,{'invitations':rows})
        m=re.match(r'/api/channels/([^/]+)/read$',path)
        if m:
            ch=m.group(1); x=c.execute('select last_read_event_id from readstate where channel_id=? and user_id=?',(ch,u['id'])).fetchone()
            last=x['last_read_event_id'] if x else 0
            head=(c.execute('select n from seq where channel_id=?',(ch,)).fetchone() or {'n':0})['n']
            mentions_count=0
            unread=max(0,head-last)
            for z in c.execute('select body,author_id,event_id from messages where channel_id=? and deleted=0 and event_id>?',(ch,last)):
                if z['author_id']!=u['id'] and u['username'] in re.findall(r'(?<![\\w@])@([A-Za-z0-9_]+)',z['body']): mentions_count+=1
            return self.sendj(200,{'read_state':{'channel_id':ch,'last_read_event_id':last,'unread_count':unread,'mention_count':mentions_count}})
        m=re.match(r'/api/channels/([^/]+)/pins$',path)
        if m:
            if not allowed(c,u['id'],m.group(1)):return self.sendj(403,{})
            rows=[]
            for x in c.execute('select p.*,m.* from pins p join messages m on m.id=p.message_id where m.channel_id=? order by p.pinned_at desc',(m.group(1),)):
                rows.append({'message':msg(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']})
            limit=int(q.get('limit',['50'])[0])
            if limit<1 or limit>200:return self.sendj(400,{})
            cur=q.get('cursor',[''])[0]; start=0
            if cur:
                ids=[x['message']['id'] for x in rows]
                if cur not in ids:return self.sendj(400,{})
                start=ids.index(cur)+1
            out=rows[start:start+limit]; nxt=out[-1]['message']['id'] if start+limit<len(rows) else None
            return self.sendj(200,{'pins':out,'next_cursor':nxt})
        m=re.match(r'/api/workspaces/([^/]+)$',path)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
            if not w:return self.sendj(404,{})
            chans=[channel(x) for x in c.execute('select * from channels where workspace_id=? and (is_archived=0 or ?) and (is_private=0 or exists(select 1 from cmembers z where z.channel_id=channels.id and z.user_id=?))',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'),u['id']))]
            states=[]
            for z in c.execute('select id from channels where workspace_id=?',(w['id'],)):
                rr=c.execute('select last_read_event_id from readstate where channel_id=? and user_id=?',(z['id'],u['id'])).fetchone()
                last=rr['last_read_event_id'] if rr else 0; head=(c.execute('select n from seq where channel_id=?',(z['id'],)).fetchone() or {'n':0})['n']
                states.append({'channel_id':z['id'],'last_read_event_id':last,'unread_count':max(0,head-last),'mention_count':0})
            return self.sendj(200,{'workspace':dict(w),'channels':chans,'read_state':states})
        m=re.match(r'/api/channels/([^/]+)/messages$',path)
        if m:
            cid=m.group(1); rows=list(c.execute('select * from messages where channel_id=? and deleted=0 order by created_at desc',(cid,)))
            limit=int(q.get('limit',['50'])[0]);
            if limit<1 or limit>200:return self.sendj(400,{})
            cur=q.get('cursor',[''])[0]; start=0
            if cur:
                ids=[x['id'] for x in rows]
                if cur not in ids:return self.sendj(400,{})
                start=ids.index(cur)+1
            out=rows[start:start+limit]; nxt=(out[-1]['id'] if start+limit<len(rows) else None)
            return self.sendj(200,{'messages':[msg(c,x) for x in out],'next_cursor':nxt})
        m=re.match(r'/api/messages/([^/]+)/replies$',path)
        if m:
            rows=list(c.execute('select * from messages where parent_id=? and deleted=0 order by created_at',(m.group(1),)))
            limit=int(q.get('limit',['50'])[0])
            if limit<1 or limit>200:return self.sendj(400,{})
            cur=q.get('cursor',[''])[0]; start=0
            if cur:
                ids=[x['id'] for x in rows]
                if cur not in ids:return self.sendj(400,{})
                start=ids.index(cur)+1
            out=rows[start:start+limit]; nxt=out[-1]['id'] if start+limit<len(rows) else None
            return self.sendj(200,{'replies':[msg(c,x) for x in out],'next_cursor':nxt})
        m=re.match(r'/api/channels/([^/]+)/members$',path)
        if m:return self.sendj(200,{'members':[userobj(c.execute('select * from users where id=?',(x['user_id'],)).fetchone()) for x in c.execute('select user_id from cmembers where channel_id=?',(m.group(1),))]})
        m=re.match(r'/api/users/([^/]+)$',path)
        if m:
            x=c.execute('select * from users where id=?',(m.group(1),)).fetchone();return self.sendj(200,{'user':userobj(x)}) if x else self.sendj(404,{})
        m=re.match(r'/api/workspaces/([^/]+)/groups$',path)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
            if not w:return self.sendj(404,{})
            if not c.execute('select 1 from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone():return self.sendj(403,{})
            rows=[]
            for x in c.execute('select * from groups_ where workspace_id=?',(w['id'],)):
                rows.append({'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['user_ids'])})
            return self.sendj(200,{'groups':rows})
        if path=='/api/search':
            text=q.get('q',[''])[0]; limit=int(q.get('limit',['50'])[0])
            if limit<1 or limit>200:return self.sendj(400,{})
            rows=list(c.execute('select m.* from messages m join channels ch on ch.id=m.channel_id join cmembers cm on cm.channel_id=ch.id and cm.user_id=? where m.deleted=0 and m.body like ? order by m.created_at desc',(u['id'],'%'+text+'%')))
            if q.get('workspace_id'): rows=[x for x in rows if c.execute('select 1 from channels where id=? and workspace_id=?',(x['channel_id'],q['workspace_id'][0])).fetchone()]
            cur=q.get('cursor',[''])[0];start=0
            if cur:
                ids=[x['id'] for x in rows]
                if cur not in ids:return self.sendj(400,{})
                start=ids.index(cur)+1
            out=rows[start:start+limit];nxt=out[-1]['id'] if start+limit<len(rows) else None
            return self.sendj(200,{'results':[msg(c,x) for x in out],'next_cursor':nxt})
        if path.startswith('/api/files/'):
            fid=path.split('/')[3];x=c.execute('select * from files where id=?',(fid,)).fetchone()
            if not x:return self.sendj(404,{})
            if path.endswith('/download'):
                self.send_response(200);self.send_header('Content-Type',x['content_type']);self.send_header('Content-Disposition','attachment; filename="'+x['filename']+'"');self.end_headers();self.wfile.write(x['data']);return
            return self.sendj(200,{'file':{k:x[k] for k in ['id','uploader_id','filename','content_type','size','created_at']}})
        return self.sendj(404,{})
    def do_POST(self):
        self.mutate()
    def do_PATCH(self):
        self.mutate()
    def do_DELETE(self):
        self.mutate()
    def mutate(self):
        with LOCK:
            return self._mutate()
    def _mutate(self):
        path=unquote(urlparse(self.path).path);c=db();u=auth(c,self.headers)
        if path=='/api/auth/register':
            b=self.body(); name=b.get('username','')
            if not re.fullmatch(r'[A-Za-z0-9_]+',name) or len(b.get('password',''))<8:return self.sendj(400,{})
            if c.execute('select 1 from users where username=?',(name,)).fetchone():return self.sendj(409,{})
            i=uid('u'); c.execute('insert into users values(?,?,?,?,?,?,?,?)',(i,name,b['password'],b.get('display_name',name),'UTC','','','')); t=uid('t'); c.execute('insert into tokens values(?,?)',(t,i)); c.commit(); return self.sendj(201,{'user':userobj(c.execute('select * from users where id=?',(i,)).fetchone()),'token':t})
        if path=='/api/auth/login':
            b=self.body();x=c.execute('select * from users where username=? and password=?',(b.get('username'),b.get('password'))).fetchone()
            if not x:return self.sendj(401,{})
            t=uid('t');c.execute('insert into tokens values(?,?)',(t,x['id']));c.commit();return self.sendj(200,{'user':userobj(x),'token':t})
        if not u:return self.sendj(401,{})
        if path=='/api/files' and self.command=='POST':
            ctype=self.headers.get('Content-Type','')
            if not ctype.startswith('multipart/form-data'):
                return self.sendj(400,{})
            boundary=ctype.split('boundary=',1)[1].encode()
            raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
            parts=raw.split(b'--'+boundary)
            data=None; filename='upload'; ftype='application/octet-stream'
            for part in parts:
                if b'filename=' not in part: continue
                head,sep,content=part.partition(b'\r\n\r\n')
                if not sep: continue
                fm=re.search(br'filename="([^"]*)"',head)
                tm=re.search(br'Content-Type:\s*([^\r\n]+)',head,re.I)
                filename=fm.group(1).decode(errors='replace') if fm else filename
                ftype=tm.group(1).decode(errors='replace') if tm else ftype
                data=content.rstrip(b'\r\n-')
                break
            if data is None:return self.sendj(400,{})
            if len(data)>10*1024*1024:return self.sendj(413,{})
            fid=uid('f'); created=now()
            c.execute('insert into files values(?,?,?,?,?,?,?,?)',(fid,u['id'],filename,ftype,len(data),created,data,0));c.commit()
            return self.sendj(201,{'file':{'id':fid,'uploader_id':u['id'],'filename':filename,'content_type':ftype,'size':len(data),'created_at':created}})
        b=self.body()
        if path=='/api/users/me' and self.command=='PATCH':
            tz=b.get('timezone')
            if tz is not None and tz not in ('UTC','Etc/UTC','Europe/London','America/New_York','America/Los_Angeles'):return self.sendj(400,{})
            allowed_fields={'display_name','timezone','avatar_url','status_text','status_emoji'}
            vals={k:b[k] for k in b if k in allowed_fields}
            if any(len(str(v))>250 for v in vals.values()):return self.sendj(400,{})
            if vals:
                sets=','.join(k+'=?' for k in vals); c.execute('update users set '+sets+' where id=?',(*vals.values(),u['id']));c.commit()
            fresh=c.execute('select * from users where id=?',(u['id'],)).fetchone(); payload={'type':'user.updated','user':userobj(fresh)}
            for arr in list(WSCLIENTS.values()):
                for ww in list(arr):
                    try: ww.send(payload)
                    except: pass
            return self.sendj(200,{'user':userobj(fresh)})
        m=re.match(r'/api/workspaces/([^/]+)/groups(?:/([^/]+))?$',path)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
            role=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone() if w else None
            if not w:return self.sendj(404,{})
            if not role:return self.sendj(403,{})
            handle=m.group(2)
            if self.command=='POST' and not handle:
                x=b.get('handle','')
                if not re.fullmatch(r'[A-Za-z0-9_-]+',x):return self.sendj(400,{})
                if c.execute('select 1 from groups_ where workspace_id=? and handle=?',(w['id'],x)).fetchone():return self.sendj(409,{})
                gid=uid('g'); ids=b.get('user_ids',[])
                c.execute('insert into groups_ values(?,?,?,?,?,?)',(gid,w['id'],x,b.get('name',x),u['id'],json.dumps(ids)));c.commit()
                return self.sendj(201,{'group':{'id':gid,'handle':x,'name':b.get('name',x),'member_user_ids':ids}})
            if not handle:return self.sendj(404,{})
            g=c.execute('select * from groups_ where workspace_id=? and handle=?',(w['id'],handle)).fetchone()
            if not g:
                return self.sendj(200,{'deleted':True}) if self.command=='DELETE' else self.sendj(404,{})
            if self.command in ('PATCH','DELETE') and u['id'] not in (g['creator_id'],w['owner_id']):return self.sendj(403,{})
            if self.command=='DELETE':
                c.execute('delete from groups_ where id=?',(g['id'],));c.commit();return self.sendj(200,{'deleted':True})
            if self.command=='PATCH':
                name=b.get('name',g['name']); ids=b.get('user_ids',json.loads(g['user_ids']))
                c.execute('update groups_ set name=?,user_ids=? where id=?',(name,json.dumps(ids),g['id']));c.commit()
                return self.sendj(200,{'group':{'id':g['id'],'handle':g['handle'],'name':name,'member_user_ids':ids}})
        m=re.match(r'/api/workspaces/([^/]+)/transfer_ownership$',path)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
            me=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])) if w else None
            if not w or not me.fetchone() or u['id']!=w['owner_id']:return self.sendj(403,{})
            target=b.get('new_owner_id'); tr=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],target)).fetchone()
            if not tr or tr['role']!='admin':return self.sendj(400,{})
            c.execute('update members set role=? where workspace_id=? and user_id=?',('admin',w['id'],u['id']))
            c.execute('update members set role=? where workspace_id=? and user_id=?',('owner',w['id'],target))
            c.execute('update workspaces set owner_id=? where id=?',(target,w['id']));c.commit()
            return self.sendj(200,{'workspace':dict(c.execute('select * from workspaces where id=?',(w['id'],)).fetchone())})
        m=re.match(r'/api/workspaces/([^/]+)/invitations$',path)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone();r=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone() if w else None
            if not w:return self.sendj(404,{})
            if not r or r['role'] not in ('owner','admin'):return self.sendj(403,{})
            code=secrets.token_urlsafe(12); exp=time.time()+int(b.get('expires_in_seconds',604800)); target=b.get('invited_user_id'); mx=1 if target or b.get('invited_username') else int(b.get('max_uses',1))
            c.execute('insert into invites values(?,?,?,?,?,?)',(code,w['id'],target,0,mx,exp));c.commit()
            return self.sendj(201,{'invitation':{'code':code,'workspace_id':w['id'],'max_uses':mx,'uses':0,'expires_at':exp}})
        m=re.match(r'/api/workspaces/([^/]+)/invitations/([^/]+)$',path)
        if m and self.command=='DELETE':
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone(); r=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone() if w else None
            if not w or not r or r['role'] not in ('owner','admin'): return self.sendj(403,{})
            if not c.execute('select 1 from invites where code=? and workspace_id=?',(m.group(2),w['id'])).fetchone(): return self.sendj(404,{})
            c.execute('delete from invites where code=?',(m.group(2),));c.commit();return self.sendj(200,{'deleted':True})
        m=re.match(r'/api/invitations/([^/]+)/accept$',path)
        if m:
            x=c.execute('select * from invites where code=?',(m.group(1),)).fetchone()
            if not x or x['uses']>=x['max_uses'] or x['expires']<time.time():return self.sendj(404,{})
            if x['invited_user_id'] and x['invited_user_id']!=u['id']:return self.sendj(403,{})
            c.execute('insert or replace into members values(?,?,?)',(x['workspace_id'],u['id'],'member'));c.execute('update invites set uses=uses+1 where code=?',(m.group(1),));c.commit()
            return self.sendj(200,{'workspace_id':x['workspace_id']})
        m=re.match(r'/api/channels/([^/]+)/read$',path)
        if m:
            ch=m.group(1); bval=max(0,int(b.get('last_read_event_id',0))); old=c.execute('select last_read_event_id from readstate where channel_id=? and user_id=?',(ch,u['id'])).fetchone(); last=max(bval,old['last_read_event_id'] if old else 0)
            c.execute('insert or replace into readstate values(?,?,?)',(ch,u['id'],last));c.commit()
            return self.sendj(200,{'read_state':{'channel_id':ch,'last_read_event_id':last,'unread_count':0,'mention_count':0}})
        m=re.match(r'/api/messages/([^/]+)/pin$',path)
        if m and self.command=='DELETE':
            c.execute('delete from pins where message_id=?',(m.group(1),));c.commit();return self.sendj(200,{'unpinned':True})
        if m:
            r=c.execute('select * from messages where id=?',(m.group(1),)).fetchone()
            if not r:return self.sendj(404,{})
            c.execute('insert or ignore into pins values(?,?,?)',(r['id'],u['id'],now()));c.commit();x=c.execute('select * from pins where message_id=?',(r['id'],)).fetchone()
            return self.sendj(200,{'pin':{'message_id':x['message_id'],'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']}})
        # workspace members, settings, and role management
        m=re.match(r'/api/workspaces/([^/]+)/members(?:/([^/]+))?$',path)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
            if not w:return self.sendj(404,{})
            me=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone()
            if not me:return self.sendj(403,{})
            if not m.group(2):
                rows=[]
                for x in c.execute('select user_id,role from members where workspace_id=?',(w['id'],)):
                    rows.append({'user_id':x['user_id'],'role':x['role']})
                return self.sendj(200,{'members':rows})
            target=m.group(2); role=b.get('role')
            old=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],target)).fetchone()
            if not old:return self.sendj(404,{})
            if role not in ('owner','admin','member','guest'):return self.sendj(400,{})
            if role=='owner':return self.sendj(400,{})
            if target==w['owner_id'] or (me['role']!='owner' and (old['role']=='admin' or target==u['id'])):return self.sendj(403,{})
            if me['role'] not in ('owner','admin'):return self.sendj(403,{})
            c.execute('update members set role=? where workspace_id=? and user_id=?',(role,w['id'],target));c.commit()
            return self.sendj(200,{'member':{'user_id':target,'role':role}})
        m=re.match(r'/api/workspaces/([^/]+)$',path)
        if m and self.command=='PATCH':
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
            if not w:return self.sendj(404,{})
            me=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone()
            if not me or me['role'] not in ('owner','admin'):return self.sendj(403,{})
            if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return self.sendj(400,{})
            c.execute('update workspaces set name=coalesce(?,name),join_mode=coalesce(?,join_mode) where id=?',(b.get('name'),b.get('join_mode'),w['id']));c.commit()
            return self.sendj(200,{'workspace':dict(c.execute('select * from workspaces where id=?',(w['id'],)).fetchone())})
        # channel settings and membership operations
        m=re.match(r'/api/channels/([^/]+)/(leave|archive|unarchive)$',path)
        if m:
            ch=c.execute('select * from channels where id=?',(m.group(1),)).fetchone()
            if not ch:return self.sendj(404,{})
            w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone()
            role=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone()
            if m.group(2) in ('archive','unarchive'):
                if not role or role['role']!='owner':return self.sendj(403,{})
                val=1 if m.group(2)=='archive' else 0;c.execute('update channels set is_archived=? where id=?',(val,ch['id']));c.commit(); event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':channel(c.execute('select * from channels where id=?',(ch['id'],)).fetchone())})
                return self.sendj(200,{'channel':channel(c.execute('select * from channels where id=?',(ch['id'],)).fetchone())})
            c.execute('delete from cmembers where channel_id=? and user_id=?',(ch['id'],u['id']));c.commit();event(c,ch['id'],'member.left',{'user_id':u['id']})
            return self.sendj(200,{'channel':channel(ch)})
        m=re.match(r'/api/channels/([^/]+)$',path)
        if m and self.command=='PATCH':
            ch=c.execute('select * from channels where id=?',(m.group(1),)).fetchone()
            if not ch:return self.sendj(404,{})
            if len(b.get('topic',''))>250:return self.sendj(400,{})
            w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone()
            member=c.execute('select 1 from cmembers where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone()
            owner=u['id']==w['owner_id']
            if not member and not owner:return self.sendj(403,{})
            c.execute('update channels set topic=? where id=?',(b.get('topic',''),ch['id']));c.commit(); updated=channel(c.execute('select * from channels where id=?',(ch['id'],)).fetchone()); event(c,ch['id'],'channel.updated',{'channel':updated})
            return self.sendj(200,{'channel':updated})
        if path=='/api/workspaces':
            if not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')):return self.sendj(400,{})
            if c.execute('select 1 from workspaces where slug=?',(b['slug'],)).fetchone():return self.sendj(409,{})
            wid=uid('w');cid=uid('c');c.execute('insert into workspaces values(?,?,?,?,?)',(wid,b['slug'],b.get('name',''),u['id'],'open'));c.execute('insert into members values(?,?,?)',(wid,u['id'],'owner'));c.execute('insert into channels values(?,?,?,?,?,?,?)',(cid,wid,'general',0,0,'',0));c.execute('insert into cmembers values(?,?)',(cid,u['id']));c.commit();return self.sendj(201,{'workspace':dict(c.execute('select * from workspaces where id=?',(wid,)).fetchone()),'general_channel':channel(c.execute('select * from channels where id=?',(cid,)).fetchone())})
        m=re.match(r'/api/workspaces/([^/]+)/channels$',path)
        if m:
            w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone(); role=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone()
            if not role or role['role']=='guest' or (b.get('is_private') and role['role'] not in ('owner','admin')):return self.sendj(403,{})
            if not re.fullmatch(r'[a-z0-9-]{1,32}',b.get('name','')) or len(b.get('topic',''))>250:return self.sendj(400,{})
            if c.execute('select 1 from channels where workspace_id=? and name=?',(w['id'],b['name'])).fetchone():return self.sendj(409,{})
            cid=uid('c'); c.execute('insert into channels values(?,?,?,?,?,?,?)',(cid,w['id'],b['name'],int(bool(b.get('is_private'))),0,b.get('topic',''),0)); c.execute('insert into cmembers values(?,?)',(cid,u['id'])); c.commit(); return self.sendj(201,{'channel':channel(c.execute('select * from channels where id=?',(cid,)).fetchone())})
        m=re.match(r'/api/channels/([^/]+)/join$',path)
        if m:
            ch=c.execute('select * from channels where id=?',(m.group(1),)).fetchone()
            if not ch:return self.sendj(404,{})
            w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone(); mem=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone()
            if mem and mem['role']=='guest':return self.sendj(403,{})
            if ch['is_private'] and not c.execute('select 1 from cmembers where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone():return self.sendj(403,{})
            if not mem and w['join_mode']=='invite_only':return self.sendj(403,{})
            if not mem:c.execute('insert into members values(?,?,?)',(w['id'],u['id'],'member'))
            if not c.execute('select 1 from cmembers where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone():c.execute('insert into cmembers values(?,?)',(ch['id'],u['id']))
            c.commit();event(c,ch['id'],'member.joined',{'user_id':u['id']});return self.sendj(200,{'channel':channel(ch)})
        m=re.match(r'/api/channels/([^/]+)/messages$',path)
        if m:
            ch=c.execute('select * from channels where id=?',(m.group(1),)).fetchone()
            if not ch:return self.sendj(404,{})
            if ch['is_archived'] and b.get('body','').strip()!='/unarchive':return self.sendj(423,{})
            if not allowed(c,u['id'],ch['id']):return self.sendj(403,{})
            body=b.get('body','')
            if not body:return self.sendj(400,{})
            w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone()
            if body.startswith('/'):
                if body.startswith('/me'):
                    if body.strip()=='/me':return self.sendj(400,{})
                elif body.startswith('/shrug'):
                    body=(body[6:].strip()+' ¯\\_(ツ)_/¯').strip()
                elif body.startswith('/topic '):
                    topic=body[7:].strip()
                    if not topic or len(topic)>250:return self.sendj(400,{})
                    c.execute('update channels set topic=? where id=?',(topic,ch['id']));c.commit()
                    updated=channel(c.execute('select * from channels where id=?',(ch['id'],)).fetchone());event(c,ch['id'],'channel.updated',{'channel':updated})
                    return self.sendj(201,{'message':None,'channel':updated})
                elif body in ('/archive','/unarchive'):
                    if u['id']!=w['owner_id']:return self.sendj(403,{})
                    val=1 if body=='/archive' else 0;c.execute('update channels set is_archived=? where id=?',(val,ch['id']));c.commit()
                    updated=channel(c.execute('select * from channels where id=?',(ch['id'],)).fetchone());event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':updated})
                    return self.sendj(201,{'message':None,'channel':updated})
                elif body.startswith('/invite '):
                    name=body[8:].strip()
                    if not name.startswith('@'):return self.sendj(400,{})
                    target=c.execute('select * from users where username=?',(name[1:],)).fetchone()
                    if not target:return self.sendj(400,{})
                    c.execute('insert or ignore into cmembers values(?,?)',(ch['id'],target['id']));c.commit()
                    return self.sendj(201,{'message':None,'invited_user':userobj(target)})
                elif not body.startswith('//'):
                    return self.sendj(400,{})
            if body.startswith('/') and not body.startswith('//') and not body.startswith('/me') and not body.startswith('/shrug'):return self.sendj(400,{})
            file_ids=b.get('file_ids',[])
            if not isinstance(file_ids,list):return self.sendj(400,{})
            for fid in file_ids:
                f=c.execute('select uploader_id,attached from files where id=?',(fid,)).fetchone()
                if not f or f['uploader_id']!=u['id'] or f['attached']:return self.sendj(400,{})
            mid=uid('m');c.execute('insert into messages values(?,?,?,?,?,?,?,?,?)',(mid,ch['id'],u['id'],body,b.get('parent_id'),now(),None,0,None))
            for fid in file_ids:
                c.execute('insert into message_files values(?,?)',(mid,fid));c.execute('update files set attached=1 where id=?',(fid,))
            c.commit();r=c.execute('select * from messages where id=?',(mid,)).fetchone(); e=event(c,ch['id'],'message.reply' if r['parent_id'] else 'message.created',{'message':msg(c,r)});c.execute('update messages set event_id=? where id=?',(e['event_id'],mid));c.commit();return self.sendj(201,{'message':msg(c,c.execute('select * from messages where id=?',(mid,)).fetchone())})
        m=re.match(r'/api/messages/([^/]+)/reactions(?:/([^/]+))?$',path)
        if m:
            r=c.execute('select * from messages where id=?',(m.group(1),)).fetchone()
            if not r:return self.sendj(404,{})
            emoji=(m.group(2) or b.get('emoji',''))
            if self.command=='POST':c.execute('insert or ignore into reactions values(?,?,?)',(r['id'],u['id'],emoji))
            else:c.execute('delete from reactions where message_id=? and user_id=? and emoji=?',(r['id'],u['id'],emoji))
            c.commit(); event(c,r['channel_id'],'reaction.added' if self.command=='POST' else 'reaction.removed',{'message_id':r['id'],'emoji':emoji}); return self.sendj(200,{'reactions':msg(c,r)['reactions']})
        m=re.match(r'/api/messages/([^/]+)/pin$',path)
        if m and self.command=='DELETE':
            c.execute('delete from pins where message_id=?',(m.group(1),));c.commit()
            return self.sendj(200,{'unpinned':True})
        m=re.match(r'/api/messages/([^/]+)$',path)
        if m:
            r=c.execute('select * from messages where id=?',(m.group(1),)).fetchone()
            if not r:return self.sendj(404,{})
            ch=c.execute('select * from channels where id=?',(r['channel_id'],)).fetchone()
            w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone() if ch and ch['workspace_id'] else None
            if self.command=='PATCH':
                if r['deleted']:return self.sendj(409,{})
                if r['author_id']!=u['id']:return self.sendj(403,{})
                body=b.get('body','')
                if not body:return self.sendj(400,{})
                c.execute('update messages set body=?,edited_at=? where id=?',(body,now(),r['id']));c.commit()
                rr=c.execute('select * from messages where id=?',(r['id'],)).fetchone()
                event(c,rr['channel_id'],'message.updated',{'message':msg(c,rr)})
                return self.sendj(200,{'message':msg(c,rr)})
            if r['deleted']:return self.sendj(200,{'deleted':True})
            if r['author_id']!=u['id'] and (not w or w['owner_id']!=u['id']):return self.sendj(403,{})
            c.execute('update messages set deleted=1 where id=?',(r['id'],));c.commit()
            event(c,r['channel_id'],'message.deleted',{'message_id':r['id']})
            return self.sendj(200,{'deleted':True})
        if path=='/api/dms':
            target=b.get('user_id'); pair=sorted([u['id'],target])
            x=c.execute("select c.id from channels c join cmembers a on a.channel_id=c.id join cmembers z on z.channel_id=c.id where c.is_dm=1 and a.user_id=? and z.user_id=?",(pair[0],pair[1])).fetchone()
            if x:return self.sendj(200,{'channel_id':x['id']})
            cid=uid('c');c.execute('insert into channels values(?,?,?,?,?,?,?)',(cid,None,'dm-'+cid,0,1,'',0));c.execute('insert into cmembers values(?,?),(?,?)',(cid,pair[0],cid,pair[1]));c.commit();return self.sendj(200,{'channel_id':cid})
        return self.sendj(404,{})
HTML='''<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>body{margin:0;font:14px Arial;color:#222}button{border:0;padding:9px 14px;color:#fff;border-radius:4px}button[data-button-role=primary]{background:#007a5a}button[data-button-role=danger]{background:#e01e5a}button[data-button-role=secondary]{background:#1264a3}.app{display:flex;height:100vh}.side{width:240px;background:#3f0f45;color:white;padding:20px}.main{flex:1;padding:25px}.row{padding:10px;border-bottom:1px solid #ddd}</style></head><body><div id="root"></div><script>
const A='/api';let token=localStorage.getItem('huddle.token');async function api(p,o={}){o.headers={...(o.headers||{}),Authorization:'Bearer '+token,'Content-Type':'application/json'};let r=await fetch(A+p,o);return r.json()}function render(){if(!token){root.innerHTML='<div id="auth-modal"><form data-testid="auth-form"><h1>Huddle</h1><input name="username" required><input name="password" type="password" required><button data-testid="auth-submit" data-button-role="primary">Sign in</button><button type="button" data-testid="auth-toggle">Create account</button><div id="err"></div></form></div>';let signup=false;document.querySelector('[data-testid=auth-toggle]').onclick=()=>{signup=!signup;document.querySelector('[data-testid=auth-submit]').textContent=signup?'Sign up':'Sign in'};document.querySelector('form').onsubmit=async e=>{e.preventDefault();let b=Object.fromEntries(new FormData(e.target));let r=await fetch(A+(signup?'/auth/register':'/auth/login'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});let x=await r.json();if(!r.ok){err.textContent='Invalid credentials or registration';return}token=x.token;localStorage.setItem('huddle.token',token);render()};return}api('/workspaces').then(x=>{let w=x.workspaces[0];root.innerHTML='<div class="app"><aside class="side"><h2 data-testid="workspace-header">Huddle</h2><button data-testid="logout-btn" data-button-role="danger">Logout</button><div data-testid="channel-list"></div></aside><main class="main"><h2 data-testid="channel-title"># general</h2><div data-testid="message-list"></div><form id="mf"><input data-testid="message-input" name="body"><button data-testid="send-btn" data-button-role="primary">Send</button></form></main></div>';logout_btn.onclick=()=>{localStorage.removeItem('huddle.token');token=null;render()};if(w)api('/workspaces/'+w.slug).then(d=>{let list=document.querySelector('[data-testid=channel-list]');d.channels.forEach(ch=>{let z=document.createElement('button');z.dataset.testid='channel-entry';z.dataset.channelId=ch.id;z.dataset.channelName=ch.name;z.textContent='# '+ch.name;z.onclick=()=>load(ch);list.append(z)});if(d.channels[0])load(d.channels[0])})});}async function load(ch){document.querySelector('[data-testid=channel-title]').textContent='# '+ch.name;let d=await api('/channels/'+ch.id+'/messages');document.querySelector('[data-testid=message-list]').innerHTML=d.messages.map(m=>'<div class="row" data-testid="message"><b>'+m.author.display_name+'</b> <span data-testid="message-body">'+m.body+'</span></div>').join('')};render();
</script></body></html>'''
class HTTP(ThreadingHTTPServer): allow_reuse_address=True
def start_http(port): HTTP(('',port),H).serve_forever()
class IRC(socketserver.ThreadingTCPServer): allow_reuse_address=True
class IRCConn(socketserver.StreamRequestHandler):
    def setup(self):super().setup();self.nick=None;self.user=None;self.token=None;self.joined=set();self.pass_valid=False;self.nick_retry=False;IRCCLIENTS.append(self)
    def finish(self):
        if self in IRCCLIENTS:IRCCLIENTS.remove(self)
        if self.nick: IRC_NICKS.discard(self.nick)
        super().finish()
    def sendraw(self,x):
        try:
            self.wfile.write((x+'\r\n').encode())
            self.wfile.flush()
        except:
            pass
    def handle(self):
        c=db(); stop=threading.Event()
        def poll():
            sent={}
            while not stop.wait(.2):
                try:
                    cc=db()
                    for name in list(self.joined):
                        mm=re.match(r'#([^/]+)/(.+)',name)
                        if not mm: continue
                        ch=cc.execute('select c.id from channels c join workspaces w on w.id=c.workspace_id where w.slug=? and c.name=?',(mm.group(1),mm.group(2))).fetchone()
                        if not ch: continue
                        last=sent.get(ch['id'],0)
                        for row in cc.execute('select event_id,frame from eventlog where channel_id=? and event_id>? order by event_id',(ch['id'],last)):
                            frame=json.loads(row['frame'])
                            sent[ch['id']]=row['event_id']
                            if frame.get('type') in ('message.created','message.reply'):
                                self.sendraw(':web!user@host PRIVMSG '+name+' :'+frame['message']['body'])
                    cc.close()
                except Exception:
                    continue
        threading.Thread(target=poll,daemon=True).start()
        try:
            for line in self.rfile:
                a=line.decode(errors='ignore').strip().split(' ',2);cmd=a[0].upper() if a else ''
                if cmd=='PASS':
                    self.token=a[1] if len(a)>1 else ''
                    self.user=auth(c,{'Authorization':'Bearer '+self.token})
                    self.pass_valid=bool(self.user)
                    if not self.pass_valid:self.sendraw(':huddle 464 * :Password incorrect')
                elif cmd=='NICK':
                    candidate=a[1] if len(a)>1 else ''
                    if candidate in IRC_NICKS:
                        self.sendraw(':huddle 433 * '+candidate+' :Nickname is already in use')
                        self.nick_retry=True
                        continue
                    if self.nick: IRC_NICKS.discard(self.nick)
                    self.nick=candidate
                    if self.pass_valid: IRC_NICKS.add(self.nick)
                    if self.nick_retry and self.user and self.pass_valid:
                        self.sendraw(':huddle 001 '+self.nick+' :Welcome')
                        self.nick_retry=False
                elif cmd=='USER' and self.user and self.pass_valid:
                    for n in ['001','002','003','004','005']:self.sendraw(':huddle '+n+' '+self.nick+' :Welcome')
                    self.sendraw(':huddle 422 '+self.nick+' :No MOTD')
                elif cmd=='PING':self.sendraw('PONG '+(a[1] if len(a)>1 else ''))
                elif cmd=='JOIN' and self.user:
                    name=a[1];m=re.match(r'#([^/]+)/(.+)',name);ch=c.execute('select c.* from channels c join workspaces w on w.id=c.workspace_id where w.slug=? and c.name=?',(m.group(1),m.group(2))).fetchone() if m else None
                    if not ch:self.sendraw(':'+name+' 403 '+str(self.nick)+' '+name+' :No such channel');continue
                    self.joined.add(name);self.sendraw(':'+self.nick+'!user@host JOIN '+name);self.sendraw(':huddle 353 '+self.nick+' = '+name+' :'+self.nick);self.sendraw(':huddle 366 '+self.nick+' '+name+' :End of /NAMES list')
                elif cmd=='PRIVMSG' and self.user:
                    name=a[1];body=a[2][1:] if len(a)>2 and a[2].startswith(':') else '';m=re.match(r'#([^/]+)/(.+)',name);ch=c.execute('select c.* from channels c join workspaces w on w.id=c.workspace_id where w.slug=? and c.name=?',(m.group(1),m.group(2))).fetchone() if m else None
                    if ch:
                        mid=uid('m');c.execute('insert into messages values(?,?,?,?,?,?,?,?,?)',(mid,ch['id'],self.user['id'],body,None,now(),None,0,None));c.commit();e=event(c,ch['id'],'message.created',{'message':msg(c,c.execute('select * from messages where id=?',(mid,)).fetchone())});c.execute('update messages set event_id=? where id=?',(e['event_id'],mid));c.commit()
                elif cmd=='QUIT':stop.set();return
                elif cmd not in ('USER','NICK','PASS','PONG','MODE','WHO','LIST','TOPIC','NAMES'):self.sendraw(':huddle 421 '+(self.nick or '*')+' '+cmd+' :Unknown command')
        except Exception:
            stop.set()
if __name__=='__main__':
    db()
    if '--irc' in sys.argv:
        IRC(('',6667),IRCConn).serve_forever()
    else:
        port=8000
        if '--port' in sys.argv:
            try: port=int(sys.argv[sys.argv.index('--port')+1])
            except (ValueError,IndexError): port=8000
        start_http(port)
