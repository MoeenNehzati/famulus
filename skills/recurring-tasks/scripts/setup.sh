#!/usr/bin/env bash
# Verify prerequisites, sync systemd user units from jobs.yaml, install the
# healthcheck cron entry, and confirm all enabled timers are active.
#
# Usage: setup.sh [--migrate-cron]
#   --migrate-cron  also remove the old ai-recurring crontab block
#                   (pass this once when migrating from a cron-based install)
#
# All extra arguments are forwarded to sync-units.py.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HEALTHCHECK="$SKILL_DIR/scripts/healthcheck.sh"
CRON_MARKER="# ai-recurring-healthcheck"

echo "── Prerequisites ──"
python3 -c "import yaml; print('PyYAML ok')"

echo ""
echo "── Syncing units ──"
python3 "$SKILL_DIR/scripts/sync-units.py" "$@"

echo ""
echo "── Installing healthcheck cron entry ──"
mkdir -p "$SKILL_DIR/logs/healthcheck"
existing_cron="$(crontab -l 2>/dev/null || true)"
if echo "$existing_cron" | grep -qF "$CRON_MARKER"; then
  echo "Healthcheck cron entry already present."
else
  cron_entry="0 */4 * * * $HEALTHCHECK >> $SKILL_DIR/logs/healthcheck/run.log 2>&1 $CRON_MARKER"
  ( echo "$existing_cron"; echo "$cron_entry" ) | crontab -
  echo "Added healthcheck cron entry (every 4 hours)."
fi

echo ""
echo "── Active timers ──"
systemctl --user list-timers 'ai-*'
