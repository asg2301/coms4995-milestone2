#!/bin/sh
set -eu
mkdir -p /app
start_node() {
  n="$1"
  port=$((8000+n))
  if ! kill -0 "$(cat "/app/node${n}.pid" 2>/dev/null)" 2>/dev/null || ! curl -sf --max-time 1 "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
    python3 /app/server.py "$n" >>"/app/node${n}.log" 2>&1 &
    echo $! >"/app/node${n}.pid"
  fi
}
start_redis() {
  if command -v redis-server >/dev/null 2>&1; then
    if ! kill -0 "$(cat /app/redis.pid 2>/dev/null)" 2>/dev/null; then
      redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >>/app/redis.log 2>&1 &
      echo $! >/app/redis.pid
    fi
  fi
}
while :; do
  start_redis
  start_node 0
  start_node 1
  start_node 2
  sleep 1
done
