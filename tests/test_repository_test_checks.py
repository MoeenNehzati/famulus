from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "officina"
    / "repository_checks.py"
)
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
    "tests/test_repository_validator_checks.py::test_run_all_isolates_unmerged_index_and_restores_git_environment",
)


def test_runner_supplies_repo_src_pythonpath() -> None:
    assert runner._pytest_args(verbose=False) == [
        "-o",
        "pythonpath=src",
        "-q",
    ]


def test_runner_adds_exact_xdist_worker_count_for_parallel_jobs() -> None:
    assert runner._pytest_args(verbose=False, jobs=4) == [
        "-o",
        "pythonpath=src",
        "-q",
        "-n",
        "4",
        "--dist",
        "worksteal",
    ]


def test_default_jobs_uses_two_thirds_of_logical_cpus(monkeypatch) -> None:
    monkeypatch.setattr(runner.os, "cpu_count", lambda: 12)
    assert runner._default_jobs() == 8

    monkeypatch.setattr(runner.os, "cpu_count", lambda: 1)
    assert runner._default_jobs() == 1

    monkeypatch.setattr(runner.os, "cpu_count", lambda: None)
    assert runner._default_jobs() == 1


def _deselected_tests(args: list[str]) -> set[str]:
    return {
        args[index + 1]
        for index, argument in enumerate(args[:-1])
        if argument == "--deselect"
    }


def test_precommit_defers_installation_chrome_and_docstring_tests() -> None:
    deselected = _deselected_tests(
        runner._suite_pytest_args("precommit", verbose=False)
    )

    assert runner.INSTALLATION_TESTS <= deselected
    assert runner.CHROME_TESTS <= deselected
    assert runner.DOCSTRING_TESTS <= deselected


def test_prepush_restores_installation_and_chrome_but_defers_docstrings() -> None:
    deselected = _deselected_tests(
        runner._suite_pytest_args("pre-push", verbose=False)
    )

    assert deselected == runner.DOCSTRING_TESTS


def test_docstring_validator_is_reserved_for_full_unless_explicit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def run_all(*, repo_root, validator_ids, excluded_validator_ids):
        calls.append((tuple(validator_ids), tuple(excluded_validator_ids)))
        return {}

    monkeypatch.setattr(runner._validator_snapshot, "run_all", run_all)
    monkeypatch.setattr(
        runner._validator_snapshot,
        "_render_findings",
        lambda _results: 0,
    )
    monkeypatch.setattr(
        runner,
        "SUITE_PHASES",
        {
            "precommit": (("validators", None),),
            "pre-push": (("validators", None),),
            "full": (("validators", None),),
        },
    )

    assert runner.run_suite(tmp_path, "precommit") == 0
    assert runner.run_suite(tmp_path, "pre-push") == 0
    assert runner.run_suite(tmp_path, "full") == 0
    assert runner.run_suite(
        tmp_path,
        "pre-push",
        validator_ids=("repo/docstrings",),
    ) == 0

    assert calls == [
        ((), ("repo/docstrings",)),
        ((), ("repo/docstrings",)),
        ((), ()),
        (("repo/docstrings",), ()),
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

    validator = workflow.index(
        "python3 repo_checks.py --suite validators"
    )
    portability = workflow.index(
        "python3 repo_checks.py --suite portability --verbose"
    )
    full = workflow.index(
        "python3 repo_checks.py --suite tests --verbose"
    )

    assert validator < portability < full


def test_precommit_hook_uses_the_combined_root_suite() -> None:
    hook = (
        Path(__file__).resolve().parents[1]
        / ".githooks"
        / "pre-commit"
    ).read_text(encoding="utf-8")

    assert (
        'python3 "$REPO_ROOT/repo_checks.py" --suite precommit'
        in hook
    )
    assert '"$REPO_ROOT/validators/runner.py"' not in hook
    assert '"$REPO_ROOT/scripts/run-python-tests.py"' not in hook


def test_skill_hooks_select_validators_through_root_checks() -> None:
    expected = {
        "check-blueprints": "skill-maker/blueprints",
        "check-dependencies": "skill-maker/dependencies",
        "check-names": "skill-maker/names",
        "check-runtime-files": "repo/skill_runtime_files",
    }
    hook_root = Path(__file__).resolve().parents[1] / ".githooks" / "skill"

    for name, validator_id in expected.items():
        hook = (hook_root / name).read_text(encoding="utf-8")
        assert (
            'repo_checks.py" --suite validators --validator '
            f"{validator_id}"
        ) in hook
        assert "validators/runner.py" not in hook
