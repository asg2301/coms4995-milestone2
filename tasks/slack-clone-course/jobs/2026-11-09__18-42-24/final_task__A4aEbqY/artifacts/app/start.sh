#!/bin/sh
cd /app
python3 -u app.py >/tmp/huddle0.log 2>&1 & echo $! >/tmp/huddle0.pid
PORT=8001 python3 -u app.py >/tmp/huddle1.log 2>&1 & echo $! >/tmp/huddle1.pid
PORT=8002 python3 -u app.py >/tmp/huddle2.log 2>&1 & echo $! >/tmp/huddle2.pid
python3 -u -c 'import sys;sys.path.insert(0,"/app");import app;app.irc_server()'
