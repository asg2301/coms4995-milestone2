#!/usr/bin/env bash
# Self-check for Auth.2 (login). Prints PASS/FAIL per rule in /spec/auth-staged/auth-2-login.md.
# Usage: bash /spec/auth-staged/checks/check_auth_2.sh      Exit 0 only if every rule passes.
set -u
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

rules_auth_2() {
  local s t
  # setup from Auth.1: a registered user
  new_user || { for r in 2.1 2.2; do bad $r "setup: could not register a user, fix Auth.1 first"; done; return; }
  s=$(call POST $N0/api/auth/login "{\"username\":\"$USER\",\"password\":\"$PW\"}")
  t=$(field token)
  if [ "$s" = 200 ] && [ -n "$t" ]; then ok 2.1 "login returns 200 with user and token"
  else bad 2.1 "login: expected 200 with a token, got status $s"; fi

  s=$(call POST $N0/api/auth/login "{\"username\":\"$USER\",\"password\":\"WRONG12345\"}")
  expect 2.2 "login with wrong password" 401 "$s"
}

[ "${CHECK_ALL:-}" = 1 ] || { boot; rules_auth_2; finish; }
