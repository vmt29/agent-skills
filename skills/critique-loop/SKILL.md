---
name: critique-loop
description: 'A task-first cross-model critique-loop working mode with two loops in one session. On /critique-loop Claude proposes a brief (task, mode, convergence criteria) for the user to confirm or edit. Full mode — Claude drafts a plan (committed to docs/plans/<slug>.md by default) → Codex critiques the plan in a loop → user approves → Claude implements with conventional commits against a recorded baseline SHA → the SAME persistent Codex session critiques the diff in a loop. Each loop runs up to 16 rounds (32 total in full mode). Direct mode — do the task, then loop (small tasks, docs; 16 rounds). Codex critiques read-only at the maximal configured model and maximal effort (auto-resolved as the higher of the codex-config tier and xhigh — e.g. ultra on gpt-5.6-sol); Claude does all fixing; product/architecture findings are surfaced to the user via needs_human, never silently decided. Every iteration is visualized (severity table, convergence trajectory, provenance incl. session id); HTML dashboard; final report includes disagreements and user decisions. Not a PR review. Trigger with /critique-loop or "critique loop", "loop with codex", "converge with codex".'
---

# Critique Loop (v2)

A **working mode**, not a PR review. You (Claude) author and repair; **Codex is the
independent second-model critic** — it runs **read-only and never edits**; **you apply
every fix**. Two models converge on one artifact against user-confirmed criteria.

Two modes, chosen at intake:

- **full** (default for code changes and anything serious) — two loops in one session:
  **plan loop** → **user approval gate** → **implement loop**. One persistent Codex
  session spans both loops, so the critic that approved the plan reviews the code and
  can catch drift from the approved plan.
- **direct** (small tasks, standalone docs, work that already exists) — the classic
  single loop: do the task, then converge.

## Fixed parameters
- **Critic:** OpenAI Codex CLI, **one persistent session per run** — the helper starts
  it on the first `exec` call and resumes it on every later call (`session-id` file is
  auto-managed). **Model = the maximal model available to you** — inherits Codex's
  configured default (auto-upgrades when your default does; `CODEX_MODEL=` pins one).
  **Effort = `xhigh` by default** (owner decision 2026-07-12: ultra's marginal
  critique quality did not justify its wall-clock and cost; the codex config's
  `model_reasoning_effort` is deliberately not consulted). `CODEX_EFFORT=` pins any
  tier explicitly (e.g. `ultra` for an especially high-stakes run). Preview what will
  run: `~/.claude/skills/critique-loop/critique-loop-run.sh info`. The helper prints
  and stamps *provenance* (exact CLI version + effective model + effort + session
  state) — always surface it.
- **Max rounds: 16 per loop** — plan loop ≤ 16 and implement loop ≤ 16 (up to 32 total
  in full mode; direct mode ≤ 16). Buffer files are numbered **continuously** across
  the run (`prompt-1 … prompt-32`); rounds are counted per loop. Stop the instant the
  criteria are met — don't over-polish.
- **Codex is read-only; Claude does all fixes.** Never change the model, effort, or
  mode to coerce a different verdict.

## Setup (first use only)
```bash
chmod +x ~/.claude/skills/critique-loop/critique-loop-run.sh
```

## Bash execution rules
- Run each command as a **separate** Bash invocation (no `&&` / `;`) so failures stay
  visible. Exception: the `git rev-parse … > plan-sha` baseline capture is one command.
- **Timeout: pass `timeout: 600000`** (the 10-min cap) on every critique call. At
  max effort a large artifact can exceed it; if so, launch the helper with
  `run_in_background: true` and read the buffer when you're re-invoked on completion.
- **Buffers are per-run:** prefix **every** helper call with
  `CRITIQUE_LOOP_DIR=/tmp/critique-loop/<slug>` (env does not persist between Bash
  calls). `<slug>` = current git branch name, else a kebab-case id from the task.
  Buffers live under `/tmp`, **never inside the repo**.
- Reusing a slug from an earlier run? Run
  `CRITIQUE_LOOP_DIR=/tmp/critique-loop/<slug> ~/.claude/skills/critique-loop/critique-loop-run.sh reset`
  first — it clears stale buffers **and the old session id**.

---

## Procedure

### Phase A — Intake (propose the brief, get confirmation)
On `/critique-loop`, **do not start working yet.** Propose a short brief:

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
2. **Run Codex** (foreground, `timeout: 600000`):
   ```bash
   CRITIQUE_LOOP_DIR=/tmp/critique-loop/<slug> CRITIQUE_PHASE=plan ~/.claude/skills/critique-loop/critique-loop-run.sh N
   ```
   The first call **starts the persistent session** — check the banner says
   `session … (started)`. If it says `NOT captured`, tell the user and continue
   stateless (degraded but valid).
3. **Visualize** (see *Visualization*) before changing anything.
4. **Classify every finding** before touching the plan:
   - **Claude-resolvable** — missing edge case / error path, unclear or mis-ordered
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
**uncommitted** (so diffs and `codex review --uncommitted` can see them); for a
document, just write the file. Then loop rounds 1 … 16 exactly as in F2 —
**Template D** first, **Template R** after, `CRITIQUE_PHASE=direct` and
`CRITIQUE_MAX=16` on helper calls, same session persistence, classification (incl.
needs-human pauses), visualization, convergence, and project checks each iteration.
Do not commit unless the user asks.

Helper `review` mode (`critique-loop-run.sh N review`) remains available for pure
code-diff runs — it invokes Codex's purpose-built reviewer, but it is **stateless**
(no session). Prefer `exec` when continuity matters. `REVIEW_BASE=<ref>` switches it
from `--uncommitted` to `--base <ref>`.

---

## Stuck, oscillation, disagreements (both modes)
- **Same finding persists 3 iterations** with no movement → stop, surface to the user —
  your revisions aren't landing; something is miscommunicated.
- Every remaining finding is oscillating/skipped, or the set isn't shrinking despite
  fixes → stop early and report.
- **Oscillating critic** (flip-flops between rounds) → don't thrash. Document both
  opinions, mark resolved, ignore thereafter:
  `NOTE(critique-loop): Codex oscillated. Iter N said X; iter M said Y. Keeping <choice> because …`
- **Disagreements** → `TODO(critique-loop):` note + ledger; **reported in the final
  summary even if empty**.
- Missing `=== VERDICT ===` **twice in a row** → surface the raw output, stop.
- Helper / CLI failure (auth expired, network, crash) → report the exact error; do not
  retry blindly.
- `session-id` empty after the first call → tell the user; continue stateless.
- The user's answer to a surfaced question is itself ambiguous → re-ask before resuming.

## Visualization (every iteration + version)
Always render, right after running Codex:

**1 — Provenance line** (from the helper's banner — includes the session state):
```
🤖 codex 0.144.1 · model gpt-5.6-sol · effort ultra · session 019f…a4d3 (resumed) — iter 3/32 · plan round 2/16
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
   codex {version, model, effort, session}, outcome, convergedAt, `gateAfter` = last
   plan iteration, iterations[] with `phase`, findings[] — resolution
   `user-decided` for needs-human items — amendments[]).
2. To let the user **see it**: publish via the **Artifact** tool (hosted URL) — the
   file is body-only and Artifact-ready — or `open` it locally.
3. Update it at the end by default; refresh live each iteration if the user watches.

## After the loop — Final summary (always give this)
Concise and skimmable:
- **What I did** — task, mode, and the substantive changes made to converge.
- **Iterations** — e.g. "plan converged in 2 rounds, implementation in 5 — 7 rounds
  total (max 16 per loop)" (or stuck / hit the cap / timed out).
- **Plan** — file path (and that it's committed, in repo mode), rounds, baseline
  `plan-sha` → final HEAD.
- **User decisions** — every needs-human question surfaced and the user's answer.
- **Disagreements with Codex** — every override: the finding, why you kept your
  approach, where documented (`TODO(critique-loop)` / `NOTE(critique-loop)`), plus
  oscillations and how resolved. **Required even if empty.**
- **Fixed** — findings addressed (file + one line each).
- **Residual** — anything still open if not fully converged.
- **Codex provenance** — version / model / effort / session id, and the dashboard link.

## How this differs from a PR review / other critique tools
- **Two loops, one critic memory:** plan and implementation are separately converged,
  but one persistent Codex session spans both — the code reviewer remembers what it
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
- **Clean separation:** Codex critiques (read-only), Claude fixes.
- **Not a PR review.** The loop ends with a branch ready for a PR; the PR reviewer
  should be a *fresh-eyes* bot in CI (it has no shared context with this session),
  with a babysit-style skill handling its comments. Out of scope here.
