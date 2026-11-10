#!/bin/sh
set -eu
mkdir -p /app
p0=; p1=; p2=; pr=
spawn() {
  case "$1" in
    0) NODE_ID=0 python3 /app/server.py >/tmp/huddle-node0.log 2>&1 & p0=$! ;;
    1) NODE_ID=1 python3 /app/server.py >/tmp/huddle-node1.log 2>&1 & p1=$! ;;
    2) NODE_ID=2 python3 /app/server.py >/tmp/huddle-node2.log 2>&1 & p2=$! ;;
    r) if command -v redis-server >/dev/null 2>&1; then redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/tmp/huddle-redis.log 2>&1 & pr=$!; fi ;;
  esac
}
cleanup() {
  [ -n "${p0:-}" ] && kill "$p0" 2>/dev/null || true
  [ -n "${p1:-}" ] && kill "$p1" 2>/dev/null || true
  [ -n "${p2:-}" ] && kill "$p2" 2>/dev/null || true
  [ -n "${pr:-}" ] && kill "$pr" 2>/dev/null || true
  exit 0
}
trap cleanup TERM INT
spawn 0; spawn 1; spawn 2; spawn r
while :; do
  kill -0 "$p0" 2>/dev/null || spawn 0
  kill -0 "$p1" 2>/dev/null || spawn 1
  kill -0 "$p2" 2>/dev/null || spawn 2
  if [ -n "${pr:-}" ]; then kill -0 "$pr" 2>/dev/null || spawn r; fi
  sleep 0.2
done
