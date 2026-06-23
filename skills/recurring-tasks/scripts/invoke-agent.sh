#!/bin/bash
# Invokes Claude with a skill as the prompt, for use by run-skill.sh.
# Usage: invoke-agent.sh <skill-name>
set -euo pipefail

skill="$1"
cd "$HOME/Documents/assistant"
exec claude --agent assistant --permission-mode bypassPermissions -p "/$skill"
