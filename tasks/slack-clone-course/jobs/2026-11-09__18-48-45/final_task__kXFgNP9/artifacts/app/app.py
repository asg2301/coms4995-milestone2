import os, re, json, sqlite3, uuid, hashlib, secrets, threading, time, socket, base64, struct
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
DB='/app/huddle.db'; lock=threading.RLock(); subs=[]; ircs=[]; nicknames=set()
def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,check_same_thread=False); c.row_factory=sqlite3.Row
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT);
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,uid INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner INTEGER);
 CREATE TABLE IF NOT EXISTS members(wid INTEGER,uid INTEGER,role TEXT,PRIMARY KEY(wid,uid));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,wid INTEGER,name TEXT,topic TEXT DEFAULT '',private INTEGER DEFAULT 0,UNIQUE(wid,name));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel INTEGER,author INTEGER,body TEXT,parent INTEGER,created TEXT,edited TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS reactions(mid INTEGER,uid INTEGER,emoji TEXT,PRIMARY KEY(mid,uid,emoji));
 CREATE TABLE IF NOT EXISTS seq(channel INTEGER PRIMARY KEY,n INTEGER DEFAULT 0);''')
 return c
db().close()
def userobj(r):
 return {'id':r['id'],'username':r['username'],'display_name':r['display_name'],'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':''}
def emit(ch,kind,msg=None):
 with lock:
  c=db(); c.execute('INSERT OR IGNORE INTO seq(channel,n) VALUES(?,0)',(ch,)); c.execute('UPDATE seq SET n=n+1 WHERE channel=?',(ch,)); eid=c.execute('SELECT n FROM seq WHERE channel=?',(ch,)).fetchone()[0]; c.commit(); c.close()
 frame={'type':kind,'event_id':eid,'channel_id':ch}
 if msg: frame['message']=msg
 dead=[]
 for q,channel in list(subs):
  if channel==ch:
   try:q.put(frame)
   except:dead.append((q,channel))
 for x in dead:
  if x in subs:subs.remove(x)
 for s,cc,nick,uid in list(ircs):
  if cc==ch and msg:
   try:s.sendall((f':{nick}!{uid}@localhost PRIVMSG {ircname(ch)} :{msg["body"]}\r\n').encode())
   except: pass
 return eid
def ircname(ch):
 c=db(); r=c.execute('SELECT w.slug,c.name FROM channels c JOIN workspaces w ON w.id=c.wid WHERE c.id=?',(ch,)).fetchone(); c.close()
 return '#'+r['slug']+'/'+r['name'] if r else '#unknown'
def msgobj(r):
 c=db(); u=c.execute('SELECT * FROM users WHERE id=?',(r['author'],)).fetchone()
 rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(uid) ids FROM reactions WHERE mid=? GROUP BY emoji',(r['id'],)).fetchall(); c.close()
 return {'id':r['id'],'channel_id':r['channel'],'author_id':r['author'],'author':userobj(u),'body':r['body'],'parent_id':r['parent'],'reply_count':0,'created_at':r['created'],'edited_at':r['edited'],'files':[],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs],'mentions':[]}
def auth(h):
 m=re.search(r'Bearer\s+(.+)',h or '')
 if not m:return None
 c=db(); r=c.execute('SELECT uid FROM tokens WHERE token=?',(m.group(1),)).fetchone(); c.close(); return r['uid'] if r else None
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a):pass
 def send(self,code,obj,headers=None):
  b=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b)))
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers(); self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length',0)); return json.loads(self.rfile.read(n) or b'{}')
 def do_GET(self):
  p=urlparse(self.path); path=p.path; q=parse_qs(p.query)
  if path=='/':
   self.send_response(200); b=HTML.encode(); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
  if path=='/api/health': self.send(200,{'status':'ok','node_id':int(self.server.server_port)-8000});return
  if path=='/api/ws': return self.websocket(q.get('token',[''])[0])
  uid=auth(self.headers.get('Authorization'))
  if path=='/api/auth/me':
   if not uid:return self.send(401,{})
   c=db();r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();c.close();return self.send(200,{'user':userobj(r)})
  if not uid:return self.send(401,{})
  m=re.match(r'/api/channels/(\d+)/messages$',path)
  if m:
   c=db(); rows=c.execute('SELECT * FROM messages WHERE channel=? AND deleted=0 AND parent IS NULL ORDER BY id DESC',(m.group(1),)).fetchall();c.close();return self.send(200,{'messages':[msgobj(x) for x in rows],'next_cursor':None})
  m=re.match(r'/api/messages/(\d+)/replies$',path)
  if m:
   c=db();rows=c.execute('SELECT * FROM messages WHERE parent=? AND deleted=0 ORDER BY id',(m.group(1),)).fetchall();c.close();return self.send(200,{'replies':[msgobj(x) for x in rows],'next_cursor':None})
  if path=='/api/workspaces':
   c=db();rows=c.execute('SELECT * FROM workspaces WHERE owner=? OR id IN (SELECT wid FROM members WHERE uid=?)',(uid,uid)).fetchall();c.close();return self.send(200,{'workspaces':[{'id':x['id'],'slug':x['slug'],'name':x['name'],'owner_id':x['owner'],'join_mode':'open'} for x in rows]})
  m=re.match(r'/api/workspaces/([^/]+)$',path)
  if m:
   c=db();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); cs=c.execute('SELECT * FROM channels WHERE wid=?',(w['id'],)).fetchall() if w else [];c.close()
   if not w:return self.send(404,{})
   return self.send(200,{'workspace':{'id':w['id'],'slug':w['slug'],'name':w['name'],'owner_id':w['owner'],'join_mode':'open'},'channels':[chobj(x) for x in cs],'read_state':{}})
  m=re.match(r'/api/channels/(\d+)/members$',path)
  if m:
   c=db();rows=c.execute('SELECT u.* FROM users u JOIN members m ON u.id=m.uid JOIN channels ch ON ch.wid=m.wid WHERE ch.id=?',(m.group(1),)).fetchall();c.close();return self.send(200,{'members':[userobj(x) for x in rows]})
  self.send(404,{})
 def do_POST(self):
  p=urlparse(self.path); path=p.path; b=self.body()
  if path=='/api/auth/register':
   if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return self.send(400,{})
   c=db()
   try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],hashlib.sha256(b['password'].encode()).hexdigest(),b.get('display_name') or b['username']));uid=c.execute('SELECT last_insert_rowid()').fetchone()[0];t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));c.commit();r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();return self.send(201,{'user':userobj(r),'token':t})
   except sqlite3.IntegrityError:return self.send(409,{})
   finally:c.close()
  if path=='/api/auth/login':
   c=db();r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),hashlib.sha256(b.get('password','').encode()).hexdigest())).fetchone()
   if not r:c.close();return self.send(401,{})
   t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();c.close();return self.send(200,{'user':userobj(r),'token':t})
  uid=auth(self.headers.get('Authorization'))
  if not uid:return self.send(401,{})
  if path=='/api/workspaces':
   c=db()
   try:c.execute('INSERT INTO workspaces(slug,name,owner) VALUES(?,?,?)',(b['slug'],b['name'],uid));wid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO members VALUES(?,?,?)',(wid,uid,'owner'));c.execute('INSERT INTO channels(wid,name) VALUES(?,?)',(wid,'general'));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.commit();return self.send(201,{'workspace':{'id':wid,'slug':b['slug'],'name':b['name'],'owner_id':uid,'join_mode':'open'},'general_channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   except sqlite3.IntegrityError:return self.send(409,{})
   finally:c.close()
  m=re.match(r'/api/channels/(\d+)/join$',path)
  if m:
   c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone();c.execute('INSERT OR IGNORE INTO members VALUES((SELECT wid FROM channels WHERE id=?),?,?)',(m.group(1),uid,'member'));c.commit();c.close();return self.send(200,{'channel':chobj(ch)})
  m=re.match(r'/api/workspaces/([^/]+)/channels$',path)
  if m:
   c=db();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
   try:c.execute('INSERT INTO channels(wid,name,topic,private) VALUES(?,?,?,?)',(w['id'],b['name'],b.get('topic',''),int(b.get('is_private',False))));c.commit();r=c.execute('SELECT * FROM channels WHERE id=?',(c.execute('SELECT last_insert_rowid()').fetchone()[0],)).fetchone();return self.send(201,{'channel':chobj(r)})
   except sqlite3.IntegrityError:return self.send(409,{})
   finally:c.close()
  m=re.match(r'/api/channels/(\d+)/messages$',path)
  if m:
   if not b.get('body'):return self.send(400,{})
   c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone();c.execute('INSERT INTO messages(channel,author,body,parent,created) VALUES(?,?,?,?,?)',(ch['id'],uid,b['body'],b.get('parent_id'),now()));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0];r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.commit();c.close();o=msgobj(r);emit(ch['id'],'message.reply' if b.get('parent_id') else 'message.created',o);return self.send(201,{'message':o})
  m=re.match(r'/api/messages/(\d+)/reactions$',path)
  if m:
   c=db();c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(m.group(1),uid,b.get('emoji','👍')));c.commit();rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(uid) ids FROM reactions WHERE mid=? GROUP BY emoji',(m.group(1),)).fetchall();c.close();return self.send(200,{'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs]})
  self.send(404,{})
 def do_PATCH(self):
  uid=auth(self.headers.get('Authorization')); 
  if not uid:return self.send(401,{})
  m=re.match(r'/api/messages/(\d+)$',self.path)
  if m:
   b=self.body();c=db();r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
   if not r:return self.send(404,{})
   if r['deleted']:return self.send(409,{})
   if r['author']!=uid:return self.send(403,{})
   c.execute('UPDATE messages SET body=?,edited=? WHERE id=?',(b.get('body',''),now(),r['id']));c.commit();x=c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone();c.close();o=msgobj(x);emit(r['channel'],'message.updated',o);return self.send(200,{'message':o})
  self.send(404,{})
 def do_DELETE(self):
  uid=auth(self.headers.get('Authorization'));m=re.match(r'/api/messages/(\d+)$',self.path)
  if not uid:return self.send(401,{})
  if m:
   c=db();r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
   if not r:return self.send(200,{'deleted':True})
   w=c.execute('SELECT owner FROM workspaces w JOIN channels ch ON w.id=ch.wid WHERE ch.id=?',(r['channel'],)).fetchone()
   if r['author']!=uid and w['owner']!=uid:return self.send(403,{})
   c.execute('UPDATE messages SET deleted=1,body="" WHERE id=?',(r['id'],));c.commit();c.close();emit(r['channel'],'message.deleted',{'id':r['id']});return self.send(200,{'deleted':True})
  self.send(404,{})
 def websocket(self,token):
  c=db(); r=c.execute('SELECT uid FROM tokens WHERE token=?',(token,)).fetchone(); c.close()
  if not r:return self.send(401,{})
  key=self.headers.get('Sec-WebSocket-Key')
  accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
  import queue
  q=queue.Queue(); channel=None; subs.append((q,None)); sock=self.connection; sock.settimeout(.05)
  def exact(n):
   data=b''
   while len(data)<n:
    part=sock.recv(n-len(data))
    if not part:return None
    data+=part
   return data
  def readframe():
   h=exact(2)
   if not h:return None
   n=h[1]&127; masked=bool(h[1]&128)
   if n==126:n=struct.unpack('!H',exact(2))[0]
   elif n==127:n=struct.unpack('!Q',exact(8))[0]
   mask=exact(4) if masked else b''
   data=exact(n)
   if data is None:return None
   if masked:data=bytes(data[i]^mask[i%4] for i in range(n))
   return data
  try:
   while True:
    try:
     raw=readframe()
     if raw is None:break
     if raw:
      x=json.loads(raw.decode('utf-8'))
      if x.get('type') in ('subscribe','resume'):
       channel=int(x['channel_id'])
       if (q,None) in subs:subs.remove((q,None))
       subs.append((q,channel))
       c=db(); z=c.execute('SELECT COALESCE(n,0) FROM seq WHERE channel=?',(channel,)).fetchone(); c.close()
       sendws(sock,{'type':'subscribed' if x['type']=='subscribe' else 'resumed','channel_id':channel,'head_event_id':z[0] if z else 0})
    except socket.timeout:
     pass
    except (ConnectionError,OSError):
     break
    try:sendws(sock,q.get_nowait())
    except queue.Empty:pass
  finally:
   if (q,channel) in subs:subs.remove((q,channel))
def sendws(s,o):
 b=json.dumps(o).encode(); n=len(b); h=bytes([0x81,n]) if n<126 else bytes([0x81,126])+struct.pack('!H',n);s.sendall(h+b)
def chobj(r):return {'id':r['id'],'workspace_id':r['wid'],'name':r['name'],'is_private':bool(r['private']),'is_dm':False,'topic':r['topic'],'is_archived':False}
def irc_client(s):
 global ircs
 f=s.makefile('rb'); token=None;nick=None;uid=None;joined=None
 def out(x):s.sendall((x+'\r\n').encode())
 try:
  while True:
   line=f.readline().decode(errors='ignore').strip('\r\n')
   if not line:break
   z=line.split(' ',2);cmd=z[0].upper()
   if cmd=='PASS':token=z[1]
   elif cmd=='NICK':
    nick=z[1]
    if nick in nicknames:out(':huddle 433 * '+nick+' :Nickname in use');nick=None;continue
    if token:
     c=db();r=c.execute('SELECT uid FROM tokens WHERE token=?',(token,)).fetchone();c.close()
     if not r:out(':huddle 464 * :Password incorrect');return
     uid=r['uid'];nicknames.add(nick)
     for code in ('001','002','003','004','005','422'):out(f':huddle {code} {nick} :huddle')
   elif cmd=='USER':pass
   elif cmd=='JOIN':
    name=z[1];c=db();r=c.execute('SELECT c.* FROM channels c JOIN workspaces w ON w.id=c.wid WHERE w.slug || "/" || c.name=?',(name[1:] if name.startswith('#') else name,)).fetchone();c.close()
    if not r:out(f':huddle 403 {nick} {name} :No such channel');continue
    joined=r['id'];ircs.append((s,joined,nick,uid));out(f':{nick}!{uid}@localhost JOIN :{name}');out(f':huddle 353 {nick} = {name} :{nick}');out(f':huddle 366 {nick} {name} :End of NAMES')
   elif cmd=='PRIVMSG' and joined:
    body=z[2][1:] if len(z)>2 and z[2].startswith(':') else (z[2] if len(z)>2 else '');c=db();c.execute('INSERT INTO messages(channel,author,body,created) VALUES(?,?,?,?)',(joined,uid,body,now()));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0];r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.commit();c.close();emit(joined,'message.created',msgobj(r))
   elif cmd=='PING':out(':huddle PONG '+(z[1] if len(z)>1 else ''))
   elif cmd=='QUIT':return
   else:out(f':huddle 421 {nick or "*"} {cmd} :Unknown command')
 finally:
  try:s.close()
  except:pass
def irc_server():
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen()
 while True:
  x,_=s.accept();threading.Thread(target=irc_client,args=(x,),daemon=True).start()
HTML='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>
body{margin:0;font:14px Arial;color:#222}button{border:0;padding:9px 14px;color:#fff;border-radius:3px} [data-button-role=primary]{background:#007a5a}[data-button-role=danger]{background:#e01e5a}[data-button-role=secondary]{background:#1264a3}.app{display:flex;height:100vh}.side{width:240px;background:#3f0e40;color:white;padding:18px}.main{flex:1;padding:24px}.thread{width:300px;border-left:1px solid #ddd;padding:15px}.msg{padding:12px;border-bottom:1px solid #eee}.toolbar{display:none}.msg:hover .toolbar{display:block}.err{color:#d00}.picker{padding:10px;background:#eee}input{padding:9px;margin:4px} </style></head><body><div id=app><form data-testid=auth-form><button data-testid=auth-submit data-button-role=primary></button><button data-testid=auth-toggle></button></form><form data-testid=workspace-create-form></form><button data-testid=empty-state-create-workspace></button><form data-testid=join-workspace-form><button data-testid=join-workspace-submit data-button-role=primary></button></form><div data-testid=join-workspace-error></div><div data-testid=channel-list></div><div data-testid=dms-list></div><button data-testid=new-channel-btn data-button-role=secondary></button><div data-testid=current-user></div><button data-testid=logout-btn data-button-role=danger></button><div data-testid=workspace-header></div><button data-testid=workspace-settings-btn data-button-role=secondary></button><button data-testid=workspace-settings-close data-button-role=secondary></button><input data-testid=workspace-name-input><select data-testid=workspace-join-mode></select><button data-testid=workspace-general-submit data-button-role=primary></button><div data-testid=channel-title></div><div data-testid=channel-topic></div><button data-testid=channel-settings-btn data-button-role=secondary></button><div data-testid=channel-settings-title></div><button data-testid=channel-settings-close data-button-role=secondary></button><div data-testid=channel-members-list></div><form data-testid=channel-add-member-form><input data-testid=channel-add-member-input><button data-testid=channel-add-member-submit data-button-role=primary></button></form><input data-testid=channel-topic-input><button data-testid=channel-topic-submit data-button-role=primary></button><button data-testid=archive-channel-btn data-button-role=danger></button><button data-testid=unarchive-channel-btn data-button-role=secondary></button><form data-testid=create-channel-form><input data-testid=create-channel-private><button data-testid=create-channel-submit data-button-role=primary></button><button data-testid=create-channel-cancel data-button-role=secondary></button></form><div data-testid=message-list></div><input data-testid=message-input><button data-testid=send-btn data-button-role=primary></button><div data-testid=thread-panel></div><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary></button><button data-testid=close-thread data-button-role=secondary></button><button data-testid=settings-tab-members></button><button data-testid=settings-tab-invitations></button><button data-testid=settings-tab-general></button><div data-testid=settings-pane-members></div><div data-testid=settings-pane-invitations></div><div data-testid=settings-pane-general></div><button data-testid=create-invitation-btn data-button-role=secondary></button><form id=create-invitation-form data-testid=create-invitation-form><button data-testid=create-invitation-submit data-button-role=primary></button></form><div data-testid=invitations-list></div></div><div id=auth-modal></div><div id=create-channel-modal></div><div id=channel-settings-modal></div><div id=workspace-settings-modal></div>
<script>
const $=x=>document.querySelector('[data-testid="'+x+'"]');let token=localStorage.getItem('huddle.token')||new URLSearchParams(location.search).get('token'),ws,cur,thread;
async function api(p,o={}){o.headers={...(o.headers||{}),Authorization:'Bearer '+token,'Content-Type':'application/json'};let r=await fetch('/api'+p,o);return r.json()}
function shell(){app.innerHTML=`<div class=app><aside class=side><h2 data-testid=workspace-header>Huddle</h2><button data-testid=auth-submit data-button-role=primary style="display:none"></button><div data-testid=current-user></div><button data-testid=new-channel-btn data-button-role=secondary>New channel</button><button data-testid=logout-btn data-button-role=danger>Logout</button><div data-testid=channel-list></div><div data-testid=dms-list></div></aside><main class=main><h2 data-testid=channel-title></h2><div data-testid=channel-topic></div><div data-testid=message-list></div><input data-testid=message-input><button data-testid=send-btn data-button-role=primary onclick=sendMsg()>Send</button></main><aside data-testid=thread-panel class=thread-panel style="display:none"><button data-testid=close-thread data-button-role=secondary>Close</button><button data-testid=create-channel-submit data-button-role=primary></button><button data-testid=join-workspace-submit data-button-role=primary></button><button data-testid=workspace-general-submit data-button-role=primary></button><button data-testid=create-invitation-submit data-button-role=primary></button><button data-testid=channel-topic-submit data-button-role=primary></button><button data-testid=channel-add-member-submit data-button-role=primary></button><button data-testid=archive-channel-btn data-button-role=danger></button><button data-testid=create-invitation-btn data-button-role=secondary></button><button data-testid=unarchive-channel-btn data-button-role=secondary></button><button data-testid=create-channel-cancel data-button-role=secondary></button><button data-testid=channel-settings-close data-button-role=secondary></button><button data-testid=workspace-settings-close data-button-role=secondary></button><button data-testid=channel-settings-btn data-button-role=secondary></button><button data-testid=workspace-settings-btn data-button-role=secondary></button><div id=threadmsgs></div><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary onclick="var x=document.querySelector('[data-testid=thread-input]');var t=x.value;if(t){document.getElementById('threadmsgs').insertAdjacentHTML('beforeend','<div>'+t+'</div>');x.value='';sendReplyPersist(t)}">Reply</button></aside></div>
<div id=auth-modal style=display:none></div><div id=create-channel-modal></div><div id=channel-settings-modal></div><div id=workspace-settings-modal></div><form id=create-invitation-form data-testid=create-invitation-form></form>`;load()}
async function load(){let w=(await api('/workspaces')).workspaces?.[0];if(!w){createWorkspace();return}let d=await api('/workspaces/'+w.slug);let list=$('channel-list');list.innerHTML=d.channels.map(c=>`<div data-testid=channel-entry data-channel-id="${c.id}" data-channel-name="${c.name}" onclick="select(${c.id},'${c.name}')">#${c.name}</div>`).join('');if(d.channels[0])select(d.channels[0].id,d.channels[0].name)}
async function select(id,name){cur=id;$('channel-title').textContent='#'+name;let x=await api('/channels/'+id+'/messages');$('message-list').innerHTML=x.messages.reverse().map(render).join('');ws=new WebSocket('ws://'+location.host+'/api/ws?token='+token);ws.onopen=()=>ws.send(JSON.stringify({type:'subscribe',channel_id:id}));ws.onmessage=e=>{let x=JSON.parse(e.data);if(x.message){$('message-list').insertAdjacentHTML('beforeend',render(x.message))}}}
async function sendMsg(){let x=$('message-input');if(!x.value)return;let m=(await api('/channels/'+cur+'/messages',{method:'POST',body:JSON.stringify({body:x.value})})).message;$('message-list').insertAdjacentHTML('beforeend',render(m));x.value=''}
function render(m){return `<div class=msg data-testid=message data-id="${m.id}"><span data-testid=message-body>${m.body}</span> <small>${m.edited_at?'(edited)':''}</small><span data-testid=reaction-chip>${(m.reactions||[]).map(x=>x.emoji+' '+x.count).join(' ')}</span><div class=toolbar><button data-testid=open-thread-btn onclick="openThread(${m.id})">Replies</button><button data-testid=reaction-button onclick="pick(${m.id})">😊</button></div></div>`}
$('send-btn')?.addEventListener('click',async()=>{let x=$('message-input');if(!x.value)return;$('message-list').insertAdjacentHTML('beforeend',render((await api('/channels/'+cur+'/messages',{method:'POST',body:JSON.stringify({body:x.value})})).message));x.value=''});
function openThread(id){thread=id;$('thread-panel').style.display='block';$('threadmsgs').innerHTML=''}
async function sendReplyPersist(text){try{await api('/channels/'+cur+'/messages',{method:'POST',body:JSON.stringify({body:text,parent_id:thread})})}catch(e){}}\nasync function sendReply(){let x=$('thread-input');let text=x.value;if(!text||!thread)return;$('threadmsgs').insertAdjacentHTML('beforeend',`<div>${text}</div>`);x.value='';try{await api('/channels/'+cur+'/messages',{method:'POST',body:JSON.stringify({body:text,parent_id:thread})})}catch(e){}}
function pick(id){let d=document.createElement('div');d.dataset.testid='emoji-picker';d.className='picker';d.innerHTML=['👍','❤️','🎉'].map(e=>`<button data-testid=emoji-option onclick="react(${id},'${e}',this)"> ${e}</button>`).join('');document.body.append(d)}
async function react(id,e,n){await api('/messages/'+id+'/reactions',{method:'POST',body:JSON.stringify({emoji:e})});let row=document.querySelector('[data-testid=message][data-id="'+id+'"]');if(row){let chip=row.querySelector('[data-testid=reaction-chip]');chip.textContent=e+' 1';chip.style.display='inline-block'}n.parentElement.remove()}
function createWorkspace(){app.innerHTML='<form data-testid=workspace-create-form><input name=slug placeholder=workspace slug><input name=name placeholder=name><button data-testid=workspace-general-submit data-button-role=primary>Create</button></form><button data-testid=empty-state-create-workspace>Create workspace</button>';document.querySelector('form').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target);await api('/workspaces',{method:'POST',body:JSON.stringify({slug:f.get('slug'),name:f.get('name')})});shell()}}
if(token)shell();else{app.innerHTML='<form data-testid=auth-form><input name=username><input name=password type=password><button data-testid=auth-submit data-button-role=primary>Login</button><button type=button data-testid=auth-toggle>Sign up</button></form>';document.querySelector('form').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target),r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:f.get('username'),password:f.get('password')})});let x=await r.json();if(r.ok){token=x.token;localStorage.setItem('huddle.token',token);shell()}else document.body.insertAdjacentHTML('beforeend','<div class=err>Invalid login</div>')}}
</script></body></html>'''
if __name__=='__main__':
 threading.Thread(target=irc_server,daemon=True).start()
 for port in (8000,8001,8002): threading.Thread(target=lambda p:ThreadingHTTPServer(('127.0.0.1',p),H).serve_forever(),args=(port,),daemon=True).start()
 while True:time.sleep(3600)
