#!/usr/bin/env python3
import os, re, json, uuid, sqlite3, hashlib, secrets, threading, time, mimetypes, urllib.parse, email.utils, socket, base64, struct
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
DB="/app/data.db"
PORT=int(os.environ.get("PORT","8000")); NODE=PORT-8000
def now(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")
def conn():
    c=sqlite3.connect(DB,timeout=30,check_same_thread=False); c.row_factory=sqlite3.Row
    for _ in range(30):
        try:
            c.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError:
            time.sleep(.1)
    return c
def init():
    os.makedirs("/app",exist_ok=True); c=conn()
    c.executescript("""CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,status_text TEXT,status_emoji TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id TEXT);
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id TEXT,join_mode TEXT);
CREATE TABLE IF NOT EXISTS members(workspace_id TEXT,user_id TEXT,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY,workspace_id TEXT,name TEXT,is_private INTEGER,is_dm INTEGER,topic TEXT,is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
CREATE TABLE IF NOT EXISTS channel_members(channel_id TEXT,user_id TEXT,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,channel_id TEXT,author_id TEXT,body TEXT,parent_id TEXT,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,seq INTEGER);
CREATE TABLE IF NOT EXISTS reactions(message_id TEXT,user_id TEXT,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,uploader_id TEXT,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB,attached INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS dms(a TEXT,b TEXT,channel_id TEXT,PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS events(channel_id TEXT,seq INTEGER,type TEXT,payload TEXT,PRIMARY KEY(channel_id,seq));
CREATE TABLE IF NOT EXISTS groups_(id TEXT PRIMARY KEY,workspace_id TEXT,handle TEXT UNIQUE,name TEXT,creator_id TEXT);
CREATE TABLE IF NOT EXISTS group_members(group_id TEXT,user_id TEXT,PRIMARY KEY(group_id,user_id));
CREATE TABLE IF NOT EXISTS presence(user_id TEXT,channel_id TEXT,updated_at REAL,PRIMARY KEY(user_id,channel_id));
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT);"""); c.commit(); c.close()
init()
def db(): return conn()
def user_obj(r):
    return {"id":r["id"],"username":r["username"],"display_name":r["display_name"],"timezone":r["timezone"] or "UTC","avatar_url":None,"status_text":r["status_text"] or "","status_emoji":r["status_emoji"] or ""}
def channel_obj(r): return {"id":r["id"],"workspace_id":r["workspace_id"],"name":r["name"],"is_private":bool(r["is_private"]),"is_dm":bool(r["is_dm"]),"topic":r["topic"] or "","is_archived":bool(r["is_archived"])}
class H(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self,*x): pass
    def sendj(self,status,obj):
        b=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get("Content-Length","0")); return self.rfile.read(n)
    def auth(self,required=True):
        t=self.headers.get("Authorization","").replace("Bearer ","",1); c=db(); r=c.execute("SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?",(t,)).fetchone(); c.close()
        if not r and required: self.sendj(401,{"error":"unauthorized"}); return None
        return r
    def route(self):
        return urllib.parse.urlparse(self.path)
    def do_GET(self):
        p=self.route(); path=p.path
        if path=="/api/health": return self.sendj(200,{"status":"ok","node_id":NODE})
        if path=="/" or path=="/index.html": return self.html()
        if path=="/api/ws": return self.websocket(urllib.parse.parse_qs(p.query).get("token",[""])[0])
        u=self.auth(); 
        if not u:return
        c=db()
        if path=="/api/auth/me": return self.sendj(200,{"user":user_obj(u)})
        m=re.fullmatch(r"/api/users/([^/]+)",path)
        if m:
            r=c.execute("SELECT * FROM users WHERE id=?",(m.group(1),)).fetchone(); return self.sendj(200,{"user":user_obj(r)}) if r else self.sendj(404,{"error":"not found"})
        if path=="/api/workspaces":
            rows=c.execute("SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?",(u["id"],)).fetchall(); return self.sendj(200,{"workspaces":[dict(x) for x in rows]})
        m=re.fullmatch(r"/api/files/([^/]+)/download",path)
        if m:
            r=c.execute("SELECT * FROM files WHERE id=?",(m.group(1),)).fetchone()
            if not r:return self.sendj(404,{"error":"not found"})
            b=r["data"]; self.send_response(200); self.send_header("Content-Type",r["content_type"]); self.send_header("Content-Disposition",'attachment; filename="%s"'%r["filename"]); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b); return
        m=re.fullmatch(r"/api/files/([^/]+)",path)
        if m:
            r=c.execute("SELECT * FROM files WHERE id=?",(m.group(1),)).fetchone(); return self.sendj(200,{"file":{k:r[k] for k in ("id","uploader_id","filename","content_type","size","created_at")}}) if r else self.sendj(404,{"error":"not found"})
        m=re.fullmatch(r"/api/workspaces/([^/]+)/groups",path)
        if m:
            w=c.execute("SELECT * FROM workspaces WHERE slug=?",(m.group(1),)).fetchone()
            if not w:return self.sendj(404,{})
            gs=c.execute("SELECT * FROM groups_ WHERE workspace_id=?",(w["id"],)).fetchall(); out=[]
            for g in gs: out.append({"id":g["id"],"handle":g["handle"],"name":g["name"],"member_user_ids":[x[0] for x in c.execute("SELECT user_id FROM group_members WHERE group_id=?",(g["id"],))]})
            return self.sendj(200,{"groups":out})
        c.close(); return self.sendj(404,{"error":"not found"})
    def do_POST(self):
        p=self.route(); path=p.path
        if path=="/api/auth/register":
            try:b=json.loads(self.body())
            except:return self.sendj(400,{"error":"bad json"})
            if not re.fullmatch(r"[A-Za-z0-9_]+",b.get("username","")) or len(b.get("password",""))<8:return self.sendj(400,{"error":"invalid"})
            c=db(); uid=str(uuid.uuid4()); tok=secrets.token_urlsafe(24)
            try:c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?)",(uid,b["username"],hashlib.sha256(b["password"].encode()).hexdigest(),b.get("display_name") or b["username"],"UTC","","")); c.execute("INSERT INTO tokens VALUES(?,?)",(tok,uid)); c.commit()
            except sqlite3.IntegrityError:return self.sendj(409,{"error":"duplicate"})
            r=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone(); c.close(); return self.sendj(201,{"user":user_obj(r),"token":tok})
        if path=="/api/auth/login":
            b=json.loads(self.body() or "{}"); c=db(); r=c.execute("SELECT * FROM users WHERE username=? AND password=?",(b.get("username"),hashlib.sha256(b.get("password","").encode()).hexdigest())).fetchone()
            if not r:c.close(); return self.sendj(401,{"error":"invalid"})
            tok=secrets.token_urlsafe(24);c.execute("INSERT INTO tokens VALUES(?,?)",(tok,r["id"]));c.commit();c.close();return self.sendj(200,{"user":user_obj(r),"token":tok})
        u=self.auth()
        if not u:return
        c=db(); b={}
        if self.headers.get("Content-Type","").startswith("application/json"):
            try:b=json.loads(self.body() or "{}")
            except: b={}
        if path=="/api/workspaces":
            if not re.fullmatch(r"[a-z0-9-]{2,32}",b.get("slug","")):return self.sendj(400,{"error":"invalid slug"})
            wid,cid=str(uuid.uuid4()),str(uuid.uuid4())
            try:c.execute("INSERT INTO workspaces VALUES(?,?,?,?,?)",(wid,b["slug"],b.get("name",""),u["id"],"open"));c.execute("INSERT INTO members VALUES(?,?,?)",(wid,u["id"],"owner"));c.execute("INSERT INTO channels VALUES(?,?,?,?,?,?,?)",(cid,wid,"general",0,0,"",0));c.execute("INSERT INTO channel_members VALUES(?,?)",(cid,u["id"]));c.commit()
            except sqlite3.IntegrityError:return self.sendj(409,{"error":"duplicate"})
            return self.sendj(201,{"workspace":{"id":wid,"slug":b["slug"],"name":b.get("name",""),"owner_id":u["id"],"join_mode":"open"},"general_channel":channel_obj(c.execute("SELECT * FROM channels WHERE id=?",(cid,)).fetchone())})
        m=re.fullmatch(r"/api/channels/([^/]+)/join",path)
        if m:
            r=c.execute("SELECT ch.*,w.join_mode FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE ch.id=?",(m.group(1),)).fetchone()
            if not r:return self.sendj(404,{})
            c.execute("INSERT OR IGNORE INTO members VALUES(?,?,?)",(r["workspace_id"],u["id"],"member"));c.execute("INSERT OR IGNORE INTO channel_members VALUES(?,?)",(r["id"],u["id"]));c.commit();return self.sendj(200,{"channel":channel_obj(r)})
        m=re.fullmatch(r"/api/workspaces/([^/]+)/channels",path)
        if m:
            w=c.execute("SELECT * FROM workspaces WHERE slug=?",(m.group(1),)).fetchone(); 
            if not w:return self.sendj(404,{})
            if not re.fullmatch(r"[a-z0-9-]{1,32}",b.get("name","")):return self.sendj(400,{})
            cid=str(uuid.uuid4())
            try:c.execute("INSERT INTO channels VALUES(?,?,?,?,?,?,?)",(cid,w["id"],b["name"],int(bool(b.get("is_private"))),0,b.get("topic",""),0));c.execute("INSERT INTO channel_members VALUES(?,?)",(cid,u["id"]));c.commit()
            except sqlite3.IntegrityError:return self.sendj(409,{})
            return self.sendj(201,{"channel":channel_obj(c.execute("SELECT * FROM channels WHERE id=?",(cid,)).fetchone())})
        m=re.fullmatch(r"/api/channels/([^/]+)/messages",path)
        if m:return self.message(c,u,m.group(1),b)
        m=re.fullmatch(r"/api/messages/([^/]+)/reactions",path)
        if m:return self.react(c,u,m.group(1),b.get("emoji"),True)
        if path=="/api/dms":
            other=b.get("user_id"); a,bb=sorted([u["id"],other]);r=c.execute("SELECT channel_id FROM dms WHERE a=? AND b=?",(a,bb)).fetchone()
            if r:return self.sendj(200,{"channel_id":r[0]})
            cid=str(uuid.uuid4());c.execute("INSERT INTO dms VALUES(?,?,?)",(a,bb,cid));c.execute("INSERT INTO channels VALUES(?,?,?,?,?,?,?)",(cid,"dm-"+cid,"dm",0,1,"",0));c.execute("INSERT INTO channel_members VALUES(?,?)",(cid,a));c.execute("INSERT INTO channel_members VALUES(?,?)",(cid,bb));c.commit();return self.sendj(200,{"channel_id":cid})
        if path=="/api/files":
            raw=self.body(); ct=self.headers.get("Content-Type",""); fn="upload"; typ="application/octet-stream"; z=raw
            mm=re.search(rb'filename="([^"]+)"',raw); mc=re.search(rb'Content-Type:\s*([^\r\n]+)',raw)
            if mm:fn=mm.group(1).decode()
            if mc:typ=mc.group(1).decode()
            if b"\r\n\r\n" in raw:z=raw.split(b"\r\n\r\n",1)[1].rsplit(b"\r\n--",1)[0]
            if len(z)>10*1024*1024:return self.sendj(413,{})
            fid=str(uuid.uuid4()); created=now();c.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,0)",(fid,u["id"],fn,typ,len(z),created,z));c.commit();return self.sendj(201,{"file":{"id":fid,"uploader_id":u["id"],"filename":fn,"content_type":typ,"size":len(z),"created_at":created}})
        m=re.fullmatch(r"/api/workspaces/([^/]+)/groups",path)
        if m:
            w=c.execute("SELECT * FROM workspaces WHERE slug=?",(m.group(1),)).fetchone();gid=str(uuid.uuid4());c.execute("INSERT INTO groups_ VALUES(?,?,?,?,?)",(gid,w["id"],b["handle"],b.get("name",""),u["id"]))
            for x in b.get("user_ids",[]):c.execute("INSERT OR IGNORE INTO group_members VALUES(?,?)",(gid,x))
            c.commit();return self.sendj(201,{"group":{"id":gid,"handle":b["handle"],"name":b.get("name",""),"member_user_ids":b.get("user_ids",[])}})
        c.close();return self.sendj(404,{})
    def message(self,c,u,cid,b):
        ch=c.execute("SELECT * FROM channels WHERE id=?",(cid,)).fetchone()
        if not ch:return self.sendj(404,{})
        body=b.get("body","")
        if not body:return self.sendj(400,{})
        if body.startswith("/") and not body.startswith("//"):
            parts=body.split(" ",1); cmd=parts[0]; arg=parts[1] if len(parts)>1 else ""
            if cmd=="/me" and not arg:return self.sendj(400,{})
            if cmd=="/shrug":body=(arg+" " if arg else "")+"¯\\_(ツ)_/¯"
            elif cmd=="/me": pass
            elif cmd=="/topic":
                c.execute("UPDATE channels SET topic=? WHERE id=?",(arg,cid));c.commit();return self.sendj(201,{"message":None,"channel":channel_obj(c.execute("SELECT * FROM channels WHERE id=?",(cid,)).fetchone())})
            elif cmd in ("/archive","/unarchive"):
                c.execute("UPDATE channels SET is_archived=? WHERE id=?",(1 if cmd=="/archive" else 0,cid));c.commit();return self.sendj(201,{"message":None,"channel":channel_obj(c.execute("SELECT * FROM channels WHERE id=?",(cid,)).fetchone())})
            elif cmd=="/invite" and arg.startswith("@"):
                r=c.execute("SELECT * FROM users WHERE username=?",(arg[1:],)).fetchone()
                if not r:return self.sendj(400,{})
                c.execute("INSERT OR IGNORE INTO members VALUES(?,?,?)",(ch["workspace_id"],r["id"],"member"));c.commit();return self.sendj(201,{"message":None,"invited_user":user_obj(r)})
            else:return self.sendj(400,{})
        if body.startswith("//"):body=body[1:]
        fs=[]
        for fid in b.get("file_ids",[]):
            r=c.execute("SELECT * FROM files WHERE id=? AND uploader_id=? AND attached=0",(fid,u["id"])).fetchone()
            if not r:return self.sendj(400,{})
            c.execute("UPDATE files SET attached=1 WHERE id=?",(fid,));fs.append({k:r[k] for k in ("id","uploader_id","filename","content_type","size","created_at")})
        mid=str(uuid.uuid4()); ts=now(); parent=b.get("parent_id"); seq=(c.execute("SELECT COALESCE(MAX(seq),0)+1 FROM messages WHERE channel_id=?",(cid,)).fetchone()[0])
        c.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)",(mid,cid,u["id"],body,parent,ts,None,0,seq));c.execute("INSERT INTO events VALUES(?,?,?,?)",(cid,seq,"message.created",json.dumps({"type":"message.created","event_id":seq,"channel_id":cid})));c.commit()
        mentions=[]
        for x in re.findall(r"(?<!\w)@([A-Za-z0-9_]+)",body):
            if x == "here":
                rows=c.execute("SELECT DISTINCT cm.user_id FROM channel_members cm JOIN presence p ON p.user_id=cm.user_id AND p.channel_id=cm.channel_id WHERE cm.channel_id=? AND p.updated_at>?",(cid,time.time()-30)).fetchall()
                mentions.extend(r[0] for r in rows if r[0] != u["id"])
                continue
            r=c.execute("SELECT id FROM users WHERE username=?",(x,)).fetchone()
            if r and r[0]!=u["id"]:mentions.append(r[0])
        for x in re.findall(r"(?<!\w)@([A-Za-z0-9_]+)",body):
            g=c.execute("SELECT id FROM groups_ WHERE workspace_id=? AND handle=?",(ch["workspace_id"],x)).fetchone()
            if g:
                rows=c.execute("SELECT cm.user_id FROM channel_members cm JOIN group_members gm ON gm.user_id=cm.user_id WHERE cm.channel_id=? AND gm.group_id=?",(cid,g["id"])).fetchall()
                mentions.extend(r[0] for r in rows if r[0] != u["id"])
        msg={"id":mid,"channel_id":cid,"author_id":u["id"],"author":user_obj(u),"body":body,"parent_id":parent,"reply_count":0,"created_at":ts,"edited_at":None,"files":fs,"reactions":[],"mentions":list(dict.fromkeys(mentions))}
        return self.sendj(201,{"message":msg})
    def react(self,c,u,mid,emoji,add):
        r=c.execute("SELECT * FROM messages WHERE id=? AND deleted=0",(mid,)).fetchone()
        if not r:return self.sendj(404,{})
        if add:c.execute("INSERT OR IGNORE INTO reactions VALUES(?,?,?)",(mid,u["id"],emoji))
        else:c.execute("DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?",(mid,u["id"],emoji))
        c.commit(); rows=c.execute("SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji",(mid,)).fetchall()
        return self.sendj(200,{"reactions":[{"emoji":x["emoji"],"count":x["n"],"user_ids":x["ids"].split(",")} for x in rows]})
    def do_DELETE(self):
        p=self.route();u=self.auth()
        if not u:return
        c=db();m=re.fullmatch(r"/api/messages/([^/]+)/reactions/(.+)",p.path)
        if m:return self.react(c,u,m.group(1),urllib.parse.unquote(m.group(2)),False)
        m=re.fullmatch(r"/api/workspaces/([^/]+)/groups/([^/]+)",p.path)
        if m:
            g=c.execute("SELECT g.* FROM groups_ g JOIN workspaces w ON w.id=g.workspace_id WHERE w.slug=? AND g.handle=?",(m.group(1),m.group(2))).fetchone()
            if not g:return self.sendj(200,{"deleted":True})
            if g["creator_id"]!=u["id"] and not c.execute("SELECT 1 FROM members WHERE workspace_id=? AND user_id=? AND role='owner'",(g["workspace_id"],u["id"])).fetchone():return self.sendj(403,{})
            c.execute("DELETE FROM groups_ WHERE id=?",(g["id"],));c.commit();return self.sendj(200,{"deleted":True})
        return self.sendj(404,{})
    def do_PATCH(self):
        p=self.route();u=self.auth()
        if not u:return
        b=json.loads(self.body() or "{}");c=db()
        if p.path=="/api/users/me":
            if "timezone" in b and b["timezone"] not in ("UTC","America/New_York","Europe/London"):return self.sendj(400,{})
            for k in ("display_name","timezone","status_text","status_emoji"):
                if k in b:c.execute("UPDATE users SET %s=? WHERE id=?"%k,(b[k],u["id"]))
            updated=user_obj(c.execute("SELECT * FROM users WHERE id=?",(u["id"],)).fetchone())
            c.execute("INSERT INTO global_events(payload) VALUES(?)",(json.dumps({"type":"user.updated","user":updated}),)); c.commit()
            return self.sendj(200,{"user":updated})
        m=re.fullmatch(r"/api/workspaces/([^/]+)/groups/([^/]+)",p.path)
        if m:
            g=c.execute("SELECT g.* FROM groups_ g JOIN workspaces w ON w.id=g.workspace_id WHERE w.slug=? AND g.handle=?",(m.group(1),m.group(2))).fetchone()
            if not g:return self.sendj(404,{})
            if g["creator_id"]!=u["id"]:return self.sendj(403,{})
            c.execute("UPDATE groups_ SET name=? WHERE id=?",(b.get("name",g["name"]),g["id"]));c.commit();return self.sendj(200,{"group":{"id":g["id"],"handle":g["handle"],"name":b.get("name",g["name"]),"member_user_ids":[x[0] for x in c.execute("SELECT user_id FROM group_members WHERE group_id=?",(g["id"],))]}})
        return self.sendj(404,{})
    def websocket(self,token):
        c=db();u=c.execute("SELECT user_id FROM tokens WHERE token=?",(token,)).fetchone();c.close()
        if not u:self.send_response(401);self.end_headers();return
        key=self.headers.get("Sec-WebSocket-Key"); acc=base64.b64encode(hashlib.sha1((key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101);self.send_header("Upgrade","websocket");self.send_header("Connection","Upgrade");self.send_header("Sec-WebSocket-Accept",acc);self.end_headers(); self.close_connection=True
        self.connection.settimeout(.2); sub=None; last=0
        c=db(); global_last=c.execute("SELECT COALESCE(MAX(id),0) FROM global_events").fetchone()[0]; c.close()
        def out(x):
            b=json.dumps(x).encode(); n=len(b); h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack("!H",n);self.connection.sendall(h+b)
        try:
            while True:
                try:
                    h=self.connection.recv(2)
                    if not h:break
                    n=h[1]&127
                    if n==126:n=struct.unpack("!H",self.connection.recv(2))[0]
                    mask=self.connection.recv(4); data=self.connection.recv(n);data=bytes(v^mask[i%4] for i,v in enumerate(data)); x=json.loads(data)
                    if x.get("type") in ("subscribe","resume"):
                        sub=x["channel_id"]; c=db(); c.execute("INSERT OR REPLACE INTO presence VALUES(?,?,?)",(u["user_id"],sub,time.time())); head=c.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE channel_id=?",(sub,)).fetchone()[0]; c.commit(); c.close(); out({"type":"subscribed" if x["type"]=="subscribe" else "resumed","channel_id":sub,"head_event_id":head}); last=x.get("since_event_id",0)
                except socket.timeout:pass
                c=db()
                grows=c.execute("SELECT id,payload FROM global_events WHERE id>? ORDER BY id",(global_last,)).fetchall()
                for gr in grows:
                    out(json.loads(gr["payload"])); global_last=gr["id"]
                c.close()
                if sub:
                    c=db(); rows=c.execute("SELECT seq,type,payload FROM events WHERE channel_id=? AND seq>? ORDER BY seq",(sub,last)).fetchall()
                    for r in rows:
                        x=json.loads(r["payload"]);x["type"]=r["type"];out(x);last=r["seq"]
                    c.close()
        except: pass
    def html(self):
        b=b"""<!doctype html><html><body><h1>Huddle</h1><p>API service</p></body></html>"""
        self.send_response(200);self.send_header("Content-Type","text/html");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
if __name__=="__main__":
    ThreadingHTTPServer(("127.0.0.1",PORT),H).serve_forever()
