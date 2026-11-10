#!/usr/bin/env python3
import socket,threading,sqlite3,json,time,uuid,os
DB='/app/chat.db'
clients=[]; lock=threading.Lock()
def db():
 c=sqlite3.connect(DB,timeout=3);c.row_factory=sqlite3.Row;return c
def send(s,line):
 try:s.sendall((line+'\r\n').encode())
 except:pass
def user_for(tok):
 c=db();r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.uid WHERE t.token=?',(tok,)).fetchone();c.close();return r
def channel(name):
 if not name.startswith('#') or '/' not in name:return None
 slug,n=name[1:].split('/',1);c=db();r=c.execute('SELECT ch.* FROM channels ch JOIN workspaces w ON w.id=ch.ws WHERE w.slug=? AND ch.name=? AND ch.is_dm=0',(slug,n)).fetchone();c.close();return r
def event(c,cid,body):
 seq=c.execute('SELECT COALESCE(MAX(seq),0)+1 n FROM events WHERE cid=?',(cid,)).fetchone()['n']
 d={'type':'message.created','event_id':seq,'channel_id':cid,'message':body}
 c.execute('INSERT INTO events VALUES(?,?,?)',(cid,seq,json.dumps(d)));c.commit()
def write_msg(u,ch,body):
 c=db();mid=str(uuid.uuid4());ts=time.strftime('%Y-%m-%dT%H:%M:%S.000Z',time.gmtime())
 c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)',(mid,ch['id'],u['id'],body,None,ts,None,0,'[]'))
 a=dict(u);a.pop('password',None)
 mo={'id':mid,'channel_id':ch['id'],'author_id':u['id'],'author':{k:a.get(k,'') for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')},'body':body,'parent_id':None,'reply_count':0,'created_at':ts,'edited_at':None,'files':[],'reactions':[],'mentions':[]}
 event(c,ch['id'],mo);c.close();return mo
def broadcaster():
 while True:
  with lock: snap=list(clients)
  for x in snap:
   if not x['joined']:continue
   c=db()
   rows=c.execute('SELECT seq,data FROM events WHERE cid=? AND seq>? ORDER BY seq',(x['joined']['id'],x['last'])).fetchall();c.close()
   for r in rows:
    x['last']=r['seq'];d=json.loads(r['data'])
    if d.get('type')=='message.created':
     send(x['sock'],':%s!%s@localhost PRIVMSG %s :%s'%(d['message']['author']['username'],d['message']['author']['username'],x['name'],d['message']['body']))
  time.sleep(.08)
def handle(s,addr):
 x={'sock':s,'user':None,'nick':None,'joined':None,'name':None,'last':0};clients.append(x);f=s.makefile('rb')
 try:
  for raw in f:
   line=raw.decode(errors='replace').strip()
   if not line:continue
   a=line.split(' ',2);cmd=a[0].upper();arg=a[1] if len(a)>1 else '';trail=(a[2][1:] if len(a)>2 and a[2].startswith(':') else (a[2] if len(a)>2 else (arg[1:] if arg.startswith(':') else arg)))
   if cmd=='PASS':
    x['user']=user_for(arg)
    if not x['user']:send(s,':irc 464 * :Password incorrect');continue
   elif cmd=='NICK':
    if any(y.get('nick')==arg for y in clients if y is not x):send(s,':irc 433 * %s :Nickname in use'%arg);continue
    x['nick']=arg
   elif cmd=='USER':
    if x['user'] and x['nick'] and not x.get('registered'):
     x['registered']=True;x['nick']=x['nick'];x['name']=None
     for n in ('001','002','003','004','005'):send(s,':irc %s %s :Huddle'% (n,x['nick']))
     send(s,':irc 422 %s :MOTD File is missing'%x['nick'])
   elif cmd=='PING':send(s,'PONG :'+trail)
   elif cmd=='JOIN' and x.get('registered'):
    ch=channel(arg)
    if not ch:send(s,':irc 403 %s %s :No such channel'%(x['nick'],arg));continue
    x['joined']=ch;x['name']=arg;x['last']=db().execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE cid=?',(ch['id'],)).fetchone()[0]
    send(s,':%s!%s@localhost JOIN %s'%(x['nick'],x['nick'],arg))
    c=db();rows=c.execute('SELECT u.username FROM cmembers m JOIN users u ON u.id=m.uid WHERE m.cid=?',(ch['id'],)).fetchall();c.close()
    send(s,':irc 353 %s = %s :%s'%(x['nick'],arg,' '.join(r['username'] for r in rows)));send(s,':irc 366 %s %s :End of /NAMES list.'%(x['nick'],arg))
   elif cmd=='NAMES':
    ch=channel(arg)
    if ch:
     c=db();rows=c.execute('SELECT u.username FROM cmembers m JOIN users u ON u.id=m.uid WHERE m.cid=?',(ch['id'],)).fetchall();c.close();send(s,':irc 353 %s = %s :%s'%(x['nick'],arg,' '.join(r['username'] for r in rows)));send(s,':irc 366 %s %s :End of /NAMES list.'%(x['nick'],arg))
   elif cmd=='PRIVMSG' and x.get('joined') and arg==x['name']:
    write_msg(x['user'],x['joined'],trail)
   elif cmd=='QUIT':break
   elif cmd not in ('MODE','WHO','LIST','TOPIC','PONG'):send(s,':irc 421 %s %s :Unknown command'%(x.get('nick','*'),cmd))
 except:pass
 finally:
  try:s.close()
  except:pass
  with lock:
   if x in clients:clients.remove(x)
def main():
 threading.Thread(target=broadcaster,daemon=True).start()
 z=socket.socket();z.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);z.bind(('0.0.0.0',6667));z.listen(50)
 while True:
  s,a=z.accept();threading.Thread(target=handle,args=(s,a),daemon=True).start()
if __name__=='__main__':main()
