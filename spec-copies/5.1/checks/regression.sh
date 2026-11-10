#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
FEEDBACK=verbose
export FEEDBACK
exec python3 -B regression.py
