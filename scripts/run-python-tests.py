#!/usr/bin/env python3
"""Run the repository's Python test suites with explicit named groupings."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists() and str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from officina.common.test_discovery import (
    discover_repository_test_dirs,
)

PRECOMMIT_EXCLUDED_TEST_DIRS = {
    "skills/install-assistant-tools/_rtx/tests",
    "skills/install-assistant-tools/tests",
}
PRECOMMIT_EXCLUDED_TESTS = {
    "tests/test_nested_module_migration.py::"
    "TestNestedModuleMigrationContract::"
    "test_repository_inventory_matches_reviewed_v5_cutover_surface",
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


@dataclass(frozen=True)
class ExecutionGroupResult:
    """Record the outcome of one independently executed pytest command group.

    Intent
    ------
    Carry a group's identity, exact command, return code, and elapsed wall time.

    Rationale
    ---------
    Explicit group records support exhaustive failure reporting and make test
    computation cost attributable to the command that incurred it.

    Pseudocode
    ----------
    - set group_result = group identity command return code and duration
    - return group_result

    Wraps
    -----
    - none
    """

    group_id: str
    command: list[str]
    returncode: int
    wall_seconds: float


def _pytest_args(*, verbose: bool) -> list[str]:
    """Build repository-wide pytest options for the requested verbosity.

    Intent
    ------
    Return the common import-path and output-mode arguments for every group.

    Rationale
    ---------
    Central construction prevents named suites from drifting in basic pytest
    configuration while leaving test selection to the suite resolver.

    Pseudocode
    ----------
    - set pytest_args = repository import-path option plus output-mode option
    - return pytest_args

    Wraps
    -----
    - none
    """
    return ["-o", "pythonpath=src", "-v" if verbose else "-q"]


def _suite_pytest_args(
    name: str, *, verbose: bool, keep_going: bool = False
) -> list[str]:
    """Build pytest options that specialize the selected named suite.

    Intent
    ------
    Add suite-specific deselection and collection-continuation options to the
    common pytest arguments.

    Rationale
    ---------
    Keeping option construction separate from test-path discovery makes suite
    policy visible without coupling it to subprocess command assembly.

    Pseudocode
    ----------
    - set args = common pytest options for verbose
    - if name is precommit:
      - set args = args plus every configured deselection
    - if keep_going:
      - set args = args plus collection-error continuation
    - return args

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._pytest_args:
      why:
        constructs: "Builds the common pytest argument list extended by suite policy."
    """
    args = _pytest_args(verbose=verbose)
    if name == "precommit":
        for test in sorted(PRECOMMIT_EXCLUDED_TESTS):
            args.extend(["--deselect", test])
    if keep_going:
        args.append("--continue-on-collection-errors")
    return args


def _resolve_suite(name: str) -> list[str]:
    """Resolve and validate the test paths belonging to one named suite.

    Intent
    ------
    Return explicit repository-relative tests for portability, precommit, or full
    execution and reject stale configured paths.

    Rationale
    ---------
    Materializing discovery once gives every later execution group a stable test
    inventory; existence checks prevent a green run caused by silently missing
    configured tests.

    Pseudocode
    ----------
    - set test_dirs = portability cases or discovered repository test directories
    - if name is precommit:
      - set test_dirs = test_dirs without explicitly excluded directories
    - set missing = selected paths whose source file is absent
    - if missing is nonempty:
      - raise configuration failure
    - return test_dirs

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.common.test_discovery.discover_repository_test_dirs:
      why:
        constructs: "Builds the repository test-directory inventory used by non-portability suites."
    """
    if name == "portability":
        test_dirs = list(PORTABILITY_TESTS)
    else:
        test_dirs = list(
            discover_repository_test_dirs(REPO_ROOT, return_relative=True)
        )
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
    """Partition selected tests into isolated pytest execution groups.

    Intent
    ------
    Keep shared tests together while assigning each nested runtime suite its own
    pytest process.

    Rationale
    ---------
    Runtime suites can carry incompatible import and collection state; process
    isolation preserves correctness while avoiding one process per ordinary test.

    Pseudocode
    ----------
    - set nested = selected runtime test directories in lexical order
    - set shared = selected paths not in nested
    - set groups = optional shared group followed by one group per nested path
    - return groups

    Wraps
    -----
    - none
    """
    nested = sorted(
        path for path in test_dirs if "/_rtx/tests" in path
    )
    shared = [path for path in test_dirs if path not in nested]
    return ([shared] if shared else []) + [[path] for path in nested]


def _write_report(
    report_path: Path | None,
    results: list[ExecutionGroupResult],
    *,
    complete: bool,
) -> None:
    """Persist structured group outcomes when the caller requested a report.

    Intent
    ------
    Serialize run completeness and ordered pytest-group measurements as JSON.

    Rationale
    ---------
    The parent gate needs to distinguish ordinary test failures from a child that
    stopped before all groups ran, while direct callers may omit report output.

    Pseudocode
    ----------
    - if report_path is none:
      - return
    - set report_payload = completeness plus serialized group results
    - set report_parent = created parent directory for report_path
    - set report_state = persisted formatted group payload

    Wraps
    -----
    - none
    """
    if report_path is None:
        return
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "complete": complete,
                "groups": [asdict(result) for result in results],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def run_groups(
    groups: list[list[str]], *, keep_going: bool, report_path: Path | None
) -> int:
    """Run pytest command groups and optionally accumulate ordinary failures.

    Intent
    ------
    Execute groups in order, measure each command, and return a process-level
    success or failure after the configured stopping policy is satisfied.

    Rationale
    ---------
    Group isolation prevents collection-state leakage. ``keep_going`` permits a
    complete failure inventory, while interrupts and OS errors produce an
    explicitly incomplete report and remain exceptional.

    Pseudocode
    ----------
    - set results = empty ordered group result list
    - set failed = false
    - for command in groups:
      - set completed = measured subprocess execution
      - if interruption or OS error occurs:
        - set report_state = persisted incomplete group state
        - raise original error
      - set group_result = command outcome and elapsed time
      - set results = results plus group_result
      - if group_result failed and keep_going is false:
        - set report_state = persisted policy-limited group state
        - return failure
      - set failed = whether any observed group failed
    - set report_state = persisted complete group state
    - return failure when any group failed, otherwise success

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._write_report:
      why:
        writes: "Persists completeness and accumulated outcomes at every terminal boundary."

    InstantiationsFromRepo
    ----------------------
    .ExecutionGroupResult:
      why:
        constructs: "Builds the measured outcome appended for each executed pytest group."
    """
    results: list[ExecutionGroupResult] = []
    failed = False
    for index, command in enumerate(groups, start=1):
        started = time.perf_counter()
        try:
            completed = subprocess.run(command, cwd=REPO_ROOT)
        except (KeyboardInterrupt, OSError):
            _write_report(report_path, results, complete=False)
            raise
        result = ExecutionGroupResult(
            group_id=f"group-{index}",
            command=command,
            returncode=completed.returncode,
            wall_seconds=time.perf_counter() - started,
        )
        results.append(result)
        if completed.returncode:
            failed = True
            if not keep_going:
                _write_report(report_path, results, complete=True)
                return 1
    _write_report(report_path, results, complete=True)
    return 1 if failed else 0


def main() -> int:
    """Parse the named-suite CLI and execute its isolated pytest groups.

    Intent
    ------
    Convert command-line suite policy into explicit pytest commands and return the
    aggregate group result as the process exit code.

    Rationale
    ---------
    A thin entry point keeps argument parsing, suite discovery, grouping, and
    execution visible as separate decisions that tests can exercise independently.

    Pseudocode
    ----------
    - set args = parsed suite verbosity continuation and report options
    - set test_dirs = validated paths for the selected suite
    - set pytest_args = common and suite-specific pytest options
    - set commands = one Python pytest command for each execution group
    - set exit_code = aggregate execution result for commands
    - return exit_code

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._resolve_suite:
      why:
        constructs: "Builds the validated test-path inventory for the selected suite."
    ._suite_pytest_args:
      why:
        constructs: "Builds the pytest options shared by every assembled command."
    .run_groups:
      why:
        constructs: "Builds the process exit code from all executed group outcomes."

    CallsFromRepo
    -------------
    ._execution_groups:
      why:
        computes: "Partitions selected tests into the isolated groups used to assemble commands."
    """
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
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Run every execution group before returning an ordinary failure.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Write structured execution-group results to this JSON file.",
    )
    args = parser.parse_args()

    test_dirs = _resolve_suite(args.suite)
    pytest_args = _suite_pytest_args(
        args.suite, verbose=args.verbose, keep_going=args.keep_going
    )
    commands = [
        [sys.executable, "-m", "pytest", *pytest_args, *group]
        for group in _execution_groups(test_dirs)
    ]
    return run_groups(
        commands, keep_going=args.keep_going, report_path=args.report
    )


if __name__ == "__main__":
    raise SystemExit(main())
