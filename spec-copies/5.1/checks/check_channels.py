#!/usr/bin/env python3
"""Black-box B acceptance samples. Fresh fixtures, standard library only."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import struct
import sys
import time
from urllib.parse import urlencode

helper = Path(__file__).with_name('check_roles.py')
spec = importlib.util.spec_from_file_location('component_a_helpers', helper)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
check, request, call = a.check, a.request, a.call


def fixture():
    p,u,slug,ws,general = a.fixture()
    c = request('B fixture create channel','POST',f'/workspaces/{slug}/channels',u['owner']['token'],{'name':p+'-chat'},status=201)['channel']
    for k in ['member','admin']:
        request('B fixture join '+k,'POST',f'/channels/{c["id"]}/join',u[k]['token'])
    return p,u,slug,ws,c


def channels():
    p,u,slug,ws,c = fixture()
    owner,member,outsider=[u[k]['token'] for k in ['owner','member','out']]
    path=f'/channels/{c["id"]}'
    check('channel required fields',{'id','workspace_id','name','is_private','is_dm','topic','is_archived'} <= set(c))
    check('channel default flags/topic',c.get('topic')=='' and c.get('is_private') is False and c.get('is_archived') is False)
    cp=f'/workspaces/{slug}/channels'
    request('channel duplicate name','POST',cp,owner,{'name':c['name']},status=409)
    for name in ['Bad Name','a'*33]:
        request('channel invalid name','POST',cp,owner,{'name':name},status=400)
    request('channel oversized initial topic','POST',cp,owner,{'name':p+'-long','topic':'x'*251},status=400)
    changed=request('channel member updates topic','PATCH',path,member,{'topic':'x'*250})
    check('channel topic boundary accepted',changed.get('channel',{}).get('topic')=='x'*250)
    request('channel too long topic','PATCH',path,member,{'topic':'x'*251},status=400)
    request('channel outsider topic forbidden','PATCH',path,outsider,{'topic':'No'},status=403)
    members=request('channel members','GET',path+'/members',owner).get('members',[])
    check('channel member list uses UserObj',bool(members) and all({'id','username','display_name','timezone','avatar_url','status_text','status_emoji'} <= set(m) for m in members))
    request('channel leave','POST',path+'/leave',member)
    request('channel leave idempotent','POST',path+'/leave',member)
    members=request('channel members after leave','GET',path+'/members',owner).get('members',[])
    check('channel departed member absent',u['member']['id'] not in [m.get('id') for m in members])
    request('channel rejoin','POST',path+'/join',member)
    secret=request('channel create private','POST',cp,owner,{'name':p+'-secret','is_private':True},status=201)['channel']
    detail=request('channel workspace detail','GET',f'/workspaces/{slug}',member)
    check('channel workspace envelope',{'workspace','channels','read_state'} <= set(detail))
    check('channel private omitted for nonmember',secret['id'] not in [x.get('id') for x in detail.get('channels',[])])
    request('channel private pins forbidden','GET',f'/channels/{secret["id"]}/pins',member,status=403)
    request('channel nonowner archive denied','POST',path+'/archive',u['admin']['token'],status=403)
    archived=request('channel owner archives','POST',path+'/archive',owner)
    check('channel archived flag',archived.get('channel',{}).get('is_archived') is True)
    request('channel archived write denied','POST',path+'/messages',owner,{'body':'blocked'},status=423)
    detail=request('channel default listing excludes archived','GET',f'/workspaces/{slug}',owner)
    check('channel archived omitted',c['id'] not in [x.get('id') for x in detail.get('channels',[])])
    detail=request('channel listing includes archived','GET',f'/workspaces/{slug}?include_archived=1',owner)
    check('channel archived included',c['id'] in [x.get('id') for x in detail.get('channels',[])])
    request('channel owner unarchives','POST',path+'/unarchive',owner)
    request('channel restored write','POST',path+'/messages',owner,{'body':'restored'},status=201)


def paging():
    p,u,slug,ws,c=fixture();token=u['owner']['token'];path=f'/channels/{c["id"]}/messages'
    def post(body,**extra):
        return request('message create','POST',path,token,{'body':body,**extra},status=201)['message']
    request('message empty invalid','POST',path,token,{'body':''},status=400)
    messages=[post(p+' marker '+str(i)) for i in range(5)]
    check('message object fields',{'id','channel_id','author_id','author','body','parent_id','reply_count','created_at','edited_at','files','reactions','mentions'} <= set(messages[0]))
    first=request('history first page','GET',path+'?limit=2',token)
    check('history newest first',[m.get('id') for m in first.get('messages',[])]==[messages[4]['id'],messages[3]['id']])
    cursor=first.get('next_cursor')
    check('history opaque cursor exists',isinstance(cursor,str) and bool(cursor))
    if not isinstance(cursor,str) or not cursor:
        raise RuntimeError('Cannot test subsequent pages without a cursor')
    post(p+' inserted later')
    second=request('history cursor across node after insertion','GET',path+'?'+urlencode({'limit':2,'cursor':cursor}),token,node=2)
    check('history stable position',[m.get('id') for m in second.get('messages',[])]==[messages[2]['id'],messages[1]['id']])
    last=request('history final page','GET',path+'?'+urlencode({'limit':2,'cursor':second.get('next_cursor','')}),token,node=1)
    check('history last page and null cursor',[m.get('id') for m in last.get('messages',[])]==[messages[0]['id']] and 'next_cursor' in last and last['next_cursor'] is None)
    for query in ['limit=0','limit=-1','limit=201','cursor=not-a-valid-cursor']:
        request('history invalid '+query,'GET',path+'?'+query,token,status=400)
    default=request('history default limit envelope','GET',path,token)
    check('history default page has cursor key','next_cursor' in default)
    parent=messages[0]
    replies=[post('reply '+str(i),parent_id=parent['id']) for i in range(3)]
    rp=f'/messages/{parent["id"]}/replies'
    one=request('replies first page','GET',rp+'?limit=2',token)
    check('replies oldest first',[m.get('id') for m in one.get('replies',[])]==[m['id'] for m in replies[:2]])
    two=request('replies next page','GET',rp+'?'+urlencode({'limit':2,'cursor':one.get('next_cursor','')}),token,node=1)
    check('replies final cursor',[m.get('id') for m in two.get('replies',[])]==[replies[2]['id']] and two.get('next_cursor','missing') is None)
    search=request('search paging first','GET','/search?'+urlencode({'q':p+' marker','limit':2,'workspace_id':ws['id']}),token)
    check('search newest first',[m.get('id') for m in search.get('results',[])]==[messages[4]['id'],messages[3]['id']])
    more=request('search paging next across node','GET','/search?'+urlencode({'q':p+' marker','limit':2,'cursor':search.get('next_cursor',''),'workspace_id':ws['id']}),token,node=2)
    check('search second page',[m.get('id') for m in more.get('results',[])]==[messages[2]['id'],messages[1]['id']])
    other=request('search outsider has no access','GET','/search?'+urlencode({'q':p+' marker'}),u['out']['token'])
    check('search access filtered',other.get('results')==[])
    for m in messages[:3]:
        pin=request('pin create','POST',f'/messages/{m["id"]}/pin',token)
        check('pin envelope',{'message_id','pinned_by','pinned_at'} <= set(pin.get('pin',{})))
    request('pin idempotent','POST',f'/messages/{messages[2]["id"]}/pin',token)
    pp=f'/channels/{c["id"]}/pins'
    pins=request('pin first page','GET',pp+'?limit=2',token)
    check('pins newest first',[x.get('message',{}).get('id') for x in pins.get('pins',[])]==[messages[2]['id'],messages[1]['id']])
    tail=request('pin next page','GET',pp+'?'+urlencode({'limit':2,'cursor':pins.get('next_cursor','')}),token,node=1)
    check('pins final page no duplicates',[x.get('message',{}).get('id') for x in tail.get('pins',[])]==[messages[0]['id']] and tail.get('next_cursor','missing') is None)
    deleted=request('pin remove','DELETE',f'/messages/{messages[0]["id"]}/pin',token)
    check('pin delete envelope',deleted.get('unpinned') is True)


class WS:
    """Minimal RFC6455 client: verified handshake, masked sends, ping support."""
    def __init__(self,token,node=0):
        self.sock=socket.create_connection(('127.0.0.1',8000+node),timeout=1)
        self.sock.settimeout(1.5)
        self.file=self.sock.makefile('rb')
        key=base64.b64encode(os.urandom(16)).decode()
        req=f'GET /api/ws?{urlencode({"token":token})} HTTP/1.1\r\nHost: 127.0.0.1:{8000+node}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'
        self.sock.sendall(req.encode());status=self.file.readline()
        headers={}
        while True:
            line=self.file.readline()
            if line in (b'\r\n',b'\n',b''):break
            k,v=line.decode().split(':',1);headers[k.lower()]=v.strip()
        expected=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        if b' 101 ' not in status or headers.get('sec-websocket-accept') != expected:
            self.close();raise RuntimeError('Invalid WebSocket handshake: '+status.decode().strip())
    def exact(self,n):
        data=self.file.read(n)
        if len(data)!=n:raise RuntimeError('WebSocket closed before expected frame')
        return data
    def send_bytes(self,payload,opcode=1):
        mask=os.urandom(4);n=len(payload)
        header=bytes([0x80|opcode,0x80|n]) if n<126 else bytes([0x80|opcode,0x80|126])+struct.pack('!H',n)
        self.sock.sendall(header+mask+bytes(v^mask[i%4] for i,v in enumerate(payload)))
    def send(self,obj):self.send_bytes(json.dumps(obj).encode())
    def receive(self):
        while True:
            first,second=self.exact(2);n=second&127
            if n==126:n=struct.unpack('!H',self.exact(2))[0]
            elif n==127:n=struct.unpack('!Q',self.exact(8))[0]
            if n>1000000:raise RuntimeError('Unexpected oversized WS frame')
            mask=self.exact(4) if second&128 else None
            payload=self.exact(n)
            if mask:payload=bytes(v^mask[i%4] for i,v in enumerate(payload))
            opcode=first&15
            if opcode==9:self.send_bytes(payload,10);continue
            if opcode==8:raise RuntimeError('Server closed WebSocket')
            if opcode==1:return json.loads(payload)
    def until(self,kind,channel=None):
        for _ in range(40):
            obj=self.receive()
            if obj.get('type')==kind and (channel is None or obj.get('channel_id')==channel):return obj
        raise RuntimeError('Did not receive '+kind)
    def close(self):
        self.file.close();self.sock.close()


def mentions():
    p,u,slug,ws,c=fixture();owner=u['owner'];member=u['member'];cid=c['id']
    path=f'/channels/{cid}'
    w=WS(member['token'],2)
    try:
        w.send({'type':'subscribe','channel_id':cid});head=w.until('subscribed',cid)['head_event_id']
        request('read mark starting head','POST',path+'/read',member['token'],{'last_read_event_id':head})
        text=f'@{member["name"]} @{member["name"]} @{owner["name"]} @{u["out"]["name"]} mail@{u["admin"]["name"]} x@{u["admin"]["name"]}'
        m=request('mentions create','POST',path+'/messages',owner['token'],{'body':text},status=201)['message']
        check('mentions resolve dedupe exclude self/nonmember/email',m.get('mentions')==[member['id']])
        event=w.until('message.created',cid)
        read=request('read after mention','GET',path+'/read',member['token'],node=1).get('read_state',{})
        check('read shape',{'channel_id','last_read_event_id','unread_count','mention_count'} <= set(read))
        check('read unread and mention increment',read.get('mention_count')==1 and read.get('unread_count',0)>=1)
        cleared=request('read advance cursor','POST',path+'/read',member['token'],{'last_read_event_id':event['event_id']}).get('read_state',{})
        check('read clear mentions',cleared.get('mention_count')==0 and cleared.get('unread_count')==0)
        backwards=request('read backwards ignored','POST',path+'/read',member['token'],{'last_read_event_id':head},node=2).get('read_state',{})
        check('read monotonic cursor',backwards.get('last_read_event_id')==event['event_id'])
        cm=request('mentions channel expansion','POST',path+'/messages',owner['token'],{'body':'@channel @'+member['name']},status=201)['message']
        check('mentions channel deduped',set(cm.get('mentions',[]))=={member['id'],u['admin']['id']} and len(cm.get('mentions',[]))==2)
        own=request('mentions author read state','GET',path+'/read',owner['token']).get('read_state',{})
        check('mentions self never increments',own.get('mention_count')==0)
        detail=request('read workspace embedding','GET',f'/workspaces/{slug}',member['token'])
        embedded=detail.get('read_state')
        if isinstance(embedded,list): rows=embedded
        elif isinstance(embedded,dict):
            rows=[embedded] if 'channel_id' in embedded else list(embedded.values())
        else:rows=[]
        check('read embedded matching shape',any(isinstance(x,dict) and x.get('channel_id')==cid and {'last_read_event_id','unread_count','mention_count'}<=set(x) for x in rows))
    finally:w.close()


def events():
    p,u,slug,ws,c=fixture();cid=c['id'];token=u['owner']['token']
    observer=WS(token,2)
    seen=[]
    try:
        observer.send({'type':'subscribe','channel_id':cid});hello=observer.until('subscribed',cid)
        check('events subscribed head integer',isinstance(hello.get('head_event_id'),int))
        def capture(kind):
            e=observer.until(kind,cid);seen.append(e)
            check('events '+kind+' sequence',e.get('event_id')==hello['head_event_id']+len(seen),e)
            return e
        message=request('events HTTP node0 write','POST',f'/channels/{cid}/messages',token,{'body':'event '+p},status=201)['message']
        capture('message.created')
        request('events node1 reply','POST',f'/channels/{cid}/messages',token,{'body':'reply','parent_id':message['id']},status=201,node=1)
        capture('message.reply')
        request('events edit','PATCH',f'/messages/{message["id"]}',token,{'body':'edited'},node=1)
        capture('message.updated')
        request('events reaction add','POST',f'/messages/{message["id"]}/reactions',token,{'emoji':'smile'})
        capture('reaction.added')
        request('events reaction remove','DELETE',f'/messages/{message["id"]}/reactions/smile',token,node=1)
        capture('reaction.removed')
        request('events channel update','PATCH',f'/channels/{cid}',token,{'topic':'updated'})
        capture('channel.updated')
        request('events archive','POST',f'/channels/{cid}/archive',token);capture('channel.archived')
        request('events unarchive','POST',f'/channels/{cid}/unarchive',token);capture('channel.unarchived')
        request('events member leave','POST',f'/channels/{cid}/leave',u['member']['token']);capture('member.left')
        request('events member rejoin','POST',f'/channels/{cid}/join',u['member']['token']);capture('member.joined')
        request('events message delete','DELETE',f'/messages/{message["id"]}',token);capture('message.deleted')
    finally:observer.close()
    replay=WS(token,1)
    try:
        replay.send({'type':'resume','channel_id':cid,'since_event_id':hello['head_event_id']})
        catchup=[]
        for _ in range(50):
            e=replay.receive()
            if e.get('type')=='resumed':
                check('events resumed head',e.get('head_event_id')==seen[-1]['event_id']);break
            if e.get('channel_id')==cid and 'event_id' in e:catchup.append(e)
        else:raise RuntimeError('Missing resumed acknowledgment')
        check('events replay exact sequence across node',[(e['type'],e['event_id']) for e in catchup]==[(e['type'],e['event_id']) for e in seen])
    finally:replay.close()
    # A second independent channel must have a local head, not inherit cid's tail.
    second=request('events second channel','POST',f'/workspaces/{slug}/channels',token,{'name':p+'-second'},status=201)['channel']
    another=WS(token,0)
    try:
        another.send({'type':'subscribe','channel_id':second['id']});start=another.until('subscribed',second['id'])
        request('events independent write','POST',f'/channels/{second["id"]}/messages',token,{'body':'independent'},status=201,node=2)
        event=another.until('message.created',second['id'])
        check('events independent channel sequence',event.get('event_id')==start['head_event_id']+1 and start['head_event_id']<seen[-1]['event_id'])
    finally:another.close()


def irc():
    p,u,slug,ws,c=fixture();token=u['owner']['token'];cid=c['id']
    s=socket.create_connection(('127.0.0.1',6667),timeout=1);s.settimeout(1.5)
    f=s.makefile('rb');w=WS(token,2)
    def send(line):s.sendall((line+'\r\n').encode())
    def until(fragment):
        lines=[]
        for _ in range(60):
            line=f.readline().decode().strip()
            if not line:raise RuntimeError('IRC disconnected')
            if line.startswith('PING '):send('PONG '+line[5:]);continue
            lines.append(line)
            if fragment in line:return lines
        raise RuntimeError('Missing IRC response '+fragment)
    try:
        w.send({'type':'subscribe','channel_id':cid});head=w.until('subscribed',cid)['head_event_id']
        send('PASS '+token);send('NICK '+u['owner']['name']);send('USER '+u['owner']['name']+' 0 * :Test user')
        lines=until(' 422 ')
        check('IRC registration numeric sequence',all(any(' '+n+' ' in line for line in lines) for n in ['001','002','003','004','005']))
        name='#'+slug+'/'+c['name'];send('JOIN '+name);joined=until(' 366 ')
        check('IRC join and names',any(' JOIN ' in x for x in joined) and any(' 353 ' in x for x in joined))
        # Joining an already joined owner should not create a spurious durable event.
        send('PRIVMSG '+name+' :from-irc-'+p)
        e=w.until('message.created',cid)
        check('IRC emits shared event',e.get('event_id')==head+1)
        listing=request('IRC persisted history','GET',f'/channels/{cid}/messages',token)
        check('IRC text in REST history',any(m.get('body')=='from-irc-'+p for m in listing.get('messages',[])))
        request('IRC web writer','POST',f'/channels/{cid}/messages',token,{'body':'from-web-'+p},status=201,node=1)
        e2=w.until('message.created',cid)
        check('IRC mixed protocol dense sequence',e2.get('event_id')==e['event_id']+1)
        lines=until('PRIVMSG '+name+' :from-web-'+p)
        check('IRC receives web message',bool(lines))
        send('PING :probe-'+p);until('probe-'+p);check('IRC ping pong',True)
        send('QUIT :done')
    finally:
        f.close();s.close();w.close()


def main():
    groups={'channels':channels,'paging':paging,'mentions':mentions,'events':events,'irc':irc}
    parser=argparse.ArgumentParser();parser.add_argument('--group',choices=groups);args=parser.parse_args()
    def deadline(signum,frame):raise TimeoutError('25-second deadline; run each group separately')
    signal.signal(signal.SIGALRM,deadline);signal.alarm(25)
    try:
        for name,fn in groups.items():
            if args.group and args.group != name:continue
            try:fn()
            except TimeoutError:raise
            except Exception as e:check('B '+name+' could not finish',False,type(e).__name__+': '+str(e))
    except TimeoutError as e:check('B deadline',False,str(e))
    finally:signal.alarm(0)
    print(f'RESULT: {a.passed} passed, {a.failed} failed',flush=True)
    return 1 if a.failed else 0


if __name__=='__main__':sys.exit(main())
