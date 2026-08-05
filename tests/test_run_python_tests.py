from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run-python-tests.py"
SPEC = importlib.util.spec_from_file_location("run_python_tests", MODULE_PATH)
assert SPEC is not None
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


EXPECTED_PORTABILITY_TESTS = (
    "tests/test_officina_atomic_files.py::test_secure_append_creates_then_appends_complete_framed_records",
    "tests/test_officina_atomic_files.py::test_windows_native_secure_create_replace_append_and_acl",
    "tests/test_officina_python_machine_interface.py::test_python_process_target_rejects_noncanonical_fields",
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
