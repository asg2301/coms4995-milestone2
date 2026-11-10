# Component C: message features

Read the staff instruction.md and all four specs under /spec/c before coding.
Build the minimum authentication, workspace, channel, message, and event
prerequisites for Component C. Keep those primitives shared across three
HTTP nodes. Do not build unrelated UI or IRC features for this run.

Work in this order:
1. /spec/c/c-1.md, then bash /spec/c/checks/check_c_1.sh.
2. /spec/c/c-2.md, then bash /spec/c/checks/check_c_2.sh.
3. /spec/c/c-3.md, then bash /spec/c/checks/check_c_3.sh.
4. /spec/c/c-4.md, then bash /spec/c/checks/check_c_4.sh.

The checks use /spec/c/checks/check_c.py, /spec/c/checks/ws.py and
/spec/c/checks/lib.sh. Leave all files in /spec unchanged. Implement and
verify the documented API rules, including cases not sampled by checks.
Do not hardcode fixtures or fabricate output.

Start a working app early. /app/start.sh must be executable and stay in
the foreground on a clean launch. During testing, launch it in the
background with redirected logs. All three nodes must return HTTP 200
and their own node_id on /api/health. Reject invalid WebSocket tokens;
losing one HTTP node must not take down the other two. Keep state durable
and consistent across nodes. Test real requests against the running app.

Run each check in a separate command and inspect the RESULT line and exit
status. Fix the app when a check fails. Rerun affected earlier stages
after shared changes. Before submitting, run all four checks and verify
startup. If time expires, accurately report any remaining failures.

Run the supplied checks only through the listed shell scripts. Do not modify,
copy, or reconfigure the supplied checks, their helpers, or any files in
/spec. You may write independent diagnostic tests against your app.
