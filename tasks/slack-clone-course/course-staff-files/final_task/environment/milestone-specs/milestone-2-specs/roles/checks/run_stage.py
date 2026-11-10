#!/usr/bin/env python3
"""Stage gates reuse the unchanged single-file black-box assertion functions."""
import signal
import sys
import check_roles as a

stages = {'1': [('auth', a.auth)],
          '2': [('workspaces', a.workspaces), ('roles', a.roles)],
          '3': [('invitations', a.invitations), ('join-mode', a.join_mode)]}


def deadline(signum, frame):
    raise TimeoutError('25-second stage deadline reached')


if len(sys.argv) != 2 or sys.argv[1] not in stages:
    raise SystemExit('Usage: run_stage.py 1|2|3')
signal.signal(signal.SIGALRM, deadline)
signal.alarm(25)
try:
    for name, fn in stages[sys.argv[1]]:
        try:
            fn()
        except TimeoutError:
            raise
        except Exception as e:
            a.check('stage '+name+' could not finish', False, type(e).__name__+': '+str(e))
except TimeoutError as e:
    a.check('stage deadline', False, str(e))
finally:
    signal.alarm(0)
print(f'RESULT: {a.passed} passed, {a.failed} failed', flush=True)
sys.exit(1 if a.failed else 0)
