#!/bin/sh
set -eu
children=""
stop() { for p in $children; do kill "$p" 2>/dev/null || true; done; }
trap stop EXIT INT TERM
supervise_http() {
  port="$1"
  while :; do
    PORT="$port" python3 /app/app.py >/tmp/huddle-"$port".log 2>&1 || true
    sleep 1
  done
}
supervise_redis() {
  while :; do
    if command -v redis-server >/dev/null 2>&1; then
      redis-server --bind 127.0.0.1 --port 6379 --save "" --appendonly no >/tmp/huddle-redis.log 2>&1 || true
    else
      sleep 2
    fi
  done
}
supervise_http 8000 & children="$children $!"
supervise_http 8001 & children="$children $!"
supervise_http 8002 & children="$children $!"
supervise_redis & children="$children $!"
wait
