## Milestone 2

In the previous milestone, you wrote specs and checks for components A (roles and invitations)
and B (channels and message history).

<mark>In this milestone, you will write the specs and checks for the
<b>remaining</b> components, and have the agent build the whole app (A to E) from scratch, with a
special emphasis on <b>testing</b>.</mark>

The remaining components are:

- **C: features built on messages:** reactions, DMs, files, slash commands, user groups,
  `@group-handle` and `@here`, profiles
- **D: cluster robustness:** a killed HTTP node, a killed Redis, WebSocket speed under load
- **E: IRC, web UI and message edits:** the IRC gateway, the web UI, editing and deleting messages

<mark>To understand the guidelines for milestone 2, use:</mark>

- <mark>this <b>README</b></mark>
- <mark>the <b><a href="GUIDE.md"><code>GUIDE.md</code></a></b>: how to write specs and checks</mark>
- <mark>the <b><a href="COMPONENTS.md"><code>COMPONENTS.md</code></a></b>: the lines from <code>instruction.md</code> that cover components C, D and E</mark>

Below, we outline the core tasks for Milestone 2:

- [Setup](#setup-part-1) (Part 1): the settings for the experiments to run
- [Analysis](#analysis-part-2) (Part 2): questions to answer based on those experiments

### Setup (Part 1)

As in the previous milestone, each component gets a **spec** (rules the code must follow) and **checks**
(scripts that print PASS or FAIL).

> [!IMPORTANT]
> <i>**Your milestone 1 Specs A and B and their checks will be used in some of the experiments
> below**, and are required in your submission (you may improve them). Without them,
> the parts that depend on A and B might not pass. Recall that in milestone 1, the agent could not break the four milestone 0 boot conditions
> (see [`milestone-0-specs/README.md`](tasks/slack-clone-course/course-staff-files/final_task/environment/milestone-specs/milestone-0-specs/README.md)). The same should hold here. You should ensure that
> Specs A and B, and their checks, still cover those boot conditions. If your milestone 1 checks
> did not test them, you should ensure you add them here.</i>

Write the specs and checks in <mark><a href="GUIDE.md#4-the-layout"><b>staged layout</b></a></mark>,
unless specified otherwise: n chained spec files per component (for example C.1 to C.n), each
with its own check.

Unlike the checks in the previous milestone, the checks in milestone 2 have a **testing mode**,
a setting for how much they print (for example `FEEDBACK=score_only` or `FEEDBACK=verbose` in each
component's `checks/lib.sh`):

- **`score_only`:** a failing check prints only the score, for example `RESULT: 7 passed, 2 failed`.
- **`verbose`:** a failing check also prints each failure and why, for example
  `FAIL 2.3 duplicate group handle: expected 409, got 201`.

Some runs also incorporate the idea of a **regression suite**. The point of this is to make
sure that *adding* something new to the app does not break something that *used* to work.

There are 5 core settings, some split by component:

**<ins>Setting 1:</ins>** Each component alone, checks in `score_only` mode.

**🔁 1.1:** `/spec` contains a `README.md`, Spec C and its checks (in `score_only` mode).<br>
**🔁 1.2:** `/spec` contains a `README.md`, Spec D and its checks (in `score_only` mode).<br>
**🔁 1.3:** `/spec` contains a `README.md`, Spec E and its checks (in `score_only` mode).

**<ins>Setting 2:</ins>** Each component alone, checks in `verbose` mode.

**🔁 2.1:** `/spec` contains a `README.md`, Spec C and its checks (in `verbose` mode).<br>
**🔁 2.2:** `/spec` contains a `README.md`, Spec D and its checks (in `verbose` mode).<br>
**🔁 2.3:** `/spec` contains a `README.md`, Spec E and its checks (in `verbose` mode).

**<ins>Setting 3:</ins>** C, D and E together, checks in `verbose` mode.

**🔁 3:** `/spec` contains a `README.md`, Specs C, D and E and their checks (in `verbose` mode).

**<ins>Setting 4:</ins>** C, D and E checks only (no specs), in `verbose` mode.

**🔁 4:** `/spec` contains only a `README.md` and the checks for C, D and E (in `verbose` mode), with no spec files.

**<ins>Setting 5:</ins>** The whole app (A to E), checks in `verbose` mode; the three runs vary how A and B are given.

**🔁 5.1:** `/spec` contains:
- a `README.md`
- Specs C, D and E and their checks (in `verbose` mode)
- the A and B regression suite (in `verbose` mode)

**🔁 5.2:** `/spec` contains:
- a `README.md`
- Specs C, D and E and their checks (in `verbose` mode)
- the A and B regression suite (in `verbose` mode)
- Specs A and B

**🔁 5.3:** `/spec` contains:
- a `README.md`
- Specs C, D and E and their checks (in `verbose` mode)
- the A and B checks (in `verbose` mode)
- Specs A and B

You may run the experiments in the order outlined below. For each run, the table shows its testing mode,
what the agent sees in `/spec`, a link to a sample of how the `/spec` folder is structured, and
how many times to repeat it.

| Run | Testing mode | What does the agent see? | How is the `/spec` folder structured? | Repeats |
| --- | --- | --- | --- | --- |
| **🔁&nbsp;1.1** | `score_only` | a `/spec/README.md`, Spec C and its checks | [View&nbsp;1.1&nbsp;sample&nbsp;tree](GUIDE.md#run-1x-layout) | 2 |
| **🔁&nbsp;1.2** | `score_only` | a `/spec/README.md`, Spec D and its checks | [View&nbsp;1.2&nbsp;sample&nbsp;tree](GUIDE.md#run-1x-layout) | 2 |
| **🔁&nbsp;1.3** | `score_only` | a `/spec/README.md`, Spec E and its checks | [View&nbsp;1.3&nbsp;sample&nbsp;tree](GUIDE.md#run-1x-layout) | 2 |
| **🔁&nbsp;2.1** | `verbose` | a `/spec/README.md`, Spec C and its checks | [View&nbsp;2.1&nbsp;sample&nbsp;tree](GUIDE.md#run-2x-layout) | 2 |
| **🔁&nbsp;2.2** | `verbose` | a `/spec/README.md`, Spec D and its checks | [View&nbsp;2.2&nbsp;sample&nbsp;tree](GUIDE.md#run-2x-layout) | 2 |
| **🔁&nbsp;2.3** | `verbose` | a `/spec/README.md`, Spec E and its checks | [View&nbsp;2.3&nbsp;sample&nbsp;tree](GUIDE.md#run-2x-layout) | 2 |
| **🔁&nbsp;3** | `verbose` | a `/spec/README.md`, Specs C, D and E and their checks | [View&nbsp;3&nbsp;sample&nbsp;tree](GUIDE.md#run-3-layout) | 2 |
| **🔁&nbsp;4** | `verbose` | a `/spec/README.md`, the checks for C, D and E | [View&nbsp;4&nbsp;sample&nbsp;tree](GUIDE.md#run-4-layout) | 2 |
| **🔁&nbsp;5.1** | `verbose` | a `/spec/README.md`, Specs C, D and E and their checks, and the A and B regression suite | [View&nbsp;5.1&nbsp;sample&nbsp;tree](GUIDE.md#run-51-layout) | 2 |
| **🔁&nbsp;5.2** | `verbose` | a `/spec/README.md`, Specs C, D and E and their checks, the A and B regression suite, and Specs A and B | [View&nbsp;5.2&nbsp;sample&nbsp;tree](GUIDE.md#run-52-layout) | 2 |
| **🔁&nbsp;5.3** | `verbose` | a `/spec/README.md`, Specs A, B, C, D, and E and their checks | [View&nbsp;5.3&nbsp;sample&nbsp;tree](GUIDE.md#run-53-layout) | 2 |
| **Total** |  |  |  | **22** |

### Analysis (Part 2)

The questions below are designed to be answered from your runs above. Read through them and
think about which runs from the table above you might want to compare to answer them. No extra
runs should be needed, but you may run more to support your answers, if you wish.

1) **Feedback:** Do you find that adding detailed output to the checks helps the agent?
2) **Tests as the spec:** Can the checks alone replace the written rules?
3) **Scale:** Do you find that a component passes fewer of its checks when the agent builds it together with other components, rather than on its own?
4) **Regression:**<br>
   a) Do you find that building components C, D, and E breaks features from A and B that were already working?<br>
   b) Does it make a difference whether the agent has Specs A and B or only their regression tests?<br>
   c) Does it make a difference whether the A and B checks are given as a regression suite or as plain checks?

For each question, say which runs you compared and why, give a table of the supporting
results, use the results to answer the question, and note how consistent your results are
across repeats. Add any other evidence you think might help explain your results. <mark>Answer these
in <a href="report.md">report.md</a>.</mark>

Happy experimenting!

> [!TIP]
> Keep in mind the following things as you are developing:
>
> - **Debug then "freeze" the spec:** <i>before your runs, you may want to do a few practice runs
>   with each spec to fix typos, broken checks, or unclear rules.</i>
>   - <i>Once you are confident in the spec, you must stop editing it. This means, for example,
>     that Spec C and its checks used in Runs 1.1, 2.1, 3, 4 and 5.x should all be the same.</i>
>   - <i>This is essential because if you edit specs across the runs, you won't know if your
>     gains are coming from your edits or from the setting you are testing.</i>
> - **Recall the role of `/spec/README.md`:** <i>it is the agent's entry point.</i>
>   - <i>It names every spec and check file, sets the order of work, tells the agent how to run
>     the checks, and says when it may submit.</i>
>   - <i>Make sure you are also instructing the agent to not edit the specs, checks, or other
>     files in `/spec`.</i>
> - **"Checks" means staged checks:** <i>as we specified above, a component's "checks" are, by
>   default, its staged checks, one per spec step.</i>
>   - <i>The only exception is the A and B regression suite (Runs 5.1 and 5.2), a single script
>     that holds every A and B rule.</i>
>   - <i>You can reuse the requests and helpers from your milestone 1 checks inside it (see
>     [the regression suite](GUIDE.md#the-regression-suite) in `GUIDE.md`).</i>
> - **Recall the role of regression checks:** <i>building C, D and E could change
>   code that A and B rely on (for example, `@here` adds to the same `mentions` list as
>   `@username`), so a step that passes its own check could still break something that already worked.</i>
>   - <i>In Runs 5.1 and 5.2, your `/spec/README.md` should tell the agent to rerun the A and B
>     regression suite (`checks/regression.sh`) after every step, not only at the end, and to fix anything they report before moving on.</i>
>   - <i>For an example, see [the regression suite](GUIDE.md#the-regression-suite) in `GUIDE.md`.</i>
> - **Setting up the testing modes:** <i>set the mode once in each component's `checks/lib.sh`
>   (see [`score_only` vs `verbose` testing mode](GUIDE.md#score_only-vs-verbose-testing-mode) in `GUIDE.md` and the example's
>   [`lib.sh`](tasks/slack-clone-course/student-files/example-spec/component-x-staged/checks/lib.sh)).</i>
>   - <i>The A and B regression suite always runs in `verbose` mode: when a check breaks, it
>     prints which rule failed and why (for example
>     `REGRESSION FAIL roles 2.3 duplicate group handle: expected 409, got 201`), not just a count.</i>
> - **Time limit:** <i>recall that mini-swe-agent cuts off any single shell command after 30 seconds by
>   default, so your checks must finish in under 30 seconds.</i>
> - **Be careful not to kill the agent itself:** <i>killing a process by name (`pkill -f`,
>   `killall`, `pgrep -f`, or parsing `ps`) can match the agent too. So checks for Component D must kill processes only by a
>   saved PID or by the PID that owns a port (found with `ss -ltnp`). Keep this in mind when
>   writing your specs, checks and `/spec/README.md`.</i>
> - **Dependent components:** <i>some rules, such as the API conventions in
>   [`COMPONENTS.md`](COMPONENTS.md) (auth headers, status codes, response envelopes), apply to
>   every endpoint in all three components. They are not a component of their own.</i>
>   - <i>How you include and test them is up to you. One option is to include them in each of
>     Specs C, D and E, and test them in each component's checks on that component's own
>     endpoints (for example, a request with no token returns `401`).</i>
>   - <i>Every run starts with an empty `/app`, so in Runs 1.1 to 4 (no Specs A and B), make
>     sure to tell the agent to build only the basics C, D and E depend on (login, workspaces,
>     channels, messages), not all of A and B.</i>
> - **Getting results for your analysis:** <i>you may write any other scripts you feel are
>   necessary for answering the Analysis questions, for example one that
>   reruns your checks on the agent's final code in `artifacts/app/` after a run. Keep them
>   outside `milestone-2-specs/`, so the agent does not see them.</i>
> - **Do earlier milestones still matter?** <i>Yes, please see the important tip at the top of
>   [Setup (Part 1)](#setup-part-1) on incorporating previous milestone requirements.</i>
> - **If you are stuck on how to write a spec:** <i>consider reviewing the examples in
>   Section 3 of the [`GUIDE.md`](GUIDE.md#3-example-specs). They are written in the Staged layout,
>   with their checks set to the `verbose` testing mode.</i>
> - **If you are unsure what is fixed and what is up to you in your spec:** <i>consider reviewing Section 4 of the
>   [`GUIDE.md`](GUIDE.md#4-the-layout).</i>

## 1. Set up (once)

> [!NOTE]
> You should already have done steps 1 to 3 (Docker, `uv` and your key) in milestone 0. They are
> here just in case. **Step 4 changes for milestone 2:** the spec link now points at
> `milestone-2-specs`.

1. **Docker**, usable without `sudo`. Install it from the
   [official guide](https://docs.docker.com/get-docker/) (Docker Desktop on macOS and Windows; it
   must be running whenever you run the agent). On Linux, also run
   `sudo usermod -aG docker $USER` and log back in. Check it with:

   ```bash
   docker run hello-world
   ```

2. **`uv`**, the Python package manager the scripts use:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv --version
   ```

3. **Your OpenAI key.** Put the OpenAI key the TAs gave you in `dev.env` at the repo root.
   From the repo root (on Linux, drop the `''` after `-i`):

   ```bash
   sed -i '' 's|^OPENAI_API_KEY=.*|OPENAI_API_KEY="<your-openai-key>"|' dev.env
   ```

   You will run the agent with `run_student_openai.sh`. <mark>Please continue using your OpenAI
   credits until you reach the limit. When your OpenAI credits run out, please inform the TAs right
   away, and they will give you instructions on how to keep running.</mark>

> [!WARNING]
> **Monitor your usage. No run should cost more than about $3.** The cost is printed at the end
> of every run (`COST: total=$...`). If you run out of OpenAI credits, inform the staff
> immediately.
>
> **Your credits are limited, so you cannot rerun as many times as you like.** Make each run
> count: before running again, fix every problem you found in the last run.

4. **Link the spec folder.** The run script refuses to start without this link:

   ```bash
   cd tasks/slack-clone-course/student-files
   ln -sfn ../course-staff-files/final_task/environment/milestone-specs/milestone-2-specs spec
   ```

   `spec` now points at `milestone-2-specs/`, the folder you write your spec in. The run copies
   it into the agent's container as `/spec`, and the agent is told to read `/spec/README.md`
   first.

> [!TIP]
> **If you see `No module named 'mini_swe_agent_local'`,** your Python environment is out of
> date. From the repo root, run `uv sync --reinstall-package long-horizon`, then run the agent
> again. If that does not fix it, run `rm -rf .venv && uv sync`.

## 2. Running the agent

Follow the run plan in [Setup](#setup-part-1). For every run:

1. Write the spec and checks in the Staged layout following [`GUIDE.md`](GUIDE.md), covering
   the lines in [`COMPONENTS.md`](COMPONENTS.md).
2. Put the spec and checks for that run directly in `milestone-2-specs/`
   (`tasks/slack-clone-course/course-staff-files/final_task/environment/milestone-specs/milestone-2-specs/`),
   with `README.md` at its top level. The run copies this folder into the agent's container as
   `/spec`, so that file becomes `/spec/README.md`. What it holds depends on the row of the run plan (the "In `/spec`" column).
3. Run the agent from `tasks/slack-clone-course/student-files`:

   ```bash
   ./run_student_openai.sh 2
   ```

4. Read the result (see [the section below](#3-reading-a-run) for how to read it). The agent runs your checks during the run,
   so look in the transcript for whether it ran them, what they printed last, and whether it
   submitted with failures still showing.
5. Fix the spec, not the code, before running again. Stay within your budget.

## 3. Reading a run

A run takes up to 50 minutes and ends with a table and a cost line:

```text
┏━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━┓
┃ Trials ┃ Exceptions ┃  Mean ┃
┡━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━┩
│      0 │          0 │ 0.000 │
└────────┴────────────┴───────┘
Results written to .../tasks/slack-clone-course/jobs/<timestamp>/result.json
COST: total=$<amount>
```

- **Stuck on `starting environment...`:** normal. The first Docker build can take up to 20
  minutes; later builds take seconds.
- **`Trials 0`, `Mean 0.000`:** expected. Ignore it.
- **`Exceptions 0`:** the agent ran and exited cleanly.
- **`COST: total=$...`:** what the run cost.
- **The run took a minute or two and cost very little:** the agent quit early. Run it again
  once; if it keeps happening, your spec or setup has a bug.

Results are in `tasks/slack-clone-course/jobs/<timestamp>/<trial>/`:

- **`agent/mini-swe-agent.txt`:** the full transcript. Check that the agent read your `/spec/README.md`,
  ran your checks, and what they printed last. Search it for your check scripts' file names to
  find each time the agent ran one, and for `PASS` or `FAIL` to see the results (see the tip in
  [GUIDE section 5](GUIDE.md#5-checks)).
- **`artifacts/app/`:** the code the agent wrote.
- **`result.json`:** timings, tokens and cost; `exception_info: null` means no crash.
- **`agent/format_errors.jsonl`:** malformed agent replies. A few are fine.

When a run goes wrong, look for a rule the agent misread, a rule your checks do not cover, or a
submit with failures still showing. Then change the spec.

## 4. What to submit

> [!IMPORTANT]
> **Submit the whole repo.** Make sure it contains:
>
> 1. **Your final specs and checks, in `milestone-2-specs/`:** exactly what the agent got in
>    Run 5.3: a `README.md` (the agent's `/spec/README.md`), your Specs A, B, C, D, and E, and the staged checks for all 5 (<i>note:
>    not the regression suite</i>). <mark>Staff will replay this folder as is against the hidden
>    tests.</mark>
> 2. **The `/spec` folder of each of the 11 runs:** for example in `spec-copies/<run>/`, so staff
>    can see exactly what the agent got in each run.
> 3. **The job folder of every repeat of each run (22 in total):** each
>    `tasks/slack-clone-course/jobs/<timestamp>/` folder. Each one holds the code the agent wrote
>    (`artifacts/app/`, the run's `/app`), the transcript, and `result.json`.
> 4. **`report.md`**, which must contain:
>    - **A table of every repeat of each run** (22 rows), with:
>      - the run (1.1 to 5.3), component, and repeat # (1 or 2)
>      - the job folder
>      - check results for each component in the run, for example `C: 18 passed, 3 failed`
>        (in Runs 5.1 and 5.2, A and B share one regression line, for example
>        `A and B: 1 broken`)
>      - cost
>      - how often the agent ran the checks
>      - whether it submitted with failures
>      - any problems
>    - **A section for each of the 4 questions** (answer 4a, 4b, and 4c separately), with:
>      - the runs you compared and why
>      - a table of the supporting results
>      - your answer
>      - how consistent your results are across repeats
>      - any other evidence that helps explain your results

A blank template is in [`report.md`](report.md) at the repo root. You may fill it in or use your
own format, as long as it has all the information above. As a reminder, you do not have access to
the hidden tests, so any results you report there will be from your
own checks.

### Before you submit: checklist

- [ ] You have read the important notes and tips above thoroughly.
- [ ] You did each of the 11 runs in the run plan with the right settings, with 2 repeats at minimum.
- [ ] You kept a copy of the `/spec` folder for each of the 11 runs.
- [ ] You kept the job folder of every repeat of each run (22 in total).
- [ ] You answered all four [Analysis](#analysis-part-2) questions in `report.md` and
  included the following to support your answers:
  - [ ] the runs you compared and why
  - [ ] a table of the supporting results, such as check results
  - [ ] a thorough, supported answer to the question
  - [ ] how consistent your results are across repeats
  - [ ] any additional, useful evidence that explains your results (e.g. evidence from the agent's transcripts)

## 5. Grading

Grading happens in this order:

1. **Required files (<mark>Pass</mark> or <mark>Fail</mark>):** your repo contains everything listed in
   the [What to submit](#4-what-to-submit) section above.
2. **`report.md` (20%):** your report contains every item listed for it in the
   [What to submit](#4-what-to-submit) section above.
3. **Hidden tests (80%):** staff run your Run 5.3 spec, starting from an empty
   `/app`, and run the hidden tests on the **whole app**, covering the four milestone 0 boot conditions
   and components A, B, C, D, and E.

> [!IMPORTANT]
> **Missing files mean a 0.** If any required file is missing, that part scores 0, and staff
> cannot run your specs, so the hidden tests (80% of the grade) cannot be scored either.

---

<h1 align="center">SWE-Marathon</h1>

<p align="center"><em>Can agents autonomously complete ultra-long-horizon software work?</em></p>


## Links

- [Website](https://www.swe-marathon.org/)
- [Paper](https://arxiv.org/abs/2606.07682)
- [Leaderboard](https://www.swe-marathon.org/#leaderboard)
- [Tasks](https://www.swe-marathon.org/#tasks)


## News
- [09/2026] 🚀 Featured on the [Qwen3.8-Max model card](https://x.com/Alibaba_Qwen/status/2094968708288680276?s=20)!
- [08/2026] 🔶 Featured on the [Hy4 preview model card](https://hy.tencent.ai/research/hy4-preview)!
- [08/2026] ⭐ Featured on the [GLM 5.3 model card](https://z.ai/blog/glm-5.3)!
- [08/2026] ⚙️ Featured on the [Grok 4.6 model card](https://media.x.ai/v1/website/card-4p6-4cd2dc57.pdf)!
- [07/2026] ➕ SWE-Marathon [v1.1 released](https://x.com/rishi_desai2/status/2087216551653146706?s=20)!
- [07/2026] 🌙 Featured on the [Kimi K3 model card](https://www.kimi.com/blog/kimi-k3)!
- [07/2026] 🔥 Featured on the [Grok 4.5 model card](https://x.ai/news/grok-4-5)!
- [06/2026] 🚀 Featured on the [GLM 5.2 model card](https://z.ai/blog/glm-5.2)!
- [06/2026] ⚡ SWE-Marathon [v1.0 released](https://x.com/rishi_desai2/status/2062930906818769356?s=20)!


## Citation

```bibtex
@misc{swemarathon_2026,
  title        = {{SWE-Marathon: Can Agents Autonomously Complete Ultra-Long-Horizon Software Work?}},
  author       = {Rishi Desai and Jesse Hu and Joan Cabezas and Neel Harsola and Pratyush Shukla and Daniel Wang and Xiangyi Li and Roey Ben Chaim and Adnan El Assadi and Omkaar Mukund Kamath and Fenil Faldu and Prannay Hebbar and Jiankai Sun and Yiyuan Li and Pramod Srinivasan and Ishan Gupta and Christopher Settles and Derek Chen and Pranav Raja and Albert Liu and Marek Šuppa and Nevasini Sasikumar and Luyang Kong and Erik Quintanilla and Ivan Bercovich and Steven Dillmann},
  year         = {2026},
  howpublished = {\url{https://arxiv.org/abs/2606.07682}},
  note         = {arXiv:2606.07682}
}
```

## License

[Apache License 2.0](LICENSE)
