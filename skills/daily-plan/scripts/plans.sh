#!/usr/bin/env bash
# Operations on GDrive:assistant/plans/:
#   plans.sh read <date>   - print plan file for date (format: M-D-YY)
#   plans.sh write <date>  - write stdin as the full plan for date
#   plans.sh exists <date> - prints "exists" or "not found"; exit 0 if exists, exit 1 if not
#   plans.sh delete <date> - delete the plan file for date
set -euo pipefail

op="${1:-}"
date_str="${2:-}"

if [ -z "$op" ] || [ -z "$date_str" ]; then
  echo "usage: plans.sh read|write|exists <date>" >&2
  exit 1
fi

case "$op" in
  read)
    rclone cat "GDrive:assistant/plans/${date_str}.md" 2>/dev/null || true
    ;;
  write)
    content="$(cat)"
    printf '%s\n' "$content" | rclone rcat "GDrive:assistant/plans/${date_str}.md"
    ;;
  exists)
    if rclone lsf "GDrive:assistant/plans/${date_str}.md" >/dev/null 2>&1; then
      echo "exists"
    else
      echo "not found"
      exit 1
    fi
    ;;
  delete)
    rclone deletefile "GDrive:assistant/plans/${date_str}.md" 2>/dev/null || true
    ;;
  *)
    echo "usage: plans.sh read|write|exists <date>" >&2
    exit 1
    ;;
esac
