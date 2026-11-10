#!/bin/sh
mkdir -p /app/data
redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/app/data/redis.log 2>&1 &
rp=$!
p0= p1= p2=
trap 'kill $rp ${p0:-} ${p1:-} ${p2:-} 2>/dev/null || true; exit 0' INT TERM EXIT
while :; do
  if [ ! -r "/proc/$rp/stat" ] || [ "$(awk '{print $3}' "/proc/$rp/stat" 2>/dev/null)" = "Z" ]; then
    redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/app/data/redis.log 2>&1 &
    rp=$!
  fi
  for n in 0 1 2; do
    eval "p=\${p$n:-}"
    if [ -z "$p" ] || [ ! -r "/proc/$p/stat" ] || [ "$(awk '{print $3}' "/proc/$p/stat" 2>/dev/null)" = "Z" ]; then
      NODE=$n python3 /app/server.py $((8000+n)) >>"/app/data/node$n.log" 2>&1 &
      eval "p$n=$!"
    fi
  done
  sleep .2
done
