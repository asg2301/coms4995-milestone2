#!/bin/sh
set -e
rm -f /app/state.db
for n in 0 1 2; do NODE_ID=$n python3 /app/app.py >/tmp/huddle-$n.log 2>&1 & echo $! >>/tmp/huddle.pids; done
python3 - <<'PY'
import socketserver,sqlite3,threading,time
DB='/app/state.db'
class Client(socketserver.BaseRequestHandler):
 def setup(self):
  self.nick=None; self.uid=None; self.token=None; self.joined=[]; self.last={}; self.alive=True; self.lock=threading.Lock()
  self.server.clients.append(self)
 def sendline(self,s):
  try:
   with self.lock:self.request.sendall((s+'\r\n').encode())
  except:self.alive=False
 def finish(self):
  self.alive=False
  if self in self.server.clients:self.server.clients.remove(self)
  try:self.request.close()
  except:pass
 def register(self):
  if self.nick and self.uid and not getattr(self,'registered',False):
   self.registered=True
   self.sendline(':irc 001 '+self.nick+' :Welcome')
   for n in ('002','003','004','005'):self.sendline(':irc '+n+' '+self.nick+' :huddle')
   self.sendline(':irc 422 '+self.nick+' :MOTD')
 def pump(self):
  while self.alive:
   time.sleep(.1)
   if not self.uid:continue
   try:
    c=sqlite3.connect(DB);c.row_factory=sqlite3.Row
    for cid,name in list(self.joined):
     for e in c.execute('select e.seq,m.body,u.username from events e join messages m on m.id=e.message join users u on u.id=m.author where e.channel=? and e.seq>? order by e.seq',(cid,self.last.get(cid,0))):
      self.last[cid]=e['seq']
      if e['username']!=self.nick:self.sendline(':'+e['username']+'!user@host PRIVMSG #'+name+' :'+e['body'])
    c.close()
   except:pass
 def handle(self):
  threading.Thread(target=self.pump,daemon=True).start()
  buf=b''
  while self.alive:
   try:
    z=self.request.recv(4096)
    if not z:break
    buf+=z
   except:break
   while b'\n' in buf:
    raw,buf=buf.split(b'\n',1); line=raw.decode(errors='replace').strip('\r')
    if not line:continue
    a=line.split(' ',2); cmd=a[0].upper(); args=a[1:]
    open('/tmp/irc-lines.log','a').write(repr((line,cmd,args,self.uid,self.joined))+'\\n')
    if cmd=='PASS':self.token=args[0] if args else ''
    elif cmd=='NICK':
     n=args[0] if args else ''
     if any(x is not self and x.nick==n for x in self.server.clients):
      self.sendline(':irc 433 * '+n+' :Nickname in use')
     else:self.nick=n;self.register()
    elif cmd=='USER':
     c=sqlite3.connect(DB);r=c.execute('select user_id from tokens where token=?',(self.token,)).fetchone();c.close()
     if r:self.uid=r[0];self.register()
     else:self.sendline(':irc 464 * :Password incorrect')
    elif cmd=='PING':self.sendline(':irc PONG '+(' '.join(args)))
    elif cmd=='JOIN':
     key=(args[-1] if args else '').lstrip(':#');c=sqlite3.connect(DB);r=c.execute('select w.slug,c.name,c.id from channels c join workspaces w on w.id=c.ws where w.slug||"/"||c.name=?',(key,)).fetchone();c.close()
     if not r:self.sendline(':irc 403 '+self.nick+' #'+key+' :No such channel');continue
     name=r[0]+'/'+r[1];self.joined.append((r[2],name));c=sqlite3.connect(DB);self.last[r[2]]=c.execute('select coalesce(max(seq),0) from events where channel=?',(r[2],)).fetchone()[0];c.close()
     self.sendline(':'+self.nick+'!user@host JOIN :#'+name);self.sendline(':irc 353 '+self.nick+' = #'+name+' :'+self.nick);self.sendline(':irc 366 '+self.nick+' #'+name+' :End of NAMES')
    elif cmd=='PRIVMSG':
     try:
      key=args[0].lstrip('#') if args else ''
      body=args[1][1:] if len(args)>1 and args[1].startswith(':') else (' '.join(args[1:]) if len(args)>1 else '')
      c=sqlite3.connect(DB,timeout=10); c.row_factory=sqlite3.Row
      r=c.execute("select c.id from channels c join workspaces w on w.id=c.ws where w.slug||'/'||c.name=?",(key,)).fetchone()
      if not r:
       open('/tmp/irc-errors.log','a').write('unresolved '+repr((key,self.uid,line))+'\\n')
      else:
       cid=r['id']
       c.execute("insert into messages(channel,author,body,parent,created) values(?,?,?,?,datetime('now'))",(cid,self.uid,body,None))
       mid=c.execute('select last_insert_rowid()').fetchone()[0]
       seq=c.execute('select coalesce(max(seq),0)+1 from events where channel=?',(cid,)).fetchone()[0]
       c.execute("insert into events(channel,seq,kind,message,created) values(?,?,?,?,datetime('now'))",(cid,seq,'message.created',mid))
       c.commit()
      c.close()
     except Exception as exc:
      open('/tmp/irc-errors.log','a').write('exception '+repr(exc)+'\\n')
    elif cmd=='QUIT':return
    elif cmd not in ('MODE','TOPIC','NAMES','WHO','LIST'):self.sendline(':irc 421 '+(self.nick or '*')+' '+cmd+' :Unknown command')
class Server(socketserver.ThreadingMixIn,socketserver.TCPServer):
 allow_reuse_address=True
srv=Server(('0.0.0.0',6667),Client);srv.clients=[];srv.serve_forever()
PY
