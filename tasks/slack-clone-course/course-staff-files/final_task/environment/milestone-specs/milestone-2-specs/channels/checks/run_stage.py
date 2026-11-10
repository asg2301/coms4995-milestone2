#!/usr/bin/env python3
"""Ordered B stage gates, sharing unchanged single-file assertion functions."""
import signal
import sys
import check_channels as b

stages = {'1': [('channels', b.channels)],
          '2': [('paging', b.paging)],
          '3': [('mentions', b.mentions)],
          '4': [('events', b.events), ('irc', b.irc)]}


def deadline(signum, frame):
    raise TimeoutError('25-second stage deadline reached')


if len(sys.argv) != 2 or sys.argv[1] not in stages:
    raise SystemExit('Usage: run_stage.py 1|2|3|4')
signal.signal(signal.SIGALRM, deadline)
signal.alarm(25)
try:
    for name, fn in stages[sys.argv[1]]:
        try:
            fn()
        except TimeoutError:
            raise
        except Exception as e:
            b.check('stage B '+name+' could not finish', False, type(e).__name__+': '+str(e))
except TimeoutError as e:
    b.check('stage B deadline', False, str(e))
finally:
    signal.alarm(0)
print(f'RESULT: {b.a.passed} passed, {b.a.failed} failed', flush=True)
sys.exit(1 if b.a.failed else 0)
