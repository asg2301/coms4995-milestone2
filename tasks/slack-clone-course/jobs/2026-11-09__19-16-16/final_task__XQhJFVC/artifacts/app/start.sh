#!/bin/sh
set -u
touch /tmp/huddle_state.lock
[ -f /tmp/huddle_state.json ] || echo '{"users":{},"tokens":{},"workspaces":{},"channels":{},"messages":{},"files":{},"dms":{},"groups":{},"events":{}}' > /tmp/huddle_state.json
redis_running() { redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; }
if redis_running; then pr=0; else redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/tmp/huddle-redis.log 2>&1 & pr=$!; fi
PORT=8000 NODE_ID=0 python3 -u /app/app.py >/tmp/huddle0.log 2>&1 & p0=$!
PORT=8001 NODE_ID=1 python3 -u /app/app.py >/tmp/huddle1.log 2>&1 & p1=$!
PORT=8002 NODE_ID=2 python3 -u /app/app.py >/tmp/huddle2.log 2>&1 & p2=$!
IRC=1 python3 -u /app/app.py >/tmp/huddleirc.log 2>&1 & pi=$!
trap 'for p in "$p0" "$p1" "$p2" "$pi"; do [ "$p" -gt 0 ] 2>/dev/null && kill "$p" 2>/dev/null || true; done; [ "$pr" -gt 0 ] 2>/dev/null && kill "$pr" 2>/dev/null || true' INT TERM EXIT
while :; do
  if ! kill -0 "$p0" 2>/dev/null; then PORT=8000 NODE_ID=0 python3 -u /app/app.py >/tmp/huddle0.log 2>&1 & p0=$!; fi
  if ! kill -0 "$p1" 2>/dev/null; then PORT=8001 NODE_ID=1 python3 -u /app/app.py >/tmp/huddle1.log 2>&1 & p1=$!; fi
  if ! kill -0 "$p2" 2>/dev/null; then PORT=8002 NODE_ID=2 python3 -u /app/app.py >/tmp/huddle2.log 2>&1 & p2=$!; fi
  if ! kill -0 "$pi" 2>/dev/null; then IRC=1 python3 -u /app/app.py >/tmp/huddleirc.log 2>&1 & pi=$!; fi
  if ! redis_running; then redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/tmp/huddle-redis.log 2>&1 & pr=$!; fi
  sleep 1
done
