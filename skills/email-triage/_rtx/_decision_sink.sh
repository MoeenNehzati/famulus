#!/usr/bin/env bash
# Append one triage decision line to triage.log.
#
# Usage: log-decision.sh ACCOUNT ID FROM SUBJECT DECISION REASON
#   ACCOUNT:  account nickname (see email-client's accounts-list)
#   ID:       IMAP UID (the "id" field from email-client's mail-list)
#   FROM:     sender display string (quote it)
#   SUBJECT:  email subject (quote it)
#   DECISION: SKIP | NO_ACTION | TODO | POTENTIAL | DEDUP
#               SKIP       — skipped without reading body (subject alone sufficient)
#               NO_ACTION  — body read; nothing actionable
#               TODO       — item added to todo list
#               POTENTIAL  — item added to triage list
#               DEDUP      — would have added but already present in destination list
#   REASON:   one sentence explaining the classification (quote it)
#
# Output line format:
#   [ISO-TIMESTAMP] [ACCOUNT] [ID:N] FROM | SUBJECT → DECISION: reason

set -euo pipefail

if [[ $# -lt 6 ]]; then
  echo "Usage: log-decision.sh ACCOUNT ID FROM SUBJECT DECISION REASON" >&2
  exit 1
fi

LOGFILE="$(dirname "$(realpath "$0")")/../triage.log"
TIMESTAMP="$(date -Iseconds)"

printf '[%s] [%s] [ID:%s] %s | %s → %s: %s\n' \
  "$TIMESTAMP" "$1" "$2" "$3" "$4" "$5" "$6" >> "$LOGFILE"
