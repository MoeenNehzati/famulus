#!/bin/bash
# Quick check of the last email-triage run metrics
# Usage: ./check-triage-status.sh

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUS_FILE="$SKILL_DIR/state/status.json"
TRIAGE_LOG="$SKILL_DIR/triage.log"

if [[ ! -f "$STATUS_FILE" ]]; then
    echo "No triage runs yet (status.json not found)"
    exit 1
fi

echo "=== Last Triage Run Status ==="
jq . "$STATUS_FILE" 2>/dev/null || cat "$STATUS_FILE"

echo ""
echo "=== Recent Triage Activity (last 10 decisions) ==="
tail -50 "$TRIAGE_LOG" 2>/dev/null | grep '\->\|Scanned' | tail -10

echo ""
echo "To see more: tail -100 $TRIAGE_LOG | grep '\[personal\]\|\[nyu\]'"
