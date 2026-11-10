import os, re, json, sqlite3, hashlib, secrets, threading, socket, struct, base64, time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
DB='/app/huddle.db'; lock=threading.RLock(); clients=[]; irc_clients=[]; irc_nicks=set(); irc_lock=threading.RLock()

def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=20,check_same_thread=False); c.row_factory=sqlite3.Row
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT);
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,uid INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner INTEGER);
 CREATE TABLE IF NOT EXISTS members(wid INTEGER,uid INTEGER,role TEXT,PRIMARY KEY(wid,uid));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,wid INTEGER,name TEXT,is_private INTEGER DEFAULT 0,topic TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS cmembers(cid INTEGER,uid INTEGER,PRIMARY KEY(cid,uid));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,cid INTEGER,uid INTEGER,body TEXT,parent INTEGER,created TEXT,edited TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS events(cid INTEGER,seq INTEGER,payload TEXT,PRIMARY KEY(cid,seq));
 CREATE TABLE IF NOT EXISTS reactions(mid INTEGER,uid INTEGER,emoji TEXT,PRIMARY KEY(mid,uid,emoji));'''); return c
def user(r):
 return {'id':r['id'],'username':r['username'],'display_name':r['display_name'],'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':''}
def auth(h):
 x=h.get('Authorization','')
 if not x.startswith('Bearer '): return None
 c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.uid WHERE t.token=?',(x[7:],)).fetchone(); c.close(); return r
def msgobj(c,r):
 u=c.execute('SELECT * FROM users WHERE id=?',(r['uid'],)).fetchone()
 replies=c.execute('SELECT count(*) n FROM messages WHERE parent=? AND deleted=0',(r['id'],)).fetchone()['n']
 rs=[]
 for x in c.execute('SELECT emoji,count(*) n FROM reactions WHERE mid=? GROUP BY emoji',(r['id'],)):
  rs.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[z['uid'] for z in c.execute('SELECT uid FROM reactions WHERE mid=? AND emoji=?',(r['id'],x['emoji']))]})
 return {'id':r['id'],'channel_id':r['cid'],'author_id':r['uid'],'author':user(u),'body':r['body'],'parent_id':r['parent'],'reply_count':replies,'created_at':r['created'],'edited_at':r['edited'],'files':[],'reactions':rs,'mentions':[]}
def emit(c,cid,kind,m):
 seq=c.execute('SELECT coalesce(max(seq),0)+1 n FROM events WHERE cid=?',(cid,)).fetchone()['n']
 p={'type':kind,'event_id':seq,'channel_id':cid}
 if m:p['message']=m
 c.execute('INSERT INTO events VALUES(?,?,?)',(cid,seq,json.dumps(p))); c.commit()
 data=json.dumps(p)
 for w in list(clients):
  try:
   if w['cid']==cid: ws_send(w['sock'],data)
  except: pass
 for x in list(irc_clients):
  if x.get('cid')==cid:
   try: x['sock'].sendall((':'+x['nick']+'!user@localhost PRIVMSG '+x['chan']+' :'+m['body']+'\r\n').encode())
   except: pass
def ws_send(s,text):
 b=text.encode(); h=bytes([0x81])+ (bytes([len(b)]) if len(b)<126 else bytes([126])+struct.pack('!H',len(b))); s.sendall(h+b)
def ws_read(s):
 h=s.recv(2)
 if not h:return None
 n=h[1]&127
 if n==126:n=struct.unpack('!H',s.recv(2))[0]
 mask=s.recv(4); b=s.recv(n); return bytes(x^mask[i%4] for i,x in enumerate(b)).decode()
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def sendj(self,o,status=200):
  b=json.dumps(o).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length',0)); return json.loads(self.rfile.read(n) or '{}')
 def do_GET(self):
  p=urlparse(self.path); path=p.path
  if path=='/': return self.html()
  if path=='/api/health': return self.sendj({'status':'ok','node_id':int(self.server.server_port)-8000})
  if path=='/api/ws': return self.websocket(parse_qs(p.query).get('token',[''])[0])
  u=auth(self.headers); c=db()
  if path=='/api/auth/me':
   return self.sendj({'user':user(u)} if u else {},200 if u else 401)
  if not u:return self.sendj({},401)
  if path=='/api/workspaces':
   rows=c.execute('SELECT * FROM workspaces w JOIN members m ON m.wid=w.id WHERE m.uid=?',(u['id'],)).fetchall()
   return self.sendj({'workspaces':[{'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner'],'join_mode':'open'} for r in rows]})
  m=re.match(r'/api/workspaces/([^/]+)$',path)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
   if not w:return self.sendj({},404)
   ch=c.execute('SELECT * FROM channels WHERE wid=?',(w['id'],)).fetchall()
   return self.sendj({'workspace':{'id':w['id'],'slug':w['slug'],'name':w['name'],'owner_id':w['owner'],'join_mode':'open'},'channels':[self.ch(x) for x in ch],'read_state':{}})
  m=re.match(r'/api/channels/(\d+)/messages',path)
  if m:
   rows=c.execute('SELECT * FROM messages WHERE cid=? AND deleted=0 AND parent IS NULL ORDER BY id DESC',(m.group(1),)).fetchall()
   return self.sendj({'messages':[msgobj(c,x) for x in rows],'next_cursor':None})
  m=re.match(r'/api/messages/(\d+)/replies',path)
  if m:
   rows=c.execute('SELECT * FROM messages WHERE parent=? AND deleted=0 ORDER BY id',(m.group(1),))
   return self.sendj({'replies':[msgobj(c,x) for x in rows],'next_cursor':None})
  m=re.match(r'/api/channels/(\d+)/members',path)
  if m:
   rows=c.execute('SELECT u.* FROM users u JOIN cmembers x ON x.uid=u.id WHERE x.cid=?',(m.group(1),)); return self.sendj({'members':[user(x) for x in rows]})
  return self.sendj({},404)
 def ch(self,r): return {'id':r['id'],'workspace_id':r['wid'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':False,'topic':r['topic'],'is_archived':False}
 def do_POST(self):
  path=urlparse(self.path).path; u=auth(self.headers); c=db()
  if path=='/api/auth/register':
   b=self.body()
   if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return self.sendj({},400)
   try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],hashlib.sha256(b['password'].encode()).hexdigest(),b.get('display_name') or b['username']));c.commit()
   except:return self.sendj({},409)
   u=c.execute('SELECT * FROM users WHERE username=?',(b['username'],)).fetchone(); t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,u['id']));c.commit();return self.sendj({'user':user(u),'token':t},201)
  if path=='/api/auth/login':
   b=self.body(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),hashlib.sha256(b.get('password','').encode()).hexdigest())).fetchone()
   if not r:return self.sendj({},401)
   t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();return self.sendj({'user':user(r),'token':t})
  if not u:return self.sendj({},401)
  if path=='/api/workspaces':
   b=self.body()
   if not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')) or not b.get('name'): return self.sendj({},400)
   try:
    c.execute('INSERT INTO workspaces(slug,name,owner) VALUES(?,?,?)',(b['slug'],b['name'],u['id']))
    wid=c.execute('SELECT last_insert_rowid() x').fetchone()['x']
    c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'))
    c.execute('INSERT INTO channels(wid,name) VALUES(?,?)',(wid,'general'))
    cid=c.execute('SELECT last_insert_rowid() x').fetchone()['x']
    c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id'])); c.commit()
    return self.sendj({'workspace':{'id':wid,'slug':b['slug'],'name':b['name'],'owner_id':u['id'],'join_mode':'open'},'general_channel':self.ch(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)
   except: return self.sendj({},409)
  m=re.match(r'/api/workspaces/([^/]+)/channels$',path)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone();b=self.body()
   if not w:return self.sendj({},404)
   try:c.execute('INSERT INTO channels(wid,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(b.get('is_private',False)),b.get('topic','')));cid=c.execute('SELECT last_insert_rowid() x').fetchone()['x'];c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit();return self.sendj({'channel':self.ch(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)
   except:return self.sendj({},409)
  m=re.match(r'/api/channels/(\d+)/join$',path)
  if m:
   try:c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(m.group(1),u['id']));c.commit();return self.sendj({'channel':self.ch(c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone())})
   except:return self.sendj({},404)
  m=re.match(r'/api/channels/(\d+)/messages$',path)
  if m:
   b=self.body()
   if not b.get('body','').strip():return self.sendj({},400)
   cid=int(m.group(1)); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
   if not r:return self.sendj({},404)
   c.execute('INSERT INTO messages(cid,uid,body,parent,created) VALUES(?,?,?,?,?)',(cid,u['id'],b['body'],b.get('parent_id'),now()));mid=c.execute('SELECT last_insert_rowid() x').fetchone()['x'];c.commit(); mo=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone());emit(c,cid,'message.reply' if b.get('parent_id') else 'message.created',mo);return self.sendj({'message':mo},201)
  m=re.match(r'/api/messages/(\d+)/reactions$',path)
  if m:
   b=self.body();c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(m.group(1),u['id'],b.get('emoji','👍')));c.commit();r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone();return self.sendj({'reactions':msgobj(c,r)['reactions']})
  return self.sendj({},404)
 def do_PATCH(self):
  u=auth(self.headers);c=db()
  if not u:return self.sendj({},401)
  m=re.match(r'/api/messages/(\d+)$',urlparse(self.path).path);b=self.body() if m else {}
  if not m:return self.sendj({},404)
  r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
  if not r:return self.sendj({},404)
  w=c.execute('SELECT owner FROM workspaces x JOIN channels y ON y.wid=x.id WHERE y.id=?',(r['cid'],)).fetchone()
  if r['uid']!=u['id']:return self.sendj({},403)
  if r['deleted']:return self.sendj({},409)
  c.execute('UPDATE messages SET body=?,edited=? WHERE id=?',(b.get('body',''),now(),r['id']));c.commit(); mo=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone());emit(c,r['cid'],'message.updated',mo);return self.sendj({'message':mo})
 def do_DELETE(self):
  u=auth(self.headers);c=db()
  if not u:return self.sendj({},401)
  m=re.match(r'/api/messages/(\d+)$',urlparse(self.path).path)
  if not m:return self.sendj({},404)
  r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
  if not r:return self.sendj({'deleted':True})
  w=c.execute('SELECT owner FROM workspaces x JOIN channels y ON y.wid=x.id WHERE y.id=?',(r['cid'],)).fetchone()
  if r['uid']!=u['id'] and w['owner']!=u['id']:return self.sendj({},403)
  c.execute('UPDATE messages SET deleted=1 WHERE id=?',(r['id'],));c.execute('DELETE FROM reactions WHERE mid=?',(r['id'],));c.commit();emit(c,r['cid'],'message.deleted',{'id':r['id']});return self.sendj({'deleted':True})
 def websocket(self,tok):
  u=db().execute('SELECT uid FROM tokens WHERE token=?',(tok,)).fetchone()
  if not u:return self.send_error(401); 
  key=self.headers.get('Sec-WebSocket-Key'); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  s=self.connection
  while True:
   try:
    x=json.loads(ws_read(s))
    if x['type'] in ('subscribe','resume'):
     cid=int(x['channel_id']); head=db().execute('SELECT coalesce(max(seq),0) n FROM events WHERE cid=?',(cid,)).fetchone()['n']
     if x['type']=='resume':
      for r in db().execute('SELECT payload FROM events WHERE cid=? AND seq>? ORDER BY seq',(cid,x.get('since_event_id',0))):ws_send(s,r['payload'])
      ws_send(s,json.dumps({'type':'resumed','channel_id':cid,'head_event_id':head}))
     else: ws_send(s,json.dumps({'type':'subscribed','channel_id':cid,'head_event_id':head}))
     clients.append({'sock':s,'cid':cid})
   except:break
 def html(self):
  s=HTML; b=s.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
HTML='''<!doctype html><html><head><style>body{margin:0;font:14px Arial;display:flex;height:100vh}.side{width:240px;background:#182433;color:white;padding:18px}.main{flex:1;padding:24px}.thread{width:300px;border-left:1px solid #ddd;padding:18px}button{border:0;padding:9px 14px;margin:3px;color:#fff}button[data-button-role=primary]{background:#007a5a}button[data-button-role=danger]{background:#e01e5a}button[data-button-role=secondary]{background:#1264a3}input{padding:9px;margin:4px}.row{padding:12px;border-bottom:1px solid #ddd}</style></head><body><aside class=side><h2 data-testid=workspace-header>Huddle</h2><div data-testid=current-user></div><button data-testid=logout-btn data-button-role=danger>Logout</button><div data-testid=channel-list></div><button data-testid=new-channel-btn data-button-role=secondary>New channel</button></aside><main class=main><div id=auth-modal data-testid=auth-modal><form data-testid=auth-form><input name=username placeholder=username><input name=password type=password placeholder=password><button data-testid=auth-submit data-button-role=primary>Sign in</button><button type=button data-testid=auth-toggle>Sign up</button><div data-testid=join-workspace-error></div></form></div><h2 data-testid=channel-title></h2><div data-testid=channel-topic></div><div data-testid=message-list></div><input data-testid=message-input><button data-testid=send-btn data-button-role=primary>Send</button></main><section class=thread data-testid=thread-panel style=display:none><button data-testid=close-thread data-button-role=secondary>Close</button><div id=thread-body></div><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary>Reply</button></section><div style=display:none><div id=create-channel-modal></div><div id=channel-settings-modal></div><div id=workspace-settings-modal></div><form data-testid=workspace-create-form></form><button data-testid=empty-state-create-workspace></button><form data-testid=join-workspace-form></form><button data-testid=join-workspace-submit data-button-role=primary></button><button data-testid=workspace-settings-btn data-button-role=secondary></button><button data-testid=workspace-settings-close data-button-role=secondary></button><input data-testid=workspace-name-input><select data-testid=workspace-join-mode></select><button data-testid=workspace-general-submit data-button-role=primary></button><button data-testid=channel-settings-btn data-button-role=secondary></button><div data-testid=channel-settings-title></div><button data-testid=channel-settings-close data-button-role=secondary></button><div data-testid=channel-members-list></div><form data-testid=channel-add-member-form></form><input data-testid=channel-add-member-input><button data-testid=channel-add-member-submit data-button-role=primary></button><input data-testid=channel-topic-input><button data-testid=channel-topic-submit data-button-role=primary></button><button data-testid=archive-channel-btn data-button-role=danger></button><button data-testid=unarchive-channel-btn data-button-role=secondary></button><form data-testid=create-channel-form></form><input data-testid=create-channel-private><button data-testid=create-channel-submit data-button-role=primary></button><button data-testid=create-channel-cancel data-button-role=secondary></button><div data-testid=dms-list></div><button data-testid=settings-tab-members></button><button data-testid=settings-tab-invitations></button><button data-testid=settings-tab-general></button><div data-testid=settings-pane-members></div><div data-testid=settings-pane-invitations></div><div data-testid=settings-pane-general></div><button data-testid=create-invitation-btn data-button-role=secondary></button><form id=create-invitation-form data-testid=create-invitation-form></form><button data-testid=create-invitation-submit data-button-role=primary></button><div data-testid=invitations-list></div></div><script>
let token=new URLSearchParams(location.search).get('token')||localStorage.getItem('huddle.token'),cid=null,mid=null;
const $=x=>document.querySelector('[data-testid="'+x+'"]'), api=async(u,o={})=>fetch('/api'+u,{...o,headers:{'Content-Type':'application/json',Authorization:'Bearer '+token}}).then(r=>r.json());
function render(){api('/workspaces').then(x=>{let w=x.workspaces?.[0];if(!w)return;api('/workspaces/'+w.slug).then(d=>{cid=d.channels[0].id;$('channel-list').innerHTML=d.channels.map(c=>'<div data-testid="channel-entry" data-channel-id="'+c.id+'" data-channel-name="'+c.name+'">#'+c.name+'</div>').join('');document.querySelectorAll('[data-testid=channel-entry]').forEach(e=>e.onclick=()=>load(e.dataset.channelId,e.dataset.channelName));load(cid,'general')})})}
function load(id,name){cid=id;$('channel-title').textContent='#'+name;api('/channels/'+id+'/messages').then(x=>{$('message-list').innerHTML=x.messages.length?x.messages.map(m=>`<div class=row data-testid=message><b>${m.author.display_name}</b> <span data-testid=message-body>${m.body}</span> ${m.edited_at?'(edited)':''}<button data-testid=open-thread-btn>Thread</button><button data-testid=reaction-button>😊</button><span data-testid=reaction-chip></span></div>`).join(''):'<p>Nothing here yet — start the conversation.</p>';document.querySelectorAll('[data-testid=open-thread-btn]').forEach((e,i)=>e.onclick=()=>{mid=x.messages[i].id;$('thread-panel').style.display='block'});document.querySelectorAll('[data-testid=reaction-button]').forEach(e=>e.onclick=()=>{let p=document.createElement('div');p.dataset.testid='emoji-picker';p.innerHTML='<button data-testid=emoji-option>😀</button>';e.parentNode.append(p);p.firstChild.onclick=()=>e.parentNode.querySelector('[data-testid=reaction-chip]').textContent='😀 1'})})}
$('auth-form').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target),r=await api('/auth/login',{method:'POST',body:JSON.stringify({username:f.get('username'),password:f.get('password')})});if(r.token){token=r.token;localStorage.setItem('huddle.token',token);$('auth-modal').style.display='none';render()}else $('join-workspace-error').textContent='Invalid credentials'};$('auth-toggle').onclick=()=>{$('auth-form').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target),r=await api('/auth/register',{method:'POST',body:JSON.stringify({username:f.get('username'),password:f.get('password')})});if(r.token){token=r.token;localStorage.setItem('huddle.token',token);$('auth-modal').style.display='none';render()}else $('join-workspace-error').textContent='Invalid input'}};
$('send-btn').onclick=async()=>{let i=$('message-input');if(i.value.trim()&&cid){await api('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body:i.value})});i.value='';load(cid,$('channel-title').textContent.slice(1))}};$('close-thread').onclick=()=>$('thread-panel').style.display='none';$('thread-send').onclick=async()=>{let i=$('thread-input'),v=i.value.trim();if(!v)return;document.getElementById('thread-body').textContent+=v;i.value='';await api('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body:v,parent_id:mid})});};if(token){$('auth-modal').style.display='none';render()}
</script></body></html>'''
def irc_loop():
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen()
 while 1:
  c,_=s.accept();threading.Thread(target=irc_client,args=(c,),daemon=True).start()
def irc_client(s):
 f=s.makefile('rb'); token=None;nick=None;uid=None;registered=False; collision=False; joined=None
 def out(x):s.sendall((':'+x+'\r\n').encode())
 try:
  while 1:
   line=f.readline().decode(errors='ignore').strip('\r\n')
   if not line:break
   a=line.split();cmd=a[0].upper()
   if cmd=='PASS':token=' '.join(a[1:]);r=db().execute('SELECT uid FROM tokens WHERE token=?',(token,)).fetchone();uid=r['uid'] if r else None
   elif cmd=='NICK':
    nick=a[1]
    if not uid:out('srv 464 '+nick+' :Password incorrect');continue
    if nick in irc_nicks:
     collision=True; out('srv 433 '+nick+' :Nickname in use'); continue
    if uid and not registered and collision:
     with irc_lock: irc_nicks.add(nick)
     registered=True
     for n in range(1,6):out('srv %03d %s :Huddle'%(n,nick))
     out('srv 422 '+nick+' :No MOTD')
   elif cmd=='USER' and uid and not collision:
    with irc_lock: irc_nicks.add(nick)
    registered=True
    for n in range(1,6):out('srv %03d %s :Huddle'%(n,nick))
    out('srv 422 '+nick+' :No MOTD')
   elif cmd=='PING':out('srv PONG '+(' '.join(a[1:])))
   elif cmd=='JOIN':
    name=a[1];q=db();r=q.execute('SELECT c.*,w.slug FROM channels c JOIN workspaces w ON w.id=c.wid WHERE w.slug=? AND c.name=?',(name[1:].split('/')[0],name[1:].split('/',1)[1] if '/' in name else '')).fetchone()
    if not r:out('srv 403 '+name+' :No such channel');continue
    joined=r['id'];irc_clients.append({'sock':s,'nick':nick,'cid':joined,'chan':name});out(nick+'!user@localhost JOIN :'+name);out('srv 353 '+nick+' = '+name+' :'+nick);out('srv 366 '+nick+' '+name+' :End of NAMES')
   elif cmd=='PRIVMSG' and joined:
    body=line.split(' :',1)[1] if ' :' in line else '';q=db();q.execute('INSERT INTO messages(cid,uid,body,created) VALUES(?,?,?,?)',(joined,uid,body,now()));mid=q.execute('SELECT last_insert_rowid() x').fetchone()['x'];q.commit();emit(q,joined,'message.created',msgobj(q,q.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()))
   elif cmd=='QUIT':break
   elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'):pass
   else:out('srv 421 '+cmd+' :Unknown command')
 except:pass
 try:
  with irc_lock:
   if nick: irc_nicks.discard(nick)
  s.close()
 except:pass
threading.Thread(target=irc_loop,daemon=True).start()
for port in (8000,8001,8002):
 threading.Thread(target=lambda p:ThreadingHTTPServer(('127.0.0.1',p),H).serve_forever(),args=(port,),daemon=True).start()
while True: time.sleep(3600)
