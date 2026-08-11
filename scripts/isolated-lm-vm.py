#!/usr/bin/env python3
"""Load the supported isolated-VM CLI from this repository by exact path."""
from pathlib import Path
import sys


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPOSITORY_ROOT))

from test_support.isolated_lm.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
