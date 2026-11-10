# Component D: cluster robustness

Read the staff instruction and the three specs below. Build the prerequisites
for the listed checks: an executable foreground /app/start.sh, three separate
HTTP nodes, health, registration, workspaces and channels, messages and listings,
authenticated WebSocket subscribe and resume, and the IRC message bridge. Keep
shared data durable and observable across all nodes. Build only the additional
features needed by the rules and checks for this run.

1. Implement /spec/d/d-1.md, then run bash /spec/d/checks/check_d_1.sh.
2. Implement /spec/d/d-2.md, then run bash /spec/d/checks/check_d_2.sh.
3. Implement /spec/d/d-3.md, then run bash /spec/d/checks/check_d_3.sh.

Start your app in the background for development checks and wait until all three
health endpoints answer. The first two checks intentionally stop a service
inside the trial; keep the supervisor running and let the service recover.
Run checks sequentially and rerun earlier stages after shared changes. Submit
after running all three checks and confirming the app still starts. Report any
remaining failures if you run out of time.

Run the supplied checks only through the listed shell scripts. Do not modify,
copy, or reconfigure the supplied checks, their helpers, or any files in
/spec. You may write independent diagnostic tests against your app.
