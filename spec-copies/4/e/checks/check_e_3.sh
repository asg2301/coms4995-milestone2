#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [ -x /opt/test-venv/bin/python ]; then
  exec /opt/test-venv/bin/python -B check_e.py 3
fi
exec python3 -B check_e.py 3
