# Cluster chat project

Read the staff instruction. Keep /app/start.sh executable and in the foreground,
with three independently supervised HTTP nodes, shared durable state, local Redis,
a local IRC listener, and a browser UI. Wait for all three health endpoints before
running checks. Enforce authentication and reuse the same data across protocols.
Run the supplied checks only through the listed shell scripts. Do not modify,
copy, or reconfigure the supplied checks, helpers, or files in /spec.
Independent diagnostic tests against your app are allowed.

First make the A/B baseline work until this command reports zero broken:
    bash /spec/checks/regression.sh
The suite uses /spec/checks/regression.py, check_baseline.py, check_roles.py
and check_channels.py in the same directory. Run the shell entry point only.
After EVERY C, D and E stage, rerun the regression suite in a separate command
and fix any new failures before moving on. Rerun it before submitting.

### C, D and E
Finish one stage before moving to the next. Launch checks as separate shell commands.
If a result is nonzero, diagnose the app, fix it, and rerun affected stages.
The D outage checks target the intended port owners; keep the supervisor running.
- Implement /spec/c/c-1.md, then run bash /spec/c/checks/check_c_1.sh.
- Implement /spec/c/c-2.md, then run bash /spec/c/checks/check_c_2.sh.
- Implement /spec/c/c-3.md, then run bash /spec/c/checks/check_c_3.sh.
- Implement /spec/c/c-4.md, then run bash /spec/c/checks/check_c_4.sh.
  /spec/c/checks contains check_c.py, ws.py and lib.sh used by these scripts.
- Implement /spec/d/d-1.md, then run bash /spec/d/checks/check_d_1.sh.
- Implement /spec/d/d-2.md, then run bash /spec/d/checks/check_d_2.sh.
- Implement /spec/d/d-3.md, then run bash /spec/d/checks/check_d_3.sh.
  /spec/d/checks contains check_d.py, ws.py and lib.sh used by these scripts.
- Implement /spec/e/e-1.md, then run bash /spec/e/checks/check_e_1.sh.
- Implement /spec/e/e-2.md, then run bash /spec/e/checks/check_e_2.sh.
- Implement /spec/e/e-3.md, then run bash /spec/e/checks/check_e_3.sh.
  /spec/e/checks contains check_e.py, ws.py and lib.sh used by these scripts.

Before submitting, confirm the app starts, all three nodes respond, and the
app still works after the checks. Report any remaining failures if time runs out.
