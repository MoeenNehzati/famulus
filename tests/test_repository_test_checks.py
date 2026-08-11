from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
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
    "tests/test_dispatcher_direct_authorization.py::test_direct_python_process_target_keeps_gateway_and_entry_separate",
    "tests/test_officina_git_provenance.py::test_git_test_repository_preserves_exact_bytes_under_ambient_autocrlf",
    "skills/recurring-tasks/_rtx/tests/test_schedule_backend.py::test_linux_sync_writes_units_and_enables_timer",
    "tests/test_officina_blueprint_graph.py::test_content_ownership_accepts_equivalent_repository_alias",
    "tests/test_repository_validator_checks.py::test_run_all_isolates_unmerged_index_and_restores_git_environment",
)


def test_named_suites_resolve_to_ordered_native_phases() -> None:
    """Catch reintroduction of a task graph or split runner routes."""
    assert runner.SUITE_PHASES == {
        "validators": ("validators",),
        "tests": ("tests:shared", "tests:performance"),
        "precommit": ("validators", "tests:shared"),
        "pre-push": ("validators", "tests:shared"),
        "portability": ("tests:shared",),
        "full": ("validators", "tests:shared", "tests:performance"),
    }


def test_combined_suites_share_one_pooled_run() -> None:
    """Catch reconstruction of a validator-first execution barrier."""
    assert runner._suite_runs("precommit", task_id=None) == ("combined",)
    assert runner._suite_runs("pre-push", task_id=None) == ("combined",)
    assert runner._suite_runs("full", task_id=None) == (
        "combined",
        "tests:performance",
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
            "officina.repository_checks",
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


@pytest.mark.parametrize("suite", ["full", "pre-push"])
def test_browser_suites_use_loadgroup(suite: str) -> None:
    args = runner._suite_pytest_args(suite, verbose=False, jobs=6)
    assert args[args.index("--dist") + 1] == "loadgroup"


@pytest.mark.parametrize("suite", ["precommit", "portability"])
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


def test_precommit_defers_installation_chrome_docstring_and_performance_tests() -> None:
    deselected = _deselected_tests(
        runner._suite_pytest_args("precommit", verbose=False)
    )

    assert runner.INSTALLATION_TESTS <= deselected
    assert runner.CHROME_TESTS <= deselected
    assert runner.DOCSTRING_TESTS <= deselected
    assert runner.PERFORMANCE_TESTS <= deselected
    assert (
        "tests/test_nested_module_migration.py::"
        "TestNestedModuleMigrationContract::"
        "test_repository_inventory_matches_reviewed_v6_cutover_surface"
        in deselected
    )


def test_prepush_restores_installation_and_chrome_but_defers_slow_tests() -> None:
    deselected = _deselected_tests(
        runner._suite_pytest_args("pre-push", verbose=False)
    )

    assert deselected == runner.DOCSTRING_TESTS | runner.PERFORMANCE_TESTS


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
    assert command[command.index("--dist") + 1] == "loadgroup"
    assert not any(argument in {"tests", "hooks/tests", "skills"} for argument in command)
    assert _deselected_tests(command) == runner.PERFORMANCE_TESTS


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


def test_full_does_not_fail_fast_before_performance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands = []
    statuses = iter((1, 0))
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
    assert len(commands) == 2
    assert "--officina-run-validators" in commands[0]
    assert "--officina-run-validators" not in commands[1]


def test_precommit_native_discovery_ignores_install_test_roots(
    tmp_path: Path,
) -> None:
    command = runner._pytest_phase_command(
        "precommit",
        "tests:shared",
        verbose=False,
        jobs=1,
        cache_dir=tmp_path / "cache",
        timing_path=None,
    )

    assert {
        argument.partition("=")[2]
        for argument in command
        if argument.startswith("--ignore=")
    } == runner.PRECOMMIT_EXCLUDED_TEST_DIRS


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


def test_phase_runner_continues_to_performance_after_pooled_failure(
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
    assert launched == ["tests:shared", "tests:performance"]


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
        if argument.endswith("/staged-paths.json")
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
    assert len(set(cache_options)) == 2


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
            'repo_checks.py" --suite validators --repository-view staged --validator '
            f"{validator_id}"
        ) in hook
        assert "validators/runner.py" not in hook
