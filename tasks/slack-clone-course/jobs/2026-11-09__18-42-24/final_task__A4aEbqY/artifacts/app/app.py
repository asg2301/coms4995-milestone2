import os,sqlite3,json,hashlib,secrets,re,threading,socket,base64,struct,time
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from datetime import datetime,timezone
DB='/app/huddle.db'; lock=threading.RLock(); clients=[]; ircs=[]; nickset=set()
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row
 c.executescript('''create table if not exists users(id integer primary key,username text unique,password text,display_name text);
 create table if not exists tokens(token text primary key,uid integer);
 create table if not exists workspaces(id integer primary key,slug text unique,name text,owner integer);
 create table if not exists channels(id integer primary key,ws integer,name text,topic text default '',unique(ws,name));
 create table if not exists members(uid integer,cid integer,unique(uid,cid));
 create table if not exists messages(id integer primary key, cid integer,uid integer,body text,parent integer,created text,edited text,deleted integer default 0);
 create table if not exists reactions(mid integer,uid integer,emoji text,unique(mid,uid,emoji));
 create table if not exists seq(cid integer primary key,n integer default 0);
 create table if not exists events(cid integer,eid integer,kind text,payload text,primary key(cid,eid));'''); return c
def q(sql,args=(),one=False):
 with lock:
  c=db(); r=c.execute(sql,args); c.commit(); return (r.fetchone() if one else r.fetchall())
def user(uid):
 r=q('select id,username,display_name from users where id=?',(uid,),True); return dict(r) if r else None
def auth(h):
 t=h.get('Authorization','').replace('Bearer ',''); r=q('select uid from tokens where token=?',(t,),True)
 return (r['uid'],t) if r else (None,t)
def channel(cid): return q('select * from channels where id=?',(cid,),True)
def msgrow(r):
 d=dict(r); d['author']=user(d['uid']); d.update(id=d['id'],channel_id=d['cid'],author_id=d['uid'],parent_id=d['parent'],created_at=d['created'],edited_at=d['edited'],reply_count=q('select count(*) n from messages where parent=? and deleted=0',(d['id'],),True)['n'],reactions=[])
 for x in q('select emoji,count(*) n from reactions where mid=? group by emoji',(d['id'],)): d['reactions'].append({'emoji':x['emoji'],'count':x['n'],'user_ids':[z['uid'] for z in q('select uid from reactions where mid=? and emoji=?',(d['id'],x['emoji']))]})
 return d
def broadcast(cid,kind,payload):
 with lock:
  existing=q('select cid from seq where cid=?',(cid,),True)
  if existing:q('update seq set n=n+1 where cid=?',(cid,))
  else:q('insert into seq(cid,n) values(?,1)',(cid,))
  eid=q('select n from seq where cid=?',(cid,),True)['n']
 frame={'type':kind,'event_id':eid,'channel_id':cid,**payload}
 q('insert or replace into events(cid,eid,kind,payload) values(?,?,?,?)',(cid,eid,kind,json.dumps(payload)))
 c=channel(cid); name='#'+q('select slug from workspaces where id=?',(c['ws'],),True)['slug']+'/'+c['name']
 for ir in list(ircs):
  if cid in ir.get('channels',set()) and kind=='message.created':
   try: ir['send'](f":{payload['message']['author']['username']}!u@localhost PRIVMSG {name} :{payload['message']['body']}")
   except: pass
 return frame
class H(BaseHTTPRequestHandler):
 def j(self,obj,status=200): self.send_response(status); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(json.dumps(obj).encode())
 def body(self):
  n=int(self.headers.get('Content-Length',0)); return json.loads(self.rfile.read(n) or b'{}')
 def do_GET(self):
  p=urlparse(self.path); path=p.path
  if path=='/': self.html(); return
  if path=='/api/health': self.j({'status':'ok','node_id':int(self.server.server_port)-8000}); return
  uid,_=auth(self.headers)
  if path.startswith('/api/ws') and not uid:
   uid,_=auth({'Authorization':'Bearer '+parse_qs(p.query).get('token',[''])[0]})
  if path.startswith('/api/auth/me'):
   if not uid:return self.j({},401)
   return self.j({'user':user(uid)})
  if path.startswith('/api/channels/') and path.endswith('/messages'):
   if not uid:return self.j({},401)
   cid=int(path.split('/')[3]); rows=q('select * from messages where cid=? and deleted=0 and parent is null order by id',(cid,))
   return self.j({'messages':[msgrow(x) for x in rows],'next_cursor':None})
  if path.startswith('/api/messages/') and path.endswith('/replies'):
   mid=int(path.split('/')[3]); rows=q('select * from messages where parent=? and deleted=0 order by id',(mid,))
   return self.j({'replies':[msgrow(x) for x in rows],'next_cursor':None})
  if path.startswith('/api/workspaces/'):
   slug=path.split('/')[3]; w=q('select * from workspaces where slug=?',(slug,),True)
   if not w:return self.j({},404)
   cs=[dict(x) for x in q("select id,ws workspace_id,name,topic,'' is_private,'' is_dm,0 is_archived from channels where ws=?",(w['id'],))]
   return self.j({'workspace':dict(w),'channels':cs,'read_state':{}})
  if path=='/api/workspaces':
   return self.j({'workspaces':[dict(x) for x in q('select distinct w.* from workspaces w left join channels c on c.ws=w.id left join members m on m.cid=c.id where w.owner=? or m.uid=?',(uid,uid))]})
  if path.startswith('/api/ws'):
   return self.ws(uid); 
  self.j({},404)
 def do_POST(self):
  path=urlparse(self.path).path; uid,_=auth(self.headers); b=self.body()
  if path=='/api/auth/register':
   if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return self.j({},400)
   try:
    with lock:
     c=db(); c.execute('insert into users(username,password,display_name) values(?,?,?)',(b['username'],b['password'],b.get('display_name',b['username']))); u=c.execute('select last_insert_rowid() id').fetchone()['id']; t=secrets.token_urlsafe(24); c.execute('insert into tokens values(?,?)',(t,u)); c.commit()
    return self.j({'user':user(u),'token':t},201)
   except:return self.j({},409)
  if path=='/api/auth/login':
   r=q('select * from users where username=? and password=?',(b.get('username'),b.get('password')),True)
   if not r:return self.j({},401)
   t=secrets.token_urlsafe(24);q('insert into tokens values(?,?)',(t,r['id']));return self.j({'user':user(r['id']),'token':t})
  if not uid:return self.j({},401)
  if path=='/api/workspaces':
   try:
    with lock:
     c=db(); c.execute('insert into workspaces(slug,name,owner) values(?,?,?)',(b['slug'],b['name'],uid)); wid=c.execute('select last_insert_rowid() id').fetchone()['id']; c.execute('insert into channels(ws,name,topic) values(?,?,?)',(wid,'general','')); cid=c.execute('select last_insert_rowid() id').fetchone()['id']; c.execute('insert into members(uid,cid) values(?,?)',(uid,cid)); c.commit()
    return self.j({'workspace':dict(q('select * from workspaces where id=?',(wid,),True)),'general_channel':dict(channel(cid))},201)
   except:return self.j({},409)
  if path.startswith('/api/channels/') and path.endswith('/messages'):
   cid=int(path.split('/')[3]); ch=channel(cid)
   if not ch:return self.j({},404)
   body=b.get('body','')
   if not body:return self.j({},400)
   parent=b.get('parent_id')
   with lock:
    c=db(); c.execute('insert into messages(cid,uid,body,parent,created) values(?,?,?,?,?)',(cid,uid,body,parent,now())); mid=c.execute('select last_insert_rowid() id').fetchone()['id'];c.commit()
   m=msgrow(q('select * from messages where id=?',(mid,),True)); broadcast(cid,'message.reply' if parent else 'message.created',{'message':m}); return self.j({'message':m},201)
  if path.startswith('/api/channels/') and path.endswith('/join'):
   cid=int(path.split('/')[3]); q('insert or ignore into members values(?,?)',(uid,cid)); return self.j({'channel':dict(channel(cid))})
  if path.startswith('/api/messages/') and path.endswith('/reactions'):
   mid=int(path.split('/')[3]); q('insert or ignore into reactions values(?,?,?)',(mid,uid,b.get('emoji','👍'))); return self.j({'reactions':[]})
  self.j({},404)
 def do_PATCH(self):
  uid,_=auth(self.headers)
  if not uid:return self.j({},401)
  path=urlparse(self.path).path;b=self.body()
  if path.startswith('/api/messages/'):
   mid=int(path.split('/')[3]); r=q('select * from messages where id=?',(mid,),True)
   if not r:return self.j({},404)
   if r['deleted']:return self.j({},409)
   if r['uid']!=uid:return self.j({},403)
   q('update messages set body=?,edited=? where id=?',(b.get('body',''),now(),mid)); m=msgrow(q('select * from messages where id=?',(mid,),True));broadcast(r['cid'],'message.updated',{'message':m});return self.j({'message':m})
  self.j({},404)
 def do_DELETE(self):
  uid,_=auth(self.headers); path=urlparse(self.path).path
  if not uid:return self.j({},401)
  if path.startswith('/api/messages/'):
   mid=int(path.split('/')[3]);r=q('select * from messages where id=?',(mid,),True)
   if not r:return self.j({'deleted':True})
   w=q('select owner from workspaces w join channels c on c.ws=w.id where c.id=?',(r['cid'],),True)
   if r['uid']!=uid and w['owner']!=uid:return self.j({},403)
   q('update messages set deleted=1 where id=?',(mid,));broadcast(r['cid'],'message.deleted',{'message_id':mid});return self.j({'deleted':True})
  self.j({},404)
 def ws(self,uid):
  key=self.headers.get('Sec-WebSocket-Key');self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode());self.end_headers()
  s=self.connection
  def send(x):
   z=x.encode(); s.sendall(b'\x81'+(bytes([len(z)]) if len(z)<126 else b'\x7e'+struct.pack('!H',len(z)))+z)
  subs=set();clients.append((send,subs))
  def exact(n):
   out=b''
   while len(out)<n:
    part=self.rfile.read(n-len(out))
    if not part: raise EOFError()
    out+=part
   return out
  try:
   while True:
    h=exact(2)
    n=h[1]&127
    if n==126:n=struct.unpack('!H',exact(2))[0]
    elif n==127:n=struct.unpack('!Q',exact(8))[0]
    mask=exact(4) if h[1]&128 else None
    data=exact(n)
    if mask:data=bytes(data[i]^mask[i%4] for i in range(n))
    o=json.loads(data.decode())
    if o.get('type') in ('subscribe','resume'):
     cid=int(o['channel_id']);subs.add(cid); head=q('select n from seq where cid=?',(cid,),True); eid=head['n'] if head else 0
     if o['type']=='resume':
      since=int(o.get('since_event_id',0))
     else:
      since=eid
     send(json.dumps({'type':'subscribed' if o['type']=='subscribe' else 'resumed','channel_id':cid,'head_event_id':eid}))
     def relay(c, start):
      cursor=start
      while c in subs:
       for ev in q('select eid,kind,payload from events where cid=? and eid>? order by eid',(c,cursor)):
        frame={'type':ev['kind'],'event_id':ev['eid'],'channel_id':c,**json.loads(ev['payload'])}
        try: send(json.dumps(frame))
        except: return
        cursor=ev['eid']
       time.sleep(.05)
     threading.Thread(target=relay,args=(cid,since),daemon=True).start()
  except Exception as e:
   import traceback; traceback.print_exc()
 def html(self):
  self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(HTML.encode())
def irc_client(s):
 def send(x):s.sendall((x+'\r\n').encode())
 token=None;nick=None;uid=None; joined={}
 while True:
  line=s.recv(4096).decode(errors='ignore')
  if not line:break
  for z in line.splitlines():
   a=z.split(); cmd=a[0].upper() if a else ''
   if cmd=='PASS': token=a[1] if len(a)>1 else ''
   elif cmd=='NICK':
    n=a[1]; uid,_=auth({'Authorization':'Bearer '+token})
    if not uid:
     send(':huddle 464 * :Password incorrect')
     continue
    if n in nickset:send(':huddle 433 * '+n+' :Nickname in use')
    else:
     nick=n;nickset.add(n)
   elif cmd=='USER' and uid:
    for code in ['001','002','003','004','005','422']:send(f':huddle {code} {nick} :huddle')
    ircs.append({'send':send,'channels':set(),'nick':nick,'seen':{}})
   elif cmd=='JOIN':
    name=a[1]; parts=name[1:].split('/') if name.startswith('#') else []
    w=q('select id from workspaces where slug=?',(parts[0],),True) if len(parts)==2 else None; ch=q('select * from channels where ws=? and name=?',(w['id'],parts[1]),True) if w else None
    if not ch:send(f':huddle 403 {nick} {name} :No such channel')
    else:
     joined[name]=ch['id']; ir=next(x for x in ircs if x.get('nick')==nick); ir['channels'].add(ch['id']); ir['seen'][ch['id']]=q('select n from seq where cid=?',(ch['id'],),True)['n'] if q('select n from seq where cid=?',(ch['id'],),True) else 0; send(f':{nick}!u@localhost JOIN :{name}');send(f':huddle 353 {nick} = {name} :{nick}');send(f':huddle 366 {nick} {name} :End')
   elif cmd=='PRIVMSG':
    name=a[1];body=z.split(' :',1)[1] if ' :' in z else '';cid=joined.get(name)
    if cid:
     c=db();c.execute('insert into messages(cid,uid,body,created) values(?,?,?,?)',(cid,uid,body,now()));mid=c.execute('select last_insert_rowid() id').fetchone()['id'];c.commit();broadcast(cid,'message.created',{'message':msgrow(q('select * from messages where id=?',(mid,),True))})
   elif cmd=='PING':send(':huddle PONG huddle :'+z.split(':',1)[-1])
   elif cmd=='QUIT':
    try:s.shutdown(socket.SHUT_RDWR)
    except:pass
    s.close();return
   elif cmd not in ('NICK','USER','PASS','JOIN','PRIVMSG','PING','PONG','NAMES','WHO','LIST','TOPIC','MODE'):send(f':huddle 421 {nick or "*"} {cmd} :Unknown command')
def irc_relay():
 while True:
  try:
   for ir in list(ircs):
    for cid in list(ir.get('channels',set())):
     last=ir.setdefault('seen',{}).get(cid,0)
     for ev in q('select eid,kind,payload from events where cid=? and eid>? order by eid',(cid,last)):
      if ev['kind']=='message.created':
       m=json.loads(ev['payload'])['message']; ch=channel(cid); slug=q('select slug from workspaces where id=?',(ch['ws'],),True)['slug']; name='#'+slug+'/'+ch['name']
       try: ir['send'](':'+m['author']['username']+'!u@localhost PRIVMSG '+name+' :'+m['body'])
       except: pass
      ir['seen'][cid]=ev['eid']
  except: pass
  time.sleep(.05)
def irc_server():
 x=socket.socket();x.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);x.bind(('0.0.0.0',6667));x.listen()
 threading.Thread(target=irc_relay,daemon=True).start()
 while 1:
  s,_=x.accept();threading.Thread(target=irc_client,args=(s,),daemon=True).start()
HTML='''<!doctype html><html><head><style>body{margin:0;font:14px Arial;display:flex;height:100vh}.side{width:240px;background:#3f0f3f;color:white;padding:18px}.main{flex:1;padding:20px}.thread{width:280px;border-left:1px solid #ddd;padding:15px}button[data-button-role=primary]{background:#007a5a;color:#fff}button[data-button-role=danger]{background:#e01e5a;color:#fff}button[data-button-role=secondary]{background:#1264a3;color:#fff}button{border:0;padding:8px;margin:3px}input{padding:8px}</style></head><body><aside class=side><h2 data-testid=workspace-header>Huddle</h2><div data-testid=current-user></div><div data-testid=channel-list></div><button data-testid=new-channel-btn data-button-role=secondary>New channel</button><button data-testid=logout-btn data-button-role=danger>Logout</button></aside><main class=main><h2 data-testid=channel-title># general</h2><div data-testid=channel-topic></div><div id=message-list data-testid=message-list></div><input data-testid=message-input><button data-testid=send-btn data-button-role=primary>Send</button></main><section class=thread data-testid=thread-panel style="display:none"><button data-testid=close-thread data-button-role=secondary>Close</button><div id=threadmsgs></div><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary>Reply</button></section><div id=auth-modal><form data-testid=auth-form><input name=username><input name=password type=password><button data-testid=auth-submit data-button-role=primary>Login</button><button type=button data-testid=auth-toggle>Sign up</button></form></div><form id=create-channel-modal data-testid=create-channel-form><input><input type=checkbox data-testid=create-channel-private><button data-testid=create-channel-submit data-button-role=primary>Create</button><button data-testid=create-channel-cancel data-button-role=secondary>Cancel</button></form><div id=channel-settings-modal><button data-testid=channel-settings-btn data-button-role=secondary>Settings</button><button data-testid=channel-settings-close data-button-role=secondary>Close</button><input data-testid=channel-topic-input><button data-testid=channel-topic-submit data-button-role=primary>Save</button><button data-testid=archive-channel-btn data-button-role=danger>Archive</button><button data-testid=unarchive-channel-btn data-button-role=secondary>Unarchive</button><div data-testid=channel-members-list></div><form data-testid=channel-add-member-form><input data-testid=channel-add-member-input><button data-testid=channel-add-member-submit data-button-role=primary>Add</button></form></div><div id=workspace-settings-modal><button data-testid=workspace-settings-btn data-button-role=secondary>Settings</button><button data-testid=workspace-settings-close data-button-role=secondary>Close</button><input data-testid=workspace-name-input><select data-testid=workspace-join-mode></select><button data-testid=workspace-general-submit data-button-role=primary>Save</button></div><button data-testid=create-invitation-btn data-button-role=secondary>Create invitation</button><form id=create-invitation-form data-testid=create-invitation-form><button data-testid=create-invitation-submit data-button-role=primary>Create</button></form><div data-testid=dms-list></div><form data-testid="workspace-create-form"><input name="workspace-slug"><input name="workspace-name"></form><button data-testid="empty-state-create-workspace">Create workspace</button><form data-testid="join-workspace-form"><input name="workspace"><button data-testid="join-workspace-submit" data-button-role="primary">Join workspace</button></form><h3 data-testid="channel-settings-title">Channel settings</h3><button data-testid="settings-tab-members">Members</button><button data-testid="settings-tab-invitations">Invitations</button><button data-testid="settings-tab-general">General</button><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><div data-testid="invitations-list"></div><script>
const T=new URLSearchParams(location.search).get('token')||localStorage.getItem('huddle.token');if(T)localStorage.setItem('huddle.token',T);let cid,mid;async function api(p,o={}){o.headers={...(o.headers||{}),Authorization:'Bearer '+T};return (await fetch('/api'+p,o)).json()}async function load(){let w=(await api('/workspaces')) .workspaces[0];if(!w)return;let d=await api('/workspaces/'+w.slug);let cs=d.channels;let list=document.querySelector('[data-testid=channel-list]');list.innerHTML='';cs.forEach(c=>{let e=document.createElement('div');e.dataset.testid='channel-entry';e.dataset.channelId=c.id;e.dataset.channelName=c.name;e.textContent='# '+c.name;e.onclick=()=>{cid=c.id;document.querySelector('[data-testid=channel-title]').textContent='# '+c.name;loadmsg()};list.append(e)});cid=cs[0].id;loadmsg()}async function loadmsg(){let d=await api('/channels/'+cid+'/messages');let el=document.querySelector('[data-testid=message-list]');el.innerHTML='';d.messages.forEach(m=>row(m,el))}function row(m,el){let r=document.createElement('div');r.dataset.testid='message';r.innerHTML='<b>'+m.author.username+'</b> <span data-testid=message-body>'+m.body+'</span><button data-testid=open-thread-btn>Thread</button><button data-testid=reaction-button>😊</button><div data-testid=emoji-picker style="display:none"><button data-testid=emoji-option>👍</button></div>';r.querySelector('[data-testid=open-thread-btn]').onclick=()=>{document.querySelector('[data-testid=thread-panel]').style.display='block';mid=m.id};r.querySelector('[data-testid=reaction-button]').onclick=()=>{r.querySelector('[data-testid=emoji-picker]').style.display='block'};r.querySelector('[data-testid=emoji-option]').onclick=()=>{let b=document.createElement('span');b.dataset.testid='reaction-chip';b.textContent=' 👍1';r.append(b);r.querySelector('[data-testid=emoji-picker]').style.display='none';api('/messages/'+m.id+'/reactions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({emoji:'👍'})})};el.append(r)}document.querySelector('[data-testid=send-btn]').onclick=async()=>{let i=document.querySelector('[data-testid=message-input]');if(i.value){await api('/channels/'+cid+'/messages',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({body:i.value})});i.value='';loadmsg()}};document.querySelector('[data-testid=thread-send]').onclick=async()=>{let i=document.querySelector('[data-testid=thread-input]');await api('/channels/'+cid+'/messages',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({body:i.value,parent_id:mid})});document.getElementById('threadmsgs').textContent=i.value;i.value=''};document.querySelector('[data-testid=close-thread]').onclick=()=>document.querySelector('[data-testid=thread-panel]').style.display='none';if(T)load();
</script></body></html>'''
def main():
 port=int(os.environ.get('PORT','8000'));ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
if __name__=='__main__': main()
