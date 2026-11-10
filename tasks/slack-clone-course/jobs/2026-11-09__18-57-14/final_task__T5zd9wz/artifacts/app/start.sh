#!/bin/sh
set -eu
mkdir -p /tmp
pids=""
cleanup() {
  for p in $pids; do kill "$p" 2>/dev/null || true; done
  exit 0
}
trap cleanup INT TERM EXIT
if ! ss -ltn 2>/dev/null | grep -q ':6379 '; then
  redis-server --bind 127.0.0.1 --port 6379 --daemonize yes --pidfile /tmp/huddle-redis.pid >/tmp/huddle-redis.log 2>&1 || true
fi
start_service() {
  name=$1
  shift
  "$@" >"/tmp/huddle-$name.log" 2>&1 &
  pid=$!
  eval "pid_$name=$pid"
  pids="$pids $pid"
}
start_service irc env IRC=1 python3 /app/server.py
start_service 0 env PORT=8000 NODE_ID=0 python3 /app/server.py
start_service 1 env PORT=8001 NODE_ID=1 python3 /app/server.py
start_service 2 env PORT=8002 NODE_ID=2 python3 /app/server.py
while :; do
  sleep 1
  if ! ss -ltn 2>/dev/null | grep -q ':6379 '; then
    redis-server --bind 127.0.0.1 --port 6379 --daemonize yes --pidfile /tmp/huddle-redis.pid >/tmp/huddle-redis.log 2>&1 || true
  fi
  for name in irc 0 1 2; do
    eval "pid=\${pid_$name}"
    if ! kill -0 "$pid" 2>/dev/null; then
      start_service "$name" env $( [ "$name" = irc ] && echo IRC=1 || echo PORT=$((8000+name)) NODE_ID=$name ) python3 /app/server.py
    fi
  done
done
