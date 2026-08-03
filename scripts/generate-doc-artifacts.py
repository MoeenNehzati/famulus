#!/usr/bin/env python3
"""Regenerate generated documentation artifacts and embedded coverage blocks."""
from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPO_ROOT, REPO_ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from docs_tooling.render import generate_all


def main() -> int:
    changed = generate_all(REPO_ROOT)
    if changed:
        for path in changed:
            print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
