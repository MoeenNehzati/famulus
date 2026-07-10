#!/usr/bin/env bash
# gcal — thin wrapper delegating to gcal.py (stdlib-only: urllib/json/
# datetime), which replaced the curl+jq+GNU-date implementation. Those
# tools needed installing (jq, curl) or were GNU/Linux-specific in
# behavior (`date -d`, `timedatectl`), so the CLI itself now has no
# non-stdlib dependency, matching the repo's Python-first shared-skill
# direction.
#
# Same CLI surface as before: same subcommands, flags, and output.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "$SCRIPT_DIR/gcal.py" "$@"
