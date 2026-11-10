#!/usr/bin/env bash
# Self-check for Auth.3 (sessions). Prints PASS/FAIL per rule in /spec/auth-staged/auth-3-sessions.md.
# Usage: bash /spec/auth-staged/checks/check_auth_3.sh      Exit 0 only if every rule passes.
set -u
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

rules_auth_3() {
  local s s1 s2 a b c
  # setup from Auth.1: a registered user and its token
  new_user || { for r in 3.1 3.2 3.3; do bad $r "setup: could not register a user, fix Auth.1 first"; done; return; }
  s=$(call GET $N0/api/auth/me "" "$TOKEN")
  if [ "$s" = 200 ] && [ "$(field user.username)" = "$USER" ]; then ok 3.1 "me returns 200 and the same username"
  else bad 3.1 "me with token: expected 200 and username $USER, got status $s"; fi

  s1=$(call GET $N0/api/auth/me); s2=$(call GET $N0/api/auth/me "" "not-a-real-token-000000")
  if [ "$s1" = 401 ] && [ "$s2" = 401 ]; then ok 3.2 "me without a valid token returns 401"
  else bad 3.2 "me: expected 401 with no token and with a made-up token, got $s1 and $s2"; fi

  a=$(call POST $N2/api/auth/login "{\"username\":\"$USER\",\"password\":\"$PW\"}")
  b=$(call GET $N1/api/auth/me "" "$TOKEN"); c=$(call GET $N2/api/auth/me "" "$TOKEN")
  if [ "$a" = 200 ] && [ "$b" = 200 ] && [ "$c" = 200 ]; then ok 3.3 "account and token work on all three nodes"
  else bad 3.3 "cross-node: login on 8002 got $a, me on 8001 got $b, me on 8002 got $c (expected 200 each)"; fi
}

[ "${CHECK_ALL:-}" = 1 ] || { boot; rules_auth_3; finish; }
