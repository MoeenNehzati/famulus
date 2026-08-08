"""Central implementation for repository tests and conformance validators."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import ModuleType
from typing import Callable, Sequence

import pytest

from officina import _validator_snapshot
from officina.common.python_source_cache import PythonSourceCache
from officina.common.test_discovery import discover_repository_test_dirs


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
PRECOMMIT_EXCLUDED_TESTS = {
    "tests/test_nested_module_migration.py::"
    "TestNestedModuleMigrationContract::"
    "test_repository_inventory_matches_reviewed_v5_cutover_surface",
    *INSTALLATION_TESTS,
    *CHROME_TESTS,
    *DOCSTRING_TESTS,
}
PREPUSH_EXCLUDED_TESTS = DOCSTRING_TESTS
SUITE_EXCLUDED_VALIDATORS = {
    "precommit": {"repo/docstrings"},
    "pre-push": {"repo/docstrings"},
}
PORTABILITY_TESTS = (
    "tests/test_officina_atomic_files.py::test_secure_append_creates_then_appends_complete_framed_records",
    "tests/test_officina_atomic_files.py::test_windows_native_secure_create_replace_append_and_acl",
    "tests/test_officina_dispatcher.py::test_python_process_target_keeps_gateway_and_entry_separate",
    "tests/test_officina_git_provenance.py::test_git_test_repository_preserves_exact_bytes_under_ambient_autocrlf",
    "skills/recurring-tasks/_rtx/tests/test_schedule_backend.py::test_linux_sync_writes_units_and_enables_timer",
    "tests/test_officina_blueprint_graph.py::test_content_ownership_accepts_equivalent_repository_alias",
    "tests/test_repository_validator_checks.py::test_run_all_isolates_unmerged_index_and_restores_git_environment",
)


@dataclass(frozen=True)
class CheckTask:
    """Describe one existing isolated pytest process for shared scheduling."""

    id: str
    argv: tuple[str, ...]
    slots: int


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
        """Keep only items emitted by the repository validator collector.

        Intent
        ------
        Remove duplicate ordinary pytest collection for explicitly selected files.

        Rationale
        ---------
        Pytest treats explicit file arguments as collection roots even when their
        names do not match test patterns. Without filtering, fixture-backed
        validator modules execute once through each collector.

        Pseudocode
        ----------
        - set validator_items = collected items carrying canonical validator ids
        - set session_items = validator items

        Wraps
        -----
        - none
        """
        items[:] = [
            item
            for item in items
            if isinstance(getattr(item, "_validator_id", None), str)
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
            if validator_id == state.owner_id:
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


def run_validators_with_pytest(
    *,
    runner: ModuleType,
    tracked_root: Path,
    display_root: Path,
    validator_ids: Sequence[str] | None,
    excluded_validator_ids: Sequence[str] | None,
    staged_paths: Sequence[str],
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
    exit_code = pytest.main(
        [
            "-q",
            "--disable-warnings",
            "--confcutdir",
            str(tracked_root),
            *(str(path) for _validator_id, path in selected_paths),
        ],
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
    "validators": (("validators", None),),
    "tests": (("tests", "full"),),
    "precommit": (("validators", None), ("tests", "precommit")),
    "pre-push": (("validators", None), ("tests", "pre-push")),
    "portability": (("tests", "portability"),),
    "full": (("validators", None), ("tests", "full")),
}


def _pytest_args(*, verbose: bool, jobs: int = 1) -> list[str]:
    """Build the common pytest arguments for repository test phases.

    Intent
    ------
    Keep source-path and output-mode configuration identical across suites.

    Rationale
    ---------
    Central arguments prevent hook and CI invocations from drifting.

    Pseudocode
    ----------
    - set pytest_arguments = source path option and selected verbosity
    - if jobs is greater than one:
      - set pytest_arguments = arguments plus xdist worker configuration
    - return pytest_arguments

    Wraps
    -----
    - none
    """
    args = ["-o", "pythonpath=src", "-v" if verbose else "-q"]
    if jobs > 1:
        args.extend(["-n", str(jobs), "--dist", "worksteal"])
    return args


def _default_jobs() -> int:
    """Use two thirds of the host's reported logical CPUs for shared tests."""

    logical_cpus = os.cpu_count() or 1
    return max(1, (logical_cpus * 2) // 3)


def _pytest_xdist_available() -> bool:
    """Report whether pytest-xdist is installed in the current Python.

    This allows an explicit CLI/error path when parallel budgeting is requested.
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
    Apply suite-specific deselections to the common pytest arguments.

    Rationale
    ---------
    Precommit exclusions are repository policy and belong with suite selection.

    Pseudocode
    ----------
    - set pytest_arguments = common pytest arguments
    - if name is precommit:
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
    args = _pytest_args(verbose=verbose, jobs=jobs)
    if name == "precommit":
        for test in sorted(PRECOMMIT_EXCLUDED_TESTS):
            args.extend(["--deselect", test])
    elif name == "pre-push":
        for test in sorted(PREPUSH_EXCLUDED_TESTS):
            args.extend(["--deselect", test])
    return args


def _resolve_suite(name: str) -> list[str]:
    """Resolve one ordinary-test suite to existing pytest targets.

    Intent
    ------
    Produce the exact repository paths or nodes selected by a suite name.

    Rationale
    ---------
    Missing configured paths must fail before partial test execution begins.

    Pseudocode
    ----------
    - set test_targets = portability nodes or discovered repository test directories
    - if name is precommit:
      - set test_targets = targets without precommit exclusions
    - if configured targets are missing:
      - raise configuration error
    - return test_targets

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .officina.common.test_discovery.discover_repository_test_dirs:
      why:
        constructs: "Builds the discovered working-tree test directory collection."
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
    """Partition ordinary tests into shared and isolated pytest processes.

    Intent
    ------
    Keep nested runtime suites isolated while consolidating all other targets.

    Rationale
    ---------
    Nested runtimes may carry incompatible import and plugin state.

    Pseudocode
    ----------
    - set nested_targets = nested runtime test targets
    - set shared_targets = all remaining test targets
    - return shared group followed by singleton nested groups

    Wraps
    -----
    - none

    """
    nested = sorted(path for path in test_dirs if "/_rtx/tests" in path)
    shared = [path for path in test_dirs if path not in nested]
    return ([shared] if shared else []) + [[path] for path in nested]


def _shared_test_jobs(jobs: int) -> int:
    """Reserve one quarter of the worker budget for isolated pytest tasks."""

    if jobs <= 1:
        return 1
    return jobs - max(1, jobs // 4)


def _run_validator_task(
    repo_root: Path,
    *,
    validator_ids: Sequence[str] = (),
    excluded_validator_ids: Sequence[str] = (),
) -> int:
    """Run the existing complete validator snapshot lifecycle as one task."""

    try:
        results = _validator_snapshot.run_all(
            repo_root=Path(repo_root).resolve(),
            validator_ids=validator_ids,
            excluded_validator_ids=excluded_validator_ids,
        )
    except _validator_snapshot.ValidatorRunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _validator_snapshot._render_findings(results)


def _build_check_tasks(
    repo_root: Path,
    suite: str,
    *,
    verbose: bool,
    jobs: int,
    validator_ids: Sequence[str],
    excluded_validator_ids: Sequence[str],
) -> list[CheckTask]:
    """Build one queue from the existing validator and test execution groups."""

    root = Path(repo_root).resolve()
    tasks: list[CheckTask] = []
    phases = SUITE_PHASES[suite]
    if any(phase == "validators" for phase, _test_suite in phases):
        tier_exclusions = (
            ()
            if validator_ids
            else SUITE_EXCLUDED_VALIDATORS.get(suite, ())
        )
        effective_exclusions = tuple(
            dict.fromkeys((*tier_exclusions, *excluded_validator_ids))
        )
        argv = [
            sys.executable,
            str(REPO_ROOT / "repo_checks.py"),
            "--internal-run-validators",
            "--repo-root",
            str(root),
        ]
        for validator_id in validator_ids:
            argv.extend(["--validator", validator_id])
        for validator_id in effective_exclusions:
            argv.extend(["--exclude-validator", validator_id])
        tasks.append(CheckTask("validators", tuple(argv), 1))

    test_suite = next(
        (test_suite for phase, test_suite in phases if phase == "tests"),
        None,
    )
    if test_suite is None:
        return tasks
    previous_root = globals()["REPO_ROOT"]
    globals()["REPO_ROOT"] = root
    try:
        groups = _execution_groups(_resolve_suite(str(test_suite)))
        shared_jobs = _shared_test_jobs(jobs)
        for group in groups:
            isolated = len(group) == 1 and "/_rtx/tests" in group[0]
            group_jobs = 1 if isolated else shared_jobs
            task_id = f"tests:{group[0]}" if isolated else "tests:shared"
            argv = (
                sys.executable,
                "-m",
                "pytest",
                *_suite_pytest_args(
                    str(test_suite),
                    verbose=verbose,
                    jobs=group_jobs,
                ),
                *group,
            )
            tasks.append(CheckTask(task_id, tuple(argv), group_jobs))
    finally:
        globals()["REPO_ROOT"] = previous_root
    return tasks


def _terminate_task_process(process: subprocess.Popen[object]) -> None:
    """Terminate one task process and its process group after interruption."""

    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()


def _run_check_tasks(
    tasks: Sequence[CheckTask],
    *,
    repo_root: Path,
    jobs: int,
    pooled: bool,
) -> int:
    """Run check tasks with one bounded first-fitting process coordinator."""

    if any(task.slots > jobs for task in tasks):
        raise ValueError("check task requires more worker slots than --jobs")
    pending = list(enumerate(tasks))
    active: dict[
        int,
        tuple[subprocess.Popen[object], CheckTask, object, object, Path, Path, float],
    ] = {}
    results: dict[int, tuple[int, float]] = {}
    admission_open = True
    root = Path(repo_root).resolve()
    with tempfile.TemporaryDirectory(prefix="officina-checks-") as temp_dir:
        output_root = Path(temp_dir)
        try:
            while pending or active:
                if admission_open:
                    while pending and (pooled or not active):
                        used = sum(
                            task.slots
                            for _process, task, _stdout, _stderr, _out_path, _err_path, _start
                            in active.values()
                        )
                        available = jobs - used
                        fitting = next(
                            (
                                position
                                for position, (_index, task) in enumerate(pending)
                                if task.slots <= available
                            ),
                            None,
                        )
                        if fitting is None:
                            break
                        index, task = pending.pop(fitting)
                        stdout_path = output_root / f"{index:04d}.stdout.log"
                        stderr_path = output_root / f"{index:04d}.stderr.log"
                        stdout_log = stdout_path.open("w", encoding="utf-8")
                        stderr_log = stderr_path.open("w", encoding="utf-8")
                        popen_kwargs: dict[str, object] = {
                            "stdout": stdout_log,
                            "stderr": stderr_log,
                            "cwd": root,
                        }
                        if os.name == "posix":
                            popen_kwargs["start_new_session"] = True
                        else:
                            popen_kwargs["creationflags"] = (
                                subprocess.CREATE_NEW_PROCESS_GROUP
                            )
                        command = list(task.argv)
                        if command[1:3] == ["-m", "pytest"]:
                            cache_dir = output_root / "pytest-cache" / f"{index:04d}"
                            command[3:3] = ["-o", f"cache_dir={cache_dir}"]
                        process = subprocess.Popen(command, **popen_kwargs)
                        start = time.monotonic()
                        print(f"START task={task.id} slots={task.slots}")
                        active[index] = (
                            process,
                            task,
                            stdout_log,
                            stderr_log,
                            stdout_path,
                            stderr_path,
                            start,
                        )

                completed = []
                for index, (
                    process,
                    task,
                    stdout_log,
                    stderr_log,
                    stdout_path,
                    stderr_path,
                    start,
                ) in active.items():
                    status = process.poll()
                    if status is None:
                        continue
                    stdout_log.close()
                    stderr_log.close()
                    results[index] = (int(status), time.monotonic() - start)
                    completed.append(index)
                    if status:
                        admission_open = False
                for index in completed:
                    active.pop(index)
                if active and not completed:
                    time.sleep(0.01)
                if not active and not admission_open:
                    break
        except KeyboardInterrupt:
            for (
                process,
                _task,
                stdout_log,
                stderr_log,
                _stdout_path,
                _stderr_path,
                _start,
            ) in active.values():
                _terminate_task_process(process)
                stdout_log.close()
                stderr_log.close()
            return 130

        for index, task in enumerate(tasks):
            if index not in results:
                continue
            stdout_path = output_root / f"{index:04d}.stdout.log"
            stderr_path = output_root / f"{index:04d}.stderr.log"
            exit_code, duration = results[index]
            print(
                f"== {task.id} == exit={exit_code} "
                f"duration_seconds={duration:.2f}"
            )
            stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
            if stdout:
                print(stdout, end="" if stdout.endswith("\n") else "\n")
            if stderr:
                print(
                    stderr,
                    end="" if stderr.endswith("\n") else "\n",
                    file=sys.stderr,
                )
        for index in range(len(tasks)):
            if results.get(index) and (results[index][0] != 0):
                return results[index][0]
    return 0


def _run_test_suite(name: str, *, verbose: bool, jobs: int = 1) -> int:
    """Execute every pytest process group for one ordinary-test suite.

    Intent
    ------
    Run resolved working-tree tests under the central repository check command.

    Rationale
    ---------
    Process grouping remains internal instead of requiring another executable.

    Pseudocode
    ----------
    - for group in resolved execution groups:
      - set group_jobs = requested jobs for shared groups or one for isolated groups
      - set pytest_arguments = arguments for named suite and group jobs
      - set group_status = pytest subprocess status
      - if group_status is nonzero:
        - return group_status
    - return success

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._resolve_suite:
      why:
        computes: "Supplies the exact pytest targets for the named suite."
    ._execution_groups:
      why:
        computes: "Supplies the process-isolated grouping of resolved targets."

    InstantiationsFromRepo
    ----------------------
    ._suite_pytest_args:
      why:
        constructs: "Builds the pytest arguments used by each process group."
    """
    for group in _execution_groups(_resolve_suite(name)):
        group_jobs = jobs if len(group) > 1 else 1
        pytest_args = _suite_pytest_args(
            name,
            verbose=verbose,
            jobs=group_jobs,
        )
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *pytest_args, *group],
            cwd=REPO_ROOT,
            check=False,
        )
        if completed.returncode:
            return completed.returncode
    return 0


def run_suite(
    repo_root: Path,
    suite: str,
    *,
    verbose: bool = False,
    jobs: int = 1,
    validator_ids: Sequence[str] = (),
    excluded_validator_ids: Sequence[str] = (),
    pooled: bool | None = None,
) -> int:
    """Run one named repository verification suite.

    Intent
    ------
    Provide one root command for validator-only, test-only, and combined gates.

    Rationale
    ---------
    Validators retain staged-mirror semantics and ordinary tests retain working-tree
    semantics while callers use one stable suite-selection interface.

    Pseudocode
    ----------
    - set phases = ordered phases for requested suite
    - for phase in phases:
      - set command = canonical validator or ordinary-test command
      - set phase_status = executed command status
      - if phase_status is nonzero:
        - return phase_status
    - return success after every phase passes

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .officina._validator_snapshot.run_all:
      why:
        constructs: "Builds validator findings from the captured staged repository."
    .officina._validator_snapshot._render_findings:
      why:
        constructs: "Builds the validator phase status from canonical findings."
    ._run_test_suite:
      why:
        constructs: "Builds the ordinary-test phase status for the selected suite."
    """

    root = Path(repo_root).resolve()
    if jobs > 1 and not _pytest_xdist_available():
        raise RuntimeError(
            "pytest-xdist is required for --jobs > 1; install pytest-xdist"
        )
    if pooled is not False:
        tasks = _build_check_tasks(
            root,
            suite,
            verbose=verbose,
            jobs=jobs,
            validator_ids=validator_ids,
            excluded_validator_ids=excluded_validator_ids,
        )
        return _run_check_tasks(
            tasks,
            repo_root=root,
            jobs=jobs,
            pooled=pooled,
        )
    for phase, test_suite in SUITE_PHASES[suite]:
        if phase == "validators":
            tier_exclusions = (
                ()
                if validator_ids
                else SUITE_EXCLUDED_VALIDATORS.get(suite, ())
            )
            effective_exclusions = tuple(
                dict.fromkeys((*tier_exclusions, *excluded_validator_ids))
            )
            try:
                results = _validator_snapshot.run_all(
                    repo_root=root,
                    validator_ids=validator_ids,
                    excluded_validator_ids=effective_exclusions,
                )
            except _validator_snapshot.ValidatorRunnerError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            status = _validator_snapshot._render_findings(results)
        else:
            previous_root = globals()["REPO_ROOT"]
            globals()["REPO_ROOT"] = root
            try:
                status = _run_test_suite(
                    str(test_suite),
                    verbose=verbose,
                    jobs=jobs,
                )
            finally:
                globals()["REPO_ROOT"] = previous_root
        if status:
            return status
    return 0


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
            "Use a total budget of N pytest workers across selected checks "
            "(default: two thirds of logical CPUs)."
        ),
    )
    parser.add_argument(
        "--internal-run-validators",
        action="store_true",
        help=argparse.SUPPRESS,
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
    if args.internal_run_validators:
        return _run_validator_task(
            args.repo_root,
            validator_ids=tuple(args.validator_ids or ()),
            excluded_validator_ids=tuple(args.excluded_validator_ids or ()),
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
        pooled=not args.sequential,
    )
