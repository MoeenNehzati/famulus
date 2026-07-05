#!/usr/bin/env bash
# healthcheck.sh
#
# Monitors all enabled AI recurring jobs. Runs from cron (independent of
# systemd) so it stays alive even if the systemd user session has issues.
#
# Checks:
#   Pre-flight:
#     1. systemd user manager is reachable
#     2. AI_AGENT_COMMAND_TEMPLATE is set in the systemd user environment
#     3. invoke-agent.sh exists and is executable
#   Per enabled job:
#     4. Unit files (.timer + .service) exist
#     5. Timer is active
#     6. Last run result was success
#     7. Log is fresh (< 2× scheduled interval)
#
# Sends a desktop notification if any check fails.
# Logs every run (pass and fail) to logs/healthcheck/run.log.
#
# Cron entry (every 4 hours):
#   0 */4 * * * /path/to/healthcheck.sh

set -uo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOBS_YAML="$SKILL_DIR/jobs.yaml"
LOG_BASE="$SKILL_DIR/logs"
LOG_FILE="$LOG_BASE/healthcheck/run.log"
INVOKE_AGENT="$SKILL_DIR/scripts/invoke-agent.sh"
SYSTEMD_UNIT_DIR="$HOME/.config/systemd/user"
# Peer skills directory (recurring-tasks lives alongside the skills it monitors,
# e.g. ~/.claude/skills/recurring-tasks and ~/.claude/skills/email-triage).
SKILLS_ROOT="$(cd "$SKILL_DIR/.." && pwd)"

UID_="$(id -u)"
XDG_RUNTIME_DIR_="/run/user/${UID_}"
DBUS_BUS_="unix:path=${XDG_RUNTIME_DIR_}/bus"

# systemctl --user needs these to reach the user manager from cron
export XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR_"
export DBUS_SESSION_BUS_ADDRESS="$DBUS_BUS_"

mkdir -p "$LOG_BASE/healthcheck"

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

log() {
  echo "[$(date -Is)] $*" | tee -a "$LOG_FILE"
}

notify() {
  local title="$1"
  local body="$2"
  XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR_" \
  DBUS_SESSION_BUS_ADDRESS="$DBUS_BUS_" \
  /usr/bin/notify-send -u critical -a "ai-jobs" "$title" "$body" 2>/dev/null || true
}

record_problem() {
  local msg="$1"
  log "  PROBLEM: $msg"
  problems+=("$msg")
}

# Parse enabled jobs from jobs.yaml → "name interval_minutes" per line
list_enabled_jobs() {
  python3 - "$JOBS_YAML" <<'PYEOF'
import yaml, sys

with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)

for job in data.get("jobs", []):
    if not job.get("enabled", False):
        continue
    name = job["name"]
    sched = job.get("schedule", "0 * * * *")
    parts = sched.split()
    if len(parts) != 5:
        print(f"{name} 60")
        continue
    m_field, h_field = parts[0], parts[1]
    if h_field == "*":
        interval = int(m_field[2:]) if m_field.startswith("*/") else 60
    elif h_field.startswith("*/"):
        interval = int(h_field[2:]) * 60
    else:
        interval = 24 * 60
    print(f"{name} {interval}")
PYEOF
}

# ------------------------------------------------------------------
# Pre-flight checks
# ------------------------------------------------------------------

problems=()

log "=== healthcheck start ==="

# 1. systemd user manager reachable
log "Pre-flight: checking systemd user manager..."
state="$(systemctl --user is-system-running 2>/dev/null)" || true
case "$state" in
  running)
    log "  OK: systemd user manager is running"
    ;;
  degraded)
    # Degraded means some units failed, but the manager itself is reachable.
    # Check whether any of our AI units are the cause.
    failed_ai="$(systemctl --user list-units --state=failed --no-legend 2>/dev/null | grep '^ai-' | awk '{print $1}' | tr '\n' ' ')"
    if [[ -n "$failed_ai" ]]; then
      record_problem "systemd user manager is degraded and AI units have failed: $failed_ai"
    else
      log "  WARN: systemd user manager is degraded (unrelated units — not an AI job issue)"
    fi
    ;;
  "")
    record_problem "systemd user manager is not reachable — all AI jobs may be broken"
    ;;
  *)
    record_problem "systemd user manager state is '$state' — all AI jobs may be broken"
    ;;
esac

# 2. AI_AGENT_COMMAND_TEMPLATE set in systemd environment
log "Pre-flight: checking AI_AGENT_COMMAND_TEMPLATE in systemd env..."
if systemctl --user show-environment 2>/dev/null | grep -q "^AI_AGENT_COMMAND_TEMPLATE="; then
  log "  OK: AI_AGENT_COMMAND_TEMPLATE is set"
else
  record_problem "AI_AGENT_COMMAND_TEMPLATE is not set in systemd user environment — all AI jobs will fail to invoke the assistant (re-run install-assistant-tools or set it via: systemctl --user set-environment AI_AGENT_COMMAND_TEMPLATE=...)"
fi

# 3. invoke-agent.sh exists and is executable
log "Pre-flight: checking invoke-agent.sh..."
if [[ -x "$INVOKE_AGENT" ]]; then
  log "  OK: invoke-agent.sh exists and is executable"
else
  record_problem "invoke-agent.sh missing or not executable at $INVOKE_AGENT"
fi

# ------------------------------------------------------------------
# Per-job checks
# ------------------------------------------------------------------

log "Per-job checks..."

while read -r name interval_minutes; do
  timer="ai-${name}.timer"
  service="ai-${name}.service"
  timer_file="$SYSTEMD_UNIT_DIR/${timer}"
  service_file="$SYSTEMD_UNIT_DIR/${service}"
  log_file="$LOG_BASE/${name}/run.log"
  stale_seconds=$(( interval_minutes * 2 * 60 ))

  log "  [$name] interval=${interval_minutes}m, stale_threshold=$(( stale_seconds / 60 ))m"

  # 4. Unit files exist
  if [[ ! -f "$timer_file" ]]; then
    record_problem "$name: timer unit file missing ($timer_file) — run setup.sh"
    continue
  fi
  if [[ ! -f "$service_file" ]]; then
    record_problem "$name: service unit file missing ($service_file) — run setup.sh"
    continue
  fi

  # 5. Timer is active
  if ! systemctl --user is-active "$timer" >/dev/null 2>&1; then
    state="$(systemctl --user is-active "$timer" 2>/dev/null || echo unknown)"
    record_problem "$name: timer is not active (state=$state) — run: systemctl --user start $timer"
    continue
  fi

  # 6. Last run result
  result="$(systemctl --user show "$service" --property=Result --value 2>/dev/null || echo unknown)"
  case "$result" in
    success|"")
      log "    last result: OK ($result)"
      ;;
    exit-code)
      record_problem "$name: last run exited with an error — check logs/run.log"
      ;;
    killed)
      record_problem "$name: last run was killed (timeout or OOM?)"
      ;;
    unknown)
      log "    last result: unknown (may not have run yet)"
      ;;
    *)
      record_problem "$name: last run result was '$result'"
      ;;
  esac

  # 7. Log freshness
  if [[ ! -f "$log_file" ]]; then
    record_problem "$name: no run log found at $log_file — job may never have run"
  else
    mtime="$(stat -c %Y "$log_file" 2>/dev/null || echo 0)"
    now="$(date +%s)"
    age=$(( now - mtime ))
    age_min=$(( age / 60 ))
    if (( age > stale_seconds )); then
      record_problem "$name: log is stale (${age_min}m old, expected run every ${interval_minutes}m)"
    else
      log "    log age: ${age_min}m — OK"
    fi
  fi

  # 8. Self-reported status (opt-in convention: a job may write
  #    SKILLS_ROOT/<name>/state/status.json with {"result": "ok"|"warning"|"error", "message": "..."}
  #    to surface problems that aren't process crashes — e.g. a skipped write,
  #    a missing precondition, a degraded fallback. Skills that don't use this
  #    convention simply have no such file and are skipped here.
  status_file="$SKILLS_ROOT/$name/state/status.json"
  if [[ -f "$status_file" ]]; then
    result="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('result','?'))" "$status_file" 2>/dev/null || echo "unreadable")"
    message="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('message',''))" "$status_file" 2>/dev/null || echo "")"
    case "$result" in
      ok)
        log "    self-reported status: OK"
        ;;
      warning|error)
        record_problem "$name: self-reported $result — $message"
        ;;
      unreadable)
        record_problem "$name: status.json at $status_file could not be parsed"
        ;;
      *)
        record_problem "$name: status.json has unrecognized result '$result'"
        ;;
    esac
  fi

done < <(list_enabled_jobs)

# ------------------------------------------------------------------
# Summary and notification
# ------------------------------------------------------------------

if (( ${#problems[@]} == 0 )); then
  log "All checks passed."
else
  log "${#problems[@]} problem(s) found:"
  for p in "${problems[@]}"; do
    log "  • $p"
  done
  body="$(printf '• %s\n' "${problems[@]}")"
  log "Sending desktop notification..."
  notify "⚠ AI jobs need attention (${#problems[@]} issue(s))" "$body"
fi

log "=== healthcheck done ==="
