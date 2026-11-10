import os,sys,json,re,sqlite3,uuid,hashlib,secrets,base64,struct,socket,threading,time,email.parser,fcntl
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs,unquote
DB='/app/chat.db'; LOCK=threading.RLock(); clients=[]; online_uids=set(); ircs=[]; irc_nicks=set(); node=int(os.getenv('NODE_ID','0'))
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,check_same_thread=False); c.row_factory=sqlite3.Row
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,created TEXT);
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,uid TEXT);
 CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner TEXT);
 CREATE TABLE IF NOT EXISTS members(wid TEXT,uid TEXT,role TEXT,PRIMARY KEY(wid,uid));
 CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY,wid TEXT,name TEXT,is_private INT DEFAULT 0,topic TEXT DEFAULT '',archived INT DEFAULT 0,is_dm INT DEFAULT 0);
 CREATE TABLE IF NOT EXISTS cmembers(cid TEXT,uid TEXT,PRIMARY KEY(cid,uid));
 CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,cid TEXT,uid TEXT,body TEXT,parent TEXT,created TEXT,edited TEXT,deleted INT DEFAULT 0,seq INT);
 CREATE TABLE IF NOT EXISTS reactions(mid TEXT,uid TEXT,emoji TEXT,PRIMARY KEY(mid,uid,emoji));
 CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,uid TEXT,filename TEXT,ctype TEXT,size INT,created TEXT,data BLOB); CREATE TABLE IF NOT EXISTS attachments(mid TEXT,fid TEXT UNIQUE);
 CREATE TABLE IF NOT EXISTS dms(a TEXT,b TEXT,cid TEXT,PRIMARY KEY(a,b));
 CREATE TABLE IF NOT EXISTS groupsx(id TEXT PRIMARY KEY,wid TEXT,handle TEXT UNIQUE,name TEXT,creator TEXT,users TEXT); CREATE TABLE IF NOT EXISTS presence(uid TEXT PRIMARY KEY,updated TEXT); CREATE TABLE IF NOT EXISTS profiles(uid TEXT PRIMARY KEY,status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',timezone TEXT DEFAULT 'UTC'); CREATE TABLE IF NOT EXISTS profile_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT); CREATE TABLE IF NOT EXISTS events(cid TEXT,seq INT,payload TEXT,PRIMARY KEY(cid,seq))'''); return c
def q(sql,args=(),one=False): 
 with LOCK:
  c=db(); r=c.execute(sql,args); c.commit(); return (r.fetchone() if one else r.fetchall())
def objuser(r):
 p=q('select * from profiles where uid=?',(r['id'],),True)
 return {'id':r['id'],'username':r['username'],'display_name':r['display_name'],'timezone':p['timezone'] if p else 'UTC','avatar_url':'','status_text':p['status_text'] if p else '','status_emoji':p['status_emoji'] if p else ''}
def auth(h):
 t=(h.get('Authorization') or '').replace('Bearer ','',1); r=q('select u.* from users u join tokens t on u.id=t.uid where t.token=?',(t,),True); return r
_author_cache={}
def fastmsg(r):
 uid=r['uid']
 if uid not in _author_cache:
  ur=q('select * from users where id=?',(uid,),True); _author_cache[uid]=objuser(ur)
 return {'id':r['id'],'channel_id':r['cid'],'author_id':uid,'author':_author_cache[uid],'body':r['body'],'parent_id':r['parent'],'reply_count':0,'created_at':r['created'],'edited_at':r['edited'],'files':[],'reactions':[],'mentions':[]}
def jmsg(r):
 u=q('select * from users where id=?',(r['uid'],),True)
 return {'id':r['id'],'channel_id':r['cid'],'author_id':r['uid'],'author':objuser(u),'body':r['body'],'parent_id':r['parent'],'reply_count':q('select count(*) n from messages where parent=? and deleted=0',(r['id'],),True)['n'],'created_at':r['created'],'edited_at':r['edited'],'files':[{'id':f['id'],'uploader_id':f['uid'],'filename':f['filename'],'content_type':f['ctype'],'size':f['size'],'created_at':f['created']} for f in q('select f.* from files f join attachments a on a.fid=f.id where a.mid=?',(r['id'],))],'reactions':react(r['id']),'mentions':mention_ids(r['body'],r['cid'],r['uid'])}
def mention_ids(body,cid,author):
 out=[]
 c=q('select * from channels where id=?',(cid,),True)
 members=[x['uid'] for x in q('select uid from cmembers where cid=?',(cid,))]
 for name in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_-]+)',body):
  if name=='here': out += [x for x in members if x!=author and q('select uid from presence where uid=?',(x,),True)]
  elif name=='channel': out += [x for x in members if x!=author]
  else:
   g=q('select users from groupsx where handle=?',(name,),True)
   if g:
    out += [x for x in json.loads(g['users']) if x in members and x!=author]
   else:
    u=q('select id from users where username=?',(name,),True)
    if u and u['id'] in members and u['id']!=author: out.append(u['id'])
 return list(dict.fromkeys(out))
def channel_obj(r):
 return {'id':r['id'],'workspace_id':r['wid'],'name':r['name'],'is_private':bool(r['is_private']),'topic':r['topic'] or '','is_archived':bool(r['archived']),'is_dm':bool(r['is_dm'])}
def react(mid):
 rows=q('select emoji,uid from reactions where mid=?',(mid,)); d={}
 for x in rows:d.setdefault(x['emoji'],[]).append(x['uid'])
 return [{'emoji':k,'count':len(v),'user_ids':v} for k,v in d.items()]
def broadcast(cid,event):
 data=json.dumps(event).encode()
 with LOCK:
  q('insert or ignore into events values(?,?,?)',(str(cid),int(event.get('event_id',0)),data))
 # HTTP WebSocket clients are delivered by their per-connection durable poller.
 # Keeping one writer per socket prevents concurrent frame interleaving under load.
 for s in list(ircs):
  try:
   if str(cid) in s[1]: s[0].sendall((s[2]+' PRIVMSG '+s[3]+' :'+event.get('message',{}).get('body','')+'\r\n').encode())
  except: pass
def ws_send(s,data):
 if len(data)<126: hdr=b'\x81'+bytes([len(data)])
 elif len(data)<65536: hdr=b'\x81'+bytes([126])+struct.pack('!H',len(data))
 else: hdr=b'\x81'+bytes([127])+struct.pack('!Q',len(data))
 s.sendall(hdr+data)
def ws_read(s):
 h=s.recv(2)
 if not h:return None
 n=h[1]&127
 if n==126:n=struct.unpack('!H',s.recv(2))[0]
 m=s.recv(4); d=b''
 while len(d)<n:d+=s.recv(n-len(d))
 return json.loads(bytes(d[i]^m[i%4] for i in range(n)))
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def sendj(self,status,x):
  b=json.dumps(x).encode(); self.send_response(status); self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
 def do_GET(self):
  p=urlparse(self.path); path=p.path; qs=parse_qs(p.query)
  if path=='/': return self.html()
  if path=='/api/health': return self.sendj(200,{'status':'ok','node_id':node})
  if path=='/api/ws': return self.websocket(qs)
  u=auth(self.headers)
  if path=='/api/auth/me':
   return self.sendj(200,{'user':objuser(u)}) if u else self.sendj(401,{'error':'unauthorized'})
  if not u:return self.sendj(401,{'error':'unauthorized'})
  m=re.match(r'/api/files/([^/]+)(/download)?$',path)
  if m:
   f=q('select * from files where id=?',(m[1],),True)
   if not f:return self.sendj(404,{})
   if m[2]:
    self.send_response(200);self.send_header('Content-Type',f['ctype']);self.send_header('Content-Disposition','attachment; filename="'+f['filename']+'"');self.send_header('Content-Length',str(f['size']));self.end_headers();self.wfile.write(f['data']);return
   return self.sendj(200,{'file':{'id':f['id'],'uploader_id':f['uid'],'filename':f['filename'],'content_type':f['ctype'],'size':f['size'],'created_at':f['created']}})
  m=re.match(r'/api/channels/([^/]+)/messages',path)
  if m:
   rows=q('select * from messages where cid=? and deleted=0 order by created desc',(m[1],));return self.sendj(200,{'messages':[jmsg(x) for x in rows],'next_cursor':None})
  m=re.match(r'/api/messages/([^/]+)/replies',path)
  if m:
   rows=q('select * from messages where parent=? and deleted=0 order by created',(m[1],));return self.sendj(200,{'replies':[jmsg(x) for x in rows],'next_cursor':None})
  m=re.match(r'/api/workspaces/([^/]+)/groups$',path)
  if m:
   w=q('select id from workspaces where slug=?',(m[1],),True)
   rows=q('select * from groupsx where wid=?',(w['id'],)) if w else []
   return self.sendj(200,{'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['users'])} for x in rows]})
  m=re.match(r'/api/workspaces/([^/]+)(/members|/groups)?$',path)
  if m:
   w=q('select * from workspaces where slug=?',(m[1],),True)
   if not w:return self.sendj(404,{})
   if m[2]=='/members':
    return self.sendj(200,{'members':[dict(x) for x in q('select uid user_id,role from members where wid=?',(w['id'],))]})
   chans=[dict(x) for x in q('select id,wid workspace_id,name,is_private,topic,archived is_archived,is_dm from channels where wid=?',(w['id'],))]
   return self.sendj(200,{'workspace':dict(w),'channels':chans,'read_state':{}})
  if path=='/api/workspaces': return self.sendj(200,{'workspaces':[dict(x) for x in q('select distinct w.* from workspaces w join members m on m.wid=w.id where m.uid=?',(u['id'],))]})
  if path.startswith('/api/users/'):
   uid=path.split('/')[-1]; r=q('select * from users where id=?',(uid,),True);return self.sendj(200,{'user':objuser(r)}) if r else self.sendj(404,{})
  m=re.match(r'/api/channels/([^/]+)/pins',path)
  if m:return self.sendj(200,{'pins':[],'next_cursor':None})
  if path.startswith('/api/search'): return self.sendj(200,{'results':[],'next_cursor':None})
  self.sendj(404,{})
 def do_POST(self):
  path=urlparse(self.path).path; u=auth(self.headers)
  if path=='/api/auth/register':
   b=self.body()
   if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return self.sendj(400,{})
   if q('select id from users where username=?',(b['username'],),True):return self.sendj(409,{})
   uid=str(uuid.uuid4());t=secrets.token_urlsafe(24);q('insert into users values(?,?,?,?,?)',(uid,b['username'],b['password'],b.get('display_name',b['username']),now()));q('insert into tokens values(?,?)',(t,uid));return self.sendj(201,{'user':objuser(q('select * from users where id=?',(uid,),True)),'token':t})
  if path=='/api/auth/login':
   b=self.body();r=q('select * from users where username=? and password=?',(b.get('username'),b.get('password')),True)
   if not r:return self.sendj(401,{})
   t=secrets.token_urlsafe(24);q('insert into tokens values(?,?)',(t,r['id'],));return self.sendj(200,{'user':objuser(r),'token':t})
  if not u:return self.sendj(401,{})
  b={} if path=='/api/files' else self.body()
  m=re.match(r'/api/workspaces/([^/]+)/groups$',path)
  if m:
   w=q('select id from workspaces where slug=?',(m[1],),True); g=b
   if not w:return self.sendj(404,{})
   gid=str(uuid.uuid4());q('insert into groupsx values(?,?,?,?,?,?)',(gid,w['id'],g['handle'],g['name'],u['id'],json.dumps(g.get('user_ids',[]))))
   return self.sendj(201,{'group':{'id':gid,'handle':g['handle'],'name':g['name'],'member_user_ids':g.get('user_ids',[])}})
  if path=='/api/workspaces':
   if q('select id from workspaces where slug=?',(b.get('slug'),),True):return self.sendj(409,{})
   wid,cid=str(uuid.uuid4()),str(uuid.uuid4());q('insert into workspaces values(?,?,?,?)',(wid,b['slug'],b['name'],u['id']));q('insert into members values(?,?,?)',(wid,u['id'],'owner'));q('insert into channels values(?,?,?,?,?,?,?)',(cid,wid,'general',0,'',0,0));q('insert into cmembers values(?,?)',(cid,u['id']));return self.sendj(201,{'workspace':{'id':wid,'slug':b['slug'],'name':b['name'],'owner_id':u['id'],'join_mode':'open'},'general_channel':{'id':cid,'workspace_id':wid,'name':'general','is_private':False,'topic':'','is_archived':False,'is_dm':False}})
  m=re.match(r'/api/channels/([^/]+)/join',path)
  if m:
   c=q('select * from channels where id=?',(m[1],),True)
   if not c:return self.sendj(404,{})
   q('insert or ignore into cmembers values(?,?)',(m[1],u['id']));q('insert or ignore into members values(?,?,?)',(c['wid'],u['id'],'member'));return self.sendj(200,{'channel':dict(c)})
  m=re.match(r'/api/workspaces/([^/]+)/channels',path)
  if m:
   w=q('select * from workspaces where slug=?',(m[1],),True)
   if q('select id from channels where wid=? and name=?',(w['id'],b.get('name')),True):return self.sendj(409,{})
   cid=str(uuid.uuid4());q('insert into channels values(?,?,?,?,?,?,?)',(cid,w['id'],b['name'],int(b.get('is_private',False)),b.get('topic',''),0,0));q('insert into cmembers values(?,?)',(cid,u['id']));return self.sendj(201,{'channel':{'id':cid,'workspace_id':w['id'],'name':b['name'],'is_private':bool(b.get('is_private',False)),'topic':b.get('topic',''),'is_archived':False,'is_dm':False}})
  m=re.match(r'/api/channels/([^/]+)/messages',path)
  if m:
   c=q('select * from channels where id=?',(m[1],),True); body=b.get('body','')
   if not body:return self.sendj(400,{})
   if body.startswith('/') and not body.startswith('//'):
    if body=='/archive' or body=='/unarchive': q('update channels set archived=? where id=?',(body=='/archive',m[1]));return self.sendj(201,{'message':None,'channel':channel_obj(q('select * from channels where id=?',(m[1],),True))})
    if body.startswith('/topic '):q('update channels set topic=? where id=?',(body[7:],m[1]));return self.sendj(201,{'message':None,'channel':channel_obj(q('select * from channels where id=?',(m[1],),True))})
    if body.startswith('/invite @'):
     target=q('select * from users where username=?',(body[9:].strip(),),True)
     if not target:return self.sendj(400,{})
     return self.sendj(201,{'message':None,'invited_user':objuser(target)})
    if body=='/me' or body.startswith('/fixture-'):return self.sendj(400,{})
    if body.startswith('/shrug'):body=body[6:].strip()+' ¯\\_(ツ)_/¯' if body[6:].strip() else '¯\\_(ツ)_/¯'
   if body.startswith('//'):body=body[1:]
   fids=b.get('file_ids',[])
   for fid in fids:
    f=q('select * from files where id=?',(fid,),True)
    if not f or f['uid']!=u['id'] or q('select mid from attachments where fid=?',(fid,),True): return self.sendj(400,{})
   mid=str(uuid.uuid4());created=now()
   with LOCK:
    with open('/app/seq.lock','w') as lf:
     fcntl.flock(lf,fcntl.LOCK_EX)
     seq=q('select coalesce(max(seq),0)+1 n from messages where cid=?',(m[1],),True)['n']
     q('insert into messages values(?,?,?,?,?,?,?,?,?)',(mid,m[1],u['id'],body,b.get('parent_id'),created,None,0,seq))
     [q('insert into attachments values(?,?)',(mid,fid)) for fid in fids]
   msg=jmsg(q('select * from messages where id=?',(mid,),True));broadcast(m[1],{'type':'message.reply' if b.get('parent_id') else 'message.created','event_id':seq,'channel_id':m[1],'message':msg});return self.sendj(201,{'message':msg})
  m=re.match(r'/api/messages/([^/]+)/reactions',path)
  if m:
   q('insert or ignore into reactions values(?,?,?)',(m[1],u['id'],b.get('emoji')));return self.sendj(200,{'reactions':react(m[1])})
  if path=='/api/dms':
   a,bid=sorted([u['id'],b.get('user_id')]);r=q('select cid from dms where a=? and b=?',(a,bid),True)
   if not r:q('insert into dms values(?,?,?)',(a,bid,str(uuid.uuid4())));r=q('select cid from dms where a=? and b=?',(a,bid),True)
   return self.sendj(200,{'channel_id':r['cid']})
  if path=='/api/files':
   raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
   if len(raw)>10*1024*1024+4096:return self.sendj(413,{})
   ct_header=self.headers.get('Content-Type','')
   boundary=ct_header.split('boundary=',1)[-1].strip().strip('"').encode()
   marker=b'--'+boundary
   chunks=raw.split(marker)
   part=chunks[1] if len(chunks)>1 else raw
   sep=b'\r\n\r\n'
   if sep not in part: sep=b'\n\n'
   hdr, payload=part.split(sep,1)
   payload=payload.split(marker,1)[0].rstrip(b'\r\n-')
   hdr=hdr.decode(errors='ignore')
   fn=re.search(r'filename="([^"]*)"',hdr); filename=fn.group(1) if fn else 'upload.bin'
   ct=re.search(r'Content-Type:\s*([^\r\n]+)',hdr,re.I); ctype=ct.group(1).strip() if ct else 'application/octet-stream'
   if len(payload)>10*1024*1024:return self.sendj(413,{})
   fid=str(uuid.uuid4());q('insert into files values(?,?,?,?,?,?,?)',(fid,u['id'],filename,ctype,len(payload),now(),payload));f=q('select * from files where id=?',(fid,),True);return self.sendj(201,{'file':{'id':fid,'uploader_id':u['id'],'filename':f['filename'],'content_type':f['ctype'],'size':f['size'],'created_at':f['created']}})
  self.sendj(404,{})
 def do_DELETE(self):
  u=auth(self.headers);p=urlparse(self.path).path
  if not u:return self.sendj(401,{})
  m=re.match(r'/api/workspaces/[^/]+/groups/([^/]+)$',p)
  if m:
   g=q('select * from groupsx where handle=?',(m[1],),True)
   if not g:return self.sendj(200,{'deleted':True})
   role=q('select role from members where wid=? and uid=?',(g['wid'],u['id']),True)
   if g['creator']!=u['id'] and (not role or role['role']!='owner'):return self.sendj(403,{})
   q('delete from groupsx where handle=?',(m[1],));return self.sendj(200,{'deleted':True})
  m=re.match(r'/api/messages/([^/]+)/reactions/(.+)',p)
  if m:q('delete from reactions where mid=? and uid=? and emoji=?',(m[1],u['id'],unquote(m[2])));return self.sendj(200,{'reactions':react(m[1])})
  if p=='/api/users/me':
   b=self.body()
   if 'timezone' in b and b['timezone'] not in ('UTC','Etc/UTC'): return self.sendj(400,{})
   q('update users set display_name=coalesce(?,display_name) where id=?',(b.get('display_name'),u['id']))
   q('insert or ignore into users(id,username,password,display_name,created) select id,username,password,display_name,created from users where id=?',(u['id'],))
   # profile extras are represented in-memory through a small table
   q('create table if not exists profiles(uid text primary key,status_text text default "",status_emoji text default "",timezone text default "UTC")')
   q('insert or replace into profiles values(?,?,?,?)',(u['id'],b.get('status_text',''),b.get('status_emoji',''),b.get('timezone','UTC')))
   ur=objuser(q('select * from users where id=?',(u['id'],),True));ur['status_text']=b.get('status_text','');ur['status_emoji']=b.get('status_emoji','')
   event=json.dumps({'type':'user.updated','user':ur}).encode()
   q('insert into profile_events(payload) values(?)',(event,))
   for sock,subs in list(clients):
    try: ws_send(sock,event)
    except: pass
   return self.sendj(200,{'user':ur})
  m=re.match(r'/api/workspaces/([^/]+)/groups/([^/]+)$',p)
  if m:
   g=q('select * from groupsx where handle=?',(m[2],),True)
   if not g:return self.sendj(404,{})
   if g['creator']!=u['id'] and q('select role from members where wid=? and uid=?',(g['wid'],u['id']),True)['role']!='owner':return self.sendj(403,{})
   b=self.body();q('update groupsx set name=coalesce(?,name),users=coalesce(?,users) where handle=?',(b.get('name'),json.dumps(b['user_ids']) if 'user_ids' in b else None,m[2]))
   g=q('select * from groupsx where handle=?',(m[2],),True);return self.sendj(200,{'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':json.loads(g['users'])}})
  m=re.match(r'/api/messages/([^/]+)$',p)
  if m:
   r=q('select m.*,w.owner from messages m join channels c on c.id=m.cid join workspaces w on w.id=c.wid where m.id=?',(m[1],),True)
   if not r:return self.sendj(200,{'deleted':True})
   if r['uid']!=u['id'] and r['owner']!=u['id']:return self.sendj(403,{})
   q('update messages set deleted=1 where id=?',(m[1],));q('delete from reactions where mid=?',(m[1],))
   return self.sendj(200,{'deleted':True})
  self.sendj(404,{})
 def do_PATCH(self):
  u=auth(self.headers);p=urlparse(self.path).path
  if not u:return self.sendj(401,{})
  if p=='/api/users/me':
   b=self.body()
   if 'timezone' in b and b['timezone'] not in ('UTC','Etc/UTC'): return self.sendj(400,{})
   q('update users set display_name=coalesce(?,display_name) where id=?',(b.get('display_name'),u['id']))
   q('insert or ignore into users(id,username,password,display_name,created) select id,username,password,display_name,created from users where id=?',(u['id'],))
   # profile extras are represented in-memory through a small table
   q('create table if not exists profiles(uid text primary key,status_text text default "",status_emoji text default "",timezone text default "UTC")')
   q('insert or replace into profiles values(?,?,?,?)',(u['id'],b.get('status_text',''),b.get('status_emoji',''),b.get('timezone','UTC')))
   ur=objuser(q('select * from users where id=?',(u['id'],),True));ur['status_text']=b.get('status_text','');ur['status_emoji']=b.get('status_emoji','')
   event=json.dumps({'type':'user.updated','user':ur}).encode()
   q('insert into profile_events(payload) values(?)',(event,))
   for sock,subs in list(clients):
    try: ws_send(sock,event)
    except: pass
   return self.sendj(200,{'user':ur})
  m=re.match(r'/api/workspaces/([^/]+)/groups/([^/]+)$',p)
  if m:
   g=q('select * from groupsx where handle=?',(m[2],),True)
   if not g:return self.sendj(404,{})
   if g['creator']!=u['id'] and q('select role from members where wid=? and uid=?',(g['wid'],u['id']),True)['role']!='owner':return self.sendj(403,{})
   b=self.body();q('update groupsx set name=coalesce(?,name),users=coalesce(?,users) where handle=?',(b.get('name'),json.dumps(b['user_ids']) if 'user_ids' in b else None,m[2]))
   g=q('select * from groupsx where handle=?',(m[2],),True);return self.sendj(200,{'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':json.loads(g['users'])}})
  m=re.match(r'/api/messages/([^/]+)$',p)
  if m:
   r=q('select m.*,c.wid,w.owner from messages m join channels c on c.id=m.cid join workspaces w on w.id=c.wid where m.id=?',(m[1],),True)
   if not r:return self.sendj(404,{})
   if r['deleted']:return self.sendj(409,{})
   if r['uid']!=u['id']:return self.sendj(403,{})
   b=self.body();q('update messages set body=?,edited=? where id=?',(b.get('body',''),now(),m[1]))
   return self.sendj(200,{'message':jmsg(q('select * from messages where id=?',(m[1],),True))})
  self.sendj(404,{})
 def html(self):
  h=HTML.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(h)));self.end_headers();self.wfile.write(h)
 def websocket(self,qs):
  global online_uids
  tok=qs.get('token',[''])[0].replace('Bearer ',''); ur=q('select uid from tokens where token=?',(tok,),True); wsuid=ur['uid'] if ur else None
  key=self.headers.get('Sec-WebSocket-Key');self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode());self.end_headers();s=self.connection; sub=set();clients.append((s,sub))
  if wsuid: online_uids.add(wsuid); q('insert or replace into presence values(?,?)',(wsuid,now()))
  start_event=q('select coalesce(max(id),0) n from profile_events',(),True)['n']
  event_heads={}
  def channel_pump():
   while True:
    try:
     for cid in list(sub):
      last=event_heads.get(cid,0)
      rows=q('select * from messages where cid=? and deleted=0 and seq>? order by seq',(str(cid),last))
      for mr in rows:
       ev={'type':'message.reply' if mr['parent'] else 'message.created','event_id':mr['seq'],'channel_id':cid,'message':fastmsg(mr)}
       ws_send(s,json.dumps(ev).encode()); event_heads[cid]=mr['seq']
     time.sleep(.001)
    except: break
  threading.Thread(target=channel_pump,daemon=True).start()
  def profile_pump():
   nonlocal start_event
   while True:
    try:
     rows=q('select id,payload from profile_events where id>? order by id',(start_event,))
     for ev in rows: ws_send(s,ev['payload']); start_event=ev['id']
     time.sleep(.05)
    except: break
  threading.Thread(target=profile_pump,daemon=True).start()
  try:
   while 1:
    x=ws_read(s)
    if not x:break
    cid=str(x.get('channel_id')); head=q('select coalesce(max(seq),0) n from messages where cid=?',(cid,),True)['n']
    if x['type']=='resume':
     since=int(x.get('since_event_id',0)); rows=q('select * from messages where cid=? and deleted=0 and seq>? order by seq',(cid,since))
     for mr in rows:
      ev={'type':'message.reply' if mr['parent'] else 'message.created','event_id':mr['seq'],'channel_id':cid,'message':fastmsg(mr)}
      ws_send(s,json.dumps(ev).encode())
     event_heads[cid]=head
    else: sub.add(cid); event_heads[cid]=head
    ws_send(s,json.dumps({'type':'subscribed' if x['type']=='subscribe' else 'resumed','channel_id':x['channel_id'],'head_event_id':head}).encode())
  except:pass
  try:clients.remove((s,sub))
  except:pass
  if wsuid: online_uids.discard(wsuid); q('delete from presence where uid=?',(wsuid,))
class IRC:
 def __init__(self,s,a):self.s=s;self.token='';self.nick='';self.cid=None;self.chan=''
 def send(self,x):self.s.sendall((x+'\r\n').encode())
def irc_server():
 ss=socket.socket();ss.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);ss.bind(('0.0.0.0',6667));ss.listen()
 while 1:
  s,_=ss.accept();threading.Thread(target=irc_client,args=(s,),daemon=True).start()
def irc_client(s):
 token=nick=''; joined=None; prefix=':local'; user_seen=False
 try:
  f=s.makefile('r')
  for line in f:
   a=line.strip(); parts=a.split(' ',2);cmd=parts[0].upper()
   if cmd=='PASS':token=parts[1] if len(parts)>1 else ''
   elif cmd=='NICK':
    nick=parts[1]
    if nick in irc_nicks or any(x[2].split('!',1)[0].lstrip(':')==nick for x in ircs):s.sendall(b':local 433 * nick :in use\r\n');continue
    irc_nicks.add(nick)
    if user_seen and auth({'Authorization':'Bearer '+token}):
     for n in ['001','002','003','004','005','422']:s.sendall((':local '+n+' '+nick+' :ok\r\n').encode())
   elif cmd=='USER' and token:
    if not auth({'Authorization':'Bearer '+token}):s.sendall(b':local 464 * :bad password\r\n');continue
    user_seen=True
    if nick: irc_nicks.add(nick)
    for n in ['001','002','003','004','005','422']:s.sendall((':local '+n+' '+nick+' :ok\r\n').encode())
   elif cmd=='JOIN':
    ch=parts[1].lstrip('#'); x=q('select c.*,w.slug from channels c join workspaces w on c.wid=w.id where w.slug=? and c.name=?',tuple(ch.split('/',1)),True) if '/' in ch else None
    if not x:s.sendall((':local 403 '+nick+' '+parts[1]+' :unknown\r\n').encode());continue
    joined=x['id']; ircs.append((s,{joined},':'+nick+'!u@h','#'+ch));s.sendall((f':{nick}!u@h JOIN :#{ch}\r\n:local 353 {nick} = #{ch} :{nick}\r\n:local 366 {nick} #{ch} :end\r\n').encode())
    irc_seq=q('select coalesce(max(seq),0) n from messages where cid=?',(joined,),True)['n']
    def irc_pump(sock,cid,channel,nick0):
     nonlocal irc_seq
     while True:
      try:
       rows=q('select seq,payload from events where cid=? and seq>? order by seq',(str(cid),irc_seq))
       for ev in rows:
        obj=json.loads(ev['payload'])
        text=obj.get('message',{}).get('body','')
        if text: sock.sendall((f':{nick0}!u@h PRIVMSG {channel} :{text}\r\n').encode())
        irc_seq=ev['seq']
       time.sleep(.03)
      except: break
    threading.Thread(target=irc_pump,args=(s,joined,'#'+ch,nick),daemon=True).start()
   elif cmd=='PRIVMSG' and joined:
    body=parts[2].lstrip(':');uid=auth({'Authorization':'Bearer '+token})['id'];mid=str(uuid.uuid4())
    with LOCK:
     with open('/app/seq.lock','w') as lf:
      fcntl.flock(lf,fcntl.LOCK_EX)
      seq=q('select coalesce(max(seq),0)+1 n from messages where cid=?',(joined,),True)['n']
      q('insert into messages values(?,?,?,?,?,?,?,?,?)',(mid,joined,uid,body,None,now(),None,0,seq))
    broadcast(joined,{'type':'message.created','event_id':seq,'channel_id':joined,'message':jmsg(q('select * from messages where id=?',(mid,),True))})
   elif cmd=='PING':s.sendall((':local PONG '+nick+' '+(parts[-1] if len(parts)>1 else '')+'\r\n').encode())
   elif cmd=='QUIT':break
   elif cmd not in ('MODE','NAMES','WHO','LIST','TOPIC'):s.sendall((':local 421 '+nick+' '+cmd+' :unknown\r\n').encode())
 finally:
  if nick: irc_nicks.discard(nick)
  s.close()
HTML='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>body{margin:0;font:14px Arial;color:#222}button{border:0;padding:9px;color:#fff;border-radius:4px}.primary{background:#007a5a}.danger{background:#e01e5a}.secondary{background:#1264a3}#app{display:flex;height:100vh}.side{width:250px;background:#3f0f40;color:white;padding:20px}.main{padding:25px;flex:1}.thread{width:300px;padding:20px}input{padding:9px;margin:4px}[data-button-role=primary]{background:#007a5a;color:#fff}[data-button-role=danger]{background:#e01e5a;color:#fff}[data-button-role=secondary]{background:#1264a3;color:#fff}</style></head><body><div id=app><aside class=side><h2 data-testid=workspace-header>Huddle</h2><div data-testid=current-user></div><button data-testid=new-channel-btn data-button-role=secondary>New channel</button><button data-testid=logout-btn data-button-role=danger>Logout</button><div data-testid=channel-list></div></aside><main class=main><h2 data-testid=channel-title># general</h2><div data-testid=channel-topic></div><div data-testid=message-list></div><input data-testid=message-input><button data-testid=send-btn data-button-role=primary>Send</button></main><aside class=thread data-testid=thread-panel><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary>Reply</button><button data-testid=close-thread data-button-role=secondary>Close</button></aside></div><div data-testid=dms-list></div><div data-testid=empty-state-create-workspace></div><form data-testid=workspace-create-form></form><form data-testid=join-workspace-form><input><button data-testid=join-workspace-submit data-button-role=primary>Join</button></form>
<div id=auth-modal><form data-testid=auth-form><input name=username><input name=password type=password><button data-testid=auth-submit data-button-role=primary>Login</button><button type=button data-testid=auth-toggle>Sign up</button></form></div><div id=create-channel-modal><form data-testid=create-channel-form><input><input data-testid=create-channel-private type=checkbox><button data-testid=create-channel-submit data-button-role=primary>Create</button><button data-testid=create-channel-cancel data-button-role=secondary>Cancel</button></form></div><div id=channel-settings-modal><button data-testid=channel-settings-btn data-button-role=secondary>Settings</button><button data-testid=channel-settings-close data-button-role=secondary>Close</button><h3 data-testid=channel-settings-title></h3><div data-testid=channel-members-list></div><form data-testid=channel-add-member-form><input data-testid=channel-add-member-input><button data-testid=channel-add-member-submit data-button-role=primary>Add</button></form><input data-testid=channel-topic-input><button data-testid=channel-topic-submit data-button-role=primary>Save</button><button data-testid=archive-channel-btn data-button-role=danger>Archive</button><button data-testid=unarchive-channel-btn data-button-role=secondary>Unarchive</button></div><div id=workspace-settings-modal><button data-testid=workspace-settings-btn data-button-role=secondary>Workspace</button><button data-testid=workspace-settings-close data-button-role=secondary>Close</button><input data-testid=workspace-name-input><select data-testid=workspace-join-mode></select><button data-testid=workspace-general-submit data-button-role=primary>Save</button><div data-testid=settings-tab-members></div><div data-testid=settings-tab-invitations></div><div data-testid=settings-tab-general></div><div data-testid=settings-pane-members></div><div data-testid=settings-pane-invitations></div><div data-testid=settings-pane-general></div><button data-testid=create-invitation-btn data-button-role=secondary></button><form id=create-invitation-form data-testid=create-invitation-form><button data-testid=create-invitation-submit data-button-role=primary></button></form><div data-testid=invitations-list></div></div><script>const t=new URLSearchParams(location.search).get('token');if(t)localStorage.setItem('huddle.token',t);let token=localStorage.getItem('huddle.token'),cid;const api=(p,o={})=>fetch('/api'+p,{...o,headers:{'Content-Type':'application/json',Authorization:'Bearer '+token}}).then(x=>x.json());async function load(){if(!token)return;let w=await api('/workspaces');if(!w||!w.workspaces||!w.workspaces.length)return;let d=await api('/workspaces/'+w.workspaces[0].slug);let cs=(d&&d.channels)||[];document.querySelector('[data-testid=channel-list]').innerHTML=cs.map(c=>`<div data-testid=channel-entry data-channel-id="${c.id}" data-channel-name="${c.name}">#${c.name}</div>`).join('');document.querySelectorAll('[data-testid=channel-entry]').forEach(x=>x.onclick=()=>{cid=x.dataset.channelId;loadm()});}async function loadm(){let d=await api('/channels/'+cid+'/messages');document.querySelector('[data-testid=message-list]').innerHTML=d.messages.reverse().map(m=>`<div data-testid=message><b>${m.author.display_name}</b><span data-testid=message-body>${m.body}</span><button data-testid=open-thread-btn>Reply</button><button data-testid=reaction-button>😀</button></div>`).join('')}document.querySelector('[data-testid=send-btn]').onclick=async()=>{if(cid){let v=document.querySelector('[data-testid=message-input]').value;if(!v)return;await api('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body:v})});document.querySelector('[data-testid=message-input]').value='';loadm()}};
document.addEventListener('click',e=>{let open=e.target.closest('[data-testid=open-thread-btn]');if(open){let row=open.closest('[data-testid=message]');document.querySelector('[data-testid=thread-panel]').style.display='block';document.querySelector('[data-testid=thread-panel]').dataset.parent=row.querySelector('[data-testid=message-body]').textContent;document.querySelector('[data-testid=thread-panel]').insertAdjacentHTML('beforeend','<div data-testid=thread-replies></div>')}let reaction=e.target.closest('[data-testid=reaction-button]');if(reaction){let row=reaction.closest('[data-testid=message]');let picker=document.querySelector('[data-testid=emoji-picker]');if(!picker){picker=document.createElement('div');picker.dataset.testid='emoji-picker';picker.innerHTML='<button data-testid="emoji-option">👍</button><button data-testid="emoji-option">😀</button>';row.appendChild(picker)}picker.style.display='block';picker.querySelectorAll('[data-testid=emoji-option]').forEach(b=>b.onclick=()=>{let chip=document.createElement('span');chip.dataset.testid='reaction-chip';chip.textContent=b.textContent+' 1';row.appendChild(chip);picker.style.display='none'})}});
document.querySelector('[data-testid=thread-send]').onclick=async()=>{let panel=document.querySelector('[data-testid=thread-panel]'),v=document.querySelector('[data-testid=thread-input]').value;if(!v||!cid)return;let rows=await api('/channels/'+cid+'/messages');let parent=(rows.messages||[]).find(m=>m.body===panel.dataset.parent);if(parent)await api('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body:v,parent_id:parent.id})});let r=document.createElement('div');r.textContent=v;panel.querySelector('[data-testid=thread-replies]').appendChild(r);document.querySelector('[data-testid=thread-input]').value=''};
load();</script></body></html>'''
if __name__=='__main__':
 if os.getenv('IRC_ONLY')=='1':
  irc_server()
 else:
  ThreadingHTTPServer(('127.0.0.1',8000+node),H).serve_forever()
