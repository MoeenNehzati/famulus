#!/usr/bin/env python3
"""Run the repository's Python test suites with explicit named groupings."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

BASE_TEST_DIRS = [
    "tests",
    "hooks/tests",
]

PRECOMMIT_EXCLUDED_TEST_DIRS = {
    "skills/install-assistant-tools/tests",
}

SUITES = {"precommit", "full"}


def _discover_skill_test_dirs() -> list[str]:
    skills_root = REPO_ROOT / "skills"
    return sorted(
        str(path.relative_to(REPO_ROOT))
        for path in skills_root.glob("*/tests")
        if path.is_dir()
    )


def _resolve_suite(name: str) -> list[str]:
    test_dirs = [*BASE_TEST_DIRS, *_discover_skill_test_dirs()]
    if name == "precommit":
        test_dirs = [
            path for path in test_dirs if path not in PRECOMMIT_EXCLUDED_TEST_DIRS
        ]
    missing = [path for path in test_dirs if not (REPO_ROOT / path).exists()]
    if missing:
        raise SystemExit(f"configured test paths are missing: {', '.join(missing)}")
    return test_dirs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run repo Python tests using an explicit named suite."
    )
    parser.add_argument(
        "--suite",
        choices=sorted(SUITES),
        default="precommit",
        help="Select which named test suite to run.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Run pytest with verbose output.",
    )
    args = parser.parse_args()

    test_dirs = _resolve_suite(args.suite)
    pytest_args = ["-v"] if args.verbose else ["-q"]
    cmd = [sys.executable, "-m", "pytest", *pytest_args, *test_dirs]
    completed = subprocess.run(cmd, cwd=REPO_ROOT)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
