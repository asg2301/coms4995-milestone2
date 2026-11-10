# Read this first: work order and rules for this run

This run is about one component: auth (register, login, `me`). It is written as three specs
that build on each other. Before you run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`, you
MUST have run the full check below and seen **all nine** rules pass. Do not submit unless
every one of them passes.

The task description you were given is still the grading contract. The files in `/spec` do
not change it; they tell you what to work on now and how to prove it works.

## Files in /spec/auth-staged

| File | What it is | Builds on |
|---|---|---|
| `README.md` | This file: work order, how to verify, when you may submit | |
| `auth-1-register.md` | Auth.1: register (rules 1.1 to 1.4) | nothing |
| `auth-2-login.md` | Auth.2: login (rules 2.1 and 2.2) | Auth.1 |
| `auth-3-sessions.md` | Auth.3: `me` and tokens on every node (rules 3.1 to 3.3) | Auth.1, Auth.2 |
| `checks/check_auth_1.sh`, `_2.sh`, `_3.sh` | Print PASS or FAIL for each rule of one spec | |
| `checks/check_auth_all.sh` | Runs the three checks in order and prints one total | |

Each spec has the same three parts: **Uses** (functions from earlier specs that you must
call, not rewrite), **Adds** (functions this spec introduces, with the names to use), and
**Rules** (what must be true over HTTP).

## Scope

Work only on the routes in the three spec files. Do NOT start on messages, files, IRC, or
the UI in this run. If `/app` is empty, first build the smallest cluster that answers
`GET /api/health` on ports `8000`, `8001`, and `8002`, then add auth. If `/app` already has
code, improve it, do not start over.

## Order of work

1. Read `/spec/auth-staged/auth-1-register.md`. Build it. Run `check_auth_1.sh` until all
   four rules pass.
2. Read `/spec/auth-staged/auth-2-login.md`. Build it using the functions from Auth.1. Run
   `check_auth_2.sh` until both rules pass.
3. Read `/spec/auth-staged/auth-3-sessions.md`. Build it using the functions from Auth.1 and
   Auth.2. Run `check_auth_3.sh` until all three rules pass.
4. After each step, run `check_auth_all.sh`. If a rule from an earlier spec starts failing,
   undo your last change before doing anything else.

Do not start a spec while a rule from an earlier spec is failing.

## How to verify

Run one of these as a single command:

```bash
bash /spec/auth-staged/checks/check_auth_1.sh
bash /spec/auth-staged/checks/check_auth_2.sh
bash /spec/auth-staged/checks/check_auth_3.sh
bash /spec/auth-staged/checks/check_auth_all.sh
```

Each check prints its test results.

- A check starts `/app/start.sh` itself if the cluster is not answering, and finishes in
  under 30 seconds.
- After you change code, restart the cluster before checking again: find the process that
  owns each port and stop it by PID. Never stop processes by name.
- Do not edit any file in `/spec`, including the checks. If a check fails, fix the code, not
  the check.

## Before you submit

You may submit only when both of these are true, from output you actually saw:

- the last line of `check_auth_all.sh` is `RESULT: 9 passed, 0 failed`;
- `GET /api/health` returns `200` on ports `8000`, `8001`, and `8002`.

These nine rules are necessary, not sufficient. The full task in the grading contract still
applies.
