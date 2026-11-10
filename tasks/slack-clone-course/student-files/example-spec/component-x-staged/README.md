# Read this first: work order and rules for this run

<!-- Example only. Component X stands for any new component: replace the component X files
and steps below with your own spec files and checks. -->

This run builds component X in two steps that build on each other. Before you run
`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`, you MUST have seen every component X check pass
and the regression suite report **nothing broken**. Do not submit unless both are true.

The task description you were given is still the grading contract. The files in `/spec` do
not change it; they tell you what to work on now and how to prove it works.

## Files in /spec/component-x-staged

| File | What it is | Builds on |
|---|---|---|
| `README.md` | This file: work order, how to verify, when you may submit | |
| `component-x-1.md` | X.1: first step (rules 1.1 and 1.2) | the regression suite |
| `component-x-2.md` | X.2: second step (rules 2.1 and 2.2) | X.1 |
| `checks/check_component_x_1.sh`, `_2.sh` | Print PASS or FAIL for each rule of one step | |
| `checks/lib.sh` | Shared helpers for the component X checks | |
| `checks/regression.sh` | The regression suite: features that must keep working. Prints only what broke | |

## Scope

Build only what the regression suite and the two component X specs cover. If `/app` is empty,
first build the smallest cluster that answers `GET /api/health` on ports `8000`, `8001`, and
`8002`. If `/app` already has code, improve it, do not start over.

## Order of work

1. Run `regression.sh`. Build what the task description asks for until it reports
   `REGRESSION: 0 broken`.
2. Read `/spec/component-x-staged/component-x-1.md`. Build it. Run `check_component_x_1.sh`
   until both rules pass.
3. Read `/spec/component-x-staged/component-x-2.md`. Build it using the functions from X.1.
   Run `check_component_x_2.sh` until both rules pass.
4. After every step, run `regression.sh`. If it reports anything broken, fix it before doing
   anything else, even if the check for the step you are on passes.

Do not start a new step while the regression suite reports something broken.

## How to verify

Run one of these as a single command:

```bash
bash /spec/component-x-staged/checks/regression.sh
bash /spec/component-x-staged/checks/check_component_x_1.sh
bash /spec/component-x-staged/checks/check_component_x_2.sh
```

Each check prints its test results. The regression suite prints one line per broken rule,
starting with `REGRESSION FAIL`, then a total such as `REGRESSION: 0 broken`.

- A check starts `/app/start.sh` itself if the cluster is not answering, and finishes in
  under 30 seconds.
- After you change code, restart the cluster before checking again: find the process that
  owns each port and stop it by PID. Never stop processes by name.
- Do not edit any file in `/spec`, including the checks. If a check fails, fix the code, not
  the check.

## Before you submit

You may submit only when all of these are true, from output you actually saw:

- the last line of `regression.sh` is `REGRESSION: 0 broken`;
- every component X check passes;
- `GET /api/health` returns `200` on ports `8000`, `8001`, and `8002`.

These rules are necessary, not sufficient. The full task in the grading contract still
applies.
