import sys,os,sqlite3,json,time,uuid,hashlib,secrets,re,base64,struct,socket,threading
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
DB='/app/data/huddle.db'; os.makedirs('/app/data',exist_ok=True)
def now(): return time.strftime('%Y-%m-%dT%H:%M:%S.',time.gmtime())+f'{int(time.time()*1000)%1000:03d}Z'
def db():
 c=sqlite3.connect(DB,timeout=20,check_same_thread=False); c.row_factory=sqlite3.Row; return c
def init():
 c=db(); c.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER);
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,topic TEXT DEFAULT '',seq INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,deleted INTEGER DEFAULT 0,edited_at TEXT);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,message_id INTEGER,created_at TEXT,PRIMARY KEY(channel_id,event_id));
'''); c.commit(); c.close()
init()
def q(sql,args=(),one=False):
 c=db(); r=c.execute(sql,args); x=r.fetchone() if one else r.fetchall(); c.close(); return x
def user(t):
 r=q('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(t,),True); return r
def msgobj(r):
 u=q('SELECT * FROM users WHERE id=?',(r['author_id'],),True)
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':dict(u) if u else None,'body':r['body'],'parent_id':r['parent_id'],'reply_count':q('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],),True)['n'],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[],'mentions':[]}
IRC_CLIENTS=[]; IRC_LOCK=threading.Lock()
def emit(cid,typ,mid=None):
 c=db(); c.execute('BEGIN IMMEDIATE'); c.execute('UPDATE channels SET seq=seq+1 WHERE id=?',(cid,)); eid=c.execute('SELECT seq FROM channels WHERE id=?',(cid,)).fetchone()[0]; c.execute('INSERT INTO events VALUES(?,?,?,?,?)',(cid,eid,typ,mid,now())); c.commit(); c.close()
 if mid:
  r=q('SELECT m.*,w.slug, ch.name FROM messages m JOIN channels ch ON ch.id=m.channel_id JOIN workspaces w ON w.id=ch.workspace_id WHERE m.id=?',(mid,),True)
  if r:
   name='#'+r['slug']+'/'+r['name']; u=q('SELECT username FROM users WHERE id=?',(r['author_id'],),True)
   line=':'+u['username']+'!user@host PRIVMSG '+name+' :'+r['body']+'\r\n'
   with IRC_LOCK:
    for sock,cid0 in list(IRC_CLIENTS):
     if cid0==cid:
      try: sock.sendall(line.encode())
      except Exception:
       try: IRC_CLIENTS.remove((sock,cid0))
       except ValueError: pass
 return eid
def sendframe(s,o):
 b=json.dumps(o,separators=(',',':')).encode(); n=len(b)
 h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack('!H',n)
 s.sendall(h+b)
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def json(self,code,o):
  b=json.dumps(o).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
 def auth(self):
  h=self.headers.get('Authorization',''); return user(h[7:] if h.startswith('Bearer ') else '')
 def do_GET(self):
  p=urlparse(self.path); path=p.path; a=self.auth()
  if path=='/api/health': return self.json(200,{'status':'ok','node_id':int(self.server.node)})
  if path=='/api/ws': return self.websocket(parse_qs(p.query).get('token',[''])[0])
  if path=='/api/auth/me':
   return self.json(200,{'user':dict(a)}) if a else self.json(401,{'error':'unauthorized'})
  if path=='/api/workspaces':
   if not a:return self.json(401,{})
   rows=q('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(a['id'],)); return self.json(200,{'workspaces':[dict(x) for x in rows]})
  if path.startswith('/api/workspaces/'):
   if not a:return self.json(401,{})
   slug=path.split('/')[3]; w=q('SELECT * FROM workspaces WHERE slug=?',(slug,),True)
   if not w:return self.json(404,{})
   cs=q('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)); return self.json(200,{'workspace':dict(w),'channels':[dict(x) for x in cs],'read_state':{}})
  if path.startswith('/api/channels/') and path.endswith('/messages'):
   if not a:return self.json(401,{})
   cid=int(path.split('/')[3]); rows=q('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(cid,)); return self.json(200,{'messages':[msgobj(x) for x in rows],'next_cursor':None})
  if path.startswith('/api/channels/') and path.endswith('/members'):
   cid=int(path.split('/')[3]); rows=q('SELECT u.* FROM cmembers m JOIN users u ON u.id=m.user_id WHERE m.channel_id=?',(cid,)); return self.json(200,{'members':[dict(x) for x in rows]})
  self.json(404,{})
 def do_POST(self):
  p=urlparse(self.path); path=p.path; a=self.auth()
  if path=='/api/auth/register':
   try:
    b=self.body(); un=b['username']; pw=b['password']
    if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8: return self.json(400,{'error':'invalid'})
    c=db(); c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,pw,b.get('display_name',un))); uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid)); c.commit(); u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close(); return self.json(201,{'user':dict(u),'token':tok})
   except sqlite3.IntegrityError:return self.json(409,{'error':'duplicate'})
  if path=='/api/auth/login':
   b=self.body(); u=q('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),b.get('password')),True)
   if not u:return self.json(401,{'error':'invalid'})
   tok=secrets.token_urlsafe(24); c=db(); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,u['id'])); c.commit(); c.close(); return self.json(200,{'user':dict(u),'token':tok})
  if not a:return self.json(401,{})
  if path=='/api/workspaces':
   b=self.body()
   try:
    c=db(); c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],a['id'])); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO members VALUES(?,?,?)',(wid,a['id'],'owner')); c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,a['id'])); c.commit(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return self.json(201,{'workspace':dict(w),'general_channel':dict(ch)})
   except sqlite3.IntegrityError:return self.json(409,{})
  if path.startswith('/api/channels/') and path.endswith('/join'):
   cid=int(path.split('/')[3]); ch=q('SELECT * FROM channels WHERE id=?',(cid,),True)
   if not ch:return self.json(404,{})
   c=db(); c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],a['id'],'member')); c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,a['id'])); c.commit(); c.close(); return self.json(200,{'channel':dict(ch)})
  if path.startswith('/api/channels/') and path.endswith('/messages'):
   cid=int(path.split('/')[3]); b=self.body(); body=b.get('body','')
   if not body:return self.json(400,{'error':'empty'})
   ch=q('SELECT * FROM channels WHERE id=?',(cid,),True)
   if not ch:return self.json(404,{})
   c=db(); c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,a['id'],body,b.get('parent_id'),now())); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.commit(); c.close(); emit(cid,'message.reply' if b.get('parent_id') else 'message.created',mid); return self.json(201,{'message':msgobj(q('SELECT * FROM messages WHERE id=?',(mid,),True))})
  self.json(404,{})
 def websocket(self,token):
  a=user(token)
  if not a:return self.json(401,{})
  key=self.headers.get('Sec-WebSocket-Key'); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
  s=self.connection; s.settimeout(1); subs={}; alive=True
  def poll():
   while alive:
    for cid,last in list(subs.items()):
     es=q('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last))
     for e in es:
      m=q('SELECT * FROM messages WHERE id=?',(e['message_id'],),True) if e['message_id'] else None
      sendframe(s,{'type':e['type'],'event_id':e['event_id'],'channel_id':cid,'message':msgobj(m) if m else None}); subs[cid]=e['event_id']
    time.sleep(.03)
  th=threading.Thread(target=poll,daemon=True); th.start()
  try:
   while True:
    h=s.recv(2)
    if not h:break
    n=h[1]&127
    if n==126:n=struct.unpack('!H',s.recv(2))[0]
    mask=s.recv(4); data=s.recv(n); data=bytes(v^mask[i%4] for i,v in enumerate(data)); o=json.loads(data)
    cid=int(o['channel_id'])
    if o['type']=='subscribe':
     head=q('SELECT seq FROM channels WHERE id=?',(cid,),True)['seq']; subs[cid]=head; sendframe(s,{'type':'subscribed','channel_id':cid,'head_event_id':head})
    elif o['type']=='resume':
     since=int(o.get('since_event_id',0))
     subs[cid]=q('SELECT seq FROM channels WHERE id=?',(cid,),True)['seq']
     for e in q('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)):
      m=q('SELECT * FROM messages WHERE id=?',(e['message_id'],),True) if e['message_id'] else None
      sendframe(s,{'type':e['type'],'event_id':e['event_id'],'channel_id':cid,'message':msgobj(m) if m else None})
     head=q('SELECT seq FROM channels WHERE id=?',(cid,),True)['seq']; subs[cid]=head
     sendframe(s,{'type':'resumed','channel_id':cid,'head_event_id':head})
  except Exception: pass
  alive=False
class IRC:
 def run(self):
  s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('0.0.0.0',6667)); s.listen()
  while True: threading.Thread(target=self.client,args=(s.accept()[0],),daemon=True).start()
 def client(self,s):
  f=s.makefile('rwb'); token=''; nick=''; joined=[]; last={}; stop=threading.Event(); poll_started=False
  def bridge():
   while not stop.is_set():
    try:
     for name,cid in list(joined):
      for e in q('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last.get(cid,0))):
       last[cid]=e['event_id']
       if e['message_id']:
        r=q('SELECT m.*,w.slug,ch.name,u.username FROM messages m JOIN channels ch ON ch.id=m.channel_id JOIN workspaces w ON w.id=ch.workspace_id JOIN users u ON u.id=m.author_id WHERE m.id=?',(e['message_id'],),True)
        if r:
         f.write((':'+r['username']+'!user@host PRIVMSG #'+r['slug']+'/'+r['name']+' :'+r['body']+'\r\n').encode()); f.flush()
    except Exception: return
    time.sleep(.03)
  def out(x): f.write((x+'\r\n').encode()); f.flush()
  try:
   while True:
    line=f.readline().decode(errors='ignore').strip()
    if not line:break
    z=line.split(' ',2); cmd=z[0].upper()
    if cmd=='PASS': token=z[1]
    elif cmd=='NICK': nick=z[1]
    elif cmd=='USER':
     if not user(token):out(':server 464 '+nick+' :Password incorrect');continue
     for n in range(1,6):out(f':server 00{n} {nick} :huddle')
     out(':server 422 '+nick+' :MOTD File is missing')
    elif cmd=='JOIN':
     name=z[1]; m=re.match(r'^#([^/]+)/(.+)$',name); w=q('SELECT * FROM workspaces WHERE slug=?',(m.group(1),),True) if m else None; ch=q('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],m.group(2)),True) if w else None
     if not ch:out(':server 403 '+nick+' '+name+' :No such channel');continue
     joined.append((name,ch['id']))
     last[ch['id']]=q('SELECT seq FROM channels WHERE id=?',(ch['id'],),True)['seq']
     if not poll_started:
      poll_started=True; threading.Thread(target=bridge,daemon=True).start()
     with IRC_LOCK:
      IRC_CLIENTS.append((s,ch['id']))
     out(':'+nick+'!user@host JOIN '+name); out(':server 353 '+nick+' = '+name+' :'+nick); out(':server 366 '+nick+' '+name+' :End of NAMES')
    elif cmd=='PING':out(':server PONG :'+(z[1] if len(z)>1 else 'server'))
    elif cmd=='PRIVMSG':
     name=z[1]; body=line.split(' :',1)[1] if ' :' in line else ''; hit=next((x for x in joined if x[0]==name),None)
     if hit:
      cid=hit[1]; uid=user(token)['id']
      c=db(); c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(cid,uid,body,now())); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.commit(); c.close(); emit(cid,'message.created',mid)
    elif cmd=='QUIT':break
  except Exception: pass
  stop.set()
  try:s.close()
  except:pass
if __name__=='__main__':
 mode=sys.argv[1]
 if mode=='irc': IRC().run()
 else:
  node=int(mode); ThreadingHTTPServer.allow_reuse_address=True
  h=ThreadingHTTPServer(('127.0.0.1',8000+node),Handler); h.node=node; h.serve_forever()
