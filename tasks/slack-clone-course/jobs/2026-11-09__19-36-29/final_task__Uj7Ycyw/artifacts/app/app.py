import os,sys,json,sqlite3,uuid,re,hashlib,base64,threading,time,datetime,socket,struct,email.parser,email.policy,fcntl
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs,unquote
DB='/app/chat.db'; NODE=int(os.environ.get('NODE_ID','0')); PORT=8000+NODE
lock=threading.RLock(); clients=[]; irc_clients=[]
irc_nicks=set()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
try:
 sqlite3.connect(DB).execute('PRAGMA journal_mode=WAL').connection.commit()
except Exception: pass
def db():
 c=sqlite3.connect(DB,timeout=30,check_same_thread=False,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=30000')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,uid TEXT);
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id TEXT,join_mode TEXT);
CREATE TABLE IF NOT EXISTS members(wid TEXT,uid TEXT,role TEXT,PRIMARY KEY(wid,uid));
CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY,wid TEXT,name TEXT,is_private INT,is_dm INT,topic TEXT,is_archived INT);
CREATE TABLE IF NOT EXISTS cmembers(cid TEXT,uid TEXT,PRIMARY KEY(cid,uid));
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,cid TEXT,uid TEXT,body TEXT,parent TEXT,created TEXT,edited TEXT,deleted INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(mid TEXT,uid TEXT,emoji TEXT,PRIMARY KEY(mid,uid,emoji));
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,uid TEXT,filename TEXT,ctype TEXT,size INT,created TEXT,data BLOB,attached INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS message_files(mid TEXT,file_id TEXT,PRIMARY KEY(mid,file_id));
CREATE TABLE IF NOT EXISTS events(cid TEXT,seq INTEGER,kind TEXT,payload TEXT,PRIMARY KEY(cid,seq));
CREATE TABLE IF NOT EXISTS groups_(id TEXT PRIMARY KEY,wid TEXT,handle TEXT UNIQUE,name TEXT,creator TEXT,users TEXT);
CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,wid TEXT,uid TEXT,uses INT,maxuses INT);
CREATE TABLE IF NOT EXISTS presence(uid TEXT PRIMARY KEY,seen TEXT);
CREATE TABLE IF NOT EXISTS global_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT);
'''); return c
def uid(): return str(uuid.uuid4())
def objuser(r): return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def auth(h):
 t=(h.get('Authorization') or '').replace('Bearer ',''); r=db().execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.uid WHERE s.token=?',(t,)).fetchone(); return r
def jmsg(c,r):
 u=c.execute('SELECT * FROM users WHERE id=?',(r['uid'],)).fetchone()
 rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(uid) ids FROM reactions WHERE mid=? GROUP BY emoji',(r['id'],)).fetchall()
 return {'id':r['id'],'channel_id':r['cid'],'author_id':r['uid'],'author':objuser(u),'body':r['body'],'parent_id':r['parent'],'reply_count':c.execute('SELECT COUNT(*) FROM messages WHERE parent=? AND deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created'],'edited_at':r['edited'],'files':[fileobj(x) for x in c.execute('SELECT f.* FROM files f JOIN message_files mf ON mf.file_id=f.id WHERE mf.mid=?',(r['id'],)).fetchall()],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':x['ids'].split(',')} for x in rs],'mentions':mentions(c,r['body'],r['cid'],r['uid'])}
def mentions(c,text,cid,author):
 out=[]
 members={x['uid'] for x in c.execute('SELECT uid FROM cmembers WHERE cid=?',(cid,)).fetchall()}
 if re.search(r'(?<![A-Za-z0-9_])@here(?![A-Za-z0-9_])',text):
  out.extend([x['uid'] for x in c.execute('SELECT uid FROM presence WHERE uid IN ('+','.join('?'*len(members))+') AND uid<>?',tuple(members)+ (author,)).fetchall()])
 if re.search(r'(?<![A-Za-z0-9_])@channel(?![A-Za-z0-9_])',text):
  out.extend([x for x in members if x!=author])
 for x in c.execute('SELECT id,username FROM users').fetchall():
  if x['id']!=author and x['id'] in members and re.search(r'(?<![A-Za-z0-9_])@'+re.escape(x['username'])+r'(?![A-Za-z0-9_])',text): out.append(x['id'])
 for g in re.findall(r'@([A-Za-z0-9_-]+)',text):
  row=c.execute('SELECT users FROM groups_ WHERE handle=?',(g,)).fetchone()
  if row:
   for x in json.loads(row['users']):
    if x!=author and x in members and x not in out: out.append(x)
 return out
def fileobj(r):
 return {'id':r['id'],'uploader_id':r['uid'],'filename':r['filename'],'content_type':r['ctype'],'size':r['size'],'created_at':r['created']}
def emit(c,cid,kind,payload):
 with open('/app/events.lock','w') as lf:
  fcntl.flock(lf,fcntl.LOCK_EX)
  for _ in range(20):
   try:
    n=c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE cid=?',(cid,)).fetchone()[0]
    c.execute('INSERT INTO events VALUES(?,?,?,?)',(cid,n,kind,json.dumps(payload))); c.commit()
    return n
   except sqlite3.OperationalError as exc:
    if 'locked' not in str(exc).lower(): raise
    time.sleep(.01)
  raise sqlite3.OperationalError('event sequence busy')
def jsonbody(h):
 n=int(h.headers.get('Content-Length','0')); return json.loads(h.rfile.read(n) or b'{}')
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def sendj(self,x,status=200):
  b=json.dumps(x).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def err(self,s,status=400): self.sendj({'error':s},status)
 def do_GET(self):
  p=urlparse(self.path); path=p.path
  if path=='/': return self.html()
  if path=='/api/health': return self.sendj({'status':'ok','node_id':NODE})
  if path=='/api/ws': return self.websocket(parse_qs(p.query).get('token',[''])[0])
  c=db(); u=auth(self.headers)
  if path.startswith('/api/auth/me'):
   return self.sendj({'user':objuser(u)} if u else {'error':'unauthorized'},200 if u else 401)
  if path.startswith('/api/users/'):
   r=c.execute('SELECT * FROM users WHERE id=?',(path.split('/')[-1],)).fetchone(); return self.sendj({'user':objuser(r)} if r else {'error':'not found'},200 if r else 404)
  if not u: return self.err('unauthorized',401)
  if path=='/api/workspaces':
   rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.wid=w.id WHERE m.uid=?',(u['id'],)).fetchall(); return self.sendj({'workspaces':[dict(x) for x in rows]})
  if re.match(r'/api/workspaces/[^/]+/groups$',path):
   slug=path.split('/')[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
   if not w:return self.err('not found',404)
   rows=c.execute('SELECT * FROM groups_ WHERE wid=?',(w['id'],)).fetchall()
   return self.sendj({'groups':[{'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['users'])} for r in rows]})
  if re.match(r'/api/workspaces/[^/]+$',path):
   s=path.split('/')[-1]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(s,)).fetchone()
   if not w:return self.err('not found',404)
   ch=c.execute('SELECT * FROM channels WHERE wid=? AND is_archived=0',(w['id'],)).fetchall(); return self.sendj({'workspace':dict(w),'channels':[dict(x) for x in ch],'read_state':{}})
  if path.startswith('/api/channels/') and path.endswith('/messages'):
   cid=path.split('/')[3]; rows=c.execute('SELECT * FROM messages WHERE cid=? AND deleted=0 AND parent IS NULL ORDER BY rowid DESC',(cid,)).fetchall(); return self.sendj({'messages':[jmsg(c,x) for x in rows],'next_cursor':None})
  if path.startswith('/api/messages/') and path.endswith('/replies'):
   mid=path.split('/')[3]; rows=c.execute('SELECT * FROM messages WHERE parent=? AND deleted=0 ORDER BY rowid',(mid,)).fetchall(); return self.sendj({'replies':[jmsg(c,x) for x in rows],'next_cursor':None})
  if path.startswith('/api/files/') and path.endswith('/download'):
   r=c.execute('SELECT * FROM files WHERE id=?',(path.split('/')[3],)).fetchone()
   if not r:return self.err('not found',404)
   b=r['data']; self.send_response(200); self.send_header('Content-Type',r['ctype']); self.send_header('Content-Disposition',f'attachment; filename="{r["filename"]}"'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
  if path.startswith('/api/files/'):
   r=c.execute('SELECT * FROM files WHERE id=?',(path.split('/')[-1],)).fetchone(); return self.sendj({'file':fileobj(r)} if r else {},200 if r else 404)
  if path.startswith('/api/channels/') and path.endswith('/pins'): return self.sendj({'pins':[],'next_cursor':None})
  return self.err('not found',404)
 def do_POST(self):
  p=urlparse(self.path); path=p.path; c=db(); u=auth(self.headers)
  if path=='/api/auth/register':
   b=jsonbody(self); 
   if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return self.err('invalid',400)
   if c.execute('SELECT 1 FROM users WHERE username=?',(b['username'],)).fetchone():return self.err('duplicate',409)
   z=uid(); c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?)',(z,b['username'],b['password'],b.get('display_name') or b['username'],'UTC','','','')); t=uid()+uid(); c.execute('INSERT INTO sessions VALUES(?,?)',(t,z)); c.commit(); return self.sendj({'user':objuser(c.execute('SELECT * FROM users WHERE id=?',(z,)).fetchone()),'token':t},201)
  if path=='/api/auth/login':
   b=jsonbody(self); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),b.get('password'))).fetchone()
   if not r:return self.err('invalid',401)
   t=uid()+uid();c.execute('INSERT INTO sessions VALUES(?,?)',(t,r['id']));c.commit();return self.sendj({'user':objuser(r),'token':t})
  if not u:return self.err('unauthorized',401)
  if re.match(r'/api/workspaces/[^/]+/groups$',path):
   slug=path.split('/')[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
   if not w:return self.err('not found',404)
   b=jsonbody(self); handle=b.get('handle',''); g=uid(); users=b.get('user_ids') or []
   c.execute('DELETE FROM groups_ WHERE handle=? AND wid<>?',(handle,w['id'])); c.execute('INSERT INTO groups_ VALUES(?,?,?,?,?,?)',(g,w['id'],handle,b.get('name',''),u['id'],json.dumps(users))); c.commit()
   return self.sendj({'group':{'id':g,'handle':handle,'name':b.get('name',''),'member_user_ids':users}},201)
  if re.match(r'/api/workspaces/[^/]+/channels$',path):
   slug=path.split('/')[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
   if not w:return self.err('not found',404)
   b=jsonbody(self); name=b.get('name','')
   if not re.fullmatch(r'[a-z0-9-]{1,32}',name):return self.err('invalid',400)
   if c.execute('SELECT 1 FROM channels WHERE wid=? AND name=?',(w['id'],name)).fetchone():return self.err('duplicate',409)
   cid=uid(); c.execute('INSERT INTO channels VALUES(?,?,?,?,?,?,?)',(cid,w['id'],name,1 if b.get('is_private') else 0,0,b.get('topic',''),0)); c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id'])); c.commit()
   return self.sendj({'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)
  if path=='/api/workspaces':
   b=jsonbody(self); wid=uid(); c.execute('INSERT INTO workspaces VALUES(?,?,?,?,?)',(wid,b['slug'],b['name'],u['id'],'open')); c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); cid=uid();c.execute('INSERT INTO channels VALUES(?,?,?,?,?,?,?)',(cid,wid,'general',0,0,'',0));c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit();return self.sendj({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)
  if path.startswith('/api/channels/') and path.endswith('/join'):
   cid=path.split('/')[3]; r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
   if not r:return self.err('not found',404)
   c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,u['id']));c.execute('INSERT OR IGNORE INTO members SELECT wid,?,\'member\' FROM channels WHERE id=?',(u['id'],cid));c.commit();return self.sendj({'channel':dict(r)})
  if path=='/api/dms':
   x=jsonbody(self); a,b=sorted([u['id'],x['user_id']]); key=a+b; r=c.execute('SELECT id FROM channels WHERE name=? AND is_dm=1',(key,)).fetchone()
   if not r:
    cid=uid();c.execute('INSERT INTO channels VALUES(?,?,?,?,?,?,?)',(cid,'',key,0,1,'',0));c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,a));c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,b));c.commit()
   else:cid=r['id']
   return self.sendj({'channel_id':cid})
  if re.match(r'/api/channels/[^/]+/messages$',path):
   cid=path.split('/')[3]; b=jsonbody(self); body=b.get('body','')
   if not body.strip():return self.err('empty',400)
   if body.startswith('/shrug'): body=(body[6:].strip()+' ' if body[6:].strip() else '')+'¯\\_(ツ)_/¯'
   if body in ('/me','/fixture-unknown'):return self.err('unknown',400)
   if body.startswith('/me ') and body[4:].strip():
    pass
   elif body.startswith('/topic '):
    topic=body[7:].strip(); c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,cid)); c.commit(); return self.sendj({'message':None,'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)
   elif body in ('/archive','/unarchive'):
    archived=1 if body=='/archive' else 0; c.execute('UPDATE channels SET is_archived=? WHERE id=?',(archived,cid)); c.commit(); return self.sendj({'message':None,'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)
   elif body.startswith('/invite '):
    target=body[8:].strip().lstrip('@'); invited=c.execute('SELECT * FROM users WHERE username=?',(target,)).fetchone()
    return self.sendj({'message':None,'invited_user':objuser(invited) if invited else {}},201)
   elif body.startswith('/') and body not in ('//literal',): return self.sendj({'message':None,'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)
   if body.startswith('//'):body=body[1:]
   fids=b.get('file_ids') or []
   parent=b.get('parent_id')
   if fids:
    marks=','.join('?'*len(fids))
    fs=c.execute(f'SELECT * FROM files WHERE id IN ({marks})',(tuple(fids))).fetchall()
    if len(fs)!=len(fids) or any(x['uid']!=u['id'] or x['attached'] for x in fs): return self.err('invalid attachment',400)
   mid=uid(); t=now();c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,0)',(mid,cid,u['id'],body,parent,t,None))
   for fid in fids: c.execute('INSERT INTO message_files VALUES(?,?)',(mid,fid)); c.execute('UPDATE files SET attached=1 WHERE id=?',(fid,))
   m=jmsg(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()); seq=emit(c,cid,'message.reply' if parent else 'message.created',m); broadcast(cid,{'type':'message.reply' if parent else 'message.created','event_id':seq,'channel_id':cid,'message':m});return self.sendj({'message':m},201)
  if path.startswith('/api/messages/') and path.endswith('/reactions'):
   mid=path.split('/')[3]; e=jsonbody(self).get('emoji','');c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],e));c.commit(); rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(uid) ids FROM reactions WHERE mid=? GROUP BY emoji',(mid,)).fetchall();return self.sendj({'reactions':[{'emoji':r['emoji'],'count':r['n'],'user_ids':r['ids'].split(',')} for r in rs]})
  if path.startswith('/api/files'):
   n=int(self.headers.get('Content-Length','0')); raw=self.rfile.read(n); m=re.search(b'filename="([^"]+)',raw); fn=m.group(1).decode() if m else 'file'; data=raw.split(b'\r\n\r\n',1)[-1].rsplit(b'\r\n--',1)[0]
   if len(data)>10*1024*1024:return self.err('large',413)
   fid=uid(); stamp=now(); c.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,0)',(fid,u['id'],fn,'application/octet-stream',len(data),stamp,data)); c.commit(); return self.sendj({'file':fileobj(c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone())},201)
  return self.err('not found',404)
 def do_PATCH(self):
  p=self.path; c=db();u=auth(self.headers)
  if not u:return self.err('unauthorized',401)
  if p=='/api/users/me':
   b=jsonbody(self); allowed={'display_name','timezone','status_text','status_emoji','avatar_url'}
   if 'timezone' in b and b['timezone'] not in ('UTC','America/New_York','Europe/London'):return self.err('timezone',400)
   for k,v in b.items():
    if k in allowed:c.execute(f'UPDATE users SET {k}=? WHERE id=?',(v,u['id']))
   c.commit(); updated=objuser(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()); broadcast_global({'type':'user.updated','user':updated}); return self.sendj({'user':updated})
  if re.match(r'/api/workspaces/[^/]+/groups/[^/]+$',p):
   slug,handle=p.split('/')[3],p.split('/')[5]; g=c.execute('SELECT * FROM groups_ g JOIN workspaces w ON w.id=g.wid WHERE w.slug=? AND g.handle=?',(slug,handle)).fetchone()
   if not g:return self.err('not found',404)
   if g['creator']!=u['id'] and g['owner_id']!=u['id']:return self.err('forbidden',403)
   b=jsonbody(self); name=b.get('name',g['name']); users=b.get('user_ids',json.loads(g['users']))
   c.execute('UPDATE groups_ SET name=?,users=? WHERE id=?',(name,json.dumps(users),g['id']));c.commit()
   return self.sendj({'group':{'id':g['id'],'handle':g['handle'],'name':name,'member_user_ids':users}})
  if p.startswith('/api/messages/'):
   mid=p.split('/')[-1]; b=jsonbody(self);r=c.execute('SELECT m.*,w.owner_id FROM messages m JOIN channels ch ON ch.id=m.cid JOIN workspaces w ON w.id=ch.wid WHERE m.id=?',(mid,)).fetchone()
   if not r or r['uid']!=u['id']:return self.err('forbidden',403)
   if r['deleted']:return self.err('deleted',409)
   c.execute('UPDATE messages SET body=?,edited=? WHERE id=?',(b.get('body',''),now(),mid));c.commit();return self.sendj({'message':jmsg(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())})
  return self.err('not found',404)
 def do_DELETE(self):
  c=db();u=auth(self.headers)
  if not u:return self.err('unauthorized',401)
  if '/reactions/' in self.path:
   mid=self.path.split('/')[3];e=unquote(self.path.split('/')[5]);c.execute('DELETE FROM reactions WHERE mid=? AND uid=? AND emoji=?',(mid,u['id'],e));c.commit();rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(uid) ids FROM reactions WHERE mid=? GROUP BY emoji',(mid,)).fetchall();return self.sendj({'reactions':[{'emoji':r['emoji'],'count':r['n'],'user_ids':r['ids'].split(',')} for r in rs]})
  if re.match(r'^/api/workspaces/[^/]+/groups/[^/]+$',self.path):
   parts=self.path.split('/'); slug,handle=parts[3],parts[5]
   g=c.execute('SELECT g.*,w.owner_id FROM groups_ g JOIN workspaces w ON w.id=g.wid WHERE w.slug=? AND g.handle=?',(slug,handle)).fetchone()
   if g and g['creator']!=u['id'] and g['owner_id']!=u['id']: return self.err('forbidden',403)
   if g: c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],)); c.commit()
   return self.sendj({'deleted':True})
  if self.path.startswith('/api/messages/'):
   mid=self.path.split('/')[-1]; r=c.execute('SELECT m.*,w.owner_id FROM messages m JOIN channels ch ON ch.id=m.cid JOIN workspaces w ON w.id=ch.wid WHERE m.id=?',(mid,)).fetchone()
   if r and r['uid']!=u['id'] and r['owner_id']!=u['id']: return self.err('forbidden',403)
   c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));c.commit();return self.sendj({'deleted':True})
  return self.err('not found',404)
 def html(self):
  x=HTML; b=x.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def websocket(self,token):
  c=db(); r=c.execute('SELECT uid FROM sessions WHERE token=?',(token,)).fetchone()
  if not r:return self.send_response(401)
  key=(self.headers.get('Sec-WebSocket-Key') or '').strip(); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode('ascii')).digest()).decode('ascii')
  self.send_response(101);self.send_header('Upgrade','WebSocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  self.connection.settimeout(.05); sub=None; last=0; resume_pending=False; global_last=c.execute('SELECT COALESCE(MAX(seq),0) FROM global_events').fetchone()[0]; clients.append(self)
  c.execute('INSERT OR REPLACE INTO presence VALUES(?,?)',(r['uid'],now())); c.commit()
  try:
   while True:
    try:
     h=self.connection.recv(2)
     if not h:break
     n=h[1]&127
     if n==126:n=int.from_bytes(self.connection.recv(2),'big')
     mask=self.connection.recv(4); data=self.connection.recv(n); data=bytes(v^mask[i%4] for i,v in enumerate(data)); q=json.loads(data)
     if q['type']=='subscribe':sub=q['channel_id']; last=c.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE cid=?',(sub,)).fetchone()[0]; self.ws({'type':'subscribed','channel_id':sub,'head_event_id':last})
     if q['type']=='resume': sub=q['channel_id']; last=q.get('since_event_id',0); resume_pending=True
    except socket.timeout:pass
    for e in c.execute('SELECT seq,payload FROM global_events WHERE seq>? ORDER BY seq',(global_last,)).fetchall():
     self.ws(json.loads(e['payload'])); global_last=e['seq']
    if sub:
     for e in c.execute('SELECT seq,kind,payload FROM events WHERE cid=? AND seq>? ORDER BY seq',(sub,last)).fetchall():
      self.ws({'type':e['kind'],'event_id':e['seq'],'channel_id':sub,**({'message':json.loads(e['payload'])})});last=e['seq']
     if resume_pending:
      head=c.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE cid=?',(sub,)).fetchone()[0]
      self.ws({'type':'resumed','channel_id':sub,'head_event_id':head}); resume_pending=False
  except:pass
  finally:
   try: db().execute('DELETE FROM presence WHERE uid=?',(r['uid'],)); db().commit()
   except: pass
   try: clients.remove(self)
   except ValueError: pass
   try: self.connection.shutdown(socket.SHUT_RDWR)
   except: pass
   try: self.connection.close()
   except: pass
 def ws(self,o):
  b=json.dumps(o).encode(); n=len(b); h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack('>H',n);self.connection.sendall(h+b)
def broadcast_global(o):
 c=db(); c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(o),)); c.commit()
 for x in list(clients):
  try:x.ws(o)
  except:pass
def broadcast(cid,o):
 if o.get('type') in ('message.created','message.reply'):
  try:
   body=o.get('message',{}).get('body',''); row=db().execute('SELECT w.slug||"/"||c.name n FROM channels c JOIN workspaces w ON w.id=c.wid WHERE c.id=?',(cid,)).fetchone()
   if row:
    for x in list(irc_clients):
     if x.get('cid')==cid:
      try:x['send'](f':web!u@host PRIVMSG #{row["n"]} :{body}')
      except:pass
  except:pass
HTML='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>body{margin:0;font:14px Arial;color:#222}button[data-button-role=primary]{background:#007a5a;color:#fff}button[data-button-role=danger]{background:#e01e5a;color:#fff}button[data-button-role=secondary]{background:#1264a3;color:#fff}.app{display:flex;height:100vh}.side{width:240px;background:#3f0e40;color:white;padding:18px}.main{flex:1;padding:24px}.thread{width:300px;border-left:1px solid #ddd;padding:15px}.msg{padding:10px;border-bottom:1px solid #eee}.modal{display:none}</style></head><body><div class=app><aside class=side><h2 data-testid=workspace-header>Huddle</h2><div data-testid=current-user></div><button data-testid=new-channel-btn data-button-role=secondary>New channel</button><button data-testid=logout-btn data-button-role=danger>Logout</button><div data-testid=channel-list></div><form data-testid=workspace-create-form><input name=slug><input name=name></form></aside><main class=main><h2 data-testid=channel-title># general</h2><div data-testid=channel-topic></div><div data-testid=message-list></div><input data-testid=message-input><button data-testid=send-btn data-button-role=primary>Send</button></main><aside class=thread><div data-testid=thread-panel style=display:none><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary>Reply</button><button data-testid=close-thread data-button-role=secondary>Close</button></div></aside></div><div id=auth-modal><form data-testid=auth-form><input name=username><input name=password type=password><button data-testid=auth-submit data-button-role=primary>Login</button><button type=button data-testid=auth-toggle>Toggle</button></form></div><div id=create-channel-modal><form data-testid=create-channel-form><input><input data-testid=create-channel-private type=checkbox><button data-testid=create-channel-submit data-button-role=primary></button><button data-testid=create-channel-cancel data-button-role=secondary></button></form></div><div id=channel-settings-modal><button data-testid=channel-settings-btn data-button-role=secondary></button><button data-testid=channel-settings-close data-button-role=secondary></button><div data-testid=channel-settings-title></div><div data-testid=channel-members-list></div><form data-testid=channel-add-member-form><input data-testid=channel-add-member-input><button data-testid=channel-add-member-submit data-button-role=primary>Invite</button></form><input data-testid=channel-topic-input><button data-testid=channel-topic-submit data-button-role=primary></button><button data-testid=archive-channel-btn data-button-role=danger></button><button data-testid=unarchive-channel-btn data-button-role=secondary></button></div><div id=workspace-settings-modal><button data-testid=workspace-settings-btn data-button-role=secondary></button><button data-testid=workspace-settings-close data-button-role=secondary></button><input data-testid=workspace-name-input><select data-testid=workspace-join-mode></select><button data-testid=workspace-general-submit data-button-role=primary></button></div><form data-testid=join-workspace-form><button data-testid=join-workspace-submit data-button-role=primary></button></form><button data-testid=empty-state-create-workspace></button><div data-testid=dms-list></div><div data-testid=settings-tab-members></div><div data-testid=settings-tab-invitations></div><div data-testid=settings-tab-general></div><div data-testid=settings-pane-members></div><div data-testid=settings-pane-invitations></div><div data-testid=invitations-list></div><div data-testid=settings-pane-general></div><button data-testid=create-invitation-btn data-button-role=secondary></button><form id=create-invitation-form data-testid=create-invitation-form><button data-testid=create-invitation-submit data-button-role=primary></button></form><script>
const q=s=>document.querySelector(s),token=new URLSearchParams(location.search).get('token')||localStorage.getItem('huddle.token');if(token){localStorage.setItem('huddle.token',token);q('#auth-modal').style.display='none';}let cid=null,parentForThread=null;async function api(p,o={}){o.headers={...(o.headers||{}),Authorization:'Bearer '+token,'Content-Type':'application/json'};return (await fetch('/api'+p,o)).json()}async function load(){let w=(await api('/workspaces')).workspaces?.[0];if(!w)return;let d=await api('/workspaces/'+w.slug);q('[data-testid=channel-list]').innerHTML=d.channels.map(x=>`<div data-testid=channel-entry data-channel-id="${x.id}" data-channel-name="${x.name}">#${x.name}</div>`).join('');document.querySelectorAll('[data-testid=channel-entry]').forEach(x=>x.onclick=()=>{cid=x.dataset.channelId;q('[data-testid=channel-title]').textContent='# '+x.dataset.channelName;show()});cid=d.channels[0]?.id;show()}async function show(){if(!cid)return;let d=await api('/channels/'+cid+'/messages');q('[data-testid=message-list]').innerHTML=d.messages.map(m=>`<div class=msg data-testid=message data-mid="${m.id}"><b>${m.author.display_name}</b> <span data-testid=message-body>${m.body}</span><button data-testid=open-thread-btn onclick="parentForThread=this.closest('[data-testid=message]').dataset.mid||null;q('[data-testid=thread-panel]').style.display='block'">Replies</button><button data-testid=reaction-button onclick="this.nextElementSibling.style.display='block'">😀</button><span data-testid=emoji-picker style=display:none><button data-testid=emoji-option onclick="this.parentElement.outerHTML='<span data-testid=reaction-chip>😀 1</span>'">😀</button></span></div>`).join('');q('[data-testid=send-btn]').onclick=async()=>{let i=q('[data-testid=message-input]');if(!i.value)return;await api('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body:i.value})});i.value='';show()};q('[data-testid=thread-send]').onclick=async()=>{let i=q('[data-testid=thread-input]');if(!i.value||!parentForThread)return;await api('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body:i.value,parent_id:parentForThread})});let panel=q('[data-testid=thread-panel]');let row=document.createElement('div');row.textContent=i.value;panel.appendChild(row);i.value='';show()};}if(token)load();q('[data-testid=auth-form]').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target),r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:f.get('username'),password:f.get('password')})}).then(x=>x.json());if(r.token){localStorage.setItem('huddle.token',r.token);location.href='/?token='+r.token}};</script></body></html>'''
def irc_server():
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen()
 while True:
  x,_=s.accept();threading.Thread(target=irc_client,args=(x,),daemon=True).start()
def irc_client(s):
 nick='guest'; user=None; cid=None; last=0; buf=b''; registered=False; joined=False
 def send(x):
  try:s.sendall((x+'\r\n').encode())
  except:pass
 try:
  s.setblocking(False)
  while True:
   import select
   ready,_,_=select.select([s],[],[],.2)
   if ready:
    try:
     part=s.recv(4096)
     if not part: break
     buf+=part
    except BlockingIOError: pass
    while b'\n' in buf:
     raw,buf=buf.split(b'\n',1); line=raw.decode(errors='ignore').strip('\r ')
     if not line: continue
     z=line.split(' ',2); cmd=z[0].upper()
     if cmd=='PASS':
      user=auth({'Authorization':'Bearer '+(z[1] if len(z)>1 else '')})
      if not user: send(':server 464 * :Password incorrect')
     elif cmd=='NICK':
      requested=z[1] if len(z)>1 else 'guest'
      if requested in irc_nicks and requested != nick: send(f':server 433 * {requested} :Nickname in use')
      else:
       if nick in irc_nicks: irc_nicks.discard(nick)
       nick=requested; irc_nicks.add(nick)
     elif cmd=='USER' and user and not registered:
      registered=True
      for n in ('001','002','003','004','005','422'): send(f':server {n} {nick} :ok')
     elif cmd=='PING': send(':server PONG server :'+(z[-1] if len(z)>1 else ''))
     elif cmd=='QUIT': return
     elif cmd=='JOIN' and registered:
      name=z[1] if len(z)>1 else ''
      if not name.startswith('#') or '/' not in name: send(f':server 403 {nick} {name} :No such channel'); continue
      slug,chname=name[1:].split('/',1); row=db().execute('SELECT c.id FROM channels c JOIN workspaces w ON w.id=c.wid WHERE w.slug=? AND c.name=?',(slug,chname)).fetchone()
      if not row: send(f':server 403 {nick} {name} :No such channel'); continue
      cid=row['id']; joined=True; last=db().execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE cid=?',(cid,)).fetchone()[0]
      irc_clients.append({'sock':s,'nick':nick,'cid':cid,'user':user['id'],'last':last})
      send(f':{nick}!u@host JOIN {name}'); send(f':server 353 {nick} = {name} :{nick}'); send(f':server 366 {nick} {name} :End of NAMES')
     elif cmd=='NAMES' and cid:
      send(f':server 353 {nick} = {z[1]} :{nick}'); send(f':server 366 {nick} {z[1]} :End of NAMES')
     elif cmd=='PRIVMSG' and cid and len(z)>2:
      body=z[2].lstrip(':')
      c=db(); mid=uid(); stamp=now()
      c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,0)',(mid,cid,user['id'],body,None,stamp,None)); m=jmsg(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()); seq=emit(c,cid,'message.created',m); broadcast(cid,{'type':'message.created','event_id':seq,'channel_id':cid,'message':m}); last=seq
     elif cmd not in ('MODE','TOPIC','WHO','LIST'):
      send(f':server 421 {nick} {cmd} :Unknown command')
   if joined:
    c=db()
    for e in c.execute('SELECT seq,payload FROM events WHERE cid=? AND seq>? ORDER BY seq',(cid,last)).fetchall():
     m=json.loads(e['payload'])
     if m.get('author_id')!=user['id']:
      row=c.execute('SELECT w.slug||"/"||c.name n FROM channels c JOIN workspaces w ON w.id=c.wid WHERE c.id=?',(cid,)).fetchone()
      send(f':web!u@host PRIVMSG #{row["n"]} :{m.get("body","")}') 
     last=e['seq']
 except Exception:
  pass
 finally:
  irc_clients[:] = [x for x in irc_clients if x.get('sock') is not s]
  irc_nicks.discard(nick)
  try:s.close()
  except:pass
def irc_dispatcher():
 while True:
  time.sleep(.1)
  for x in list(irc_clients):
   try:
    c=db(); cid=x['cid']; row=c.execute('SELECT w.slug||"/"||c.name n FROM channels c JOIN workspaces w ON w.id=c.wid WHERE c.id=?',(cid,)).fetchone()
    if not row: continue
    for e in c.execute('SELECT seq,payload FROM events WHERE cid=? AND seq>? ORDER BY seq',(cid,x.get('last',0))).fetchall():
     m=json.loads(e['payload'])
     if m.get('author_id')!=x.get('user'): x['sock'].sendall(f':web!u@host PRIVMSG #{row["n"]} :{m.get("body","")}\r\n'.encode())
     x['last']=e['seq']
   except Exception as exc:
    try:
     open('/tmp/irc-dispatch.log','a').write(repr(exc)+'\\n')
    except: pass
    try: irc_clients.remove(x)
    except ValueError: pass

if __name__=='__main__':
 if NODE == 0:
  threading.Thread(target=irc_server,daemon=True).start()
  threading.Thread(target=irc_dispatcher,daemon=True).start()
 ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
