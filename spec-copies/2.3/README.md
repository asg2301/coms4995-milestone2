# Component E: message edits, IRC and browser UI

Read the staff instruction and build a runnable app for these three stages.
An executable /app/start.sh must stay in the foreground and start all three
HTTP nodes. Build the relevant authentication, workspace, channel, message,
thread, reaction and WebSocket prerequisites. Use shared durable state.

1. Implement /spec/e/e-1.md, then run bash /spec/e/checks/check_e_1.sh.
2. Implement /spec/e/e-2.md, then run bash /spec/e/checks/check_e_2.sh.
3. Implement /spec/e/e-3.md, then run bash /spec/e/checks/check_e_3.sh.

Start the app in the background for development checks and wait for health
on all three nodes. Keep the browser application functional beyond what the
checks sample. Run the stages in order and rerun affected stages after changes.
Before submitting, run all three checks and verify clean startup. Report
any remaining failures if you run out of time.

Run the supplied checks only through the listed shell scripts. Do not modify,
copy, or reconfigure the supplied checks, their helpers, or any files in
/spec. You may write independent diagnostic tests against your app.
