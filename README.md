# agent-skills

Personal collection of skills for Claude Code and Codex. Markdown, a shell entry
point, and a Python standard-library runner. Repo layout follows the
[Agent Skills](https://github.com/vercel-labs/skills) convention
(`skills/<name>/SKILL.md`).

| Skill | Status | What it does |
| --- | --- | --- |
| [`critique-loop`](./skills/critique-loop) | v3 | Bidirectional critique: converge a plan, get user approval, implement, then converge the diff with the same persistent reviewer session. |

## critique-loop

The agent running the skill is the programmer and applies every fix. The other
agent reviews read-only:

| Programmer | Reviewer | Review model | Effort |
| --- | --- | --- | --- |
| Claude Code | Codex CLI | `gpt-6-astra` (GPT-6 Astra) | `max` |
| Codex | Claude Code CLI | `claude-fable-5-1` (Fable 5.1) | `max` |

These are explicit defaults, independent of either CLI's configured default model.
`max` is the literal effort setting in both CLIs. The programmer's model is unchanged.

**Downgrade policy:** report the reason and next settings before retrying. Codex
stays on GPT-6 Astra and tries `xhigh` first; no further Codex downgrade is authorized.
Claude Code tries **Fable 5.1 `max` → Fable 5.1 `xhigh` → Opus 5 `xhigh`**, in that
order. **Opus 4.8 is never accepted.** Automatic Claude model switching is disabled,
and unexpected models in the result are rejected. The helper prints the next allowed
settings but never downgrades by itself. Each reported settings change starts a fresh
session with the accepted task/plan context; failed buffers and overall round counts
are preserved. Permission denials and safety refusals are not downgrade triggers.

- **Full** (default for code): draft a plan in `docs/plans/`, converge it in up to
  16 review rounds, get user approval, implement against a recorded baseline SHA,
  and converge the implementation in up to 16 more rounds.
- **Direct** (small tasks, docs, existing diffs): do the task, then review in up to
  16 rounds.
- **One reviewer session per run:** `codex exec` → `codex exec resume`, or
  `claude --print` → `claude --print --resume`. The reviewer that checked the plan
  checks the implementation. The helper rejects changes to the run's roles,
  model, effort, mode, or working directory.
- **Read-only review:** Codex uses a read-only sandbox. Claude Code runs with
  customizations disabled and only Read, Glob, and Grep tools. The helper supplies
  Git status, diff, and baseline history; the programmer runs tests and fixes files.
- **Checkable criteria:** confirmed at intake and amendable during the run.
  Product and architecture questions go to the user as `needs_human` decisions.
- **Visible results:** findings, convergence trajectory, CLI/model/effort/session
  provenance, and a dashboard that identifies both roles. The final report includes
  disagreements and user decisions.

### Requirements

- Python 3.9+; no third-party Python packages.
- An authenticated reviewer CLI with access to the selected model: a current
  Codex CLI for GPT-6 Astra, or Claude Code 2.1.255+ supporting `--safe-mode` for
  Fable 5.1. CLI flags were checked with Codex 0.153.4 and Claude Code 2.1.263.
- If a model or effort setting is unavailable, the helper reports the error instead
  of silently choosing another one.

Model references: [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)
and [Claude Code model configuration](https://code.claude.com/docs/en/model-config).

### Install

Clone once and link the skill into both agents' discovery directories:

```bash
git clone git@github.com:vmt29/agent-skills.git
mkdir -p ~/.claude/skills ~/.agents/skills
ln -s "$(pwd)/agent-skills/skills/critique-loop" ~/.claude/skills/critique-loop
ln -s "$(pwd)/agent-skills/skills/critique-loop" ~/.agents/skills/critique-loop
```

If a destination already contains an installed copy, update that copy or replace
its link deliberately. Keep the whole skill directory together, including both
runner files. Invoke through `bash`; no chmod step is needed.

Preview each direction without making a model request:

```bash
CRITIQUE_PROGRAMMER=claude bash ~/.claude/skills/critique-loop/critique-loop-run.sh info
CRITIQUE_PROGRAMMER=codex bash ~/.agents/skills/critique-loop/critique-loop-run.sh info
```

Trigger `/critique-loop <task>` in Claude Code, or `$critique-loop <task>` in Codex.
The programmer proposes the brief and follows the same plan and implementation
workflow in either direction. Helper paths are resolved relative to the loaded
skill, so copies installed elsewhere also work.

Set `CRITIQUE_PROGRAMMER=claude|codex` and a unique
`CRITIQUE_LOOP_DIR=/tmp/critique-loop/<slug>` on every helper call. The helper also
recognizes host markers when present. Old calls without markers or an explicit
role still choose Codex as the reviewer. Explicit reviewer selection is available
through `CRITIQUE_REVIEWER=codex|claude`.

User-requested overrides use `CODEX_MODEL` / `CODEX_EFFORT` or `CLAUDE_MODEL` /
`CLAUDE_EFFORT`; use full model IDs. Set them before starting and keep them fixed
throughout the run. Reset a reused buffer directory before starting a new run.

### Validation

```bash
python3 -m unittest discover -s tests -v
```

Tests use local fake CLIs and real temporary Git repositories to check routing,
model/effort flags, read-only tool restrictions, session continuity, Git evidence,
and failure propagation without making model requests.

## Credits

The two-loop structure, persistent-session mechanics, verdict-driven gate, and the
programmer-resolvable vs needs-human classification adapt ideas from
[gzaripov/agent-skills](https://github.com/gzaripov/agent-skills) (`critique-loop`).
The optional grilling pass adapts [mattpocock/skills](https://github.com/mattpocock/skills)
(`grilling` / `grill-with-docs`), and the ADR offer criteria come from the same
ecosystem. MIT, like both sources.

## License

MIT — see [LICENSE](./LICENSE).
