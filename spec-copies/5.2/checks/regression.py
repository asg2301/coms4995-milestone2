"""Run the same A/B assertion groups through one regression entry point."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
groups = {
    "A.auth": ("check_roles", "auth"),
    "A.workspaces": ("check_roles", "workspaces"),
    "A.roles": ("check_roles", "roles"),
    "A.invitations": ("check_roles", "invitations"),
    "A.join_mode": ("check_roles", "join_mode"),
    "B.channels": ("check_channels", "channels"),
    "B.paging": ("check_channels", "paging"),
    "B.mentions": ("check_channels", "mentions"),
    "B.events": ("check_channels", "events"),
    "B.irc": ("check_channels", "irc"),
}
worker = """
import importlib, sys
mod = importlib.import_module(sys.argv[1])
try:
    getattr(mod, sys.argv[2])()
except Exception as exc:
    mod.check('group did not finish', False, f'{type(exc).__name__}: {exc}')
print('REGRESSION_GROUP_COMPLETE')
"""


def run(label):
    cmd = ([sys.executable, "-B", "check_baseline.py"] if label == "boot" else
           [sys.executable, "-B", "-c", worker, *groups[label]])
    try:
        result = subprocess.run(cmd, cwd=here, text=True, capture_output=True, timeout=22)
    except subprocess.TimeoutExpired:
        return [(label, "check group exceeded its time limit")]
    lines = result.stdout.splitlines()
    failed = [(label, row[5:]) for row in lines if row.startswith("FAIL ")]
    complete = any(line.startswith("RESULT: ") for line in lines) if label == "boot" else "REGRESSION_GROUP_COMPLETE" in lines
    if not complete or (result.returncode and not failed):
        detail = result.stderr.strip().splitlines()
        failed.append((label, detail[-1][:200] if detail else "check group exited before completion"))
    return failed


def main():
    with ThreadPoolExecutor(max_workers=11) as pool:
        failures = [failure for results in pool.map(run, ("boot", *groups)) for failure in results]
    for label, message in failures:
        print(f"REGRESSION FAIL {label}: {message}")
    print(f"REGRESSION: {len(failures)} broken")
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
