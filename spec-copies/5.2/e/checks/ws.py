import base64, hashlib, json, os, socket, struct
from urllib.parse import urlencode

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
