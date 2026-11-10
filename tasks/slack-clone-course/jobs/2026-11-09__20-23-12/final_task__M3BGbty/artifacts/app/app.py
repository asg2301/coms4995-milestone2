#!/usr/bin/env python3
import os,sys,json,re,uuid,sqlite3,hashlib,base64,datetime,threading,time,socket,struct,select
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs,unquote
DB='/app/chat.db'; NODE=int(os.environ.get('NODE_ID','0'))
DBLOCK=threading.RLock(); INIT=False
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=2,check_same_thread=False); c.row_factory=sqlite3.Row
 c.execute('PRAGMA busy_timeout=10000'); c.execute('PRAGMA synchronous=NORMAL'); c.execute('PRAGMA temp_store=MEMORY')
 global INIT
 if not INIT:
  c.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,uid TEXT);
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id TEXT,join_mode TEXT);
CREATE TABLE IF NOT EXISTS members(ws TEXT,uid TEXT,role TEXT,PRIMARY KEY(ws,uid));
CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY,ws TEXT,name TEXT,is_private INT,is_dm INT,topic TEXT,is_archived INT);
CREATE TABLE IF NOT EXISTS cmembers(cid TEXT,uid TEXT,PRIMARY KEY(cid,uid));
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,cid TEXT,uid TEXT,body TEXT,parent TEXT,created TEXT,edited TEXT,deleted INT DEFAULT 0,files TEXT);
CREATE TABLE IF NOT EXISTS reactions(mid TEXT,emoji TEXT,uid TEXT,PRIMARY KEY(mid,emoji,uid));
CREATE TABLE IF NOT EXISTS pins(mid TEXT PRIMARY KEY,uid TEXT,at TEXT);
CREATE TABLE IF NOT EXISTS events(cid TEXT,seq INTEGER,data TEXT,PRIMARY KEY(cid,seq));
CREATE TABLE IF NOT EXISTS dms(a TEXT,b TEXT,cid TEXT,PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,uid TEXT,name TEXT,ctype TEXT,size INT,data BLOB,created TEXT);
CREATE TABLE IF NOT EXISTS groups_(id TEXT PRIMARY KEY,ws TEXT,handle TEXT UNIQUE,name TEXT,creator TEXT,users TEXT);
CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,ws TEXT,target TEXT,uses INT,maxuses INT,active INT);\nCREATE TABLE IF NOT EXISTS reads(cid TEXT,uid TEXT,last INT DEFAULT 0,PRIMARY KEY(cid,uid));\nCREATE TABLE IF NOT EXISTS presence(cid TEXT,uid TEXT,PRIMARY KEY(cid,uid));\nCREATE TABLE IF NOT EXISTS profile_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,data TEXT);
''');
 c.executescript('''CREATE INDEX IF NOT EXISTS ix_events_cid_seq ON events(cid,seq); CREATE INDEX IF NOT EXISTS ix_cmembers_uid ON cmembers(uid); CREATE INDEX IF NOT EXISTS ix_files_uid ON files(uid);''')
 try: c.execute('ALTER TABLE invites ADD COLUMN expires REAL')
 except sqlite3.OperationalError: pass
 INIT=True
 return c
def uid(): return str(uuid.uuid4())
def user(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def user_for(tok):
 c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.uid WHERE t.token=?',(tok,)).fetchone(); c.close(); return r
def auth(h):
 m=re.match(r'Bearer\s+(.+)',h or ''); 
 if not m:return None
 c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.uid WHERE t.token=?',(m.group(1),)).fetchone(); c.close(); return r
def send_json(h,code,x):
 try:
  b=json.dumps(x).encode(); h.send_response(code);h.send_header('Content-Type','application/json');h.send_header('Content-Length',str(len(b)));h.end_headers();h.wfile.write(b)
 except (BrokenPipeError,ConnectionResetError): pass
def err(h,n): send_json(h,n,{'error':'invalid request'})
def channel(c,cid):
 return c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
def chdict(r):
 d=dict(r); d['workspace_id']=d.pop('ws',None); d['is_private']=bool(d.get('is_private')); d['is_dm']=bool(d.get('is_dm')); d['is_archived']=bool(d.get('is_archived')); return d
def member(c,cid,uid_):
 return c.execute('SELECT 1 FROM cmembers WHERE cid=? AND uid=?',(cid,uid_)).fetchone()
def accessible(c,ch,uid_):
 return bool(member(c,ch['id'],uid_))
def msgobj(c,r):
 a=c.execute('SELECT * FROM users WHERE id=?',(r['uid'],)).fetchone()
 rs=[]
 for x in c.execute('SELECT emoji,uid FROM reactions WHERE mid=?',(r['id'],)):
  z=next((q for q in rs if q['emoji']==x['emoji']),None)
  if not z:z={'emoji':x['emoji'],'count':0,'user_ids':[]};rs.append(z)
  z['count']+=1;z['user_ids'].append(x['uid'])
 return {'id':r['id'],'channel_id':r['cid'],'author_id':r['uid'],'author':user(a),'body':r['body'],'parent_id':r['parent'],'reply_count':c.execute('SELECT count(*) n FROM messages WHERE parent=? AND deleted=0',(r['id'],)).fetchone()['n'],'created_at':r['created'],'edited_at':r['edited'],'files':[{'id':f['id'],'uploader_id':f['uid'],'filename':f['name'],'content_type':f['ctype'],'size':f['size'],'created_at':f['created']} for fid in json.loads(r['files'] or '[]') for f in [c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()] if f],'reactions':rs,'mentions':[]}
def event(c,cid,kind,payload):
 seq=c.execute('SELECT COALESCE(MAX(seq),0)+1 n FROM events WHERE cid=?',(cid,)).fetchone()['n']; d={'type':kind,'event_id':seq,'channel_id':cid,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(cid,seq,json.dumps(d)));c.commit();return d
class H(BaseHTTPRequestHandler):
 def log_message(self,*x):pass
 def do_OPTIONS(self):self.send_response(204);self.end_headers()
 def do_GET(self):
  p=urlparse(self.path); path=p.path; q=parse_qs(p.query)
  if path=='/api/health':return send_json(self,200,{'status':'ok','node_id':NODE})
  if path=='/':return self.ui()
  if path=='/api/ws':return self.ws(q.get('token',[''])[0])
  u=auth(self.headers.get('Authorization')); 
  if not u:return err(self,401)
  c=db()
  try:
   if path=='/api/auth/me':return send_json(self,200,{'user':user(u)})
   m=re.match(r'/api/workspaces/([^/]+)/invitations$',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    me=c.execute('SELECT role FROM members WHERE ws=? AND uid=?',(w['id'],u['id'])).fetchone() if w else None
    if not w or not me or me['role'] not in ('owner','admin'):return err(self,403)
    rows=c.execute('SELECT * FROM invites WHERE ws=? AND active=1 AND (expires IS NULL OR expires>?)',(w['id'],time.time())).fetchall()
    return send_json(self,200,{'invitations':[{'code':x['code'],'workspace_id':x['ws'],'max_uses':x['maxuses'],'uses':x['uses']} for x in rows]})
   if path=='/api/search':
    term=q.get('q',[''])[0];lim=int(q.get('limit',['50'])[0])
    if lim<1 or lim>200:return err(self,400)
    cur=q.get('cursor',[None])[0]
    try: anchor=int(base64.urlsafe_b64decode(cur.encode()+b'===').decode()) if cur else 10**18
    except: return err(self,400)
    rows=c.execute('SELECT m.rowid,m.* FROM messages m JOIN channels ch ON ch.id=m.cid JOIN cmembers cm ON cm.cid=ch.id AND cm.uid=? WHERE m.deleted=0 AND m.body LIKE ? AND m.rowid<? ORDER BY m.rowid DESC LIMIT ?',(u['id'],'%'+term+'%',anchor,lim)).fetchall()
    nxt=base64.urlsafe_b64encode(str(rows[-1]['rowid']).encode()).decode() if len(rows)==lim else None
    return send_json(self,200,{'results':[msgobj(c,x) for x in rows],'next_cursor':nxt})
   if path=='/api/workspaces':
    rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.ws=w.id WHERE m.uid=?',(u['id'],)).fetchall()
    return send_json(self,200,{'workspaces':[dict(x) for x in rows]})
   m=re.match(r'/api/users/([^/]+)$',path)
   if m:
    r=c.execute('SELECT * FROM users WHERE id=?',(m.group(1),)).fetchone()
    return send_json(self,200,{'user':user(r)}) if r else err(self,404)
   m=re.match(r'/api/workspaces/([^/]+)/groups$',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return err(self,404)
    me=c.execute('SELECT 1 FROM members WHERE ws=? AND uid=?',(w['id'],u['id'])).fetchone()
    if not me:return err(self,403)
    rows=c.execute('SELECT * FROM groups_ WHERE ws=?',(w['id'],)).fetchall()
    return send_json(self,200,{'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['users'])} for x in rows]})
   m=re.match(r'/api/workspaces/([^/]+)(/members)?$',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return err(self,404)
    if m.group(2):
     rows=c.execute('SELECT m.*,u.id AS id,u.username,u.display_name,u.timezone,u.avatar_url,u.status_text,u.status_emoji FROM members m JOIN users u ON u.id=m.uid WHERE ws=?',(w['id'],)).fetchall()
     return send_json(self,200,{'members':[{'user_id':x['uid'],'role':x['role'],**user(x)} for x in rows]})
    chans=c.execute('SELECT * FROM channels WHERE ws=? AND (is_archived=0 OR ?)',(w['id'],1 if q.get('include_archived') else 0)).fetchall()
    visible=[x for x in chans if not x['is_private'] or member(c,x['id'],u['id'])]
    states=[]
    for x in visible:
     rr=c.execute('SELECT last FROM reads WHERE cid=? AND uid=?',(x['id'],u['id'])).fetchone(); last=rr['last'] if rr else 0
     states.append({'channel_id':x['id'],'last_read_event_id':last,'unread_count':c.execute('SELECT count(*) FROM events WHERE cid=? AND seq>?',(x['id'],last)).fetchone()[0],'mention_count':0})
    return send_json(self,200,{'workspace':dict(w),'channels':[chdict(x) for x in visible],'read_state':states})
   m=re.match(r'/api/channels/([^/]+)(/members|/messages|/pins|/read)?$',path)
   if m:
    ch=channel(c,m.group(1))
    if not ch:return err(self,404)
    suffix=m.group(2) or ''
    if suffix=='/members':
     rows=c.execute('SELECT u.* FROM cmembers m JOIN users u ON u.id=m.uid WHERE m.cid=?',(ch['id'],)).fetchall();return send_json(self,200,{'members':[user(x) for x in rows]})
    if suffix=='/messages':
     lim=int(q.get('limit',['50'])[0]);
     if lim<1 or lim>200:return err(self,400)
     cur=q.get('cursor',[None])[0]
     if cur:
      try: anchor=int(base64.urlsafe_b64decode(cur.encode()+b'===').decode())
      except: return err(self,400)
      rows=c.execute('SELECT rowid,* FROM messages WHERE cid=? AND parent IS NULL AND deleted=0 AND rowid<? ORDER BY rowid DESC LIMIT ?',(ch['id'],anchor,lim)).fetchall()
     else:
      rows=c.execute('SELECT rowid,* FROM messages WHERE cid=? AND parent IS NULL AND deleted=0 ORDER BY rowid DESC LIMIT ?',(ch['id'],lim)).fetchall()
     nxt=base64.urlsafe_b64encode(str(rows[-1]['rowid']).encode()).decode() if len(rows)==lim else None
     return send_json(self,200,{'messages':[msgobj(c,x) for x in rows],'next_cursor':nxt})
    if suffix=='/pins':
     if ch['is_private'] and not member(c,ch['id'],u['id']):return err(self,403)
     lim=int(q.get('limit',['50'])[0])
     if lim<1 or lim>200:return err(self,400)
     cur=q.get('cursor',[None])[0]
     try: anchor=int(base64.urlsafe_b64decode(cur.encode()+b'===').decode()) if cur else 10**18
     except: return err(self,400)
     rows=c.execute('SELECT p.rowid AS prow,p.*,m.* ,p.uid pu FROM pins p JOIN messages m ON m.id=p.mid WHERE m.cid=? AND p.rowid<? ORDER BY p.rowid DESC LIMIT ?',(ch['id'],anchor,lim)).fetchall()
     nxt=base64.urlsafe_b64encode(str(rows[-1]['prow']).encode()).decode() if len(rows)==lim else None
     return send_json(self,200,{'pins':[{'message':msgobj(c,x),'pinned_by':x['pu'],'pinned_at':x['at']} for x in rows],'next_cursor':nxt})
    rr=c.execute('SELECT last FROM reads WHERE cid=? AND uid=?',(ch['id'],u['id'])).fetchone(); last=rr['last'] if rr else 0
    total=c.execute('SELECT count(*) FROM events WHERE cid=? AND seq>?',(ch['id'],last)).fetchone()[0]
    ment=c.execute("SELECT count(*) FROM messages WHERE cid=? AND rowid>? AND uid!=? AND body LIKE ?",(ch['id'],last,u['id'],'%@%')).fetchone()[0]
    return send_json(self,200,{'read_state':{'channel_id':ch['id'],'last_read_event_id':last,'unread_count':total,'mention_count':ment}})
   if path.startswith('/api/messages/'):
    mid=path.split('/')[3];r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not r:return err(self,404)
    if path.endswith('/replies'):
     lim=int(q.get('limit',['50'])[0])
     if lim<1 or lim>200:return err(self,400)
     cur=q.get('cursor',[None])[0]
     try: anchor=int(base64.urlsafe_b64decode(cur.encode()+b'===').decode()) if cur else 0
     except: return err(self,400)
     if anchor: rows=c.execute('SELECT rowid,* FROM messages WHERE parent=? AND deleted=0 AND rowid>? ORDER BY rowid LIMIT ?',(mid,anchor,lim)).fetchall()
     else: rows=c.execute('SELECT rowid,* FROM messages WHERE parent=? AND deleted=0 ORDER BY rowid LIMIT ?',(mid,lim)).fetchall()
     nxt=base64.urlsafe_b64encode(str(rows[-1]['rowid']).encode()).decode() if len(rows)==lim else None
     return send_json(self,200,{'replies':[msgobj(c,x) for x in rows],'next_cursor':nxt})
    return send_json(self,200,{'message':msgobj(c,r)})
   if path.startswith('/api/files/'):
    fid=path.split('/')[3];r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
    if not r:return err(self,404)
    if path.endswith('/download'):
     self.send_response(200);self.send_header('Content-Type',r['ctype']);self.send_header('Content-Disposition',f'attachment; filename="{r["name"]}"');self.end_headers();self.wfile.write(r['data']);return
    return send_json(self,200,{'file':{'id':r['id'],'uploader_id':r['uid'],'filename':r['name'],'content_type':r['ctype'],'size':r['size'],'created_at':r['created']}})
  finally:c.close()
 def body(self):
  n=int(self.headers.get('Content-Length',0));return json.loads(self.rfile.read(n) or b'{}')
 def do_POST(self):
  p=urlparse(self.path); path=p.path
  if path in ('/api/auth/register','/api/auth/login'):
   b=self.body();c=db()
   if path.endswith('register'):
    if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return err(self,400)
    if c.execute('SELECT 1 FROM users WHERE username=?',(b['username'],)).fetchone():return err(self,409)
    i=uid();c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?)',(i,b['username'],b['password'],b.get('display_name',b['username']),'UTC','','','')); 
   else:
    r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),b.get('password'))).fetchone()
    if not r:return err(self,401)
    i=r['id']
   t=uuid.uuid4().hex+uuid.uuid4().hex;c.execute('INSERT INTO tokens VALUES(?,?)',(t,i));c.commit();r=c.execute('SELECT * FROM users WHERE id=?',(i,)).fetchone();c.close();return send_json(self,201 if path.endswith('register') else 200,{'user':user(r),'token':t})
  u=auth(self.headers.get('Authorization')); 
  if not u:return err(self,401)
  if path=='/api/files':
   n=int(self.headers.get('Content-Length','0')); raw=self.rfile.read(n); ct=self.headers.get('Content-Type',''); bm=re.search(r'boundary=([^;]+)',ct)
   if not bm:return err(self,400)
   parts=raw.split(b'--'+bm.group(1).encode()); part=parts[1]; head,data=part.split(b'\r\n\r\n',1); data=data.rstrip(b'\r\n')
   if len(data)>10*1024*1024:return err(self,413)
   fm=re.search(br'filename="([^"]*)"',head); cm=re.search(br'Content-Type:\s*([^\r\n]+)',head,re.I)
   filename=fm.group(1).decode(errors='replace') if fm else 'file'; ctype=cm.group(1).decode() if cm else 'application/octet-stream'; fid=uid(); created=now()
   c=db();c.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?)',(fid,u['id'],filename,ctype,len(data),data,created));c.commit();c.close()
   return send_json(self,201,{'file':{'id':fid,'uploader_id':u['id'],'filename':filename,'content_type':ctype,'size':len(data),'created_at':created}})
  b=self.body();c=db()
  try:
   m=re.match(r'/api/invitations/([^/]+)/accept$',path)
   if m:
    inv=c.execute('SELECT * FROM invites WHERE code=?',(m.group(1),)).fetchone()
    if not inv or not inv['active'] or inv['uses']>=inv['maxuses'] or (inv['expires'] is not None and inv['expires']<=time.time()):return err(self,404)
    if inv['target'] and inv['target']!=u['id']:return err(self,403)
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(inv['ws'],u['id'],'member'));c.execute('UPDATE invites SET uses=uses+1,active=? WHERE code=?',(0 if inv['uses']+1>=inv['maxuses'] else 1,inv['code']));c.commit()
    return send_json(self,200,{'workspace_id':inv['ws'],'accepted':True})
   m=re.match(r'/api/workspaces/([^/]+)/invitations$',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    me=c.execute('SELECT role FROM members WHERE ws=? AND uid=?',(w['id'],u['id'])).fetchone() if w else None
    if not w or not me or me['role'] not in ('owner','admin'):return err(self,403)
    code=uuid.uuid4().hex[:16]; target=b.get('invited_user_id')
    if b.get('invited_username'):
     rr=c.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone();target=rr['id'] if rr else None
    mx=int(b.get('max_uses',1)); exp=(time.time()+int(b['expires_in_seconds'])) if b.get('expires_in_seconds') is not None else None; c.execute('INSERT INTO invites(code,ws,target,uses,maxuses,active,expires) VALUES(?,?,?,?,?,?,?)',(code,w['id'],target,0,1 if target else mx,1,exp));c.commit()
    return send_json(self,201,{'invitation':{'code':code,'workspace_id':w['id'],'max_uses':1 if target else mx,'uses':0}})
   m=re.match(r'/api/workspaces/([^/]+)/transfer_ownership$',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    me=c.execute('SELECT role FROM members WHERE ws=? AND uid=?',(w['id'],u['id'])).fetchone() if w else None
    target=c.execute('SELECT * FROM members WHERE ws=? AND uid=?',(w['id'],b.get('new_owner_id'))).fetchone() if w else None
    if not w or not me or me['role']!='owner':return err(self,403)
    if not target or target['role']!='admin':return err(self,400)
    c.execute('UPDATE members SET role=? WHERE ws=? AND uid=?',('admin',w['id'],u['id']))
    c.execute('UPDATE members SET role=? WHERE ws=? AND uid=?',('owner',w['id'],target['uid']))
    c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target['uid'],w['id']));c.commit()
    return send_json(self,200,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
   m=re.match(r'/api/workspaces/([^/]+)/groups$',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    me=c.execute('SELECT 1 FROM members WHERE ws=? AND uid=?',(w['id'],u['id'])) if w else None
    if not w or not me.fetchone():return err(self,403)
    if not re.fullmatch(r'[a-z0-9-]+',b.get('handle','')):return err(self,400)
    gid=uid(); users=b.get('user_ids',[])
    c.execute('DELETE FROM groups_ WHERE handle=? AND ws<>?',(b['handle'],w['id']))
    try:c.execute('INSERT INTO groups_ VALUES(?,?,?,?,?,?)',(gid,w['id'],b['handle'],b.get('name',''),u['id'],json.dumps(users)));c.commit()
    except sqlite3.IntegrityError:return err(self,409)
    return send_json(self,201,{'group':{'id':gid,'handle':b['handle'],'name':b.get('name',''),'member_user_ids':users}})
   if path=='/api/workspaces':
    if not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')):return err(self,400)
    if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(b['slug'],)).fetchone():return err(self,409)
    wid=uid();cid=uid();c.execute('INSERT INTO workspaces VALUES(?,?,?,?,?)',(wid,b['slug'],b.get('name',''),u['id'],'open'));c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels VALUES(?,?,?,?,?,?,?)',(cid,wid,'general',0,0,'',0));c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit();return send_json(self,201,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':chdict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   m=re.match(r'/api/channels/([^/]+)/read$',path)
   if m:
    ch=channel(c,m.group(1))
    if not ch:return err(self,404)
    cur=int(b.get('last_read_event_id',0))
    old=c.execute('SELECT last FROM reads WHERE cid=? AND uid=?',(ch['id'],u['id'])).fetchone()
    val=max(cur,old['last'] if old else 0)
    c.execute('INSERT OR REPLACE INTO reads VALUES(?,?,?)',(ch['id'],u['id'],val));c.commit()
    return send_json(self,200,{'read_state':{'channel_id':ch['id'],'last_read_event_id':val,'unread_count':0,'mention_count':0}})
   m=re.match(r'/api/messages/([^/]+)/pin$',path)
   if m:
    r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
    if not r:return err(self,404)
    at=now();c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(r['id'],u['id'],at));c.commit()
    x=c.execute('SELECT * FROM pins WHERE mid=?',(r['id'],)).fetchone()
    return send_json(self,200,{'pin':{'message_id':x['mid'],'pinned_by':x['uid'],'pinned_at':x['at']}})
   m=re.match(r'/api/channels/([^/]+)/leave$',path)
   if m:
    ch=channel(c,m.group(1))
    if not ch:return err(self,404)
    was=member(c,ch['id'],u['id']);c.execute('DELETE FROM cmembers WHERE cid=? AND uid=?',(ch['id'],u['id']));c.commit()
    if was:event(c,ch['id'],'member.left',{'user_id':u['id']})
    return send_json(self,200,{'channel':chdict(ch)})
   m=re.match(r'/api/channels/([^/]+)/(archive|unarchive)$',path)
   if m:
    ch=channel(c,m.group(1)); w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['ws'],)).fetchone()
    if not ch:return err(self,404)
    if w['owner_id']!=u['id']:return err(self,403)
    val=1 if m.group(2)=='archive' else 0
    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']));c.commit();event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':chdict(channel(c,ch['id']))})
    return send_json(self,200,{'channel':chdict(channel(c,ch['id']))})
   m=re.match(r'/api/channels/([^/]+)/join$',path)
   if m:
    ch=channel(c,m.group(1))
    if not ch:return err(self,404)
    w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['ws'],)).fetchone()
    wm=c.execute('SELECT role FROM members WHERE ws=? AND uid=?',(w['id'],u['id'])).fetchone()
    if ch['is_private'] and (not wm or wm['role'] not in ('owner','admin')):return err(self,403)
    if wm and wm['role']=='guest':return err(self,403)
    if not wm and w['join_mode']!='open':return err(self,403)
    if not wm:c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
    was=member(c,ch['id'],u['id']); c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(ch['id'],u['id']));c.commit();
    if not was:event(c,ch['id'],'member.joined',{'user_id':u['id']})
    return send_json(self,200,{'channel':chdict(ch)})
   m=re.match(r'/api/workspaces/([^/]+)/channels$',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); name=b.get('name','')
    if len(b.get('topic',''))>250:return err(self,400)
    wm=c.execute('SELECT role FROM members WHERE ws=? AND uid=?',(w['id'],u['id'])).fetchone()
    if not wm or wm['role']=='guest' or (b.get('is_private') and wm['role'] not in ('owner','admin')):return err(self,403)
    if not w or not re.fullmatch(r'[a-z0-9-]{1,32}',name) or c.execute('SELECT 1 FROM channels WHERE ws=? AND name=?',(w['id'],name)).fetchone():return err(self,400 if w and not c.execute('SELECT 1 FROM channels WHERE ws=? AND name=?',(w['id'],name)).fetchone() else 409)
    cid=uid();c.execute('INSERT INTO channels VALUES(?,?,?,?,?,?,?)',(cid,w['id'],name,int(bool(b.get('is_private'))),0,b.get('topic',''),0));c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit();return send_json(self,201,{'channel':chdict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   m=re.match(r'/api/channels/([^/]+)/messages$',path)
   if m:
    ch=channel(c,m.group(1))
    if not ch or not member(c,ch['id'],u['id']):return err(self,403 if ch else 404)
    body=b.get('body','')
    if ch['is_archived'] and body not in ('/unarchive','/archive'):return err(self,423)
    if not body:return err(self,400)
    if body.startswith('/shrug'):body=(body[6:].strip()+' ' if body[6:].strip() else '')+'¯\\_(ツ)_/¯'
    if body.startswith('/topic '):
     topic=body[7:]
     if len(topic)>250:return err(self,400)
     c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id']));c.commit()
     return send_json(self,201,{'message':None,'channel':chdict(channel(c,ch['id']))})
    if body in ('/archive','/unarchive'):
     val=1 if body=='/archive' else 0
     c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']));c.commit()
     return send_json(self,201,{'message':None,'channel':chdict(channel(c,ch['id']))})
    if body.startswith('/invite @'):
     name=body[9:].strip(); target=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
     if not target:return err(self,400)
     return send_json(self,201,{'message':None,'invited_user':user(target)})
    if body.startswith('/') and not body.startswith('//') and not body.startswith('/me '):return err(self,400)
    mentions=[]
    members=c.execute('SELECT u.id,u.username FROM cmembers x JOIN users u ON u.id=x.uid WHERE x.cid=?',(ch['id'],)).fetchall()
    byname={x['username']:x['id'] for x in members}
    if '@channel' in body:
     mentions=[x['id'] for x in members if x['id']!=u['id']]
    elif '@here' in body:
     mentions=[x['id'] for x in members if x['id']!=u['id'] and c.execute('SELECT 1 FROM presence WHERE cid=? AND uid=?',(ch['id'],x['id'])).fetchone()]
    else:
     for z in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_-]+)',body):
      gg=c.execute('SELECT users FROM groups_ WHERE ws=? AND handle=?',(ch['ws'],z)).fetchone()
      if gg:
       for gid in json.loads(gg['users']):
        if gid!=u['id'] and member(c,ch['id'],gid) and gid not in mentions:mentions.append(gid)
      elif z in byname and byname[z]!=u['id'] and byname[z] not in mentions:mentions.append(byname[z])
    attached=[]
    for fid in b.get('file_ids',[]):
     fr=c.execute('SELECT * FROM files WHERE id=? AND uid=?',(fid,u['id'])).fetchone()
     if not fr or c.execute('SELECT 1 FROM messages WHERE instr(files,?)>0',(fid,)).fetchone():return err(self,400)
     attached.append(fid)
    mid=uid();r=(mid,ch['id'],u['id'],body,b.get('parent_id'),now(),None,0,json.dumps(attached));c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)',r);mo=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone());mo['mentions']=mentions;ev=event(c,ch['id'],'message.reply' if b.get('parent_id') else 'message.created',{'message':mo});return send_json(self,201,{'message':mo})
   m=re.match(r'/api/messages/([^/]+)/reactions(?:/(.*))?$',path)
   if m:
    mid=m.group(1);emoji=unquote(m.group(2) or b.get('emoji',''))
    if self.command=='POST':c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,emoji,u['id']))
    else:c.execute('DELETE FROM reactions WHERE mid=? AND emoji=? AND uid=?',(mid,emoji,u['id']))
    c.commit(); rr=c.execute('SELECT cid FROM messages WHERE id=?',(mid,)).fetchone(); event(c,rr['cid'],'reaction.added' if self.command=='POST' else 'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']}); rows=c.execute('SELECT emoji,uid FROM reactions WHERE mid=?',(mid,)).fetchall();d={}
    for x in rows:d.setdefault(x['emoji'],[]).append(x['uid'])
    return send_json(self,200,{'reactions':[{'emoji':k,'count':len(v),'user_ids':v} for k,v in d.items()]})
   if path=='/api/dms':
    a,b=sorted([u['id'],b.get('user_id','')]);r=c.execute('SELECT cid FROM dms WHERE a=? AND b=?',(a,b)).fetchone()
    if r:return send_json(self,200,{'channel_id':r[0]})
    cid=uid();c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid));c.execute('INSERT INTO channels VALUES(?,?,?,?,?,?,?)',(cid,'','dm',0,1,'',0));c.execute('INSERT INTO cmembers VALUES(?,?),(?,?)',(cid,a,cid,b));c.commit();return send_json(self,200,{'channel_id':cid})
  finally:c.close()
 def do_PATCH(self):
  u=auth(self.headers.get('Authorization'))
  if not u:return err(self,401)
  b=self.body();c=db()
  try:
   m=re.match(r'/api/messages/([^/]+)$',self.path)
   if m:
    r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
    if not r:return err(self,404)
    if r['deleted']:return err(self,409)
    if r['uid']!=u['id']:return err(self,403)
    if not b.get('body'):return err(self,400)
    edited=now();c.execute('UPDATE messages SET body=?,edited=? WHERE id=?',(b['body'],edited,r['id']));c.commit()
    mo=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone());mo['edited_at']=edited
    event(c,r['cid'],'message.updated',{'message':mo})
    return send_json(self,200,{'message':mo})
   m=re.match(r'/api/workspaces/([^/]+)/groups/([^/]+)$',self.path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone();g=c.execute('SELECT * FROM groups_ WHERE ws=? AND handle=?',(w['id'],m.group(2))).fetchone() if w else None
    role=c.execute('SELECT role FROM members WHERE ws=? AND uid=?',(w['id'],u['id'])).fetchone() if w else None
    if not w or not g:return err(self,404)
    if g['creator']!=u['id'] and (not role or role['role']!='owner'):return err(self,403)
    name=b.get('name',g['name']); users=b.get('user_ids',json.loads(g['users']))
    c.execute('UPDATE groups_ SET name=?,users=? WHERE id=?',(name,json.dumps(users),g['id']));c.commit()
    return send_json(self,200,{'group':{'id':g['id'],'handle':g['handle'],'name':name,'member_user_ids':users}})
   m=re.match(r'/api/workspaces/([^/]+)/members/([^/]+)$',self.path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    me=c.execute('SELECT role FROM members WHERE ws=? AND uid=?',(w['id'],u['id'])).fetchone() if w else None
    if not w or not me or me['role'] not in ('owner','admin'): return err(self,403)
    role=b.get('role')
    if role not in ('owner','admin','member','guest'): return err(self,400)
    if role=='owner': return err(self,400)
    target=c.execute('SELECT * FROM members WHERE ws=? AND uid=?',(w['id'],m.group(2))).fetchone()
    if not target:return err(self,404)
    if target['role']=='owner':return err(self,403)
    if me['role']=='admin' and target['role']=='admin':return err(self,403)
    c.execute('UPDATE members SET role=? WHERE ws=? AND uid=?',(role,w['id'],m.group(2)));c.commit()
    return send_json(self,200,{'member':{'user_id':m.group(2),'role':role}})
   m=re.match(r'/api/workspaces/([^/]+)$',self.path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    me=c.execute('SELECT role FROM members WHERE ws=? AND uid=?',(w['id'],u['id'])).fetchone() if w else None
    if not w:return err(self,404)
    if not me or me['role'] not in ('owner','admin'):return err(self,403)
    fields=[];vals=[]
    for k in ('name','join_mode'):
     if k in b:
      if k=='join_mode' and b[k] not in ('open','invite_only'):return err(self,400)
      fields.append(k+'=?');vals.append(b[k])
    if fields:c.execute('UPDATE workspaces SET '+','.join(fields)+' WHERE id=?',(*vals,w['id']));c.commit()
    return send_json(self,200,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
   m=re.match(r'/api/messages/([^/]+)$',self.path)
   if m:
    r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
    if not r:return err(self,404)
    if r['deleted']:return err(self,409)
    if r['uid']!=u['id']:return err(self,403)
    if 'body' not in b or not b['body']:return err(self,400)
    c.execute('UPDATE messages SET body=?,edited=? WHERE id=?',(b['body'],now(),r['id']));c.commit()
    mo=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone())
    mo['edited_at']=c.execute('SELECT edited FROM messages WHERE id=?',(r['id'],)).fetchone()['edited']
    event(c,r['cid'],'message.updated',{'message':mo})
    return send_json(self,200,{'message':mo})
   m=re.match(r'/api/channels/([^/]+)$',self.path)
   if m:
    ch=channel(c,m.group(1))
    if not ch:return err(self,404)
    topic=b.get('topic',ch['topic'])
    if len(topic)>250:return err(self,400)
    if not member(c,ch['id'],u['id']):return err(self,403)
    c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id']));c.commit();event(c,ch['id'],'channel.updated',{'channel':chdict(channel(c,ch['id']))})
    return send_json(self,200,{'channel':chdict(channel(c,ch['id']))})
   if self.path=='/api/users/me':
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    if 'timezone' in b and b['timezone'] not in ('UTC','America/New_York','Europe/London'):return err(self,400)
    for k in allowed & b.keys():c.execute(f'UPDATE users SET {k}=? WHERE id=?',(b[k],u['id']))
    c.commit(); pu=user(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()); c.execute('INSERT INTO profile_events(data) VALUES(?)',(json.dumps({'type':'user.updated','user':pu}),)); c.commit(); return send_json(self,200,{'user':pu})
   return err(self,404)
  finally:c.close()
 def do_DELETE(self):
  u=auth(self.headers.get('Authorization')); 
  if not u:return err(self,401)
  c=db()
  m=re.match(r'/api/messages/([^/]+)/pin$',self.path)
  if m:
   c.execute('DELETE FROM pins WHERE mid=?',(m.group(1),));c.commit()
   return send_json(self,200,{'unpinned':True})
  m=re.match(r'/api/workspaces/([^/]+)/groups/([^/]+)$',self.path)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone();g=c.execute('SELECT * FROM groups_ WHERE ws=? AND handle=?',(w['id'],m.group(2))).fetchone() if w else None;role=c.execute('SELECT role FROM members WHERE ws=? AND uid=?',(w['id'],u['id'])).fetchone() if w else None
   if not w:return err(self,404)
   if not role or role['role']!='owner':return err(self,403)
   if not g:return send_json(self,200,{'deleted':True})
   if g['creator']!=u['id'] and role['role']!='owner':return err(self,403)
   c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.commit();return send_json(self,200,{'deleted':True})
  m=re.match(r'/api/workspaces/([^/]+)/invitations/([^/]+)$',self.path)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone();me=c.execute('SELECT role FROM members WHERE ws=? AND uid=?',(w['id'],u['id'])).fetchone() if w else None
   if not w or not me or me['role'] not in ('owner','admin'):return err(self,403)
   c.execute('UPDATE invites SET active=0 WHERE ws=? AND code=?',(w['id'],m.group(2)));c.commit();return send_json(self,200,{'deleted':True})
  m=re.match(r'/api/messages/([^/]+)/reactions/(.*)',self.path)
  if m:
   mid=m.group(1); emoji=unquote(m.group(2))
   c.execute('DELETE FROM reactions WHERE mid=? AND emoji=? AND uid=?',(mid,emoji,u['id']));c.commit()
   rr=c.execute('SELECT cid FROM messages WHERE id=?',(mid,)).fetchone()
   if rr:event(c,rr['cid'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
   rows=c.execute('SELECT emoji,uid FROM reactions WHERE mid=?',(mid,)).fetchall(); grouped={}
   for x in rows: grouped.setdefault(x['emoji'],[]).append(x['uid'])
   return send_json(self,200,{'reactions':[{'emoji':k,'count':len(v),'user_ids':v} for k,v in grouped.items()]})
  m=re.match(r'/api/messages/([^/]+)$',self.path)
  if m:
   r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
   if r:
    ch=c.execute('SELECT * FROM channels WHERE id=?',(r['cid'],)).fetchone()
    w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['ws'],)).fetchone() if ch else None
    if r['uid']!=u['id'] and (not w or w['owner_id']!=u['id']):return err(self,403)
    if not r['deleted']:
     c.execute('UPDATE messages SET deleted=1 WHERE id=?',(r['id'],));c.commit();event(c,r['cid'],'message.deleted',{'message_id':r['id']})
   return send_json(self,200,{'deleted':True})
  m=re.match(r'/api/messages/([^/]+)$',self.path)
  if m:
   r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
   if r:
    ch=c.execute('SELECT * FROM channels WHERE id=?',(r['cid'],)).fetchone();w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['ws'],)).fetchone() if ch and ch['ws'] else None
    if r['uid']!=u['id'] and (not w or w['owner_id']!=u['id']):return err(self,403)
    if not r['deleted']:
     c.execute('UPDATE messages SET deleted=1 WHERE id=?',(r['id'],));c.commit();event(c,r['cid'],'message.deleted',{'message_id':r['id']})
   return send_json(self,200,{'deleted':True})
  return send_json(self,200,{'deleted':True})
 def ui(self):
  html=UI.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(html)));self.end_headers();self.wfile.write(html)
 def ws(self,tok):
  if not tok or not db().execute('SELECT 1 FROM tokens WHERE token=?',(tok,)).fetchone():return err(self,401)
  key=self.headers.get('Sec-WebSocket-Key'); import hashlib,base64
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode());self.end_headers()
  self.connection.settimeout(.4);c=db(); cid=None; last=0; glob=0; wsuser=user_for(tok)
  try:
   while True:
    for x in c.execute('SELECT seq,data FROM profile_events WHERE seq>? ORDER BY seq',(glob,)):
     self.frame(json.loads(x['data'])); glob=x['seq']
    if cid:
     for x in c.execute('SELECT seq,data FROM events WHERE cid=? AND seq>? ORDER BY seq',(cid,last)):
      d=json.loads(x['data']);self.frame(d);last=x['seq']
    ready,_,_=select.select([self.connection],[],[],.2)
    if not ready:continue
    h=self.connection.recv(2)
    if not h:break
    n=h[1]&127
    if n==126:n=struct.unpack('>H',self.connection.recv(2))[0]
    elif n==127:n=struct.unpack('>Q',self.connection.recv(8))[0]
    mask=self.connection.recv(4);data=bytes(x^mask[i%4] for i,x in enumerate(self.connection.recv(n)))
    o=json.loads(data); cid=str(o.get('channel_id'))
    if o['type']=='subscribe':
     c.execute('INSERT OR REPLACE INTO presence VALUES(?,?)',(cid,wsuser['id']));c.commit()
     last=c.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE cid=?',(cid,)).fetchone()[0]
     self.frame({'type':'subscribed','channel_id':cid,'head_event_id':last})
    elif o['type']=='resume':
     last=int(o.get('since_event_id',0))
     for x in c.execute('SELECT seq,data FROM events WHERE cid=? AND seq>? ORDER BY seq',(cid,last)):
      self.frame(json.loads(x['data']));last=x['seq']
     self.frame({'type':'resumed','channel_id':cid,'head_event_id':last})
  except Exception:pass
  finally:
   if cid:
    try:c.execute('DELETE FROM presence WHERE cid=? AND uid=?',(cid,wsuser['id']));c.commit()
    except:pass
 def frame(self,o):
  b=json.dumps(o).encode();n=len(b);h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack('>H',n);self.connection.sendall(h+b)
UI='''<!doctype html><html><head><title>Huddle</title><style>body{font-family:Arial;margin:0}button{border:0;padding:9px;color:#fff;border-radius:4px}.primary{background:#007a5a}.danger{background:#e01e5a}.secondary{background:#1264a3}</style></head><body><main id="app"><form data-testid="auth-form" id="auth-modal"><input name="username"><input name="password" type="password"><button data-testid="auth-submit" data-button-role="primary">Login</button><button data-testid="auth-toggle" type="button">Sign up</button></form><aside data-testid="channel-list"></aside><section><h2 data-testid="workspace-header">Huddle</h2><h3 data-testid="channel-title"></h3><div data-testid="channel-topic"></div><div data-testid="message-list"></div><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary">Send</button></section><button data-testid="logout-btn" data-button-role="danger">Logout</button><button data-testid="new-channel-btn" data-button-role="secondary">New channel</button><div data-testid="thread-panel" hidden></div></main></body></html>'''
if __name__=='__main__':
 os.makedirs('/app',exist_ok=True);db().close()
 ThreadingHTTPServer(('127.0.0.1',8000+NODE),H).serve_forever()
