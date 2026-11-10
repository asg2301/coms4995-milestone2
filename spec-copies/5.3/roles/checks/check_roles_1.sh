#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
status=0
python3 -B check_baseline.py || status=1
python3 -B run_stage.py 1 || status=1
exit "$status"
