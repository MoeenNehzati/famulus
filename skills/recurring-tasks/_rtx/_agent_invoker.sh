#!/bin/bash
# Invokes a skill via the assistant command, for use by run-skill.sh.
# Usage: invoke-agent.sh <skill-name>
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$script_dir/_agent_env.sh"

skill="$1"
backend="${ASSISTANT_DEFAULT:-claude}"

case "$backend" in
  claude)
    exec assistant --local --claude --permission-mode bypassPermissions -p "/$skill"
    ;;
  codex)
    exec assistant --local --codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox "\$$skill"
    ;;
  *)
    echo "Unknown ASSISTANT_DEFAULT backend: $backend" >&2
    exit 2
    ;;
esac
