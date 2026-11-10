import sys,os,json,sqlite3,threading,time,secrets,hashlib,socket,struct,base64,re
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/huddle.db'; NODE=int(sys.argv[1]) if len(sys.argv)>1 else 0
def db():
 c=sqlite3.connect(DB,timeout=30); c.execute('PRAGMA journal_mode=WAL'); return c
def init():
 c=db()
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT);
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner INTEGER);
 CREATE TABLE IF NOT EXISTS members(ws INTEGER,user INTEGER,role TEXT,PRIMARY KEY(ws,user));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,ws INTEGER,name TEXT,is_private INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS chanmem(ch INTEGER,user INTEGER,PRIMARY KEY(ch,user));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel INTEGER,author INTEGER,body TEXT,created REAL);
 CREATE TABLE IF NOT EXISTS events(channel INTEGER,event_id INTEGER,message INTEGER,kind TEXT,UNIQUE(channel,event_id));'''); c.commit(); c.close()
init()
clients=[]; lock=threading.RLock()
def q(sql,args=(),one=False):
 c=db(); r=c.execute(sql,args); rows=r.fetchall(); c.close(); return rows[0] if one and rows else (None if one else rows)
def user(t):
 r=q('SELECT u.id,u.username FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(t,),True); return r
def objmsg(r):
 return {'id':r[0],'channel_id':r[1],'author_id':r[2],'body':r[3],'parent_id':None,'reply_count':0,'created_at':time.strftime('%Y-%m-%dT%H:%M:%S.000Z',time.gmtime(r[4])),'edited_at':None,'files':[],'reactions':[],'mentions':[]}
def broadcast(ev):
 with lock:
  for c,ch in list(clients):
   if c.get('channel')==ev['channel_id'] and ev['event_id']>c.get('since',0):
    try: c['send'](ev)
    except: clients.remove((c,ch))
def poll():
 last={}
 while True:
  try:
   rows=q('SELECT channel,event_id,message,kind FROM events ORDER BY rowid')
   for ch,eid,mid,kind in rows:
    if eid<=last.get(ch,0): continue
    last[ch]=eid
    r=q('SELECT id,channel,author,body,created FROM messages WHERE id=?',(mid,),True)
    if r: broadcast({'type':'message.created','event_id':eid,'channel_id':ch,'message':objmsg(r)})
  except: pass
  time.sleep(.015)
threading.Thread(target=poll,daemon=True).start()
def addmsg(ch,uid,body):
 c=db()
 try:
  c.execute('BEGIN IMMEDIATE'); now=time.time()
  c.execute('INSERT INTO messages(channel,author,body,created) VALUES(?,?,?,?)',(ch,uid,body,now)); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  n=c.execute('SELECT COALESCE(MAX(event_id),0)+1 FROM events WHERE channel=?',(ch,)).fetchone()[0]
  c.execute('INSERT INTO events(channel,event_id,message,kind) VALUES(?,?,?,?)',(ch,n,mid,'message.created')); c.commit()
 finally:c.close()
 r=q('SELECT id,channel,author,body,created FROM messages WHERE id=?',(mid,),True); return mid,n,objmsg(r)
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def sendj(self,x,status=200):
  b=json.dumps(x).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
 def auth(self):
  h=self.headers.get('Authorization',''); return user(h[7:] if h.startswith('Bearer ') else '')
 def do_GET(self):
  if self.path=='/api/health': return self.sendj({'status':'ok','node_id':NODE})
  if self.path.startswith('/api/ws'): return self.ws()
  u=self.auth()
  if not u:return self.sendj({'error':'unauthorized'},401)
  p=self.path.split('?')[0].split('/')
  if p[2]=='auth' and p[3]=='me': return self.sendj({'user':{'id':u[0],'username':u[1],'display_name':u[1]}})
  if p[2]=='workspaces':
   if len(p)==3:return self.sendj({'workspaces':[{'id':x[0],'slug':x[1],'name':x[2],'owner_id':x[3],'join_mode':'open'} for x in q('SELECT id,slug,name,owner FROM workspaces WHERE owner=? OR id IN (SELECT ws FROM members WHERE user=?)',(u[0],u[0]))]})
   w=q('SELECT id,slug,name,owner FROM workspaces WHERE slug=?',(p[3],),True)
   if not w:return self.sendj({'error':'not found'},404)
   cs=q('SELECT id,ws,name,is_private FROM channels WHERE ws=?',(w[0],))
   return self.sendj({'workspace':{'id':w[0],'slug':w[1],'name':w[2],'owner_id':w[3],'join_mode':'open'},'channels':[{'id':x[0],'workspace_id':x[1],'name':x[2],'is_private':bool(x[3]),'is_dm':False,'topic':'','is_archived':False} for x in cs],'read_state':{}})
  if p[2]=='channels':
   cid=int(p[3]); ch=q('SELECT id,ws,name,is_private FROM channels WHERE id=?',(cid,),True)
   if not ch:return self.sendj({},404)
   if len(p)>4 and p[4]=='messages':
    rows=q('SELECT id,channel,author,body,created FROM messages WHERE channel=? ORDER BY id DESC',(cid,))
    return self.sendj({'messages':[objmsg(x) for x in rows],'next_cursor':None})
   if len(p)>4 and p[4]=='members':
    return self.sendj({'members':[{'id':x[0],'username':x[1],'display_name':x[1]} for x in q('SELECT u.id,u.username FROM users u JOIN chanmem m ON m.user=u.id WHERE m.ch=?',(cid,))]})
  return self.sendj({'error':'not found'},404)
 def do_POST(self):
  p=self.path.split('/'); data=self.body()
  if p[2]=='auth' and p[3] in ('register','login'):
   un=data.get('username',''); pw=data.get('password','')
   if p[3]=='register':
    if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return self.sendj({'error':'invalid'},400)
    try:
     c=db();c.execute('INSERT INTO users(username,password) VALUES(?,?)',(un,pw));uid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.commit();c.close()
    except:return self.sendj({'error':'duplicate'},409)
   else:
    r=q('SELECT id FROM users WHERE username=? AND password=?',(un,pw),True)
    if not r:return self.sendj({'error':'invalid'},401)
    uid=r[0]
   tok=secrets.token_urlsafe(24); c=db();c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid));c.commit();c.close()
   return self.sendj({'user':{'id':uid,'username':un,'display_name':un},'token':tok},201 if p[3]=='register' else 200)
  u=self.auth()
  if not u:return self.sendj({},401)
  if p[2]=='workspaces' and len(p)==3:
   d=data; c=db()
   try:
    c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO workspaces(slug,name,owner) VALUES(?,?,?)',(d['slug'],d['name'],u[0])); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u[0],'owner')); c.execute('INSERT INTO channels(ws,name) VALUES(?,?)',(wid,'general')); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO chanmem VALUES(?,?)',(cid,u[0]));c.commit()
   except: c.rollback();return self.sendj({},409)
   finally:c.close()
   return self.sendj({'workspace':{'id':wid,'slug':d['slug'],'name':d['name'],'owner_id':u[0],'join_mode':'open'},'general_channel':{'id':cid,'workspace_id':wid,'name':'general','is_private':False,'is_dm':False,'topic':'','is_archived':False}},201)
  if p[2]=='channels' and p[3].isdigit() and p[4]=='join':
   cid=int(p[3]); c=db();c.execute('INSERT OR IGNORE INTO chanmem VALUES(?,?)',(cid,u[0],));c.commit();c.close();return self.sendj({'joined':True})
  if p[2]=='channels' and p[3].isdigit() and p[4]=='messages':
   cid=int(p[3]); ch=q('SELECT id FROM channels WHERE id=?',(cid,),True)
   if not ch:return self.sendj({},404)
   if not data.get('body'):return self.sendj({},400)
   mid,eid,m=addmsg(cid,u[0],data['body']);return self.sendj({'message':m},201)
  return self.sendj({},404)
 def ws(self):
  tok=self.path.split('token=')[-1].split('&')[0]; u=user(tok)
  if not u:return self.send_response(401)
  key=self.headers.get('Sec-WebSocket-Key'); acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','WebSocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers()
  def send(o):
   b=json.dumps(o).encode(); n=len(b); h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack('!H',n); self.wfile.write(h+b);self.wfile.flush()
  def recv():
   h=self.rfile.read(2); 
   if len(h)<2:raise Exception()
   n=h[1]&127
   if n==126:n=struct.unpack('!H',self.rfile.read(2))[0]
   mask=self.rfile.read(4); b=self.rfile.read(n); return json.loads(bytes(x^mask[i%4] for i,x in enumerate(b)))
  try:
   while True:
    z=recv(); ch=int(z['channel_id']); head=q('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel=?',(ch,),True)[0]
    if z['type']=='subscribe':
     with lock: clients.append(({'channel':ch,'since':head,'send':send},self))
     send({'type':'subscribed','channel_id':ch,'head_event_id':head})
    elif z['type']=='resume':
     since=int(z.get('since_event_id',0))
     for eid,mid in q('SELECT event_id,message FROM events WHERE channel=? AND event_id>? ORDER BY event_id',(ch,since)):
      r=q('SELECT id,channel,author,body,created FROM messages WHERE id=?',(mid,),True);send({'type':'message.created','event_id':eid,'channel_id':ch,'message':objmsg(r)})
     send({'type':'resumed','channel_id':ch,'head_event_id':head})
    with lock:
     if z['type']!='subscribe': clients.append(({'channel':ch,'since':head,'send':send},self))
  except: pass
class IRC:
 def run(self):
  s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen()
  while True: threading.Thread(target=self.one,args=(s.accept()[0],),daemon=True).start()
 def one(self,s):
  f=s.makefile('r'); tok=None; nick=None; joined=None
  def out(x):s.sendall((x+'\r\n').encode())
  try:
   for line in f:
    a=line.rstrip('\r\n').split(' ',2); cmd=a[0].upper()
    if cmd=='PASS':tok=a[1]
    elif cmd=='NICK':nick=a[1]
    elif cmd=='USER':
     u=user(tok)
     if not u:out(':srv 464 * :Password incorrect');continue
     out(f':srv 001 {nick} :Welcome');out(f':srv 002 {nick} :huddle');out(f':srv 003 {nick} :local');out(f':srv 004 {nick} :huddle');out(f':srv 005 {nick} :CHANTYPES=#');out(f':srv 422 {nick} :No MOTD')
    elif cmd=='JOIN':
     name=a[1]; m=re.match(r'#([^/]+)/(.+)',name); ch=q('SELECT c.id FROM channels c JOIN workspaces w ON w.id=c.ws WHERE w.slug=? AND c.name=?',(m.group(1),m.group(2)),True) if m else None
     if not ch:out(f':srv 403 {nick} {name} :No such channel');continue
     joined=ch[0];out(f':{nick}!u@localhost JOIN {name}');out(f':srv 366 {nick} {name} :End of NAMES')
     with lock: clients.append(({'channel':joined,'since':q('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel=?',(joined,),True)[0],'send':lambda e,n=name:out(f':web!u@localhost PRIVMSG {n} :{e["message"]["body"]}')},self))
    elif cmd=='PRIVMSG' and joined:addmsg(joined,user(tok)[0],a[2].lstrip(':'))
    elif cmd=='PING':out('PONG '+(a[1] if len(a)>1 else ''))
    elif cmd=='QUIT':break
    else: out(f':srv 421 {nick or "*"} {cmd} :Unknown command')
  except:pass
  s.close()
if NODE==0:threading.Thread(target=IRC().run,daemon=True).start()
ThreadingHTTPServer(('127.0.0.1',8000+NODE),H).serve_forever()
