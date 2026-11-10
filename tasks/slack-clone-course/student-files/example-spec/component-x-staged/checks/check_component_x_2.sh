#!/usr/bin/env bash
# Self-check for X.2. Prints PASS/FAIL per rule in /spec/component-x-staged/component-x-2.md.
# Usage: bash /spec/component-x-staged/checks/check_component_x_2.sh      Exit 0 only if every rule passes.
set -u
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

rules_component_x_2() {
  local s
  # Replace these two rules with the rules in component-x-2.md.
  # new_user gives you a fresh logged-in user ($USER, $TOKEN).
  new_user || { for r in 2.1 2.2; do bad $r "setup: could not register a user (run regression.sh)"; done; return; }

  # s=$(call GET $N0/api/<route> "" "$TOKEN")
  # expect 2.1 "<rule name>" <status> "$s"
  bad 2.1 "rule 2.1 is not written yet"

  # s=$(call POST $N0/api/<route> '{"<field>":"<bad value>"}' "$TOKEN")
  # expect 2.2 "<rule name>" <status> "$s"
  bad 2.2 "rule 2.2 is not written yet"
}

boot; rules_component_x_2; finish
