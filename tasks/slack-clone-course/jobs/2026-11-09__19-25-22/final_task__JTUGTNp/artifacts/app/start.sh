#!/bin/sh
set -eu
mkdir -p /app
run_node(){ n="$1"; while :; do NODE_ID="$n" python3 /app/app.py || true; sleep 1; done; }
run_irc(){ while :; do IRC_ONLY=1 python3 /app/app.py || true; sleep 1; done; }
run_redis(){ while :; do redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no --daemonize no || true; sleep 1; done; }
run_node 0 & p0=$!
run_node 1 & p1=$!
run_node 2 & p2=$!
run_irc & pi=$!
run_redis & pr=$!
trap 'kill $p0 $p1 $p2 $pi $pr 2>/dev/null || true' INT TERM EXIT
wait
