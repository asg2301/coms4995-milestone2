#!/usr/bin/env bash
# Self-check for Auth.1 (register). Prints PASS/FAIL per rule in /spec/auth-staged/auth-1-register.md.
# Usage: bash /spec/auth-staged/checks/check_auth_1.sh      Exit 0 only if every rule passes.
set -u
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

rules_auth_1() {
  local u="chk$(date +%s)$RANDOM" s t n
  s=$(call POST $N0/api/auth/register "{\"username\":\"$u\",\"password\":\"$PW\"}")
  t=$(field token); n=$(field user.username)
  if [ "$s" = 201 ] && [ "${#t}" -ge 16 ] && [ "$n" = "$u" ]; then
    ok 1.1 "register returns 201 with user and token"
  else
    bad 1.1 "register: expected 201 with user.username and a token of 16+ chars, got status $s, token length ${#t}"
  fi

  s=$(call POST $N0/api/auth/register "{\"username\":\"$u\",\"password\":\"$PW\"}")
  expect 1.2 "duplicate username" 409 "$s"

  s=$(call POST $N0/api/auth/register "{\"username\":\"bad name $RANDOM\",\"password\":\"$PW\"}")
  expect 1.3 "username with a space" 400 "$s"

  s=$(call POST $N0/api/auth/register "{\"username\":\"${u}b\",\"password\":\"short77\"}")
  expect 1.4 "7 character password" 400 "$s"
}

[ "${CHECK_ALL:-}" = 1 ] || { boot; rules_auth_1; finish; }
