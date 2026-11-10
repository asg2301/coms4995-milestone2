#!/bin/sh
set -u
mkdir -p /app
redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/app/redis.log 2>&1 &
r=$!
for n in 0 1 2; do (while :; do python3 /app/server.py "$n"; sleep 1; done) & done
while :; do
 (! kill -0 "$r" 2>/dev/null || [ "$(awk '{print $3}' /proc/$r/stat 2>/dev/null)" = Z ]) && { redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/app/redis.log 2>&1 & r=$!; }
 sleep 1
done
