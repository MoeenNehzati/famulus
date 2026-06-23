#!/bin/bash
# Invokes a skill via the assistant command, for use by run-skill.sh.
# Usage: invoke-agent.sh <skill-name>
set -euo pipefail

skill="$1"
exec assistant --permission-mode bypassPermissions -p "/$skill"
