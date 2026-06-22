#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: run-skill.sh <skill-name>" >&2
  exit 2
fi

skill_name="$1"
template="${AI_AGENT_COMMAND_TEMPLATE:-}"

if [[ -z "$template" ]]; then
  cat >&2 <<'EOF'
AI_AGENT_COMMAND_TEMPLATE is not set.
Set it to a shell command template containing {skill}, for example in the
systemd user environment or in the job command itself.
EOF
  exit 2
fi

command="${template//\{skill\}/$skill_name}"
exec bash -lc "$command"
