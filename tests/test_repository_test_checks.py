from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

MODULE_PATH = (
    REPO_ROOT
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

    assert runner.run_suite(tmp_path, "precommit", pooled=False) == 0
    assert runner.run_suite(tmp_path, "pre-push", pooled=False) == 0
    assert runner.run_suite(tmp_path, "full", pooled=False) == 0
    assert runner.run_suite(
        tmp_path,
        "pre-push",
        validator_ids=("repo/docstrings",),
        pooled=False,
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


def test_shared_test_jobs_reserve_capacity_for_isolated_tasks() -> None:
    assert runner._shared_test_jobs(1) == 1
    assert runner._shared_test_jobs(4) == 3
    assert runner._shared_test_jobs(8) == 6


def test_build_check_tasks_combines_validator_and_existing_test_groups(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner,
        "_resolve_suite",
        lambda _name: ["tests", "skills/example/_rtx/tests"],
    )

    tasks = runner._build_check_tasks(
        tmp_path,
        "precommit",
        verbose=False,
        jobs=8,
        validator_ids=(),
        excluded_validator_ids=(),
    )

    assert [task.id for task in tasks] == [
        "validators",
        "tests:shared",
        "tests:skills/example/_rtx/tests",
    ]
    assert [task.slots for task in tasks] == [1, 6, 1]
    assert "--internal-run-validators" in tasks[0].argv


def test_internal_validator_task_preserves_existing_snapshot_lifecycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        runner._validator_snapshot,
        "run_all",
        lambda **kwargs: calls.append(kwargs) or {"repo/example": ["finding"]},
    )
    monkeypatch.setattr(
        runner._validator_snapshot,
        "_render_findings",
        lambda findings: 1 if findings else 0,
    )

    status = runner._run_validator_task(
        tmp_path,
        validator_ids=("repo/example",),
        excluded_validator_ids=("repo/other",),
    )

    assert status == 1
    assert calls == [
        {
            "repo_root": tmp_path.resolve(),
            "validator_ids": ("repo/example",),
            "excluded_validator_ids": ("repo/other",),
        }
    ]


def test_internal_validator_cli_bypasses_suite_scheduling(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        runner,
        "_run_validator_task",
        lambda repo_root, **kwargs: calls.append((repo_root, kwargs)) or 1,
    )

    status = runner.main(
        [
            "--internal-run-validators",
            "--repo-root",
            str(tmp_path),
            "--validator",
            "repo/example",
        ]
    )

    assert status == 1
    assert calls == [
        (
            tmp_path,
            {
                "validator_ids": ("repo/example",),
                "excluded_validator_ids": (),
            },
        )
    ]


def test_pooled_runner_fills_budget_and_stops_admission_after_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    launched = []

    class FakeProcess:
        active = 0
        peak_active = 0

        def __init__(self, command, **_kwargs):
            self.command = command
            self.returncode = None
            launched.append(command[-1])
            type(self).active += 1
            type(self).peak_active = max(type(self).peak_active, type(self).active)

        def poll(self):
            if self.returncode is None:
                self.returncode = int(self.command[-1])
                type(self).active -= 1
            return self.returncode

        def wait(self, timeout=None):
            return self.poll()

        def terminate(self):
            self.returncode = 130

        def kill(self):
            self.returncode = 130

    monkeypatch.setattr(runner.subprocess, "Popen", FakeProcess)
    tasks = [
        runner.CheckTask("first", ("check", "1"), 1),
        runner.CheckTask("second", ("check", "2"), 1),
        runner.CheckTask("not-started", ("check", "0"), 1),
    ]

    status = runner._run_check_tasks(
        tasks,
        repo_root=tmp_path,
        jobs=2,
        pooled=True,
    )

    assert status == 1
    assert launched == ["1", "2"]
    assert FakeProcess.peak_active == 2


def test_windows_process_tree_termination_uses_taskkill_tree_mode(monkeypatch) -> None:
    calls = []

    class FakeProcess:
        pid = 4312

    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    runner._terminate_windows_process_tree(FakeProcess(), force=False)
    runner._terminate_windows_process_tree(FakeProcess(), force=True)

    assert calls[0][0] == ["taskkill", "/PID", "4312", "/T"]
    assert calls[1][0] == ["taskkill", "/PID", "4312", "/T", "/F"]
    assert all(call[1]["check"] is False for call in calls)


def test_sequential_control_uses_the_same_check_tasks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        runner._validator_snapshot,
        "run_all",
        lambda **kwargs: calls.append(("validators", kwargs)) or {},
    )
    monkeypatch.setattr(
        runner._validator_snapshot,
        "_render_findings",
        lambda findings: 0 if not findings else 1,
    )
    monkeypatch.setattr(
        runner,
        "_run_test_suite",
        lambda *args, **kwargs: calls.append(("tests", args, kwargs)) or 0,
    )
    monkeypatch.setattr(
        runner,
        "_build_check_tasks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pooled path used during sequential run")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_run_check_tasks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pooled path used during sequential run")
        ),
    )

    status = runner.run_suite(tmp_path, "precommit", jobs=1, pooled=False)

    assert status == 0
    assert calls[0][0] == "validators"
    assert calls[0][1]["repo_root"] == tmp_path.resolve()
    assert calls[0][1]["validator_ids"] == ()
    assert calls[0][1]["excluded_validator_ids"] == ("repo/docstrings",)
    assert calls[1] == ("tests", ("precommit",), {"verbose": False, "jobs": 1})


def test_xdist_required_for_parallel_jobs(monkeypatch) -> None:
    monkeypatch.setattr(
        runner,
        "_pytest_xdist_available",
        lambda: False,
    )

    with pytest.raises(RuntimeError, match="pytest-xdist"):
        runner.run_suite(Path("/tmp"), "full", jobs=4)



def test_each_pytest_task_receives_an_isolated_cache_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands = []

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            commands.append(command)
            self.returncode = None

        def poll(self):
            self.returncode = 0
            return 0

        def wait(self, timeout=None):
            return self.poll()

    monkeypatch.setattr(runner.subprocess, "Popen", FakeProcess)
    pytest_argv = (runner.sys.executable, "-m", "pytest", "-q", "tests")
    tasks = [
        runner.CheckTask("tests:shared", pytest_argv, 1),
        runner.CheckTask("tests:isolated", pytest_argv, 1),
    ]

    assert runner._run_check_tasks(
        tasks,
        repo_root=tmp_path,
        jobs=2,
        pooled=True,
    ) == 0

    cache_options = [
        next(argument for argument in command if argument.startswith("cache_dir="))
        for command in commands
    ]
    assert len(set(cache_options)) == 2


def test_portability_suite_has_exact_early_failure_nodes() -> None:
    assert runner.PORTABILITY_TESTS == EXPECTED_PORTABILITY_TESTS
    assert runner._resolve_suite("portability") == list(EXPECTED_PORTABILITY_TESTS)


def test_ci_runs_combined_full_suite_before_portability() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "python-tests.yml"
    ).read_text(encoding="utf-8")

    full = workflow.index("python3 repo_checks.py --suite full --verbose")
    portability = workflow.index(
        "python3 repo_checks.py --suite portability --verbose"
    )

    assert full < portability
    assert "pytest-xdist" in workflow
    assert "python3 repo_checks.py --suite validators" not in workflow
    assert "python3 repo_checks.py --suite tests --verbose" not in workflow


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
