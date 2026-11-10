#!/bin/bash
set -u
mkdir -p /app/data
while :; do
  if ! ss -ltnp 'sport = :6379' | grep -q redis-server; then redis-server --bind 127.0.0.1 --port 6379 --daemonize yes >/dev/null 2>&1 || true; fi
  for n in 0 1 2; do
    if ! ss -ltn "sport = :$((8000+n))" | grep -q LISTEN; then python3 /app/server.py "$n" >/tmp/huddle-$n.log 2>&1 & echo $! >/tmp/huddle-$n.pid; fi
  done
  if ! ss -ltn 'sport = :6667' | grep -q LISTEN; then python3 /app/server.py irc >/tmp/huddle-irc.log 2>&1 & echo $! >/tmp/huddle-irc.pid; fi
  sleep 1
done
