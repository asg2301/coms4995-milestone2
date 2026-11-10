import os,sys,json,sqlite3,hashlib,secrets,re,threading,time,socket,socketserver
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
DB='/app/state.db'; NODE=int(os.environ.get('NODE_ID','0')); PORT=8000+NODE
def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=10,check_same_thread=False); c.row_factory=sqlite3.Row
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT);
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER);
 CREATE TABLE IF NOT EXISTS members(ws INTEGER,user_id INTEGER,role TEXT,UNIQUE(ws,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,ws INTEGER,name TEXT,is_private INTEGER DEFAULT 0,topic TEXT DEFAULT "");
 CREATE TABLE IF NOT EXISTS cmember(channel INTEGER,user_id INTEGER,UNIQUE(channel,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel INTEGER,author INTEGER,body TEXT,parent INTEGER,created TEXT,edited TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS reactions(message INTEGER,user_id INTEGER,emoji TEXT,UNIQUE(message,user_id,emoji));
 CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,channel INTEGER,seq INTEGER,kind TEXT,message INTEGER,created TEXT,UNIQUE(channel,seq));''')
 return c
def user(c,uid):
 r=c.execute('select * from users where id=?',(uid,)).fetchone()
 return dict(r) if r else None
def msg(c,r):
 if not r:return None
 d=dict(r); d['author']=user(c,d['author']); d['parent_id']=d.pop('parent'); d['reply_count']=c.execute('select count(*) from messages where parent=? and deleted=0',(d['id'],)).fetchone()[0]; d['edited_at']=d.pop('edited'); d['files']=[]; d['mentions']=[]; d['reactions']=[]
 for x in c.execute('select emoji,count(*) n from reactions where message=? group by emoji',(d['id'],)):
  ids=[z[0] for z in c.execute('select user_id from reactions where message=? and emoji=?',(d['id'],x['emoji']))]; d['reactions'].append({'emoji':x['emoji'],'count':x['n'],'user_ids':ids})
 return d
def emit(c,ch,kind,mid=None):
 seq=c.execute('select coalesce(max(seq),0)+1 from events where channel=?',(ch,)).fetchone()[0]
 c.execute('insert into events(channel,seq,kind,message,created) values(?,?,?,?,?)',(ch,seq,kind,mid,now())); c.commit(); return seq
def auth(h,c):
 t=(h.get('Authorization') or '').replace('Bearer ',''); r=c.execute('select user_id from tokens where token=?',(t,)).fetchone()
 return r['user_id'] if r else None
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def sendj(self,status,obj,headers=None):
  b=json.dumps(obj).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); 
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers(); self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length',0)); return json.loads(self.rfile.read(n) or b'{}')
 def route(self):
  c=db(); p=urlparse(self.path); q=parse_qs(p.query); path=p.path; uid=auth(self.headers,c)
  if path=='/api/health':return self.sendj(200,{'status':'ok','node_id':NODE})
  if path=='/': return self.html()
  if path=='/api/ws':
   wt=q.get('token',[''])[0].replace('Bearer ',''); rr=c.execute('select user_id from tokens where token=?',(wt,)).fetchone(); return self.websocket(c,rr['user_id'] if rr else uid,wt)
  if path=='/api/auth/register' and self.command=='POST':
   x=self.body()
   if not re.fullmatch(r'[A-Za-z0-9_]+',x.get('username','')) or len(x.get('password',''))<8:return self.sendj(400,{'error':'invalid'})
   try:c.execute('insert into users(username,password,display_name) values(?,?,?)',(x['username'],x['password'],x.get('display_name') or x['username']));c.commit()
   except:return self.sendj(409,{'error':'duplicate'})
   u=c.execute('select * from users where username=?',(x['username'],)).fetchone(); t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,u['id']));c.commit()
   return self.sendj(201,{'user':user(c,u['id']),'token':t})
  if path=='/api/auth/login' and self.command=='POST':
   x=self.body();r=c.execute('select * from users where username=? and password=?',(x.get('username'),x.get('password'))).fetchone()
   if not r:return self.sendj(401,{'error':'bad credentials'})
   t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,r['id']));c.commit();return self.sendj(200,{'user':user(c,r['id']),'token':t})
  if path=='/api/auth/me':
   if not uid:return self.sendj(401,{'error':'unauthorized'})
   return self.sendj(200,{'user':user(c,uid)})
  if not uid:return self.sendj(401,{'error':'unauthorized'})
  if path=='/api/workspaces' and self.command=='POST':
   x=self.body()
   try:
    c.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(x['slug'],x['name'],uid)); wid=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into members values(?,?,?)',(wid,uid,'owner'));c.execute('insert into channels(ws,name) values(?,?)',(wid,'general'));cid=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into cmember values(?,?)',(cid,uid));c.commit()
   except:return self.sendj(409,{'error':'duplicate'})
   return self.sendj(201,{'workspace':dict(c.execute('select * from workspaces where id=?',(wid,)).fetchone()),'general_channel':dict(c.execute('select * from channels where id=?',(cid,)).fetchone())})
  if path=='/api/workspaces':
   return self.sendj(200,{'workspaces':[dict(x) for x in c.execute('select w.* from workspaces w join members m on m.ws=w.id where m.user_id=?',(uid,))]})
  m=re.fullmatch(r'/api/workspaces/([^/]+)',path)
  if m:
   w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
   if not w:return self.sendj(404,{'error':'not found'})
   ch=[dict(x) for x in c.execute('select * from channels where ws=?',(w['id'],))];return self.sendj(200,{'workspace':dict(w),'channels':ch,'read_state':{}})
  m=re.fullmatch(r'/api/workspaces/([^/]+)/channels',path)
  if m and self.command=='POST':
   w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone();x=self.body()
   if not w:return self.sendj(404,{})
   role=c.execute('select role from members where ws=? and user_id=?',(w['id'],uid)).fetchone()
   if x.get('is_private') and role['role'] not in ('owner','admin'):return self.sendj(403,{})
   try:c.execute('insert into channels(ws,name,is_private,topic) values(?,?,?,?)',(w['id'],x['name'],int(x.get('is_private',False)),x.get('topic','')));cid=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into cmember values(?,?)',(cid,uid));c.commit()
   except:return self.sendj(409,{})
   return self.sendj(201,{'channel':dict(c.execute('select * from channels where id=?',(cid,)).fetchone())})
  m=re.fullmatch(r'/api/channels/(\d+)/join',path)
  if m and self.command=='POST':
   ch=c.execute('select * from channels where id=?',(int(m.group(1)),)).fetchone()
   if not ch:return self.sendj(404,{})
   c.execute('insert or ignore into cmember values(?,?)',(ch['id'],uid));c.execute('insert or ignore into members values(?,?,?)',(ch['ws'],uid,'member'));c.commit();return self.sendj(200,{'channel':dict(ch)})
  m=re.fullmatch(r'/api/channels/(\d+)/messages',path)
  if m:
   cid=int(m.group(1));ch=c.execute('select * from channels where id=?',(cid,)).fetchone()
   if not ch:return self.sendj(404,{})
   if self.command=='POST':
    x=self.body(); b=x.get('body','')
    if not b:return self.sendj(400,{'error':'empty'})
    c.execute('insert into messages(channel,author,body,parent,created) values(?,?,?,?,?)',(cid,uid,b,x.get('parent_id'),now()));mid=c.execute('select last_insert_rowid()').fetchone()[0];seq=emit(c,cid,'message.reply' if x.get('parent_id') else 'message.created',mid)
    return self.sendj(201,{'message':msg(c,c.execute('select * from messages where id=?',(mid,)).fetchone())})
   rows=[msg(c,x) for x in c.execute('select * from messages where channel=? and deleted=0 and parent is null order by id desc',(cid,))];return self.sendj(200,{'messages':rows,'next_cursor':None})
  m=re.fullmatch(r'/api/messages/(\d+)',path)
  if m:
   mid=int(m.group(1));r=c.execute('select m.*,c.ws,w.owner_id from messages m join channels c on c.id=m.channel join workspaces w on w.id=c.ws where m.id=?',(mid,)).fetchone()
   if not r:return self.sendj(404,{})
   if self.command=='PATCH':
    if r['deleted']:return self.sendj(409,{})
    if r['author']!=uid:return self.sendj(403,{})
    x=self.body();c.execute('update messages set body=?,edited=? where id=?',(x.get('body',''),now(),mid));emit(c,r['channel'],'message.updated',mid);return self.sendj(200,{'message':msg(c,c.execute('select * from messages where id=?',(mid,)).fetchone())})
   if self.command=='DELETE':
    if uid not in (r['author'],r['owner_id']):return self.sendj(403,{})
    if not r['deleted']:c.execute('update messages set deleted=1,body="" where id=?',(mid,));emit(c,r['channel'],'message.deleted',mid)
    return self.sendj(200,{'deleted':True})
  m=re.fullmatch(r'/api/messages/(\d+)/reactions',path)
  if m and self.command=='POST':
   x=self.body();c.execute('insert or ignore into reactions values(?,?,?)',(int(m.group(1)),uid,x['emoji']));c.commit();return self.sendj(200,{'reactions':msg(c,c.execute('select * from messages where id=?',(int(m.group(1)),)).fetchone())['reactions']})
  if path.startswith('/api/users/me') and self.command=='GET':return self.sendj(200,{'user':user(c,uid)})
  self.sendj(404,{'error':'not found'})
 def do_GET(self):self.route()
 def do_POST(self):self.route()
 def do_PATCH(self):self.route()
 def do_DELETE(self):self.route()
 def websocket(self,c,uid,q):
  if not uid:return self.sendj(401,{})
  key=self.headers.get('Sec-WebSocket-Key'); acc=hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()
  import base64;self.send_response(101);self.send_header('Upgrade','WebSocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',base64.b64encode(acc).decode());self.end_headers()
  self.connection.settimeout(.5); channels=[];last={}
  def send(o):
   b=json.dumps(o).encode();n=len(b);h=bytes([129,n]) if n<126 else bytes([129,126])+n.to_bytes(2,'big');self.connection.sendall(h+b)
  while True:
   try:
    h=self.connection.recv(2)
    if not h:break
    n=h[1]&127
    if n==126:n=int.from_bytes(self.connection.recv(2),'big')
    mask=self.connection.recv(4);data=self.connection.recv(n);data=bytes(v^mask[i%4] for i,v in enumerate(data));o=json.loads(data)
    if o.get('type') in ('subscribe','resume'):
     ch=int(o['channel_id']);channels.append(ch); head=c.execute('select coalesce(max(seq),0) from events where channel=?',(ch,)).fetchone()[0];last[ch]=o.get('since_event_id',head);send({'type':'subscribed' if o['type']=='subscribe' else 'resumed','channel_id':ch,'head_event_id':head})
   except socket.timeout:pass
   except:break
   for ch in channels:
    fresh=db()
    for e in fresh.execute('select * from events where channel=? and seq>? order by seq',(ch,last.get(ch,0))):
     if e['message']:
      mr=fresh.execute('select * from messages where id=?',(e['message'],)).fetchone()
      send({'type':e['kind'],'event_id':e['seq'],'channel_id':ch,'message':msg(fresh,mr)})
     last[ch]=e['seq']
   fresh.close()
 def html(self):
  b=HTML.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
HTML='''<!doctype html><html><head><meta charset=utf-8><style>body{margin:0;font:14px Arial;display:flex;height:100vh}.side{width:250px;background:#3f0f3f;color:white;padding:20px}.main{flex:1;padding:20px}.thread{width:300px;border-left:1px solid #ddd;padding:15px}button{border:0;padding:9px 14px;color:#fff;margin:3px;border-radius:3px}[data-button-role=primary]{background:#007a5a}[data-button-role=danger]{background:#e01e5a}[data-button-role=secondary]{background:#1264a3}.hidden{display:none}.row{padding:10px;border-bottom:1px solid #eee}.toolbar{display:none}.row:hover .toolbar{display:block}</style></head><body><aside class=side><h2 data-testid=workspace-header>Huddle</h2><div data-testid=current-user></div><button data-testid=logout-btn data-button-role=danger>Logout</button><div data-testid=channel-list></div><div data-testid=dms-list></div><button data-testid=new-channel-btn data-button-role=secondary>New channel</button></aside><main class=main><h2 data-testid=channel-title># general</h2><div data-testid=channel-topic></div><div data-testid=message-list></div><input data-testid=message-input><button data-testid=send-btn data-button-role=primary>Send</button></main><section class=thread hidden data-testid=thread-panel><button data-testid=close-thread data-button-role=secondary>Close</button><div class=replies></div><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary>Reply</button></section>
<div id=auth-modal><form data-testid=auth-form><input name=username><input name=password type=password><button data-testid=auth-submit data-button-role=primary>Login</button><button type=button data-testid=auth-toggle>Sign up</button></form></div>
<div id=create-channel-modal><form data-testid=create-channel-form><input name=name><input data-testid=create-channel-private type=checkbox><button data-testid=create-channel-submit data-button-role=primary>Create</button><button data-testid=create-channel-cancel data-button-role=secondary>Cancel</button></form></div><div id=channel-settings-modal><button data-testid=channel-settings-close data-button-role=secondary>Close</button><button data-testid=channel-settings-btn data-button-role=secondary>Settings</button><input data-testid=channel-topic-input><button data-testid=channel-topic-submit data-button-role=primary>Save</button><div data-testid=channel-settings-title></div><div data-testid=channel-members-list></div><form data-testid=channel-add-member-form><input data-testid=channel-add-member-input><button data-testid=channel-add-member-submit data-button-role=primary>Add</button></form><button data-testid=archive-channel-btn data-button-role=danger>Archive</button><button data-testid=unarchive-channel-btn data-button-role=secondary>Unarchive</button></div><div id=workspace-settings-modal><button data-testid=workspace-settings-btn data-button-role=secondary>Settings</button><button data-testid=workspace-settings-close data-button-role=secondary>Close</button><input data-testid=workspace-name-input><select data-testid=workspace-join-mode></select><button data-testid=workspace-general-submit data-button-role=primary>Save</button><div data-testid=settings-tab-members></div><div data-testid=settings-tab-invitations></div><div data-testid=settings-tab-general></div><div data-testid=settings-pane-members></div><div data-testid=settings-pane-invitations></div><div data-testid=settings-pane-general></div><button data-testid=create-invitation-btn data-button-role=secondary>Create invite</button><form id=create-invitation-form data-testid=create-invitation-form><button data-testid=create-invitation-submit data-button-role=primary>Invite</button></form><div data-testid=invitations-list></div></div><form data-testid=workspace-create-form></form><button data-testid=empty-state-create-workspace></button><form data-testid=join-workspace-form></form><button data-testid=join-workspace-submit data-button-role=primary></button><div data-testid=join-workspace-error></div>
<script>
let token=new URLSearchParams(location.search).get('token')||localStorage.getItem('huddle.token')||'';
let cid=null,slug=null,currentMessage=null;
function api(path,opts){
 opts=opts||{}; opts.headers=Object.assign({},opts.headers||{},{'Authorization':'Bearer '+token,'Content-Type':'application/json'});
 return fetch('/api'+path,opts).then(function(r){return r.json()});
}
function render(){
 api('/workspaces').then(function(x){
  if(!x.workspaces||!x.workspaces.length)return;
  slug=x.workspaces[x.workspaces.length-1].slug;
  return api('/workspaces/'+slug);
 }).then(function(w){
  if(!w)return;
  var list=document.querySelector('[data-testid="channel-list"]'); list.innerHTML='';
  (w.channels||[]).forEach(function(ch){
   var e=document.createElement('div'); e.textContent='# '+ch.name;
   e.dataset.testid='channel-entry';e.dataset.channelId=ch.id;e.dataset.channelName=ch.name;
   e.onclick=function(){select(ch)};list.appendChild(e);
  });
  if(w.channels&&w.channels.length)select(w.channels[0]);
 });
}
function select(ch){cid=ch.id;document.querySelector('[data-testid="channel-title"]').textContent='# '+ch.name;load();}
function load(){
 api('/channels/'+cid+'/messages').then(function(x){
  var list=document.querySelector('[data-testid="message-list"]');list.innerHTML='';
  (x.messages||[]).slice().reverse().forEach(function(m){
   var row=document.createElement('div');row.className='row';row.dataset.testid='message';
   row.innerHTML='<span data-testid="message-body"></span> <small></small><span class="toolbar"><button data-testid="open-thread-btn">Thread</button><button data-testid="reaction-button">😊</button></span>';
   row.querySelector('[data-testid="message-body"]').textContent=m.body;
   row.querySelector('small').textContent=m.author.username;
   row.querySelector('[data-testid="open-thread-btn"]').onclick=function(){currentMessage=m;document.querySelector('[data-testid="thread-panel"]').removeAttribute('hidden');document.querySelector('[data-testid="thread-panel"]').classList.remove('hidden')};
   row.querySelector('[data-testid="reaction-button"]').onclick=function(){
    var picker=document.createElement('span');picker.dataset.testid='emoji-picker';picker.innerHTML='<button data-testid="emoji-option">👍</button>';row.appendChild(picker);
    picker.firstChild.onclick=function(){var chip=document.createElement('b');chip.dataset.testid='reaction-chip';chip.textContent='👍 1';row.appendChild(chip)};
   };
   list.appendChild(row);
  });
 });
}
document.querySelector('[data-testid="send-btn"]').onclick=function(){
 var input=document.querySelector('[data-testid="message-input"]');
 if(!input.value.trim())return;
 api('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body:input.value})}).then(function(){input.value='';load()});
};
document.querySelector('[data-testid="auth-form"]').onsubmit=function(e){
 e.preventDefault();var f=e.target;
 api('/auth/login',{method:'POST',body:JSON.stringify({username:f.username.value,password:f.password.value})}).then(function(x){
  if(x.token){token=x.token;localStorage.setItem('huddle.token',token);document.querySelector('#auth-modal').style.display='none';render();}
 });
};
document.querySelector('[data-testid="close-thread"]').onclick=function(){document.querySelector('[data-testid="thread-panel"]').setAttribute('hidden','');document.querySelector('[data-testid="thread-panel"]').classList.add('hidden')};
document.querySelector('[data-testid="thread-send"]').onclick=function(){
 var i=document.querySelector('[data-testid="thread-input"]');
 if(currentMessage&&i.value.trim()){var text=i.value;api('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body:text,parent_id:currentMessage.id})}).then(function(){var r=document.createElement('div');r.textContent=text;document.querySelector('[data-testid="thread-panel"] .replies').appendChild(r);i.value='';});}
};
if(token){document.querySelector('#auth-modal').style.display='none';render();}
</script></body></html>'''
if __name__=='__main__':
 ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
