#!/bin/sh
set -eu
mkdir -p /app
if ! mkdir /tmp/huddle-supervisor.lock 2>/dev/null; then exit 0; fi
trap 'rmdir /tmp/huddle-supervisor.lock 2>/dev/null || true' EXIT

run_node() {
  n="$1"
  while :; do
    NODE_ID="$n" python3 /app/app.py || true
    sleep .2
  done
}
run_irc() {
  while :; do
    python3 /app/irc.py || true
    sleep .2
  done
}
run_redis() {
  while :; do
    redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no || true
    sleep .2
  done
}
run_redis >/app/redis.log 2>&1 &
echo $! >/app/redis-supervisor.pid
run_irc >/app/irc.log 2>&1 &
echo $! >/app/irc-supervisor.pid
for n in 0 1 2; do
  run_node "$n" >/app/node$n.log 2>&1 &
  echo $! >/app/node$n-supervisor.pid
done
wait
