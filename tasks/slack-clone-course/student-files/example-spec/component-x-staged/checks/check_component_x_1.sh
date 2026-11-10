#!/usr/bin/env bash
# Self-check for X.1. Prints PASS/FAIL per rule in /spec/component-x-staged/component-x-1.md.
# Usage: bash /spec/component-x-staged/checks/check_component_x_1.sh      Exit 0 only if every rule passes.
set -u
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

rules_component_x_1() {
  local s
  # Replace these two rules with the rules in component-x-1.md.
  # new_user gives you a fresh logged-in user ($USER, $TOKEN).
  new_user || { for r in 1.1 1.2; do bad $r "setup: could not register a user (run regression.sh)"; done; return; }

  # s=$(call GET $N0/api/<route> "" "$TOKEN")
  # expect 1.1 "<rule name>" <status> "$s"
  bad 1.1 "rule 1.1 is not written yet"

  # s=$(call POST $N0/api/<route> '{"<field>":"<bad value>"}' "$TOKEN")
  # expect 1.2 "<rule name>" <status> "$s"
  bad 1.2 "rule 1.2 is not written yet"
}

boot; rules_component_x_1; finish
