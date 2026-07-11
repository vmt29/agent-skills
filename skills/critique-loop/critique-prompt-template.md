# Critique prompt templates (v2)

Fill the `<…>` placeholders and write the result to `$CRITIQUE_LOOP_DIR/prompt-<N>.txt`
before running `critique-loop-run.sh <N>`. Keep the `=== VERDICT ===` block exactly.

Numbering: `<N>` (files, helper calls) counts **continuously across the whole run**
(1…32 in full mode); `<K>` (the round shown to the critic) counts **per loop**, 16 max
each — plan round 3 and implementation round 3 are different `<N>`s.

Pick the template by where you are:

| Template | When |
|---|---|
| **P** — plan critique | Full mode, first call of the plan loop (starts the session) |
| **I** — implementation critique | Full mode, first call after the user approved the plan |
| **R** — re-review | Any loop, every iteration after the first of that loop |
| **D** — direct critique | Direct mode, first call |

The session persists across all `exec` calls of a run, so P/I/R build on each other —
never re-paste what the critic has already seen; reference it.

## Shared verdict block

Every template ends by requiring EXACTLY this block, nothing after it:

```
=== VERDICT ===
converged: yes | no
blocking: <int>
major: <int>
minor: <int>
nits: <int>
needs_human: none | <one line — the decision only the human operator can make>
rationale: <one line: are the convergence criteria met, and why / why not>
```

`needs_human` is for product decisions, architecture tradeoffs with no clear right
answer, business rules, or anything depending on information outside the repo. It is a
BLOCK signal: the loop pauses and the question goes to the user.

## Shared findings format

For every issue: **severity** (blocking | major | minor | nit) · **location** (file:line
or section) · **issue** (what is wrong / missing / risky) · **fix** (concrete suggested
change). Tag findings that require a human decision with `[needs-human]` and also count
them in the verdict's `needs_human` line. Be specific and adversarial; skip praise. If
nothing blocks the criteria, say so plainly.

---

## Template P — plan critique

```
You are the CRITIC in a critique loop, reviewing a PLAN before any implementation
exists. Do NOT modify any files — output a critique only.

## Task
<one short paragraph: what the author was asked to produce or change>

## The plan
Read `<PLAN_FILE_PATH>` and any code it references — you have read access to the repo.

## Convergence criteria for the PLAN (the plan loop stops when these are met)
<the agreed plan criteria, verbatim>

## Context
Plan-loop round <K> of 16.

## What I need from you
Critique like a rigorous senior reviewer at the highest scrutiny. Probe for:
- missing edge cases and error paths
- risky assumptions or unstated dependencies
- scope that does not match the stated task (too broad, too narrow, misaligned)
- simpler alternatives the author may have missed
- wrong file paths, function names, or stale references (check the actual repo)
- verification steps that would not actually catch regressions
- open questions the author should not decide alone → mark [needs-human]

<shared findings format + shared verdict block>
```

## Template I — implementation critique

```
The plan you reviewed is now implemented (the user approved the plan first).
Do NOT modify any files — output a critique only.

## What to inspect
- Approved plan: `<PLAN_FILE_PATH>`
- Baseline: the plan was approved at commit `<PLAN_SHA>`.
- Run `git log <PLAN_SHA>..HEAD --oneline` and `git diff <PLAN_SHA>` (this includes
  uncommitted changes), then read the changed files.

## Convergence criteria for the IMPLEMENTATION (the loop stops when these are met)
<the agreed implementation criteria, verbatim>

## Context
Implementation-loop round <K> of 16.

## What I need from you
Verify, in priority order:
(a) the implementation matches the plan you approved — flag drift explicitly;
(b) no scope creep beyond what the plan approved;
(c) no introduced bugs, regressions, or missed edge cases;
(d) tests cover the changes adequately.

<shared findings format + shared verdict block>
```

## Template R — re-review (session-aware follow-up)

```
Round <K> of 16 in this loop. I addressed your last critique. Since your previous pass:
<bullets — one per finding: what I changed, or why I pushed back / left it (disputed);
 plus any task/criteria amendment from the user, verbatim>

Re-inspect: <same instruction as the loop's first template — the plan file, or
`git diff <PLAN_SHA>`, or the uncommitted diff / files for direct mode>.

Note which of your prior findings are resolved and which remain open — do not re-raise
resolved or explicitly disputed items unless still clearly present. Raise anything new.

Same output format as before, end with the `=== VERDICT ===` block.
```

## Template D — direct critique (direct mode, first call)

```
You are the CRITIC in a critique loop. Do NOT modify any files — output a critique only.

## Task under review
<one short paragraph: what the author was asked to produce or change>

## What to inspect
<pick one:>
- The uncommitted changes in this repo. Diff below (for large diffs, omit and run
  `git diff` yourself — you have read access):
  ```diff
  <paste `git diff` for small/medium changes>
  ```
- The file(s): <path(s)>. Contents below (for large files, give paths only and read
  them yourself):
  ```
  <paste doc/text for small artifacts>
  ```

## Convergence criteria (the loop stops when these are met)
<the user's criteria, verbatim>

## Context
Round <K> of 16.

## What I need from you
Critique like a rigorous senior reviewer at the highest scrutiny. Prioritise
correctness and completeness against the task, then anything that blocks the
convergence criteria.

<shared findings format + shared verdict block>
```
