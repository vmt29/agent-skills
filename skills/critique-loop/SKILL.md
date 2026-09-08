---
name: critique-loop
description: 'Task-first critique loop between Claude Code and Codex, in either direction. The active agent programs; the other reviews read-only in one persistent session. Codex reviews use GPT-6 Astra at max effort; Claude Code reviews use Fable 5.1 at max effort. Full mode converges a plan, gets user approval, then converges the implementation; direct mode reviews existing work. Includes checkable criteria, human decision gates, and a dashboard. Use for critique loop, loop with codex, loop with claude, or converge with either agent. Not a PR review.'
---

# Critique Loop (v3)

A **working mode**, not a PR review. You, the agent running this skill, are the
**programmer**: author and repair the work. The other CLI is the independent
**reviewer**: it runs **read-only and never edits**. You apply every fix.

| Programmer (current host) | Reviewer | Pinned review model | Effort |
|---|---|---|---|
| Claude Code | Codex CLI | `gpt-6-astra` (GPT-6 Astra) | `max` |
| Codex | Claude Code CLI | `claude-fable-5-1` (Fable 5.1) | `max` |

Set `CRITIQUE_PROGRAMMER=claude` in Claude Code or `CRITIQUE_PROGRAMMER=codex`
in Codex on **every helper call**; the helper chooses the opposite reviewer.
It also recognizes `CODEX_THREAD_ID` / `CLAUDECODE` when called without an explicit
role, and preserves the original Codex-reviewer default when neither is present.
If both host markers are present, specify the programmer explicitly.
These settings select the **reviewer**; they do not change the active programmer's model.

Two modes, chosen at intake:

- **full** (default for code changes and anything serious) — two loops in one session:
  **plan loop** → **user approval gate** → **implement loop**. One persistent reviewer
  session spans both loops, so the critic that approved the plan reviews the code and
  can catch drift from the approved plan.
- **direct** (small tasks, standalone docs, work that already exists) — the classic
  single loop: do the task, then converge.

## Fixed parameters
- **One persistent reviewer session per run.** The helper starts it on the first
  `exec` call and resumes its exact ID on every later call. Codex uses `exec resume`;
  Claude Code uses `--resume`. `session-id` and `run-config.json` are auto-managed.
  The latter locks the programmer, reviewer, model, effort, mode, and working directory.
  Reuse the same values throughout both loops; do not share a run directory between runs.
  The reported downgrade procedure below is the exception: it starts a fresh session
  and re-seeds the accepted context instead of silently changing the existing reviewer.
- **Pinned defaults:** GPT-6 Astra + `max` for Codex; Fable 5.1 + `max` for Claude Code.
  `max` is the literal CLI effort value. User config defaults do not select the model
  or effort. Explicit alternatives use `CODEX_MODEL` / `CODEX_EFFORT` or
  `CLAUDE_MODEL` / `CLAUDE_EFFORT`, only when the user requests them and before a run
  starts, or for the authorized downgrade sequence below.
  `CRITIQUE_REVIEWER=codex|claude` is an alternative to setting the programmer;
  if both are set they must name different agents. Preview with `info` (below).
- **Read-only review:** Codex uses `sandbox_mode="read-only"` on start and resume.
  Claude Code uses `--safe-mode`, only `Read,Glob,Grep`, and disabled MCP tools;
  customizations and hooks are disabled. It has no shell or editing tools. The helper
  supplies `git-context-N.txt` with status, tracked diff (staged and unstaged), and
  the baseline commit log. The reviewer can read untracked files listed in the status.
  For code rounds, the baseline comes from `plan-sha`; `REVIEW_BASE` can select a ref
  explicitly. The programmer runs all tests and supplies their results in the prompt.
- **Provenance:** surface the reviewer CLI version, requested model and effort,
  session state, and any observed model usage returned by the CLI. A terminal CLI error,
  unexpected model, or lost session is a failed pass, never a converged verdict.
- **Live monitoring:** check reviewer activity and API errors at least every
  **60 seconds** until it exits. Base every user-facing status on a fresh check,
  with its timestamp and current elapsed time; never recycle an earlier estimate.
- **Max rounds: 16 per loop** — plan loop ≤ 16 and implement loop ≤ 16 (up to 32 total
  in full mode; direct mode ≤ 16). Buffer files are numbered **continuously** across
  the run (`prompt-1 … prompt-32`); rounds are counted per loop. Stop the instant the
  criteria are met — don't over-polish.
- **The reviewer is read-only; the programmer does all fixes.** Never change the model, effort, or
  mode to coerce a different verdict.

## Downgrades: report first, effort first

Do not accept an automatic model substitution. The helper passes
`switchModelsOnFlag: false` and an empty `fallbackModel` chain to Claude Code on
every call; in non-interactive mode a flagged request ends with a refusal instead
of silently switching. It also checks the models reported in `modelUsage` and
rejects a substituted or unidentified reviewer, even if its output says converged.
**Never accept Opus 4.8**, whether suggested by the CLI or found in a returned result.

When a model or effort downgrade is needed, use this owner-authorized sequence:

| Reviewer | Initial request | First retry | Second retry |
|---|---|---|---|
| Codex | GPT-6 Astra `max` | **GPT-6 Astra `xhigh`** | Stop and report; no model downgrade authorized |
| Claude Code | Fable 5.1 `max` | **Fable 5.1 `xhigh`** | **Opus 5 `xhigh`** (`claude-opus-5`) |

1. **Report before retrying:** the requested model/effort, the actual error or proposed
   substitution, that its output was rejected, and the exact next settings. The
   sequence above is already authorized; do not ask for permission again for these steps.
2. Only advance after the preceding settings failed or were reported unavailable;
   do not skip the effort reduction. Set `CODEX_EFFORT=xhigh`, or
   `CLAUDE_EFFORT=xhigh`, keeping the original model first. If Fable 5.1 at `xhigh`
   also fails, set `CLAUDE_MODEL=claude-opus-5 CLAUDE_EFFORT=xhigh`.
3. Use a **fresh run directory** (e.g. `<slug>-astra-xhigh`, `<slug>-fable-xhigh`,
   `<slug>-opus5-xhigh`), preserving the failed run's buffers. Copy `plan-sha` when
   present, and provide a complete first prompt with the task, criteria, approved
   plan, user decisions, accepted findings, and current changes. Do not reuse a
   rejected reviewer response as accepted context. Session continuity restarts
   explicitly; retain the overall round counts and log the change as an amendment.
4. Record every downgrade and its reason in the dashboard and final provenance.
   Stop after the last allowed step fails. Auth, network, permission, or safety
   failures need their own resolution; this ladder is not a way around a refusal
   or a permission denial. Never enable permission bypass or automatic fallback.

The helper reports the next allowed settings after a failed pass, but **never
performs the downgrade itself**. See the official
[Claude Code fallback behavior](https://code.claude.com/docs/en/model-config#ask-before-switching).

## Setup and execution
Requires Python 3.9+ (standard library only) and an authenticated reviewer CLI.
Use a current Codex CLI with GPT-6 Astra access, or Claude Code **2.1.255+** with
Fable 5.1 access and the `--safe-mode` flag. If the selected model or effort is
unavailable, report the CLI error; do not downgrade silently.

Resolve `<skill-dir>` to the directory containing **this loaded SKILL.md**. It may
be under `~/.claude/skills`, `~/.agents/skills`, `~/.codex/skills`, or a repo checkout.
Invoke the shell entry point with `bash`; no executable-bit setup is required.
The entry point calls the adjacent `critique-loop-run.py`, so keep both files together.

Preview the two directions without making a model request:
```bash
CRITIQUE_PROGRAMMER=claude bash "<skill-dir>/critique-loop-run.sh" info
CRITIQUE_PROGRAMMER=codex bash "<skill-dir>/critique-loop-run.sh" info
```

- Run each helper call as a separate shell invocation so failures stay visible.
- **In Claude Code:** start long reviewer calls with `run_in_background: true`
  and monitor them every minute until completion. Do not let a short frontend
  timeout terminate an otherwise healthy reviewer.
- **In Codex:** start with `exec_command` and a short `yield_time_ms`; retain its
  process `session_id` and poll with `write_stdin` until it exits. A running process
  is not a failed critique. Keep the user informed while waiting and never launch
  a duplicate call merely because the tool yielded before completion.
- **Buffers are per-run:** prefix **every** helper call with
  `CRITIQUE_PROGRAMMER=<programmer> CRITIQUE_LOOP_DIR=/tmp/critique-loop/<slug>`
  (env does not persist between calls). Replace `<programmer>` with `claude` or
  `codex` from the table. `<slug>` = a filesystem-safe branch/task ID unique to this run.
  Buffers live under `/tmp`, **never inside the repo**.
- Reusing a slug from an earlier run? Run
  `CRITIQUE_LOOP_DIR=/tmp/critique-loop/<slug> bash "<skill-dir>/critique-loop-run.sh" reset`
  first — it clears stale buffers, **the old session ID, and its configuration**.

### Reviewer activity and API errors — required

The helper uses Claude's `stream-json` output with partial-message events and
API diagnostics. It prints a fresh heartbeat at least once per minute, reports
API error events immediately, and updates `$CRITIQUE_LOOP_DIR/status.json`
while the process runs. The adjacent `reviewer_activity.py` is part of the helper;
keep it with `critique-loop-run.py` when moving or copying the skill.

Poll the running tool/process at least every 60 seconds. Immediately before
answering a status question, request a fresh status:

```bash
CRITIQUE_PROGRAMMER=<programmer> CRITIQUE_LOOP_DIR=/tmp/critique-loop/<slug> \
  bash "<skill-dir>/critique-loop-run.sh" status
```

Report the checked-at time, process liveness, elapsed time, age/type of the last
observable activity, and whether API errors or retries have appeared. The status
command recalculates ages at query time and marks a running monitor stale after
60 seconds without an update. A stale monitor is not proof that the reviewer is
dead: inspect the actual process and fresh raw/API logs before drawing conclusions.
Distinguish tool errors (such as a missing file) from API/auth/network failures.

**Silence is not failure.** Single-result JSON can stay empty until the entire
review ends; even a live stream may pause during a long model request. Thinking
events, tool activity, output growth, and API retries are evidence of activity.
If none is visible, report that the process is alive with no recent observable
output. Do not claim it is actively thinking, kill it, restart it, or downgrade
its effort solely because time has elapsed or the result file is empty.

When an actual API error occurs, identify its category/status and investigate:
allow an already-running CLI retry to finish; for transient rate limits, server
errors, or connection timeouts, honor backoff/Retry-After and retry the same model,
effort, and session after the failed process has exited. Resolve authentication,
permission, network, or configuration faults through their appropriate mechanism;
model downgrades do not fix those. Preserve failed buffers, use the next iteration
number, and never start a duplicate live reviewer. If the same error survives two
manual retries without a new diagnosis or fix, stop retrying and report the concrete
blocker. Only use the separate downgrade sequence for an evidenced model/effort
availability problem or an explicit user request.

If an API failure precedes the first validated verdict, the observed session ID
is in the live status/init event. Verify it against that run's configuration before
resuming it; an observed ID does not by itself validate a review or its model.

Summarize observable activity rather than exposing private reasoning. Raw streams
and `api-N.log` may contain private text or credentials: do not paste them into
chat or the dashboard. The helper's status contains only counters, timestamps,
safe error categories, and model/session metadata. A progress heartbeat is not a
verdict; normal session/model checks and convergence criteria still apply.

---

## Procedure

### Phase A — Intake (propose the brief, get confirmation)
On `/critique-loop` in Claude Code, `$critique-loop` in Codex, or an equivalent
natural-language invocation, **do not start working yet.** Propose a short brief:

1. **Task** — one or two concrete sentences, inferred from any argument after the
   command and the conversation. Nothing to infer → ask what to work on.
2. **Mode** — `full` or `direct`, with your recommendation (full for code /
   multi-file / anything serious; direct for small, doc-only, or already-existing work).
3. **Convergence criteria** — concrete and *checkable*. Full mode has two sets:
   - **Plan criteria** (default: "no blocking or major findings; no open `needs_human`
     items; the plan's Open questions are resolved; verification steps are concrete").
   - **Implementation criteria** (default: "no blocking or major findings; project
     checks pass; the implementation matches the approved plan").
4. **Plan location** (full mode) — **default: repo mode**, `docs/plans/<slug>.md`,
   committed (`docs:` commits) so the plan is a first-class design doc reviewable in
   the PR. Offer **ephemeral mode** (`$CRITIQUE_LOOP_DIR/plan.md`, never committed)
   for throwaway work or when not in a git repo.

**Optional grilling — only when the task has load-bearing ambiguity** (an open decision
that changes the approach: scope boundary, per-user vs global behavior, data source,
compatibility target). Interview the user **one question at a time**, each with your
recommended answer; stop as soon as the task is concrete. If it's already concrete,
skip — don't perform ceremony.

Present the brief plainly and ask the user to **confirm or edit**. Record the agreed
task + mode + criteria — they drive every convergence decision.

### Living task & criteria (amendable any time)
The user may amend the task or criteria mid-session. At the next iteration boundary:
- Update the record and **re-baseline**: convergence is judged against the *new*
  criteria from then on.
- Log an **amendment** (`{iter, note}`) — surface it in the iteration output and the
  dashboard's `amendments` list. In repo mode, commit any resulting plan revision.
- Do whatever extra work the amended task requires before the next critique.
After each iteration, briefly remind the user they can amend task or criteria.

---

## FULL MODE

### Step F1 — Draft the plan
Explore the repo enough to write a concrete plan (real paths, existing patterns to
match). **Don't implement yet.** Write the plan to the configured location, sections:

- **Task** — one paragraph in your own words.
- **Context** — what the repo constrains (existing patterns, relevant files, prior art).
- **Approach** — numbered steps at the level of "edit function X in file Y to do Z".
- **Files to touch** — paths with a one-line reason each.
- **Verification** — how you'll know it worked (commands, manual checks).
- **Open questions** — things the user or critic should weigh in on; don't invent answers.

Repo mode: `mkdir -p docs/plans`, commit as `docs: plan for <slug>`; each later
revision commits as `docs: revise plan for <slug> (round N)`.

### Step F2 — Plan loop (rounds 1 … 16)
For each round (buffer/iteration number **N** keeps counting across the whole run):

1. **Write the prompt** to `$CRITIQUE_LOOP_DIR/prompt-N.txt` — **Template P** on the
   first iteration, **Template R** afterwards (see `critique-prompt-template.md`).
   Include the plan path, the task summary, the **plan criteria verbatim**, and (R)
   what changed since the last round.
2. **Run the reviewer** using the host-specific execution rules above:
   ```bash
   CRITIQUE_PROGRAMMER=<programmer> CRITIQUE_LOOP_DIR=/tmp/critique-loop/<slug> CRITIQUE_PHASE=plan bash "<skill-dir>/critique-loop-run.sh" N
   ```
   The first call **starts the persistent session** — check the banner says
   `session … (started)`. A nonzero exit or missing session ID stops the loop;
   inspect `raw-N.txt` and report the error before continuing.
3. **Visualize** (see *Visualization*) before changing anything.
4. **Classify every finding** before touching the plan:
   - **Programmer-resolvable** — missing edge case / error path, unclear or mis-ordered
     steps, wrong paths or names, missing verification, a simpler alternative that
     preserves the user's goal, in-task scope trim → fix in the plan.
   - **needs-human** — product decisions, architecture tradeoffs with no clear right
     answer, business rules, anything outside the repo → **pause the loop**, present
     each (the ask · why it matters · the critic's suggestion · your recommendation),
     wait for the answer, then incorporate it. Log it as a **user decision** (reported
     at the gate and in the final summary). A verdict `needs_human:` ≠ `none` triggers
     this pause even with zero findings.
   - **Disagree** — do NOT silently comply: leave a `TODO(critique-loop):` note in the
     plan stating why, and record a **disagreement**.
5. **Decide convergence — your call**, applying the plan criteria *literally*. Met →
   Step F3. Not met → fix, commit the revision (repo mode), next iteration.

### Step F3 — User approval gate
The critic is satisfied; the user has final say before implementation. Present:

- **Slug** and current branch.
- **One sentence** — what will be implemented.
- **Decisions made during plan rounds** — user answers that shaped the plan (skip if none).
- **Open questions still unresolved** — from the plan file. If any remain, list them —
  do **NOT** silently proceed past unresolved open questions.
- **Plan file path** (the user can open it before saying go).
- **Explicit prompt:** "Ready to implement? Say 'go', or tell me what to change."

Then:
- **Go** → first, if any plan decision is **hard to reverse + surprising without
  context + the result of a real trade-off** (all three), offer to record it as an ADR
  (`docs/adr/`, or the repo's convention) — most plans don't warrant one. Then commit
  any final plan revision (repo mode) and record the baseline:
  ```bash
  git rev-parse HEAD > /tmp/critique-loop/<slug>/plan-sha
  ```
  Everything after this SHA is "the implementation". → Step F4.
- **Minor edits** (wording, small scope trim, preference tweaks) → revise the plan,
  re-present. **No critic round** — these are user-preference edits.
- **Substantive changes** (new requirement, different approach, a concern the critic
  never saw) → revise the plan and run **one more plan-loop round (F2)** — the design
  shifted; the critic should re-see it before you build it. Unsure which? Prefer the
  critic round.
- **Stop / pivot** → stop. The plan stays on disk (committed in repo mode).

### Step F4 — Implement
Implement **exactly** the approved plan. Scope discipline: a minor oversight in the
same area → fix it and note it for the critique; a real scope question → stop and ask
the user. Use **conventional commits** (`feat:`, `fix:`, `refactor:`, …), one logical
change per commit. (A standalone doc/artifact with nothing to commit may stay
uncommitted — critiques then target file contents instead of the diff.) Run the
project's checks (lint / typecheck / tests) and fix breakage **before every critique
round**.

### Step F5 — Implement loop (rounds 1 … 16; buffer numbering continues from the plan loop)
Same cycle as F2, with these changes:

- First iteration: **Template I** — the critic already knows the plan (same session);
  the prompt hands it the baseline: run `git log <plan-sha>..HEAD --oneline` and
  `git diff <plan-sha>` (includes uncommitted changes) and verify **(a)** the
  implementation matches the approved plan, **(b)** no scope creep, **(c)** no bugs /
  regressions / missed edge cases, **(d)** tests are adequate. Include the
  **implementation criteria verbatim**. Later iterations: **Template R**.
- Helper calls use `CRITIQUE_PHASE=code`. Banner must say `(resumed)` with the same
  session id — if it started a new session, something is wrong; check `session-id`.
- Fixes are real commits (`fix:`, `test:`, …) — they are the PR's actual work.
- Same classification, visualization, amendment, and convergence handling. Converged →
  **Final summary**. Round 16 of this loop unconverged → apply the fixes you're
  confident in, stop, and report the residual honestly.

---

## DIRECT MODE (the classic loop)
**Phase 0 — do the task** to best first-pass quality. Leave code changes
**uncommitted** (so the reviewer can inspect the diff); for a
document, just write the file. Then loop rounds 1 … 16 exactly as in F2 —
**Template D** first, **Template R** after, `CRITIQUE_PHASE=direct` and
`CRITIQUE_MAX=16` on helper calls, same session persistence, classification (incl.
needs-human pauses), visualization, convergence, and project checks each iteration.
Do not commit unless the user asks.

Helper `review` mode (`critique-loop-run.sh N review`) remains available for pure
code-diff runs **when the reviewer is Codex** — it invokes Codex's purpose-built
reviewer, but it is **stateless** (no session). Prefer `exec` when continuity matters.
`REVIEW_BASE=<ref>` selects a base ref instead of the default uncommitted scope.
Claude Code reviews always use `exec`.

---

## Stuck, oscillation, disagreements (both modes)
- **Same finding persists 3 iterations** with no movement → stop, surface to the user —
  your revisions aren't landing; something is miscommunicated.
- Every remaining finding is oscillating/skipped, or the set isn't shrinking despite
  fixes → stop early and report.
- **Oscillating critic** (flip-flops between rounds) → don't thrash. Document both
  opinions, mark resolved, ignore thereafter:
  `NOTE(critique-loop): Reviewer oscillated. Iter N said X; iter M said Y. Keeping <choice> because …`
- **Disagreements** → `TODO(critique-loop):` note + ledger; **reported in the final
  summary even if empty**.
- Missing `=== VERDICT ===` → the pass fails; surface the raw output. If a corrected
  prompt still yields no verdict on the next attempt, stop.
- Helper / CLI failure (auth expired, network, crash) → report the exact error; do not
  retry blindly.
- Missing or changed session ID → stop and report; do not silently restart stateless.
- Run configuration mismatch → use a fresh run directory or reset intentionally;
  never send a Codex session ID to Claude Code, or vice versa.
- The user's answer to a surfaced question is itself ambiguous → re-ask before resuming.

## Visualization (every iteration + version)
Always render, right after running the reviewer:

**1 — Provenance line** (from the helper's banner — includes the session state):
```
🤖 codex 0.153.4 · model gpt-6-astra · effort max · session 019f…a4d3 (resumed) — iter 3/32 · plan round 2/16
🤖 claude 2.1.263 · model claude-fable-5-1 · effort max · session 019f…b5e4 (resumed) — iter 3/32 · plan round 2/16
```

**2 — Findings** as a severity-badged table (🔴 blocking · 🟠 major · 🟡 minor · ⚪ nit),
with a 🙋 marker on needs-human rows:
```
| # | severity     | location        | issue                          | fix / decision            |
|---|--------------|-----------------|--------------------------------|---------------------------|
| 1 | 🔴 blocking  | auth/token.py:42| await missing on verify()      | add `await`               |
| 2 | 🟠 major 🙋  | plan.md §3      | default on or off? product call| → asked user              |
```

**3 — Convergence tracker** — grows one row per iteration; phases visible:
```
| iter | phase | 🔴 | 🟠 | 🟡 | ⚪ | 🙋 | status            |
|------|-------|----|----|----|----|----|-------------------|
|  1   | plan  | 1  | 3  | 2  | 1  | 1  | ❌ not converged  |
|  2   | plan  | 0  | 0  | 1  | 1  | 0  | ✅ plan converged |
|  —   | plan approved · baseline abc1234 ————————————————— |
|  3   | code  | 0  | 2  | 1  | 0  | 0  | ❌ not converged  |
```
Then one line: are the current criteria met, and why / why not.

### Dashboard (visual, "check with your eyes")
Maintain the HTML dashboard from `dashboard-template.html`:
1. Copy the template to `/tmp/critique-loop/<slug>/dashboard.html` and **overwrite the
   `RUN` object** with the run's real data (task, criteria, operator "vmt29", mode,
   programmer ("Claude Code" or "Codex"), reviewer {cli, version, model, effort, session}
   (`cli` is `codex` or `claude`), outcome, convergedAt, `gateAfter` = last
   plan iteration, iterations[] with `phase`, findings[] — resolution
   `user-decided` for needs-human items — amendments[]).
2. Link the local HTML file so the user can open it. If an Artifact tool is available
   and publishing is authorized, it also accepts this body-only template.
3. Update it at the end by default; refresh live each iteration if the user watches.

## After the loop — Final summary (always give this)
Concise and skimmable:
- **What I did** — task, mode, and the substantive changes made to converge.
- **Iterations** — e.g. "plan converged in 2 rounds, implementation in 5 — 7 rounds
  total (max 16 per loop)" (or stuck / hit the cap / timed out).
- **Plan** — file path (and that it's committed, in repo mode), rounds, baseline
  `plan-sha` → final HEAD.
- **User decisions** — every needs-human question surfaced and the user's answer.
- **Disagreements with the reviewer** — every override: the finding, why you kept your
  approach, where documented (`TODO(critique-loop)` / `NOTE(critique-loop)`), plus
  oscillations and how resolved. **Required even if empty.**
- **Fixed** — findings addressed (file + one line each).
- **Residual** — anything still open if not fully converged.
- **Reviewer provenance** — version / model / effort / session id, and the dashboard link.
  Include every rejected automatic substitution and each reported effort/model downgrade.

## How this differs from a PR review / other critique tools
- **Two loops, one critic memory:** plan and implementation are separately converged,
  but one persistent reviewer session spans both — the code reviewer remembers what it
  approved and why.
- **Interactive intake + living criteria:** the user confirms task, mode, and
  *checkable* convergence criteria up front and may amend them mid-session
  (re-baselined, logged).
- **User-defined convergence** is the primary stop condition; verdict counts,
  stuck/oscillation detection, and the 16-iteration cap are the safety nets.
- **Nothing is silently decided:** needs-human findings pause the loop; the user's
  answers are first-class artifacts in the gate and the final report.
- **Visualized + versioned:** severity trajectory, provenance with session id, HTML
  dashboard, and a required disagreements section.
- **Clean separation:** the opposite agent critiques (read-only); the programmer fixes.
- **Not a PR review.** The loop ends with a branch ready for a PR; the PR reviewer
  should be a *fresh-eyes* bot in CI (it has no shared context with this session),
  with a babysit-style skill handling its comments. Out of scope here.
