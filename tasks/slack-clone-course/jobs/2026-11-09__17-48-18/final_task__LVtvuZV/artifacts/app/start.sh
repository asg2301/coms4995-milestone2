#!/bin/sh
set -eu
python3 /app/app.py >/app/node0.log 2>&1 & echo $! >/app/node0.pid
PORT=8001 python3 /app/app.py >/app/node1.log 2>&1 & echo $! >/app/node1.pid
PORT=8002 python3 /app/app.py >/app/node2.log 2>&1 & echo $! >/app/node2.pid
trap 'kill "$(cat /app/node0.pid)" "$(cat /app/node1.pid)" "$(cat /app/node2.pid)" 2>/dev/null || true' INT TERM EXIT
wait
