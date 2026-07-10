#!/usr/bin/env bash
# Verify prerequisites, sync systemd user units from jobs.yaml, install the
# healthcheck cron entry, and confirm all enabled timers are active.
#
# Usage: setup.sh [--migrate-cron]
#   --migrate-cron  also remove the old ai-recurring crontab block
#                   (pass this once when migrating from a cron-based install)
#
# All extra arguments are forwarded to sync_units.py.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HEALTHCHECK="python3 $SKILL_DIR/_rtx/_healthcheck_probe.py"
CRON_MARKER="# ai-recurring-healthcheck"

echo "── Prerequisites ──"
python3 -c "import yaml; print('PyYAML ok')"
REPO_ROOT="$(cd "$SKILL_DIR/../.." && pwd)"
BIN_DIR="$(dirname "$(command -v assistant 2>/dev/null || echo "$HOME/Documents/scripts/bin/assistant")")"
python3 "$SKILL_DIR/_rtx/_ensure_agent_env.py" \
  --repo-root "$REPO_ROOT" \
  --home "$HOME" \
  --bin-dir "$BIN_DIR"

echo ""
echo "── Syncing units ──"
python3 "$SKILL_DIR/_rtx/_unit_writer.py" "$@"

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
