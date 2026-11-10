#!/usr/bin/env bash
# Regression suite for auth: reruns all the auth rules and prints only what broke.
# Usage: bash /spec/component-x-staged/checks/regression.sh      Exit 0 only if nothing broke.
#
# In your Runs 5.1 and 5.2 spec, a suite like this would hold your A and B rules instead.
set -u

# Testing mode: verbose prints a REGRESSION FAIL line per broken rule, score_only prints only the total.
FEEDBACK=verbose   # or: score_only

N0=http://127.0.0.1:8000; N1=http://127.0.0.1:8001; N2=http://127.0.0.1:8002
BODY=/tmp/regression_body.json
PW="correct-horse-1"
broken=0

# Passing rules print nothing. In verbose mode, each failure is labelled with its component.
ok()  { :; }
bad() { [ "$FEEDBACK" = verbose ] && echo "REGRESSION FAIL auth $1 $2"; broken=$((broken+1)); }
# expect <rule> <description> <expected-status> <actual-status>
expect() { if [ "$4" = "$3" ]; then ok "$1"; else bad "$1" "$2: expected $3, got $4"; fi; }

# call <METHOD> <URL> [json-body] [token]  -> prints the HTTP status, saves the body in $BODY
call() {
  local args=(-s -o "$BODY" -w '%{http_code}' --max-time 5 -X "$1" "$2")
  [ -n "${3:-}" ] && args+=(-H 'Content-Type: application/json' -d "$3")
  [ -n "${4:-}" ] && args+=(-H "Authorization: Bearer $4")
  curl "${args[@]}" 2>/dev/null || true   # status 000 means no answer within 5s
}
# field <dotted.path>  -> prints that field of the last response body, or nothing
field() {
  python3 -c 'import json,sys
try:
    v=json.load(open(sys.argv[1]))
    for k in sys.argv[2].split("."): v=v[k]
    print(v)
except Exception: pass' "$BODY" "$1"
}
healthy() {
  for u in $N0 $N1 $N2; do curl -fs --max-time 2 "$u/api/health" >/dev/null 2>&1 || return 1; done
}
# Total wait is capped at 20s so the whole suite fits the agent's 30s command limit.
boot() {
  if ! healthy; then
    (cd /app && nohup /app/start.sh >/tmp/start.log 2>&1 &)
    for _ in $(seq 1 20); do healthy && break; sleep 1; done
  fi
  healthy || {
    [ "$FEEDBACK" = verbose ] && echo "REGRESSION FAIL cluster 0 not answering /api/health on 8000, 8001, 8002 (see /tmp/start.log)"
    echo "REGRESSION: 1 broken"; exit 1
  }
}

boot

# Auth.1: register
u="chk$(date +%s)$RANDOM"
s=$(call POST $N0/api/auth/register "{\"username\":\"$u\",\"password\":\"$PW\"}")
t=$(field token); n=$(field user.username)
if [ "$s" = 201 ] && [ "${#t}" -ge 16 ] && [ "$n" = "$u" ]; then ok 1.1
else bad 1.1 "register: expected 201 with user.username and a token of 16+ chars, got status $s, token length ${#t}"; fi

s=$(call POST $N0/api/auth/register "{\"username\":\"$u\",\"password\":\"$PW\"}")
expect 1.2 "duplicate username" 409 "$s"

s=$(call POST $N0/api/auth/register "{\"username\":\"bad name $RANDOM\",\"password\":\"$PW\"}")
expect 1.3 "username with a space" 400 "$s"

s=$(call POST $N0/api/auth/register "{\"username\":\"${u}b\",\"password\":\"short77\"}")
expect 1.4 "7 character password" 400 "$s"

# Auth.2: login (reuses the user registered above)
s=$(call POST $N0/api/auth/login "{\"username\":\"$u\",\"password\":\"$PW\"}")
if [ "$s" = 200 ] && [ -n "$(field token)" ]; then ok 2.1
else bad 2.1 "login: expected 200 with a token, got status $s"; fi

s=$(call POST $N0/api/auth/login "{\"username\":\"$u\",\"password\":\"WRONG12345\"}")
expect 2.2 "login with wrong password" 401 "$s"

# Auth.3: me and tokens on every node
if [ -z "$t" ]; then
  for r in 3.1 3.2 3.3; do bad $r "setup: register gave no token (see 1.1)"; done
else
  s=$(call GET $N0/api/auth/me "" "$t")
  if [ "$s" = 200 ] && [ "$(field user.username)" = "$u" ]; then ok 3.1
  else bad 3.1 "me with token: expected 200 and username $u, got status $s"; fi

  s1=$(call GET $N0/api/auth/me); s2=$(call GET $N0/api/auth/me "" "not-a-real-token-000000")
  if [ "$s1" = 401 ] && [ "$s2" = 401 ]; then ok 3.2
  else bad 3.2 "me: expected 401 with no token and with a made-up token, got $s1 and $s2"; fi

  a=$(call POST $N2/api/auth/login "{\"username\":\"$u\",\"password\":\"$PW\"}")
  b=$(call GET $N1/api/auth/me "" "$t"); c=$(call GET $N2/api/auth/me "" "$t")
  if [ "$a" = 200 ] && [ "$b" = 200 ] && [ "$c" = 200 ]; then ok 3.3
  else bad 3.3 "cross-node: login on 8002 got $a, me on 8001 got $b, me on 8002 got $c (expected 200 each)"; fi
fi

echo "REGRESSION: $broken broken"
[ "$broken" -eq 0 ]
