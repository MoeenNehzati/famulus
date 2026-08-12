"""Central implementation for repository tests and conformance validators."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import ModuleType
from typing import Callable, Sequence
import xml.etree.ElementTree as ElementTree

import pytest

from officina import _validator_snapshot
from officina.common.python_source_cache import PythonSourceCache


REPO_ROOT = Path(__file__).resolve().parents[2]

PRECOMMIT_EXCLUDED_TEST_DIRS = {
    "skills/install-assistant-tools/_rtx/tests",
    "skills/install-assistant-tools/tests",
}
INSTALLATION_TESTS = {
    "tests/test_install_lifecycle.py",
    "tests/test_officina_famulus_paths.py",
    "tests/test_officina_install_info.py",
    "tests/test_officina_launcher_entry.py",
    "tests/test_officina_managed_runtime.py",
    "tests/test_officina_runtime_pointer.py",
    "tests/test_officina_uv_bootstrap.py",
}
CHROME_TESTS = {
    "tests/test_visualization_browser.py",
    "tests/test_visualization_containment_edges_browser.py",
    "tests/test_visualization_inspector_and_bezier_browser.py",
    "tests/test_visualization_projection_arrangements_browser.py",
    "tests/test_visualization_projection_browser.py",
}
DOCSTRING_TESTS = {
    "tests/test_docstring_schema_dynamic_sections.py",
    "tests/test_docstrings_validator.py",
}
PERFORMANCE_TESTS = {"tests/test_dispatcher_performance.py"}
PRECOMMIT_EXCLUDED_TESTS = {
    "tests/test_nested_module_migration.py::"
    "TestNestedModuleMigrationContract::"
    "test_repository_inventory_matches_reviewed_v6_cutover_surface",
    *INSTALLATION_TESTS,
    *CHROME_TESTS,
    *DOCSTRING_TESTS,
    *PERFORMANCE_TESTS,
}
PREPUSH_EXCLUDED_TESTS = CHROME_TESTS | DOCSTRING_TESTS | PERFORMANCE_TESTS
SUITE_EXCLUDED_VALIDATORS = {
    "precommit": {"repo/docstrings"},
    "pre-push": {"repo/docstrings"},
}
PORTABILITY_TESTS = (
    "tests/test_officina_atomic_files.py::test_secure_append_creates_then_appends_complete_framed_records",
    "tests/test_officina_atomic_files.py::test_windows_native_secure_create_replace_append_and_acl",
    "tests/test_dispatcher_direct_authorization.py::test_direct_python_process_target_keeps_gateway_and_entry_separate",
    "tests/test_officina_git_provenance.py::test_git_test_repository_preserves_exact_bytes_under_ambient_autocrlf",
    "skills/recurring-tasks/_rtx/tests/test_schedule_backend.py::test_linux_sync_writes_units_and_enables_timer",
    "tests/test_officina_blueprint_graph.py::test_content_ownership_accepts_equivalent_repository_alias",
    "tests/test_repository_validator_checks.py::test_run_all_isolates_unmerged_index_and_restores_git_environment",
)


@dataclass(frozen=True)
class _GraphState:
    """Store one shared blueprint preflight result.

    Intent
    ------
    Keep the owner, findings, and optional graph from one validator session together.

    Rationale
    ---------
    Graph consumers must reuse one topology decision without losing owner diagnostics.

    Pseudocode
    ----------
    - set graph_state = owner_id errors and graph
    - return graph_state

    Wraps
    -----
    - none
    """

    owner_id: str
    errors: tuple[str, ...]
    graph: object | None


@dataclass(frozen=True)
class _PhaseResult:
    """Record one completed repository-check phase for timing output.

    Intent
    ------
    Carry phase identity, status, wall time, and optional JUnit evidence together.

    Rationale
    ---------
    Partial timing reports must preserve every completed phase even when a
    later phase fails or is interrupted.

    Pseudocode
    ----------
    - set phase_result = task identity, exit status, wall time, and timing path
    - return phase_result

    Wraps
    -----
    - none
    """

    task_id: str
    exit_code: int
    wall_seconds: float
    timing_path: Path | None


class _ValidatorModule(pytest.Module):
    """Collect one validator entry point as a normal pytest function.

    Intent
    ------
    Adapt a discovered validator module to pytest's standard function collector.

    Rationale
    ---------
    Normal function items provide fixture injection, timing, selection, and reporting.

    Pseudocode
    ----------
    - set validator_module = module from plugin registry
    - set validator_entry = protocol-specific callable
    - return ordinary pytest function item with canonical validator id

    Wraps
    -----
    - none
    """

    validator_id: str
    validator_plugin: "ValidatorPytestPlugin"

    def __init__(
        self,
        *args: object,
        validator_id: str,
        validator_plugin: "ValidatorPytestPlugin",
        **kwargs: object,
    ) -> None:
        """Attach canonical validator metadata to one pytest module collector.

        Intent
        ------
        Retain the validator identity and session plugin during collection.

        Rationale
        ---------
        Pytest node construction otherwise discards repository-specific metadata.

        Pseudocode
        ----------
        - set pytest_module = initialized standard pytest module
        - set validator_metadata = validator id and validator plugin

        Wraps
        -----
        - none
        """
        super().__init__(*args, **kwargs)
        self.validator_id = validator_id
        self.validator_plugin = validator_plugin

    def _getobj(self) -> ModuleType:
        """Return the already loaded staged validator module.

        Intent
        ------
        Bind pytest collection to code imported from the staged mirror.

        Rationale
        ---------
        Reloading through normal imports could select working-tree or user-site code.

        Pseudocode
        ----------
        - set module = plugin module for canonical validator id
        - return module

        Wraps
        -----
        - none
        """
        return self.validator_plugin.modules[self.validator_id]

    def collect(self):
        """Return marked normal pytest functions for this validator.

        Intent
        ------
        Make the selected validator protocol visible to standard pytest execution.

        Rationale
        ---------
        Native module collection enables validator-local fixtures and parametrization.
        Validators without test functions retain their established singleton item.

        Pseudocode
        ----------
        - if validator module has no test functions:
          - return singleton legacy validator item
        - set pytest_functions = normally collected module test functions
        - set pytest_function_metadata = validator marker and canonical id
        - return pytest function items

        Wraps
        -----
        - none
        """
        test_names = tuple(
            name
            for name, value in vars(self.obj).items()
            if name.startswith("test_") and callable(value)
        )
        if not test_names:
            return [self._legacy_item()]

        items = [
            item
            for item in super().collect()
            if isinstance(item, pytest.Function)
        ]
        for item in items:
            item.add_marker("validator")
            item._validator_id = self.validator_id  # type: ignore[attr-defined]
            item._validator_entry_name = (  # type: ignore[attr-defined]
                item.originalname or item.name
            )
        return items

    def _legacy_item(self) -> pytest.Function:
        """Build the established single entry-point validator item.

        Intent
        ------
        Preserve collection for validator modules that do not define pytest tests.

        Rationale
        ---------
        Fixture-backed validators are opt-in and must not migrate existing modules.

        Pseudocode
        ----------
        - set validator_entry = configured protocol entry point
        - set pytest_function = ordinary function item for validator entry
        - set pytest_function_metadata = validator marker and canonical id
        - return pytest function

        Wraps
        -----
        - none
        """
        entry_name, callobj = self.validator_plugin.entry_points[self.validator_id]
        item = pytest.Function.from_parent(
            self,
            name=self.validator_id,
            callobj=callobj,
        )
        item.add_marker("validator")
        item._validator_id = self.validator_id  # type: ignore[attr-defined]
        item._validator_entry_name = entry_name  # type: ignore[attr-defined]
        return item


class ValidatorPytestPlugin:
    """Expose staged repository validators through pytest.

    Intent
    ------
    Supply collection, fixtures, result translation, and shared graph preparation.

    Rationale
    ---------
    Validators are conformance tests whose special behavior belongs at collection
    and result boundaries rather than in a separate execution framework.

    Pseudocode
    ----------
    - set validator_modules = selected staged validator modules
    - set validator_entries = protocol-specific function entry points
    - set pytest_items = normal pytest functions for validator_entries
    - set validator_arguments = staged repository fixture values
    - set validator_results = finding lists translated to pytest outcomes

    Wraps
    -----
    - none
    """

    def __init__(
        self,
        *,
        runner: ModuleType,
        tracked_root: Path,
        display_root: Path,
        selected_paths: Sequence[tuple[str, Path]],
        staged_paths: Sequence[str],
    ) -> None:
        """Prepare one isolated validator pytest session.

        Intent
        ------
        Build immutable session metadata before pytest begins collection.

        Rationale
        ---------
        Discovery, staged paths, and displayed paths must share one captured snapshot.

        Pseudocode
        ----------
        - set repository_state = normalized roots and staged paths
        - set validator_path_ids = selected paths mapped to canonical ids
        - set validator_results = empty result and graph state
        - set validator_entries = loaded validators and selected callables

        Wraps
        -----
        - none
        """
        self.runner = runner
        self.tracked_root = Path(tracked_root).resolve()
        self.display_root = Path(display_root).resolve()
        self.staged_path_values = tuple(staged_paths)
        self.path_ids = {
            path.resolve(): validator_id
            for validator_id, path in selected_paths
        }
        self.modules: dict[str, ModuleType] = {}
        self.entry_points: dict[str, tuple[str, Callable[..., object]]] = {}
        self.results: dict[str, list[str]] = {}
        self.execution_error: str | None = None
        self._graph_state_value: _GraphState | None = None
        self._graph_views: dict[str, tuple[object, object]] = {}
        self._preflight_owner_id: str | None = None
        self._load_selected_validators(selected_paths)

    def _load_selected_validators(
        self,
        selected_paths: Sequence[tuple[str, Path]],
    ) -> None:
        """Load selected validators and assign their pytest callables.

        Intent
        ------
        Preserve ordinary, staged-aware, and shared-graph validator protocols.

        Rationale
        ---------
        Choosing the generic fallback would silently change staged or graph semantics.

        Pseudocode
        ----------
        - for selected_validator in selected_validators:
          - set module = loaded staged validator module
          - if staged and graph protocols overlap:
            - raise validator protocol error
          - set entry = staged or ordinary callable
        - set graph_owner = preflight owner when graph consumers exist
        - set graph_entries = graph consumers mapped to validate_with_graph

        Wraps
        -----
        - none
        """
        for validator_id, path in selected_paths:
            module, validate = self.runner._load_validator(validator_id, path)
            self.modules[validator_id] = module
            validate_staged = getattr(module, "validate_staged", None)
            graph_hooks = [
                name
                for name in ("preflight", "validate_with_graph")
                if callable(getattr(module, name, None))
            ]
            if getattr(module, "REQUIRES_BLUEPRINT_GRAPH", False) is True:
                graph_hooks.insert(0, "REQUIRES_BLUEPRINT_GRAPH")
            if callable(validate_staged) and graph_hooks:
                raise self.runner.ValidatorRunnerError(
                    f"{validator_id}: validate_staged cannot be combined with "
                    + ", ".join(graph_hooks)
                )
            if callable(validate_staged):
                self.entry_points[validator_id] = (
                    "validate_staged",
                    validate_staged,
                )
            else:
                self.entry_points[validator_id] = ("validate", validate)

        graph_consumers = {
            validator_id
            for validator_id, module in self.modules.items()
            if getattr(module, "REQUIRES_BLUEPRINT_GRAPH", False) is True
        }
        if graph_consumers:
            available = self.runner._validator_paths(self.tracked_root)
            owner_id = "skill-maker/blueprints"
            if owner_id not in available:
                required = [
                    validator_id
                    for validator_id in graph_consumers
                    if not getattr(
                        self.modules[validator_id],
                        "BLUEPRINT_GRAPH_OPTIONAL",
                        False,
                    )
                ]
                if required:
                    raise self.runner.ValidatorRunnerError(
                        "graph validators require exactly one blueprint preflight owner"
                    )
                return
            self._preflight_owner_id = owner_id
            if owner_id not in self.modules:
                owner_module, _validate = self.runner._load_validator(
                    owner_id,
                    available[owner_id],
                )
                self.modules[owner_id] = owner_module
            for validator_id in graph_consumers:
                validate_with_graph = getattr(
                    self.modules[validator_id],
                    "validate_with_graph",
                    None,
                )
                if not callable(validate_with_graph):
                    raise self.runner.ValidatorRunnerError(
                        f"{validator_id}: graph validator has no callable "
                        "validate_with_graph"
                    )
                self.entry_points[validator_id] = (
                    "validate_with_graph",
                    validate_with_graph,
                )

        owner_id = self._preflight_owner_id
        if owner_id is not None and owner_id in self.entry_points:
            validate_with_graph = getattr(
                self.modules[owner_id],
                "validate_with_graph",
                None,
            )
            if callable(validate_with_graph):
                self.entry_points[owner_id] = (
                    "validate_with_graph",
                    validate_with_graph,
                )

    def _normalized_errors(self, errors: Sequence[str]) -> list[str]:
        """Replace temporary mirror prefixes with caller-visible paths.

        Intent
        ------
        Keep validator diagnostics stable outside the temporary staged mirror.

        Rationale
        ---------
        Users need findings that point to their source repository paths.

        Pseudocode
        ----------
        - set normalized_errors = errors with tracked prefix replaced by display prefix
        - return normalized errors

        Wraps
        -----
        - none
        """
        mirror_prefix = str(self.tracked_root)
        display_prefix = str(self.display_root)
        return [
            error.replace(mirror_prefix, display_prefix)
            for error in errors
        ]

    def _graph_state(self) -> _GraphState | None:
        """Prepare and retain the repository blueprint graph once.

        Intent
        ------
        Execute the canonical blueprint preflight at most once per pytest session.

        Rationale
        ---------
        Graph consumers need one consistent topology and owner-scoped diagnostics.

        Pseudocode
        ----------
        - return no state when no graph consumer exists
        - return cached state when already prepared
        - set preflight_result = owner preflight under detected schema version
        - set preflight_errors = validated and normalized preflight findings
        - set graph_state = owner findings and prepared graph
        - return graph state

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        ._GraphState:
          why:
            constructs: "Builds the retained owner findings and shared graph state."
        """
        if self._preflight_owner_id is None:
            return None
        if self._graph_state_value is not None:
            return self._graph_state_value
        owner_id = self._preflight_owner_id
        owner = self.modules[owner_id]
        preflight = getattr(owner, "preflight", None)
        if not callable(preflight):
            raise self.runner.ValidatorRunnerError(
                f"{owner_id}: blueprint preflight is unavailable"
            )
        schema_version = getattr(owner, "repository_schema_version", None)
        try:
            if callable(schema_version):
                value = preflight(
                    self.tracked_root,
                    expected_schema_version=schema_version(self.tracked_root),
                )
            else:
                value = preflight(self.tracked_root)
        except BaseException as exc:
            raise self.runner.ValidatorRunnerError(
                f"{owner_id}: validator execution failed: {exc}"
            ) from exc
        if not isinstance(value, tuple) or len(value) != 2:
            raise self.runner.ValidatorRunnerError(
                f"{owner_id}: preflight must return tuple[list[str], graph | None]"
            )
        errors = self.runner._validated_errors(owner_id, "preflight", value[0])
        normalized = tuple(self._normalized_errors(errors))
        self._graph_state_value = _GraphState(owner_id, normalized, value[1])
        if normalized:
            self.results[owner_id] = list(normalized)
        return self._graph_state_value

    @pytest.fixture(scope="session")
    def repo_root(self) -> Path:
        """Return the exact staged repository root.

        Intent
        ------
        Inject the materialized Git-index view into validator functions.

        Rationale
        ---------
        Validators must not read unstaged working-tree bytes. The immutable session
        path may be shared by broader-scoped validator preparation fixtures.

        Pseudocode
        ----------
        - return tracked_root

        Wraps
        -----
        - none
        """

        return self.tracked_root

    @pytest.fixture(scope="session")
    def staged_paths(self) -> tuple[str, ...]:
        """Return changed regular paths from the captured index.

        Intent
        ------
        Inject one validated staged-path tuple into staged-aware validators.

        Rationale
        ---------
        Recomputing paths could mix a later live-index state into the session.

        Pseudocode
        ----------
        - return staged_path_values

        Wraps
        -----
        - none
        """

        return self.staged_path_values

    @pytest.fixture(scope="session")
    def python_source_cache(self) -> PythonSourceCache:
        """Return lazy Python preparation shared within this staged session.

        Intent
        ------
        Inject one source-and-AST cache into fixture-backed validators.

        Rationale
        ---------
        Independent validators frequently read and parse the same immutable files.

        Pseudocode
        ----------
        - return Python source cache owned by tracked root

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .common.python_source_cache.PythonSourceCache:
          why:
            constructs: "Builds the staged session's lazy source and AST cache."
        """

        return PythonSourceCache(self.tracked_root)

    @pytest.fixture
    def graph(self, request: pytest.FixtureRequest) -> object | None:
        """Return an isolated view of the session blueprint graph.

        Intent
        ------
        Give each graph validator a defensive copy of one prepared topology.

        Rationale
        ---------
        Per-item copies preserve sharing while detecting cross-validator mutation.

        Pseudocode
        ----------
        - set graph_state = shared graph preflight state
        - return none when graph is unavailable
        - set graph_view = copied graph with pristine comparison
        - return per-item graph view

        Wraps
        -----
        - none
        """

        state = self._graph_state()
        if state is None or state.graph is None:
            return None
        view = copy.deepcopy(state.graph)
        self._graph_views[request.node.nodeid] = (
            view,
            copy.deepcopy(view),
        )
        return view

    def pytest_collect_file(self, file_path: Path, parent: pytest.Collector):
        """Collect selected validator paths and ignore all other files.

        Intent
        ------
        Extend pytest file collection only for the runner-selected validator catalog.

        Rationale
        ---------
        Exact path matching prevents helpers and untracked validators from executing.

        Pseudocode
        ----------
        - set validator_id = canonical id for file path
        - return none when file is not selected
        - return validator module collector

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        ._ValidatorModule.from_parent:
          why:
            constructs: "Builds the pytest module collector for one selected validator file."
        """
        validator_id = self.path_ids.get(Path(file_path).resolve())
        if validator_id is None:
            return None
        return _ValidatorModule.from_parent(
            parent,
            path=file_path,
            validator_id=validator_id,
            validator_plugin=self,
        )

    def pytest_collection_modifyitems(
        self,
        items: list[pytest.Item],
    ) -> None:
        """Remove only duplicate default items for selected validator files.

        Intent
        ------
        Preserve ordinary test items while removing duplicate default collection
        for explicitly selected validator files.

        Rationale
        ---------
        Pytest treats explicit file arguments as collection roots even when their
        names do not match test patterns. Without filtering, fixture-backed
        validator modules execute once through each collector.

        Pseudocode
        ----------
        - set retained_items = custom validator items or items outside selected paths
        - set items = retained items

        Wraps
        -----
        - none
        """
        selected_paths = set(self.path_ids)
        items[:] = [
            item
            for item in items
            if isinstance(getattr(item, "_validator_id", None), str)
            or Path(item.path).resolve() not in selected_paths
        ]

    def pytest_pyfunc_call(self, pyfuncitem: pytest.Function):
        """Execute validator functions using pytest-resolved fixtures.

        Intent
        ------
        Translate the established finding-list protocol into pytest outcomes.

        Rationale
        ---------
        Normal pytest functions ignore non-None returns, while validators return
        an empty list for success and string findings for conformance failures.

        Pseudocode
        ----------
        - if pytest function is not a validator:
          - return unhandled status
        - set graph_state = applicable graph preflight state
        - set validator_result = validator call with resolved fixtures
        - set validated_errors = validated finding-list result
        - set final_errors = normalized findings plus graph mutation finding
        - if final_errors is nonempty:
          - raise pytest failure with final_errors
        - return handled status

        Wraps
        -----
        - none
        """
        validator_id = getattr(pyfuncitem, "_validator_id", None)
        if not isinstance(validator_id, str):
            return None
        entry_name = getattr(pyfuncitem, "_validator_entry_name", "validate")
        fixture_names = pyfuncitem._fixtureinfo.argnames
        module = self.modules[validator_id]
        uses_graph = entry_name == "validate_with_graph" or getattr(
            module,
            "REQUIRES_BLUEPRINT_GRAPH",
            False,
        ) is True
        state = self._graph_state() if uses_graph else None
        if state is not None and state.errors:
            if (
                validator_id == state.owner_id
                or state.owner_id not in self.entry_points
            ):
                pytest.fail("\n".join(state.errors), pytrace=False)
            pytest.skip("blueprint preflight failed")
        if state is not None and state.graph is None:
            pytest.skip("blueprint preflight produced no graph")
        arguments = {
            name: pyfuncitem.funcargs[name]
            for name in fixture_names
        }
        try:
            returned = pyfuncitem.obj(**arguments)
            errors = self.runner._validated_errors(
                validator_id,
                entry_name,
                returned,
            )
        except self.runner.ValidatorRunnerError as exc:
            self.execution_error = str(exc)
            pytest.fail(str(exc), pytrace=False)
        except BaseException as exc:
            self.execution_error = (
                f"{validator_id}: validator execution failed: {exc}"
            )
            pytest.fail(self.execution_error, pytrace=False)
        graph_pair = self._graph_views.get(pyfuncitem.nodeid)
        if graph_pair is not None and graph_pair[0] != graph_pair[1]:
            errors.append(
                f"{validator_id}: validator mutated its blueprint graph view"
            )
        errors = self._normalized_errors(errors)
        if errors:
            self.results.setdefault(validator_id, []).extend(errors)
            pytest.fail("\n".join(errors), pytrace=False)
        return True


class _WorkerAssignmentMetricsPlugin:
    """Record how long each pytest worker has a test protocol assigned.

    Intent
    ------
    Separate xdist scheduling occupancy from aggregate CPU consumed by pytest
    workers and their descendant subprocesses.

    Rationale
    ---------
    CPU-seconds divided by wall-seconds cannot show whether workers are waiting
    for work. Pytest reports expose the setup, call, and teardown wall time for
    the worker that executed each item, which provides the required assignment
    measurement without modifying tests or the xdist scheduler.

    Pseudocode
    ----------
    - set session_start = controller monotonic time
    - for report in worker_reports:
      - set assigned_seconds = prior assigned time plus report duration
      - set item_count = prior count plus terminal item indicator
    - set unassigned_seconds = session duration minus assigned time per worker
    - set output_artifact = atomically serialized sorted worker records

    Wraps
    -----
    - none
    """

    def __init__(self, output_path: Path) -> None:
        """Initialize one controller-side metrics accumulator.

        Intent
        ------
        Bind a report destination and empty per-worker aggregates.

        Rationale
        ---------
        Workers emit reports to the controller, so no cross-process shared state
        is required.

        Pseudocode
        ----------
        - set output_path = resolved requested destination
        - set session_start = zero
        - set workers = empty aggregate mapping

        Wraps
        -----
        - none
        """
        self.output_path = Path(output_path).resolve()
        self.started_at = 0.0
        self.workers: dict[str, dict[str, float | int]] = {}

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        """Start the controller-side assignment clock.

        Intent
        ------
        Establish the wall-time denominator shared by worker occupancy records.

        Rationale
        ---------
        Worker readiness and reports are meaningful only relative to the same
        pytest controller session.

        Pseudocode
        ----------
        - set session_start = current monotonic time

        Wraps
        -----
        - none
        """

        del session
        self.started_at = time.monotonic()

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodeready(self, node: object) -> None:
        """Retain workers that receive no completed items.

        Intent
        ------
        Include every ready xdist worker in the final assignment report.

        Rationale
        ---------
        Omitting zero-item workers would hide scheduler underutilization.

        Pseudocode
        ----------
        - set worker_id = ready node gateway identity
        - if worker_id is valid:
          - set worker_aggregate = existing or empty aggregate

        Wraps
        -----
        - none
        """

        gateway = getattr(node, "gateway", None)
        worker_id = getattr(gateway, "id", None)
        if isinstance(worker_id, str):
            self._worker(worker_id)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Add one setup, call, or teardown report to its executing worker.

        Intent
        ------
        Accumulate worker assignment duration and count each terminal test item.

        Rationale
        ---------
        Pytest report duration includes fixture phases while call or failed setup
        identifies one completed item without double counting teardown.

        Pseudocode
        ----------
        - set worker_id = report worker identity or main
        - set assigned_seconds = prior time plus report duration
        - if report is call or terminal setup:
          - set item_count = prior count plus one

        Wraps
        -----
        - none
        """

        worker_id = getattr(report, "worker_id", None)
        if not isinstance(worker_id, str):
            node = getattr(report, "node", None)
            worker_id = getattr(getattr(node, "gateway", None), "id", "main")
        worker = self._worker(worker_id)
        worker["assigned_seconds"] = float(worker["assigned_seconds"]) + float(
            report.duration
        )
        terminal_setup = report.when == "setup" and (
            bool(getattr(report, "skipped", False))
            or bool(getattr(report, "failed", False))
        )
        if report.when == "call" or terminal_setup:
            worker["item_count"] = int(worker["item_count"]) + 1

    def pytest_sessionfinish(
        self,
        session: pytest.Session,
        exitstatus: int,
    ) -> None:
        """Atomically persist assignment and idle time for every known worker.

        Intent
        ------
        Produce one complete controller-owned worker assignment artifact.

        Rationale
        ---------
        Atomic replacement prevents the benchmark harness from reading a partial
        JSON report after pytest exits.

        Pseudocode
        ----------
        - set session_seconds = elapsed controller session time
        - for worker_id in sorted workers:
          - set worker_fields = assigned and unassigned durations
          - set records = records plus worker record
        - set temporary_artifact = serialized schema payload
        - set output_artifact = atomic replacement with temporary artifact

        Wraps
        -----
        - none
        """

        del session, exitstatus
        session_seconds = max(0.0, time.monotonic() - self.started_at)
        records: list[dict[str, float | int | str]] = []
        for worker_id in sorted(self.workers):
            worker = self.workers[worker_id]
            assigned = float(worker["assigned_seconds"])
            unassigned = max(0.0, session_seconds - assigned)
            records.append(
                {
                    "worker_id": worker_id,
                    "item_count": int(worker["item_count"]),
                    "assigned_seconds": assigned,
                    "unassigned_seconds": unassigned,
                    "assigned_fraction": (
                        assigned / session_seconds if session_seconds else 0.0
                    ),
                }
            )
        payload = {
            "schema_version": 1,
            "session_seconds": session_seconds,
            "workers": records,
        }
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output_path.with_name(
            f".{self.output_path.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.output_path)

    def _worker(self, worker_id: str) -> dict[str, float | int]:
        """Return the mutable aggregate for one controller-observed worker.

        Intent
        ------
        Centralize zero-value initialization for ready nodes and test reports.

        Rationale
        ---------
        Both hooks must address the same aggregate without duplicating defaults.

        Pseudocode
        ----------
        - set worker = existing aggregate or zero-value aggregate
        - return worker

        Wraps
        -----
        - none
        """

        return self.workers.setdefault(
            worker_id,
            {"item_count": 0, "assigned_seconds": 0.0},
        )


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register private options for adding validators to normal collection.

    Intent
    ------
    Expose the staged-root and validator-selection inputs consumed by the plugin.

    Rationale
    ---------
    Pytest must parse these options before repository validators can join its
    ordinary collection session.

    Pseudocode
    ----------
    - set option_group = repository validator parser group
    - set option_group = option group plus validator collection controls

    Wraps
    -----
    - none
    """
    group = parser.getgroup("officina repository validators")
    group.addoption(
        "--officina-run-validators",
        action="store_true",
        help="collect repository validators beside ordinary pytest tests",
    )
    group.addoption("--officina-validator-root", type=Path)
    group.addoption("--officina-validator-display-root", type=Path)
    group.addoption("--officina-staged-paths-file", type=Path)
    group.addoption("--officina-validator", action="append", default=[])
    group.addoption("--officina-exclude-validator", action="append", default=[])


def pytest_configure(config: pytest.Config) -> None:
    """Register worker metrics and optional validator collection plugins.

    Intent
    ------
    Add controller-only assignment reporting and place selected validators in
    the same pytest session as ordinary items.

    Rationale
    ---------
    Metrics must not be registered inside xdist workers, while validator
    collection requires one validated repository view and staged-path snapshot.

    Pseudocode
    ----------
    - if controller worker metrics path is configured:
      - set plugin_registry = registry plus worker metrics plugin
    - if validator collection is disabled:
      - return
    - set validator_inputs = roots, selection, and staged paths from pytest options
    - if required validator inputs are absent:
      - raise pytest usage error
    - set plugin_registry = registry plus validator plugin

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._WorkerAssignmentMetricsPlugin:
      why:
        orchestrates: "Captures controller-side worker assignment evidence."

    InstantiationsFromRepo
    ----------------------
    .ValidatorPytestPlugin:
      why:
        constructs: "Supplies selected validator items to the shared session."
    officina._validator_snapshot._selected_validator_paths:
      why:
        transforms: "Resolves canonical validator IDs against the selected view."
    officina._validator_snapshot._load_staged_paths:
      why:
        constructs: "Restores the immutable staged-path list for validators."
    """
    worker_metrics_path = os.environ.get("OFFICINA_PYTEST_WORKER_METRICS")
    if worker_metrics_path and not hasattr(config, "workerinput"):
        config.pluginmanager.register(
            _WorkerAssignmentMetricsPlugin(Path(worker_metrics_path)),
            "officina-worker-assignment-metrics",
        )
    if not config.getoption("--officina-run-validators"):
        return
    tracked_root = config.getoption("--officina-validator-root")
    display_root = config.getoption("--officina-validator-display-root")
    staged_paths_file = config.getoption("--officina-staged-paths-file")
    if tracked_root is None or display_root is None or staged_paths_file is None:
        raise pytest.UsageError(
            "--officina-run-validators requires validator root, display root, "
            "and staged paths file"
        )
    tracked_root = tracked_root.resolve()
    selected_paths = _validator_snapshot._selected_validator_paths(
        tracked_root,
        config.getoption("--officina-validator") or None,
        config.getoption("--officina-exclude-validator"),
    )
    staged_paths = _validator_snapshot._load_staged_paths(
        tracked_root,
        staged_paths_file,
    )
    plugin = ValidatorPytestPlugin(
        runner=_validator_snapshot,
        tracked_root=tracked_root,
        display_root=display_root.resolve(),
        selected_paths=selected_paths,
        staged_paths=staged_paths,
    )
    config.pluginmanager.register(plugin, "officina-validator-items")


def run_validators_with_pytest(
    *,
    runner: ModuleType,
    tracked_root: Path,
    display_root: Path,
    validator_ids: Sequence[str] | None,
    excluded_validator_ids: Sequence[str] | None,
    staged_paths: Sequence[str],
    timing_output: Path | None = None,
) -> dict[str, list[str]]:
    """Collect and execute staged validators through pytest.

    Intent
    ------
    Replace the custom child execution loop while retaining its result mapping.

    Rationale
    ---------
    Pytest should own fixture resolution, item execution, timing, and reporting.

    Pseudocode
    ----------
    - set import_paths = staged mirror and staged source root
    - set selected_paths = selected validator source paths
    - set validator_plugin = pytest plugin for selected_paths
    - set pytest_status = pytest run over selected_paths
    - if pytest_status indicates infrastructure failure:
      - raise validator runner error
    - return canonical findings

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .ValidatorPytestPlugin:
      why:
        constructs: "Builds the staged validator collection, fixtures, and result adapter."
    """

    mirror_paths = [str(tracked_root), str(tracked_root / "src")]
    runner.sys.path[:] = mirror_paths + [
        entry
        for entry in runner.sys.path
        if entry
        and not Path(entry).is_relative_to(runner.REPO_ROOT)
        and entry not in mirror_paths
    ]
    selected_paths = runner._selected_validator_paths(
        tracked_root,
        validator_ids,
        excluded_validator_ids,
    )
    if not selected_paths:
        return {}
    plugin = ValidatorPytestPlugin(
        runner=runner,
        tracked_root=tracked_root,
        display_root=display_root,
        selected_paths=selected_paths,
        staged_paths=staged_paths,
    )
    pytest_arguments = [
        "-q",
        "--disable-warnings",
        "--rootdir",
        str(tracked_root),
        "--confcutdir",
        str(tracked_root),
    ]
    if timing_output is not None:
        pytest_arguments.append(f"--junitxml={timing_output}")
    pytest_arguments.extend(
        str(path) for _validator_id, path in selected_paths
    )
    exit_code = pytest.main(
        pytest_arguments,
        plugins=[plugin],
    )
    if plugin.execution_error is not None:
        raise runner.ValidatorRunnerError(plugin.execution_error)
    if int(exit_code) not in {0, 1}:
        raise runner.ValidatorRunnerError(
            f"pytest validator execution failed with status {int(exit_code)}"
        )
    if int(exit_code) == 1 and not plugin.results:
        raise runner.ValidatorRunnerError(
            "pytest validator execution failed without validator findings"
        )
    return dict(sorted(plugin.results.items()))


SUITE_PHASES = {
    "validators": ("validators",),
    "tests": ("tests:shared", "tests:performance"),
    "precommit": ("validators", "tests:shared"),
    "pre-push": ("validators", "tests:shared", "tests:browser"),
    "portability": ("tests:shared",),
    "full": ("validators", "tests:shared", "tests:performance"),
}

SUITE_TEST_PROFILES = {
    "tests": "full",
    "precommit": "precommit",
    "pre-push": "pre-push",
    "portability": "portability",
    "full": "full",
}


def _suite_runs(suite: str, task_id: str | None) -> tuple[str, ...]:
    """Resolve one suite to its minimal ordered pytest invocations.

    Intent
    ------
    Combine validator and functional phases whenever both belong to the suite.

    Rationale
    ---------
    One combined invocation gives both item kinds the same xdist queue; only
    load-sensitive performance thresholds retain a separate serial run.

    Pseudocode
    ----------
    - if task_id is present:
      - return task_id as the only run
    - set pooled_phases = validator and shared-test phases in suite
    - if both pooled phases are present:
      - set runs = combined run
    - else:
      - set runs = non-performance suite phases
    - append non-pooled phases such as the serial browser phase
    - if performance phase is present:
      - set runs = runs plus performance phase
    - return ordered runs

    Wraps
    -----
    - none
    """
    if task_id is not None:
        return (task_id,)
    phases = SUITE_PHASES[suite]
    pooled = {"validators", "tests:shared"}.intersection(phases)
    runs: list[str] = ["combined"] if pooled == {"validators", "tests:shared"} else []
    if runs:
        runs.extend(
            phase
            for phase in phases
            if phase not in pooled and phase != "tests:performance"
        )
    else:
        runs.extend(phase for phase in phases if phase != "tests:performance")
    if "tests:performance" in phases:
        runs.append("tests:performance")
    return tuple(runs)


def _resolve_repository_view(suite: str, requested: str) -> str:
    """Choose one import and collection tree for the complete pytest session.

    Intent
    ------
    Apply the staged precommit policy while honoring explicit view selection.

    Rationale
    ---------
    A pytest session must not combine staged validator inputs with working-tree
    test imports.

    Pseudocode
    ----------
    - if requested view is explicit:
      - return requested view
    - if suite is precommit:
      - return staged view
    - return working view

    Wraps
    -----
    - none
    """
    if requested != "auto":
        return requested
    return "staged" if suite == "precommit" else "working"


def _pytest_args(
    *,
    verbose: bool,
    jobs: int = 1,
    distribution: str = "worksteal",
) -> list[str]:
    """Build the common pytest arguments for repository test phases.

    Intent
    ------
    Keep source-path, output-mode, and selected xdist configuration identical
    across suites.

    Rationale
    ---------
    Central arguments prevent hook and CI invocations from drifting while the
    suite policy supplies the distribution mode appropriate to its tests.

    Pseudocode
    ----------
    - set pytest_arguments = source path option and selected verbosity
    - if jobs is greater than one:
      - set pytest_arguments = arguments plus xdist worker and distribution options
    - return pytest_arguments

    Wraps
    -----
    - none
    """
    args = ["-o", "pythonpath=src", "-v" if verbose else "-q"]
    if jobs > 1:
        args.extend(["-n", str(jobs), "--dist", distribution])
    return args


def _default_jobs() -> int:
    """Return the default total pytest-worker lease budget.

    Intent
    ------
    Use two thirds of detected logical CPUs while retaining a usable minimum.

    Rationale
    ---------
    Reserving host capacity limits contention from pytest controllers and unrelated
    processes without requiring machine-specific configuration.

    Pseudocode
    ----------
    - set logical_cpus = detected logical CPUs or one
    - set jobs = two thirds of logical_cpus with minimum one
    - return jobs

    Wraps
    -----
    - none
    """

    logical_cpus = os.cpu_count() or 1
    return max(1, (logical_cpus * 2) // 3)


def _pytest_xdist_available() -> bool:
    """Return whether parallel pytest execution is available.

    Intent
    ------
    Fail before task construction when parallel execution lacks pytest-xdist.

    Rationale
    ---------
    Detecting the missing plugin in a child process would produce a less actionable
    argument failure after scheduling has started.

    Pseudocode
    ----------
    - set xdist_spec = import specification for xdist
    - return whether xdist_spec exists

    Wraps
    -----
    - none
    """

    return importlib.util.find_spec("xdist") is not None


def _suite_pytest_args(
    name: str,
    *,
    verbose: bool,
    jobs: int = 1,
) -> list[str]:
    """Build pytest arguments for one named ordinary-test suite.

    Intent
    ------
    Apply suite-specific xdist distribution and deselections to common pytest
    arguments.

    Rationale
    ---------
    Browser-containing full suites use ``loadgroup`` so their shared browser
    marker forms one work unit. The pre-push browser phase runs separately and
    serially, while its pooled phase uses ``worksteal``. Serial suites emit no
    xdist arguments. Precommit exclusions remain repository policy and belong
    with suite selection.

    Pseudocode
    ----------
    - set distribution = loadgroup for full; otherwise worksteal
    - set pytest_arguments = common arguments for jobs and distribution
    - if name is precommit:
      - set pytest_arguments = arguments plus configured deselections
    - else:
      - if name is pre-push:
        - set pytest_arguments = arguments plus configured deselections
    - return pytest_arguments

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._pytest_args:
      why:
        constructs: "Builds the common pytest argument list extended by this suite."
    """
    if name == "full":
        distribution = "loadgroup"
    else:
        distribution = "worksteal"
    args = _pytest_args(
        verbose=verbose,
        jobs=jobs,
        distribution=distribution,
    )
    if name == "precommit":
        for test_dir in sorted(PRECOMMIT_EXCLUDED_TEST_DIRS):
            args.append(f"--ignore={test_dir}")
        for test in sorted(PRECOMMIT_EXCLUDED_TESTS):
            args.extend(["--deselect", test])
    elif name == "pre-push":
        for test in sorted(PREPUSH_EXCLUDED_TESTS):
            args.extend(["--deselect", test])
    return args


def _terminate_windows_process_tree(
    process: subprocess.Popen[object],
    *,
    force: bool,
) -> None:
    """Ask the native tree terminator to stop a controller and all descendants.

    Intent
    ------
    Match POSIX process-group cleanup for pytest controllers that may own xdist,
    browser, or other descendant processes.

    Rationale
    ---------
    Single-process termination APIs affect only the controller on this platform;
    the native tree operation traverses every process created beneath its PID.

    Pseudocode
    ----------
    - set command = taskkill for process PID with tree traversal enabled
    - if force is enabled:
      - set command = command plus forced termination
    - set taskkill_result = command run with output suppressed
    - return none

    Wraps
    -----
    - none
    """

    command = ["taskkill", "/PID", str(process.pid), "/T"]
    if force:
        command.append("/F")
    subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _terminate_task_process(process: subprocess.Popen[object]) -> None:
    """Terminate one active task process group after coordinator interruption.

    Intent
    ------
    Stop the complete task tree, wait for child cleanup, and escalate only when the
    initial termination does not complete within five seconds.

    Rationale
    ---------
    The coordinator owns temporary logs and cache directories that cannot be cleaned
    safely while pytest, xdist, or browser descendants remain alive.

    Pseudocode
    ----------
    - return when the task already exited
    - if platform is POSIX:
      - set termination = process-group termination signal
    - else:
      - set termination = native process-tree termination
    - set completion = process wait with five-second timeout
    - if termination or wait fails:
      - set forced_termination = process-group or process-tree kill
      - set completion = process wait without timeout
    - return none

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._terminate_windows_process_tree:
      why:
        computes: "Terminates all descendants when POSIX group signals are unavailable."
    """

    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            _terminate_windows_process_tree(process, force=False)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                _terminate_windows_process_tree(process, force=True)
            process.wait()


def _junit_source_path(repo_root: Path, classname: str) -> str:
    """Map a pytest JUnit classname to one repository Python source path.

    Intent
    ------
    Attribute class- and function-level pytest records to their owning test or
    validator file.

    Rationale
    ---------
    JUnit records classnames rather than source paths. Selecting the longest
    existing dotted-prefix file preserves nested test classes without guessing
    which classname suffix represents a Python class.

    Pseudocode
    ----------
    - set components = classname split into dotted components
    - for component_prefix in longest-to-shortest components:
      - if the corresponding repository Python file exists:
        - return its repository-relative path
    - return an explicit unmapped marker

    Wraps
    -----
    - none
    """

    parts = classname.split(".")
    for length in range(len(parts), 0, -1):
        candidate = Path(*parts[:length]).with_suffix(".py")
        if (repo_root / candidate).is_file():
            return candidate.as_posix()
    return f"<unmapped:{classname}>"


def _read_junit_timing(
    path: Path,
    *,
    repo_root: Path,
    task_id: str,
) -> tuple[float, list[dict[str, object]]]:
    """Aggregate one pytest JUnit artifact by repository source file.

    Intent
    ------
    Convert framework-owned testcase timing and outcomes into stable per-file
    records for repository performance comparisons.

    Rationale
    ---------
    Pytest already measures setup, call, and teardown consistently, while the
    repository runner owns task grouping. Parsing its portable JUnit output avoids
    a second timing plugin and remains compatible with xdist controllers.

    Pseudocode
    ----------
    - set document = parsed JUnit artifact
    - set pytest_seconds = sum of suite durations
    - for testcase in document:
      - set source_file = repository source mapped from testcase classname
      - set source_file_timing = prior timing plus testcase duration and outcome
    - return pytest session seconds and sorted file records

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._junit_source_path:
      why:
        constructs: "Builds the repository-relative owner of each JUnit testcase."
    """

    root = ElementTree.parse(path).getroot()
    suites = list(root) if root.tag == "testsuites" else [root]
    pytest_seconds = sum(float(suite.attrib.get("time", "0")) for suite in suites)
    grouped: dict[str, dict[str, object]] = {}
    for testcase in root.iter("testcase"):
        classname = testcase.attrib.get("classname", "")
        source_path = _junit_source_path(repo_root, classname)
        record = grouped.setdefault(
            source_path,
            {
                "task_id": task_id,
                "kind": "validator" if task_id == "validators" else "test",
                "path": source_path,
                "item_count": 0,
                "seconds": 0.0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
            },
        )
        record["item_count"] = int(record["item_count"]) + 1
        record["seconds"] = float(record["seconds"]) + float(
            testcase.attrib.get("time", "0")
        )
        if testcase.find("skipped") is not None:
            record["skipped"] = int(record["skipped"]) + 1
        elif testcase.find("failure") is not None or testcase.find("error") is not None:
            record["failed"] = int(record["failed"]) + 1
        else:
            record["passed"] = int(record["passed"]) + 1
    return pytest_seconds, [grouped[key] for key in sorted(grouped)]


def _run_process(
    command: Sequence[str],
    *,
    cwd: Path,
    task_id: str,
    pycache_prefix: Path,
) -> int:
    """Run one pytest phase with isolated imports, bytecode, and interruption.

    Intent
    ------
    Execute the selected staged or working repository view without writing
    Python bytecode into that view or inheriting an unrelated source path.

    Rationale
    ---------
    Staged mirrors must remain immutable during validation, while ordinary
    imports should still benefit from a writable bytecode cache. A separate
    process group also lets an interrupted root command terminate worker and
    subprocess descendants as one phase.

    Pseudocode
    ----------
    - set execution_root = resolved execution root
    - set resolved_cache = resolved bytecode cache
    - if bytecode cache is inside execution root:
      - raise ValueError
    - set child_environment = isolated source path and external bytecode cache
    - set process = child command in a new process group
    - if process is interrupted:
      - @_terminate_task_process(process)
      - return 130
    - return process exit status

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._terminate_task_process:
      why:
        writes: "Stops the phase process group and all descendants on interruption."
    """
    execution_root = cwd.resolve()
    resolved_pycache_prefix = pycache_prefix.resolve()
    if (
        resolved_pycache_prefix == execution_root
        or resolved_pycache_prefix.is_relative_to(execution_root)
    ):
        raise ValueError("pycache prefix must be outside the execution root")
    resolved_pycache_prefix.mkdir(parents=True, exist_ok=True)
    child_environment = os.environ.copy()
    child_environment.pop("PYTHONDONTWRITEBYTECODE", None)
    child_environment["PYTHONPYCACHEPREFIX"] = str(resolved_pycache_prefix)
    source_root = str(cwd / "src")
    existing_pythonpath = child_environment.get("PYTHONPATH")
    child_environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_root, existing_pythonpath) if part
    )
    popen_kwargs: dict[str, object] = {
        "cwd": cwd,
        "env": child_environment,
    }
    if os.environ.get("OFFICINA_FIXTURE_PROBE_DIR"):
        child_environment["OFFICINA_FIXTURE_PROBE_TASK_ID"] = task_id
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(list(command), **popen_kwargs)
    try:
        return int(process.wait())
    except KeyboardInterrupt:
        _terminate_task_process(process)
        return 130


def _pytest_phase_command(
    suite: str,
    task_id: str,
    *,
    verbose: bool,
    jobs: int,
    cache_dir: Path,
    timing_path: Path | None,
    validator_root: Path | None = None,
    validator_display_root: Path | None = None,
    staged_paths_file: Path | None = None,
    validator_ids: Sequence[str] = (),
    excluded_validator_ids: Sequence[str] = (),
    validator_paths: Sequence[Path] = (),
) -> list[str]:
    """Build one pytest command for selected ordinary and validator items.

    Intent
    ------
    Translate a resolved suite phase into one complete pytest argv.

    Rationale
    ---------
    Suite deselection, xdist policy, validator plugin inputs, cache location,
    and timing output must travel together to prevent invocation drift.

    Pseudocode
    ----------
    - set profile = suite test profile
    - if task is shared or combined:
      - set phase_inputs = functional arguments and targets from profile
    - else:
      - if task is validators:
        - set phase_inputs = validator arguments and targets
      - else:
      - if task is browser or performance:
          - set pytest_arguments = serial arguments
          - set targets = selected phase targets
        - else:
          - raise ValueError
    - if task includes validators:
      - set pytest_arguments = arguments plus validator plugin inputs
    - set pytest_arguments = arguments plus cache and optional timing path
    - return Python pytest command

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._pytest_args:
      why:
        constructs: "Builds validator-only and serial performance pytest options."
    ._suite_pytest_args:
      why:
        constructs: "Builds suite-specific functional and combined pytest options."
    """
    profile = SUITE_TEST_PROFILES.get(suite, "full")
    if task_id in {"tests:shared", "combined"}:
        pytest_args = _suite_pytest_args(profile, verbose=verbose, jobs=jobs)
        if profile == "full":
            for test in sorted(PERFORMANCE_TESTS):
                pytest_args.extend(["--deselect", test])
        targets = list(PORTABILITY_TESTS) if profile == "portability" else []
    elif task_id == "validators":
        pytest_args = _pytest_args(verbose=verbose, jobs=jobs)
        targets = [str(path) for path in validator_paths]
    elif task_id == "tests:performance":
        pytest_args = _pytest_args(verbose=verbose, jobs=1)
        targets = sorted(PERFORMANCE_TESTS)
    elif task_id == "tests:browser":
        pytest_args = _pytest_args(verbose=verbose, jobs=1)
        targets = sorted(CHROME_TESTS)
    else:
        raise ValueError(f"not a pytest phase: {task_id}")
    if task_id in {"combined", "validators"}:
        if (
            validator_root is None
            or validator_display_root is None
            or staged_paths_file is None
        ):
            raise ValueError("validator collection requires one repository view")
        pytest_args.extend(
            [
                "--officina-run-validators",
                "--officina-validator-root",
                str(validator_root),
                "--officina-validator-display-root",
                str(validator_display_root),
                "--officina-staged-paths-file",
                str(staged_paths_file),
            ]
        )
        for validator_id in validator_ids:
            pytest_args.extend(["--officina-validator", validator_id])
        for validator_id in excluded_validator_ids:
            pytest_args.extend(["--officina-exclude-validator", validator_id])
    pytest_args.extend(["-o", f"cache_dir={cache_dir}"])
    if timing_path is not None:
        pytest_args.append(f"--junitxml={timing_path}")
    plugin_args = (
        ["-p", "officina.repository_checks"]
        if task_id in {"combined", "validators"}
        else []
    )
    return [
        sys.executable,
        "-m",
        "pytest",
        *plugin_args,
        *pytest_args,
        *targets,
    ]


def _capture_working_staged_paths(repo_root: Path) -> tuple[str, ...]:
    """Return one immutable staged-path list for working-view validators.

    Intent
    ------
    Give working-view validators the same staged-path metadata shape as staged
    mirror validators.

    Rationale
    ---------
    Validator behavior may depend on which paths are staged even when source
    bytes come from the working tree.

    Pseudocode
    ----------
    - return capture_staged_paths(repo_root)

    Wraps
    -----
    officina._validator_snapshot.capture_staged_paths -> preprocess: pass the repository root unchanged; postprocess: return captured paths unchanged; fixed_arguments: none
    """
    return _validator_snapshot.capture_staged_paths(repo_root)


def _write_phase_timing_report(
    output_path: Path,
    *,
    repo_root: Path,
    results: Sequence[_PhaseResult],
) -> None:
    """Write timing schema version 1 from completed ordered phases.

    Intent
    ------
    Persist task wall time and per-file pytest totals after every completed phase.

    Rationale
    ---------
    Rewriting the full artifact after each phase preserves useful partial
    evidence when a later phase fails or is interrupted.

    Pseudocode
    ----------
    - set task_records = empty task records
    - set file_records = empty file records
    - for result in completed_results:
      - set parsed_timing = JUnit timing when available
      - set task_records = task records plus phase summary
      - set file_records = file records plus parsed files
    - set output_artifact = schema-version-1 sorted JSON

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._read_junit_timing:
      why:
        constructs: "Aggregates pytest testcase reports into per-file records."
    """
    task_records: list[dict[str, object]] = []
    file_records: list[dict[str, object]] = []
    for result in results:
        pytest_seconds = 0.0
        files: list[dict[str, object]] = []
        if result.timing_path is not None and result.timing_path.is_file():
            pytest_seconds, files = _read_junit_timing(
                result.timing_path,
                repo_root=repo_root,
                task_id=result.task_id,
            )
        task_records.append(
            {
                "task_id": result.task_id,
                "exit_code": result.exit_code,
                "wall_seconds": result.wall_seconds,
                "pytest_seconds": pytest_seconds,
                "file_count": len(files),
            }
        )
        file_records.extend(files)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repo": str(repo_root.resolve()),
                "tasks": task_records,
                "files": sorted(
                    file_records,
                    key=lambda record: (str(record["kind"]), str(record["path"])),
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def run_suite(
    repo_root: Path,
    suite: str,
    *,
    verbose: bool = False,
    jobs: int = 1,
    validator_ids: Sequence[str] = (),
    excluded_validator_ids: Sequence[str] = (),
    timing_output: Path | None = None,
    task_id: str | None = None,
    task_cache_dir: Path | None = None,
    repository_view: str = "auto",
) -> int:
    """Run one named repository verification suite.

    Intent
    ------
    Provide one root command for validator-only, test-only, and combined gates.

    Rationale
    ---------
    Every invocation selects one repository view, and combined suites give
    validator and ordinary items to the same pytest-xdist scheduler.

    Pseudocode
    ----------
    - set repository_view = staged precommit or requested working/staged view
    - set pooled_items = selected validators plus ordinary tests
    - set pooled_status = one pytest-xdist process for pooled items without fail-fast
    - set performance_status = selected serial performance thresholds after pooled items
    - return the aggregate status

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._pytest_xdist_available:
      why:
        computes: "Confirms that a parallel worker budget can be executed."
    ._write_phase_timing_report:
      why:
        computes: "Persists completed phase timing evidence after each phase."
    .officina._validator_snapshot._selected_validator_paths:
      why:
        computes: "Resolves the exact validator paths for validator-only phases."

    InstantiationsFromRepo
    ----------------------
    .officina._validator_snapshot.staged_repository_view:
      why:
        constructs: "Builds the one staged tree used by every collected item."
    ._pytest_phase_command:
      why:
        constructs: "Builds the sole pytest command for a selected test phase."
    ._run_process:
      why:
        constructs: "Runs one streaming pytest process with interruption cleanup."
    ._PhaseResult:
      why:
        constructs: "Records one completed phase for status and timing output."
    ._capture_working_staged_paths:
      why:
        constructs: "Captures staged metadata for working-tree validator runs."
    ._resolve_repository_view:
      why:
        constructs: "Resolves auto view selection before preparing the run."
    ._suite_runs:
      why:
        constructs: "Resolves the ordered pytest phases selected by the suite."
    """

    root = Path(repo_root).resolve()
    if jobs > 1 and not _pytest_xdist_available():
        raise RuntimeError(
            "pytest-xdist is required for --jobs > 1; install pytest-xdist"
        )
    phases = SUITE_PHASES[suite]
    if task_id is not None:
        if task_id not in phases:
            raise ValueError(f"task {task_id!r} is not part of suite {suite!r}")
        phases = (task_id,)
    if task_cache_dir is not None and task_id is None:
        raise ValueError("task_cache_dir requires task_id")
    runs = _suite_runs(suite, task_id)
    resolved_view = _resolve_repository_view(suite, repository_view)
    completed: list[_PhaseResult] = []
    final_status = 0
    with tempfile.TemporaryDirectory(prefix="officina-checks-") as temp_dir:
        artifact_root = Path(temp_dir)
        execution_root = root
        view_manager = None
        includes_validators = any(
            run in {"combined", "validators"} for run in runs
        )
        try:
            if includes_validators and resolved_view == "staged":
                view_manager = _validator_snapshot.staged_repository_view(root)
                prepared_view = view_manager.__enter__()
                execution_root = prepared_view.root
                staged_paths = prepared_view.staged_paths
            elif includes_validators:
                staged_paths = _capture_working_staged_paths(root)
            else:
                staged_paths = ()
        except _validator_snapshot.ValidatorRunnerError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        staged_paths_file = artifact_root / "staged-paths.json"
        staged_paths_file.write_text(
            json.dumps(list(staged_paths)),
            encoding="utf-8",
        )
        tier_exclusions = (
            () if validator_ids else SUITE_EXCLUDED_VALIDATORS.get(suite, ())
        )
        effective_exclusions = tuple(
            dict.fromkeys((*tier_exclusions, *excluded_validator_ids))
        )
        try:
            for index, phase in enumerate(runs):
                report_task_id = (
                    "tests:shared" if phase == "combined" else phase
                )
                timing_path = (
                    artifact_root / "timings" / f"{index:04d}.xml"
                    if timing_output is not None
                    else None
                )
                if timing_path is not None:
                    timing_path.parent.mkdir(parents=True, exist_ok=True)
                print(f"START task={report_task_id}")
                start = time.monotonic()
                cache_dir = (
                    Path(task_cache_dir)
                    if task_cache_dir is not None
                    else artifact_root / "pytest-cache" / f"{index:04d}"
                )
                validator_paths: tuple[Path, ...] = ()
                if phase == "validators":
                    validator_paths = tuple(
                        path
                        for _validator_id, path in (
                            _validator_snapshot._selected_validator_paths(
                                execution_root,
                                validator_ids or None,
                                effective_exclusions,
                            )
                        )
                    )
                command = _pytest_phase_command(
                    suite,
                    phase,
                    verbose=verbose,
                    jobs=jobs,
                    cache_dir=cache_dir,
                    timing_path=timing_path,
                    validator_root=execution_root,
                    validator_display_root=root,
                    staged_paths_file=staged_paths_file,
                    validator_ids=validator_ids,
                    excluded_validator_ids=effective_exclusions,
                    validator_paths=validator_paths,
                )
                phase_pycache_prefix = (
                    artifact_root / "python-cache" / f"{index:04d}"
                )
                status = _run_process(
                    command,
                    cwd=execution_root,
                    task_id=report_task_id,
                    pycache_prefix=phase_pycache_prefix,
                )
                duration = time.monotonic() - start
                completed.append(
                    _PhaseResult(report_task_id, status, duration, timing_path)
                )
                print(
                    f"== {report_task_id} == exit={status} "
                    f"duration_seconds={duration:.2f}"
                )
                final_status = max(final_status, status)
                if timing_output is not None:
                    _write_phase_timing_report(
                        timing_output,
                        repo_root=execution_root,
                        results=completed,
                    )
                if status == 130:
                    break
        finally:
            if view_manager is not None:
                view_manager.__exit__(None, None, None)
    return final_status


def main(argv: Sequence[str] | None = None) -> int:
    """Parse and execute the root verification interface.

    Intent
    ------
    Expose named repository suites and optional validator selection to callers.

    Rationale
    ---------
    One parser keeps hooks, CI, and local commands on the same selection contract.

    Pseudocode
    ----------
    - set arguments = parsed suite repository verbosity and validator options
    - if validator selection accompanies test-only suite:
      - raise parser error
    - set suite_status = selected suite result
    - return child status

    Wraps
    -----
    .run_suite -> preprocess: parse and validate CLI arguments; postprocess: return child status; fixed_arguments: none

    CallsFromRepo
    -------------
    ._default_jobs:
      why:
        computes: "Supplies the parser's default worker budget."
    ._pytest_xdist_available:
      why:
        computes: "Validates explicit parallel worker requests before dispatch."

    InstantiationsFromRepo
    ----------------------
    .officina._validator_snapshot._write_tracked_result:
      why:
        constructs: "Builds the private staged-child result payload and status."
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=sorted(SUITE_PHASES),
        default="precommit",
        help="Select validators, tests, or a named combined repository gate.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--validator",
        action="append",
        dest="validator_ids",
        help="Select one canonical validator ID; may be repeated.",
    )
    parser.add_argument(
        "--exclude-validator",
        action="append",
        dest="excluded_validator_ids",
        help="Exclude one canonical validator ID; may be repeated.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Use verbose pytest output for ordinary test phases.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=_default_jobs(),
        help=(
            "Use N pytest-xdist workers for the pooled validator and test run "
            "(default: two thirds of logical CPUs)."
        ),
    )
    parser.add_argument(
        "--repository-view",
        choices=("auto", "working", "staged"),
        default="auto",
        help=(
            "Choose one source tree for validators and tests together; auto uses "
            "the staged index for precommit and the working tree otherwise."
        ),
    )
    parser.add_argument(
        "--task-id",
        choices=("validators", "tests:shared", "tests:browser", "tests:performance"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--task-cache-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--timing-output",
        type=Path,
        help="Write task and per-file pytest timings as JSON.",
    )
    parser.add_argument("--sequential", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--tracked-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--display-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--staged-paths-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.jobs > 1 and not _pytest_xdist_available():
        parser.error(
            "--jobs > 1 requires pytest-xdist; install with `pip install pytest-xdist`"
        )
    if args.task_cache_dir is not None and args.task_id is None:
        parser.error("--task-cache-dir requires --task-id")
    if args.task_id is not None and args.task_id not in SUITE_PHASES[args.suite]:
        parser.error(
            f"task {args.task_id!r} is not part of suite {args.suite!r}"
        )
    if args.tracked_root is not None:
        if (
            args.display_root is None
            or args.result_path is None
            or args.staged_paths_file is None
        ):
            parser.error(
                "--tracked-root requires --display-root, --result-path, and "
                "--staged-paths-file"
            )
        return _validator_snapshot._write_tracked_result(
            args.tracked_root.resolve(),
            args.display_root.resolve(),
            args.result_path,
            args.validator_ids,
            args.excluded_validator_ids,
            args.staged_paths_file,
            timing_output=args.timing_output,
        )
    if (args.validator_ids or args.excluded_validator_ids) and args.suite in {
        "tests",
        "portability",
    }:
        parser.error(
            "validator selection requires a suite that includes validators"
        )
    return run_suite(
        args.repo_root,
        args.suite,
        verbose=args.verbose,
        jobs=args.jobs,
        validator_ids=args.validator_ids or (),
        excluded_validator_ids=args.excluded_validator_ids or (),
        timing_output=args.timing_output,
        task_id=args.task_id,
        task_cache_dir=args.task_cache_dir,
        repository_view=args.repository_view,
    )
