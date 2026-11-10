import os, re, json, uuid, sqlite3, hashlib, base64, threading, time, mimetypes, urllib.parse, email.parser, email.policy
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
DB='/app/data/huddle.db'; os.makedirs('/app/data',exist_ok=True)
lock=threading.RLock(); con=sqlite3.connect(DB,check_same_thread=False); con.row_factory=sqlite3.Row
def q(s,args=(),one=False):
 with lock:
  r=con.execute(s,args); con.commit(); return (r.fetchone() if one else r.fetchall())
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def uid(): return str(uuid.uuid4())
def init():
 q('''create table if not exists users(id text primary key,username text unique,password text,display_name text,timezone text,status_text text,status_emoji text,avatar_url text)''')
 q('''create table if not exists tokens(token text primary key,user_id text)''')
 q('''create table if not exists workspaces(id text primary key,slug text unique,name text,owner_id text,join_mode text)''')
 q('''create table if not exists members(workspace_id text,user_id text,role text,primary key(workspace_id,user_id))''')
 q('''create table if not exists channels(id text primary key,workspace_id text,name text,is_private int,is_dm int,topic text,is_archived int)''')
 q('''create table if not exists cm(channel_id text,user_id text,primary key(channel_id,user_id))''')
 q('''create table if not exists messages(id text primary key,channel_id text,author_id text,body text,parent_id text,created_at text,edited_at text,deleted int default 0)''')
 q('''create table if not exists reactions(message_id text,user_id text,emoji text,primary key(message_id,user_id,emoji))''')
 q('''create table if not exists files(id text primary key,uploader_id text,filename text,content_type text,size int,created_at text,data blob,attached int default 0)''')
 q('''create table if not exists dms(a text,b text,channel_id text,unique(a,b))''')
 q('''create table if not exists groups_(id text primary key,workspace_id text,handle text unique,name text,creator_id text)''')
 q('''create table if not exists group_users(group_id text,user_id text,primary key(group_id,user_id))''')
 q('''create table if not exists events(id integer primary key autoincrement,channel_id text,type text,payload text)''')
init()
clients=[]; clients_lock=threading.Lock()
def userobj(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def channel(r):
 return {k:r[k] for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def auth(h):
 x=h.get('Authorization','')
 if not x.startswith('Bearer '): return None
 r=q('select u.* from tokens t join users u on u.id=t.user_id where t.token=?',(x[7:],),True); return r
def msgobj(r):
 if not r:return None
 reactions=[]
 for x in q('select emoji,count(*) n from reactions where message_id=? group by emoji',(r['id'],)):
  ids=[z['user_id'] for z in q('select user_id from reactions where message_id=? and emoji=?',(r['id'],x['emoji']))]
  reactions.append({'emoji':x['emoji'],'count':x['n'],'user_ids':ids})
 mentions=[]
 for name in re.findall(r'(?<!\w)@([A-Za-z0-9_-]+)',r['body']):
  if name=='here':
   online=[]
   with clients_lock: online=[getattr(c,'user_id',None) for c in clients]
   for z in q('select user_id from cm where channel_id=?',(r['channel_id'],)):
    if z['user_id'] in online and z['user_id']!=r['author_id'] and z['user_id'] not in mentions: mentions.append(z['user_id'])
   continue
  if name=='channel':
   for z in q('select user_id from cm where channel_id=?',(r['channel_id'],)):
    if z['user_id']!=r['author_id'] and z['user_id'] not in mentions: mentions.append(z['user_id'])
   continue
  u=q('select id from users where username=?',(name,),True)
  if u and u['id']!=r['author_id'] and u['id'] not in mentions: mentions.append(u['id'])
 files=[]
 for f in q('select * from files where id in (select json_each.value from messages, json_each(messages.body) where 1=0)'): pass
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(q('select * from users where id=?',(r['author_id'],),True)),'body':r['body'],'parent_id':r['parent_id'],'reply_count':q('select count(*) n from messages where parent_id=? and deleted=0',(r['id'],),True)['n'],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reactions,'mentions':mentions}
def broadcast(obj):
 with clients_lock:
  for c in list(clients):
   try:c.send(obj)
   except: clients.remove(c)
def event(cid,typ,payload):
 with lock:
  cur=con.execute('insert into events(channel_id,type,payload) values(?,?,?)',(cid,typ,json.dumps(payload))); con.commit(); eid=cur.lastrowid
 obj={'type':typ,'event_id':eid,'channel_id':cid,**payload}; broadcast(obj); return obj
class WS:
 def __init__(self,h): self.h=h; self.sock=h.request; self.lock=threading.Lock()
 def send(self,o):
  b=json.dumps(o).encode(); n=len(b); head=bytes([129])+ (bytes([n]) if n<126 else bytes([126])+n.to_bytes(2,'big')); self.sock.sendall(head+b)
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def sendj(self,code,obj,headers={}):
  b=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); 
  for k,v in headers.items():self.send_header(k,v)
  self.end_headers(); self.wfile.write(b)
 def read(self):
  n=int(self.headers.get('Content-Length','0')); return self.rfile.read(n)
 def body(self):
  try:return json.loads(self.read() or b'{}')
  except:return {}
 def do_GET(self):
  p=urllib.parse.urlparse(self.path); path=p.path
  if path=='/api/health': return self.sendj(200,{'status':'ok','node_id':int(self.server.server_port)-8000})
  if path=='/': return self.html()
  if path=='/api/ws': return self.websocket()
  u=auth(self.headers)
  if path.startswith('/api/') and not u:return self.sendj(401,{'error':'unauthorized'})
  parts=path[5:].strip('/').split('/')
  if parts[0]=='auth' and parts[1]=='me':return self.sendj(200,{'user':userobj(u)})
  if parts[0]=='users':
   r=q('select * from users where id=?',(parts[1],),True); return self.sendj(200,{'user':userobj(r)}) if r else self.sendj(404,{})
  if parts[0]=='workspaces':
   if len(parts)==1:return self.sendj(200,{'workspaces':[dict(x) for x in q('select * from workspaces where id in (select workspace_id from members where user_id=?)',(u['id'],))]})
   w=q('select * from workspaces where slug=?',(parts[1],),True)
   if len(parts)>2 and parts[2]=='groups': return self.groups(w,u)
   if len(parts)>2 and parts[2]=='members': return self.sendj(200,{'members':[dict(x) for x in q('select * from members where workspace_id=?',(w['id'],))]})
   cs=[channel(x) for x in q('select * from channels where workspace_id=?',(w['id'],))]; return self.sendj(200,{'workspace':dict(w),'channels':cs,'read_state':{}})
  if parts[0]=='channels':
   cid=parts[1]
   if len(parts)>2 and parts[2]=='messages':
    rs=q('select * from messages where channel_id=? and deleted=0 and parent_id is null order by rowid desc',(cid,));return self.sendj(200,{'messages':[msgobj(x) for x in rs],'next_cursor':None})
   if len(parts)>2 and parts[2]=='members': return self.sendj(200,{'members':[userobj(q('select * from users where id=?',(x['user_id'],),True)) for x in q('select * from cm where channel_id=?',(cid,))]})
  if parts[0]=='messages' and len(parts)>2 and parts[2]=='replies':
   rs=q('select * from messages where parent_id=? and deleted=0 order by rowid',(parts[1],));return self.sendj(200,{'replies':[msgobj(x) for x in rs],'next_cursor':None})
  if parts[0]=='files':
   f=q('select * from files where id=?',(parts[1],),True)
   if not f:return self.sendj(404,{})
   if len(parts)>2:
    self.send_response(200);self.send_header('Content-Type',f['content_type']);self.send_header('Content-Disposition',f'attachment; filename="{f["filename"]}"');self.send_header('Content-Length',str(f['size']));self.end_headers();self.wfile.write(f['data']);return
   return self.sendj(200,{'file':{k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}})
  return self.sendj(404,{})
 def do_POST(self):
  path=urllib.parse.urlparse(self.path).path; u=auth(self.headers)
  if path=='/api/auth/register':
   b=self.body()
   if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return self.sendj(400,{})
   if q('select id from users where username=?',(b['username'],),True):return self.sendj(409,{})
   i=uid();q('insert into users values(?,?,?,?,?,?,?,?)',(i,b['username'],b['password'],b.get('display_name') or b['username'],'UTC','','',None));t=uuid.uuid4().hex+uuid.uuid4().hex;q('insert into tokens values(?,?)',(t,i));return self.sendj(201,{'user':userobj(q('select * from users where id=?',(i,),True)),'token':t})
  if path=='/api/auth/login':
   b=self.body();r=q('select * from users where username=? and password=?',(b.get('username'),b.get('password')),True)
   if not r:return self.sendj(401,{})
   t=uuid.uuid4().hex+uuid.uuid4().hex;q('insert into tokens values(?,?)',(t,r['id']));return self.sendj(200,{'user':userobj(r),'token':t})
  if not u:return self.sendj(401,{})
  ps=path[5:].strip('/').split('/')
  b={} if ps[0]=='files' else self.body()
  if ps[0]=='workspaces' and len(ps)==1:
   i=uid();q('insert into workspaces values(?,?,?,?,?)',(i,b['slug'],b['name'],u['id'],'open'));q('insert into members values(?,?,?)',(i,u['id'],'owner'));c=uid();q('insert into channels values(?,?,?,?,?,?,?)',(c,i,'general',0,0,'',0));q('insert into cm values(?,?)',(c,u['id']));return self.sendj(201,{'workspace':dict(q('select * from workspaces where id=?',(i,),True)),'general_channel':channel(q('select * from channels where id=?',(c,),True))})
  if ps[0]=='channels' and len(ps)>2 and ps[2]=='join':
   c=q('select * from channels where id=?',(ps[1],),True);q('insert or ignore into cm values(?,?)',(ps[1],u['id']));q('insert or ignore into members select workspace_id,?,\'member\' from channels where id=?',(u['id'],ps[1]));return self.sendj(200,{'channel':channel(c)})
  if ps[0]=='workspaces' and len(ps)>2 and ps[2]=='channels':
   w=q('select * from workspaces where slug=?',(ps[1],),True); c=uid();q('insert into channels values(?,?,?,?,?,?,?)',(c,w['id'],b['name'],int(b.get('is_private',False)),0,b.get('topic',''),0));q('insert into cm values(?,?)',(c,u['id']));return self.sendj(201,{'channel':channel(q('select * from channels where id=?',(c,),True))})
  if ps[0]=='channels' and len(ps)>2 and ps[2]=='messages':
   c=q('select * from channels where id=?',(ps[1],),True); text=b.get('body','')
   if not text:return self.sendj(400,{})
   if text.startswith('/') and not text.startswith('//'):
    if text=='/me' or text.startswith('/fixture-') :return self.sendj(400,{})
    if text.startswith('/me '): pass
    elif text.startswith('/shrug'): text=(text[6:].strip()+' ' if text[6:].strip() else '')+'¯\\_(ツ)_/¯'
    elif text.startswith('/topic '): q('update channels set topic=? where id=?',(text[7:],c['id']));return self.sendj(201,{'message':None,'channel':channel(q('select * from channels where id=?',(c['id'],),True))})
    elif text in ('/archive','/unarchive'):q('update channels set is_archived=? where id=?',(text=='/archive',c['id']));return self.sendj(201,{'message':None,'channel':channel(q('select * from channels where id=?',(c['id'],),True))})
    elif text.startswith('/invite @'):
     x=q('select * from users where username=?',(text[9:].strip(),),True);return self.sendj(201,{'message':None,'invited_user':userobj(x)})
    else:return self.sendj(400,{})
   if text.startswith('//'):text=text[1:]
   ids=b.get('file_ids',[]); fs=[]
   for fid in ids:
    f=q('select * from files where id=?',(fid,),True)
    if not f or f['uploader_id']!=u['id'] or f['attached']:return self.sendj(400,{})
    q('update files set attached=1 where id=?',(fid,));fs.append({k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')})
   i=uid();q('insert into messages(id,channel_id,author_id,body,parent_id,created_at) values(?,?,?,?,?,?)',(i,c['id'],u['id'],text,b.get('parent_id'),now()));r=q('select * from messages where id=?',(i,),True); o=msgobj(r);o['files']=fs;event(c['id'],'message.reply' if b.get('parent_id') else 'message.created',{'message':o});return self.sendj(201,{'message':o})
  if ps[0]=='messages' and len(ps)>2 and ps[2]=='reactions':
   mid=ps[1];e=b.get('emoji');q('insert or ignore into reactions values(?,?,?)',(mid,u['id'],e));return self.sendj(200,{'reactions':msgobj(q('select * from messages where id=?',(mid,),True))['reactions']})
  if ps[0]=='dms':
   a,bid=sorted([u['id'],b.get('user_id')]);r=q('select channel_id from dms where a=? and b=?',(a,bid),True)
   if not r:q('insert into dms values(?,?,?)',(a,bid,uid()));r=q('select channel_id from dms where a=? and b=?',(a,bid),True)
   return self.sendj(200,{'channel_id':r['channel_id']})
  if ps[0]=='files':
   raw=self.read()
   ct=self.headers.get('Content-Type','')
   boundary=ct.split('boundary=',1)[-1].strip().strip('"').encode()
   start=raw.find(b'filename=')
   sep=raw.find(b'\r\n\r\n',start)
   end=raw.find(b'\r\n--'+boundary,sep)
   if start<0 or sep<0 or end<0:return self.sendj(400,{})
   head=raw[raw.rfind(b'\r\n',0,start)+2:sep]
   data=raw[sep+4:end]
   m=re.search(br'filename="([^"]+)',head)
   fn=m.group(1).decode() if m else 'file'
   typ=re.search(br'Content-Type:\s*([^\r\n]+)',head,re.I)
   typ=typ.group(1).decode() if typ else 'application/octet-stream'
   if len(data)>10*1024*1024:return self.sendj(413,{})
   i=uid();q('insert into files values(?,?,?,?,?,?,?,?)',(i,u['id'],fn,typ,len(data),now(),data,0));f=q('select * from files where id=?',(i,),True);return self.sendj(201,{'file':{k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}})
  if ps[0]=='workspaces' and len(ps)>2 and ps[2]=='groups': return self.group_create(q('select * from workspaces where slug=?',(ps[1],),True),u,b)
  return self.sendj(404,{})
 def do_DELETE(self):
  u=auth(self.headers);p=urllib.parse.urlparse(self.path).path
  if not u:return self.sendj(401,{})
  x=p[5:].strip('/').split('/')
  if x[0]=='messages' and len(x)>2 and x[2]=='reactions':q('delete from reactions where message_id=? and user_id=? and emoji=?',(x[1],u['id'],urllib.parse.unquote(x[3])));return self.sendj(200,{'reactions':msgobj(q('select * from messages where id=?',(x[1],),True))['reactions']})
  if x[0]=='workspaces' and len(x)>3 and x[2]=='groups':
   w=q('select * from workspaces where slug=?',(x[1],),True); g=q('select * from groups_ where workspace_id=? and handle=?',(w['id'],x[3]),True)
   if g and g['creator_id']!=u['id'] and w['owner_id']!=u['id']: return self.sendj(403,{})
   if g:q('delete from groups_ where id=?',(g['id'],))
   return self.sendj(200,{'deleted':True})
  return self.sendj(404,{})
 def do_PATCH(self):
  u=auth(self.headers);p=urllib.parse.urlparse(self.path).path
  if not u:return self.sendj(401,{})
  b=self.body();x=p[5:].strip('/').split('/')
  if x[0]=='users' and x[1]=='me':
   if 'timezone' in b and b['timezone']!='UTC':return self.sendj(400,{})
   for k in ('display_name','timezone','status_text','status_emoji','avatar_url'):
    if k in b:q('update users set '+k+'=? where id=?',(b[k],u['id']))
   o=userobj(q('select * from users where id=?',(u['id'],),True));broadcast({'type':'user.updated','user':o});return self.sendj(200,{'user':o})
  if x[0]=='workspaces' and len(x)>3 and x[2]=='groups':
   w=q('select * from workspaces where slug=?',(x[1],),True); g=q('select * from groups_ where workspace_id=? and handle=?',(w['id'],x[3]),True)
   if not g:return self.sendj(404,{})
   if g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return self.sendj(403,{})
   q('update groups_ set name=? where id=?',(b.get('name',g['name']),g['id']));return self.sendj(200,{'group':self.gobj(g['id'])})
  return self.sendj(404,{})
 def groups(self,w,u):return self.sendj(200,{'groups':[self.gobj(x['id']) for x in q('select * from groups_ where workspace_id=?',(w['id'],))]})
 def gobj(self,i):
  g=q('select * from groups_ where id=?',(i,),True);return {'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':[x['user_id'] for x in q('select user_id from group_users where group_id=?',(i,))]}
 def group_create(self,w,u,b):
  i=uid();q('insert into groups_ values(?,?,?,?,?)',(i,w['id'],b['handle'],b['name'],u['id']))
  for x in b.get('user_ids',[]):q('insert or ignore into group_users values(?,?)',(i,x))
  return self.sendj(201,{'group':self.gobj(i)})
 def html(self):
  b=b'''<!doctype html><html><body><h1>Huddle</h1></body></html>''';self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def websocket(self):
  t=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('token',[''])[0]
  if not q('select * from tokens where token=?',(t,),True):self.send_response(401);self.end_headers();return
  key=self.headers.get('Sec-WebSocket-Key');accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101,'Switching Protocols');self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  w=WS(self); w.user_id=q('select user_id from tokens where token=?',(t,),True)['user_id']; clients.append(w)
  try:
   while True:
    h=self.rfile.read(2)
    if not h:break
    n=h[1]&127
    if n==126:n=int.from_bytes(self.rfile.read(2),'big')
    mask=self.rfile.read(4); data=self.rfile.read(n); data=bytes(v^mask[i%4] for i,v in enumerate(data)); o=json.loads(data)
    if o.get('type')=='subscribe':w.send({'type':'subscribed','channel_id':o['channel_id'],'head_event_id':q('select max(id) x from events where channel_id=?',(o['channel_id'],),True)['x'] or 0})
  except:pass
  finally:
   if w in clients:clients.remove(w)
def run(port):ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
if __name__=='__main__':
 for p in (8000,8001,8002):threading.Thread(target=run,args=(p,),daemon=True).start()
 while True:time.sleep(3600)
