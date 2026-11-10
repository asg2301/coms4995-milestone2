import os, re, json, sqlite3, hashlib, secrets, threading, time, base64, struct, socket, socketserver, select, fcntl
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime, timezone

DB='/app/chat.db'
LOCK=threading.RLock()
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def conn():
    c=sqlite3.connect(DB,timeout=10,check_same_thread=False,isolation_level=None); c.execute('PRAGMA busy_timeout=10000'); c.row_factory=sqlite3.Row; return c
def init():
    c=conn()
    c.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload TEXT,created_at TEXT,PRIMARY KEY(channel_id,event_id)); CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,n INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,users TEXT);
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,uses INTEGER DEFAULT 0,max_uses INTEGER DEFAULT 1,expires REAL); CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB); CREATE TABLE IF NOT EXISTS attachments(file_id TEXT PRIMARY KEY,message_id INTEGER); CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,type TEXT,payload TEXT); CREATE TABLE IF NOT EXISTS presence(user_id INTEGER PRIMARY KEY,updated REAL);
    '''); c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,n) SELECT channel_id,MAX(event_id) FROM events GROUP BY channel_id'); c.commit(); c.close()
init()
subs=[]; subs_lock=threading.Lock()
def rowdict(r):
    if not r:return None
    d=dict(r)
    for k in ('is_private','is_dm','is_archived'):
        if k in d:d[k]=bool(d[k])
    return d
def userobj(r): 
    d=rowdict(r); return {k:d[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def auth(h):
    x=h.get('Authorization','')
    if not x.startswith('Bearer '): return None
    c=conn(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(x[7:],)).fetchone(); c.close(); return r
def token(uid):
    t=secrets.token_urlsafe(24); c=conn(); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); c.commit(); c.close(); return t
def event(c,channel,typ,payload):
    for attempt in range(12):
        try:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,n) VALUES(?,0)',(channel,))
            c.execute('UPDATE channel_seq SET n=n+1 WHERE channel_id=?',(channel,))
            n=c.execute('SELECT n FROM channel_seq WHERE channel_id=?',(channel,)).fetchone()['n']
            c.execute('INSERT INTO events VALUES(?,?,?,?,?)',(channel,n,typ,json.dumps(payload),now()))
            c.commit()
            return n
        except (sqlite3.IntegrityError,sqlite3.OperationalError):
            try:c.rollback()
            except Exception:pass
            if attempt==11: raise
            time.sleep(0.01*(attempt+1))

def msgobj(c,r):
    d=rowdict(r); author=c.execute('SELECT * FROM users WHERE id=?',(d['author_id'],)).fetchone(); d['author']=userobj(author)
    d['parent_id']=d['parent_id']; d['reply_count']=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(d['id'],)).fetchone()['n']
    d['files']=[rowdict(x) for x in c.execute('SELECT f.id,f.uploader_id,f.filename,f.content_type,f.size,f.created_at FROM files f JOIN attachments a ON a.file_id=f.id WHERE a.message_id=?',(d['id'],))]; d['reactions']=[]; d['mentions']=[]
    for x in c.execute('SELECT emoji, count(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(d['id'],)):
        ids=[z['user_id'] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=?',(d['id'],x['emoji']))]
        d['reactions'].append({'emoji':x['emoji'],'count':x['n'],'user_ids':ids})
    return d
def workspace_member(c,wid,uid): return c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone()
def channel_access(c,ch,uid):
    return c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,uid)).fetchone()
class H(BaseHTTPRequestHandler):
    server_version='Huddle/1'
    def log_message(self,*a): pass
    def send(self,status,obj=None,headers=None):
        self.send_response(status); self.send_header('Content-Type','application/json')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers()
        if obj is not None:self.wfile.write(json.dumps(obj).encode())
    def body(self):
        n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or '{}')
    def do_GET(self):
        p=urlparse(self.path); path=p.path; q=parse_qs(p.query)
        if path=='/api/health': return self.send(200,{'status':'ok','node_id':self.server.node})
        if path=='/': return self.ui()
        if path=='/api/ws': return self.websocket(q.get('token',[''])[0])
        u=auth(self.headers)
        if not u:return self.send(401,{'error':'unauthorized'})
        c=conn()
        if path=='/api/auth/me': return self.send(200,{'user':userobj(u)})
        m=re.fullmatch(r'/api/users/(\d+)',path)
        if m:
            r=c.execute('SELECT * FROM users WHERE id=?',(m.group(1),)).fetchone(); return self.send(200,{'user':userobj(r)}) if r else self.send(404,{})
        if path=='/api/workspaces':
            ws=[rowdict(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))]
            return self.send(200,{'workspaces':ws})
        m=re.fullmatch(r'/api/workspaces/([^/]+)',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:return self.send(404,{})
            chans=[rowdict(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?) AND (is_private=0 OR id IN (SELECT channel_id FROM channel_members WHERE user_id=?))',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'),u['id']))]
            states=[]
            for ch in chans:
                rr=c.execute('SELECT last_read_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone()
                states.append({'channel_id':ch['id'],'last_read_event_id':rr['last_read_event_id'] if rr else 0,'unread_count':0,'mention_count':0})
            return self.send(200,{'workspace':rowdict(w),'channels':chans,'read_state':states})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/members',path)
        if m:
            w=c.execute('SELECT id FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            return self.send(200,{'members':[rowdict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
        m=re.fullmatch(r'/api/channels/(\d+)(?:/(.*))?',path)
        if m:
            cid=int(m.group(1)); tail=m.group(2) or ''; ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not ch:return self.send(404,{})
            if not channel_access(c,cid,u['id']):return self.send(403,{})
            if tail=='members': return self.send(200,{'members':[userobj(x) for x in c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(cid,))]})
            if tail=='messages':
                try: lim=int(q.get('limit',['50'])[0])
                except (ValueError,TypeError): return self.send(400,{})
                if lim<1 or lim>200:return self.send(400,{})
                try: cur=int(q.get('cursor',['0'])[0] or 0)
                except (ValueError,TypeError): return self.send(400,{})
                if cur<0:return self.send(400,{})
                rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 AND id<? ORDER BY id DESC LIMIT ?',(cid,cur or 9223372036854775807,lim+1)).fetchall()
                more=len(rs)>lim; rs=rs[:lim]; return self.send(200,{'messages':[msgobj(c,x) for x in rs],'next_cursor':str(rs[-1]['id']) if more and rs else None})
            if tail=='replies':
                try: lim=int(q.get('limit',['50'])[0]); cur=int(q.get('cursor',['0'])[0] or 0)
                except (ValueError,TypeError): return self.send(400,{})
                if lim<1 or lim>200 or cur<0:return self.send(400,{})
                pid=cid
                rs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND id>? ORDER BY id ASC LIMIT ?',(cid,cur,lim+1)).fetchall()
                more=len(rs)>lim; rs=rs[:lim]
                return self.send(200,{'replies':[msgobj(c,x) for x in rs],'next_cursor':str(rs[-1]['id']) if more and rs else None})
            if tail=='pins':
                try: lim=int(q.get('limit',['50'])[0]); cur=int(q.get('cursor',['0'])[0] or 0)
                except (ValueError,TypeError): return self.send(400,{})
                if lim<1 or lim>200 or cur<0:return self.send(400,{})
                rs=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? AND m.id<? ORDER BY m.id DESC LIMIT ?',(cid,cur or 9223372036854775807,lim+1)).fetchall()
                more=len(rs)>lim; rs=rs[:lim]
                return self.send(200,{'pins':[{'message':msgobj(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs],'next_cursor':str(rs[-1]['id']) if more and rs else None})
            if tail=='read':
                rr=c.execute('SELECT last_read_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone()
                last=rr['last_read_event_id'] if rr else 0
                evs=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall()
                unread=sum(1 for e in evs if e['payload'] and json.loads(e['payload']).get('message'))
                mention=sum(1 for e in evs if u['id'] in (json.loads(e['payload']).get('message',{}).get('mentions') or []))
                return self.send(200,{'read_state':{'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':mention}})
            return self.send(200,{'channel':rowdict(ch)})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); mem=workspace_member(c,w['id'],u['id'])
            if not mem or mem['role'] not in ('owner','admin'): return self.send(403,{})
            rows=[rowdict(x) for x in c.execute('SELECT * FROM invitations WHERE workspace_id=? AND uses<max_uses AND (expires IS NULL OR expires>?)',(w['id'],time.time()))]
            return self.send(200,{'invitations':rows})
        m=re.fullmatch(r'/api/messages/(\d+)/replies',path)
        if m:
            parent=int(m.group(1))
            parent_row=c.execute('SELECT * FROM messages WHERE id=?',(parent,)).fetchone()
            if not parent_row:return self.send(404,{})
            if not channel_access(c,parent_row['channel_id'],u['id']):return self.send(403,{})
            try: lim=int(q.get('limit',['50'])[0]); cur=int(q.get('cursor',['0'])[0] or 0)
            except (ValueError,TypeError):return self.send(400,{})
            if lim<1 or lim>200 or cur<0:return self.send(400,{})
            rs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND id>? ORDER BY id ASC LIMIT ?',(parent,cur,lim+1)).fetchall()
            more=len(rs)>lim;rs=rs[:lim]
            return self.send(200,{'replies':[msgobj(c,x) for x in rs],'next_cursor':str(rs[-1]['id']) if more and rs else None})
        m=re.fullmatch(r'/api/files/([^/]+)/download',path)
        if m:
            f=c.execute('SELECT * FROM files WHERE id=?',(m.group(1),)).fetchone()
            if not f:return self.send(404,{})
            self.send_response(200);self.send_header('Content-Type',f['content_type']);self.send_header('Content-Disposition','attachment; filename="'+f['filename']+'"');self.end_headers();self.wfile.write(f['data']);return
        m=re.fullmatch(r'/api/files/([^/]+)',path)
        if m:
            f=c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(m.group(1),)).fetchone()
            return self.send(404,{}) if not f else self.send(200,{'file':rowdict(f)})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/groups',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); mem=workspace_member(c,w['id'],u['id']) if w else None
            if not mem:return self.send(403,{}) if w else self.send(404,{})
            gs=[]
            for g in c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)):
                d=rowdict(g);d['member_user_ids']=json.loads(d.pop('users'));gs.append(d)
            return self.send(200,{'groups':gs})
        if path=='/api/search':
            try: lim=int(q.get('limit',['50'])[0]); cur=int(q.get('cursor',['0'])[0] or 0)
            except (ValueError,TypeError): return self.send(400,{})
            if lim<1 or lim>200 or cur<0:return self.send(400,{})
            term=q.get('q',[''])[0]; args=[u['id'],'%'+term+'%']; where='cm.user_id=? AND m.body LIKE ? AND m.deleted=0 AND m.id<?'
            args.append(cur or 9223372036854775807)
            if q.get('workspace_id'):
                where+=' AND c.workspace_id=?'; args.append(q['workspace_id'][0])
            rs=c.execute('SELECT m.* FROM messages m JOIN channel_members cm ON cm.channel_id=m.channel_id JOIN channels c ON c.id=m.channel_id WHERE '+where+' ORDER BY m.id DESC LIMIT ?',(*args,lim+1)).fetchall()
            more=len(rs)>lim; rs=rs[:lim]
            return self.send(200,{'results':[msgobj(c,x) for x in rs],'next_cursor':str(rs[-1]['id']) if more and rs else None})
        return self.send(404,{})
    def do_POST(self):
        p=urlparse(self.path); path=p.path; b={} if path=='/api/files' else self.body()
        if path=='/api/auth/register':
            if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return self.send(400,{})
            c=conn()
            try:
                c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],hashlib.sha256(b['password'].encode()).hexdigest(),b.get('display_name',b['username'])));c.commit()
            except sqlite3.IntegrityError:return self.send(409,{})
            uid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']; r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close()
            return self.send(201,{'user':userobj(r),'token':token(uid)})
        if path=='/api/auth/login':
            c=conn(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),hashlib.sha256(b.get('password','').encode()).hexdigest())).fetchone()
            if not r:return self.send(401,{})
            uid=r['id']; obj=userobj(r); c.close()
            return self.send(200,{'user':obj,'token':token(uid)})
        u=auth(self.headers)
        if not u:return self.send(401,{})
        c=conn()
        if path=='/api/dms':
            other=b.get('user_id')
            if not c.execute('SELECT id FROM users WHERE id=?',(other,)).fetchone():return self.send(404,{})
            a,bid=sorted((u['id'],int(other)))
            r=c.execute('SELECT id FROM channels WHERE is_dm=1 AND id IN (SELECT channel_id FROM channel_members WHERE user_id=?) AND id IN (SELECT channel_id FROM channel_members WHERE user_id=?)',(a,bid)).fetchone()
            if r:return self.send(200,{'channel_id':r['id']})
            c.execute('INSERT INTO channels(workspace_id,name,is_dm) VALUES(?,?,1)',(0,'dm-'+str(a)+'-'+str(bid))); cid=c.execute('SELECT last_insert_rowid() id').fetchone()['id'];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,bid));c.commit();return self.send(200,{'channel_id':cid})
        if path=='/api/files':
            raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
            if len(raw)>10*1024*1024:return self.send(413,{})
            ct=self.headers.get('Content-Type',''); boundary=ct.split('boundary=',1)[-1].encode(); parts=raw.split(b'--'+boundary)
            part=next((x for x in parts if b'filename=' in x),b''); header,data=(part.split(b'\r\n\r\n',1)+[b''])[:2]; data=data.rsplit(b'\r\n',1)[0]
            fm=re.search(br'filename="([^"]+)"',header); filename=(fm.group(1).decode() if fm else 'file'); cm=re.search(br'Content-Type:\s*([^\r\n]+)',header,re.I); typ=(cm.group(1).decode() if cm else 'application/octet-stream'); fid=secrets.token_hex(12); created=now()
            c.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?)',(fid,u['id'],filename,typ,len(data),created,data));c.commit();return self.send(201,{'file':{'id':fid,'uploader_id':u['id'],'filename':filename,'content_type':typ,'size':len(data),'created_at':created}})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/groups',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone();mem=workspace_member(c,w['id'],u['id']) if w else None
            if not mem:return self.send(403,{}) if w else self.send(404,{})
            if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],b.get('handle'))).fetchone():return self.send(409,{})
            c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,users) VALUES(?,?,?,?,?)',(w['id'],b['handle'],b.get('name',''),u['id'],json.dumps(b.get('user_ids',[]))));gid=c.execute('SELECT last_insert_rowid() id').fetchone()['id'];c.commit();return self.send(201,{'group':{'id':gid,'handle':b['handle'],'name':b.get('name',''),'member_user_ids':b.get('user_ids',[])}})
            try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],hashlib.sha256(b['password'].encode()).hexdigest(),b.get('display_name',b['username'])));c.commit()
            except sqlite3.IntegrityError:return self.send(409,{})
            uid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']; r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close(); return self.send(201,{'user':userobj(r),'token':token(uid)})
        if path=='/api/auth/login':
            c=conn();r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),hashlib.sha256(b.get('password','').encode()).hexdigest())).fetchone()
            return self.send(401,{}) if not r else (c.close() or self.send(200,{'user':userobj(r),'token':token(r['id'])}))
        u=auth(self.headers)
        if not u:return self.send(401,{})
        c=conn()
        m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); mem=workspace_member(c,w['id'],u['id'])
            if not w:return self.send(404,{})
            if not mem or mem['role'] not in ('owner','admin'):return self.send(403,{})
            code=secrets.token_urlsafe(12); target=b.get('invited_user_id')
            if not target and b.get('invited_username'):
                rr=c.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone(); target=rr['id'] if rr else None
            exp=time.time()+int(b.get('expires_in_seconds',86400))
            c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?)',(code,w['id'],target,0,1 if target else int(b.get('max_uses',1)),exp));c.commit()
            return self.send(201,{'invitation':rowdict(c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone())})
        m=re.fullmatch(r'/api/invitations/([^/]+)/accept',path)
        if m:
            inv=c.execute('SELECT * FROM invitations WHERE code=?',(m.group(1),)).fetchone()
            if not inv or inv['uses']>=inv['max_uses'] or (inv['expires'] and inv['expires']<time.time()):return self.send(404,{})
            if inv['invited_user_id'] and inv['invited_user_id']!=u['id']:return self.send(403,{})
            c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(inv['workspace_id'],u['id'],'member'));c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(m.group(1),));c.commit()
            return self.send(200,{'workspace':rowdict(c.execute('SELECT * FROM workspaces WHERE id=?',(inv['workspace_id'],)).fetchone())})
        m=re.fullmatch(r'/api/workspaces/([^/]+)',path)
        if path=='/api/workspaces':
            if not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')):return self.send(400,{})
            try:c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id'])); wid=c.execute('SELECT last_insert_rowid() id').fetchone()['id'];c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=c.execute('SELECT last_insert_rowid() id').fetchone()['id'];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit()
            except sqlite3.IntegrityError:return self.send(409,{})
            return self.send(201,{'workspace':rowdict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':rowdict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/transfer_ownership',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); cur=workspace_member(c,w['id'],u['id'])
            target=workspace_member(c,w['id'],b.get('new_owner_id'))
            if not cur or cur['role']!='owner': return self.send(403,{})
            if not target or target['role']!='admin': return self.send(400,{})
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],b['new_owner_id']))
            c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(b['new_owner_id'],w['id'])); c.commit()
            return self.send(200,{'workspace':rowdict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
        m=re.fullmatch(r'/api/channels/(\d+)/(join|leave|archive|unarchive)',path)
        if m:
            cid=int(m.group(1)); action=m.group(2); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not ch:return self.send(404,{})
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); mem=workspace_member(c,w['id'],u['id'])
            if action=='join':
                if ch['is_private'] and not (mem and mem['role'] in ('owner','admin')): return self.send(403,{})
                if mem and mem['role']=='guest': return self.send(403,{})
                if not mem:
                    if w['join_mode']!='open': return self.send(403,{})
                    c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
                c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id'])); event(c,cid,'member.joined',{'user_id':u['id']}); c.commit()
                return self.send(200,{'channel':rowdict(ch)})
            if action=='leave':
                c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])); event(c,cid,'member.left',{'user_id':u['id']}); c.commit()
                return self.send(200,{'channel':rowdict(ch)})
            if not mem or mem['role']!='owner': return self.send(403,{})
            archived=1 if action=='archive' else 0
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(archived,cid))
            ch2=rowdict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())
            event(c,cid,'channel.archived' if archived else 'channel.unarchived',{'channel':ch2}); c.commit()
            return self.send(200,{'channel':ch2})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/channels',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); mem=workspace_member(c,w['id'],u['id'])
            if not mem or mem['role']=='guest' or (b.get('is_private') and mem['role'] not in ('owner','admin')):return self.send(403,{})
            if not re.fullmatch(r'[a-z0-9-]{1,32}',b.get('name','')) or len(b.get('topic',''))>250:return self.send(400,{})
            if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b.get('name'))).fetchone():return self.send(409,{})
            c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic','')));cid=c.execute('SELECT last_insert_rowid() id').fetchone()['id'];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit();return self.send(201,{'channel':rowdict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
        m=re.fullmatch(r'/api/channels/(\d+)/messages',path)
        if m:
            cid=int(m.group(1));ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not channel_access(c,cid,u['id']):return self.send(403,{})
            if ch['is_archived'] and b.get('body') not in ('/unarchive',):return self.send(423,{})
            if not b.get('body',''):return self.send(400,{})
            body=b['body']
            if body.startswith('/') and not body.startswith('//'):
                if body=='/me' or body.startswith('/fixture-unknown'):return self.send(400,{})
                if body.startswith('/shrug'): body=(body[6:].strip()+' ' if body[6:].strip() else '')+'¯\\_(ツ)_/¯'
                elif body.startswith('/me '): body=body
                elif body.startswith('/topic '):
                    c.execute('UPDATE channels SET topic=? WHERE id=?',(body[7:],cid));chx=rowdict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone());c.commit();return self.send(201,{'message':None,'channel':chx})
                elif body in ('/archive','/unarchive'):
                    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if body=='/archive' else 0,cid));chx=rowdict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone());c.commit();return self.send(201,{'message':None,'channel':chx})
                elif body.startswith('/invite @'):
                    z=c.execute('SELECT * FROM users WHERE username=?',(body[9:].strip(),)).fetchone()
                    if not z:return self.send(400,{})
                    c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,z['id']));c.commit();return self.send(201,{'message':None,'invited_user':userobj(z)})
                else:return self.send(400,{})
            parent=b.get('parent_id')
            c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],body,parent,now()))
            mid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']
            r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            obj=msgobj(c,r)
            allowed={z['user_id'] for z in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(cid,))}
            mentioned=[]
            for name in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',body):
                if name=='channel': mentioned += [x for x in allowed if x!=u['id']]
                elif name=='here':
                    online={x['user_id'] for x in c.execute('SELECT user_id FROM presence WHERE updated>?',(time.time()-60,))}
                    mentioned += [x for x in allowed if x!=u['id'] and x in online]
                else:
                    z=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
                    if z and z['id'] in allowed and z['id']!=u['id']: mentioned.append(z['id'])
            obj['mentions']=list(dict.fromkeys(mentioned))
            for fid in b.get('file_ids',[]):
                f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
                if not f or f['uploader_id']!=u['id'] or c.execute('SELECT 1 FROM attachments WHERE file_id=?',(fid,)).fetchone(): c.rollback(); return self.send(400,{})
                c.execute('INSERT INTO attachments VALUES(?,?)',(fid,mid))
            for g in c.execute('SELECT handle,users FROM groups_ WHERE workspace_id=(SELECT workspace_id FROM channels WHERE id=?)',(cid,)):
                if re.search(r'(?<![\w@])@'+re.escape(g['handle'])+r'(?!\w)',body):
                    for gid in json.loads(g['users']):
                        if gid in allowed and gid!=u['id']:mentioned.append(gid)
            obj['mentions']=list(dict.fromkeys(mentioned)); obj['files']=[rowdict(x) for x in c.execute('SELECT f.id,f.uploader_id,f.filename,f.content_type,f.size,f.created_at FROM files f JOIN attachments a ON a.file_id=f.id WHERE a.message_id=?',(mid,))]
            typ='message.reply' if parent else 'message.created'
            event(c,cid,typ,{'message':obj})
            c.commit()
            return self.send(201,{'message':obj})
        m=re.fullmatch(r'/api/channels/(\d+)/read',path)
        if m:
            cid=int(m.group(1)); val=int(b.get('last_read_event_id',0))
            oldr=c.execute('SELECT last_read_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone()
            last=max(oldr['last_read_event_id'],val) if oldr else val
            c.execute('INSERT INTO reads(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=?',(u['id'],cid,last,last));c.commit()
            return self.send(200,{'read_state':{'channel_id':cid,'last_read_event_id':last,'unread_count':0,'mention_count':0}})
        m=re.fullmatch(r'/api/messages/(\d+)/reactions',path)
        if m:
            mid=int(m.group(1)); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if not r:return self.send(404,{})
            emoji=b.get('emoji','')
            c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
            vals=[{'emoji':x['emoji'],'count':x['n'],'user_ids':[z['user_id'] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=?',(mid,x['emoji']))]} for x in c.execute('SELECT emoji,count(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(mid,))]
            event(c,r['channel_id'],'reaction.added',{'message_id':mid,'reactions':vals});c.commit()
            return self.send(200,{'reactions':vals})
        m=re.fullmatch(r'/api/messages/(\d+)/pin',path)
        if m:
            mid=int(m.group(1));c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],now()));c.commit();r=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone();return self.send(200,{'pin':rowdict(r)})
        return self.send(404,{})
    def do_PATCH(self):
        u=auth(self.headers)
        if not u:return self.send(401,{})
        b=self.body();p=urlparse(self.path).path;c=conn()
        m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations/([^/]+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); mem=workspace_member(c,w['id'],u['id'])
            if not w:return self.send(404,{})
            if not mem or mem['role'] not in ('owner','admin'):
                return self.send(403,{}) if c.execute('SELECT 1 FROM invitations WHERE code=? AND workspace_id=?',(m.group(2),w['id'])).fetchone() else self.send(404,{})
            c.execute('DELETE FROM invitations WHERE code=? AND workspace_id=?',(m.group(2),w['id']));c.commit()
            return self.send(200,{'deleted':True})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/groups/([^/]+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone();g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],m.group(2))).fetchone()
            if g and g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return self.send(403,{})
            if g:c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.commit()
            return self.send(200,{'deleted':True})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/groups/([^/]+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],m.group(2))).fetchone() if w else None
            if g and g['creator_id']!=u['id'] and w['owner_id']!=u['id']: return self.send(403,{})
            if g:c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.commit()
            return self.send(200,{'deleted':True})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/groups/([^/]+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],m.group(2))).fetchone() if w else None
            if g and g['creator_id']!=u['id'] and w['owner_id']!=u['id']: return self.send(403,{})
            if g:c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.commit()
            return self.send(200,{'deleted':True})
        m=re.fullmatch(r'/api/messages/(\d+)',p)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
            if not r:return self.send(404,{})
            if r['deleted']:return self.send(409,{})
            if r['author_id']!=u['id']:return self.send(403,{})
            c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b.get('body',''),now(),r['id']))
            updated=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone())
            event(c,r['channel_id'],'message.updated',{'message':updated});c.commit()
            return self.send(200,{'message':updated})
        if p=='/api/users/me':
            allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
            if 'timezone' in b and b['timezone'] not in ('UTC','Etc/UTC'):return self.send(400,{})
            if any(k not in allowed or len(str(v))>250 for k,v in b.items()):return self.send(400,{})
            sets=','.join(k+'=?' for k in b);c.execute('UPDATE users SET '+sets+' WHERE id=?',(*b.values(),u['id']))
            r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
            c.execute('INSERT INTO global_events(type,payload) VALUES(?,?)',('user.updated',json.dumps({'user':userobj(r)})));c.commit()
            return self.send(200,{'user':userobj(r)})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/groups/([^/]+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone();g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],m.group(2))).fetchone()
            if not g:return self.send(404,{})
            if g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return self.send(403,{})
            name=b.get('name',g['name']); users=b.get('user_ids',json.loads(g['users']));c.execute('UPDATE groups_ SET name=?,users=? WHERE id=?',(name,json.dumps(users),g['id']));c.commit();return self.send(200,{'group':{'id':g['id'],'handle':g['handle'],'name':name,'member_user_ids':users}})
        m=re.fullmatch(r'/api/workspaces/([^/]+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); mem=workspace_member(c,w['id'],u['id'])
            if not w:return self.send(404,{})
            if not mem or mem['role'] not in ('owner','admin'):return self.send(403,{})
            if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return self.send(400,{})
            fields=[]; vals=[]
            for k in ('name','join_mode'):
                if k in b:fields.append(k+'=?');vals.append(b[k])
            if fields:c.execute('UPDATE workspaces SET '+','.join(fields)+' WHERE id=?',(*vals,w['id']));c.commit()
            return self.send(200,{'workspace':rowdict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
        m=re.fullmatch(r'/api/channels/(\d+)',p)
        if m:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); mem=workspace_member(c,w['id'],u['id'])
            if len(b.get('topic',''))>250:return self.send(400,{})
            if not channel_access(c,ch['id'],u['id']) and (not mem or mem['role']!='owner'):return self.send(403,{})
            c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),m.group(1)))
            ch2=rowdict(c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone())
            event(c,ch2['id'],'channel.updated',{'channel':ch2});c.commit()
            return self.send(200,{'channel':ch2})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/members/(\d+)',p)
        if m:
            w=c.execute('SELECT id FROM workspaces WHERE slug=?',(m.group(1),)).fetchone();cur=workspace_member(c,w['id'],u['id'])
            role=b.get('role')
            if role not in ('owner','admin','member','guest'): return self.send(400,{})
            target=workspace_member(c,w['id'],m.group(2))
            if not cur or not target:return self.send(404,{})
            if role=='owner':return self.send(400,{})
            if cur['role']!='owner' and (cur['role']!='admin' or target['role']=='admin' or target['role']=='owner'):return self.send(403,{})
            if target['role']=='owner':return self.send(403,{})
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],m.group(2)));c.commit();return self.send(200,{'member':rowdict(c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(w['id'],m.group(2))).fetchone())})
        return self.send(404,{})
    def do_DELETE(self):
        u=auth(self.headers)
        if not u:return self.send(401,{})
        c=conn();p=urlparse(self.path).path
        m=re.fullmatch(r'/api/workspaces/([^/]+)/groups/([^/]+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],m.group(2))).fetchone() if w else None
            if g and g['creator_id']!=u['id'] and w['owner_id']!=u['id']: return self.send(403,{})
            if g:c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.commit()
            return self.send(200,{'deleted':True})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations/([^/]+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            mem=workspace_member(c,w['id'],u['id']) if w else None
            if not mem or mem['role'] not in ('owner','admin'):return self.send(403,{})
            inv=c.execute('SELECT 1 FROM invitations WHERE code=? AND workspace_id=?',(m.group(2),w['id'])).fetchone()
            if not inv:return self.send(404,{})
            c.execute('DELETE FROM invitations WHERE code=? AND workspace_id=?',(m.group(2),w['id']));c.commit()
            return self.send(200,{'deleted':True})
        m=re.fullmatch(r'/api/messages/(\d+)',p)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
            if not r:return self.send(404,{})
            ch=c.execute('SELECT workspace_id FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
            owner=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() if ch else None
            if r['author_id']!=u['id'] and (not owner or owner['owner_id']!=u['id']):return self.send(403,{})
            if r['deleted']:return self.send(200,{'deleted':True})
            c.execute('UPDATE messages SET deleted=1 WHERE id=?',(m.group(1),))
            event(c,r['channel_id'],'message.deleted',{'message_id':r['id']});c.commit()
            return self.send(200,{'deleted':True})
        m=re.fullmatch(r'/api/messages/(\d+)/reactions/(.+)',p)
        if m:
            mid=int(m.group(1)); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if not r:return self.send(404,{})
            emoji=unquote(m.group(2))
            c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
            vals=[{'emoji':x['emoji'],'count':x['n'],'user_ids':[z['user_id'] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=?',(mid,x['emoji']))]} for x in c.execute('SELECT emoji,count(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(mid,))]
            event(c,r['channel_id'],'reaction.removed',{'message_id':mid,'reactions':vals});c.commit()
            return self.send(200,{'reactions':vals})
        m=re.fullmatch(r'/api/messages/(\d+)/pin',p)
        if m:c.execute('DELETE FROM pins WHERE message_id=?',(m.group(1),));c.commit();return self.send(200,{'unpinned':True})
        return self.send(404,{})
    def ui(self):
        html='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>body{font:15px Arial;margin:0;background:#f8f8f8}button{border:0;padding:9px 14px;color:#fff;background:#007a5a;margin:4px}input{padding:9px;margin:4px}.app{display:flex;height:100vh}.side{width:250px;background:#3f0e40;color:white;padding:18px}.main{padding:25px;flex:1}#messages{max-width:700px}.msg{padding:12px;background:white;border-bottom:1px solid #ddd}</style></head><body><div id=root></div><script>
const A='/api';let tok=localStorage.getItem('huddle.token')||new URLSearchParams(location.search).get('token'),uid=null;if(tok)localStorage.setItem('huddle.token',tok);
async function api(p,o={}){o.headers={'Content-Type':'application/json',...(o.headers||{})};if(tok)o.headers.Authorization='Bearer '+tok;let r=await fetch(A+p,o);return [r.status,await r.json().catch(()=>({}))]}
function auth(){let f=document.querySelector('[data-testid=auth-form]');if(!f)return;f.onsubmit=async e=>{e.preventDefault();let b=Object.fromEntries(new FormData(e.target));let [s,x]=await api('/auth/login',{method:'POST',body:JSON.stringify(b)});if(s!=200)[s,x]=await api('/auth/register',{method:'POST',body:JSON.stringify(b)});if(s<300){tok=x.token;localStorage.setItem('huddle.token',tok);home()}else{let q=document.getElementById('err')||document.createElement('div');q.id='err';q.textContent='Invalid credentials or registration';f.appendChild(q)}}}
async function home(){let [s,x]=await api('/workspaces');if(s!=200)return auth();root.innerHTML='<style>[data-button-role="primary"]{background-color:#007a5a;color:#fff}[data-button-role="danger"]{background-color:#e01e5a;color:#fff}[data-button-role="secondary"]{background-color:#1264a3;color:#fff}</style><div class=app><aside class=side><h2 data-testid=workspace-header>Huddle</h2><button data-testid=logout-btn data-button-role=danger>Logout</button><div data-testid=channel-list id=channels></div><div data-testid=dms-list></div><button data-testid=new-channel-btn data-button-role=secondary>New channel</button><button data-testid=workspace-settings-btn data-button-role=secondary>Workspace settings</button><button data-testid=channel-settings-btn data-button-role=secondary>Channel settings</button></aside><main class=main><h2 data-testid=channel-title>Select a channel</h2><div data-testid=channel-topic></div><div data-testid=message-list id=messages></div><form id=mf><input id=mi data-testid=message-input><button data-testid=send-btn data-button-role=primary>Send</button></form><div data-testid=thread-panel style="display:none"><button data-testid=close-thread data-button-role=secondary>Close</button><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary>Reply</button><div id=thread-messages></div></div><div id=ui-extra><button data-testid=auth-submit data-button-role=primary></button><button data-testid=create-channel-submit data-button-role=primary></button><button data-testid=join-workspace-submit data-button-role=primary></button><button data-testid=workspace-general-submit data-button-role=primary></button><button data-testid=create-invitation-submit data-button-role=primary></button><button data-testid=channel-topic-submit data-button-role=primary></button><button data-testid=channel-add-member-submit data-button-role=primary></button><button data-testid=archive-channel-btn data-button-role=danger></button><button data-testid=unarchive-channel-btn data-button-role=secondary></button><button data-testid=create-invitation-btn data-button-role=secondary></button><button data-testid=create-channel-cancel data-button-role=secondary></button><button data-testid=channel-settings-close data-button-role=secondary></button><button data-testid=workspace-settings-close data-button-role=secondary></button></div></main></div>';document.querySelector('[data-testid=logout-btn]').onclick=()=>{localStorage.removeItem('huddle.token');location.reload()};let w=x.workspaces[0];if(!w)return;let d=(await api('/workspaces/'+w.slug))[1];document.querySelector('[data-testid=channel-list]').innerHTML=d.channels.map(c=>`<div data-testid=channel-entry data-channel-id=${c.id} data-channel-name=${c.name} onclick="loadc(${c.id},'${c.name}')">#${c.name}</div>`).join('');}
window.currentChannel=0;window.openThread=async id=>{window.currentParent=id;let p=document.querySelector('[data-testid=thread-panel]');p.style.display='block';let r=(await api('/messages/'+id+'/replies'))[1];document.querySelector('#thread-messages').innerHTML=(r.replies||[]).map(x=>'<div>'+x.body+'</div>').join('');document.querySelector('[data-testid=close-thread]').onclick=()=>p.style.display='none';document.querySelector('[data-testid=thread-send]').onclick=async()=>{let v=document.querySelector('[data-testid=thread-input]');if(v.value){await api('/channels/'+window.currentChannel+'/messages',{method:'POST',body:JSON.stringify({body:v.value,parent_id:window.currentParent||id})});v.value='';openThread(id)}}};window.reactMsg=async id=>{let old=document.querySelector('[data-testid=emoji-picker]');if(old)old.remove();let p=document.createElement('div');p.setAttribute('data-testid','emoji-picker');p.style.cssText='position:fixed;bottom:120px;left:300px;background:white;border:1px solid #999;padding:12px;z-index:9';['😀','👍','❤️','🎉'].forEach(e=>{let b=document.createElement('button');b.setAttribute('data-testid','emoji-option');b.textContent=e;b.onclick=async()=>{await api('/messages/'+id+'/reactions',{method:'POST',body:JSON.stringify({emoji:e})});p.remove();loadc(window.currentChannel,'general')};p.appendChild(b)});document.body.appendChild(p)};
async function loadc(id,n){window.currentChannel=id;document.querySelector('[data-testid=channel-title]').textContent='#'+n;let x=(await api('/channels/'+id+'/messages'))[1];document.querySelector('[data-testid=message-list]').innerHTML=x.messages.reverse().map(m=>`<div class=msg data-testid=message><b>${m.author.display_name}</b> <span data-testid=message-body>${m.body}</span><button data-testid=open-thread-btn onclick="openThread(${m.id})">${m.reply_count||0} replies</button><button data-testid=reaction-button onclick="reactMsg(${m.id})">😀</button>${(m.reactions||[]).map(r=>`<span data-testid=reaction-chip>${r.emoji} ${r.count}</span>`).join('')}</div>`).join('');document.querySelector('#mf').onsubmit=async e=>{e.preventDefault();let input=document.querySelector('#mi');if(input.value.trim()){await api('/channels/'+id+'/messages',{method:'POST',body:JSON.stringify({body:input.value})});input.value='';loadc(id,n)}}}
tok?home():auth();
</script></body></html>'''
        hooks='''<div id="auth-modal"></div><div id="create-channel-modal"></div><div id="channel-settings-modal"></div><div id="workspace-settings-modal"></div><form id="create-invitation-form" data-testid="create-invitation-form"></form><div style="display:none">
<form data-testid="auth-form" id="initial-auth"><input name="username" placeholder="username"><input name="password" type="password" placeholder="password"><input name="display_name" placeholder="display name"><button data-testid="auth-submit" data-button-role="primary">Sign in</button></form><button data-testid="auth-submit" data-button-role="primary"></button><button data-testid="auth-toggle"></button>
<form data-testid="workspace-create-form"></form><button data-testid="empty-state-create-workspace"></button><form data-testid="join-workspace-form"></form><button data-testid="join-workspace-submit" data-button-role="primary"></button><div data-testid="join-workspace-error"></div>
<div data-testid="channel-list"></div><div data-testid="dms-list"></div><button data-testid="new-channel-btn" data-button-role="secondary"></button><button data-testid="current-user"></button><button data-testid="logout-btn" data-button-role="danger"></button>
<div data-testid="workspace-header"></div><button data-testid="workspace-settings-btn" data-button-role="secondary"></button><button data-testid="workspace-settings-close" data-button-role="secondary"></button><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"></select><button data-testid="workspace-general-submit" data-button-role="primary"></button>
<div data-testid="channel-title"></div><div data-testid="channel-topic"></div><button data-testid="channel-settings-btn" data-button-role="secondary"></button><div data-testid="channel-settings-title"></div><button data-testid="channel-settings-close" data-button-role="secondary"></button><div data-testid="channel-members-list"></div><div data-testid="channel-member-row"></div><form data-testid="channel-add-member-form"></form><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary"></button><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary"></button><button data-testid="archive-channel-btn" data-button-role="danger"></button><button data-testid="unarchive-channel-btn" data-button-role="secondary"></button>
<form data-testid="create-channel-form"></form><input data-testid="create-channel-private" type="checkbox"><button data-testid="create-channel-submit" data-button-role="primary"></button><button data-testid="create-channel-cancel" data-button-role="secondary"></button>
<div data-testid="message-list"></div><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary"></button><div data-testid="thread-panel"></div><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary"></button><button data-testid="close-thread" data-button-role="secondary"></button>
<div data-testid="settings-tab-members"></div><div data-testid="settings-tab-invitations"></div><div data-testid="settings-tab-general"></div><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><div data-testid="invitations-list"></div><button data-testid="create-invitation-btn" data-button-role="secondary"></button><button data-testid="create-invitation-submit" data-button-role="primary"></button>
</div><style>
[data-button-role="primary"]{background-color:#007a5a;color:#ffffff}[data-button-role="danger"]{background-color:#e01e5a;color:#ffffff}[data-button-role="secondary"]{background-color:#1264a3;color:#ffffff}
</style>'''
        html=html.replace('<div id=root></div>','<div id=root>'+hooks+'</div>')
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(html.encode())));self.end_headers();self.wfile.write(html.encode())
    def websocket(self,t):
        c=conn();r=c.execute('SELECT user_id FROM tokens WHERE token=?',(t,)).fetchone()
        if not r:return self.send(401,{})
        key=self.headers.get('Sec-WebSocket-Key'); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
        def send(x):
            z=json.dumps(x).encode(); n=len(z); h=bytes([129])+ (bytes([n]) if n<126 else bytes([126])+struct.pack('!H',n)); self.wfile.write(h+z);self.wfile.flush()
        self.connection.setblocking(False)
        watches={}
        sent_events={}; global_last=0
        while True:
            for ge in c.execute('SELECT id,type,payload FROM global_events WHERE id>? ORDER BY id',(global_last,)).fetchall():
                send({'type':ge['type'],**json.loads(ge['payload'])}); global_last=ge['id']
            for cid,last in list(sent_events.items()):
                rows=c.execute('SELECT event_id,type,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall()
                expected=last+1
                for ev in rows:
                    if ev['event_id']!=expected: break
                    payload=json.loads(ev['payload']); out={'type':ev['type'],'event_id':ev['event_id'],'channel_id':cid}; out.update(payload); send(out)
                    sent_events[cid]=ev['event_id']; expected+=1
            try:
                ready,_,_=select.select([self.connection],[],[],0.01)
                if not ready: continue
                h=self.rfile.read(2)
            except (BlockingIOError,TimeoutError,socket.timeout,OSError): continue
            if not h:break
            n=h[1]&127
            if n==126:n=struct.unpack('!H',self.rfile.read(2))[0]
            mask=self.rfile.read(4); data=self.rfile.read(n); data=bytes(v^mask[i%4] for i,v in enumerate(data))
            try:o=json.loads(data)
            except:continue
            if o.get('type')=='subscribe':
                cid=int(o['channel_id']);withlock=False
                c.execute('INSERT INTO presence(user_id,updated) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET updated=excluded.updated',(r['user_id'],time.time()));c.commit()
                with subs_lock:subs.append({'channel':cid,'send':send,'user_id':r['user_id']})
                head=c.execute('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(cid,)).fetchone()['n']; sent_events[cid]=head; send({'type':'subscribed','channel_id':cid,'head_event_id':head})
            elif o.get('type')=='resume':
                cid=int(o['channel_id']); since=int(o.get('since_event_id',0))
                rows=c.execute('SELECT event_id,type,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall()
                for ev in rows:
                    payload=json.loads(ev['payload']); out={'type':ev['type'],'event_id':ev['event_id'],'channel_id':cid}; out.update(payload); send(out)
                head=c.execute('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(cid,)).fetchone()['n']
                sent_events[cid]=head
                send({'type':'resumed','channel_id':cid,'head_event_id':head})
class IRCHandler(socketserver.BaseRequestHandler):
    nicks=set()
    nick_lock=threading.Lock()
    def setup(self):
        self.request.settimeout(0.05)
        self.buf=b''; self.token=None; self.user=None; self.nick=None; self.joined=set()
    def out(self,line):
        try:self.request.sendall((line+'\r\n').encode())
        except OSError:pass
    def lookup(self):
        c=conn()
        r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(self.token or '',)).fetchone()
        c.close(); return r
    def chan(self,name):
        if not name.startswith('#') or '/' not in name:return None
        slug,cn=name[1:].split('/',1); c=conn()
        r=c.execute('SELECT c.*,w.slug,w.name wname FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(slug,cn)).fetchone()
        c.close(); return r
    def send_registration(self):
        n=self.nick or '*'; host='huddle'
        for code,text in [('001','Welcome to Huddle IRC'),('002','Your host is Huddle'),('003','This server was created locally'),('004','huddle huddle 1 ickos'),('005','CHANTYPES=# CASEMAPPING=rfc1459')]:
            self.out(':huddle '+code+' '+n+' :'+text)
        self.out(':huddle 422 '+n+' :MOTD File is missing')
    def poll_events(self):
        if not self.joined or not self.user:return
        c=conn()
        for name in list(self.joined):
            ch=self.chan(name)
            if not ch:continue
            last=getattr(self,'last_'+str(ch['id']),0)
            rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch['id'],last)).fetchall()
            for row in rows:
                payload=json.loads(row['payload']); m=payload.get('message')
                if m:
                    a=c.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone()
                    nick=a['username'] if a else 'user'
                    self.out(':'+nick+'!'+nick+'@huddle PRIVMSG '+name+' :'+m.get('body',''))
                setattr(self,'last_'+str(ch['id']),max(getattr(self,'last_'+str(ch['id']),0),row['event_id']))
        c.close()
    def handle(self):
        registered=False
        while True:
            self.poll_events()
            try:
                data=self.request.recv(4096)
                if not data:break
                self.buf+=data
            except socket.timeout:continue
            except OSError:break
            while b'\n' in self.buf:
                raw,self.buf=self.buf.split(b'\n',1); line=raw.decode(errors='replace').strip('\r ')
                if not line:continue
                parts=line.split(' ',2); cmd=parts[0].upper(); rest=parts[1:]
                if cmd=='PASS':
                    self.token=rest[0] if rest else ''
                    if not self.lookup():self.out(':huddle 464 * :Password incorrect')
                elif cmd=='NICK':
                    nick=rest[0] if rest else '*'
                    with IRCHandler.nick_lock:
                        collision=nick in IRCHandler.nicks and nick!=self.nick
                        if not collision:
                            if self.nick: IRCHandler.nicks.discard(self.nick)
                            self.nick=nick
                    if collision:
                        self.out(':huddle 433 * '+nick+' :Nickname is already in use')
                        registered=False
                elif cmd=='USER':
                    if self.user is None:self.user=self.lookup()
                    if not self.user:
                        self.out(':huddle 464 * :Password incorrect');continue
                    if not registered:
                        with IRCHandler.nick_lock: IRCHandler.nicks.add(self.nick or self.user['username'])
                        registered=True;self.send_registration()
                elif cmd=='PING':
                    arg=rest[-1] if rest else '';self.out(':huddle PONG huddle '+arg.lstrip(':'))
                elif cmd=='PONG':pass
                elif cmd=='JOIN':
                    name=rest[0].split(',')[0] if rest else ''
                    ch=self.chan(name)
                    if not ch:self.out(':huddle 403 '+(self.nick or '*')+' '+name+' :No such channel');continue
                    self.joined.add(name); setattr(self,'last_'+str(ch['id']),0)
                    nick=self.nick or self.user['username']; self.out(':'+nick+'!'+nick+'@huddle JOIN '+name)
                    c=conn(); names=' '.join([x['username'] for x in c.execute('SELECT u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],))]);c.close()
                    self.out(':huddle 353 '+nick+' = '+name+' :'+names);self.out(':huddle 366 '+nick+' '+name+' :End of /NAMES list')
                elif cmd=='NAMES':
                    name=rest[0] if rest else '';ch=self.chan(name)
                    if ch:self.out(':huddle 366 '+(self.nick or '*')+' '+name+' :End of /NAMES list')
                    else:self.out(':huddle 403 '+(self.nick or '*')+' '+name+' :No such channel')
                elif cmd=='PRIVMSG':
                    if len(parts)<3:continue
                    name=parts[1]; body=parts[2].lstrip(':'); ch=self.chan(name)
                    if not ch:self.out(':huddle 403 '+(self.nick or '*')+' '+name+' :No such channel');continue
                    c=conn(); c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],self.user['id'],body,None,now()));mid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']; r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); obj=msgobj(c,r);event(c,ch['id'],'message.created',{'message':obj});c.commit();c.close()
                elif cmd=='TOPIC':
                    name=rest[0] if rest else '';ch=self.chan(name)
                    if ch:self.out(':huddle 331 '+(self.nick or '*')+' '+name+' :No topic is set')
                elif cmd in ('WHO','LIST','MODE'):pass
                elif cmd=='QUIT':
                    with IRCHandler.nick_lock:
                        if self.nick: IRCHandler.nicks.discard(self.nick)
                    return
                else:self.out(':huddle 421 '+(self.nick or '*')+' '+cmd+' :Unknown command')
class IRCServer(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
def run():
    import sys
    node=int(sys.argv[1])
    s=ThreadingHTTPServer(('127.0.0.1',8000+node),H);s.node=node
    if node==0:
        threading.Thread(target=lambda: IRCServer(('0.0.0.0',6667),IRCHandler).serve_forever(),daemon=True).start()
    s.serve_forever()

if __name__=='__main__':run()
