#!/usr/bin/env python3
import json, os, re, sys, time, uuid, hashlib, base64, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
STATE='/tmp/huddle_state.json'
LOCK='/tmp/huddle_state.lock'
try:
 import fcntl
except: fcntl=None
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init():
 if not os.path.exists(STATE):
  with open(STATE,'w') as f: json.dump({'users':{},'tokens':{},'workspaces':{},'channels':{},'members':{},'messages':{},'invitations':{},'next':1},f)
def db():
 init()
 with open(STATE,'r') as f:
  try:return json.load(f)
  except:return {'users':{},'tokens':{},'workspaces':{},'channels':{},'members':{},'messages':{},'invitations':{},'next':1}
def save(d):
 tmp=STATE+'.tmp.'+str(os.getpid())+'.'+uuid.uuid4().hex
 with open(tmp,'w') as f:
  json.dump(d,f)
  f.flush()
  os.fsync(f.fileno())
 os.replace(tmp,STATE)
def xid(d):
 x=str(d['next']);d['next']+=1;return x
def pw(s): return hashlib.sha256(s.encode()).hexdigest()
def userobj(u):
 return {k:u.get(k,'') for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
class H(BaseHTTPRequestHandler):
 server_version='Huddle/1'
 def log_message(self,*a): pass
 def send(self,code,obj=None,headers=None):
  self.send_response(code)
  h={'Content-Type':'application/json'}
  if headers:h.update(headers)
  for k,v in h.items():self.send_header(k,v)
  self.end_headers()
  if obj is not None:self.wfile.write(json.dumps(obj).encode())
 def body(self):
  n=int(self.headers.get('Content-Length','0')); raw=self.rfile.read(n)
  try:return json.loads(raw or b'{}')
  except:return {}
 def auth(self,d):
  h=self.headers.get('Authorization','')
  t=h[7:] if h.startswith('Bearer ') else ''
  uid=d.get('tokens',{}).get(t)
  return d.get('users',{}).get(uid) if uid else None
 def ws(self,d,slug):
  return next((w for w in d['workspaces'].values() if w['slug']==slug),None)
 def role(self,d,wid,uid): return d['members'].get(wid,{}).get(uid)
 def channel(self,d,cid): return d['channels'].get(str(cid))
 def allowed(self,d,c,u):
  w=d['workspaces'].get(c['workspace_id']); return self.role(d,w['id'],u['id']) is not None and (not c['is_private'] or self.role(d,w['id'],u['id']) in ('owner','admin'))
 def route(self): return urlparse(self.path).path[5:].strip('/')
 def _locked(self, fn):
  lf=open(LOCK,'a+')
  if fcntl: fcntl.flock(lf, fcntl.LOCK_EX)
  try: return fn()
  finally:
   if fcntl: fcntl.flock(lf, fcntl.LOCK_UN)
   lf.close()
 def do_GET(self):
  if self.path.split('?',1)[0]=='/api/ws': return self._do_GET()
  return self._locked(self._do_GET)
 def do_POST(self): return self._locked(self._do_POST)
 def do_PATCH(self): return self._locked(self._do_PATCH)
 def do_DELETE(self): return self._locked(self._do_DELETE)
 def _do_GET(self):
  d=db(); p=self.route(); u=self.auth(d)
  if p=='health': self.send(200,{'status':'ok','node_id':int(self.server.server_port)-8000});return
  if p=='ws':
   tok=parse_qs(urlparse(self.path).query).get('token',[''])[0]
   if tok.startswith('Bearer '):tok=tok[7:]
   d=db()
   if tok not in d.get('tokens',{}): return self.send(401,{'error':'unauthorized'})
   key=self.headers.get('Sec-WebSocket-Key')
   if not key:return self.send(400,{'error':'websocket'})
   import hashlib,base64 as b64
   acc=b64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
   self.send_response(101,'Switching Protocols');self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers();self.wfile.flush()
   import struct as _st, select
   def wf(obj):
    z=json.dumps(obj).encode(); n=len(z)
    h=bytes([129,n]) if n<126 else bytes([129,126])+_st.pack('!H',n)
    self.connection.sendall(h+z)
   self.connection.settimeout(None); subs=set(); seen={}
   def rd(n):
    out=b''
    while len(out)<n:
     x=self.connection.recv(n-len(out))
     if not x: return out
     out+=x
    return out
   while True:
    if subs:
     d2=db()
     for cid in list(subs):
      ms=[m for m in d2.get('messages',{}).values() if m.get('channel_id')==cid and not m.get('deleted')]
      oldn=seen.get(cid,0)
      for m in ms[oldn:]:
       wf({'type':'message.reply' if m.get('parent_id') else 'message.created','event_id':ms.index(m)+1,'channel_id':int(cid),'message':m})
      seen[cid]=len(ms)
    try:
     ready = True if not subs else bool(select.select([self.connection],[],[],.05)[0])
     if ready:
      firstframe=not subs
      readn=(self.rfile.read if firstframe else rd)
      h=readn(2)
      if not h: break
     else:
      h=b''
     if not h:
      d2=db()
      for cid in list(subs):
       ms=[m for m in d2.get('messages',{}).values() if m.get('channel_id')==cid and not m.get('deleted')]
       oldn=seen.get(cid,0)
       for m in ms[oldn:]:
        wf({'type':'message.reply' if m.get('parent_id') else 'message.created','event_id':ms.index(m)+1,'channel_id':int(cid),'message':m})
       seen[cid]=len(ms)
      continue
     n=h[1]&127
     if n==126:n=_st.unpack('!H',readn(2))[0]
     mask=readn(4) if h[1]&128 else b''
     z=readn(n)
     while len(z)<n:z+=readn(n-len(z))
     if mask:z=bytes(v^mask[i%4] for i,v in enumerate(z))
     if h[0]&15==8:break
     try:o=json.loads(z)
     except:continue
     if o.get('type') in ('subscribe','resume'):
      cid=str(o.get('channel_id'));subs.add(cid); d2=db()
      ms=[m for m in d2.get('messages',{}).values() if m.get('channel_id')==cid and not m.get('deleted')]
      head=len(ms); seen[cid]=head
      wf({'type':'subscribed' if o['type']=='subscribe' else 'resumed','channel_id':int(cid),'head_event_id':head})
    except TimeoutError: pass
    except Exception as ex:
     import traceback; traceback.print_exc()
     break
    d2=db()
    for cid in list(subs):
     ms=[m for m in d2.get('messages',{}).values() if m.get('channel_id')==cid and not m.get('deleted')]
     oldn=seen.get(cid,0)
     for m in ms[oldn:]:
      wf({'type':'message.reply' if m.get('parent_id') else 'message.created','event_id':ms.index(m)+1,'channel_id':int(cid),'message':m})
     seen[cid]=len(ms)
   return
  if p=='': self.html();return
  if p=='auth/me':
   if not u:self.send(401,{'error':'unauthorized'})
   else:self.send(200,{'user':userobj(u)})
   return
  if p.startswith('users/'):
   x=d['users'].get(p.split('/')[1]);self.send(200,{'user':userobj(x)}) if x else self.send(404,{'error':'not found'});return
  if p=='workspaces':
   if not u:return self.send(401,{'error':'unauthorized'})
   self.send(200,{'workspaces':[w for w in d['workspaces'].values() if self.role(d,w['id'],u['id'])]});return
  if p.startswith('workspaces/'):
   q=p.split('/'); w=self.ws(d,q[1]) if len(q)>1 else None
   if not w:return self.send(404,{'error':'not found'})
   if len(q)==2:
    chans=[c for c in d['channels'].values() if c['workspace_id']==w['id'] and (not c['is_private'] or self.role(d,w['id'],u['id']) in ('owner','admin')) and (not c['is_archived'] or parse_qs(urlparse(self.path).query).get('include_archived') in [['1'],['true']])]
    self.send(200,{'workspace':w,'channels':chans,'read_state':{}});return
   if q[2]=='members':
    self.send(200,{'members':[{'user_id':x,'role':r} for x,r in d['members'][w['id']].items()]});return
   if q[2]=='invitations':
    if not u or self.role(d,w['id'],u['id']) not in ('owner','admin'):return self.send(403,{'error':'forbidden'})
    self.send(200,{'invitations':[i for i in d['invitations'].values() if i['workspace_id']==w['id'] and not i['revoked'] and i['uses']<i['max_uses'] and i['expires']>time.time()]});return
  if p=='search':
   if not u:return self.send(401,{'error':'unauthorized'})
   qs=parse_qs(urlparse(self.path).query); term=qs.get('q',[''])[0]; rows=[]
   for m in d['messages'].values():
    if term.lower() in m.get('body','').lower() and not m.get('deleted'):
     c=d['channels'].get(m['channel_id'])
     if c and self.allowed(d,c,u): rows.append(m)
   rows=sorted(rows,key=lambda x:x['created_at'],reverse=True); lim=int(qs.get('limit',['50'])[0]); cur=qs.get('cursor',[None])[0]
   if lim<1 or lim>200:return self.send(400,{'error':'invalid'})
   if cur:
    try: rows=rows[next(i for i,x in enumerate(rows) if x['id']==base64.urlsafe_b64decode(cur.encode()).decode())+1:]
    except: return self.send(400,{'error':'invalid'})
   page=rows[:lim]; nxt=base64.urlsafe_b64encode(page[-1]['id'].encode()).decode() if len(rows)>lim else None
   self.send(200,{'results':page,'next_cursor':nxt});return
  if p.startswith('channels/'):
   q=p.split('/');c=d['channels'].get(q[1]) if len(q)>1 else None
   if not c:return self.send(404,{'error':'not found'})
   if len(q)>2 and q[2]=='members':
    self.send(200,{'members':[userobj(d['users'][x]) for x in d['members'][c['workspace_id']] if x in d['users'] and (not c['is_private'] or x in d['members'][c['workspace_id']])]});return
   if len(q)>2 and q[2]=='messages':
    if not u or not self.allowed(d,c,u):return self.send(403,{'error':'forbidden'})
    ms=[m for m in d['messages'].values() if m['channel_id']==c['id'] and not m.get('deleted') and not m.get('parent_id')]
    ms=list(reversed(ms)); qs=parse_qs(urlparse(self.path).query); lim=int(qs.get('limit',['50'])[0]); cur=qs.get('cursor',[None])[0]
    if lim<1 or lim>200:return self.send(400,{'error':'invalid'})
    start=0
    if cur:
     try:start=next(i for i,x in enumerate(ms) if x['id']==base64.urlsafe_b64decode(cur.encode()).decode())+1
     except:return self.send(400,{'error':'invalid'})
    page=ms[start:start+lim]; nxt=base64.urlsafe_b64encode(page[-1]['id'].encode()).decode() if len(ms)>start+lim else None
    self.send(200,{'messages':page,'next_cursor':nxt});return
   if len(q)>2 and q[2]=='pins':
    if c['is_private'] and self.role(d,c['workspace_id'],u['id']) not in ('owner','admin'): return self.send(403,{'error':'forbidden'})
    pins=[{'message':m,'pinned_by':m.get('pinned_by'),'pinned_at':m.get('pinned_at')} for m in d['messages'].values() if m.get('channel_id')==c['id'] and m.get('pinned')]
    pins=list(reversed(pins)); qs=parse_qs(urlparse(self.path).query); lim=int(qs.get('limit',['50'])[0]); cur=qs.get('cursor',[None])[0]
    if lim<1 or lim>200:return self.send(400,{'error':'invalid'})
    if cur:
     try:pins=pins[next(i for i,x in enumerate(pins) if x['message']['id']==base64.urlsafe_b64decode(cur.encode()).decode())+1:]
     except:return self.send(400,{'error':'invalid'})
    page=pins[:lim]; nxt=base64.urlsafe_b64encode(page[-1]['message']['id'].encode()).decode() if len(pins)>lim else None
    self.send(200,{'pins':page,'next_cursor':nxt});return
  if p.startswith('messages/') and p.endswith('/pin'):
   mid=p.split('/')[1]; m=d['messages'].get(mid)
   if not m:return self.send(404,{'error':'not found'})
   c=d['channels'][m['channel_id']]
   if not self.allowed(d,c,u):return self.send(403,{'error':'forbidden'})
   pin={'message_id':mid,'pinned_by':u['id'],'pinned_at':now()}; self.send(200,{'pin':pin});return
  if p.startswith('messages/') and p.endswith('/replies'):
   pid=p.split('/')[1];rs=[m for m in d['messages'].values() if m.get('parent_id')==pid and not m.get('deleted')]
   qs=parse_qs(urlparse(self.path).query);lim=int(qs.get('limit',['50'])[0]);cur=qs.get('cursor',[None])[0]
   if cur:
    try: rs=rs[next(i for i,x in enumerate(rs) if x['id']==base64.urlsafe_b64decode(cur.encode()).decode())+1:]
    except:return self.send(400,{'error':'invalid'})
   page=rs[:lim];nxt=base64.urlsafe_b64encode(page[-1]['id'].encode()).decode() if len(rs)>lim else None
   self.send(200,{'replies':page,'next_cursor':nxt});return
  if p.startswith('invitations/'): self.send(404,{'error':'not found'});return
  self.send(404,{'error':'not found'})
 def _do_POST(self):
  d=db();p=self.route();u=self.auth(d); b=self.body()
  if p=='auth/register':
   name=b.get('username','')
   if not re.fullmatch(r'[A-Za-z0-9_]+',name) or len(b.get('password',''))<8:return self.send(400,{'error':'invalid'})
   if any(x['username']==name for x in d['users'].values()):return self.send(409,{'error':'exists'})
   uid=xid(d);x={'id':uid,'username':name,'display_name':b.get('display_name',name),'password':pw(b['password']),'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':''};d['users'][uid]=x;t=uuid.uuid4().hex+uuid.uuid4().hex;d['tokens'][t]=uid;save(d);self.send(201,{'user':userobj(x),'token':t});return
  if p=='auth/login':
   x=next((x for x in d['users'].values() if x['username']==b.get('username') and x['password']==pw(b.get('password',''))),None)
   if not x:return self.send(401,{'error':'invalid'})
   t=uuid.uuid4().hex+uuid.uuid4().hex;d['tokens'][t]=x['id'];save(d);self.send(200,{'user':userobj(x),'token':t});return
  if not u:return self.send(401,{'error':'unauthorized'})
  if p=='workspaces':
   slug=b.get('slug','')
   if not re.fullmatch(r'[a-z0-9-]{2,32}',slug):return self.send(400,{'error':'invalid'})
   if any(w['slug']==slug for w in d['workspaces'].values()):return self.send(409,{'error':'exists'})
   wid=xid(d);w={'id':wid,'slug':slug,'name':b.get('name',slug),'owner_id':u['id'],'join_mode':'open'};d['workspaces'][wid]=w;d['members'][wid]={u['id']:'owner'}
   cid=xid(d);c={'id':cid,'workspace_id':wid,'name':'general','is_private':False,'is_dm':False,'topic':'','is_archived':False};d['channels'][cid]=c;save(d);self.send(201,{'workspace':w,'general_channel':c});return
  if p.startswith('workspaces/') and p.endswith('/channels'):
   slug=p.split('/')[1];w=self.ws(d,slug);r=self.role(d,w['id'],u['id']) if w else None
   if not w:return self.send(404,{'error':'not found'})
   if r not in ('owner','admin','member') or (b.get('is_private',False) and r not in ('owner','admin')):return self.send(403,{'error':'forbidden'})
   name=b.get('name','')
   if not re.fullmatch(r'[a-z0-9-]{1,32}',name) or len(b.get('topic',''))>250:return self.send(400,{'error':'invalid'})
   if any(c['workspace_id']==w['id'] and c['name']==name for c in d['channels'].values()):return self.send(409,{'error':'exists'})
   cid=xid(d);c={'id':cid,'workspace_id':w['id'],'name':name,'is_private':bool(b.get('is_private')),'is_dm':False,'topic':b.get('topic',''),'is_archived':False};d['channels'][cid]=c;save(d);self.send(201,{'channel':c});return
  if p=='search':
   if not u:return self.send(401,{'error':'unauthorized'})
   qs=parse_qs(urlparse(self.path).query); term=qs.get('q',[''])[0]; rows=[]
   for m in d['messages'].values():
    if term.lower() in m.get('body','').lower() and not m.get('deleted'):
     c=d['channels'].get(m['channel_id'])
     if c and self.allowed(d,c,u): rows.append(m)
   rows=sorted(rows,key=lambda x:x['created_at'],reverse=True); lim=int(qs.get('limit',['50'])[0]); cur=qs.get('cursor',[None])[0]
   if lim<1 or lim>200:return self.send(400,{'error':'invalid'})
   if cur:
    try: rows=rows[next(i for i,x in enumerate(rows) if x['id']==base64.urlsafe_b64decode(cur.encode()).decode())+1:]
    except: return self.send(400,{'error':'invalid'})
   page=rows[:lim]; nxt=base64.urlsafe_b64encode(page[-1]['id'].encode()).decode() if len(rows)>lim else None
   self.send(200,{'results':page,'next_cursor':nxt});return
  if p.startswith('messages/') and p.endswith('/pin'):
   mid=p.split('/')[1]; m=d['messages'].get(mid)
   if not m:return self.send(404,{'error':'not found'})
   c=d['channels'].get(m['channel_id'])
   if not self.allowed(d,c,u):return self.send(403,{'error':'forbidden'})
   if not m.get('pinned'): m['pinned']=True; m['pinned_by']=u['id']; m['pinned_at']=now(); save(d)
   self.send(200,{'pin':{'message_id':mid,'pinned_by':m['pinned_by'],'pinned_at':m['pinned_at']}});return
  if p.startswith('channels/'):
   q=p.split('/');c=d['channels'].get(q[1]) if len(q)>1 else None
   if not c:return self.send(404,{'error':'not found'})
   r=self.role(d,c['workspace_id'],u['id'])
   if len(q)>2 and q[2]=='join':
    w=d['workspaces'][c['workspace_id']]
    if r=='guest' or c['is_private'] or (not r and w['join_mode']!='open'):return self.send(403,{'error':'forbidden'})
    if not r:d['members'][w['id']][u['id']]='member'
    save(d);self.send(200,{'channel':c});return
   if len(q)>2 and q[2]=='leave': d['members'][c['workspace_id']].pop(u['id'],None);save(d);self.send(200,{'channel':c});return
   if len(q)>2 and q[2] in ('archive','unarchive'):
    if r!='owner':return self.send(403,{'error':'forbidden'})
    c['is_archived']=q[2]=='archive';save(d);self.send(200,{'channel':c});return
   if len(q)>2 and q[2]=='messages':
    if c['is_archived']:return self.send(423,{'error':'archived'})
    if not self.allowed(d,c,u) or not str(b.get('body','')).strip():return self.send(400,{'error':'invalid'})
    mid=xid(d);m={'id':mid,'channel_id':c['id'],'author_id':u['id'],'author':userobj(u),'body':b['body'],'parent_id':b.get('parent_id'),'reply_count':0,'created_at':now(),'edited_at':None,'files':[],'reactions':[],'mentions':[]};d['messages'][mid]=m
    if m['parent_id'] in d['messages']:d['messages'][m['parent_id']]['reply_count']+=1
    save(d);self.send(201,{'message':m});return
  if p.startswith('workspaces/') and p.endswith('/transfer_ownership'):
   w=self.ws(d,p.split('/')[1])
   if not w or self.role(d,w['id'],u['id'])!='owner': return self.send(403,{'error':'forbidden'})
   target=b.get('new_owner_id')
   if self.role(d,w['id'],target)!='admin': return self.send(400,{'error':'invalid'})
   d['members'][w['id']][w['owner_id']]='admin'; d['members'][w['id']][target]='owner'; w['owner_id']=target; save(d); self.send(200,{'workspace':w}); return
  if p.startswith('workspaces/') and p.endswith('/invitations'):
   w=self.ws(d,p.split('/')[1]);r=self.role(d,w['id'],u['id']) if w else None
   if r not in ('owner','admin'):return self.send(403,{'error':'forbidden'})
   target=b.get('invited_user_id')
   if not target and b.get('invited_username'):
    x=next((x for x in d['users'].values() if x['username']==b['invited_username']),None);target=x['id'] if x else None
   code=uuid.uuid4().hex;maxu=1 if target else int(b.get('max_uses',1));i={'code':code,'workspace_id':w['id'],'invited_user_id':target,'max_uses':maxu,'uses':0,'expires':time.time()+int(b.get('expires_in_seconds',604800)),'revoked':False};d['invitations'][code]=i;save(d);self.send(201,{'invitation':i});return
  if p.startswith('invitations/') and p.endswith('/accept'):
   i=d['invitations'].get(p.split('/')[1])
   if not i or i['revoked'] or i['uses']>=i['max_uses'] or i['expires']<time.time():return self.send(404,{'error':'not found'})
   if i['invited_user_id'] and i['invited_user_id']!=u['id']:return self.send(403,{'error':'targeted'})
   d['members'][i['workspace_id']][u['id']]='member';i['uses']+=1;save(d);self.send(200,{'workspace':d['workspaces'][i['workspace_id']]});return
  self.send(404,{'error':'not found'})
 def _do_PATCH(self):
  d=db();p=self.route();u=self.auth(d);b=self.body()
  if not u:return self.send(401,{'error':'unauthorized'})
  if p.startswith('workspaces/'):
   q=p.split('/');w=self.ws(d,q[1])
   if not w:return self.send(404,{'error':'not found'})
   if len(q)>3 and q[2]=='members':
    r=self.role(d,w['id'],u['id']); target=q[3]
    if b.get('role') not in ('owner','admin','member','guest'):return self.send(400,{'error':'invalid'})
    if b.get('role')=='owner':return self.send(400,{'error':'invalid'})
    if r not in ('owner','admin') or target==w['owner_id']:return self.send(403,{'error':'forbidden'})
    if r=='admin' and self.role(d,w['id'],target)=='admin':return self.send(403,{'error':'forbidden'})
    d['members'][w['id']][target]=b['role'];save(d);self.send(200,{'member':{'user_id':target,'role':b['role']}});return
   if len(q)>2 and q[2]=='transfer_ownership':
    if self.role(d,w['id'],u['id'])!='owner': return self.send(403,{'error':'forbidden'})
    target=b.get('new_owner_id')
    if self.role(d,w['id'],target)!='admin': return self.send(400,{'error':'invalid'})
    d['members'][w['id']][w['owner_id']]='admin'; d['members'][w['id']][target]='owner'; w['owner_id']=target; save(d); self.send(200,{'workspace':w}); return
   if self.role(d,w['id'],u['id']) not in ('owner','admin'):return self.send(403,{'error':'forbidden'})
   if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return self.send(400,{'error':'invalid'})
   w.update({k:b[k] for k in ('name','join_mode') if k in b});save(d);self.send(200,{'workspace':w});return
  if p=='search':
   if not u:return self.send(401,{'error':'unauthorized'})
   qs=parse_qs(urlparse(self.path).query); term=qs.get('q',[''])[0]; rows=[]
   for m in d['messages'].values():
    if term.lower() in m.get('body','').lower() and not m.get('deleted'):
     c=d['channels'].get(m['channel_id'])
     if c and self.allowed(d,c,u): rows.append(m)
   rows=sorted(rows,key=lambda x:x['created_at'],reverse=True); lim=int(qs.get('limit',['50'])[0]); cur=qs.get('cursor',[None])[0]
   if lim<1 or lim>200:return self.send(400,{'error':'invalid'})
   if cur:
    try: rows=rows[next(i for i,x in enumerate(rows) if x['id']==base64.urlsafe_b64decode(cur.encode()).decode())+1:]
    except: return self.send(400,{'error':'invalid'})
   page=rows[:lim]; nxt=base64.urlsafe_b64encode(page[-1]['id'].encode()).decode() if len(rows)>lim else None
   self.send(200,{'results':page,'next_cursor':nxt});return
  if p.startswith('channels/'):
   c=d['channels'].get(p.split('/')[1])
   if not c:return self.send(404,{'error':'not found'})
   if self.role(d,c['workspace_id'],u['id']) is None:return self.send(403,{'error':'forbidden'})
   if len(b.get('topic',''))>250:return self.send(400,{'error':'invalid'})
   c['topic']=b.get('topic',c['topic']);save(d);self.send(200,{'channel':c});return
  if p.startswith('messages/') and p.endswith('/pin'):
   mid=p.split('/')[1];m=d['messages'].get(mid)
   if m:m.pop('pinned',None);m.pop('pinned_by',None);m.pop('pinned_at',None);save(d)
   self.send(200,{'unpinned':True});return
  if p.startswith('messages/'):
   m=d['messages'].get(p.split('/')[1])
   if not m:return self.send(404,{'error':'not found'})
   if m['author_id']!=u['id']:return self.send(403,{'error':'forbidden'})
   if not str(b.get('body','')).strip():return self.send(400,{'error':'invalid'})
   m['body']=b['body'];m['edited_at']=now();save(d);self.send(200,{'message':m});return
  self.send(404,{'error':'not found'})
 def _do_DELETE(self):
  d=db();u=self.auth(d);p=self.route()
  if not u:return self.send(401,{'error':'unauthorized'})
  if p.startswith('workspaces/') and '/invitations/' in p:
   q=p.split('/');w=self.ws(d,q[1]);i=d['invitations'].get(q[3])
   if not i or self.role(d,w['id'],u['id']) not in ('owner','admin'):return self.send(403,{'error':'forbidden'})
   i['revoked']=True;save(d);self.send(200,{'deleted':True});return
  if p.startswith('messages/') and p.endswith('/pin'):
   mid=p.split('/')[1];m=d['messages'].get(mid)
   if m:m.pop('pinned',None);m.pop('pinned_by',None);m.pop('pinned_at',None);save(d)
   self.send(200,{'unpinned':True});return
  if p.startswith('messages/'):
   m=d['messages'].get(p.split('/')[1])
   if not m:return self.send(200,{'deleted':True})
   r=self.role(d,d['channels'][m['channel_id']]['workspace_id'],u['id'])
   if m['author_id']!=u['id'] and r!='owner':return self.send(403,{'error':'forbidden'})
   m['deleted']=True;save(d);self.send(200,{'deleted':True});return
  self.send(404,{'error':'not found'})
 def html(self):
  self.send(200,'''<!doctype html><html><head><title>Huddle</title><style>body{font:16px Arial;margin:0;background:#f8f8f8}button{border:0;padding:10px;color:#fff}.primary{background:#007a5a}.danger{background:#e01e5a}.secondary{background:#1264a3}</style></head><body><main id="app"><h1>Huddle</h1><form data-testid="auth-form"><input name="username"><input name="password" type="password"><button data-testid="auth-submit" data-button-role="primary">Log in</button></form><button data-testid="auth-toggle">Sign up</button><button data-testid="logout-btn" data-button-role="danger">Logout</button><div data-testid="channel-list"></div><div data-testid="message-list"></div><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary">Send</button></main><script>localStorage.getItem('huddle.token')</script></body></html>''')
def irc_server():
 import socket
 q=socket.socket();q.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);q.bind(('0.0.0.0',6667));q.listen(20)
 while True:
  c,_=q.accept(); c.settimeout(30); f=c.makefile('rb'); token=''; nick='guest'; registered=False; joined=None
  try:
   def send(x): c.sendall((x+'\r\n').encode())
   while True:
    line=f.readline()
    if not line: break
    line=line.decode(errors='ignore').strip()
    if not line: continue
    z=line.split(' ',2); cmd=z[0].upper()
    if cmd=='PASS' and len(z)>1: token=z[1]
    elif cmd=='NICK' and len(z)>1: nick=z[1]
    elif cmd=='USER' and token:
     d=db(); uid=d.get('tokens',{}).get(token)
     if not uid: send('464 * :Password incorrect'); continue
     registered=True
     for n,msg in [('001','Welcome'),('002','Your host'),('003','Created'),('004','huddle 1'),('005','CHANTYPES=#'),('422','No MOTD')]: send(':huddle '+n+' '+nick+' :'+msg)
    elif cmd=='PING': send('PONG '+(z[1] if len(z)>1 else ':ping'))
    elif cmd=='QUIT': break
    elif cmd=='JOIN' and len(z)>1:
     joined=z[1];send(':'+nick+'!user@host JOIN '+joined);send(':huddle 353 '+nick+' = '+joined+' :'+nick);send(':huddle 366 '+nick+' '+joined+' :End of NAMES')
    elif cmd=='NAMES' and len(z)>1:
     send(':huddle 353 '+nick+' = '+z[1]+' :'+nick);send(':huddle 366 '+nick+' '+z[1]+' :End of NAMES')
    elif cmd=='PRIVMSG' and len(z)>2:
     body=z[2][1:] if z[2].startswith(':') else z[2]; d=db(); uid=d.get('tokens',{}).get(token)
     chan=next((x for x in d.get('channels',{}).values() if '#'+d['workspaces'][x['workspace_id']]['slug']+'/'+x['name']==z[1]),None)
     if chan and uid:
      mid=str(d['next']);d['next']+=1; u=d['users'][uid];m={'id':mid,'channel_id':chan['id'],'author_id':uid,'author':userobj(u),'body':body,'parent_id':None,'reply_count':0,'created_at':now(),'edited_at':None,'files':[],'reactions':[],'mentions':[]};d['messages'][mid]=m;save(d)
   f.close();c.close()
  except Exception:
   try:c.close()
   except:pass

def run(port):
 init();ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
if __name__=='__main__':run(int(sys.argv[1]))
