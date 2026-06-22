#!/usr/bin/env bash
# Show status of all recurring task timers and, optionally, detailed
# debug info for a single job.
#
# Usage: status.sh [job-name]
#   No argument: list all ai-* timers.
#   With job-name: also show service status and recent journal entries.
set -euo pipefail

PREFIX="ai-"

echo "── Timers ──"
systemctl --user list-timers "${PREFIX}*"

if [[ -n "${1:-}" ]]; then
    echo ""
    echo "── Service status: ${PREFIX}${1}.service ──"
    systemctl --user status "${PREFIX}${1}.service" --no-pager || true

    echo ""
    echo "── Timer status: ${PREFIX}${1}.timer ──"
    systemctl --user status "${PREFIX}${1}.timer" --no-pager || true

    echo ""
    echo "── Journal: ${PREFIX}${1}.service (last 2 hours) ──"
    journalctl --user -u "${PREFIX}${1}.service" --since "2 hours ago" --no-pager
fi
