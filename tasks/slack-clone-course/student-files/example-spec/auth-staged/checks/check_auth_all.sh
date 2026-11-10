#!/usr/bin/env bash
# Runs the Auth.1, Auth.2 and Auth.3 checks in order and prints one total.
# Usage: bash /spec/auth-staged/checks/check_auth_all.sh      Exit 0 only if all nine rules pass.
set -u
CHECK_ALL=1
D="$(dirname "${BASH_SOURCE[0]}")"
. "$D/check_auth_1.sh"; . "$D/check_auth_2.sh"; . "$D/check_auth_3.sh"
pass=0; fail=0
boot; rules_auth_1; rules_auth_2; rules_auth_3; finish
