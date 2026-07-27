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
    "skills/install-assistant-tools/_rtx/tests",
    "skills/install-assistant-tools/tests",
}

PORTABILITY_TESTS = (
    "tests/test_officina_atomic_files.py::test_secure_append_creates_then_appends_complete_framed_records",
    "tests/test_officina_atomic_files.py::test_windows_native_secure_create_replace_append_and_acl",
    "tests/test_officina_dispatcher.py::test_python_process_target_keeps_gateway_and_entry_separate",
    "tests/test_officina_git_provenance.py::test_git_test_repository_preserves_exact_bytes_under_ambient_autocrlf",
    "skills/recurring-tasks/_rtx/tests/test_schedule_backend.py::test_linux_sync_writes_units_and_enables_timer",
    "tests/test_officina_blueprint_graph.py::test_content_ownership_accepts_equivalent_repository_alias",
    "tests/test_validator_runner.py::test_run_all_isolates_unmerged_index_and_restores_git_environment",
)

SUITES = {"precommit", "portability", "full"}


def _pytest_args(*, verbose: bool) -> list[str]:
    return ["-o", "pythonpath=src", "-v" if verbose else "-q"]


def _discover_skill_test_dirs() -> list[str]:
    skills_root = REPO_ROOT / "skills"
    return sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for pattern in ("*/tests", "*/_rtx/tests")
        for path in skills_root.glob(pattern)
        if path.is_dir()
    )


def _resolve_suite(name: str) -> list[str]:
    if name == "portability":
        test_dirs = list(PORTABILITY_TESTS)
    else:
        test_dirs = [*BASE_TEST_DIRS, *_discover_skill_test_dirs()]
    if name == "precommit":
        test_dirs = [
            path for path in test_dirs if path not in PRECOMMIT_EXCLUDED_TEST_DIRS
        ]
    missing = [
        path
        for path in test_dirs
        if not (REPO_ROOT / path.split("::", 1)[0]).exists()
    ]
    if missing:
        raise SystemExit(f"configured test paths are missing: {', '.join(missing)}")
    return test_dirs


def _execution_groups(test_dirs: list[str]) -> list[list[str]]:
    nested = sorted(
        path for path in test_dirs if "/_rtx/tests" in path
    )
    shared = [path for path in test_dirs if path not in nested]
    return ([shared] if shared else []) + [[path] for path in nested]


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
    pytest_args = _pytest_args(verbose=args.verbose)
    for group in _execution_groups(test_dirs):
        cmd = [sys.executable, "-m", "pytest", *pytest_args, *group]
        completed = subprocess.run(cmd, cwd=REPO_ROOT)
        if completed.returncode:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
