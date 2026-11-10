#!/bin/sh
set -eu
mkdir -p /app
redis_pid=
if command -v redis-server >/dev/null 2>&1; then
  redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/tmp/huddle-redis.log 2>&1 &
  redis_pid=$!
fi
p0= p1= p2=
cleanup() {
  [ -n "${p0:-}" ] && kill "$p0" 2>/dev/null || true
  [ -n "${p1:-}" ] && kill "$p1" 2>/dev/null || true
  [ -n "${p2:-}" ] && kill "$p2" 2>/dev/null || true
  [ -n "${redis_pid:-}" ] && kill "$redis_pid" 2>/dev/null || true
}
trap cleanup INT TERM EXIT
while :; do
  if [ -z "${p0:-}" ] || ! kill -0 "$p0" 2>/dev/null; then NODE_ID=0 python3 /app/app.py >/tmp/huddle-node0.log 2>&1 & p0=$!; fi
  if [ -z "${p1:-}" ] || ! kill -0 "$p1" 2>/dev/null; then NODE_ID=1 python3 /app/app.py >/tmp/huddle-node1.log 2>&1 & p1=$!; fi
  if [ -z "${p2:-}" ] || ! kill -0 "$p2" 2>/dev/null; then NODE_ID=2 python3 /app/app.py >/tmp/huddle-node2.log 2>&1 & p2=$!; fi
  if command -v redis-server >/dev/null 2>&1 && { [ -z "${redis_pid:-}" ] || ! kill -0 "$redis_pid" 2>/dev/null; }; then
    redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/tmp/huddle-redis.log 2>&1 &
    redis_pid=$!
  fi
  sleep 0.2
done
