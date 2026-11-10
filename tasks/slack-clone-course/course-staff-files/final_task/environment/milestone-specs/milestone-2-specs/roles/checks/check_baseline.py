#!/usr/bin/env python3
"""Health + unauthenticated WS handshakes. Crash experiment is separate."""
import base64
import json
import os
import socket
import sys
from pathlib import Path
from urllib.request import urlopen

passed = failed = 0


def check(label, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print('PASS '+label)
    else:
        failed += 1
        print('FAIL '+label+' '+str(detail)[:180])


check('baseline start.sh is executable', os.access('/app/start.sh', os.X_OK))

for node in range(3):
    try:
        with urlopen(f'http://127.0.0.1:{8000+node}/api/health', timeout=1) as r:
            b = json.load(r)
            check(f'baseline node {node} health', r.status == 200 and b.get('status') == 'ok' and b.get('node_id') == node, b)
    except Exception as e:
        check(f'baseline node {node} health', False, e)

for node in range(3):
    for query in ['?token=invalid-token-not-issued', '']:
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            req = (f'GET /api/ws{query} HTTP/1.1\r\nHost: 127.0.0.1:{8000+node}\r\n'
                   f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                   'Sec-WebSocket-Version: 13\r\n\r\n')
            with socket.create_connection(('127.0.0.1',8000+node),timeout=1) as s:
                s.sendall(req.encode())
                response = s.recv(4096)
            first = response.split(b'\r\n',1)[0].split()
            # Require a rejection response; silence, timeout, or connection refusal
            # must not accidentally count as proof of authentication enforcement.
            status = int(first[1]) if len(first)>1 else 0
            check(f'baseline node {node} WS {"bad" if query else "missing"} token', status in (400,401,403), response[:100])
        except Exception as e:
            check(f'baseline node {node} WS token rejection', False, e)

print(f'RESULT: {passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
