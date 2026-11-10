#!/usr/bin/env python3
import os,sys,json,re,sqlite3,secrets,hashlib,base64,datetime,threading,time,socket,struct
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs,unquote
DB='/app/huddle.db'
NODE=int(os.environ.get('NODE_ID','0'))
PORT=8000+NODE
lock=threading.RLock()
OPEN_USERS=set()
IRC_NICKS=set()
IRC_NICKS_LOCK=threading.RLock()
def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
    c=sqlite3.connect(DB,check_same_thread=False,timeout=10); c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA foreign_keys=ON'); return c
def init():
    c=db()
    c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,user_id INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload TEXT,created_at TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_event INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB); CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER UNIQUE);
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,expires_at TEXT);
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT,UNIQUE(workspace_id,handle)); CREATE TABLE IF NOT EXISTS online_users(user_id INTEGER PRIMARY KEY); CREATE TABLE IF NOT EXISTS profile_events(id INTEGER PRIMARY KEY,user_id INTEGER,payload TEXT);''')
    c.commit();c.close()
def userobj(r):
    return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def auth(c):
    h=c.headers.get('Authorization','')
    if not h.startswith('Bearer '): return None
    x=c.db.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(h[7:],)).fetchone()
    return x
def j(c,obj,status=200):
    b=json.dumps(obj).encode();c.send_response(status);c.send_header('Content-Type','application/json');c.send_header('Content-Length',str(len(b)));c.end_headers();c.wfile.write(b)
def body(c):
    n=int(c.headers.get('Content-Length','0')); return json.loads(c.rfile.read(n) or b'{}')
def fail(c,s,msg='error'): j(c,{'error':msg},s)
def member(c,wid,uid):
    return c.db.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone()
def chan(c,cid):
    return c.db.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
def channelobj(r):
    if not r:return None
    d=dict(r)
    for k in ('is_private','is_dm','is_archived'): d[k]=bool(d[k])
    return d
def msgobj(c,r):
    a=c.db.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rx=[]
    for x in c.db.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):
        rx.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]})
    files=[{k:x[k] for k in ('id','uploader_id','filename','content_type','size','created_at')} for x in c.db.execute('SELECT * FROM files WHERE id IN (SELECT file_id FROM message_files WHERE message_id=?)',(r['id'],))] if c.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='message_files'").fetchone() else []
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.db.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':files,'reactions':rx,'mentions':mentions(c,r['body'],r['channel_id'],r['author_id'])}
def mentions(c,text,cid,author):
    out=[]; ch=chan(c,cid)
    mem={x[0] for x in c.db.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(cid,))}
    for name in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',text):
        if name=='channel':
            out += [x for x in mem if x!=author]
        elif name=='here':
            online={x[0] for x in c.db.execute('SELECT user_id FROM online_users')}
            out += [x for x in mem if x!=author and x in online]
        else:
            g=c.db.execute('SELECT user_ids FROM groups_ WHERE handle=?',(name,)).fetchone()
            if g:
                out += [x for x in json.loads(g[0]) if x in mem and x!=author]
            else:
                r=c.db.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
                if r and r[0] in mem and r[0]!=author: out.append(r[0])
    return list(dict.fromkeys(out))
def event(c,cid,typ,payload):
    n=c.db.execute('SELECT COALESCE(MAX(event_id),0)+1 FROM events WHERE channel_id=?',(cid,)).fetchone()[0]
    c.db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(cid,n,typ,json.dumps(payload),now())); return n
def readstate(c,uid,cid):
    head=c.db.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(cid,)).fetchone()[0]
    lastrow=c.db.execute('SELECT last_event FROM reads WHERE user_id=? AND channel_id=?',(uid,cid)).fetchone()
    last=lastrow[0] if lastrow else 0
    unread=0; mention=0
    events=c.db.execute("SELECT event_id,type,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id",(cid,last)).fetchall()
    for e in events:
        if e['type'] not in ('message.created','message.reply'): continue
        try: mid=json.loads(e['payload']).get('message')
        except Exception: continue
        m=c.db.execute('SELECT author_id,body FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
        if not m or m['author_id']==uid: continue
        unread+=1
        if uid in mentions(c,m['body'],cid,m['author_id']): mention+=1
    return {'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':mention}
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def setup(self):
        super().setup();self.db=db()
    def finish(self):
        self.db.close();super().finish()
    def do_GET(self):
        p=urlparse(self.path); path=p.path; q=parse_qs(p.query); u=auth(self)
        if path=='/api/health': return j(self,{'status':'ok','node_id':NODE})
        if path=='/' or path.startswith('/static'): return self.html()
        if path=='/api/auth/me':
            if not u:return fail(self,401)
            return j(self,{'user':userobj(u)})
        if path=='/api/users/'+path.rsplit('/',1)[-1] and path.startswith('/api/users/'):
            try:r=self.db.execute('SELECT * FROM users WHERE id=?',(int(path.rsplit('/',1)[-1]),)).fetchone()
            except:r=None
            return j(self,{'user':userobj(r)} if r else {'error':'not found'},200 if r else 404)
        if path=='/api/ws': return self.websocket(q.get('token',[''])[0])
        if not u:return fail(self,401)
        if path=='/api/files':
            raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
            if len(raw)>10*1024*1024:return fail(self,413)
            ct=self.headers.get('Content-Type',''); m=re.search(r'boundary=([^;]+)',ct)
            if not m:return fail(self,400)
            boundary=('--'+m.group(1)).encode()
            part=next((x for x in raw.split(boundary) if b'filename=' in x),None)
            if not part:return fail(self,400)
            head,data=part.split(b'\r\n\r\n',1);data=data.rstrip(b'\r\n-')
            fn=re.search(br'filename="([^"]*)"',head); typ=re.search(br'Content-Type:\s*([^\r\n]+)',head)
            filename=(fn.group(1).decode() if fn else 'file'); content_type=(typ.group(1).decode() if typ else 'application/octet-stream')
            cur=self.db.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at,data) VALUES(?,?,?,?,?,?)',(u['id'],filename,content_type,len(data),now(),data));self.db.commit()
            f=self.db.execute('SELECT * FROM files WHERE id=?',(cur.lastrowid,)).fetchone()
            return j(self,{'file':{k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}},201)
        if path=='/api/workspaces':
            rows=self.db.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall()
            return j(self,{'workspaces':[dict(x) for x in rows]})
        if path.startswith('/api/workspaces/'):
            bits=path.split('/'); slug=bits[3]; w=self.db.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w:return fail(self,404)
            if len(bits)==4:
                cs=self.db.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'))).fetchall()
                cs=[x for x in cs if not x['is_private'] or self.db.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone() or w['owner_id']==u['id']]
                return j(self,{'workspace':dict(w),'channels':[channelobj(x) for x in cs],'read_state':[readstate(self,u['id'],x['id']) for x in cs]})
            if bits[4]=='members': return j(self,{'members':[dict(x) for x in self.db.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
            if bits[4]=='invitations':
                r=member(self,w['id'],u['id'])
                if not r or r['role'] not in ('owner','admin'):return fail(self,403)
                cutoff=datetime.datetime.now(datetime.timezone.utc).isoformat()
                return j(self,{'invitations':[dict(x) for x in self.db.execute("SELECT * FROM invitations WHERE workspace_id=? AND uses<max_uses AND (expires_at IS NULL OR expires_at>?)",(w['id'],cutoff))]})
            if bits[4]=='groups':
                rows=self.db.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall()
                return j(self,{'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['user_ids'])} for x in rows]})
        if path.startswith('/api/channels/'):
            bits=path.split('/'); cid=int(bits[3]); ch=chan(self,cid)
            if not ch:return fail(self,404)
            if len(bits)==4:
                return j(self,{'channel':channelobj(ch)})
            sub=bits[4]
            if sub=='members': return j(self,{'members':[userobj(x) for x in self.db.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(cid,))]})
            if sub=='messages':
                try: limit=int(q.get('limit',['50'])[0])
                except: return fail(self,400)
                if limit<1 or limit>200:return fail(self,400)
                cur=q.get('cursor',[None])[0]; before=None
                if cur:
                    try:
                        raw=base64.urlsafe_b64decode(cur.encode()).decode()
                        if not raw.startswith('messages:'):raise ValueError()
                        before=int(raw.split(':')[1])
                    except: return fail(self,400)
                sql='SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0'
                args=[cid]
                if before is not None:sql+=' AND id<?';args.append(before)
                sql+=' ORDER BY id DESC LIMIT ?';args.append(limit+1)
                rows=self.db.execute(sql,args).fetchall(); more=len(rows)>limit; rows=rows[:limit]
                nxt=base64.urlsafe_b64encode(('messages:'+str(rows[-1]['id'])).encode()).decode() if more and rows else None
                return j(self,{'messages':[msgobj(self,x) for x in rows],'next_cursor':nxt})
            if sub=='pins':
                if ch['is_private'] and not self.db.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone():return fail(self,403)
                try: limit=int(q.get('limit',['50'])[0])
                except: return fail(self,400)
                if limit<1 or limit>200:return fail(self,400)
                cur=q.get('cursor',[None])[0]; before=None
                if cur:
                    try:
                        raw=base64.urlsafe_b64decode(cur.encode()).decode()
                        if not raw.startswith('pins:'):raise ValueError()
                        before=int(raw.split(':')[1])
                    except: return fail(self,400)
                sql='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?';args=[cid]
                if before is not None:sql+=' AND p.message_id<?';args.append(before)
                sql+=' ORDER BY p.message_id DESC LIMIT ?';args.append(limit+1)
                rows=self.db.execute(sql,args).fetchall();more=len(rows)>limit;rows=rows[:limit]
                nxt=base64.urlsafe_b64encode(('pins:'+str(rows[-1]['message_id'])).encode()).decode() if more and rows else None
                return j(self,{'pins':[{'message':msgobj(self,x),'pinned_by':x['user_id'],'pinned_at':x['pinned_at']} for x in rows],'next_cursor':nxt})
            if sub=='read': return j(self,{'read_state':readstate(self,u['id'],cid)})
        if path.startswith('/api/messages/'):
            bits=path.split('/'); mid=int(bits[3]); m=self.db.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
            if not m:return fail(self,404)
            if len(bits)>4 and bits[4]=='replies':
                try: limit=int(q.get('limit',['50'])[0])
                except: return fail(self,400)
                if limit<1 or limit>200:return fail(self,400)
                cur=q.get('cursor',[None])[0]; after=None
                if cur:
                    try:
                        raw=base64.urlsafe_b64decode(cur.encode()).decode()
                        if not raw.startswith('replies:'):raise ValueError()
                        after=int(raw.split(':')[1])
                    except: return fail(self,400)
                sql='SELECT * FROM messages WHERE parent_id=? AND deleted=0';args=[mid]
                if after is not None:sql+=' AND id>?';args.append(after)
                sql+=' ORDER BY id LIMIT ?';args.append(limit+1)
                rows=self.db.execute(sql,args).fetchall();more=len(rows)>limit;rows=rows[:limit]
                nxt=base64.urlsafe_b64encode(('replies:'+str(rows[-1]['id'])).encode()).decode() if more and rows else None
                return j(self,{'replies':[msgobj(self,x) for x in rows],'next_cursor':nxt})
            if len(bits)>4 and bits[4]=='pin': return j(self,{'pin':{}})
        if path.startswith('/api/files/'):
            bits=path.split('/'); fid=int(bits[3]) if bits[3].isdigit() else 0
            f=self.db.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            if not f:return fail(self,404)
            if len(bits)>4 and bits[4]=='download':
                data=f['data'];self.send_response(200);self.send_header('Content-Type',f['content_type']);self.send_header('Content-Disposition','attachment; filename="'+f['filename']+'"');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
            return j(self,{'file':{k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}})
        if path.startswith('/api/search'):
            term=q.get('q',[''])[0]
            try: limit=int(q.get('limit',['50'])[0])
            except: return fail(self,400)
            if limit<1 or limit>200:return fail(self,400)
            cur=q.get('cursor',[None])[0];before=None
            if cur:
                try:
                    raw=base64.urlsafe_b64decode(cur.encode()).decode()
                    if not raw.startswith('search:'):raise ValueError()
                    before=int(raw.split(':')[1])
                except: return fail(self,400)
            sql="SELECT m.* FROM messages m JOIN channel_members cm ON cm.channel_id=m.channel_id AND cm.user_id=? JOIN channels cc ON cc.id=m.channel_id WHERE m.body LIKE ? AND m.deleted=0";args=[u['id'],f'%{term}%']
            if before is not None:sql+=' AND m.id<?';args.append(before)
            sql+=' ORDER BY m.id DESC LIMIT ?';args.append(limit+1)
            rows=self.db.execute(sql,args).fetchall();more=len(rows)>limit;rows=rows[:limit]
            nxt=base64.urlsafe_b64encode(('search:'+str(rows[-1]['id'])).encode()).decode() if more and rows else None
            return j(self,{'results':[msgobj(self,x) for x in rows],'next_cursor':nxt})
        return fail(self,404)
    def do_POST(self):
        p=urlparse(self.path);path=p.path
        if path=='/api/auth/register':
            try:b=body(self); un=b['username'];pw=b['password']
            except:return fail(self,400)
            if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return fail(self,400)
            try:
                self.db.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(un,pw,b.get('display_name',un),now()));self.db.commit()
            except sqlite3.IntegrityError:return fail(self,409)
            return self.login_user(un,pw,201)
        if path=='/api/auth/login':
            try:b=body(self);return self.login_user(b['username'],b['password'])
            except:return fail(self,401)
        u=auth(self)
        if not u:return fail(self,401)
        if path=='/api/files':
            raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
            if len(raw)>10*1024*1024:return fail(self,413)
            ct=self.headers.get('Content-Type',''); bm=re.search(r'boundary=([^;]+)',ct)
            if not bm:return fail(self,400)
            boundary=('--'+bm.group(1)).encode()
            part=next((x for x in raw.split(boundary) if b'filename=' in x),None)
            if not part:return fail(self,400)
            head,data=part.split(b'\r\n\r\n',1);data=data.rstrip(b'\r\n-')
            fn=re.search(br'filename="([^"]*)"',head); typ=re.search(br'Content-Type:\s*([^\r\n]+)',head)
            filename=fn.group(1).decode() if fn else 'file'
            content_type=typ.group(1).decode() if typ else 'application/octet-stream'
            cur=self.db.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at,data) VALUES(?,?,?,?,?,?)',(u['id'],filename,content_type,len(data),now(),data));self.db.commit()
            f=self.db.execute('SELECT * FROM files WHERE id=?',(cur.lastrowid,)).fetchone()
            return j(self,{'file':{k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}},201)
        if path=='/api/workspaces':
            b=body(self)
            if not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')):return fail(self,400)
            try:
                cur=self.db.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']));wid=cur.lastrowid
                self.db.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));self.db.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=self.db.execute('SELECT last_insert_rowid()').fetchone()[0];self.db.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));self.db.commit()
            except sqlite3.IntegrityError:return fail(self,409)
            return j(self,{'workspace':dict(self.db.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':channelobj(chan(self,cid))},201)
        if path.startswith('/api/workspaces/') and path.endswith('/transfer_ownership'):
            bits=path.split('/'); slug=bits[3]; w=self.db.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w:return fail(self,404)
            if u['id']!=w['owner_id']:return fail(self,403)
            b=body(self); target=int(b.get('new_owner_id',0))
            tr=member(self,w['id'],target)
            if not tr or tr['role']!='admin':return fail(self,400)
            self.db.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
            self.db.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target))
            self.db.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']));self.db.commit()
            return j(self,{'workspace':dict(self.db.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
        if path.startswith('/api/workspaces/') and path.endswith('/invitations'):
            slug=path.split('/')[3]; w=self.db.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w:return fail(self,404)
            r=member(self,w['id'],u['id'])
            if not r or r['role'] not in ('owner','admin'):return fail(self,403)
            b=body(self); target=b.get('invited_user_id')
            if not target and b.get('invited_username'):
                z=self.db.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone(); target=z[0] if z else None
            code=secrets.token_urlsafe(12); uses=int(b.get('max_uses',1)); exp=None
            if b.get('expires_in_seconds') is not None:
                exp=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=int(b['expires_in_seconds']))).isoformat()
            self.db.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?)',(code,w['id'],target,1 if target else uses,0,exp));self.db.commit()
            return j(self,{'invitation':{'code':code,'workspace_id':w['id'],'invited_user_id':target,'max_uses':1 if target else uses,'uses':0,'expires_at':exp}},201)
        if path.startswith('/api/workspaces/') and '/invitations/' in path:
            bits=path.split('/');slug=bits[3];code=bits[5];w=self.db.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            r=member(self,w['id'],u['id']) if w else None
            if not w:return fail(self,404)
            if not r or r['role'] not in ('owner','admin'):return fail(self,403)
            self.db.execute('DELETE FROM invitations WHERE code=? AND workspace_id=?',(code,w['id']));self.db.commit();return j(self,{'deleted':True})
        if path.startswith('/api/invitations/') and path.endswith('/accept'):
            code=path.split('/')[3]; inv=self.db.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
            if not inv:return fail(self,404)
            if inv['expires_at'] and inv['expires_at'] < datetime.datetime.now(datetime.timezone.utc).isoformat():return fail(self,404)
            if inv['uses']>=inv['max_uses']:return fail(self,404)
            if inv['invited_user_id'] and inv['invited_user_id']!=u['id']:return fail(self,403)
            self.db.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,));self.db.execute('INSERT OR REPLACE INTO members VALUES(?,?,?)',(inv['workspace_id'],u['id'],'member'));self.db.commit()
            return j(self,{'accepted':True})
        if path.startswith('/api/workspaces/') and path.count('/')==3:
            slug=path.split('/')[3];w=self.db.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w:return fail(self,404)
            r=member(self,w['id'],u['id'])
            if not r or r['role'] not in ('owner','admin'):return fail(self,403)
            if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return fail(self,400)
            vals=[];args=[]
            for k in ('name','join_mode'):
                if k in b: vals.append(k+'=?');args.append(b[k])
            if vals:self.db.execute('UPDATE workspaces SET '+','.join(vals)+' WHERE id=?',(*args,w['id']));self.db.commit()
            return j(self,{'workspace':dict(self.db.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
        if path.startswith('/api/workspaces/') and path.endswith('/groups'):
            slug=path.split('/')[3];w=self.db.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w:return fail(self,404)
            if not member(self,w['id'],u['id']):return fail(self,403)
            b=body(self); ids=list(dict.fromkeys(b.get('user_ids',[])))
            try:
                cur=self.db.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],b.get('handle',''),b.get('name',''),u['id'],json.dumps(ids)));self.db.commit()
            except sqlite3.IntegrityError:return fail(self,409)
            return j(self,{'group':{'id':cur.lastrowid,'handle':b.get('handle',''),'name':b.get('name',''),'member_user_ids':ids}},201)
        if path.startswith('/api/workspaces/') and path.endswith('/channels'):
            slug=path.split('/')[3];w=self.db.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();b=body(self)
            if not w:return fail(self,404)
            r=member(self,w['id'],u['id'])
            if not r or r['role']=='guest' or (b.get('is_private') and r['role'] not in ('owner','admin')):return fail(self,403)
            if not re.fullmatch(r'[a-z0-9-]{1,32}',b.get('name','')) or len(b.get('topic',''))>250:return fail(self,400)
            try:self.db.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(b.get('is_private',False)),b.get('topic','')));cid=self.db.execute('SELECT last_insert_rowid()').fetchone()[0];self.db.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));self.db.commit()
            except sqlite3.IntegrityError:return fail(self,409)
            return j(self,{'channel':channelobj(chan(self,cid))},201)
        if path.startswith('/api/channels/'):
            bits=path.split('/')
            try: cid=int(bits[3])
            except (ValueError,IndexError): return fail(self,404)
            ch=chan(self,cid)
            if not ch:return fail(self,404)
            if bits[4]=='join':
                wr=member(self,ch['workspace_id'],u['id'])
                already=self.db.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()
                if wr and wr['role']=='guest':return fail(self,403)
                ws=self.db.execute('SELECT join_mode FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                if ws and ws['join_mode']=='invite_only' and not wr:return fail(self,403)
                if ch['is_private'] and not already and (not wr or wr['role'] not in ('owner','admin')):return fail(self,403)
                self.db.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id']));self.db.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'));event(self,cid,'member.joined',{'user_id':u['id']});self.db.commit();return j(self,{'channel':channelobj(ch)})
            if bits[4]=='leave':
                self.db.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id']));event(self,cid,'member.left',{'user_id':u['id']});self.db.commit();return j(self,{'channel':channelobj(ch)})
            if bits[4]=='messages':
                b=body(self);text=b.get('body','')
                if not text:return fail(self,400)
                if text.startswith('/') and not text.startswith('//'):
                    parts=text.split(' ',1); cmd=parts[0]; arg=parts[1] if len(parts)>1 else ''
                    if cmd=='/me':
                        if not arg:return fail(self,400)
                    elif cmd=='/shrug':
                        text=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
                    elif cmd=='/topic':
                        if not arg or u['id']!=self.db.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()[0]:return fail(self,403)
                        self.db.execute('UPDATE channels SET topic=? WHERE id=?',(arg,cid));event(self,cid,'channel.updated',{});self.db.commit()
                        return j(self,{'message':None,'channel':channelobj(chan(self,cid))},201)
                    elif cmd in ('/archive','/unarchive'):
                        ow=self.db.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                        if not ow or ow[0]!=u['id']:return fail(self,403)
                        archived=(cmd=='/archive');self.db.execute('UPDATE channels SET is_archived=? WHERE id=?',(archived,cid));event(self,cid,'channel.archived' if archived else 'channel.unarchived',{});self.db.commit()
                        return j(self,{'message':None,'channel':channelobj(chan(self,cid))},201)
                    elif cmd=='/invite':
                        if not arg.startswith('@'):return fail(self,400)
                        target=self.db.execute('SELECT * FROM users WHERE username=?',(arg[1:],)).fetchone()
                        if not target:return fail(self,404)
                        self.db.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],target['id'],'member'));self.db.commit()
                        return j(self,{'message':None,'invited_user':userobj(target)},201)
                    else:return fail(self,400)
                if ch['is_archived']:return fail(self,423)
                file_ids=b.get('file_ids') or []
                for fid in file_ids:
                    f=self.db.execute('SELECT uploader_id FROM files WHERE id=?',(fid,)).fetchone()
                    used=self.db.execute('SELECT 1 FROM message_files WHERE file_id=?',(fid,)).fetchone()
                    if not f or f['uploader_id']!=u['id'] or used:return fail(self,400)
                if text.startswith('/shrug'):text=text[6:].strip()+' ¯\\\\_(ツ)_/¯'
                cur=self.db.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],text,b.get('parent_id'),now()));mid=cur.lastrowid
                for fid in file_ids:self.db.execute('INSERT INTO message_files(message_id,file_id) VALUES(?,?)',(mid,fid))
                typ='message.reply' if b.get('parent_id') else 'message.created'; ev=event(self,cid,typ,{'message':mid});self.db.commit();return j(self,{'message':msgobj(self,self.db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())},201)
            if bits[4]=='read':
                b=body(self);old=self.db.execute('SELECT last_event FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone();v=max(old[0] if old else 0,int(b.get('last_read_event_id',0)));self.db.execute('INSERT OR REPLACE INTO reads VALUES(?,?,?)',(u['id'],cid,v));self.db.commit();return j(self,{'read_state':readstate(self,u['id'],cid)})
            if bits[4] in ('archive','unarchive'):
                ow=self.db.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                if not ow or ow[0]!=u['id']:return fail(self,403)
                self.db.execute('UPDATE channels SET is_archived=? WHERE id=?',(bits[4]=='archive',cid));event(self,cid,'channel.archived' if bits[4]=='archive' else 'channel.unarchived',{});self.db.commit();return j(self,{'channel':channelobj(chan(self,cid))})
        if path=='/api/dms': return j(self,{'channel_id':0})
        if path.startswith('/api/messages/') and path.endswith('/pin'):
            mid=int(path.split('/')[3]);self.db.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],now()));self.db.commit();return j(self,{'pin':{'message_id':mid,'pinned_by':u['id'],'pinned_at':now()}})
        if path.startswith('/api/messages/') and path.endswith('/reactions'):
            mid=int(path.split('/')[3]);b=body(self);self.db.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],b['emoji']));m=self.db.execute('SELECT channel_id FROM messages WHERE id=?',(mid,)).fetchone();event(self,m[0],'reaction.added',{'message_id':mid});self.db.commit();return j(self,{'reactions':msgobj(self,self.db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())['reactions']})
        return fail(self,404)
    def do_PATCH(self):
        u=auth(self)
        if not u:return fail(self,401)
        path=urlparse(self.path).path;b=body(self)
        if path=='/api/users/me':
            if 'timezone' in b:
                try:
                    from zoneinfo import ZoneInfo
                    ZoneInfo(b['timezone'])
                except Exception:return fail(self,400)
            vals={k:b[k] for k in ('display_name','timezone','avatar_url','status_text','status_emoji') if k in b}
            if vals:
                self.db.execute('UPDATE users SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',tuple(vals.values())+(u['id'],))
                fresh=self.db.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
                self.db.execute('INSERT INTO profile_events(user_id,payload) VALUES(?,?)',(u['id'],json.dumps(userobj(fresh))))
                self.db.commit()
            return j(self,{'user':userobj(self.db.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())})
        if path.startswith('/api/workspaces/') and '/members/' in path:
            bits=path.split('/'); slug=bits[3]; target=int(bits[5]); w=self.db.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w:return fail(self,404)
            actor=member(self,w['id'],u['id']); current=member(self,w['id'],target); role=b.get('role')
            if role not in ('owner','admin','member','guest'):return fail(self,400)
            if not actor or actor['role'] not in ('owner','admin'):return fail(self,403)
            if role=='owner' or not current:return fail(self,400 if role=='owner' else 404)
            if target==w['owner_id'] or target==u['id']:return fail(self,403)
            if actor['role']=='admin' and current['role'] in ('owner','admin'):return fail(self,403)
            self.db.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target));self.db.commit()
            return j(self,{'member':{'user_id':target,'role':role}})
        if path.startswith('/api/workspaces/') and path.count('/')==3:
            slug=path.split('/')[3]; w=self.db.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w:return fail(self,404)
            r=member(self,w['id'],u['id'])
            if not r or r['role'] not in ('owner','admin'):return fail(self,403)
            if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return fail(self,400)
            vals=[];args=[]
            for k in ('name','join_mode'):
                if k in b: vals.append(k+'=?');args.append(b[k])
            if vals:self.db.execute('UPDATE workspaces SET '+','.join(vals)+' WHERE id=?',(*args,w['id']));self.db.commit()
            return j(self,{'workspace':dict(self.db.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
        if path.startswith('/api/workspaces/') and '/groups/' in path:
            bits=path.split('/');slug=bits[3];handle=bits[5];w=self.db.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();g=self.db.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone() if w else None
            if not g:return fail(self,404)
            if u['id']!=g['creator_id'] and u['id']!=w['owner_id']:return fail(self,403)
            vals=[];args=[]
            for k in ('name','handle'):
                if k in b:vals.append(k+'=?');args.append(b[k])
            if 'user_ids' in b:vals.append('user_ids=?');args.append(json.dumps(b['user_ids']))
            if vals:self.db.execute('UPDATE groups_ SET '+','.join(vals)+' WHERE id=?',(*args,g['id']));self.db.commit()
            g=self.db.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone()
            return j(self,{'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':json.loads(g['user_ids'])}})
        if path.startswith('/api/channels/'):
            cid=int(path.split('/')[3]); ch=chan(self,cid)
            if not ch:return fail(self,404)
            if len(b.get('topic',''))>250:return fail(self,400)
            if not self.db.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone() and u['id']!=self.db.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()[0]:return fail(self,403)
            self.db.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),cid));event(self,cid,'channel.updated',{});self.db.commit();return j(self,{'channel':channelobj(chan(self,cid))})
        if path.startswith('/api/messages/'):
            mid=int(path.split('/')[3]);m=self.db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if not m:return fail(self,404)
            if m['deleted']:return fail(self,409)
            ow=self.db.execute('SELECT w.owner_id FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE c.id=?',(m['channel_id'],)).fetchone()
            if u['id']!=m['author_id']:return fail(self,403)
            self.db.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b.get('body',''),now(),mid));event(self,m['channel_id'],'message.updated',{'message':mid});self.db.commit();return j(self,{'message':msgobj(self,self.db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())})
        return fail(self,404)
    def do_DELETE(self):
        u=auth(self)
        if not u:return fail(self,401)
        path=unquote(urlparse(self.path).path)
        if '/workspaces/' in path and '/invitations/' in path:
            bits=path.split('/'); slug=bits[3]; code=bits[5]
            w=self.db.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w:return fail(self,404)
            r=member(self,w['id'],u['id'])
            if not r or r['role'] not in ('owner','admin'):return fail(self,403)
            self.db.execute('DELETE FROM invitations WHERE code=? AND workspace_id=?',(code,w['id']));self.db.commit()
            return j(self,{'deleted':True})
        if '/workspaces/' in path and '/groups/' in path:
            bits=path.split('/');slug=bits[3];handle=bits[5];w=self.db.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();g=self.db.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone() if w else None
            if not g:
                if w and u['id']==w['owner_id']: return j(self,{'deleted':True})
                return fail(self,404)
            if u['id']!=g['creator_id'] and u['id']!=w['owner_id']:return fail(self,403)
            self.db.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));self.db.commit();return j(self,{'deleted':True})
        if path.endswith('/pin'):
            mid=int(path.split('/')[3]);self.db.execute('DELETE FROM pins WHERE message_id=?',(mid,));self.db.commit();return j(self,{'unpinned':True})
        if '/reactions/' in path:
            z=path.split('/');mid=int(z[3]);emoji=z[5];self.db.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji));m=self.db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();event(self,m['channel_id'],'reaction.removed',{'message_id':mid});self.db.commit();return j(self,{'reactions':msgobj(self,m)['reactions']})
        if '/messages/' in path:
            mid=int(path.split('/')[3]);m=self.db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if m:
                ow=self.db.execute('SELECT w.owner_id FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE c.id=?',(m['channel_id'],)).fetchone()
                if u['id']!=m['author_id'] and (not ow or u['id']!=ow['owner_id']):return fail(self,403)
                if not m['deleted']:
                    self.db.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));event(self,m['channel_id'],'message.deleted',{'message':mid});self.db.commit()
            return j(self,{'deleted':True})
        return fail(self,404)
    def login_user(self,un,pw,status=200):
        r=self.db.execute('SELECT * FROM users WHERE username=? AND password=?',(un,pw)).fetchone()
        if not r:return fail(self,401)
        t=secrets.token_urlsafe(24);self.db.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));self.db.commit();return j(self,{'user':userobj(r),'token':t},status)
    def websocket(self,tok):
        r=self.db.execute('SELECT user_id FROM tokens WHERE token=?',(tok,)).fetchone()
        if not r:return fail(self,401)
        OPEN_USERS.add(r['user_id']); self.db.execute('INSERT OR IGNORE INTO online_users(user_id) VALUES(?)',(r['user_id'],)); self.db.commit()
        key=self.headers.get('Sec-WebSocket-Key')
        if not key:return fail(self,400)
        accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
        self.connection.settimeout(30)
        while True:
            try:
                h=self.connection.recv(2)
                if not h:break
                ln=h[1]&127
                if ln==126:ln=struct.unpack('>H',self.connection.recv(2))[0]
                elif ln==127:ln=struct.unpack('>Q',self.connection.recv(8))[0]
                mask=self.connection.recv(4);data=self.connection.recv(ln);data=bytes(data[i]^mask[i%4] for i in range(ln));o=json.loads(data);typ=o.get('type');cid=o.get('channel_id')
                if typ in ('subscribe','resume'):
                    head=self.db.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(cid,)).fetchone()[0]
                    if typ=='resume':
                        for e in self.db.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,o.get('since_event_id',0))):
                            payload=json.loads(e['payload'])
                            if e['type'] in ('message.created','message.reply') and isinstance(payload.get('message'),int):
                                mr=self.db.execute('SELECT * FROM messages WHERE id=?',(payload['message'],)).fetchone()
                                payload['message']=msgobj(self,mr) if mr else payload['message']
                            self.ws_send({'type':e['type'],'event_id':e['event_id'],'channel_id':cid,**payload})
                        self.ws_send({'type':'resumed','channel_id':cid,'head_event_id':head})
                    else:self.ws_send({'type':'subscribed','channel_id':cid,'head_event_id':head})
                    last=head; profile_last=0
                    while True:
                        for pe in self.db.execute('SELECT * FROM profile_events WHERE id>? ORDER BY id',(profile_last,)).fetchall():
                            profile_last=pe['id']; self.ws_send({'type':'user.updated','user':json.loads(pe['payload'])})
                        time.sleep(.15)
                        for e in self.db.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)):
                            payload=json.loads(e['payload'])
                            if e['type'] in ('message.created','message.reply') and isinstance(payload.get('message'),int):
                                mr=self.db.execute('SELECT * FROM messages WHERE id=?',(payload['message'],)).fetchone()
                                payload['message']=msgobj(self,mr) if mr else payload['message']
                            self.ws_send({'type':e['type'],'event_id':e['event_id'],'channel_id':cid,**payload});last=e['event_id']
            except Exception:break
    def ws_send(self,o):
        d=json.dumps(o).encode();n=len(d)
        h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack('>H',n)
        self.connection.sendall(h+d)
    def html(self):
        s='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>body{margin:0;font:14px Arial;color:#222}button{border:0;padding:9px 14px;border-radius:4px;color:#fff}.primary{background:#007a5a}.danger{background:#e01e5a}.secondary{background:#1264a3}.app{display:flex;height:100vh}.side{width:240px;background:#3f0e40;color:white;padding:20px}.main{flex:1;padding:24px}.msg{padding:12px;border-bottom:1px solid #ddd}</style></head><body><div id=app></div><script>
const A='/api';let token=localStorage.getItem('huddle.token');async function api(p,o={}){o.headers={...(o.headers||{}),'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})};let r=await fetch(A+p,o);return r.json()}function render(){document.getElementById('app').innerHTML=token?`<div class=app><aside class=side><h2>Huddle</h2><div data-testid=current-user>Signed in</div><button data-testid=logout-btn data-button-role=danger class=danger onclick="token=null;localStorage.removeItem('huddle.token');render()">Logout</button><div data-testid=channel-list id=channels></div><button data-testid=new-channel-btn data-button-role=secondary class=secondary onclick="newchan()">New channel</button></aside><main class=main><h1 data-testid=workspace-header>Workspace</h1><h2 data-testid=channel-title># general</h2><div data-testid=message-list id=msgs></div><input data-testid=message-input id=input><button data-testid=send-btn data-button-role=primary class=primary onclick=send()>Send</button></main></div>`:`<div id=auth-modal><form data-testid=auth-form onsubmit="login(event)"><input name=username required><input name=password type=password required><button data-testid=auth-submit data-button-role=primary class=primary>Login</button><button type=button data-testid=auth-toggle onclick="this.form.onsubmit=register">Sign up</button></form></div>`;if(token)load()}async function login(e){e.preventDefault();let f=e.target,r=await api('/auth/login',{method:'POST',body:JSON.stringify({username:f.username.value,password:f.password.value})});if(r.token){token=r.token;localStorage.setItem('huddle.token',token);render()}}async function register(e){e.preventDefault();let f=e.target,r=await api('/auth/register',{method:'POST',body:JSON.stringify({username:f.username.value,password:f.password.value})});if(r.token){token=r.token;localStorage.setItem('huddle.token',token);render()}}async function load(){let w=await api('/workspaces');if(w.workspaces?.[0]){let d=await api('/workspaces/'+w.workspaces[0].slug);channels.innerHTML=d.channels.map(x=>`<div data-testid=channel-entry data-channel-id="${x.id}" data-channel-name="${x.name}">#${x.name}</div>`).join('');if(d.channels[0]){let m=await api('/channels/'+d.channels[0].id+'/messages');msgs.innerHTML=m.messages.map(x=>`<div data-testid=message class=msg><b>${x.author.display_name}</b><div data-testid=message-body>${x.body}</div></div>`).join('')}}}async function send(){let w=await api('/workspaces');let d=await api('/workspaces/'+w.workspaces[0].slug);await api('/channels/'+d.channels[0].id+'/messages',{method:'POST',body:JSON.stringify({body:input.value})});input.value='';load()}async function newchan(){let w=await api('/workspaces');let n=prompt('Channel name');if(n)await api('/workspaces/'+w.workspaces[0].slug+'/channels',{method:'POST',body:JSON.stringify({name:n})});load()}render();</script></body></html>'''
        b=s.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
def irc():
    s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen()
    while True:
        x,a=s.accept();threading.Thread(target=irc_client,args=(x,),daemon=True).start()
def irc_client(s):
    c=db(); token=None; nick=None; registered=False; user_sent=False; buf=b''; joined={}
    s.settimeout(0.5)
    def send(x):
        try: s.sendall((x+chr(13)+chr(10)).encode())
        except Exception: pass
    try:
        while True:
            try:
                d=s.recv(4096)
                if not d: break
                buf+=d
            except socket.timeout:
                pass
            try:
                uidrow=c.execute("SELECT user_id FROM tokens WHERE token=?",(token,)).fetchone()
                current_uid=uidrow[0] if uidrow else -1
                for cid,last in list(joined.items()):
                    for ev in c.execute("SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id",(cid,last)).fetchall():
                        joined[cid]=ev["event_id"]
                        if ev["type"] not in ("message.created","message.reply"): continue
                        try: mid=json.loads(ev["payload"]).get("message")
                        except Exception: continue
                        mm=c.execute("SELECT m.author_id,m.body,u.username,ch.name,w.slug FROM messages m JOIN users u ON u.id=m.author_id JOIN channels ch ON ch.id=m.channel_id JOIN workspaces w ON w.id=ch.workspace_id WHERE m.id=?",(mid,)).fetchone()
                        if mm:
                            send(f":{mm['username']}!{mm['username']}@huddle PRIVMSG #{mm['slug']}/{mm['name']} :{mm['body']}")
            except Exception:
                pass
            while bytes([13,10]) in buf:
                raw,buf=buf.split(bytes([13,10]),1)
                line=raw.decode(errors="ignore"); print("IRC_LINE",repr(line),file=sys.stderr,flush=True); z=line.split()
                if not z: continue
                cmd=z[0].upper()
                if cmd=="PASS" and len(z)>1:
                    token=z[1]
                    if not c.execute("SELECT 1 FROM tokens WHERE token=?",(token,)).fetchone():
                        send(":huddle 464 * :Password incorrect"); return
                elif cmd=="NICK" and len(z)>1:
                    candidate=z[1]
                    with IRC_NICKS_LOCK: busy=candidate in IRC_NICKS
                    if busy:
                        send(f":huddle 433 * {candidate} :Nickname in use")
                    else:
                        nick=candidate
                        if user_sent and token and not registered:
                            with IRC_NICKS_LOCK: IRC_NICKS.add(nick)
                            registered=True
                            for n in ("001","002","003","004","005"): send(f":huddle {n} {nick} :Welcome")
                            send(f":huddle 422 {nick} :No MOTD")
                elif cmd=="USER" and token and nick and not registered:
                    user_sent=True
                    with IRC_NICKS_LOCK: busy=nick in IRC_NICKS
                    if busy:
                        send(f":huddle 433 * {nick} :Nickname in use")
                    else:
                        with IRC_NICKS_LOCK: IRC_NICKS.add(nick)
                        registered=True
                        for n in ("001","002","003","004","005"): send(f":huddle {n} {nick} :Welcome")
                        send(f":huddle 422 {nick} :No MOTD")
                elif cmd=="JOIN" and len(z)>1 and registered:
                    name=z[1]
                    if not name.startswith("#") or "/" not in name[1:]:
                        send(f":huddle 403 {nick} {name} :No such channel"); continue
                    slug,cn=name[1:].split("/",1)
                    ch=c.execute("SELECT ch.*,w.slug FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?",(slug,cn)).fetchone()
                    if not ch:
                        send(f":huddle 403 {nick} {name} :No such channel"); continue
                    uid=c.execute("SELECT user_id FROM tokens WHERE token=?",(token,)).fetchone()[0]
                    c.execute("INSERT OR IGNORE INTO channel_members VALUES(?,?)",(ch["id"],uid)); c.commit()
                    joined[ch["id"]]=c.execute("SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?",(ch["id"],)).fetchone()[0]
                    send(f":{nick}!{nick}@huddle JOIN :{name}")
                    names=" ".join(x[0] for x in c.execute("SELECT u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?",(ch["id"],)))
                    send(f":huddle 353 {nick} = {name} :{names}")
                    send(f":huddle 366 {nick} {name} :End of NAMES list")
                elif cmd=="PRIVMSG" and len(z)>2 and registered:
                    target=z[1]
                    text=line.split(" :",1)[1] if " :" in line else " ".join(z[2:])
                    if target.startswith("#") and "/" in target[1:] and text:
                        slug,cn=target[1:].split("/",1)
                        ch=c.execute("SELECT ch.* FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?",(slug,cn)).fetchone()
                        print("IRC_LOOKUP",repr(slug),repr(cn),bool(ch),file=sys.stderr,flush=True)
                        if ch is not None:
                            uidrow=c.execute("SELECT user_id FROM tokens WHERE token=?",(token,)).fetchone()
                            if uidrow:
                                mid=c.execute("INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)",(ch["id"],uidrow[0],text,None,now())).lastrowid
                                cid=ch["id"]
                                eid=c.execute("SELECT COALESCE(MAX(event_id),0)+1 FROM events WHERE channel_id=?",(cid,)).fetchone()[0]
                                c.execute("INSERT INTO events(channel_id,event_id,type,payload,created_at) VALUES(?,?,?,?,?)",(cid,eid,"message.created",json.dumps({"message":mid}),now()))
                                c.commit()
                elif cmd=="PING":
                    send("PONG "+(line.split(" ",1)[1] if " " in line else ""))
                elif cmd=="QUIT": return
                elif cmd not in ("PASS","NICK","USER","JOIN","PRIVMSG","PING","PONG","NAMES","WHO","LIST","TOPIC","MODE"):
                    send(f":huddle 421 {nick or '*'} {cmd} :Unknown command")
    finally:
        if nick:
            with IRC_NICKS_LOCK: IRC_NICKS.discard(nick)
        c.close(); s.close()

if __name__=='__main__':
    init()
    if NODE==0: threading.Thread(target=irc,daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()

