#!/bin/sh
set -eu
mkdir -p /app
rm -f /app/.pids
python3 -u /app/app.py >/app/node0.log 2>&1 & echo $! >> /app/.pids
PORT=8001 python3 -u /app/app.py >/app/node1.log 2>&1 & echo $! >> /app/.pids
PORT=8002 python3 -u /app/app.py >/app/node2.log 2>&1 & echo $! >> /app/.pids
trap 'kill $(cat /app/.pids) 2>/dev/null || true; wait' INT TERM EXIT
wait
