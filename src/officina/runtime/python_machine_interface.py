"""Base contract for Python implementations of machine interfaces."""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Mapping, Sequence

if TYPE_CHECKING:
    from officina.common.blueprint_graph import RepositoryBlueprintGraph
    from officina.dispatcher import ResolvedInvocationMetadata


class PythonRouteSmokeTraceError(RuntimeError):
    """Raised when a Python route-smoke dependency trace cannot complete."""


class PythonProcessTargetError(ValueError):
    """Raised when a Python gateway and process entry are not canonical."""


def logical_python_package_name(module_id: str) -> str:
    """Return a reversible, import-safe package name for one global module ID."""

    if not isinstance(module_id, str) or not module_id:
        raise PythonProcessTargetError(
            "logical Python package identity needs a non-empty module ID"
        )
    return f"_officina_module_{module_id.encode('utf-8').hex()}"


@dataclass(frozen=True)
class PythonProcessTarget:
    """A validated Python gateway path and entry class."""

    gateway_path: Path
    process_entry: str
    logical_package: str | None = None
    logical_entrypoint: str | None = None

    def __post_init__(self) -> None:
        path = self.gateway_path
        has_logical_identity = (
            self.logical_package is not None
            or self.logical_entrypoint is not None
        )
        if (
            not isinstance(path, Path)
            or path.is_absolute()
            or (not has_logical_identity and (
                len(path.parts) < 2 or path.parts[0] != "_rtx"
            ))
            or "." in path.parts
            or ".." in path.parts
            or path.suffix != ".py"
        ):
            raise PythonProcessTargetError(
                "Python gateway path must be a relative `_rtx/*.py` path "
                "without current- or parent-directory traversal"
            )
        package = self.logical_package
        entrypoint = self.logical_entrypoint
        if (package is None) != (entrypoint is None):
            raise PythonProcessTargetError(
                "logical package and entrypoint must be provided together"
            )
        if package is not None and entrypoint is not None:
            if not all(part.isidentifier() for part in package.split(".")):
                raise PythonProcessTargetError(
                    "logical Python package must be a dotted identifier"
                )
            physical_parts = (
                path.parent.parts
                if path.name == "__init__.py"
                else (*path.parent.parts, path.stem)
            )
            suffix = ".".join(
                part for part in physical_parts if part not in {"", "."}
            )
            expected_entrypoint = (
                package if not suffix else f"{package}.{suffix}"
            )
            if entrypoint != expected_entrypoint:
                raise PythonProcessTargetError(
                    "logical Python entrypoint must match the physical gateway path"
                )
        entry = self.process_entry
        if (
            not isinstance(entry, str)
            or entry != entry.strip()
            or not entry.isidentifier()
        ):
            raise PythonProcessTargetError(
                "Python process entry must be one non-empty Python identifier"
            )


@dataclass(frozen=True)
class RuntimeDispatchContext:
    """Runtime identity of the Python interface currently being executed."""

    caller_module_id: str | None = None
    caller_source_id: str | None = None
    repo_root: Path | None = None
    repository_config: Path | None = None


_RUNTIME_DISPATCH_CONTEXT_ATTRIBUTE = "_officina_runtime_dispatch_context"


def set_runtime_dispatch_context(
    interface: "PythonMachineInterface",
    *,
    caller_module_id: str | None = None,
    caller_source_id: str | None = None,
    repo_root: Path | None = None,
    repository_config: Path | None = None,
) -> None:
    """Attach dispatcher-resolved runtime identity to one loaded interface."""

    setattr(
        interface,
        _RUNTIME_DISPATCH_CONTEXT_ATTRIBUTE,
        RuntimeDispatchContext(
            caller_module_id=caller_module_id,
            caller_source_id=caller_source_id,
            repo_root=repo_root,
            repository_config=repository_config,
        ),
    )


def runtime_dispatch_context(
    interface: "PythonMachineInterface",
) -> RuntimeDispatchContext:
    """Return dispatcher-resolved runtime identity for one loaded interface."""

    context = getattr(interface, _RUNTIME_DISPATCH_CONTEXT_ATTRIBUTE, None)
    if isinstance(context, RuntimeDispatchContext):
        return context
    return RuntimeDispatchContext()


@dataclass(frozen=True)
class DispatchCallDeclaration:
    """One alias-resolved DispatchCall declaration in a Python syntax tree."""

    caller_module_id: str | None
    target_module_id: str | None
    interface: str | None
    lineno: int
    keywords: Mapping[str, ast.Constant]
    legacy_v4: bool = False

    @property
    def caller_skill(self) -> str | None:
        return self.caller_module_id

    @property
    def target_skill(self) -> str | None:
        return self.target_module_id


def analyze_dispatch_call_declarations(
    tree: ast.AST,
) -> tuple[DispatchCallDeclaration, ...]:
    """Return only DispatchCall constructors imported from the runtime owner."""

    direct: set[str] = set()
    modules: set[str] = set()
    constants: dict[str, str] = {}
    if isinstance(tree, ast.Module):
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == (
                "officina.runtime.python_machine_interface"
            ):
                direct.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "DispatchCall"
                )
            elif isinstance(node, ast.Import):
                modules.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "officina.runtime.python_machine_interface"
                )
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if (
                    isinstance(target, ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    constants[target.id] = node.value.value

    def resolved(value: ast.AST | None) -> str | None:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, ast.Name):
            return constants.get(value.id)
        return None

    declarations: list[DispatchCallDeclaration] = []

    def qualified_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = qualified_name(node.value)
            return f"{prefix}.{node.attr}" if prefix is not None else None
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        function_name = qualified_name(function)
        recognized = function_name in direct or function_name in {
            f"{module}.DispatchCall" for module in modules
        }
        if not recognized:
            continue
        keywords = {
            keyword.arg: keyword.value
            for keyword in node.keywords
            if keyword.arg is not None
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        }
        values = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        declarations.append(
            DispatchCallDeclaration(
                caller_module_id=(
                    resolved(values.get("caller_module_id"))
                    or resolved(values.get("caller_skill"))
                ),
                target_module_id=(
                    resolved(values.get("target_module_id"))
                    or resolved(values.get("target_skill"))
                ),
                interface=resolved(values.get("interface")),
                lineno=getattr(node, "lineno", 0),
                keywords=keywords,
                legacy_v4=(
                    "caller_module_id" not in values
                    and "target_module_id" not in values
                ),
            )
        )
    return tuple(declarations)


def _normalize_route_smoke_trace_specifications(
    repo_root: Path,
    specifications: Iterable[tuple[Path, PythonProcessTarget]],
) -> tuple[tuple[Path, PythonProcessTarget], ...]:
    """Validate and canonicalize route-smoke trace specifications."""

    repository_root = repo_root.resolve()
    normalized: set[tuple[Path, PythonProcessTarget]] = set()
    for specification in specifications:
        if not isinstance(specification, tuple) or len(specification) != 2:
            raise ValueError(
                "route-smoke trace specifications must be "
                "(skill_root, PythonProcessTarget) pairs"
            )
        skill_dir, python_target = specification
        try:
            skill_root = Path(skill_dir).resolve()
        except TypeError as exc:
            raise ValueError("route-smoke skill roots must be paths") from exc
        try:
            skill_root.relative_to(repository_root)
        except ValueError as exc:
            raise ValueError(
                f"route-smoke skill root is outside the repository: {skill_root}"
            ) from exc
        if not skill_root.is_dir():
            raise ValueError(f"route-smoke skill root is not a directory: {skill_root}")
        if not isinstance(python_target, PythonProcessTarget):
            raise ValueError(
                "route-smoke targets must be PythonProcessTarget values"
            )
        if not (skill_root / python_target.gateway_path).is_file():
            raise ValueError(
                f"route-smoke gateway does not exist: "
                f"{skill_root / python_target.gateway_path}"
            )
        normalized.add((skill_root, python_target))
    return tuple(
        sorted(
            normalized,
            key=lambda item: (
                item[0].as_posix(),
                item[1].gateway_path.as_posix(),
                item[1].process_entry,
            ),
        )
    )


def _python_process_target_payload(
    target: PythonProcessTarget,
) -> dict[str, str]:
    payload = {
        "gateway_path": target.gateway_path.as_posix(),
        "process_entry": target.process_entry,
    }
    if (
        target.logical_package is not None
        and target.logical_entrypoint is not None
    ):
        payload["logical_package"] = target.logical_package
        payload["logical_entrypoint"] = target.logical_entrypoint
    return payload


def trace_python_route_smoke_dependencies_batch(
    repo_root: Path,
    specifications: Iterable[tuple[Path, PythonProcessTarget]],
    *,
    expected_schema_version: int = 5,
    schema_root: Path | None = None,
) -> dict[tuple[Path, PythonProcessTarget], tuple[Path, ...]]:
    """Return isolated loaded-path traces from one Python child process."""

    if expected_schema_version not in {4, 5}:
        raise ValueError("expected_schema_version must be 4 or 5")
    repository_root = repo_root.resolve()
    normalized = _normalize_route_smoke_trace_specifications(
        repository_root,
        specifications,
    )
    if not normalized:
        return {}
    candidate_source_root = repository_root / "src"
    source_root = (
        candidate_source_root
        if (candidate_source_root / "officina").is_dir()
        else Path(__file__).resolve().parents[2]
    )
    if schema_root is None:
        candidate_schema_root = repository_root / "references" / "blueprint"
        package_schema_root = (
            Path(__file__).resolve().parents[3] / "references" / "blueprint"
        )
        if expected_schema_version == 4:
            candidate_schema_root /= "migrations" / "v4"
            package_schema_root /= "migrations" / "v4"
        selected_schema_root = (
            candidate_schema_root
            if (candidate_schema_root / "module.schema.json").is_file()
            else package_schema_root
        )
    else:
        selected_schema_root = schema_root.resolve()
    trace_code = r"""
import contextlib
import io
import json
import os
import sys
from pathlib import Path

src_root = Path(sys.argv[1]).resolve()
repo_root = Path(sys.argv[2]).resolve()
schema_root = Path(sys.argv[3]).resolve()
expected_schema_version = int(sys.argv[5])
officina_root = src_root / "officina"
sys.path.insert(0, str(src_root))

from officina.common.blueprint_graph import (
    BlueprintGraphError,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.common.blueprint_inventory import iter_blueprints
from officina.runtime.python_machine_interface import (
    DispatchDependencyResolver,
    PythonProcessTarget,
)
from officina.runtime.python_machine_interface_runner import load_interface, run_python_machine_interface

specifications = [
    (
        Path(item["skill_root"]).resolve(),
        PythonProcessTarget(
            Path(item["python_target"]["gateway_path"]),
            item["python_target"]["process_entry"],
            logical_package=item["python_target"].get("logical_package"),
            logical_entrypoint=item["python_target"].get("logical_entrypoint"),
        ),
    )
    for item in json.loads(sys.argv[4])
]

def is_under(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True

def module_path(module):
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return None
    path = Path(module_file).resolve()
    if path.suffix not in {".py", ".pyi"}:
        return None
    return path

def module_snapshot():
    return {
        name: path
        for name, module in tuple(sys.modules.items())
        if (path := module_path(module)) is not None
    }

def collect_loaded_paths(paths, before):
    for name, module in tuple(sys.modules.items()):
        path = module_path(module)
        if path is None:
            continue
        if not (is_under(path, repo_root) or is_under(path, officina_root)):
            continue
        if before.get(name) != path:
            paths.add(path.as_posix())

def collect_bound_paths(paths, interface):
    sources = getattr(interface, "_officina_bound_package_sources", {})
    if not isinstance(sources, dict):
        return
    executed_modules = getattr(sources, "executed_modules", set())
    if not isinstance(executed_modules, set):
        return
    for module_name in executed_modules:
        source = sources.get(module_name)
        if source is None:
            continue
        _source, physical_path, _is_package = source
        path = Path(physical_path).resolve()
        if path.suffix == ".py" and path.is_file() and is_under(path, repo_root):
            paths.add(path.as_posix())

from officina.common.certification_view import CertificationDecision

class TraceCertificationView:
    def check_export(self, module_id, interface_id, interface_version, source_node_id):
        return CertificationDecision(True, "route-smoke-trace", "Trace-only certification view.")

    def certificate_for(self, node_id):
        return None

try:
    graph = load_repository_blueprint_graph(
        repo_root,
        schema_root=schema_root,
        expected_schema_version=expected_schema_version,
    )
except BlueprintGraphError as exc:
    if tuple(iter_blueprints(repo_root)):
        raise
    graph = RepositoryBlueprintGraph(
        nodes={},
        node_edges=(),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
    )
resolver = DispatchDependencyResolver(
    repo_root=repo_root,
    certification_view=TraceCertificationView(),
    graph=graph,
)
base_cwd = Path.cwd()
base_sys_path = list(sys.path)
base_modules = dict(sys.modules)
base_module_paths = module_snapshot()
base_module_namespaces = {
    name: dict(module.__dict__)
    for name, module in base_modules.items()
    if (
        (path := module_path(module)) is not None
        and (is_under(path, repo_root) or is_under(path, officina_root))
    )
}
basis_paths = {
    path.as_posix()
    for path in base_module_paths.values()
    if is_under(path, officina_root)
}

def restore_module_baseline():
    for name, module in tuple(sys.modules.items()):
        path = module_path(module)
        if (
            path is not None
            and (is_under(path, repo_root) or is_under(path, officina_root))
            and base_module_paths.get(name) != path
        ):
            del sys.modules[name]
    for name, module in base_modules.items():
        path = module_path(module)
        if (
            path is not None
            and (is_under(path, repo_root) or is_under(path, officina_root))
        ):
            namespace = base_module_namespaces[name]
            for attribute in tuple(module.__dict__):
                if attribute not in namespace:
                    del module.__dict__[attribute]
            module.__dict__.update(namespace)
            sys.modules[name] = module

results = []
for skill_dir, python_target in specifications:
    os.chdir(base_cwd)
    sys.path[:] = base_sys_path
    restore_module_baseline()
    before = module_snapshot()
    paths = set(basis_paths)
    try:
        os.chdir(skill_dir)
        with contextlib.redirect_stdout(io.StringIO()):
            interface = load_interface(
                python_target.gateway_path,
                python_target.process_entry,
                logical_package=python_target.logical_package,
                logical_entrypoint=python_target.logical_entrypoint,
            )
            run_python_machine_interface(interface, ["--route-smoke"])
            collect_bound_paths(paths, interface)
            collect_loaded_paths(paths, before)
            physical_gateway = (
                skill_dir / python_target.gateway_path
            ).resolve()
            caller_sources = [
                source_id
                for source_id, source in graph.nodes.items()
                if source.node_type == "behavioral_source"
                and source.gateway_path == physical_gateway
            ]
            caller_source_id = (
                caller_sources[0] if len(caller_sources) == 1 else None
            )
            caller_module_id = (
                graph.source_modules.get(caller_source_id)
                if caller_source_id is not None
                else None
            )
            dependencies = resolver.collect(
                interface,
                caller_module_id=caller_module_id,
                caller_source_id=caller_source_id,
            )
            collect_loaded_paths(paths, before)
            for dependency in dependencies:
                invocation = dependency.resolved
                target_interface = resolver.load_resolved_python_interface(invocation)
                if target_interface is not None:
                    previous_cwd = Path.cwd()
                    try:
                        os.chdir(invocation.cwd)
                        run_python_machine_interface(
                            target_interface,
                            ["--route-smoke"],
                        )
                        collect_bound_paths(paths, target_interface)
                    finally:
                        os.chdir(previous_cwd)
                collect_loaded_paths(paths, before)
    finally:
        os.chdir(base_cwd)
        sys.path[:] = base_sys_path
        restore_module_baseline()
    results.append(
        {
            "skill_root": skill_dir.as_posix(),
            "python_target": {
                key: value
                for key, value in {
                    "gateway_path": python_target.gateway_path.as_posix(),
                    "process_entry": python_target.process_entry,
                    "logical_package": python_target.logical_package,
                    "logical_entrypoint": python_target.logical_entrypoint,
                }.items()
                if value is not None
            },
            "paths": sorted(paths),
        }
    )

print(json.dumps(results))
"""
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(source_root)
        if not current_pythonpath
        else f"{source_root}{os.pathsep}{current_pythonpath}"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            trace_code,
            str(source_root),
            str(repository_root),
            str(selected_schema_root),
            json.dumps(
                [
                    {
                        "skill_root": skill_root.as_posix(),
                        "python_target": _python_process_target_payload(
                            python_target
                        ),
                    }
                    for skill_root, python_target in normalized
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            str(expected_schema_version),
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=env,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        subject = (
            normalized[0][1].gateway_path.as_posix()
            if len(normalized) == 1
            else f"{len(normalized)} route-smoke specifications"
        )
        raise PythonRouteSmokeTraceError(
            f"route-smoke dependency trace failed for {subject}: {detail}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        subject = (
            normalized[0][1].gateway_path.as_posix()
            if len(normalized) == 1
            else f"{len(normalized)} route-smoke specifications"
        )
        raise PythonRouteSmokeTraceError(
            f"route-smoke dependency trace returned invalid JSON for {subject}"
        ) from exc
    expected = set(normalized)
    expected_order = list(normalized)
    traces: dict[tuple[Path, PythonProcessTarget], tuple[Path, ...]] = {}
    if not isinstance(payload, list):
        raise PythonRouteSmokeTraceError(
            "route-smoke dependency trace returned invalid batch results"
        )
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or set(item) != {
            "skill_root",
            "python_target",
            "paths",
        }:
            raise PythonRouteSmokeTraceError(
                "route-smoke dependency trace returned invalid batch results"
            )
        skill_text = item["skill_root"]
        target_payload = item["python_target"]
        path_texts = item["paths"]
        if (
            not isinstance(skill_text, str)
            or not isinstance(target_payload, dict)
            or not {"gateway_path", "process_entry"} <= set(target_payload)
            or not set(target_payload) <= {
                "gateway_path",
                "process_entry",
                "logical_package",
                "logical_entrypoint",
            }
            or not isinstance(target_payload["gateway_path"], str)
            or not isinstance(target_payload["process_entry"], str)
            or (
                target_payload.get("logical_package") is not None
                and not isinstance(target_payload["logical_package"], str)
            )
            or (
                target_payload.get("logical_entrypoint") is not None
                and not isinstance(target_payload["logical_entrypoint"], str)
            )
            or not isinstance(path_texts, list)
            or not all(isinstance(path, str) for path in path_texts)
            or path_texts != sorted(set(path_texts))
        ):
            raise PythonRouteSmokeTraceError(
                "route-smoke dependency trace returned invalid batch paths"
            )
        if index >= len(expected_order):
            raise PythonRouteSmokeTraceError(
                "route-smoke dependency trace returned invalid batch results"
            )
        expected_key = expected_order[index]
        if (
            skill_text != expected_key[0].as_posix()
            or target_payload != _python_process_target_payload(expected_key[1])
        ):
            raise PythonRouteSmokeTraceError(
                "route-smoke dependency trace returned invalid batch paths"
            )
        try:
            python_target = PythonProcessTarget(
                Path(target_payload["gateway_path"]),
                target_payload["process_entry"],
                logical_package=target_payload.get("logical_package"),
                logical_entrypoint=target_payload.get("logical_entrypoint"),
            )
        except PythonProcessTargetError as exc:
            raise PythonRouteSmokeTraceError(
                "route-smoke dependency trace returned invalid target"
            ) from exc
        key = (Path(skill_text), python_target)
        paths = tuple(Path(path) for path in path_texts)
        if (
            key not in expected
            or key in traces
            or any(
                not path.is_absolute()
                or path.as_posix() != path_text
                or path.resolve() != path
                or not path.is_file()
                or not (
                    path.is_relative_to(repository_root)
                    or path.is_relative_to(source_root / "officina")
                )
                for path, path_text in zip(paths, path_texts, strict=True)
            )
        ):
            raise PythonRouteSmokeTraceError(
                "route-smoke dependency trace returned invalid batch paths"
            )
        traces[key] = paths
    if set(traces) != expected:
        raise PythonRouteSmokeTraceError(
            "route-smoke dependency trace returned incomplete batch results"
        )
    return traces


def trace_python_route_smoke_dependencies(
    skill_dir: Path,
    repo_root: Path,
    python_target: PythonProcessTarget,
    *,
    expected_schema_version: int = 5,
    schema_root: Path | None = None,
) -> tuple[Path, ...]:
    """Return Python files loaded by one route-smoke dependency traversal."""

    repository_root = repo_root.resolve()
    normalized = _normalize_route_smoke_trace_specifications(
        repository_root,
        ((skill_dir, python_target),),
    )
    key = normalized[0]
    options = {}
    if expected_schema_version != 4:
        options["expected_schema_version"] = expected_schema_version
    if schema_root is not None:
        options["schema_root"] = schema_root
    return trace_python_route_smoke_dependencies_batch(
        repository_root,
        normalized,
        **options,
    )[key]


@dataclass(frozen=True, init=False)
class DispatchCall:
    """One declared cross-skill dispatch available to a machine interface."""

    caller_module_id: str
    target_module_id: str
    interface: str
    version: int = 1
    smoke_args: tuple[str, ...] = ("--route-smoke",)
    smoke_stdin: bool = False
    legacy_v4: bool = False

    def __init__(
        self,
        caller_module_id: str | None = None,
        target_module_id: str | None = None,
        interface: str = "",
        version: int = 1,
        smoke_args: tuple[str, ...] = ("--route-smoke",),
        smoke_stdin: bool = False,
        *,
        caller_skill: str | None = None,
        target_skill: str | None = None,
    ) -> None:
        uses_legacy_keywords = caller_skill is not None or target_skill is not None
        if uses_legacy_keywords and (
            caller_module_id is not None or target_module_id is not None
        ):
            raise TypeError(
                "canonical module IDs and legacy skill aliases cannot be mixed"
            )
        caller = (
            caller_skill if uses_legacy_keywords else caller_module_id
        )
        target = (
            target_skill if uses_legacy_keywords else target_module_id
        )
        if not isinstance(caller, str) or not caller:
            raise TypeError("caller_module_id is required")
        if not isinstance(target, str) or not target:
            raise TypeError("target_module_id is required")
        if not isinstance(interface, str) or not interface:
            raise TypeError("interface is required")
        if not uses_legacy_keywords and ".interface." in interface:
            raise ValueError(
                "canonical DispatchCall.interface must be a local interface name"
            )
        for name, value in (
            ("caller_module_id", caller),
            ("target_module_id", target),
            ("interface", interface),
            ("version", version),
            ("smoke_args", smoke_args),
            ("smoke_stdin", smoke_stdin),
            ("legacy_v4", uses_legacy_keywords),
        ):
            object.__setattr__(self, name, value)

    @property
    def caller_skill(self) -> str:
        return self.caller_module_id

    @property
    def target_skill(self) -> str:
        return self.target_module_id

    @property
    def target_interface_id(self) -> str:
        if self.legacy_v4:
            return self.interface
        return f"{self.target_module_id}.interface.{self.interface}"


@dataclass(frozen=True)
class ResolvedDispatchDependency:
    """A declared dispatch after dispatcher policy resolution."""

    key: str
    call: DispatchCall
    resolved: "ResolvedInvocationMetadata"
    depth: int


class DispatchDependencyResolver:
    """Resolve declared dispatch dependencies recursively through dispatcher policy."""

    def __init__(
        self,
        repo_root: Path | None = None,
        certification_view=None,
        graph: "RepositoryBlueprintGraph | None" = None,
    ) -> None:
        from officina.dispatcher.core import get_repo_root

        self.repo_root = get_repo_root(repo_root)
        self.certification_view = certification_view
        self.graph = graph

    def collect(
        self,
        interface: "PythonMachineInterface",
        *,
        caller_module_id: str | None = None,
        caller_source_id: str | None = None,
    ) -> list[ResolvedDispatchDependency]:
        """Return all dispatch dependencies reachable from an interface."""

        return self.collect_from_dispatches(
            interface.dispatches,
            caller_module_id=caller_module_id,
            caller_source_id=caller_source_id,
        )

    def collect_from_dispatches(
        self,
        dispatches: Mapping[str, DispatchCall],
        *,
        caller_module_id: str | None = None,
        caller_source_id: str | None = None,
    ) -> list[ResolvedDispatchDependency]:
        """Return all dispatch dependencies reachable from a declared dispatch map."""

        results: list[ResolvedDispatchDependency] = []
        visited_interfaces: set[tuple[str, str]] = set()
        self._collect(
            dispatches,
            depth=0,
            results=results,
            visited_interfaces=visited_interfaces,
            caller_module_id=caller_module_id,
            caller_source_id=caller_source_id,
        )
        return results

    def _collect(
        self,
        dispatches: Mapping[str, DispatchCall],
        *,
        depth: int,
        results: list[ResolvedDispatchDependency],
        visited_interfaces: set[tuple[str, str]],
        caller_module_id: str | None,
        caller_source_id: str | None,
    ) -> None:
        for key, call in sorted(dispatches.items()):
            resolved = self.resolve_call(
                call,
                caller_module_id=caller_module_id,
                caller_source_id=caller_source_id,
            )
            dependency = ResolvedDispatchDependency(key=key, call=call, resolved=resolved, depth=depth)
            results.append(dependency)
            identity = (resolved.target_module_id, resolved.target)
            if identity in visited_interfaces:
                continue
            visited_interfaces.add(identity)
            target_interface = self.load_resolved_python_interface(resolved)
            if target_interface is None:
                continue
            self._collect(
                target_interface.dispatches,
                depth=depth + 1,
                results=results,
                visited_interfaces=visited_interfaces,
                caller_module_id=resolved.terminal_module_id,
                caller_source_id=resolved.implementing_source_id,
            )

    def resolve_call(
        self,
        call: DispatchCall,
        *,
        caller_module_id: str | None = None,
        caller_source_id: str | None = None,
    ) -> "ResolvedInvocationMetadata":
        """Resolve one declared dispatch through the canonical dispatcher checks."""

        from officina.dispatcher.core import _resolve_dispatch_metadata_for_trace

        if caller_module_id is not None and caller_module_id != call.caller_module_id:
            raise ValueError(
                "runtime dispatch context caller module "
                f"`{caller_module_id}` does not match declared dispatch caller "
                f"`{call.caller_module_id}`"
            )
        kwargs = {
            "caller_module_id": call.caller_module_id,
            "caller_source_id": caller_source_id,
            "args": list(call.smoke_args),
            "stdin_requested": call.smoke_stdin,
            "target_version": call.version,
            "repo_root": self.repo_root,
            "certification_view": self.certification_view,
        }
        target_interface_id = call.target_interface_id
        if ".interface." not in target_interface_id:
            raise ValueError(
                "dispatch dependencies require a fully qualified "
                "`<module>.interface.<name>` target"
            )
        kwargs["target"] = target_interface_id
        if self.graph is not None:
            kwargs["graph"] = self.graph
        return _resolve_dispatch_metadata_for_trace(**kwargs)

    def load_resolved_python_interface(
        self,
        resolved: "ResolvedInvocationMetadata",
    ) -> "PythonMachineInterface | None":
        """Load a resolved runner target without reimplementing graph lookup."""

        from officina.runtime.python_machine_interface_runner import load_interface

        python_target = resolved.python_target
        if python_target is None:
            return None
        previous_cwd = Path.cwd()
        previous_sys_path = list(sys.path)
        try:
            if python_target.logical_package is None:
                module_path = str(resolved.cwd)
                sys.path[:] = [
                    entry for entry in sys.path if entry != module_path
                ]
                sys.path.insert(0, module_path)
            os.chdir(resolved.cwd)
            return load_interface(
                python_target.gateway_path,
                python_target.process_entry,
                logical_package=python_target.logical_package,
                logical_entrypoint=python_target.logical_entrypoint,
            )
        finally:
            os.chdir(previous_cwd)
            sys.path[:] = previous_sys_path

class PythonMachineInterface:
    """Base class for Python bindings of dispatcher machine interfaces.

    A skill-specific machine interface subclasses this class and implements
    parser construction plus normal execution. The shared runner owns the
    process lifecycle and route-smoke behavior, so individual skills do not
    copy that control flow.
    """

    dispatches: ClassVar[dict[str, DispatchCall]] = {}
    description: str = ""
    parser_class: type[argparse.ArgumentParser] = argparse.ArgumentParser
    formatter_class: type[argparse.HelpFormatter] | None = None
    prog: str | None = None
    usage: str | None = None
    add_help: bool = True

    def build_parser(self) -> argparse.ArgumentParser:
        """Build and return the parser for this interface.

        The base parser includes shared runtime flags. Subclasses should call
        ``super().build_parser()`` and add only interface-specific arguments.
        This method should not read credentials, contact external services,
        write files, or perform the interface's real work.
        """

        kwargs: dict[str, Any] = {
            "prog": self.prog,
            "usage": self.usage,
            "description": self.description or None,
            "add_help": self.add_help,
        }
        if self.formatter_class is not None:
            kwargs["formatter_class"] = self.formatter_class
        parser = self.parser_class(**kwargs)
        parser.add_argument(
            "--route-smoke",
            action="store_true",
            help=argparse.SUPPRESS,
        )
        return parser

    def route_smoke(self) -> None:
        """Run optional local checks for dispatcher route-smoke mode.

        Reaching this hook already proves the subprocess launched, the module
        imported, the interface object was constructed, and the parser was
        built. Override only for cheap side-effect-free checks, such as import
        aliases or local binary presence.

        Health dependency exploration uses this hook as the same-skill dynamic
        Python dependency surface. If normal execution imports same-skill Python
        modules lazily and those modules can affect behavior, route_smoke()
        should import the same modules without performing real side effects.
        Cross-skill dependencies belong in dispatches via DispatchCall, and
        non-Python file roots belong in the blueprint's directly_* fields.
        """

        return None

    def dispatch(
        self,
        key: str,
        *,
        args: Sequence[str] | None = None,
        stdin: str | bytes | None = None,
        timeout: float | None = None,
        capture_output: bool = True,
        check: bool = False,
        text: bool | None = None,
        repo_root: Path | None = None,
    ) -> Any:
        """Execute one declared dispatch by key."""

        try:
            call = self.dispatches[key]
        except KeyError as exc:
            known = ", ".join(sorted(self.dispatches)) or "none"
            raise KeyError(f"unknown dispatch key `{key}`; known keys: {known}") from exc

        target_interface_id = call.target_interface_id
        if ".interface." not in target_interface_id:
            raise ValueError(
                "dispatch dependencies require a fully qualified "
                "`<module>.interface.<name>` target"
            )
        context = runtime_dispatch_context(self)
        if (
            context.caller_module_id is not None
            and context.caller_module_id != call.caller_module_id
        ):
            raise ValueError(
                "runtime dispatch context caller module "
                f"`{context.caller_module_id}` does not match declared "
                f"dispatch caller `{call.caller_module_id}`"
            )

        from officina.dispatcher.core import (
            _resolve_dispatch,
            _run_resolved_invocation,
        )

        resolved = _resolve_dispatch(
            caller_skill=call.caller_module_id,
            target=target_interface_id,
            args=list(args or []),
            stdin_requested=stdin is not None,
            caller_source_id=None,
            repo_root=repo_root if repo_root is not None else context.repo_root,
            target_version=call.version,
            certification_view=None,
            host_caller=False,
            repository_config=context.repository_config,
        )
        return _run_resolved_invocation(
            resolved,
            stdin=stdin,
            timeout=timeout,
            capture_output=capture_output,
            check=check,
            text=text,
        )

    def parse_args(self, parser: argparse.ArgumentParser, argv: list[str]) -> Any:
        """Parse normal-mode argv before ``run``.

        Most interfaces should use the default argparse behavior. Legacy CLI
        adapters can override this to pass argv through while still sharing the
        standard route-smoke lifecycle.
        """

        return parser.parse_args(argv)

    def run(self, args: argparse.Namespace) -> int | None:
        """Execute the interface's real behavior in normal mode."""

        raise NotImplementedError


class PythonArgvMachineInterface(PythonMachineInterface):
    """Adapter base for existing Python CLIs that already own argv parsing.

    Subclasses implement ``run(argv)`` and delegate to the existing CLI entry
    point. This is a migration bridge: route-smoke still imports the module,
    constructs the interface object, and builds the shared parser, while normal
    execution preserves the script's current parser behavior.
    """

    def parse_args(self, parser: argparse.ArgumentParser, argv: list[str]) -> list[str]:
        """Return argv unchanged for an interface-owned CLI parser."""

        return argv


def coerce_exit_code(value: Any) -> int:
    """Convert a machine-interface return value into a process exit code."""

    if value is None:
        return 0
    if isinstance(value, bool):
        return 0 if value else 1
    if isinstance(value, int):
        return value
    raise TypeError(f"machine interface returned unsupported value {value!r}")
