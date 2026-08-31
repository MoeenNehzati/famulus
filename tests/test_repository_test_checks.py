from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

MODULE_PATH = (
    REPO_ROOT
    / "src"
    / "officina"
    / "repository"
    / "checks"
    / "runner.py"
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
    "tests/test_dispatcher_direct_authorization.py::test_direct_python_process_target_keeps_gateway_and_entry_separate",
    "tests/test_officina_git_provenance.py::test_git_test_repository_preserves_exact_bytes_under_ambient_autocrlf",
    "tests/test_officina_recurring_managed.py::test_native_renderers_preserve_only_exact_bounded_environment",
    "tests/test_officina_blueprint_graph.py::test_content_ownership_accepts_equivalent_repository_alias",
    "tests/test_repository_validator_checks.py::test_run_all_isolates_unmerged_index_and_restores_git_environment",
)


def test_named_suites_resolve_to_ordered_native_phases() -> None:
    """Catch reintroduction of a task graph or split runner routes."""
    assert runner.SUITE_PHASES == {
        "validators": ("validators",),
        "tests": ("tests:shared", "tests:performance", "tests:browser"),
        "precommit": ("validators", "tests:shared"),
        "pre-push": ("validators", "tests:shared", "tests:browser"),
        "portability": ("tests:shared",),
        "full": (
            "tests:performance",
            "validators",
            "tests:shared",
            "tests:browser",
        ),
    }


def test_combined_suites_share_one_pooled_run() -> None:
    """Catch reconstruction of a validator-first execution barrier."""
    assert runner._suite_runs("precommit", task_id=None) == ("combined",)
    assert runner._suite_runs("pre-push", task_id=None) == (
        "combined",
        "tests:browser",
    )
    assert runner._suite_runs("full", task_id=None) == (
        "tests:performance",
        "combined",
        "tests:browser",
    )


def test_repository_view_defaults_are_explicit_by_suite() -> None:
    assert runner._resolve_repository_view("precommit", "auto") == "staged"
    for suite in ("validators", "tests", "pre-push", "portability", "full"):
        assert runner._resolve_repository_view(suite, "auto") == "working"
    assert runner._resolve_repository_view("full", "staged") == "staged"
    assert runner._resolve_repository_view("precommit", "working") == "working"


def test_validator_collection_keeps_ordinary_pytest_items(tmp_path: Path) -> None:
    """Catch a validator plugin that erases default pytest collection."""
    validator_path = (tmp_path / "validators" / "example.py").resolve()
    test_path = (tmp_path / "tests" / "test_example.py").resolve()
    plugin = object.__new__(runner.ValidatorPytestPlugin)
    plugin.path_ids = {validator_path: "repo/example"}
    validator_item = SimpleNamespace(
        path=validator_path,
        _validator_id="repo/example",
    )
    duplicate_default_item = SimpleNamespace(path=validator_path)
    ordinary_item = SimpleNamespace(path=test_path)
    items = [validator_item, duplicate_default_item, ordinary_item]

    plugin.pytest_collection_modifyitems(items)

    assert items == [validator_item, ordinary_item]


def test_real_pytest_collection_combines_validator_and_standard_items(
    tmp_path: Path,
) -> None:
    """Catch separate pytest sessions for validator and ordinary items."""
    staged_paths = tmp_path / "staged-paths.json"
    staged_paths.write_text("[]\n", encoding="utf-8")
    ordinary_node = (
        "tests/test_repository_test_checks.py::"
        "test_runner_supplies_repo_src_pythonpath"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "officina.repository.checks.runner",
            "--collect-only",
            "-q",
            "--officina-run-validators",
            "--officina-validator-root",
            str(REPO_ROOT),
            "--officina-validator-display-root",
            str(REPO_ROOT),
            "--officina-staged-paths-file",
            str(staged_paths),
            "--officina-validator",
            "repo/portable_dates",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(SRC_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "validators/portable_dates.py::test_portable_dates" in completed.stdout
    assert ordinary_node in completed.stdout


def test_worker_assignment_metrics_record_per_worker_busy_and_idle_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measure scheduler assignment separately from descendant CPU usage."""
    output = tmp_path / "workers.json"
    clock = iter((10.0, 20.0))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    plugin = runner._WorkerAssignmentMetricsPlugin(output)

    plugin.pytest_sessionstart(SimpleNamespace())
    for report in (
        SimpleNamespace(worker_id="gw0", when="setup", duration=1.0, skipped=False),
        SimpleNamespace(worker_id="gw0", when="call", duration=3.0, skipped=False),
        SimpleNamespace(worker_id="gw0", when="teardown", duration=1.0, skipped=False),
        SimpleNamespace(worker_id="gw1", when="setup", duration=2.0, skipped=True),
    ):
        plugin.pytest_runtest_logreport(report)
    plugin.pytest_sessionfinish(SimpleNamespace(), 0)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 1,
        "session_seconds": 10.0,
        "workers": [
            {
                "worker_id": "gw0",
                "item_count": 1,
                "assigned_seconds": 5.0,
                "unassigned_seconds": 5.0,
                "assigned_fraction": 0.5,
            },
            {
                "worker_id": "gw1",
                "item_count": 1,
                "assigned_seconds": 2.0,
                "unassigned_seconds": 8.0,
                "assigned_fraction": 0.2,
            },
        ],
    }


def test_worker_metrics_plugin_registers_only_on_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "workers.json"
    monkeypatch.setenv("OFFICINA_PYTEST_WORKER_METRICS", str(output))

    class PluginManager:
        def __init__(self) -> None:
            self.registered: list[tuple[object, str]] = []

        def register(self, plugin: object, name: str) -> None:
            self.registered.append((plugin, name))

    controller = SimpleNamespace(
        pluginmanager=PluginManager(),
        getoption=lambda _name: False,
    )
    worker = SimpleNamespace(
        pluginmanager=PluginManager(),
        getoption=lambda _name: False,
        workerinput={},
    )

    runner.pytest_configure(controller)
    runner.pytest_configure(worker)

    assert len(controller.pluginmanager.registered) == 1
    plugin, name = controller.pluginmanager.registered[0]
    assert isinstance(plugin, runner._WorkerAssignmentMetricsPlugin)
    assert name == "officina-worker-assignment-metrics"
    assert worker.pluginmanager.registered == []


def test_worker_metrics_plugin_satisfies_pytest_hook_contract(tmp_path: Path) -> None:
    """Catch hook parameters that pluggy cannot match to pytest specifications."""
    plugin_manager = pytest.PytestPluginManager()
    plugin_manager.register(
        runner._WorkerAssignmentMetricsPlugin(tmp_path / "workers.json")
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


def test_full_pooled_phase_defers_browser_tests_and_uses_worksteal() -> None:
    args = runner._suite_pytest_args("full", verbose=False, jobs=6)
    assert args[args.index("--dist") + 1] == "worksteal"
    assert runner.CHROME_TESTS <= _deselected_tests(args)


@pytest.mark.parametrize("suite", ["precommit", "pre-push", "portability"])
def test_browser_free_suites_keep_worksteal(suite: str) -> None:
    args = runner._suite_pytest_args(suite, verbose=False, jobs=6)
    assert args[args.index("--dist") + 1] == "worksteal"


def test_serial_browser_suite_adds_no_distribution_mode() -> None:
    args = runner._suite_pytest_args("full", verbose=False, jobs=1)
    assert "--dist" not in args


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


def test_precommit_defers_chrome_docstring_and_performance_tests() -> None:
    deselected = _deselected_tests(
        runner._suite_pytest_args("precommit", verbose=False)
    )

    assert runner.CHROME_TESTS <= deselected
    assert runner.DOCSTRING_TESTS <= deselected
    assert runner.PERFORMANCE_TESTS <= deselected


def test_prepush_defers_browser_and_slow_tests_from_parallel_pool() -> None:
    deselected = _deselected_tests(
        runner._suite_pytest_args("pre-push", verbose=False)
    )

    assert deselected == (
        runner.CHROME_TESTS | runner.DOCSTRING_TESTS | runner.PERFORMANCE_TESTS
    )


def test_docstring_validator_is_reserved_for_full_unless_explicit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands = []
    monkeypatch.setattr(runner, "_capture_working_staged_paths", lambda _root: ())
    monkeypatch.setattr(
        runner,
        "_run_process",
        lambda command, **_kwargs: commands.append(command) or 0,
    )

    assert runner.run_suite(tmp_path, "pre-push", repository_view="working") == 0
    assert runner.run_suite(tmp_path, "full", repository_view="working") == 0
    assert runner.run_suite(
        tmp_path,
        "pre-push",
        validator_ids=("repo/docstrings",),
        repository_view="working",
    ) == 0
    combined = [command for command in commands if "--officina-run-validators" in command]
    assert "repo/docstrings" in combined[0]
    assert "--officina-exclude-validator" not in combined[1]
    assert "--officina-validator" in combined[2]
    assert "--officina-exclude-validator" not in combined[2]


def test_functional_phase_uses_native_discovery_without_explicit_roots(
    tmp_path: Path,
) -> None:
    command = runner._pytest_phase_command(
        "full",
        "tests:shared",
        verbose=False,
        jobs=8,
        cache_dir=tmp_path / "cache",
        timing_path=None,
    )

    assert command[command.index("-n") + 1] == "8"
    assert command[command.index("--dist") + 1] == "worksteal"
    assert not any(argument in {"tests", "hooks/tests", "skills"} for argument in command)
    assert _deselected_tests(command) == (
        runner.CHROME_TESTS | runner.PERFORMANCE_TESTS
    )


def test_combined_command_enables_validators_in_the_same_xdist_session(
    tmp_path: Path,
) -> None:
    staged_paths = tmp_path / "staged-paths.json"
    command = runner._pytest_phase_command(
        "precommit",
        "combined",
        verbose=False,
        jobs=8,
        cache_dir=tmp_path / "cache",
        timing_path=None,
        validator_root=tmp_path,
        validator_display_root=tmp_path,
        staged_paths_file=staged_paths,
        validator_ids=("repo/example",),
        excluded_validator_ids=("repo/other",),
    )

    assert command[command.index("-n") + 1] == "8"
    assert "--officina-run-validators" in command
    assert command[command.index("--officina-validator-root") + 1] == str(tmp_path)
    assert command[command.index("--officina-validator") + 1] == "repo/example"
    assert command[command.index("--officina-exclude-validator") + 1] == "repo/other"


def test_full_runs_performance_before_pooled_and_browser_phases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands = []
    statuses = iter((0, 1, 0))
    monkeypatch.setattr(
        runner,
        "_capture_working_staged_paths",
        lambda _root: (),
    )
    monkeypatch.setattr(
        runner,
        "_run_process",
        lambda command, **_kwargs: commands.append(command) or next(statuses),
    )

    status = runner.run_suite(
        tmp_path,
        "full",
        jobs=2,
        repository_view="working",
    )

    assert status == 1
    assert len(commands) == 3
    assert commands[0][-len(runner.PERFORMANCE_TESTS) :] == sorted(
        runner.PERFORMANCE_TESTS
    )
    assert "--officina-run-validators" in commands[1]
    assert commands[2][-len(runner.CHROME_TESTS) :] == sorted(runner.CHROME_TESTS)


def test_task_selector_runs_only_the_requested_phase(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []
    validator_path = tmp_path / "validators" / "example.py"
    monkeypatch.setattr(runner, "_capture_working_staged_paths", lambda _root: ())
    monkeypatch.setattr(
        runner._validator_snapshot,
        "_selected_validator_paths",
        lambda *_args, **_kwargs: (("repo/example", validator_path),),
    )
    monkeypatch.setattr(
        runner,
        "_run_process",
        lambda command, **kwargs: calls.append((command, kwargs)) or 1,
    )

    status = runner.run_suite(
        tmp_path,
        "full",
        task_id="validators",
        validator_ids=("repo/example",),
    )

    assert status == 1
    assert len(calls) == 1
    assert "--officina-run-validators" in calls[0][0]
    assert str(validator_path) in calls[0][0]
    assert calls[0][1]["task_id"] == "validators"


def test_portability_task_keeps_its_identity_at_the_process_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Catch portability evidence mislabeled as the broader shared task."""

    calls = []
    monkeypatch.setattr(
        runner,
        "_run_process",
        lambda command, **kwargs: calls.append((command, kwargs)) or 0,
    )

    assert runner.run_suite(
        tmp_path,
        "full",
        task_id="tests:portability",
        jobs=1,
    ) == 0

    assert len(calls) == 1
    assert calls[0][1]["task_id"] == "tests:portability"
    assert calls[0][0][-len(runner.PORTABILITY_TESTS) :] == sorted(
        runner.PORTABILITY_TESTS
    )


def test_phase_runner_continues_after_performance_and_pooled_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    launched = []
    monkeypatch.setattr(runner, "_capture_working_staged_paths", lambda _root: ())
    monkeypatch.setattr(
        runner,
        "_run_process",
        lambda _command, **kwargs: launched.append(kwargs["task_id"]) or 5,
    )

    assert runner.run_suite(tmp_path, "full", jobs=8) == 5
    assert launched == ["tests:performance", "tests:shared", "tests:browser"]


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


def test_sequential_flag_is_a_noop_compatibility_alias(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(runner, "_capture_working_staged_paths", lambda _root: ())
    monkeypatch.setattr(
        runner,
        "_run_process",
        lambda command, **kwargs: calls.append((command, kwargs)) or 0,
    )

    common = [
        "--suite", "precommit", "--repo-root", str(tmp_path), "--jobs", "1",
        "--repository-view", "working",
    ]
    assert runner.main(common) == 0
    first_command, first_kwargs = calls[0]
    calls.clear()
    assert runner.main([*common, "--sequential"]) == 0
    second_command, second_kwargs = calls[0]
    normalize = lambda command: [
        "cache_dir=<temporary>"
        if argument.startswith("cache_dir=")
        else "staged-paths=<temporary>"
        if Path(argument).name == "staged-paths.json"
        else argument
        for argument in command
    ]
    assert normalize(second_command) == normalize(first_command)
    assert {
        **second_kwargs,
        "pycache_prefix": "<temporary>",
    } == {
        **first_kwargs,
        "pycache_prefix": "<temporary>",
    }


def test_performance_phase_is_serial_and_uses_only_performance_nodes(
    tmp_path: Path,
) -> None:
    command = runner._pytest_phase_command(
        "full",
        "tests:performance",
        verbose=False,
        jobs=8,
        cache_dir=tmp_path / "cache",
        timing_path=None,
    )

    assert command[:5] == [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "pythonpath=src",
    ]
    assert "-n" not in command
    assert command[-len(runner.PERFORMANCE_TESTS) :] == sorted(runner.PERFORMANCE_TESTS)


def test_browser_phase_is_serial_and_uses_only_browser_modules(tmp_path: Path) -> None:
    command = runner._pytest_phase_command(
        "pre-push",
        "tests:browser",
        verbose=False,
        jobs=8,
        cache_dir=tmp_path / "cache",
        timing_path=None,
    )

    assert "-n" not in command
    assert "--maxfail=1" in command
    assert command[-len(runner.CHROME_TESTS) :] == sorted(runner.CHROME_TESTS)


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
    assert runner.run_suite(tmp_path, "tests", jobs=2) == 0

    cache_options = [
        next(argument for argument in command if argument.startswith("cache_dir="))
        for command in commands
    ]
    assert len(set(cache_options)) == len(runner.SUITE_PHASES["tests"])


def test_phase_runner_writes_per_file_timing_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands = []

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            commands.append(command)
            self.returncode = None
            junit_argument = next(
                argument
                for argument in command
                if argument.startswith("--junitxml=")
            )
            junit_path = Path(junit_argument.partition("=")[2])
            junit_path.parent.mkdir(parents=True, exist_ok=True)
            junit_path.write_text(
                '<?xml version="1.0" encoding="utf-8"?>'
                '<testsuites><testsuite tests="2" time="0.750">'
                '<testcase classname="tests.test_example.Example" '
                'name="test_one" time="0.250" />'
                '<testcase classname="tests.test_example.Example" '
                'name="test_two" time="0.500"><skipped /></testcase>'
                '</testsuite></testsuites>',
                encoding="utf-8",
            )

        def poll(self):
            self.returncode = 0
            return 0

        def wait(self, timeout=None):
            return self.poll()

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(runner.subprocess, "Popen", FakeProcess)
    report_path = tmp_path / "timings.json"

    assert runner.run_suite(
        tmp_path,
        "tests",
        jobs=1,
        timing_output=report_path,
        task_id="tests:shared",
    ) == 0

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["repo"] == str(tmp_path.resolve())
    assert payload["tasks"][0]["task_id"] == "tests:shared"
    assert payload["tasks"][0]["pytest_seconds"] == pytest.approx(0.75)
    assert payload["files"] == [
        {
            "task_id": "tests:shared",
            "kind": "test",
            "path": "tests/test_example.py",
            "item_count": 2,
            "seconds": pytest.approx(0.75),
            "passed": 1,
            "failed": 0,
            "skipped": 1,
        }
    ]


def test_timing_output_cli_is_forwarded_to_suite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        runner,
        "run_suite",
        lambda repo_root, suite, **kwargs: calls.append(
            (repo_root, suite, kwargs)
        )
        or 0,
    )
    monkeypatch.setattr(runner, "_pytest_xdist_available", lambda: True)
    timing_output = tmp_path / "timings.json"

    assert runner.main(
        [
            "--suite",
            "tests",
            "--repo-root",
            str(tmp_path),
            "--jobs",
            "1",
            "--timing-output",
            str(timing_output),
        ]
    ) == 0

    assert calls == [
        (
            tmp_path,
            "tests",
            {
                "verbose": False,
                "jobs": 1,
                "validator_ids": (),
                "excluded_validator_ids": (),
                "timing_output": timing_output,
                "task_id": None,
                "task_cache_dir": None,
                "repository_view": "auto",
                "selectors": (),
            },
        )
    ]


def test_probe_task_environment_is_copied_per_child_without_parent_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    received = []

    class FakeProcess:
        def __init__(self, _command, **kwargs):
            received.append(kwargs.get("env"))
            self.returncode = None

        def poll(self):
            self.returncode = 0
            return 0

        def wait(self, timeout=None):
            return self.poll()

    monkeypatch.setenv("OFFICINA_FIXTURE_PROBE_DIR", str(tmp_path / "probe"))
    monkeypatch.delenv("OFFICINA_FIXTURE_PROBE_TASK_ID", raising=False)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "ambient-cache"))
    monkeypatch.setattr(runner.subprocess, "Popen", FakeProcess)
    cache_one = tmp_path.parent / f"{tmp_path.name}-cache-one"
    cache_two = tmp_path.parent / f"{tmp_path.name}-cache-two"

    assert runner._run_process(
        ["check", "one"],
        cwd=tmp_path,
        task_id="one",
        pycache_prefix=cache_one,
    ) == 0
    assert runner._run_process(
        ["check", "two"],
        cwd=tmp_path,
        task_id="two",
        pycache_prefix=cache_two,
    ) == 0

    assert [
        environment["OFFICINA_FIXTURE_PROBE_TASK_ID"]
        for environment in received
    ] == ["one", "two"]
    assert [environment["PYTHONPYCACHEPREFIX"] for environment in received] == [
        str(cache_one.resolve()),
        str(cache_two.resolve()),
    ]
    assert all(
        "PYTHONDONTWRITEBYTECODE" not in environment
        for environment in received
    )
    assert "OFFICINA_FIXTURE_PROBE_TASK_ID" not in __import__("os").environ
    assert os.environ["PYTHONDONTWRITEBYTECODE"] == "1"
    assert os.environ["PYTHONPYCACHEPREFIX"] == str(tmp_path / "ambient-cache")


def test_process_runner_removes_hook_git_routing_from_child_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    received = []

    class FakeProcess:
        def __init__(self, _command, **kwargs):
            received.append(kwargs["env"])

        def wait(self):
            return 0

    routing_variables = (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
    )
    for name in routing_variables:
        monkeypatch.setenv(name, f"hostile-{name.lower()}")
    monkeypatch.setenv("UNRELATED_ENVIRONMENT_VALUE", "retained")
    monkeypatch.setattr(runner.subprocess, "Popen", FakeProcess)

    assert runner._run_process(
        ["check"],
        cwd=tmp_path,
        task_id="git-environment-probe",
        pycache_prefix=tmp_path.parent / f"{tmp_path.name}-pycache",
    ) == 0

    child_environment = received[0]
    assert all(name not in child_environment for name in routing_variables)
    assert child_environment["UNRELATED_ENVIRONMENT_VALUE"] == "retained"
    assert all(name in os.environ for name in routing_variables)


def test_native_smoke_opt_ins_are_scoped_to_their_child_processes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Catch native CI probes that depend on workflow-only environment wiring."""

    received = []

    class FakeProcess:
        def __init__(self, _command, **kwargs):
            received.append(kwargs["env"])

        def wait(self):
            return 0

    monkeypatch.delenv("FAMULUS_REQUIRE_NATIVE_KEYRING", raising=False)
    monkeypatch.delenv("FAMULUS_RUN_SCHEDULER_SMOKE", raising=False)
    monkeypatch.setattr(runner.subprocess, "Popen", FakeProcess)

    for task_id in ("native:keyring", "native:scheduler"):
        assert runner._run_process(
            ["check"],
            cwd=tmp_path,
            task_id=task_id,
            pycache_prefix=(
                tmp_path.parent
                / f"{tmp_path.name}-{task_id.replace(':', '-')}"
            ),
        ) == 0

    assert received[0]["FAMULUS_REQUIRE_NATIVE_KEYRING"] == "1"
    assert "FAMULUS_RUN_SCHEDULER_SMOKE" not in received[0]
    assert received[1]["FAMULUS_RUN_SCHEDULER_SMOKE"] == "1"
    assert "FAMULUS_REQUIRE_NATIVE_KEYRING" not in received[1]
    assert "FAMULUS_REQUIRE_NATIVE_KEYRING" not in os.environ
    assert "FAMULUS_RUN_SCHEDULER_SMOKE" not in os.environ


def test_process_runner_keeps_execution_root_free_of_bytecode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Catch validator-visible cache files created by the shared pytest child."""
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    (tmp_path / "sample_module.py").write_text("VALUE = 1\n", encoding="utf-8")
    observed_prefix = tmp_path / "observed-prefix.txt"
    pycache_prefix = tmp_path.parent / f"{tmp_path.name}-pycache"

    assert runner._run_process(
        [
            sys.executable,
            "-c",
            (
                "import sample_module, sys; "
                "from pathlib import Path; "
                "Path('observed-prefix.txt').write_text("
                "str(sys.pycache_prefix), encoding='utf-8')"
            ),
        ],
        cwd=tmp_path,
        task_id="bytecode-probe",
        pycache_prefix=pycache_prefix,
    ) == 0
    assert (
        Path(observed_prefix.read_text(encoding="utf-8"))
        == pycache_prefix.resolve()
    )
    assert tuple(pycache_prefix.rglob("*.pyc"))
    assert not tuple(tmp_path.rglob("*.pyc"))


def test_process_runner_rejects_pycache_inside_execution_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside the execution root"):
        runner._run_process(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            task_id="invalid-bytecode-probe",
            pycache_prefix=tmp_path / "pycache",
        )


def test_portability_suite_has_exact_early_failure_nodes() -> None:
    assert runner.PORTABILITY_TESTS == EXPECTED_PORTABILITY_TESTS
    command = runner._pytest_phase_command(
        "portability",
        "tests:shared",
        verbose=False,
        jobs=1,
        cache_dir=Path("cache"),
        timing_path=None,
    )
    assert command[-len(EXPECTED_PORTABILITY_TESTS) :] == list(
        EXPECTED_PORTABILITY_TESTS
    )


def test_ci_runs_combined_full_suite_before_portability() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "python-tests.yml"
    ).read_text(encoding="utf-8")

    full = workflow.index("python3 repo_checks.py --suite full --verbose --jobs 1")
    portability = workflow.index(
        "python3 repo_checks.py --suite full --task tests:portability"
    )

    assert full < portability
    assert "python3 -m pip install -r requirements-ci.txt" in workflow
    assert "python3 repo_checks.py --suite validators" not in workflow
    assert "python3 repo_checks.py --suite tests --verbose" not in workflow


def test_ci_runs_browser_behavior_only_on_stable_hosts() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "python-tests.yml"
    ).read_text(encoding="utf-8")
    parsed = yaml.safe_load(workflow)
    test_job = parsed["jobs"]["test"]
    assert test_job["strategy"]["matrix"]["include"] == [
        {
            "os": "ubuntu-latest",
            "task": "combined",
            "artifact": "ubuntu-combined",
        },
        {
            "os": "macos-latest",
            "task": "validators",
            "jobs": 4,
            "artifact": "macos-validators",
        },
        {
            "os": "macos-latest",
            "task": "tests:shared",
            "jobs": 4,
            "artifact": "macos-tests-shared",
        },
        {
            "os": "macos-latest",
            "task": "tests:performance",
            "jobs": 4,
            "artifact": "macos-tests-performance",
        },
        {
            "os": "windows-latest",
            "task": "validators",
            "jobs": 4,
            "artifact": "windows-validators",
        },
        {
            "os": "windows-latest",
            "task": "tests:shared",
            "jobs": 4,
            "artifact": "windows-tests-shared",
        },
        {
            "os": "windows-latest",
            "task": "tests:performance",
            "jobs": 4,
            "artifact": "windows-tests-performance",
        },
        {
            "os": "windows-latest",
            "task": "tests:browser",
            "jobs": 1,
            "artifact": "windows-tests-browser",
        },
    ]
    assert "env" not in test_job
    steps = {step["name"]: step for step in test_job["steps"] if "name" in step}
    assert steps["Run repository checks"]["env"] == {
        "FAMULUS_REQUIRE_BROWSER": "1"
    }
    assert steps["Run repository check shard"]["env"] == {
        "FAMULUS_REQUIRE_BROWSER": "${{ matrix.task == 'tests:browser' && '1' || '0' }}"
    }
    assert parsed["jobs"]["probe"]["env"] == {
        "FAMULUS_REQUIRE_BROWSER": (
            "${{ (inputs.task == 'tests:browser' || inputs.task == 'combined') "
            "&& '1' || '0' }}"
        )
    }
    assert "matrix.os == 'macos-latest' && matrix.task == 'tests:performance'" in workflow
    assert "FAMULUS_RUN_PERFORMANCE_GATES: '1'" not in workflow
    assert 'if: matrix.task == \'combined\'' in workflow
    assert 'if: matrix.task != \'combined\'' in workflow
    assert (
        'python3 repo_checks.py --suite full --task "${{ matrix.task }}" '
        '--verbose --jobs "${{ matrix.jobs }}"'
    ) in workflow
    assert workflow.count("timeout-minutes: 20") == 1
    assert workflow.count("timeout-minutes: 10") == 1


def test_ci_workflow_dispatches_a_full_matrix_or_one_safe_probe() -> None:
    """Catch remote debugging inputs that bypass exact-SHA or argv boundaries."""

    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "python-tests.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    for input_name in (
        "mode",
        "request_id",
        "expected_sha",
        "os",
        "task",
        "selector",
        "jobs",
        "profile",
    ):
        assert f"      {input_name}:" in workflow
    assert "run-name:" in workflow
    assert "inputs.request_id" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "inputs.mode == 'matrix'" in workflow
    assert "inputs.mode == 'probe'" in workflow
    assert "REPO_CHECKS_EXPECTED_SHA: ${{ inputs.expected_sha }}" in workflow
    assert "REPO_CHECKS_TASK: ${{ inputs.task }}" in workflow
    assert "REPO_CHECKS_SELECTOR: ${{ inputs.selector }}" in workflow
    assert "json.loads(raw_selectors)" in workflow
    assert 'if raw_selectors.startswith("[")' in workflow
    assert "else [raw_selectors] if raw_selectors else []" in workflow
    assert 'command.extend(["--selector", selector])' in workflow
    assert 'if task != "combined":' in workflow
    assert "inputs.selector == '[]'" in workflow
    assert workflow.count("Run probe portability sentinel") == 1
    assert workflow.count("Run probe native keyring smoke") == 1
    assert workflow.count("Run probe native recurring scheduler smoke") == 1
    assert "--task native:keyring" in workflow
    assert "--task native:scheduler" in workflow
    assert ".repo-checks/portability.json" in workflow
    assert ".repo-checks/native-keyring.json" in workflow
    assert ".repo-checks/native-scheduler.json" in workflow
    assert '["git", "rev-parse", "HEAD"]' in workflow
    assert "--timing-output" in workflow
    assert "if: always()" in workflow
    assert "actions/upload-artifact@" in workflow

    in_run_block = False
    for line in workflow.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("run:"):
            in_run_block = True
            continue
        if in_run_block and line and not line.startswith(" " * 10):
            in_run_block = False
        if in_run_block:
            assert "${{ inputs." not in line

    assert "<<:" not in workflow


def test_ci_dependency_lock_covers_the_complete_test_environment() -> None:
    requirements = (
        Path(__file__).resolve().parents[1] / "requirements-ci.txt"
    ).read_text(encoding="utf-8").splitlines()

    assert requirements == [
        "pytest==8.3.4",
        "pytest-xdist==3.8.0",
        "PyYAML==6.0.2",
        "jsonschema==4.23.0",
        "mcp>=1,<2",
        "keyring==25.6.0",
        "cryptography==44.0.1",
        "lark==1.3.1",
        "pyflakes==3.2.0",
        "tzdata==2026.3",
    ]


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


@pytest.mark.parametrize("commit_only", (False, True))
def test_precommit_hook_commits_synchronized_plugin_versions(
    tmp_path: Path,
    commit_only: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a hook that leaves the two committed plugin versions stale."""

    from test_support.git_repository import GitTestRepository, isolated_git_environment

    repository = GitTestRepository.create(tmp_path / "repository")
    (repository.root / "pyproject.toml").write_text(
        '[project]\nname = "famulus-officina"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    for directory in (".claude-plugin", ".codex-plugin"):
        target = repository.root / directory / "plugin.json"
        target.parent.mkdir(parents=True)
        target.write_text(
            '{\n  "name": "famulus",\n  "version": "0.1.0"\n}\n',
            encoding="utf-8",
        )
    hooks = repository.root / ".githooks"
    scripts = repository.root / "scripts"
    hooks.mkdir()
    scripts.mkdir()
    shutil.copy2(REPO_ROOT / ".githooks" / "pre-commit", hooks / "pre-commit")
    shutil.copy2(
        REPO_ROOT / "scripts" / "sync-release-version.py",
        scripts / "sync-release-version.py",
    )
    (repository.root / "repo_checks.py").write_text(
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    repository.git("add", ".")
    repository.git("commit", "--quiet", "-m", "baseline")
    repository.git("config", "core.hooksPath", ".githooks")

    (repository.root / "pyproject.toml").write_text(
        '[project]\nname = "famulus-officina"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    repository.git("add", "pyproject.toml")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    true_binary = shutil.which("true")
    assert true_binary is not None
    fake_gitleaks = fake_bin / ("gitleaks.exe" if os.name == "nt" else "gitleaks")
    shutil.copyfile(true_binary, fake_gitleaks)
    fake_gitleaks.chmod(0o755)
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "ambient.index"))

    # famulus-raw-git: category=hook-contract; reason=the test must exercise Git's real pre-commit index and hook environment
    command = [
        "git",
        "-c",
        "commit.gpgSign=false",
        "-C",
        str(repository.root),
        "commit",
        "--quiet",
        "-m",
        "bump version",
    ]
    if commit_only:
        command.extend(("--only", "pyproject.toml"))
    completed = subprocess.run(
        command,
        env=isolated_git_environment(
            {"PATH": os.pathsep.join((str(fake_bin), os.environ["PATH"]))}
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    if commit_only:
        assert completed.returncode != 0
        assert "partial-path commits cannot synchronize a version change" in completed.stderr
        assert b'version = "0.1.0"' in repository.git(
            "show", "HEAD:pyproject.toml"
        ).stdout
        assert b'version = "1.2.3"' in repository.git(
            "show", ":pyproject.toml"
        ).stdout
    else:
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert b'version = "1.2.3"' in repository.git(
            "show", "HEAD:pyproject.toml"
        ).stdout
    for directory in (".claude-plugin", ".codex-plugin"):
        committed = json.loads(
            repository.git("show", f"HEAD:{directory}/plugin.json").stdout
        )
        staged = json.loads(
            repository.git("show", f":{directory}/plugin.json").stdout
        )
        expected = "0.1.0" if commit_only else "1.2.3"
        assert committed["version"] == expected
        assert staged["version"] == expected
        assert json.loads(
            (repository.root / directory / "plugin.json").read_text(encoding="utf-8")
        )["version"] == expected


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
            'repo_checks.py" --suite validators --repository-view staged --validator '
            f"{validator_id}"
        ) in hook
        assert "validators/runner.py" not in hook
