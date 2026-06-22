#!/usr/bin/env bash
# Verify prerequisites, sync systemd user units from jobs.yaml, and confirm
# all enabled timers are active.
#
# Usage: setup.sh [--migrate-cron]
#   --migrate-cron  also remove the old ai-recurring crontab block
#                   (pass this once when migrating from a cron-based install)
#
# All extra arguments are forwarded to sync-units.py.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
echo "── Prerequisites ──"
python3 -c "import yaml; print('PyYAML ok')"

echo ""
echo "── Syncing units ──"
python3 "$SKILL_DIR/scripts/sync-units.py" "$@"

echo ""
echo "── Active timers ──"
systemctl --user list-timers 'ai-*'
