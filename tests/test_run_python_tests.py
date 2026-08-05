from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run-python-tests.py"
SPEC = importlib.util.spec_from_file_location("run_python_tests", MODULE_PATH)
assert SPEC is not None
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


EXPECTED_PORTABILITY_TESTS = (
    "tests/test_officina_atomic_files.py::test_secure_append_creates_then_appends_complete_framed_records",
    "tests/test_officina_atomic_files.py::test_windows_native_secure_create_replace_append_and_acl",
    "tests/test_officina_dispatcher.py::test_python_process_target_keeps_gateway_and_entry_separate",
    "tests/test_officina_git_provenance.py::test_git_test_repository_preserves_exact_bytes_under_ambient_autocrlf",
    "skills/recurring-tasks/_rtx/tests/test_schedule_backend.py::test_linux_sync_writes_units_and_enables_timer",
    "tests/test_officina_blueprint_graph.py::test_content_ownership_accepts_equivalent_repository_alias",
    "tests/test_validator_runner.py::test_run_all_isolates_unmerged_index_and_restores_git_environment",
)


def test_runner_supplies_repo_src_pythonpath() -> None:
    assert runner._pytest_args(verbose=False) == [
        "-o",
        "pythonpath=src",
        "-q",
    ]


def mkdir(path: Path) -> None:
    path.mkdir(parents=True)


def test_precommit_discovers_skill_tests_except_install_tests(
    tmp_path: Path, monkeypatch
) -> None:
    mkdir(tmp_path / "tests")
    mkdir(tmp_path / "hooks" / "tests")
    mkdir(tmp_path / "skills" / "new-skill" / "tests")
    mkdir(tmp_path / "skills" / "new-skill" / "_rtx" / "tests")
    mkdir(tmp_path / "skills" / "skill-drift" / "tests")
    mkdir(tmp_path / "skills" / "install-assistant-tools" / "tests")
    mkdir(
        tmp_path
        / "skills"
        / "install-assistant-tools"
        / "_rtx"
        / "tests"
    )
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)

    assert runner._resolve_suite("precommit") == [
        "tests",
        "hooks/tests",
        "skills/new-skill/_rtx/tests",
        "skills/new-skill/tests",
        "skills/skill-drift/tests",
    ]


def test_full_discovers_install_tests(tmp_path: Path, monkeypatch) -> None:
    mkdir(tmp_path / "tests")
    mkdir(tmp_path / "hooks" / "tests")
    mkdir(tmp_path / "skills" / "new-skill" / "tests")
    mkdir(tmp_path / "skills" / "new-skill" / "_rtx" / "tests")
    mkdir(tmp_path / "skills" / "install-assistant-tools" / "tests")
    mkdir(
        tmp_path
        / "skills"
        / "install-assistant-tools"
        / "_rtx"
        / "tests"
    )
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)

    assert runner._resolve_suite("full") == [
        "tests",
        "hooks/tests",
        "skills/install-assistant-tools/_rtx/tests",
        "skills/install-assistant-tools/tests",
        "skills/new-skill/_rtx/tests",
        "skills/new-skill/tests",
    ]


def test_nested_module_tests_run_in_isolated_pytest_processes() -> None:
    assert runner._execution_groups(
        [
            "tests",
            "hooks/tests",
            "skills/alpha/_rtx/tests",
            "skills/alpha/tests",
            "skills/beta/_rtx/tests",
            "skills/beta/tests",
        ]
    ) == [
        [
            "tests",
            "hooks/tests",
            "skills/alpha/tests",
            "skills/beta/tests",
        ],
        ["skills/alpha/_rtx/tests"],
        ["skills/beta/_rtx/tests"],
    ]


def test_portability_suite_has_exact_early_failure_nodes() -> None:
    assert runner.PORTABILITY_TESTS == EXPECTED_PORTABILITY_TESTS
    assert runner._resolve_suite("portability") == list(EXPECTED_PORTABILITY_TESTS)


def test_ci_runs_portability_between_validators_and_full_suite() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "python-tests.yml"
    ).read_text(encoding="utf-8")

    validator = workflow.index("python3 validators/runner.py")
    portability = workflow.index(
        "python3 scripts/run-python-tests.py --suite portability --verbose"
    )
    full = workflow.index(
        "python3 scripts/run-python-tests.py --suite full --verbose"
    )

    assert validator < portability < full


def fake_runs(returncodes: list[int], calls: list[list[str]]):
    """Return a subprocess fake that makes each configured group observable."""

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=returncodes.pop(0))

    return run


def load_report(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_keep_going_runs_every_group_after_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """A failed group must not keep a later configured group unreachable."""
    groups = [["pytest", "first"], ["pytest", "second"]]
    calls: list[list[str]] = []
    report = tmp_path / "groups.json"
    monkeypatch.setattr(
        runner.subprocess, "run", fake_runs([1, 0], calls)
    )

    assert runner.run_groups(groups, keep_going=True, report_path=report) == 1
    assert calls == groups
    loaded = load_report(report)
    assert loaded["complete"] is True
    assert [row["group_id"] for row in loaded["groups"]] == [
        "group-1",
        "group-2",
    ]
    assert [row["command"] for row in loaded["groups"]] == groups
    assert [row["returncode"] for row in loaded["groups"]] == [1, 0]
    assert all(
        isinstance(row["wall_seconds"], float)
        and row["wall_seconds"] >= 0
        for row in loaded["groups"]
    )


def test_fail_fast_stops_after_the_first_failed_group(
    tmp_path: Path, monkeypatch
) -> None:
    """Reusable non-hook callers retain the runner's explicit fail-fast mode."""
    groups = [["pytest", "first"], ["pytest", "second"]]
    calls: list[list[str]] = []
    monkeypatch.setattr(
        runner.subprocess, "run", fake_runs([1, 0], calls)
    )

    assert (
        runner.run_groups(
            groups, keep_going=False, report_path=tmp_path / "report.json"
        )
        == 1
    )
    assert calls == [groups[0]]


def test_exhaustive_precommit_pytest_args_continue_after_collection_errors() -> None:
    """A collection failure must not suppress collectible tests in its group."""
    args = runner._suite_pytest_args(
        "precommit", verbose=False, keep_going=True
    )

    assert "--continue-on-collection-errors" in args
    assert "--maxfail" not in args


def test_interrupted_group_marks_report_incomplete(
    tmp_path: Path, monkeypatch
) -> None:
    """An interrupt while launching pytest is not an ordinary test failure."""
    report = tmp_path / "groups.json"

    def interrupt(_command: list[str], **_kwargs: object) -> SimpleNamespace:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner.subprocess, "run", interrupt)

    with pytest.raises(KeyboardInterrupt):
        runner.run_groups(
            [["pytest", "first"]], keep_going=True, report_path=report
        )

    loaded = load_report(report)
    assert loaded["complete"] is False
    assert loaded["groups"] == []


def test_group_launch_error_marks_report_incomplete(
    tmp_path: Path, monkeypatch
) -> None:
    """A process-launch error is infrastructure failure, not pytest failure."""
    report = tmp_path / "groups.json"

    def launch_error(_command: list[str], **_kwargs: object) -> SimpleNamespace:
        raise OSError("pytest launcher unavailable")

    monkeypatch.setattr(runner.subprocess, "run", launch_error)

    with pytest.raises(OSError, match="pytest launcher unavailable"):
        runner.run_groups(
            [["pytest", "first"]], keep_going=True, report_path=report
        )

    loaded = load_report(report)
    assert loaded["complete"] is False
    assert loaded["groups"] == []
