#!/bin/sh
set -e
mkdir -p /tmp
rm -f /tmp/huddle_state.json
python3 /app/app.py 8000 &
p1=$!
python3 /app/app.py 8001 &
p2=$!
python3 /app/app.py 8002 &
p3=$!
python3 -c 'import sys;sys.path.insert(0,"/app");import app;app.irc_server()' &
p4=$!
trap 'kill $p1 $p2 $p3 $p4 2>/dev/null || true' TERM INT EXIT
wait
