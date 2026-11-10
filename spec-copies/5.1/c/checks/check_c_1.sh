#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
source ./lib.sh
export FEEDBACK
exec python3 -B check_c.py 1
