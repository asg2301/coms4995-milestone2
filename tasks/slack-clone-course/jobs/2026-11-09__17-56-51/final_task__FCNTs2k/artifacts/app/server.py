#!/usr/bin/env python3
import os,sys,json,sqlite3,secrets,hashlib,re,base64,struct,socket,threading,time,subprocess
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
DB='/app/data/cluster.db'; os.makedirs('/app/data',exist_ok=True)
lock=threading.RLock(); clients=[]; irc_clients=[]; stop=threading.Event()
def db():
 c=sqlite3.connect(DB,timeout=30,check_same_thread=False); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=30000'); return c
def init():
 with db() as c:
  c.executescript('''PRAGMA journal_mode=WAL;
 CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE,password TEXT,display_name TEXT);
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE,name TEXT,owner_id INTEGER);
 CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,topic TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,author_id INTEGER,body TEXT,created_at REAL,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,event_id INTEGER,message_id INTEGER,kind TEXT,UNIQUE(channel_id,event_id));
 CREATE TABLE IF NOT EXISTS seq(channel_id INTEGER PRIMARY KEY,n INTEGER DEFAULT 0)''')
def now(): return time.time()
def iso(t=None): return time.strftime('%Y-%m-%dT%H:%M:%S',time.gmtime(t or now()))+'.000Z'
def user(r): return {'id':r['id'],'username':r['username'],'display_name':r['display_name'] or r['username'],'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':''}
def auth(h):
 t=h.get('Authorization','')
 if not t.startswith('Bearer '): return None
 with db() as c:
  r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(t[7:],)).fetchone()
 return r
def channel(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':False,'topic':r['topic'] or '','is_archived':False}
def msg(r):
 with db() as c: u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 return {'id':(r['message_id'] if 'message_id' in r.keys() else r['id']),'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(u),'body':r['body'],'parent_id':None,'reply_count':0,'created_at':iso(r['created_at']),'edited_at':None,'files':[],'reactions':[],'mentions':[]}
def emit(c, cid, mid, kind='message.created'):
 with lock,db() as x:
  x.execute('INSERT INTO seq(channel_id,n) VALUES(?,1) ON CONFLICT(channel_id) DO UPDATE SET n=n+1',(cid,))
  eid=x.execute('SELECT n FROM seq WHERE channel_id=?',(cid,)).fetchone()['n']
  x.execute('INSERT INTO events(channel_id,event_id,message_id,kind) VALUES(?,?,?,?)',(cid,eid,mid,kind)); x.commit()
 return eid
def broadcast(ev):
 frame=json.dumps(ev).encode()
 for w in list(clients):
  try:
   if ev.get('channel_id') in w['subs']: ws_send(w['sock'],frame)
  except: 
   try: clients.remove(w)
   except: pass
 for q in list(irc_clients):
  try:
   if ev.get('channel_id')==q.cid: q.sock.sendall((f":{q.nick}!u@localhost PRIVMSG {q.name} :{ev['message']['body']}\r\n").encode())
  except: pass
def watcher():
 with db() as c:
  row=c.execute('SELECT COALESCE(MAX(id),0) n FROM events').fetchone()
 seen=row['n']
 while not stop.is_set():
  try:
   with db() as c: rows=c.execute('SELECT e.*,m.* FROM events e JOIN messages m ON m.id=e.message_id WHERE e.id>? ORDER BY e.id',(seen,)).fetchall()
   for r in rows:
    seen=max(seen,r['id']); broadcast({'type':r['kind'],'event_id':r['event_id'],'channel_id':r['channel_id'],'message':msg(r)})
  except: pass
  time.sleep(.005)
def ws_send(s,b):
 n=len(b); h=bytes([129])+((bytes([n]) if n<126 else bytes([126])+struct.pack('!H',n)))
 s.sendall(h+b)
def ws_read(s):
 h=s.recv(2)
 if not h:return None
 n=h[1]&127
 if n==126:n=struct.unpack('!H',s.recv(2))[0]
 mask=s.recv(4); b=s.recv(n); return bytes(v^mask[i%4] for i,v in enumerate(b))
def ws_loop(s,u):
 while True:
  b=ws_read(s)
  if not b: break
  try:
   o=json.loads(b); typ=o.get('type'); cid=o.get('channel_id')
   if typ=='subscribe':
    with db() as c: row=c.execute('SELECT n FROM seq WHERE channel_id=?',(cid,)).fetchone(); head=row['n'] if row else 0
    clients.append({'sock':s,'subs':{cid}}); ws_send(s,json.dumps({'type':'subscribed','channel_id':cid,'head_event_id':head}).encode())
   elif typ=='resume':
    since=o.get('since_event_id',0)
    with db() as c:
     rows=c.execute('SELECT e.*,m.* FROM events e JOIN messages m ON m.id=e.message_id WHERE e.channel_id=? AND e.event_id>? ORDER BY e.event_id',(cid,since)).fetchall()
     row=c.execute('SELECT n FROM seq WHERE channel_id=?',(cid,)).fetchone(); head=row['n'] if row else 0
    for r in rows: ws_send(s,json.dumps({'type':r['kind'],'event_id':r['event_id'],'channel_id':cid,'message':msg(r)}).encode())
    ws_send(s,json.dumps({'type':'resumed','channel_id':cid,'head_event_id':head}).encode())
  except: break
 try:s.close()
 except:pass
class H(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def sendj(self,o,status=200):
  b=json.dumps(o).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def do_GET(self):
  p=urlparse(self.path); path=p.path
  if path=='/api/health': return self.sendj({'status':'ok','node_id':int(os.environ.get('NODE','0'))})
  if path=='/api/ws':
   u=auth({'Authorization':'Bearer '+parse_qs(p.query).get('token',[''])[0]})
   if not u:return self.send_error(401)
   key=self.headers.get('Sec-WebSocket-Key'); self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); import hashlib
   self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()); self.end_headers(); ws_loop(self.connection,u); return
  u=auth(self.headers)
  if path.startswith('/api/') and not path=='/api/health' and not u:return self.sendj({'error':'unauthorized'},401)
  if path=='/api/auth/me': return self.sendj({'user':user(u)})
  m=re.match(r'/api/channels/(\d+)/messages',path)
  if m:
   cid=int(m.group(1))
   with db() as c: rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC',(cid,)).fetchall()
   return self.sendj({'messages':[msg(r) for r in rows],'next_cursor':None})
  if path=='/api/workspaces':
   with db() as c: rows=c.execute('SELECT * FROM workspaces WHERE owner_id=?',(u['id'],)).fetchall()
   return self.sendj({'workspaces':[{'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':'open'} for r in rows]})
  m=re.match(r'/api/workspaces/([^/]+)$',path)
  if m:
   with db() as c:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); cs=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchall()
   return self.sendj({'workspace':{'id':w['id'],'slug':w['slug'],'name':w['name'],'owner_id':w['owner_id'],'join_mode':'open'},'channels':[channel(x) for x in cs],'read_state':{}})
  self.sendj({'error':'not found'},404)
 def do_POST(self):
  p=urlparse(self.path); path=p.path; u=auth(self.headers)
  if path=='/api/auth/register':
   o=self.body()
   if not re.fullmatch(r'[A-Za-z0-9_]+',o.get('username','')) or len(o.get('password',''))<8:return self.sendj({'error':'invalid'},400)
   try:
    with db() as c:
     cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(o['username'],o['password'],o.get('display_name',o['username']))); uid=cur.lastrowid; t=secrets.token_hex(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); c.commit(); r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
    return self.sendj({'user':user(r),'token':t},201)
   except:return self.sendj({'error':'duplicate'},409)
  if path=='/api/auth/login':
   o=self.body()
   with db() as c:r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(o.get('username'),o.get('password'))).fetchone()
   if not r:return self.sendj({'error':'invalid'},401)
   t=secrets.token_hex(24)
   with db() as c:c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']))
   return self.sendj({'user':user(r),'token':t})
  if not u:return self.sendj({'error':'unauthorized'},401)
  if path=='/api/workspaces':
   o=self.body()
   try:
    with db() as c:
     cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(o['slug'],o['name'],u['id'])); wid=cur.lastrowid;c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));cur=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=cur.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    return self.sendj({'workspace':{'id':w['id'],'slug':w['slug'],'name':w['name'],'owner_id':u['id'],'join_mode':'open'},'general_channel':channel(ch)},201)
   except:return self.sendj({'error':'duplicate'},409)
  m=re.match(r'/api/channels/(\d+)/join',path)
  if m:
   with db() as c:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(int(m.group(1)),u['id']))
   return self.sendj({'joined':True})
  m=re.match(r'/api/channels/(\d+)/messages',path)
  if m:
   o=self.body(); body=o.get('body','')
   if not body:return self.sendj({'error':'empty'},400)
   with db() as c:cur=c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(int(m.group(1)),u['id'],body,now())); mid=cur.lastrowid; r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
   eid=emit(c,int(m.group(1)),mid)
   return self.sendj({'message':msg(r)},201)
  self.sendj({'error':'not found'},404)
 def body(self):
  n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or '{}')
def irc():
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen()
 while True:
  c,_=s.accept(); threading.Thread(target=irc_client,args=(c,),daemon=True).start()
def irc_client(s):
 f=s.makefile('rb'); token=None; nick=''; user=None; cid=None; name=''
 try:
  while True:
   line=f.readline().decode(errors='ignore').strip()
   if not line:break
   a=line.split(' ',2); cmd=a[0].upper()
   if cmd=='PASS':token=a[1]
   elif cmd=='NICK':nick=a[1]
   elif cmd=='USER':
    user=auth({'Authorization':'Bearer '+token})
    if user:s.sendall(f':local 001 {nick} :Welcome\r\n:local 002 {nick} :Your host\r\n:local 003 {nick} :Created\r\n:local 004 {nick} :local\r\n:local 005 {nick} CHANTYPES=# :are supported\r\n:local 422 {nick} :No MOTD\r\n'.encode())
   elif cmd=='JOIN':
    name=a[1]; slug,ch=name[1:].split('/',1)
    with db() as c:r=c.execute('SELECT * FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(slug,ch)).fetchone()
    if not r:s.sendall(f':local 403 {nick} {name} :No such channel\r\n'.encode());continue
    cid=r['id']; irc_clients.append(type('Q',(),{'sock':s,'cid':cid,'nick':nick,'name':name})()); s.sendall(f':{nick}!u@localhost JOIN {name}\r\n:local 353 {nick} = {name} :{nick}\r\n:local 366 {nick} {name} :End\r\n'.encode())
   elif cmd=='PRIVMSG' and cid:
    body=line.split(' :',1)[1]
    with db() as c:cur=c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(cid,user['id'],body,now()));mid=cur.lastrowid;r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    eid=emit(c,cid,mid)
   elif cmd=='PING':s.sendall(b':local PONG local\r\n')
 except:pass
try:s.close()
except:pass
if __name__=='__main__':
 init(); threading.Thread(target=watcher,daemon=True).start()
 if os.environ.get('NODE','0') == '0': threading.Thread(target=irc,daemon=True).start()
 port=int(sys.argv[1]); ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
