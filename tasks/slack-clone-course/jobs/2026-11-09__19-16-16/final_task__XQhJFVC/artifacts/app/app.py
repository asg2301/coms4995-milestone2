#!/usr/bin/env python3
import os,json,time,uuid,re,hashlib,secrets,base64,threading,socket,struct,fcntl,mimetypes,urllib.parse
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from datetime import datetime,timezone
STATE='/tmp/huddle_state.json'; LOCK='/tmp/huddle_state.lock'; MAX=10*1024*1024
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def rid(): return str(uuid.uuid4())
def load():
 os.makedirs('/tmp',exist_ok=True)
 with open(LOCK,'a+') as l:
  fcntl.flock(l,fcntl.LOCK_EX)
  try:
   if not os.path.exists(STATE): return {'users':{},'tokens':{},'workspaces':{},'channels':{},'messages':{},'files':{},'dms':{},'groups':{},'events':{}}
   with open(STATE) as f:return json.load(f)
  finally: fcntl.flock(l,fcntl.LOCK_UN)
def save(s):
 with open(LOCK,'a+') as l:
  fcntl.flock(l,fcntl.LOCK_EX)
  with open(STATE,'w') as f: json.dump(s,f)
  fcntl.flock(l,fcntl.LOCK_UN)
def userobj(u):
 return {k:u.get(k,'') for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def err(h,c,msg):
 b=json.dumps({'error':msg}).encode()
 h.send_response(c);h.send_header('Content-Type','application/json');h.send_header('Content-Length',str(len(b)));h.send_header('Connection','close');h.end_headers();h.wfile.write(b)
def send(h,c,obj,headers={}):
 b=json.dumps(obj).encode()
 h.send_response(c);h.send_header('Content-Type','application/json');h.send_header('Content-Length',str(len(b)));h.send_header('Connection','close')
 for k,v in headers.items():h.send_header(k,v)
 h.end_headers();h.wfile.write(b)
def channelobj(c): return {k:c.get(k) for k in ['id','workspace_id','name','is_private','is_dm','topic','is_archived']}
def emit(s,cid,kind,payload):
 es=s.setdefault('events',{}).setdefault(cid,[])
 eid=len(es)+1
 e={'type':kind,'event_id':eid,'channel_id':cid}
 e.update(payload)
 es.append(e)
 return e
def wsframe(obj):
 raw=json.dumps(obj,separators=(',',':')).encode(); n=len(raw)
 if n<126: return bytes([129,n])+raw
 if n<65536: return bytes([129,126])+struct.pack('>H',n)+raw
 return bytes([129,127])+struct.pack('>Q',n)+raw
def msgobj(m,s):
 u=s['users'].get(m['author_id'],{})
 x=dict(m);x['author']=userobj(u);x.setdefault('parent_id',None);x.setdefault('reply_count',0);x.setdefault('reactions',[]);x.setdefault('mentions',[]);x.setdefault('files',[])
 return x
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def auth(self,s):
  t=self.headers.get('Authorization','').replace('Bearer ','')
  uid=s['tokens'].get(t); return s['users'].get(uid) if uid else None
 def body(self):
  n=int(self.headers.get('Content-Length','0')); return self.rfile.read(n)
 def jbody(self):
  try:return json.loads(self.body() or b'{}')
  except:return {}
 def do_GET(self):
  if self.path=='/' or self.path.startswith('/?'): return self.ui()
  s=load(); p=urllib.parse.urlparse(self.path); path=p.path; q=urllib.parse.parse_qs(p.query)
  if path=='/api/health': return send(self,200,{'status':'ok','node_id':int(os.environ.get('NODE_ID','0'))})
  if path.startswith('/api/ws'): return self.websocket(s)
  u=self.auth(s)
  if path.startswith('/api/auth/') and path.endswith('/me'):
   return send(self,200,{'user':userobj(u)}) if u else err(self,401,'unauthorized')
  if path.startswith('/api/users/'):
   uid=path.split('/')[-1]; x=s['users'].get(uid)
   return send(self,200,{'user':userobj(x)}) if x else err(self,404,'not found')
  if not u:return err(self,401,'unauthorized')
  if path=='/api/workspaces':
   return send(self,200,{'workspaces':[w for w in s['workspaces'].values() if u['id'] in w['members']]})
  if path.startswith('/api/workspaces/'):
   slug=path.split('/')[3]; w=next((x for x in s['workspaces'].values() if x['slug']==slug),None)
   if not w:return err(self,404,'not found')
   if path.endswith('/members'): return send(self,200,{'members':[{'user_id':i,'role':r} for i,r in w['members'].items()]})
   if path.endswith('/groups'): return send(self,200,{'groups':[x for x in s['groups'].values() if x['workspace_id']==w['id']]})
   cs=[channelobj(c) for c in s['channels'].values() if c['workspace_id']==w['id'] and (not c['is_private'] or u['id'] in c['members'])]
   return send(self,200,{'workspace':w,'channels':cs,'read_state':{}})
  if path.startswith('/api/channels/'):
   cid=path.split('/')[3]; c=s['channels'].get(cid)
   if not c:return err(self,404,'not found')
   if path.endswith('/members'):return send(self,200,{'members':[userobj(s['users'][i]) for i in c['members']]})
   if path.endswith('/pins'):return send(self,200,{'pins':[],'next_cursor':None})
   if path.endswith('/messages'):
    ms=[msgobj(x,s) for x in s['messages'].values() if x['channel_id']==cid and not x.get('deleted') and not x.get('parent_id')]
    ms.sort(key=lambda x:x['created_at'],reverse=True);return send(self,200,{'messages':ms,'next_cursor':None})
   if path.endswith('/read'):return send(self,200,{'read_state':{'channel_id':cid,'last_read_event_id':0,'unread_count':0,'mention_count':0}})
  if path.startswith('/api/messages/') and path.endswith('/replies'):
   pid=path.split('/')[3];return send(self,200,{'replies':[msgobj(x,s) for x in s['messages'].values() if x.get('parent_id')==pid],'next_cursor':None})
  if path.startswith('/api/files/'):
   fid=path.split('/')[3];f=s['files'].get(fid)
   if not f:return err(self,404,'not found')
   if path.endswith('/download'):
    data=open(f['path'],'rb').read();self.send_response(200);self.send_header('Content-Type',f['content_type']);self.send_header('Content-Disposition','attachment; filename="'+f['filename']+'"');self.send_header('Content-Length',len(data));self.end_headers();self.wfile.write(data);return
   return send(self,200,{'file':{k:f[k] for k in ['id','uploader_id','filename','content_type','size','created_at']}})
  if path=='/api/search':
   term=q.get('q',[''])[0].lower(); ms=[msgobj(x,s) for x in s['messages'].values() if term in x['body'].lower()];return send(self,200,{'results':ms,'next_cursor':None})
  return err(self,404,'not found')
 def do_POST(self):
  s=load();p=urllib.parse.urlparse(self.path);path=p.path
  if path=='/api/auth/register':
   d=self.jbody(); un=d.get('username','')
   if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(d.get('password',''))<8:return err(self,400,'invalid')
   if any(x['username']==un for x in s['users'].values()):return err(self,409,'duplicate')
   uid=rid();u={'id':uid,'username':un,'display_name':d.get('display_name') or un,'password':hashlib.sha256(d['password'].encode()).hexdigest(),'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':''};s['users'][uid]=u;t=secrets.token_urlsafe(24);s['tokens'][t]=uid;save(s);return send(self,201,{'user':userobj(u),'token':t})
  if path=='/api/auth/login':
   d=self.jbody();u=next((x for x in s['users'].values() if x['username']==d.get('username')),None)
   if not u or u['password']!=hashlib.sha256(d.get('password','').encode()).hexdigest():return err(self,401,'invalid')
   t=secrets.token_urlsafe(24);s['tokens'][t]=u['id'];save(s);return send(self,200,{'user':userobj(u),'token':t})
  u=self.auth(s)
  if not u:return err(self,401,'unauthorized')
  if path=='/api/workspaces':
   d=self.jbody();slug=d.get('slug','')
   if not re.fullmatch(r'[a-z0-9-]{2,32}',slug):return err(self,400,'invalid slug')
   if any(w['slug']==slug for w in s['workspaces'].values()):return err(self,409,'duplicate')
   wid=rid();w={'id':wid,'slug':slug,'name':d.get('name',slug),'owner_id':u['id'],'join_mode':'open','members':{u['id']:'owner'}};s['workspaces'][wid]=w
   cid=rid();s['channels'][cid]={'id':cid,'workspace_id':wid,'name':'general','is_private':False,'is_dm':False,'topic':'','is_archived':False,'members':[u['id']]};save(s);return send(self,201,{'workspace':w,'general_channel':channelobj(s['channels'][cid])})
  if path.startswith('/api/workspaces/') and path.endswith('/groups'):
   slug=path.split('/')[3];w=next((x for x in s['workspaces'].values() if x['slug']==slug),None);d=self.jbody()
   if not w:return err(self,404,'not found')
   if u['id'] not in w['members']:return err(self,403,'forbidden')
   handle=d.get('handle','').lstrip('@')
   if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',handle):return err(self,400,'invalid handle')
   if any(g['workspace_id']==w['id'] and g['handle']==handle for g in s['groups'].values()):return err(self,409,'duplicate')
   gid=rid();g={'id':gid,'workspace_id':w['id'],'handle':handle,'name':d.get('name',handle),'member_user_ids':[x for x in d.get('user_ids',[]) if x in s['users']],'creator_id':u['id']}
   s['groups'][gid]=g;save(s);return send(self,201,{'group':{'id':gid,'handle':handle,'name':g['name'],'member_user_ids':g['member_user_ids']}})
  if path.startswith('/api/workspaces/') and path.endswith('/channels'):
   slug=path.split('/')[3];w=next((x for x in s['workspaces'].values() if x['slug']==slug),None);d=self.jbody()
   if not w:return err(self,404,'not found')
   if any(c['workspace_id']==w['id'] and c['name']==d.get('name') for c in s['channels'].values()):return err(self,409,'duplicate')
   cid=rid();c={'id':cid,'workspace_id':w['id'],'name':d.get('name',''),'is_private':bool(d.get('is_private',False)),'is_dm':False,'topic':d.get('topic',''),'is_archived':False,'members':[u['id']]};s['channels'][cid]=c;save(s);return send(self,201,{'channel':channelobj(c)})
  if path.startswith('/api/channels/') and path.endswith('/archive'):
   c=s['channels'].get(path.split('/')[3])
   if not c:return err(self,404,'not found')
   c['is_archived']=True;emit(s,c['id'],'channel.archived',{'channel':channelobj(c)});save(s);return send(self,200,{'channel':channelobj(c)})
  if path.startswith('/api/channels/') and path.endswith('/unarchive'):
   c=s['channels'].get(path.split('/')[3])
   if not c:return err(self,404,'not found')
   c['is_archived']=False;emit(s,c['id'],'channel.unarchived',{'channel':channelobj(c)});save(s);return send(self,200,{'channel':channelobj(c)})
  if path.startswith('/api/channels/') and path.endswith('/join'):
   c=s['channels'].get(path.split('/')[3])
   if not c:return err(self,404,'not found')
   if u['id'] not in c['members']:c['members'].append(u['id']);save(s)
   return send(self,200,{'channel':channelobj(c)})
  if path.startswith('/api/channels/') and path.endswith('/messages'):
   cid=path.split('/')[3];c=s['channels'].get(cid);d=self.jbody();body=d.get('body','')
   if not c:return err(self,404,'not found')
   if c.get('is_archived'):return err(self,423,'archived')
   if not body.strip():return err(self,400,'empty')
   if body.startswith('/') and not body.startswith('//'):
    z=body.split(' ',1);cmd=z[0];arg=z[1] if len(z)>1 else ''
    if cmd=='/shrug':body=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
    elif cmd in ['/topic','/archive','/unarchive']: c['topic']=arg if cmd=='/topic' else c['topic'];c['is_archived']=cmd=='/archive';save(s);return send(self,201,{'message':None,'channel':channelobj(c)})
    elif cmd not in ['/me']:return err(self,400,'unknown')
   attach=d.get('file_ids',[]) or []
   if not isinstance(attach,list):return err(self,400,'invalid files')
   for fid in attach:
    f=s['files'].get(fid)
    if not f or f.get('uploader_id')!=u['id'] or f.get('attached'):return err(self,400,'invalid attachment')
   for fid in attach:s['files'][fid]['attached']=True
   mid=rid();m={'id':mid,'channel_id':cid,'author_id':u['id'],'body':body,'parent_id':d.get('parent_id'),'reply_count':0,'created_at':now(),'edited_at':None,'reactions':[],'mentions':[],'files':[ {k:s['files'][fid][k] for k in ['id','uploader_id','filename','content_type','size','created_at']} for fid in attach]};s['messages'][mid]=m
   if m['parent_id'] in s['messages']:s['messages'][m['parent_id']]['reply_count']+=1
   ev=emit(s,cid,'message.reply' if m.get('parent_id') else 'message.created',{'message':msgobj(m,s)})
   save(s);return send(self,201,{'message':msgobj(m,s)})
  if path=='/api/dms':
   other=self.jbody().get('user_id');key=':'.join(sorted([u['id'],other]));cid=s['dms'].get(key)
   if not cid: cid=rid();s['dms'][key]=cid;s['channels'][cid]={'id':cid,'workspace_id':'','name':'dm','is_private':True,'is_dm':True,'topic':'','is_archived':False,'members':[u['id'],other]};save(s)
   return send(self,200,{'channel_id':cid})
  if path.startswith('/api/messages/') and path.endswith('/reactions'):
   m=s['messages'].get(path.split('/')[3]);d=self.jbody();e=d.get('emoji')
   if not m:return err(self,404,'not found')
   r=next((x for x in m['reactions'] if x['emoji']==e),None)
   if not r:r={'emoji':e,'count':0,'user_ids':[]};m['reactions'].append(r)
   if u['id'] not in r['user_ids']:r['user_ids'].append(u['id']);r['count']=len(r['user_ids'])
   emit(s,m['channel_id'],'reaction.added',{'message_id':m['id'],'reactions':m['reactions']})
   save(s);return send(self,200,{'reactions':m['reactions']})
  if path=='/api/files':
   raw=self.body(); ctype=self.headers.get('Content-Type','application/octet-stream'); fn=self.headers.get('X-Filename','upload.bin'); data=raw
   if ctype.lower().startswith('multipart/form-data') and 'boundary=' in ctype:
    boundary=ctype.split('boundary=',1)[1].strip().strip('"').encode(); parts=raw.split(b'--'+boundary)
    for part in parts:
     if b'Content-Disposition' in part and b'filename=' in part:
      head,sep,val=part.partition(b'\\r\
\\r\
')
      if sep:
       mm=re.search(br'filename="([^"]*)"',head); fn=(mm.group(1).decode(errors='ignore') if mm else fn); cm=re.search(br'Content-Type:\\s*([^\\r\
]+)',head,re.I); ctype=(cm.group(1).decode().strip() if cm else 'application/octet-stream'); data=val.rsplit(b'\\r\
',1)[0];break
   if len(data)>MAX:return err(self,413,'too large')
   fid=rid();fp='/tmp/'+fid;open(fp,'wb').write(data);f={'id':fid,'uploader_id':u['id'],'filename':fn,'content_type':ctype.split(';')[0],'size':len(data),'created_at':now(),'path':fp};s['files'][fid]=f;save(s);return send(self,201,{'file':{k:f[k] for k in ['id','uploader_id','filename','content_type','size','created_at']}})
  return err(self,404,'not found')
 def do_PATCH(self):
  s=load();u=self.auth(s)
  if not u:return err(self,401,'unauthorized')
  p=self.path.split('/');d=self.jbody()
  if self.path=='/api/users/me':
   allowed=['display_name','timezone','status_text','status_emoji','avatar_url']
   if 'timezone' in d and (not isinstance(d['timezone'],str) or len(d['timezone'])>64 or not re.fullmatch(r'[A-Za-z_]+(?:/[A-Za-z_]+)*',d['timezone'])):return err(self,400,'invalid timezone')
   for k in allowed:
    if k in d and (not isinstance(d[k],str) or len(d[k])>250):return err(self,400,'invalid profile field')
   s['users'][u['id']].update({k:v for k,v in d.items() if k in allowed});save(s);return send(self,200,{'user':userobj(s['users'][u['id']])})
  if len(p)>=4 and p[2]=='channels':
   c=s['channels'].get(p[3])
   if not c:return err(self,404,'not found')
   if 'topic' in d and (not isinstance(d['topic'],str) or len(d['topic'])>250):return err(self,400,'invalid topic')
   c['topic']=d.get('topic',c.get('topic',''));emit(s,c['id'],'channel.updated',{'channel':channelobj(c)});save(s);return send(self,200,{'channel':channelobj(c)})
  if len(p)>=5 and p[2]=='workspaces' and p[4]=='groups':
   slug=p[3];handle=p[5] if len(p)>5 else '';w=next((x for x in s['workspaces'].values() if x['slug']==slug),None);g=next((x for x in s['groups'].values() if x['workspace_id']==(w or {}).get('id') and x['handle']==handle),None)
   if not g:return err(self,404,'not found')
   d=self.jbody();g['name']=d.get('name',g['name']);g['member_user_ids']=[x for x in d.get('user_ids',g['member_user_ids']) if x in s['users']];save(s);return send(self,200,{'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':g['member_user_ids']}})
  if len(p)>=4 and p[2]=='messages':
   m=s['messages'].get(p[3])
   if not m:return err(self,404,'not found')
   if m.get('deleted'):return err(self,409,'deleted')
   if m['author_id']!=u['id']:return err(self,403,'forbidden')
   if not str(d.get('body',m['body'])).strip():return err(self,400,'empty')
   m['body']=d.get('body',m['body']);m['edited_at']=now();emit(s,m['channel_id'],'message.updated',{'message':msgobj(m,s)});save(s);return send(self,200,{'message':msgobj(m,s)})
  return err(self,404,'not found')
 def do_DELETE(self):
  s=load();u=self.auth(s)
  s=load();u=self.auth(s)
  if not u:return err(self,401,'unauthorized')
  p=self.path.split('/')
  if len(p)>=5 and p[2]=='workspaces' and p[4]=='groups':
   slug=p[3];handle=p[5] if len(p)>5 else '';w=next((x for x in s['workspaces'].values() if x['slug']==slug),None)
   g=next((x for x in s['groups'].values() if x['workspace_id']==(w or {}).get('id') and x['handle']==handle),None)
   if g and u['id'] not in (g.get('creator_id'),w.get('owner_id')):return err(self,403,'forbidden')
   if g:s['groups'].pop(g['id'],None);save(s)
   return send(self,200,{'deleted':True})
  if len(p)>=5 and p[2]=='messages' and p[4]=='reactions':
   m=s['messages'].get(p[3]);e=urllib.parse.unquote(p[5]) if len(p)>5 else ''; 
   if m:
    for r in m['reactions']:
     if r['emoji']==e and u['id'] in r['user_ids']:r['user_ids'].remove(u['id']);r['count']=len(r['user_ids'])
    save(s);return send(self,200,{'reactions':m['reactions']})
  if len(p)>=4 and p[2]=='messages':
   m=s['messages'].get(p[3])
   if m:m['deleted']=True;save(s)
   return send(self,200,{'deleted':True})
  return err(self,404,'not found')
 def websocket(self,s):
  key=self.headers.get('Sec-WebSocket-Key')
  if not key:return err(self,400,'websocket')
  self.send_response(101);self.send_header('Upgrade','WebSocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode());self.end_headers()
  self.connection.settimeout(0.2); subscribed=None; sent=0
  try:
   while True:
    try:
     b=self.rfile.read(2)
     if not b: break
     n=b[1]&127
     if n==126:n=struct.unpack('>H',self.rfile.read(2))[0]
     elif n==127:n=struct.unpack('>Q',self.rfile.read(8))[0]
     mask=self.rfile.read(4); data=bytes(x^mask[i%4] for i,x in enumerate(self.rfile.read(n)))
     d=json.loads(data)
     if d.get('type') in ('subscribe','resume'):
      subscribed=str(d.get('channel_id')); sent=int(d.get('since_event_id',0)) if d.get('type')=='resume' else 0
      s2=load(); head=len(s2.get('events',{}).get(subscribed,[]))
      self.wfile.write(wsframe({'type':'resumed' if d.get('type')=='resume' else 'subscribed','channel_id':d.get('channel_id'),'head_event_id':head}));self.wfile.flush()
    except socket.timeout: pass
    except (ConnectionResetError,BrokenPipeError): break
    except Exception: break
    if subscribed:
     s2=load(); events=s2.get('events',{}).get(subscribed,[])
     for e in events[sent:]:
      self.wfile.write(wsframe(e));self.wfile.flush();sent+=1
  finally:
   try:self.connection.close()
   except:pass
 def ui(self):
  html=UI.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',len(html));self.end_headers();self.wfile.write(html)
UI='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>body{margin:0;font:14px Arial;color:#222}.app{display:flex;height:100vh}.side{width:250px;background:#3f0f3f;color:white;padding:18px}.main{flex:1;padding:25px}.thread{width:320px;border-left:1px solid #ddd;padding:20px}button{border:0;border-radius:4px;padding:9px 14px;color:#fff;margin:3px}.primary{background:#007a5a}.danger{background:#e01e5a}.secondary{background:#1264a3}input,textarea{padding:9px;margin:4px;border:1px solid #bbb;border-radius:4px}.modal{padding:20px}.hidden{display:none}.msg{padding:12px;border-bottom:1px solid #eee}.emoji{display:inline-block}</style></head><body><div id=app class=app><aside class=side><h2 data-testid=workspace-header>Huddle</h2><div data-testid=current-user></div><button data-testid=logout-btn data-button-role=danger class=danger>Logout</button><h3>Channels</h3><div data-testid=channel-list></div><div data-testid=dms-list></div><button data-testid=new-channel-btn data-button-role=secondary class=secondary>+ Channel</button></aside><main class=main><h2><span data-testid=channel-title># general</span></h2><div data-testid=channel-topic></div><div data-testid=message-list></div><form id=sendform><input data-testid=message-input id=message-input placeholder="Message"><button data-testid=send-btn data-button-role=primary class=primary>Send</button></form><div id=empty>Nothing here yet — start the conversation.</div></main><aside class=thread hidden data-testid=thread-panel><button data-testid=close-thread data-button-role=secondary class=secondary>Close</button><div id=replies></div><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary class=primary>Reply</button></aside></div><div id=auth-modal class=modal><form data-testid=auth-form id=auth><h2>Sign in to Huddle</h2><input name=username placeholder=username required><input name=password type=password placeholder=password required><input name=display_name placeholder="display name"><div id=auth-error></div><button type=submit data-testid=auth-submit data-button-role=primary class=primary>Sign in</button><button type=button data-testid=auth-toggle data-button-role=secondary class=secondary>Sign up</button></form></div><div id=create-channel-modal hidden><form data-testid=create-channel-form><input name=name><input name=topic><label><input type=checkbox name=is_private data-testid=create-channel-private>Private</label><button data-testid=create-channel-submit data-button-role=primary class=primary>Create</button><button type=button data-testid=create-channel-cancel data-button-role=secondary class=secondary>Cancel</button></form></div><script>
let token=localStorage.getItem('huddle.token'),me,ws,cur;const $=x=>document.querySelector(x),api=async(u,o={})=>{o.headers={...(o.headers||{}),Authorization:'Bearer '+token,'Content-Type':'application/json'};let r=await fetch('/api'+u,o);return r.json()};function show(){ $('#auth-modal').style.display='none';load()}async function load(){me=(await api('/auth/me')).user;$('.side [data-testid=current-user]').textContent=me.display_name;let x=await api('/workspaces');if(!x.workspaces.length){let z=await api('/workspaces',{method:'POST',body:JSON.stringify({slug:'my-team',name:'My Team'})});x.workspaces=[z.workspace]}let w=await api('/workspaces/'+x.workspaces[0].slug);cur=w.channels[0];renderChannels(w.channels);select(cur)}function renderChannels(cs){$('[data-testid=channel-list]').innerHTML=cs.map(c=>`<div data-testid=channel-entry data-channel-id="${c.id}" data-channel-name="${c.name}" onclick='select(${JSON.stringify(c)})'># ${c.name}</div>`).join('')}async function select(c){cur=c;$('[data-testid=channel-title]').textContent='# '+c.name;let x=await api('/channels/'+c.id+'/messages');$('[data-testid=message-list]').innerHTML=x.messages.reverse().map(m=>`<div class=msg data-testid=message><b>${m.author.display_name}</b> <span data-testid=message-body>${m.body}</span> ${m.edited_at?'(edited)':''}<button data-testid=open-thread-btn onclick='thread("${m.id}")'>${m.reply_count||0} replies</button><button data-testid=reaction-button onclick='react("${m.id}","👍")'>👍</button></div>`).join('')||'<p>Nothing here yet — start the conversation.</p>'}$('#auth').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target),mode=e.target.dataset.signup;let x=await fetch('/api/auth/'+(mode?'register':'login'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(f))});let j=await x.json();if(!x.ok){$('#auth-error').textContent=j.error||'Invalid input';return}token=j.token;localStorage.setItem('huddle.token',token);window.__huddle_token=token;show()};$('[data-testid=auth-toggle]').onclick=()=>{$('#auth').dataset.signup=!$('#auth').dataset.signup;$('.modal h2').textContent=$('#auth').dataset.signup?'Create account':'Sign in to Huddle'};$('#sendform').onsubmit=async e=>{e.preventDefault();let i=$('#message-input');if(!i.value.trim())return;await api('/channels/'+cur.id+'/messages',{method:'POST',body:JSON.stringify({body:i.value})});i.value='';select(cur)};async function react(id,e){await api('/messages/'+id+'/reactions',{method:'POST',body:JSON.stringify({emoji:e})});select(cur)}async function thread(id){$('.thread').hidden=false;$('#replies').textContent='Thread';}if(token)show();
</script></body></html>'''
def run_http(port,node):
 os.environ['NODE_ID']=str(node);ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
def irc():
 ls=socket.socket();ls.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);ls.bind(('0.0.0.0',6667));ls.listen()
 while True:
  c,_=ls.accept()
  threading.Thread(target=lambda:irc_client(c),daemon=True).start()
def irc_client(c):
 nick=None; token=None; uid=None; joined={}
 def out(x):
  try:c.send((x+'\\r\
').encode())
  except:raise
 try:
  c.settimeout(1)
  while uid is None or nick is None:
   line=c.recv(4096).decode(errors='ignore')
   if not line: return
   for x in line.splitlines():
    a=x.split(' ',2);cmd=a[0].upper()
    if cmd=='PASS' and len(a)>1:
     token=a[1]; ss=load();uid=ss.get('tokens',{}).get(token)
     if not uid: out(':huddle 464 * :Password incorrect')
    elif cmd=='NICK' and len(a)>1:
     ss=load()
     if nick==a[1]: out(':huddle 433 * '+a[1]+' :Nickname in use')
     else:nick=a[1]
    elif cmd=='USER' and len(a)>1: pass
    elif cmd=='PING':out('PONG '+(a[1] if len(a)>1 else ''))
    elif cmd not in ('PASS','NICK','USER'):out(':huddle 451 * :You have not registered')
   if uid and nick:
    out(':huddle 001 '+nick+' :Welcome');out(':huddle 002 '+nick+' :Your host');out(':huddle 003 '+nick+' :Created');out(':huddle 004 '+nick+' huddle');out(':huddle 005 '+nick+' NETWORK=huddle');out(':huddle 422 '+nick+' :MOTD')
  while True:
   line=c.recv(4096).decode(errors='ignore')
   if not line:return
   for x in line.splitlines():
    a=x.split(' ',2);cmd=a[0].upper()
    if cmd=='PING':out('PONG '+(a[1] if len(a)>1 else ''))
    elif cmd=='QUIT':return
    elif cmd=='JOIN' and len(a)>1:
     name=a[1];ss=load(); found=None
     for ch in ss.get('channels',{}).values():
      w=ss.get('workspaces',{}).get(ch.get('workspace_id'),{})
      if '#'+w.get('slug','')+'/'+ch.get('name','')==name:found=ch;break
     if not found:out(':huddle 403 '+nick+' '+name+' :No such channel');continue
     joined[name]=found['id'];out(':'+nick+'!user@localhost JOIN '+name);out(':huddle 353 '+nick+' = '+name+' :'+nick);out(':huddle 366 '+nick+' '+name+' :End of NAMES')
    elif cmd=='NAMES' and len(a)>1:
     name=a[1];out(':huddle 353 '+nick+' = '+name+' :'+nick);out(':huddle 366 '+nick+' '+name+' :End of NAMES')
    elif cmd=='PRIVMSG' and len(a)>2:
     name=a[1];body=a[2][1:] if a[2].startswith(':') else a[2];cid=joined.get(name)
     if cid:
      ss=load();mid=rid();m={'id':mid,'channel_id':cid,'author_id':uid,'body':body,'parent_id':None,'reply_count':0,'created_at':now(),'edited_at':None,'reactions':[],'mentions':[],'files':[]};ss['messages'][mid]=m;emit(ss,cid,'message.created',{'message':msgobj(m,ss)});save(ss)
    elif cmd in ('WHO','LIST','TOPIC','MODE'): pass
    else:out(':huddle 421 '+nick+' '+cmd+' :Unknown command')
 except Exception: pass
 finally:
  try:c.close()
  except:pass

if __name__=='__main__':
 if os.environ.get('IRC')=='1':irc()
 else:run_http(int(os.environ.get('PORT','8000')),int(os.environ.get('NODE_ID','0')))
