#!/usr/bin/env bash
# Sets up a Python venv at ./.venv and installs dependencies.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -d ".venv" ]; then
    echo "Creating virtualenv at .venv ..."
    python3 -m venv .venv
fi

./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt -e .

echo "Done. Activate with: source .venv/bin/activate"
