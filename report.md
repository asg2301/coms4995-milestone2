# Milestone 2 report

- Name: Aravind Ganga
- UNI: asg2301

## Run results

I used GPT-5.6 Luna for two repeats of each of the 11 settings. The results are from my supplied checks, not the course's hidden tests. Scores are passed/failed. I took the last completed result for each staged script from its trajectory. A partial total shows how many stages returned a complete score; an em dash means none did. Later changes can invalidate earlier scores, so these numbers are not a fresh replay of every check on the final app. The A/B regression score is the last complete suite output.

Check counts are completed staged outputs or full regression summaries. A count of x/y means x completed summaries out of y staged invocations listed in commands; chained commands may skip later invocations. The A.1 wrapper returns two summaries (boot and auth), counted together. Direct helper diagnostics and ad hoc checks are excluded. Submission status comes from the trajectory, not Harbor's zero-exception summary.

| Setting | Components | Repeat | Job folder | Last check results | Cost (USD) | Check runs | Submitted with failures? | Problems |
|---|---|---:|---|---|---:|---|---|---|
| 1.1 | C | 1 | [2026-11-09__17-27-08](tasks/slack-clone-course/jobs/2026-11-09__17-27-08/result.json) | C: 97/0 | $0.229803 | C: 18 | No observed failures | Agent temporarily changed the check configuration and saw assertion details. |
| 1.1 | C | 2 | [2026-11-09__17-34-02](tasks/slack-clone-course/jobs/2026-11-09__17-34-02/result.json) | C: 97/0 | $0.449767 | C: 26/31 | No observed failures | — |
| 1.2 | D | 1 | [2026-11-09__17-56-51](tasks/slack-clone-course/jobs/2026-11-09__17-56-51/result.json) | D: 32/0 | $0.238573 | D: 14 | No observed failures | — |
| 1.2 | D | 2 | [2026-11-09__18-05-03](tasks/slack-clone-course/jobs/2026-11-09__18-05-03/result.json) | D: 32/0 | $0.143747 | D: 7/9 | No observed failures | — |
| 1.3 | E | 1 | [2026-11-09__18-22-13](tasks/slack-clone-course/jobs/2026-11-09__18-22-13/result.json) | E: 88/0 | $0.261220 | E: 16/17 | No observed failures | Agent used a modified E.3 diagnostic; the supplied wrapper stayed score-only. |
| 1.3 | E | 2 | [2026-11-09__18-33-01](tasks/slack-clone-course/jobs/2026-11-09__18-33-01/result.json) | E: 88/0 | $0.477585 | E: 21/22 | No observed failures | Agent bypassed the feedback setting in E.2 and E.3 diagnostic calls. |
| 2.1 | C | 1 | [2026-11-09__17-42-56](tasks/slack-clone-course/jobs/2026-11-09__17-42-56/result.json) | C: 97/0 | $0.256619 | C: 15 | No observed failures | — |
| 2.1 | C | 2 | [2026-11-09__17-48-18](tasks/slack-clone-course/jobs/2026-11-09__17-48-18/result.json) | C: 97/0 | $0.257611 | C: 21/23 | No observed failures | — |
| 2.2 | D | 1 | [2026-11-09__18-10-40](tasks/slack-clone-course/jobs/2026-11-09__18-10-40/result.json) | D: 32/0 | $0.169158 | D: 10 | No observed failures | — |
| 2.2 | D | 2 | [2026-11-09__18-15-33](tasks/slack-clone-course/jobs/2026-11-09__18-15-33/result.json) | D: 32/0 | $0.069407 | D: 5 | No observed failures | — |
| 2.3 | E | 1 | [2026-11-09__18-42-24](tasks/slack-clone-course/jobs/2026-11-09__18-42-24/result.json) | E: 88/0 | $0.318575 | E: 18/19 | No observed failures | — |
| 2.3 | E | 2 | [2026-11-09__18-48-45](tasks/slack-clone-course/jobs/2026-11-09__18-48-45/result.json) | E: 88/0 | $0.166951 | E: 14/15 | No observed failures | — |
| 3 | C, D, E | 1 | [2026-11-09__18-57-14](tasks/slack-clone-course/jobs/2026-11-09__18-57-14/result.json) | C: 97/0; D: 32/0; E: 88/0 | $1.760849 | C: 19; D: 20/22; E: 16 | No observed failures | Two D commands produced no summary; later reruns passed. |
| 3 | C, D, E | 2 | [2026-11-09__19-16-16](tasks/slack-clone-course/jobs/2026-11-09__19-16-16/result.json) | C: —; D: —; E: — | $0.502075 | C: 0; D: 0; E: 0 | Submitted; unverified | Submitted after manual checks without running the supplied staged checks. |
| 4 | C, D, E | 1 | [2026-11-09__19-25-22](tasks/slack-clone-course/jobs/2026-11-09__19-25-22/result.json) | C: 97/0; D: 32/0; E: 88/0 | $0.784791 | C: 14; D: 11/12; E: 13 | No observed failures | One D.1 call produced no summary; a later rerun passed. |
| 4 | C, D, E | 2 | [2026-11-09__19-36-29](tasks/slack-clone-course/jobs/2026-11-09__19-36-29/result.json) | C: 97/0; D: 32/0; E: 88/0 | $2.275696 | C: 30; D: 19/21; E: 14/15 | No observed failures | Some D.1 and E.3 calls produced no summary; later reruns passed. |
| 5.1 | A–E | 1 | [2026-11-09__19-54-57](tasks/slack-clone-course/jobs/2026-11-09__19-54-57/result.json) | A/B: 0 broken; C: 97/0; D: 32/0; E: 40/1 (2/3 stages) | $3.010946 | regression: 31; C: 14; D: 7/8; E: 6 | Did not submit | Limit stop during E.2 (IRC). E.3 not reached. Last A/B checkpoint predates E.2. |
| 5.1 | A–E | 2 | [2026-11-09__20-23-12](tasks/slack-clone-course/jobs/2026-11-09__20-23-12/result.json) | A/B: 1 broken; C: 97/0; D: 32/0; E: 40/1 (2/3 stages) | $3.026545 | regression: 31; C: 16; D: 4; E: 5 | Did not submit | Limit stop during E.2 (IRC). E.3 not reached. Last A/B checkpoint predates E.2. |
| 5.2 | A–E | 1 | [2026-11-09__20-44-30](tasks/slack-clone-course/jobs/2026-11-09__20-44-30/result.json) | A/B: 3 broken; C: —; D: —; E: — | $0.538963 | regression: 16; C: 0; D: 0; E: 0 | Yes | Submitted with 3 broken baseline checks (mentions, events, IRC); no C/D/E scores. |
| 5.2 | A–E | 2 | [2026-11-09__20-58-20](tasks/slack-clone-course/jobs/2026-11-09__20-58-20/result.json) | A/B: 0 broken; C: 97/0; D: 32/0; E: 88/0 | $3.031260 | regression: 20; C: 11; D: 7; E: 19 | Did not submit | All stages scored and last regression was zero, but the agent hit a limit before submitting. |
| 5.3 | A–E | 1 | [2026-11-09__21-29-47](tasks/slack-clone-course/jobs/2026-11-09__21-29-47/result.json) | A: 182/1; B: 217/0; C: 97/0; D: 32/0; E: 40/1 (2/3 stages) | $3.000269 | A: 9/10; B: 14; C: 20; D: 3/5; E: 9 | Did not submit | Limit stop during E.2 (IRC); E.3 not reached. A.2 last showed a cross-workspace permissions failure. |
| 5.3 | A–E | 2 | [2026-11-09__21-50-56](tasks/slack-clone-course/jobs/2026-11-09__21-50-56/result.json) | A: 183/0; B: 217/0; C: 97/0; D: 32/0; E: 54/0 (2/3 stages) | $3.006762 | A: 15; B: 25/26; C: 14; D: 4/9; E: 14/23 | Did not submit | Limit stop after nine E.3 attempts without a complete summary; 30-second shell timeouts. |

Recorded cost for the 22 planned attempts: **$23.977171**.

## 1. Does detailed feedback help?

I compared 1.1 with 2.1 for C, 1.2 with 2.2 for D, and 1.3 with 2.3 for E. Each pair uses the same component and supplied assertions. Results are ordered repeat 1, repeat 2.

| Component | Score-only scores | Verbose scores | Score-only completed checks | Verbose completed checks |
|---|---|---|---:|---:|
| C | 97/0; 97/0 | 97/0; 97/0 | 18; 26 | 15; 21 |
| D | 32/0; 32/0 | 32/0; 32/0 | 14; 7 | 10; 5 |
| E | 88/0; 88/0 | 88/0; 88/0 | 16; 21 | 18; 14 |

Verbose output did not improve the final observed scores: all twelve individual-component runs reached zero failures. It was associated with fewer completed check invocations in both repeats for C and D. For E the direction differed by repeat: verbose took 18 versus 16 completed checks in repeat 1, and 14 versus 21 in repeat 2. D gives the clearest comparison because neither score-only run exposed detailed assertion output. C repeat 1 and both E score-only repeats saw details through workarounds. For example, E repeat 2 overrode how the helper read its feedback configuration. That weakens the C and E comparisons. Fewer invocations also do not necessarily mean less time: the agent may spend time reading code or running independent diagnostics.

## 2. Can checks alone replace written rules?

I compared Setting 3 (C/D/E specs and checks) with Setting 4 (the same C/D/E checks without written component specs).

| Setting | Repeat | C | D | E | Completed staged checks | Outcome |
|---|---:|---:|---:|---:|---:|---|
| 3 | 1 | 97/0 | 32/0 | 88/0 | 55 | Submitted |
| 3 | 2 | — | — | — | 0 | Submitted after manual checks only |
| 4 | 1 | 97/0 | 32/0 | 88/0 | 38 | Submitted |
| 4 | 2 | 97/0 | 32/0 | 88/0 | 63 | Submitted |

Checks alone were enough for both Setting 4 agents to pass the sampled assertions. The Setting 3 repeat with comparable results reached the same scores. Its second repeat submitted after manual smoke checks, so it cannot be scored as passing or failing the supplied suite. I would keep written rules because finite checks do not specify untested behavior. The experiment shows success on the assertions the agent saw, not equivalent coverage of the whole contract.

## 3. Do components pass fewer checks when built together?

I compared the individual verbose settings (2.1–2.3) with Setting 3 to keep the feedback mode the same. Setting 4 is a secondary comparison because it also changes whether the written specs are available.

| Component | Individual verbose (r1; r2) | Together with specs, 3 (r1; r2) | Together with checks only, 4 (r1; r2) |
|---|---|---|---|
| C | 97/0; 97/0 | 97/0; — | 97/0; 97/0 |
| D | 32/0; 32/0 | 32/0; — | 32/0; 32/0 |
| E | 88/0; 88/0 | 88/0; — | 88/0; 88/0 |

I did not observe a lower final score in the combined run with measurements. There was extra integration work: Setting 3 repeat 1 scored 4/6 on D.3 before revisiting it after E and reaching 10/0. That shows difficulty during development even though the last score matched the isolated runs. Setting 3 repeat 2 has no supplied-check results, so there is only one direct combined repeat to compare. Both checks-only repeats also reached the isolated scores, but they do not fill in the missing result.

## 4a. Did C, D, and E break working A/B behavior?

I used 5.1 and 5.2 to compare the last A/B checkpoint before C/D/E with later regression checks.

| Setting | Repeat | Before C/D/E | Highest later broken count | Last observed broken count |
|---|---:|---:|---:|---:|
| 5.1 | 1 | 0 | 10 | 0 |
| 5.1 | 2 | 0 | 3 | 1 |
| 5.2 | 1 | 3 | — | 3 |
| 5.2 | 2 | 0 | 19 | 0 |

Later checks detected loss of previously working behavior in every repeat that reached a zero-broken baseline and proceeded to C/D/E. In 5.1 repeat 1 the suite went from zero to one broken after C.2, then as high as ten after D.1 before recovering to zero. In 5.2 repeat 2 the count reached 19 during a restart, with closed connections and failing health checks, and later returned to zero. That spike shows temporary service instability, not 19 lasting defects. 5.1 repeat 2 had one broken check at its last checkpoint. 5.2 repeat 1 never reached a clean baseline, so it cannot establish a regression from one.

## 4b. Did written A/B specs help when the regression suite was available?

Settings 5.1 and 5.2 share the regression suite and C/D/E inputs; 5.2 adds the A/B specs.

| Setting | Repeat | Last A/B broken count | Last E score | Outcome |
|---|---:|---:|---|---|
| 5.1 | 1 | 0 | 40/1 (2/3 stages) | LimitsExceeded |
| 5.1 | 2 | 1 | 40/1 (2/3 stages) | LimitsExceeded |
| 5.2 | 1 | 3 | — | Submitted |
| 5.2 | 2 | 0 | 88/0 | LimitsExceeded |

The result was mixed. Both 5.1 runs reached a zero-broken baseline but stopped before E.3. With written A/B specs, 5.2 repeat 2 completed the most check coverage and recovered to zero broken. The other 5.2 run did worse: it submitted with three broken checks and no C/D/E results. The added specs may help some trajectories, but the repeats do not show a consistent advantage. Both 5.1 A/B counts were recorded before their last E.2 attempts.

## 4c. Did a regression suite differ from staged A/B checks?

I compared 5.2 and 5.3. Both give the agent A/B specs. 5.2 uses a combined A/B regression suite during C/D/E work; 5.3 uses individual A/B stage checks. These measurements differ, so an A/B regression count is not directly comparable with the sum of staged assertions.

| Setting | Repeat | Last A/B evidence | Last C | Last D | Last E |
|---|---:|---|---|---|---|
| 5.2 | 1 | 3 broken | — | — | — |
| 5.2 | 2 | 0 broken | 97/0 | 32/0 | 88/0 |
| 5.3 | 1 | A 182/1; B 217/0 | 97/0 | 32/0 | 40/1 (2/3 stages) |
| 5.3 | 2 | A 183/0; B 217/0 | 97/0 | 32/0 | 54/0 (2/3 stages) |

The combined suite showed whether earlier behavior survived later changes. The staged checks gave more local evidence while building A and B, but neither 5.3 repeat had a final combined A/B checkpoint after C/D/E. Both 5.3 repeats obtained full C and D scores and stopped without a complete E.3 score. The best observed coverage was 5.2 repeat 2, but its other repeat did not get past the baseline checks. There is not enough consistent evidence to say one format produced a better final app. In 5.3 repeat 2, repeated E.3 shell timeouts also prevented a final browser score despite passing E.1 and E.2.

## Run notes

The submission keeps the planned repeats, including limit stops. It excludes setup failures before the agent ran and early Component C pilots. An environment-file parsing mismatch delayed the early runs; after correcting the local file format, the same runner could make model calls. No credentials are included in this repository.
