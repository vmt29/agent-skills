#!/usr/bin/env bash
# Stable entry point for either direction of the critique loop (Python 3.9+).
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/critique-loop-run.py" "$@"
