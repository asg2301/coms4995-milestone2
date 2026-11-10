#!/bin/sh
set -eu
cleanup() {
  for p in "${pid8000:-}" "${pid8001:-}" "${pid8002:-}" "${pidirc:-}" "${pidredis:-}"; do
    [ -n "$p" ] && kill "$p" 2>/dev/null || true
  done
}
trap cleanup INT TERM EXIT
start_http() {
  port="$1"
  python3 -u /app/server.py --port "$port" >"/tmp/huddle-$port.log" 2>&1 &
  eval "pid$port=$!"
}
start_irc() {
  python3 -u /app/server.py --irc >/tmp/huddle-irc.log 2>&1 &
  pidirc=$!
}
start_redis() {
  redis-server --bind 127.0.0.1 --port 6379 --save "" --appendonly no >/tmp/huddle-redis.log 2>&1 &
  pidredis=$!
}
start_redis
start_http 8000
start_http 8001
start_http 8002
start_irc
while :; do
  sleep 1
  for port in 8000 8001 8002; do
    eval "pid=\${pid$port}"
    if ! kill -0 "$pid" 2>/dev/null; then
      start_http "$port"
    fi
  done
  if ! kill -0 "$pidirc" 2>/dev/null; then start_irc; fi
  if ! kill -0 "$pidredis" 2>/dev/null; then start_redis; fi
done
