#!/usr/bin/env bash
# critique-loop-run.sh — run ONE Codex critique pass for the /critique-loop skill (v2).
#
# Codex is the CRITIC only: it runs read-only and never edits your files.
# Claude reads the critique from the buffer and applies every fix itself.
#
# v2: ONE PERSISTENT CODEX SESSION per run. The first `exec` call starts a session and
# records its id in $CRITIQUE_LOOP_DIR/session-id; every later `exec` call resumes it,
# so the critic that reviewed the plan is the same one reviewing the implementation.
# (`review` mode is stateless — codex's purpose-built diff reviewer has no sessions.)
#
# Model  : inherits Codex's configured default = your maximal available model
#          (override with CODEX_MODEL=... only if you need to pin a specific one).
# Effort : the MAXIMAL tier — the higher of your codex-config `model_reasoning_effort`
#          and `xhigh` on the known ladder minimal<low<medium<high<xhigh<ultra. An
#          unrecognized configured tier is trusted as newer-and-higher (future tiers).
#          Override with CODEX_EFFORT=... to pin one explicitly.
#
# Every run prints a PROVENANCE line — exact Codex CLI version, effective model, effort,
# and session state — and stamps it into the buffer.
#
# Usage:
#   critique-loop-run.sh <iteration> [mode] [target_dir]
#     <iteration>   1..32 — buffer numbering is CONTINUOUS across the whole run; each
#                   loop (plan / implement) runs at most 16 rounds, so full mode can
#                   reach 32, direct mode 16 (set CRITIQUE_MAX=16 for correct stamps)
#     [mode]        exec (default; session-persistent, works for plans, code, or docs)
#                   review (codex git-diff reviewer; STATELESS; --uncommitted, or
#                           REVIEW_BASE=<ref> to review against a base ref instead)
#     [target_dir]  repo root Codex runs in (default: current dir)
#
#   critique-loop-run.sh info
#     Print the resolved provenance (CLI version · model · effort) without running.
#
#   critique-loop-run.sh reset
#     Wipe $CRITIQUE_LOOP_DIR (buffers + session id) to start a fresh run. Refuses to
#     delete anything outside /tmp.
#
# Reads prompt from : $CRITIQUE_LOOP_DIR/prompt-<iteration>.txt   (Claude writes this first)
# Writes critique to: $CRITIQUE_LOOP_DIR/critique-<iteration>.md
# Session id file   : $CRITIQUE_LOOP_DIR/session-id (auto-managed; delete to force a new session)
#
# Env: CODEX_MODEL (default: unset -> Codex config default) · CODEX_EFFORT (default: resolved max)
#      CRITIQUE_LOOP_DIR (default: /tmp/critique-loop — the skill sets /tmp/critique-loop/<slug>)
#      CRITIQUE_PHASE (plan | code | direct — label only) · CODEX_HOME (default: ~/.codex)
#      CRITIQUE_MAX (stamp denominator: 32 default; 16 for direct mode)
#      REVIEW_BASE (review mode only: ref passed to `codex review --base`)

set -euo pipefail

BUF_DIR="${CRITIQUE_LOOP_DIR:-/tmp/critique-loop}"
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"

# --- reset subcommand ----------------------------------------------------------------
if [ "${1:-}" = "reset" ]; then
  case "$BUF_DIR" in
    /tmp/*) rm -rf "$BUF_DIR"; echo "critique_loop: cleared $BUF_DIR" ;;
    *)      echo "ERROR: refusing to reset non-/tmp dir: $BUF_DIR" >&2; exit 2 ;;
  esac
  exit 0
fi

# --- helper: read a key from codex config.toml ---------------------------------------
# The `|| true` keeps a missing key from killing the script under `set -e -o pipefail`
# (grep exits 1 on no match, which would otherwise abort the whole run silently).
codex_config() {  # codex_config <key>
  { grep -E "^[[:space:]]*${1}[[:space:]]*=" "$CODEX_HOME_DIR/config.toml" 2>/dev/null || true; } \
    | head -1 | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]*(#.*)?$//; s/^"//; s/"$//'
}

# --- Effort: maximal tier ------------------------------------------------------------
# rank on the known ladder; -1 = unknown tier (assumed newer than the ladder, trusted)
effort_rank() {
  case "$1" in
    minimal) echo 0 ;; low) echo 1 ;; medium) echo 2 ;; high) echo 3 ;;
    xhigh)   echo 4 ;; ultra) echo 5 ;; *) echo -1 ;;
  esac
}

if [ -z "${CODEX_EFFORT:-}" ]; then
  CFG_EFFORT="$(codex_config model_reasoning_effort)"
  if [ -n "$CFG_EFFORT" ]; then
    CR="$(effort_rank "$CFG_EFFORT")"
    XR="$(effort_rank xhigh)"
    # configured tier wins if it ranks >= xhigh, or is unknown (assumed newer/higher)
    if [ "$CR" -lt 0 ] || [ "$CR" -ge "$XR" ]; then
      CODEX_EFFORT="$CFG_EFFORT"
    else
      CODEX_EFFORT="xhigh"
    fi
  else
    CODEX_EFFORT="xhigh"
  fi
fi

# --- Provenance: resolve exactly which Codex produces this critique ------------------
CLI_VERSION="$(codex --version 2>/dev/null | head -1 | awk '{print $NF}')"
if [ -n "${CODEX_MODEL:-}" ]; then
  EFFECTIVE_MODEL="$CODEX_MODEL"                       # explicitly pinned
else                                                   # inherited = your maximal default
  EFFECTIVE_MODEL="$(codex_config model)"
  [ -z "$EFFECTIVE_MODEL" ] && EFFECTIVE_MODEL="(codex default)"
fi
PROV="codex ${CLI_VERSION:-?} · model ${EFFECTIVE_MODEL} · effort ${CODEX_EFFORT}"

# --- info subcommand -----------------------------------------------------------------
if [ "${1:-}" = "info" ]; then
  echo "$PROV"
  exit 0
fi

ITER="${1:?usage: critique-loop-run.sh <iteration>|info|reset [mode] [target_dir]}"
MODE="${2:-exec}"
TARGET_DIR="${3:-$PWD}"
PHASE="${CRITIQUE_PHASE:--}"
MAXI="${CRITIQUE_MAX:-32}"

mkdir -p "$BUF_DIR"

PROMPT_FILE="$BUF_DIR/prompt-${ITER}.txt"
OUT_FILE="$BUF_DIR/critique-${ITER}.md"
RAW_FILE="$BUF_DIR/raw-${ITER}.txt"
BODY_FILE="$BUF_DIR/.body-${ITER}.txt"
SESSION_FILE="$BUF_DIR/session-id"

if [ ! -f "$PROMPT_FILE" ]; then
  echo "ERROR: no critique prompt at $PROMPT_FILE — Claude must write it before running." >&2
  exit 2
fi

# Always force max effort; inherit account default model unless CODEX_MODEL pins one.
# (CFG always holds >=2 elements, so "${CFG[@]}" is safe under `set -u` on macOS bash 3.2.)
CFG=(-c "model_reasoning_effort=${CODEX_EFFORT}")
[ -n "${CODEX_MODEL:-}" ] && CFG+=(-c "model=${CODEX_MODEL}")

# `< /dev/null` on every codex call is mandatory: codex reads stdin in addition to the
# prompt argument, and blocks forever when a non-interactive harness leaves stdin open.

if [ "$MODE" = "review" ]; then
  # Codex's purpose-built reviewer; custom instructions from the prompt file. Stateless.
  if [ -n "${REVIEW_BASE:-}" ]; then REV_ARGS=(--base "$REVIEW_BASE"); else REV_ARGS=(--uncommitted); fi
  ( cd "$TARGET_DIR" && codex review "${REV_ARGS[@]}" "${CFG[@]}" \
      "$(cat "$PROMPT_FILE")" < /dev/null ) &> "$RAW_FILE" || true
  # Strip the CLI banner: keep everything after the last line that mentions "codex".
  LAST=$(grep -in 'codex' "$RAW_FILE" | tail -1 | cut -d: -f1 || true)
  if [ -z "${LAST:-}" ]; then cp "$RAW_FILE" "$BODY_FILE"; else tail -n +"$((LAST + 1))" "$RAW_FILE" > "$BODY_FILE"; fi
  SESSION_NOTE="stateless (review mode)"
else
  # General critic. read-only sandbox = Codex can inspect files/diff but NEVER edit.
  # -o writes ONLY the final critique message -> clean buffer, no banner to strip.
  if [ -s "$SESSION_FILE" ]; then
    # RESUME the persistent session. `codex exec resume` takes no --sandbox / -C flag:
    # sandbox goes via -c sandbox_mode (value parsed as TOML, hence inner quotes),
    # and the working directory via cd.
    SID="$(cat "$SESSION_FILE")"
    ( cd "$TARGET_DIR" && codex exec resume "$SID" "${CFG[@]}" \
        -c sandbox_mode='"read-only"' \
        --skip-git-repo-check \
        -o "$BODY_FILE" \
        "$(cat "$PROMPT_FILE")" < /dev/null ) &> "$RAW_FILE" || true
    SESSION_NOTE="session ${SID} (resumed)"
  else
    # START the persistent session, then capture its id from the CLI banner.
    ( cd "$TARGET_DIR" && codex exec "${CFG[@]}" \
        --sandbox read-only \
        --skip-git-repo-check \
        -o "$BODY_FILE" \
        "$(cat "$PROMPT_FILE")" < /dev/null ) &> "$RAW_FILE" || true
    grep -oE 'session id: [0-9a-f-]{36}' "$RAW_FILE" | head -1 | awk '{print $3}' > "$SESSION_FILE" || true
    if [ -s "$SESSION_FILE" ]; then
      SESSION_NOTE="session $(cat "$SESSION_FILE") (started)"
    else
      SESSION_NOTE="session NOT captured — next call will start fresh"
      echo "WARN: could not capture codex session id from $RAW_FILE; continuing stateless." >&2
    fi
  fi
  [ -s "$BODY_FILE" ] || cp "$RAW_FILE" "$BODY_FILE"   # fall back to raw if Codex errored
fi

# Stamp provenance into the buffer so the record is self-describing.
{ echo "<!-- critique_loop · iter ${ITER}/${MAXI} · phase ${PHASE} · mode ${MODE} · ${PROV} · ${SESSION_NOTE} -->"; echo; cat "$BODY_FILE"; } > "$OUT_FILE"
rm -f "$BODY_FILE"

echo "════ critique_loop · iteration ${ITER}/${MAXI} · phase=${PHASE} · mode=${MODE} ════"
echo "     ${PROV}"
echo "     ${SESSION_NOTE}"
echo "──── critique buffer: ${OUT_FILE} ────"
cat "$OUT_FILE"
