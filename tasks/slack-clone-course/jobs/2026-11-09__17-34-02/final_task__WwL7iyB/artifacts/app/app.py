import os,sys,json,sqlite3,uuid,re,hashlib,secrets,mimetypes,base64,struct,threading,cgi
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs,unquote
DB='/app/huddle.db'; PORT=int(os.environ.get('PORT','8000')); NODE=(PORT-8000)//1
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
db=sqlite3.connect(DB,check_same_thread=False); db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA busy_timeout=5000')
db.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id TEXT);
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id TEXT,join_mode TEXT);
CREATE TABLE IF NOT EXISTS members(workspace_id TEXT,user_id TEXT,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY,workspace_id TEXT,name TEXT,is_private INTEGER,is_dm INTEGER,topic TEXT,is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
CREATE TABLE IF NOT EXISTS cmembers(channel_id TEXT,user_id TEXT,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,channel_id TEXT,author_id TEXT,body TEXT,parent_id TEXT,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,files TEXT);
CREATE TABLE IF NOT EXISTS reactions(message_id TEXT,user_id TEXT,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,uploader_id TEXT,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB,attached INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS dms(a TEXT,b TEXT,channel_id TEXT,PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS groups_(id TEXT PRIMARY KEY,workspace_id TEXT,handle TEXT UNIQUE,name TEXT,creator_id TEXT,user_ids TEXT);
CREATE TABLE IF NOT EXISTS events(channel_id TEXT,event_id INTEGER,message TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS presence(user_id TEXT PRIMARY KEY,updated_at TEXT);
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,message TEXT);
'''); db.commit()
locks=threading.RLock(); sockets=[]; online=set()
def q(sql,args=(),one=False): 
 with locks:
  c=db.execute(sql,args); rows=c.fetchall(); return (rows[0] if rows else None) if one else rows
def ex(sql,args=()):
 with locks: db.execute(sql,args); db.commit()
def uid(): return str(uuid.uuid4())
def user(r):
 return {'id':r[0],'username':r[1],'display_name':r[3] or r[1],'timezone':r[4] or 'UTC','avatar_url':r[5] or '','status_text':r[6] or '','status_emoji':r[7] or ''}
def auth(h):
 if not h or not h.startswith('Bearer '): return None
 r=q('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(h[7:],),True); return (r,h[7:]) if r else None
def msg(r):
 a=q('SELECT * FROM users WHERE id=?',(r[2],),True); fs=[]
 if r[8]:
  for x in json.loads(r[8]): 
   z=q('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(x,),True)
   if z: fs.append({'id':z[0],'uploader_id':z[1],'filename':z[2],'content_type':z[3],'size':z[4],'created_at':z[5]})
 rr=q('SELECT emoji,COUNT(*),GROUP_CONCAT(user_id) FROM reactions WHERE message_id=? GROUP BY emoji',(r[0],))
 return {'id':r[0],'channel_id':r[1],'author_id':r[2],'author':user(a),'body':r[3],'parent_id':r[4],'reply_count':q('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r[0],),True)[0],'created_at':r[5],'edited_at':r[6],'files':fs,'reactions':[{'emoji':x[0],'count':x[1],'user_ids':x[2].split(',')} for x in rr],'mentions':[]}
def chan(r): return {'id':r[0],'workspace_id':r[1],'name':r[2],'is_private':bool(r[3]),'is_dm':bool(r[4]),'topic':r[5] or '','is_archived':bool(r[6])}
def broadcast(channel,kind,payload):
 with locks:
  n=q('SELECT COALESCE(MAX(event_id),0)+1 FROM events WHERE channel_id=?',(channel,),True)[0]
  ob={'type':kind,'event_id':n,'channel_id':channel,**payload}; ex('INSERT INTO events VALUES(?,?,?)',(channel,n,json.dumps(ob)))
 for s,c in list(sockets):
  if c==channel:
   try: ws_send(s,ob)
   except: pass
 return n
def mention(body,cid,author):
 out=[]
 for u in q('SELECT id,username FROM users'):
  if u[0]!=author and re.search(r'(?<![\w@])@'+re.escape(u[1])+r'(?!\w)',body):
   if q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u[0]),True): out.append(u[0])
 if re.search(r'(?<!\w)@channel\b',body): out += [x[0] for x in q('SELECT user_id FROM cmembers WHERE channel_id=? AND user_id!=?',(cid,author))]
 if re.search(r'(?<!\w)@here\b',body): out += [x[0] for x in q('SELECT user_id FROM cmembers WHERE channel_id=? AND user_id!=?',(cid,author)) if q('SELECT 1 FROM presence WHERE user_id=?',(x[0],),True)]
 for g in q('SELECT handle,user_ids FROM groups_'):
  if re.search(r'(?<!\w)@'+re.escape(g[0])+r'\b',body):
   out += [x for x in json.loads(g[1]) if x!=author and q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,x),True)]
 return list(dict.fromkeys(out))
def ws_send(s,o):
 b=json.dumps(o).encode(); n=len(b); h=bytes([129])+ (bytes([n]) if n<126 else bytes([126])+struct.pack('!H',n)); s.sendall(h+b)
class H(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def sendj(self,status,o):
  b=json.dumps(o).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def read(self):
  n=int(self.headers.get('Content-Length','0')); return self.rfile.read(n)
 def do_GET(self):
  p=urlparse(self.path); path=p.path; qs=parse_qs(p.query)
  if path=='/api/health': return self.sendj(200,{'status':'ok','node_id':NODE})
  if path=='/': return self.sendj(200,{'status':'ok'})
  if path=='/api/ws': return self.websocket(qs.get('token',[''])[0])
  au=auth(self.headers.get('Authorization')); 
  if not au: return self.sendj(401,{'error':'unauthorized'})
  u=au[0]; parts=path.split('/')
  if parts[:3]==['','api','auth'] and parts[3:] == ['me']: return self.sendj(200,{'user':user(u)})
  if parts[:3]==['','api','users'] and len(parts)==4:
   r=q('SELECT * FROM users WHERE id=?',(parts[3],),True); return self.sendj(200,{'user':user(r)}) if r else self.sendj(404,{'error':'not found'})
  if path=='/api/workspaces': return self.sendj(200,{'workspaces':[{'id':x[0],'slug':x[1],'name':x[2],'owner_id':x[3],'join_mode':x[4]} for x in q('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u[0],))]})
  if len(parts)==4 and parts[2]=='workspaces':
   w=q('SELECT * FROM workspaces WHERE slug=?',(parts[3],),True)
   if not w:return self.sendj(404,{})
   cs=q('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0)',(w[0],)); return self.sendj(200,{'workspace':{'id':w[0],'slug':w[1],'name':w[2],'owner_id':w[3],'join_mode':w[4]},'channels':[chan(x) for x in cs],'read_state':{}})
  if parts[2:3]==['channels'] and len(parts)==4:
   cid=parts[3]; c=q('SELECT * FROM channels WHERE id=?',(cid,),True)
   if not c:return self.sendj(404,{})
   if path.endswith('/members'): pass
  if len(parts)>=5 and parts[2]=='channels' and parts[4]=='messages':
   cid=parts[3]; rows=q('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY rowid DESC',(cid,)); return self.sendj(200,{'messages':[msg(x) for x in rows],'next_cursor':None})
  if len(parts)>=5 and parts[2]=='messages' and parts[4]=='replies':
   rows=q('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY rowid',(parts[3],)); return self.sendj(200,{'replies':[msg(x) for x in rows],'next_cursor':None})
  if parts[2:3]==['files']:
   r=q('SELECT id,uploader_id,filename,content_type,size,created_at,data FROM files WHERE id=?',(parts[3],),True)
   if not r:return self.sendj(404,{})
   if len(parts)>4:
    self.send_response(200);self.send_header('Content-Type',r[3]);self.send_header('Content-Disposition','attachment; filename="'+r[2]+'"');self.send_header('Content-Length',str(len(r[6])));self.end_headers();self.wfile.write(r[6]);return
   return self.sendj(200,{'file':dict(zip(('id','uploader_id','filename','content_type','size','created_at'),r[:6]))})
  if len(parts)>=5 and parts[2]=='workspaces' and parts[4]=='groups':
   ws=q('SELECT id FROM workspaces WHERE slug=?',(parts[3],),True)[0]; return self.sendj(200,{'groups':[{'id':x[0],'handle':x[1],'name':x[2],'member_user_ids':json.loads(x[5])} for x in q('SELECT * FROM groups_ WHERE workspace_id=?',(ws,))]})
  return self.sendj(404,{})
 def do_POST(self): self.mutate('POST')
 def do_PATCH(self): self.mutate('PATCH')
 def do_DELETE(self): self.mutate('DELETE')
 def mutate(self,method):
  path=urlparse(self.path).path; parts=path.split('/'); au=auth(self.headers.get('Authorization'))
  if path=='/api/auth/register':
   try: b=json.loads(self.read()); 
   except: return self.sendj(400,{})
   if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return self.sendj(400,{})
   if q('SELECT 1 FROM users WHERE username=?',(b['username'],),True):return self.sendj(409,{})
   i=uid(); ex('INSERT INTO users VALUES(?,?,?,?,?,?,?,?)',(i,b['username'],hashlib.sha256(b['password'].encode()).hexdigest(),b.get('display_name'), 'UTC','','',''))
   t=secrets.token_urlsafe(24);ex('INSERT INTO tokens VALUES(?,?)',(t,i));return self.sendj(201,{'user':user(q('SELECT * FROM users WHERE id=?',(i,),True)),'token':t})
  if path=='/api/auth/login':
   b=json.loads(self.read()); r=q('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),hashlib.sha256(b.get('password','').encode()).hexdigest()),True)
   if not r:return self.sendj(401,{})
   t=secrets.token_urlsafe(24);ex('INSERT INTO tokens VALUES(?,?)',(t,r[0],));return self.sendj(200,{'user':user(r),'token':t})
  if not au:return self.sendj(401,{})
  u=au[0]
  try:b=json.loads(self.read()) if self.headers.get('Content-Type','').startswith('application/json') else {}
  except:b={}
  if path=='/api/workspaces' and method=='POST':
   if q('SELECT 1 FROM workspaces WHERE slug=?',(b.get('slug'),),True):return self.sendj(409,{})
   wid=uid();cid=uid();ex('INSERT INTO workspaces VALUES(?,?,?,?,?)',(wid,b['slug'],b['name'],u[0],'open'));ex('INSERT INTO members VALUES(?,?,?)',(wid,u[0],'owner'));ex('INSERT INTO channels VALUES(?,?,?,?,?,?,?)',(cid,wid,'general',0,0,'',0));ex('INSERT INTO cmembers VALUES(?,?)',(cid,u[0]));return self.sendj(201,{'workspace':{'id':wid,'slug':b['slug'],'name':b['name'],'owner_id':u[0],'join_mode':'open'},'general_channel':chan(q('SELECT * FROM channels WHERE id=?',(cid,),True))})
  if parts[2:3]==['channels'] and len(parts)==4 and method=='POST':
   cid=parts[3]; c=q('SELECT * FROM channels WHERE id=?',(cid,),True)
   if c and c[6]: return self.sendj(423,{})
  if len(parts)==5 and parts[2]=='channels' and parts[4]=='join' and method=='POST':
   c=q('SELECT * FROM channels WHERE id=?',(parts[3],),True)
   if not c:return self.sendj(404,{})
   ex('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(c[0],u[0])); ex('INSERT OR IGNORE INTO members VALUES(?,?,?)',(c[1],u[0],'member'));return self.sendj(200,{'channel':chan(c)})
  if len(parts)>=5 and parts[2]=='workspaces' and parts[4]=='channels' and method=='POST':
   w=q('SELECT * FROM workspaces WHERE slug=?',(parts[3],),True); cid=uid(); 
   try: ex('INSERT INTO channels VALUES(?,?,?,?,?,?,?)',(cid,w[0],b['name'],int(bool(b.get('is_private'))),0,b.get('topic',''),0))
   except sqlite3.IntegrityError:return self.sendj(409,{})
   ex('INSERT INTO cmembers VALUES(?,?)',(cid,u[0]));return self.sendj(201,{'channel':chan(q('SELECT * FROM channels WHERE id=?',(cid,),True))})
  if len(parts)>=5 and parts[2]=='channels' and parts[4]=='messages' and method=='POST': return self.message(parts[3],u,b)
  if len(parts)>=6 and parts[2]=='messages' and parts[4]=='reactions' and method=='DELETE':
   ex('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(parts[3],u[0],unquote(parts[5])));rows=q('SELECT emoji,COUNT(*),GROUP_CONCAT(user_id) FROM reactions WHERE message_id=? GROUP BY emoji',(parts[3],));return self.sendj(200,{'reactions':[{'emoji':x[0],'count':x[1],'user_ids':x[2].split(',')} for x in rows]})
  if len(parts)>=5 and parts[2]=='messages' and parts[4]=='reactions' and method=='POST':
   mid=parts[3]; emoji=b.get('emoji'); ex('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u[0],emoji)); rows=q('SELECT emoji,COUNT(*),GROUP_CONCAT(user_id) FROM reactions WHERE message_id=? GROUP BY emoji',(mid,));return self.sendj(200,{'reactions':[{'emoji':x[0],'count':x[1],'user_ids':x[2].split(',')} for x in rows]})
  if path=='/api/dms':
   target=q('SELECT id FROM users WHERE id=?',(b.get('user_id'),),True)
   if not target or target[0]==u[0]: return self.sendj(400,{})
   a,bb=sorted([u[0],target[0]]); r=q('SELECT channel_id FROM dms WHERE a=? AND b=?',(a,bb),True)
   if not r:
    cid=uid();ex('INSERT INTO dms VALUES(?,?,?)',(a,bb,cid));ex('INSERT INTO channels VALUES(?,?,?,?,?,?,?)',(cid,'dm-'+cid,'dm',0,1,'',0));ex('INSERT INTO cmembers VALUES(?,?)',(cid,a));ex('INSERT INTO cmembers VALUES(?,?)',(cid,bb))
   else:cid=r[0]
   return self.sendj(200,{'channel_id':cid})
  if path=='/api/files':
   env=cgi.FieldStorage(fp=self.rfile,headers=self.headers,environ={'REQUEST_METHOD':'POST','CONTENT_TYPE':self.headers.get('Content-Type'),'CONTENT_LENGTH':self.headers.get('Content-Length')}); f=env['file']; data=f.file.read()
   if len(data)>10*1024*1024:return self.sendj(413,{})
   fid=uid();ex('INSERT INTO files VALUES(?,?,?,?,?,?,?,0)',(fid,u[0],f.filename,f.type or 'application/octet-stream',len(data),now(),data));r=q('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(fid,),True);return self.sendj(201,{'file':dict(zip(('id','uploader_id','filename','content_type','size','created_at'),r))})
  if path=='/api/users/me' and method=='PATCH':
   if b.get('timezone') and b['timezone'] not in ('UTC','Etc/UTC'):return self.sendj(400,{})
   for k in ('display_name','timezone','avatar_url','status_text','status_emoji'):
    if k in b: ex(f'UPDATE users SET {k}=? WHERE id=?',(b[k],u[0]))
   ob={'type':'user.updated','user':user(q('SELECT * FROM users WHERE id=?',(u[0],),True))}
   ex('INSERT INTO global_events(message) VALUES(?)',(json.dumps(ob),))
   return self.sendj(200,{'user':ob['user']})
  if len(parts)>=5 and parts[2]=='workspaces' and parts[4]=='groups':
   w=q('SELECT id FROM workspaces WHERE slug=?',(parts[3],),True)[0]
   if method=='POST':
    g=uid(); ex('DELETE FROM groups_ WHERE handle=?',(b['handle'],)); ex('INSERT INTO groups_ VALUES(?,?,?,?,?,?)',(g,w,b['handle'],b.get('name',''),u[0],json.dumps(b.get('user_ids',[])))); return self.sendj(201,{'group':{'id':g,'handle':b['handle'],'name':b.get('name',''),'member_user_ids':b.get('user_ids',[])}})
   h=parts[5]; gr=q('SELECT * FROM groups_ WHERE handle=?',(h,),True)
   if not gr and method=='DELETE': return self.sendj(200,{'deleted':True})
   if not gr:return self.sendj(404,{})
   owner=q('SELECT owner_id FROM workspaces WHERE id=?',(w,),True)[0]
   if u[0] not in (gr[4],owner): return self.sendj(403,{})
   if method=='DELETE': ex('DELETE FROM groups_ WHERE handle=?',(h,));return self.sendj(200,{'deleted':True})
   for k in ('name','user_ids'):
    if k in b: ex(f'UPDATE groups_ SET {k if k=="name" else "user_ids"}=? WHERE handle=?',(json.dumps(b[k]) if k=='user_ids' else b[k],h))
   return self.sendj(200,{'group':{'id':gr[0],'handle':h,'name':b.get('name',gr[3]),'member_user_ids':b.get('user_ids',json.loads(gr[5]))}})
  return self.sendj(404,{})
 def message(self,cid,u,b):
  c=q('SELECT * FROM channels WHERE id=?',(cid,),True)
  if not c:return self.sendj(404,{})
  body=b.get('body','')
  if not body:return self.sendj(400,{})
  if body.startswith('/') and not body.startswith('//'):
   if body=='/me':return self.sendj(400,{})
   if body.startswith('/me '):body=body
   elif body.startswith('/shrug'):body=(body[7:].strip()+' ' if body[7:].strip() else '')+'¯\\_(ツ)_/¯'
   elif body.startswith('/topic '):ex('UPDATE channels SET topic=? WHERE id=?',(body[7:],cid)); cc=q('SELECT * FROM channels WHERE id=?',(cid,),True);return self.sendj(201,{'message':None,'channel':chan(cc)})
   elif body=='/archive' or body=='/unarchive': ex('UPDATE channels SET is_archived=? WHERE id=?',(1 if body=='/archive' else 0,cid));return self.sendj(201,{'message':None,'channel':chan(q('SELECT * FROM channels WHERE id=?',(cid,),True))})
   elif body.startswith('/invite @'):
    r=q('SELECT * FROM users WHERE username=?',(body[9:].strip(),),True)
    return self.sendj(201,{'message':None,'invited_user':user(r) if r else None})
   else:return self.sendj(400,{})
  if body.startswith('//'):body=body[1:]
  ids=b.get('file_ids',[]); files=[]
  for fid in ids:
   f=q('SELECT * FROM files WHERE id=?',(fid,),True)
   if not f or f[1]!=u[0] or f[7]:return self.sendj(400,{})
   files.append(fid);ex('UPDATE files SET attached=1 WHERE id=?',(fid,))
  mid=uid(); par=b.get('parent_id'); ex('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)',(mid,cid,u[0],body,par,now(),None,0,json.dumps(files))); r=q('SELECT * FROM messages WHERE id=?',(mid,),True); mo=msg(r); mo['mentions']=mention(body,cid,u[0]); broadcast(cid,'message.reply' if par else 'message.created',{'message':mo}); return self.sendj(201,{'message':mo})
 def websocket(self,tok):
  global sockets
  r=q('SELECT user_id FROM tokens WHERE token=?',(tok,),True)
  if not r:return self.send_error(401); 
  key=self.headers.get('Sec-WebSocket-Key'); self.send_response(101);self.send_header('Upgrade','WebSocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode());self.end_headers()
  s=self.connection; s.settimeout(.3); online.add(r[0]); ex('INSERT OR REPLACE INTO presence VALUES(?,?)',(r[0],now())); channel=None;sockets.append((s,channel)); last_global=q('SELECT COALESCE(MAX(id),0) FROM global_events',(),True)[0]
  try:
   while True:
    try:
     h=s.recv(2)
    except TimeoutError:
     for ge in q('SELECT id,message FROM global_events WHERE id>? ORDER BY id',(last_global,)):
      last_global=ge[0]
      try: ws_send(s,json.loads(ge[1]))
      except: pass
     continue
    if not h: break
    n=h[1]&127
    if n==126: n=struct.unpack('!H',s.recv(2))[0]
    mask=s.recv(4); data=s.recv(n); data=bytes(v^mask[i%4] for i,v in enumerate(data)); o=json.loads(data)
    if o.get('type')=='subscribe':
     channel=o.get('channel_id'); sockets[-1]=(s,channel); head=q('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(channel,),True)[0]; ws_send(s,{'type':'subscribed','channel_id':channel,'head_event_id':head})
  except:pass
  finally:
   try:sockets.remove((s,channel))
   except:pass
   online.discard(r[0]); ex('DELETE FROM presence WHERE user_id=?',(r[0],))
def main():
 ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
if __name__=='__main__':main()
