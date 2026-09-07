"""Launch the Famulus MCP server with its dedicated interpreter."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
ERROR = "famulus MCP launcher: dispatcher runtime unavailable"


def main() -> int:
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from officina.common.famulus_paths import resolve_famulus_paths

        paths = resolve_famulus_paths(
            platform=sys.platform, home=Path.home(), environ=os.environ
        )
        return subprocess.run(
            [str(paths.venv_python_path), str(ROOT / "mcp_server.py")]
        ).returncode
    except Exception:
        print(ERROR, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
