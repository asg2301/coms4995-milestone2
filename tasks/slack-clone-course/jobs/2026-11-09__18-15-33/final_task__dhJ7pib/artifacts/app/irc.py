import socket,threading,re
import server, os
from server import one,q,db,lock,now,msgobj,broadcast
def client(s):
 f=s.makefile('rb'); nick=None;uid=None;cid=None;slug=None
 def out(x):s.sendall((x+'\r\n').encode())
 try:
  while True:
   line=f.readline().decode(errors='ignore').strip()
   if not line:break
   a=line.split(); cmd=a[0].upper()
   if cmd=='PASS': 
    r=one('select user_id from tokens where token=?',(a[1],));uid=r[0] if r else None
   elif cmd=='NICK':nick=a[1]
   elif cmd=='USER' and uid:
    out(f':local 001 {nick} :Welcome');out(f':local 002 {nick} :Your host');out(f':local 003 {nick} :Created');out(f':local 004 {nick} :server');out(f':local 005 {nick} :CHATHISTORY');out(f':local 422 {nick} :No MOTD')
   elif cmd=='JOIN' and uid:
    m=re.match(r'#([^/]+)/(.+)',a[1]); r=one('select c.id,w.slug from channels c join workspaces w on w.id=c.workspace_id where w.slug=? and c.name=?',(m.group(1),m.group(2))) if m else None
    if not r:out(f':local 403 {nick} {a[1]} :No such channel');continue
    cid,slug=r; broadcast({'type':'noop','channel_id':cid});out(f':{nick}!u@localhost JOIN #{slug}/general');out(f':local 353 {nick} = #{slug}/general :{nick}');out(f':local 366 {nick} #{slug}/general :End of NAMES')
    from server import irc_clients
    irc_clients.append({'sock':s,'cid':cid,'slug':slug})
   elif cmd=='PRIVMSG' and cid:
    body=line.split(' :',1)[1] if ' :' in line else ''; 
    with lock:
     t=now();c=db.execute('insert into messages(channel_id,author_id,body,created_at) values(?,?,?,?)',(cid,uid,body,t));mid=c.lastrowid;eid=db.execute('select coalesce(max(event_id),0)+1 from events where channel_id=?',(cid,)).fetchone()[0];db.execute('insert into events values(?,?,?,?,?)',(cid,eid,mid,'message.created',t));db.commit()
   elif cmd=='PING':out('PONG '+(a[1] if len(a)>1 else ''))
   elif cmd=='QUIT':break
 finally:s.close()
def serve():
 x=socket.socket();x.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);x.bind(('0.0.0.0',6667));x.listen()
 while True:
  s,_=x.accept();threading.Thread(target=client,args=(s,),daemon=True).start()
if os.environ.get('GATEWAY'):
    serve()
else:
    from http.server import ThreadingHTTPServer
    ThreadingHTTPServer(('127.0.0.1',8000+server.NODE),server.H).serve_forever()
