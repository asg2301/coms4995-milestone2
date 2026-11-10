# Writing specs and checks

How to write the specs and checks for milestone 2. Most of this is the same as in milestone 1.
What is new: the component names and folder trees, a tip on building the two testing modes
into your checks, and the regression suite for A and B (both in [section 5](#5-checks)).

## 1. What a spec is

A spec says what the software must do, in a way that can be checked ("users can upload JPEG or
PNG up to 10MB", not "add photo sharing"). The agent writes the code from your spec. An agent
given a vague request has to guess, and it guesses wrong in ways that look plausible.

## 2. What the agent reads

| Document | Who writes it | What it says |
| --- | --- | --- |
| `instruction.md` | Staff (you cannot change it) | The whole Slack clone: every endpoint and status code. |
| Your spec folder (`/spec`) | You | How to get there in 50 minutes: what to build, in what order, how to test it, when to submit. |

`instruction.md` alone is not enough. It is too big for one run, gives no order, and gives the
agent no way to know whether its code works. Left alone, the agent builds a bit of everything,
tests nothing, and submits.

Your specs do not replace `instruction.md`; the agent still has it. Your specs take the
milestone 2 lines listed in [`COMPONENTS.md`](COMPONENTS.md), turn each one into a rule a script
can check, and tell the agent when it may submit.

You edit the spec folder here (it starts empty):

```text
tasks/slack-clone-course/course-staff-files/final_task/environment/milestone-specs/milestone-2-specs/
```

The run copies it into the agent's container
as `/spec`, with `README.md` at its top level (so it becomes `/spec/README.md`). `/spec/README.md` is the only file the agent is told to read. It
must name every other file, and state the scope, the order of work, and when the agent may
submit. A file your `/spec/README.md` does not mention may never be opened. Every run starts
with an empty `/app`. You will add your specs and checks in this folder.

Below, we illustrate some helpful examples of what your `milestone-2-specs/` folder (which
becomes `/spec`) can look like. They are in
[`tasks/slack-clone-course/student-files/example-spec/`](tasks/slack-clone-course/student-files/example-spec/).

## 3. Example specs

> [!WARNING]
> **THESE ARE JUST EXAMPLES. DO NOT COPY THEM INTO YOUR SPEC FOLDER.** Use them to see what a
> spec and its checks can look like. They are suggestions, not the required format: you may write
> your specs another way, as long as they follow the Staged layout.

### A staged spec: auth

Consider auth as a component. A complete example spec for auth (register, login, `me`) is in
[`tasks/slack-clone-course/student-files/example-spec/auth-staged/`](tasks/slack-clone-course/student-files/example-spec/auth-staged/).
It is the same example as in milestone 1, except that its checks now have the two testing modes
(explained further below, in [Section 5](#5-checks)). The agent does not (and should not) see this example.

| File | What it is |
| --- | --- |
| `auth-staged/auth-1-register.md`, `auth-2-login.md`, `auth-3-sessions.md` | Nine rules split into three chained spec files (n = 3), each one request with an exact result |
| `auth-staged/checks/check_auth_1.sh`, `_2.sh`, `_3.sh` | One check per spec file. Tests each rule against the running cluster, prints PASS or FAIL |
| `auth-staged/checks/check_auth_all.sh` | Runs the three checks in order and prints one total |
| `auth-staged/checks/lib.sh` | Shared helpers for the checks. The testing mode is set near the top: `FEEDBACK=verbose` or `FEEDBACK=score_only` |
| `auth-staged/README.md` | Written for the agent, not for you: it is the first file the agent reads in `/spec`. It gives the scope, order of work, how to verify, and when the agent may submit |

<details>
<summary>The <code>instruction.md</code> lines behind the auth specs</summary>

> `POST /api/auth/register` — body `{username, password, display_name?}`. `username` is a short
> URL-safe slug (letters/digits/underscore, no spaces or special chars). `password` >= 8
> characters. Returns `201 {user, token}` where `token` is a bearer token of length >= 16.
> Duplicate `username` → `409`. Invalid username (e.g. contains a space) or short password →
> `400`. The endpoint takes no email field.

> `POST /api/auth/login` — body `{username, password}`. Returns `200 {user, token}`. Wrong
> password → `401`.

> `GET /api/auth/me` — returns `200 {user}` for the bearer-token holder. Missing or invalid
> token → `401`.

Each rule in the auth spec files takes one behavior from this text and states it so a script
can check it. Each `checks/check_auth_N.sh` then sends real requests to the running app for each rule and prints
PASS or FAIL, so the agent can tell which rules its code still gets wrong.

</details>

### A new component, with auth as a regression test

Below is how one might build a new component while turning auth into a regression test. The
example is in
[`tasks/slack-clone-course/student-files/example-spec/component-x-staged/`](tasks/slack-clone-course/student-files/example-spec/component-x-staged/).
It has the same layout as `auth-staged/`: a new component, component X, built in staged steps,
plus a single `checks/regression.sh` that reruns the auth rules above. The agent does not (and should not) see this example either.

| File | What it is |
| --- | --- |
| `component-x-staged/README.md` | Written for the agent, not for you: it is the first file the agent reads in `/spec`. It gives the scope, the order of work (get `regression.sh` to `0 broken` first, then X.1 and X.2, rerunning the suite after every step), how to verify, and when the agent may submit |
| `component-x-staged/component-x-1.md`, `component-x-2.md` | Two chained spec files (n = 2), each with Uses, Adds and Rules. Everything in `<angle brackets>` is for you to fill in |
| `component-x-staged/checks/check_component_x_1.sh`, `_2.sh` | One check per spec file. Prints PASS or FAIL for each rule, then a `RESULT` line. They fail until you write the rules |
| `component-x-staged/checks/lib.sh` | Shared helpers for the checks. The testing mode is set near the top: `FEEDBACK=verbose` or `FEEDBACK=score_only` |
| `component-x-staged/checks/regression.sh` | The regression suite: one script holding the auth rules. Prints only what broke, then `REGRESSION: N broken`. It has its own `FEEDBACK` line |

## 4. The layout

Every spec (A to E) uses the **Staged** layout: each component's rules are split into n
ordered steps (for example C.1 to C.n), each with its own check. You choose n, and it can differ
between components. Each step builds on the functions from the step before it, and lists the
functions it uses and the ones it adds.

The same specs are used in every run of the run plan: **individual** (one of C, D or E),
**together** (C, D and E) and **whole app** (A to E).

**What should stay fixed:**

- Every component has n ordered spec files, each with its own check, and each file lists
  the functions it uses from earlier files and the ones it adds.
- Every run has a `/spec/README.md` that the agent reads first and that lists the other
  files in that `/spec`. Change it between runs only where the run requires it (which files
  it lists, the order of work, how A and B are checked). Keep everything else word for word
  the same, and keep it identical between the two testing modes.
- Every check can print in both testing modes (`score_only` and `verbose`), switched by one
  setting such as `FEEDBACK` in each component's `checks/lib.sh`. Nothing else in the checks
  changes between the two modes.

**What is up to you:**

- n, and where each step starts and ends
- file and folder names
- function names, and which functions each step adds or reuses
- how each rule is worded, and how finely you break up each line (one line can become several
  rules)
- the order of work, including which component comes first when they are given together, and
  when the agent may submit
- where pieces shared by two components are built and checked (for example, D needs IRC
  bridging to survive a killed node, but the IRC gateway is in E; E's web UI needs reactions
  from C), and say which you chose in your `/spec/README.md`
- workflow instructions in your `/spec/README.md` (for example, run the checks after each step and fix
  failures before moving on)
- warnings in your `/spec/README.md` or spec files about mistakes you have seen the agent make in
  practice runs, added before you freeze the spec
- time guidance in your `/spec/README.md`, such as how much of the agent's 50 minutes to spend on each
  step or component (e.g. the agent cannot see a clock, so you can tell it to check the time with `date`)
- what your checks test and print, and how they are built (for example, a helper file shared by
  several checks, like `component-x-staged/checks/lib.sh` in the example), as long as the
  testing mode works as described above
- what you include beyond the lines in [`COMPONENTS.md`](COMPONENTS.md), such as the
  milestone 0 conditions
- how you word your base `/spec/README.md`, the version you start from before adding or removing the
  parts that change between runs

## 5. Checks

The agent cannot tell whether its own code works. Your checks are how it finds out.

A check is a small script that sends real requests to the running app and prints PASS or FAIL
for each rule. The agent runs it while it works, fixes what fails, and submits only when
everything passes.

- **Your checks** are the practice exam. The agent can run them as often as it likes.
- **The hidden tests** are the real exam. The agent never sees them, and they decide your grade.

The closer your checks are to the real exam, the better the agent scores. A rule with no check
is a rule the agent will probably get wrong without noticing.

Two limits: each agent command is cut off after 30 seconds, so a check that takes longer never
finishes; and the agent has 50 minutes in total.

Your checks run inside the agent's container, against the app it builds, so you cannot fully
test them on your own machine. Before a run, check each script for syntax errors (for a bash
script, `bash -n <your-check>.sh`; for a Python script, `python3 -m py_compile <your-check>.py`).
After your first run, read the transcript to confirm your checks ran, finished within 30
seconds, and printed clear PASS or FAIL lines.

### Keep check output from getting too long

Each time the agent runs a command, such as one of your checks, it reads that command's output
to decide what to do next. If the output is very long (10,000 characters or more), the agent
does not get all of it: it gets the first 5,000 characters, a note saying the middle was cut,
and the last 5,000 characters. Say a check prints a huge response body for every rule. The
`FAIL` line for rule 2.3 might land in the cut middle part, so the agent never sees that rule
2.3 failed and does not fix it. The transcript you read afterwards has the same gap, so you
cannot see it either.

For this reason, you might want to make sure your check output does not get too long in `verbose` mode:

- you can keep each rule's result to a line or two, as long as a `FAIL` line still describes the
  bug: what was expected and what came back. For example, `PASS 2.1 reaction returns 200 with
  count 1` or `FAIL 2.3 duplicate group handle: expected 409, got 201`
- end with a summary line, for example `RESULT: 22 passed, 0 failed`
- do not print full response bodies unless a rule fails

### `score_only` vs `verbose` testing mode

The experiments for this milestone require every check to print in either the `score_only`
or the `verbose` testing mode. The easiest way to add this option is a helper in each component's `checks/` folder that all of that component's checks
load, with the testing mode set at the top. Each check then calls the same function for every
failure:

```bash
# <component-staged>/checks/lib.sh
FEEDBACK=verbose   # or: score_only

pass=0; fail=0
ok()  { pass=$((pass+1)); [ "$FEEDBACK" = verbose ] && echo "PASS $1 $2"; return 0; }
bad() { fail=$((fail+1)); [ "$FEEDBACK" = verbose ] && echo "FAIL $1 $2: expected $3, got $4"; return 0; }
summary() { echo "RESULT: $pass passed, $fail failed"; [ "$fail" -eq 0 ]; }
```

Switching testing modes is then a one-word change in each `lib.sh`, so you know nothing else
changed between runs. The example's `component-x-staged/checks/lib.sh` works this way: set `FEEDBACK=score_only` at its
top and every component X check prints only its `RESULT` line.

Do not mention the testing mode or how to switch it in your `/spec/README.md` or spec files. If you want
your `/spec/README.md` to show sample output, show only output that looks the same in both modes (for
example, a final score line). That way one `/spec/README.md` works for both modes, and a
`score_only` run gives the agent no hint that more detailed output exists.

### The regression suite

Runs 5.1 and 5.2 ask for a *regression suite*. You may be wondering how to turn your A
and B checks into one.

So far, your checks may have been plain staged checks: many separate scripts, each run on its own,
with output like this:

```text
PASS 2.1 owner can promote member
FAIL 2.3 duplicate group handle: expected 409, got 201
RESULT: 7 passed, 1 failed
```

One way to build the regression suite is to put all the A and B rules into one script, such as
`checks/regression.sh`, that prints only what broke and one combined total in the style below:

```text
REGRESSION FAIL roles 2.3 duplicate group handle: expected 409, got 201
REGRESSION: 1 broken
```

You can reuse the requests and helpers from your milestone 1 checks, but in Runs 5.1 and 5.2
the plain A and B check scripts themselves are not in `/spec`, so the suite has to hold the
rules itself. The example in [section 3](#3-example-specs) does this: its
[`checks/regression.sh`](tasks/slack-clone-course/student-files/example-spec/component-x-staged/checks/regression.sh)
holds all the auth rules, and its `README.md` tells the agent to run it after every step.

## 6. Example folder trees

Suppose you are writing **k** new components, **X1** to **Xk** (in your milestone, k = 3: C, D
and E), each split into n steps in the [Staged layout](#4-the-layout). Below is what `/spec` holds in each run of the run plan (see
[README Setup](README.md#setup-part-1)). Every run has a `/spec/README.md` that the
agent reads first and that lists every other file in that `/spec`.

### Run 1.x layout

```text
/spec/
  README.md                    # the agent reads this first; it names every file below
  xi/                          # one component, Xi
    xi-1.md                    # step 1 of Spec Xi
    xi-2.md                    # step 2, builds on step 1
    ...
    xi-n.md
    checks/
      lib.sh                   # helper the Xi checks load: FEEDBACK=score_only
      check_xi_1.sh            # checks the rules in xi-1.md
      check_xi_2.sh
      ...
      check_xi_n.sh
```

### Run 2.x layout

```text
/spec/
  README.md                    # the agent reads this first; it names every file below
  xi/                          # one component, Xi
    xi-1.md                    # step 1 of Spec Xi
    xi-2.md                    # step 2, builds on step 1
    ...
    xi-n.md
    checks/
      lib.sh                   # helper the Xi checks load: FEEDBACK=verbose
      check_xi_1.sh            # checks the rules in xi-1.md
      check_xi_2.sh
      ...
      check_xi_n.sh
```

### Run 3 layout

```text
/spec/
  README.md                    # names X1 to Xk and the order of work
  x1/
    x1-1.md ... x1-n.md
    checks/lib.sh              # FEEDBACK=verbose
    checks/check_x1_1.sh ... checks/check_x1_n.sh
  ...
  xk/
    xk-1.md ... xk-n.md
    checks/lib.sh              # FEEDBACK=verbose
    checks/check_xk_1.sh ... checks/check_xk_n.sh
```

### Run 4 layout

```text
/spec/
  README.md                    # names the checks and the order to make them pass
  x1/
    checks/lib.sh              # FEEDBACK=verbose
    checks/check_x1_1.sh ... checks/check_x1_n.sh
  ...
  xk/
    checks/lib.sh              # FEEDBACK=verbose
    checks/check_xk_1.sh ... checks/check_xk_n.sh
```

### Run 5.1 layout

```text
/spec/
  README.md                    # gets regression.sh to 0 broken first, then X1 to Xk; tells the agent to run regression.sh after each step
  checks/
    regression.sh              # the A and B regression suite: one command for every A and B rule
  x1/
    x1-1.md ... x1-n.md
    checks/lib.sh              # FEEDBACK=verbose
    checks/check_x1_1.sh ... checks/check_x1_n.sh
  ...
  xk/
    xk-1.md ... xk-n.md
    checks/lib.sh              # FEEDBACK=verbose
    checks/check_xk_1.sh ... checks/check_xk_n.sh
```

### Run 5.2 layout

```text
/spec/
  README.md                    # names the A and B specs; builds A and B until regression.sh reports 0 broken, then X1 to Xk; tells the agent to run regression.sh after each step
  checks/
    regression.sh              # the A and B regression suite: one command for every A and B rule
  a/                           # spec only, no plain checks
    a-1.md ... a-n.md
  b/                           # spec only, no plain checks
    b-1.md ... b-n.md
  x1/
    x1-1.md ... x1-n.md
    checks/lib.sh              # FEEDBACK=verbose
    checks/check_x1_1.sh ... checks/check_x1_n.sh
  ...
  xk/
    xk-1.md ... xk-n.md
    checks/lib.sh              # FEEDBACK=verbose
    checks/check_xk_1.sh ... checks/check_xk_n.sh
```

### Run 5.3 layout

```text
/spec/
  README.md                    # gives the order A, B, X1 to Xk; names the A and B specs; tells the agent to run each A and B check on its own
  a/                           # plain checks, no regression suite
    a-1.md ... a-n.md
    checks/lib.sh              # FEEDBACK=verbose
    checks/check_a_1.sh ... checks/check_a_n.sh
  b/                           # plain checks, no regression suite
    b-1.md ... b-n.md
    checks/lib.sh              # FEEDBACK=verbose
    checks/check_b_1.sh ... checks/check_b_n.sh
  x1/
    x1-1.md ... x1-n.md
    checks/lib.sh              # FEEDBACK=verbose
    checks/check_x1_1.sh ... checks/check_x1_n.sh
  ...
  xk/
    xk-1.md ... xk-n.md
    checks/lib.sh              # FEEDBACK=verbose
    checks/check_xk_1.sh ... checks/check_xk_n.sh
```

File and folder names are up to you, as long as `/spec/README.md` (at the top of the spec folder)
names them.
