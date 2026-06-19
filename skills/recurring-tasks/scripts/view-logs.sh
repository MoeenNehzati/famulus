#!/usr/bin/env bash
# Usage: view-logs.sh <job-name> [lines]
set -euo pipefail

NAME="${1:-}"
LINES="${2:-50}"
SKILL_DIR="$(dirname "$0")/.."
LOG="$SKILL_DIR/logs/$NAME/run.log"

if [[ -z "$NAME" ]]; then
    echo "Usage: view-logs.sh <job-name> [lines]" >&2
    exit 1
fi

if [[ ! -f "$LOG" ]]; then
    echo "No log found at $LOG" >&2
    exit 1
fi

tail -n "$LINES" -f "$LOG"
