"""Central implementation for repository tests and conformance validators."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from typing import Callable, Sequence

import pytest

from officina import _validator_snapshot
from officina.common.test_discovery import discover_repository_test_dirs


REPO_ROOT = Path(__file__).resolve().parents[2]

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
        """Return one marked normal pytest function for this validator.

        Intent
        ------
        Make the selected validator protocol visible to standard pytest execution.

        Rationale
        ---------
        One item per canonical validator preserves granular reports and selection.

        Pseudocode
        ----------
        - set validator_entry = entry name and callable
        - set pytest_function = normal pytest function for validator_entry
        - set pytest_function_metadata = validator marker and canonical id
        - return singleton item list

        Wraps
        -----
        - none
        """
        entry_name, callobj = self.validator_plugin.entry_points[
            self.validator_id
        ]
        item = pytest.Function.from_parent(
            self,
            name=self.validator_id,
            callobj=callobj,
        )
        item.add_marker("validator")
        item._validator_id = self.validator_id  # type: ignore[attr-defined]
        item._validator_entry_name = entry_name  # type: ignore[attr-defined]
        return [item]


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

    @pytest.fixture
    def repo_root(self) -> Path:
        """Return the exact staged repository root.

        Intent
        ------
        Inject the materialized Git-index view into validator functions.

        Rationale
        ---------
        Validators must not read unstaged working-tree bytes.

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
        state = self._graph_state() if entry_name == "validate_with_graph" else None
        if state is not None and state.errors:
            if validator_id == state.owner_id:
                pytest.fail("\n".join(state.errors), pytrace=False)
            pytest.skip("blueprint preflight failed")
        if state is not None and state.graph is None:
            pytest.skip("blueprint preflight produced no graph")
        arguments = {
            name: pyfuncitem.funcargs[name]
            for name in pyfuncitem._fixtureinfo.argnames
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
            self.results[validator_id] = errors
            pytest.fail("\n".join(errors), pytrace=False)
        return True


def run_validators_with_pytest(
    *,
    runner: ModuleType,
    tracked_root: Path,
    display_root: Path,
    validator_ids: Sequence[str] | None,
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
    "pre-push": (("validators", None), ("tests", "full")),
    "portability": (("tests", "portability"),),
    "full": (("validators", None), ("tests", "full")),
}


def _pytest_args(*, verbose: bool) -> list[str]:
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
    - return pytest_arguments

    Wraps
    -----
    - none
    """
    return ["-o", "pythonpath=src", "-v" if verbose else "-q"]


def _suite_pytest_args(name: str, *, verbose: bool) -> list[str]:
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
    args = _pytest_args(verbose=verbose)
    if name == "precommit":
        for test in sorted(PRECOMMIT_EXCLUDED_TESTS):
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


def _run_test_suite(name: str, *, verbose: bool) -> int:
    """Execute every pytest process group for one ordinary-test suite.

    Intent
    ------
    Run resolved working-tree tests under the central repository check command.

    Rationale
    ---------
    Process grouping remains internal instead of requiring another executable.

    Pseudocode
    ----------
    - set pytest_arguments = arguments for named suite
    - for group in resolved execution groups:
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
    pytest_args = _suite_pytest_args(name, verbose=verbose)
    for group in _execution_groups(_resolve_suite(name)):
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
    validator_ids: Sequence[str] = (),
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
    for phase, test_suite in SUITE_PHASES[suite]:
        if phase == "validators":
            try:
                results = _validator_snapshot.run_all(
                    repo_root=root,
                    validator_ids=validator_ids,
                )
            except _validator_snapshot.ValidatorRunnerError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            status = _validator_snapshot._render_findings(results)
        else:
            previous_root = globals()["REPO_ROOT"]
            globals()["REPO_ROOT"] = root
            try:
                status = _run_test_suite(str(test_suite), verbose=verbose)
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
        "-v",
        "--verbose",
        action="store_true",
        help="Use verbose pytest output for ordinary test phases.",
    )
    parser.add_argument("--tracked-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--display-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--staged-paths-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
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
            args.staged_paths_file,
        )
    if args.validator_ids and args.suite in {"tests", "portability"}:
        parser.error("--validator requires a suite that includes validators")
    return run_suite(
        args.repo_root,
        args.suite,
        verbose=args.verbose,
        validator_ids=args.validator_ids or (),
    )
