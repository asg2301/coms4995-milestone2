#!/usr/bin/env python3
import sys,os,json,sqlite3,threading,time,secrets,hashlib,socket,base64,struct,re
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/data/chat.db'; NODE=int(sys.argv[1]) if len(sys.argv)>1 else 0; PORT=8000+NODE
os.makedirs('/app/data',exist_ok=True)
db=sqlite3.connect(DB,check_same_thread=False,timeout=30)
db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA synchronous=NORMAL')
db.executescript('''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE,password TEXT,display_name TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE,name TEXT,owner_id INTEGER);
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,topic TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,author_id INTEGER,body TEXT,created_at REAL,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,message_id INTEGER,kind TEXT,created_at REAL,PRIMARY KEY(channel_id,event_id));
'''); db.commit()
lock=threading.RLock(); clients=[]; clients_lock=threading.Lock(); irc_clients=[]

def now(): return time.time()
def iso(t=None): return time.strftime('%Y-%m-%dT%H:%M:%S.',time.gmtime(t or now()))+f'{int((t or now())%1*1000):03d}Z'
def q(sql,args=()):
 with lock:return db.execute(sql,args).fetchall()
def one(sql,args=()):
 x=q(sql,args); return x[0] if x else None
def userobj(uid):
 r=one('select id,username,display_name from users where id=?',(uid,))
 return {'id':r[0],'username':r[1],'display_name':r[2] or r[1],'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':''} if r else None
def chanobj(r): return {'id':r[0],'workspace_id':r[1],'name':r[2],'is_private':bool(r[3]),'is_dm':False,'topic':r[4] or '','is_archived':False}
def msgobj(r):
 u=userobj(r[2]); return {'id':r[0],'channel_id':r[1],'author_id':r[2],'author':u,'body':r[3],'parent_id':None,'reply_count':0,'created_at':iso(r[4]),'edited_at':None,'files':[],'reactions':[],'mentions':[]}
def broadcast(e):
 frame=json.dumps(e).encode()
 with clients_lock:
  for c in list(clients):
   if c['cid']==e.get('channel_id') and c['alive']:
    try: ws_send(c['sock'],frame)
    except: c['alive']=False
 for c in list(irc_clients):
  if c.get('cid')==e.get('channel_id') and e.get('type')=='message.created':
   try:
    body=e['message']['body']; c['sock'].sendall(f":{e['message']['author']['username']}!u@localhost PRIVMSG #{c['slug']}/general :{body}\r\n".encode())
   except: pass
def poller():
 last=0
 while True:
  rows=q('select rowid,channel_id,event_id,message_id,kind from events where rowid>? order by rowid',(last,))
  for rid,ch,eid,mid,kind in rows:
   row=one('select id,channel_id,author_id,body,created_at from messages where id=?',(mid,)) if mid else None
   if row: broadcast({'type':kind,'event_id':eid,'channel_id':ch,'message':msgobj(row)})
   last=rid
  time.sleep(.015)
threading.Thread(target=poller,daemon=True).start()
def ws_send(s,payload,opcode=1):
 n=len(payload); h=bytes([0x80|opcode])
 if n<126:h+=bytes([n])
 elif n<65536:h+=bytes([126])+struct.pack('!H',n)
 else:h+=bytes([127])+struct.pack('!Q',n)
 s.sendall(h+payload)
def ws_recv(f):
 h=f.read(2)
 if len(h)<2:return None
 n=h[1]&127
 if n==126:n=struct.unpack('!H',f.read(2))[0]
 elif n==127:n=struct.unpack('!Q',f.read(8))[0]
 mask=f.read(4) if h[1]&128 else b''
 p=f.read(n)
 return bytes(x^mask[i%4] for i,x in enumerate(p)) if mask else p
def ws_loop(sock,uid):
 f=sock.makefile('rb'); sub=None
 try:
  while True:
   p=ws_recv(f)
   if not p:break
   x=json.loads(p); typ=x.get('type'); cid=int(x.get('channel_id',0))
   if typ=='subscribe':
    sub=cid; head=one('select coalesce(max(event_id),0) from events where channel_id=?',(cid,))[0]
    with clients_lock: clients.append({'sock':sock,'cid':cid,'alive':True})
    ws_send(sock,json.dumps({'type':'subscribed','channel_id':cid,'head_event_id':head}).encode())
   elif typ=='resume':
    rows=q('select event_id,message_id,kind from events where channel_id=? and event_id>? order by event_id',(cid,int(x.get('since_event_id',0))))
    for eid,mid,kind in rows:
     r=one('select id,channel_id,author_id,body,created_at from messages where id=?',(mid,))
     if r: ws_send(sock,json.dumps({'type':kind,'event_id':eid,'channel_id':cid,'message':msgobj(r)}).encode())
    head=one('select coalesce(max(event_id),0) from events where channel_id=?',(cid,))[0]
    ws_send(sock,json.dumps({'type':'resumed','channel_id':cid,'head_event_id':head}).encode())
 except: pass
 finally:
  try:sock.close()
  except:pass
  with clients_lock:
   for c in clients:
    if c['sock'] is sock:c['alive']=False
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def sendj(self,obj,status=200):
  b=json.dumps(obj).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
 def auth(self):
  t=self.headers.get('Authorization','').replace('Bearer ',''); r=one('select user_id from tokens where token=?',(t,)); return r[0] if r else None
 def do_GET(self):
  if self.path=='/api/health': return self.sendj({'status':'ok','node_id':NODE})
  if self.path.startswith('/api/ws'):
   tok=re.search(r'token=([^& ]+)',self.path); uid=one('select user_id from tokens where token=?',(tok.group(1),))[0] if tok and one('select user_id from tokens where token=?',(tok.group(1),)) else None
   if not uid:return self.send_error(401)
   key=self.headers.get('Sec-WebSocket-Key'); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
   self.send_response(101); self.send_header('Upgrade','WebSocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers(); ws_loop(self.request,uid); return
  uid=self.auth()
  if self.path.startswith('/api/channels/') and self.path.endswith('/messages') and uid:
   cid=int(self.path.split('/')[3]); rows=q('select id,channel_id,author_id,body,created_at from messages where channel_id=? and deleted=0 order by id desc',(cid,)); return self.sendj({'messages':[msgobj(r) for r in rows],'next_cursor':None})
  if self.path=='/api/auth/me' and uid:return self.sendj({'user':userobj(uid)})
  if self.path=='/api/workspaces' and uid:
   rows=q('select w.id,w.slug,w.name,w.owner_id from workspaces w join members m on m.workspace_id=w.id where m.user_id=?',(uid,)); return self.sendj({'workspaces':[{'id':r[0],'slug':r[1],'name':r[2],'owner_id':r[3],'join_mode':'open'} for r in rows]})
  self.sendj({},404)
 def do_POST(self):
  path=self.path; b=self.body()
  if path=='/api/auth/register':
   if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return self.sendj({'error':'invalid'},400)
   try:
    with lock:
     c=db.execute('insert into users(username,password,display_name) values(?,?,?)',(b['username'],b['password'],b.get('display_name',b['username'])));uid=c.lastrowid
     tok=secrets.token_urlsafe(24);db.execute('insert into tokens values(?,?)',(tok,uid));db.commit()
    return self.sendj({'user':userobj(uid),'token':tok},201)
   except sqlite3.IntegrityError:return self.sendj({'error':'duplicate'},409)
  if path=='/api/auth/login':
   r=one('select id from users where username=? and password=?',(b.get('username'),b.get('password')))
   if not r:return self.sendj({'error':'unauthorized'},401)
   tok=secrets.token_urlsafe(24); q('insert into tokens values(?,?)',(tok,r[0]));db.commit();return self.sendj({'user':userobj(r[0]),'token':tok})
  uid=self.auth()
  if not uid:return self.sendj({'error':'unauthorized'},401)
  if path=='/api/workspaces':
   try:
    with lock:
     c=db.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(b['slug'],b['name'],uid));wid=c.lastrowid
     db.execute('insert into members values(?,?,?)',(wid,uid,'owner'));db.execute('insert into channels(workspace_id,name) values(?,?)',(wid,'general'));cid=db.execute('select last_insert_rowid()').fetchone()[0];db.execute('insert into cmembers values(?,?)',(cid,uid));db.commit()
    return self.sendj({'workspace':{'id':wid,'slug':b['slug'],'name':b['name'],'owner_id':uid,'join_mode':'open'},'general_channel':chanobj(one('select id,workspace_id,name,is_private,topic from channels where id=?',(cid,)))},201)
   except sqlite3.IntegrityError:return self.sendj({'error':'duplicate'},409)
  m=re.match(r'/api/channels/(\d+)/messages$',path)
  if m:
   cid=int(m.group(1));r=one('select id,workspace_id from channels where id=?',(cid,))
   if not r or not b.get('body'):return self.sendj({'error':'invalid'},400)
   if not one('select 1 from cmembers where channel_id=? and user_id=?',(cid,uid)):return self.sendj({'error':'forbidden'},403)
   with lock:
    t=now();c=db.execute('insert into messages(channel_id,author_id,body,created_at) values(?,?,?,?)',(cid,uid,b['body'],t));mid=c.lastrowid
    eid=(db.execute('select coalesce(max(event_id),0)+1 from events where channel_id=?',(cid,)).fetchone()[0]);db.execute('insert into events values(?,?,?,?,?)',(cid,eid,mid,'message.created',t));db.commit()
   return self.sendj({'message':msgobj((mid,cid,uid,b['body'],t))},201)
  m=re.match(r'/api/channels/(\d+)/join$',path)
  if m:
   cid=int(m.group(1));r=one('select workspace_id from channels where id=?',(cid,))
   if not r:return self.sendj({},404)
   with lock: db.execute('insert or ignore into cmembers values(?,?)',(cid,uid));db.execute('insert or ignore into members values(?,?,?)',(r[0],uid,'member'));db.commit()
   return self.sendj({'channel':chanobj(one('select id,workspace_id,name,is_private,topic from channels where id=?',(cid,)))})
  self.sendj({},404)
