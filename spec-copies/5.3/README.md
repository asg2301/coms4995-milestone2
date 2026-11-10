# Cluster chat project

Read the staff instruction. Keep /app/start.sh executable and in the foreground,
with three independently supervised HTTP nodes, shared durable state, local Redis,
a local IRC listener, and a browser UI. Wait for all three health endpoints before
running checks. Enforce authentication and reuse the same data across protocols.
Run the supplied checks only through the listed shell scripts. Do not modify,
copy, or reconfigure the supplied checks, helpers, or files in /spec.
Independent diagnostic tests against your app are allowed.

### A and B
Read /spec/channels/common.md before implementing B.
- Implement /spec/roles/roles-1.md, then run bash /spec/roles/checks/check_roles_1.sh.
- Implement /spec/roles/roles-2.md, then run bash /spec/roles/checks/check_roles_2.sh.
- Implement /spec/roles/roles-3.md, then run bash /spec/roles/checks/check_roles_3.sh.
- Implement /spec/channels/channels-1.md, then run bash /spec/channels/checks/check_channels_1.sh.
- Implement /spec/channels/channels-2.md, then run bash /spec/channels/checks/check_channels_2.sh.
- Implement /spec/channels/channels-3.md, then run bash /spec/channels/checks/check_channels_3.sh.
- Implement /spec/channels/channels-4.md, then run bash /spec/channels/checks/check_channels_4.sh.
Run each A and B stage check separately. Rerun affected stages after shared changes.
The roles helpers are check_baseline.py, check_roles.py, run_stage.py and lib.sh.
The channels helpers are check_channels.py, run_stage.py and lib.sh.

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
