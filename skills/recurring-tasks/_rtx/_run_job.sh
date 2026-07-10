#!/usr/bin/env bash
# Universal runner for recurring jobs. Invoked by systemd service.
# Arguments: <skill-name> <command>
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: run-job.sh <skill-name> <command> [args...]" >&2
  exit 2
fi

skill="$1"
shift
command=("$@")

# Execute the command. The command is typically: invoke-skill <skill_name>
# The environment is already set up by the systemd service/runner script.
exec "${command[@]}"
