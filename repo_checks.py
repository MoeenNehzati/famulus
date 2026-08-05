#!/usr/bin/env python3
"""Run named repository test and conformance-check suites."""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from officina.repository_checks import main


if __name__ == "__main__":
    raise SystemExit(main())
