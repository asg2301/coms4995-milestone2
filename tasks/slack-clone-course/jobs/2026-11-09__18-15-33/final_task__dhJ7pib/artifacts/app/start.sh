#!/bin/bash
set -u
mkdir -p /app/data
while :; do
  redis-server --bind 127.0.0.1 --port 6379 --save "" --appendonly no >/app/data/redis.log 2>&1
  sleep 1
done &
while :; do
  GATEWAY=1 python3 -B /app/irc.py >/app/data/gateway.log 2>&1
  sleep 1
done &
for n in 0 1 2; do
  while :; do
    python3 -B /app/irc.py "$n" >/app/data/node$n.log 2>&1
    sleep 1
  done &
done
wait
