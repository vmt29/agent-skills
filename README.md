# agent-skills

Personal collection of Claude Code skills. Markdown + a small shell helper, no
frameworks. Repo layout follows the [Agent Skills](https://github.com/vercel-labs/skills)
convention (`skills/<name>/SKILL.md`), so the `skills` CLI can install from here too.

| Skill | Status | What it does |
| --- | --- | --- |
| [`critique-loop`](./skills/critique-loop) | v2 | Two-loop cross-model critique: plan → Codex critiques the plan in a loop → user approval gate → implement → the **same persistent Codex session** critiques the diff in a loop, until user-confirmed convergence criteria are met. |

## critique-loop

A **working mode**, not a PR review. Claude authors and fixes; OpenAI Codex is the
independent read-only critic. Two modes:

- **Full** (default for code / serious work): Claude drafts a plan (committed to
  `docs/plans/<slug>.md` by default) → plan loop (≤ 16 rounds) → explicit user
  approval gate → implementation with conventional commits against a recorded
  baseline SHA → implementation loop (≤ 16 rounds) reviewing `git diff <plan-sha>`.
- **Direct** (small tasks, docs, existing diffs): do the task, then loop (≤ 16 rounds).

Key properties:

- **One persistent Codex session per run** (`codex exec` → `codex exec resume`): the
  critic that approved the plan reviews the code and catches drift from it.
- **Maximal critic, always**: model inherits your Codex config default; effort
  auto-resolves to the higher of your configured tier and `xhigh`
  (`minimal < low < medium < high < xhigh < ultra`; unknown tiers trusted as newer).
  Provenance (CLI version · model · effort · session) is stamped into every critique.
- **Checkable convergence criteria**, confirmed by the user at intake and amendable
  mid-session (re-baselined and logged).
- **Nothing silently decided**: product/architecture findings pause the loop as
  `needs_human` questions; the user's answers are first-class artifacts in the gate
  summary and the final report.
- **Visualized**: severity-badged findings table, convergence trajectory, and an HTML
  dashboard per run. Final report includes every disagreement with the critic.
- **Optional grilling** at intake — one question at a time, each with a recommended
  answer — only when the task has load-bearing ambiguity.

### Requirements

- Claude Code.
- OpenAI Codex CLI (`codex`), authenticated (`codex login`). Model and effort come
  from `~/.codex/config.toml`; check what the critic will run with:
  `critique-loop-run.sh info`.

### Install

Symlink (recommended — updates are just `git pull`, local edits are git-tracked):

```bash
git clone git@github.com:vmt29/agent-skills.git
ln -s "$(pwd)/agent-skills/skills/critique-loop" ~/.claude/skills/critique-loop
chmod +x ~/.claude/skills/critique-loop/critique-loop-run.sh
```

Or plain copy:

```bash
cp -R agent-skills/skills/critique-loop ~/.claude/skills/critique-loop
chmod +x ~/.claude/skills/critique-loop/critique-loop-run.sh
```

Or via the skills CLI:

```bash
npx skills add vmt29/agent-skills --skill critique-loop -a claude-code -g
```

Trigger with `/critique-loop <task>` (or "loop with codex on …"). Claude proposes a
brief — task, mode, criteria, plan location — and starts only after you confirm it.

## Credits

The two-loop structure, persistent-session mechanics, verdict-driven gate, and the
Claude-resolves vs needs-human ask classification adapt ideas from
[gzaripov/agent-skills](https://github.com/gzaripov/agent-skills) (`critique-loop`).
The optional grilling pass adapts [mattpocock/skills](https://github.com/mattpocock/skills)
(`grilling` / `grill-with-docs`), and the ADR offer criteria (hard-to-reverse +
surprising + real trade-off) come from the same ecosystem. MIT, like both sources.

## License

MIT — see [LICENSE](./LICENSE).
