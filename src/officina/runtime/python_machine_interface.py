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


@dataclass(frozen=True)
class DispatchCallDeclaration:
    """One alias-resolved DispatchCall declaration in a Python syntax tree."""

    caller_skill: str | None
    target_skill: str | None
    interface: str | None
    lineno: int
    keywords: Mapping[str, ast.Constant]


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
                caller_skill=resolved(values.get("caller_skill")),
                target_skill=resolved(values.get("target_skill")),
                interface=resolved(values.get("interface")),
                lineno=getattr(node, "lineno", 0),
                keywords=keywords,
            )
        )
    return tuple(declarations)


def _normalize_route_smoke_trace_specifications(
    repo_root: Path,
    specifications: Iterable[tuple[Path, str]],
) -> tuple[tuple[Path, str], ...]:
    """Validate and canonicalize route-smoke trace specifications."""

    repository_root = repo_root.resolve()
    normalized: set[tuple[Path, str]] = set()
    for specification in specifications:
        if not isinstance(specification, tuple) or len(specification) != 2:
            raise ValueError(
                "route-smoke trace specifications must be "
                "(skill_root, entrypoint) pairs"
            )
        skill_dir, entrypoint = specification
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
        if not isinstance(entrypoint, str) or entrypoint != entrypoint.strip():
            raise ValueError("route-smoke entrypoints must be non-empty strings")
        module_text, separator, class_name = entrypoint.partition(":")
        module_path = Path(module_text)
        if (
            separator != ":"
            or not module_text
            or not class_name
            or not class_name.isidentifier()
            or module_path.is_absolute()
            or ".." in module_path.parts
            or not module_path.parts
            or module_path.parts[0] != "_rtx"
            or module_path.suffix != ".py"
        ):
            raise ValueError(
                "route-smoke entrypoints must be "
                "`_rtx/path.py:ClassName` without parent traversal"
            )
        normalized_module = Path(*module_path.parts).as_posix()
        if not (skill_root / normalized_module).is_file():
            raise ValueError(
                f"route-smoke entrypoint does not exist: "
                f"{skill_root / normalized_module}"
            )
        normalized.add((skill_root, f"{normalized_module}:{class_name}"))
    return tuple(sorted(normalized, key=lambda item: (item[0].as_posix(), item[1])))


def trace_python_route_smoke_dependencies_batch(
    repo_root: Path,
    specifications: Iterable[tuple[Path, str]],
) -> dict[tuple[Path, str], tuple[Path, ...]]:
    """Return isolated loaded-path traces from one Python child process."""

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
    candidate_schema_root = repository_root / "references" / "blueprint"
    schema_root = (
        candidate_schema_root
        if (candidate_schema_root / "module.schema.json").is_file()
        else Path(__file__).resolve().parents[3] / "references" / "blueprint"
    )
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
specifications = [
    (Path(skill_root).resolve(), entrypoint)
    for skill_root, entrypoint in json.loads(sys.argv[4])
]
officina_root = src_root / "officina"
skills_root = repo_root / "skills"
sys.path.insert(0, str(src_root))

from officina.common.blueprint_graph import (
    BlueprintGraphError,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.common.blueprint_inventory import iter_blueprints
from officina.runtime.python_machine_interface import DispatchDependencyResolver
from officina.runtime.python_machine_interface_runner import load_interface, run_python_machine_interface

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

from officina.common.certification_view import CertificationDecision

class TraceCertificationView:
    def check_export(self, module_id, interface_id, interface_version, source_node_id):
        return CertificationDecision(True, "route-smoke-trace", "Trace-only certification view.")

    def certificate_for(self, node_id):
        return None

try:
    graph = load_repository_blueprint_graph(repo_root, schema_root=schema_root)
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
for skill_dir, entrypoint in specifications:
    os.chdir(base_cwd)
    sys.path[:] = base_sys_path
    restore_module_baseline()
    before = module_snapshot()
    paths = set(basis_paths)
    try:
        os.chdir(skill_dir)
        with contextlib.redirect_stdout(io.StringIO()):
            interface = load_interface(entrypoint)
            run_python_machine_interface(interface, ["--route-smoke"])
            collect_loaded_paths(paths, before)
            dependencies = resolver.collect(interface)
            collect_loaded_paths(paths, before)
            for dependency in dependencies:
                invocation = dependency.resolved
                for token in invocation.command:
                    candidate = Path(token)
                    if not candidate.is_absolute():
                        candidate = invocation.cwd / candidate
                    candidate = candidate.resolve()
                    if candidate.exists() and is_under(candidate, skills_root):
                        paths.add(candidate.as_posix())
                target_interface = resolver.load_resolved_python_interface(invocation)
                if target_interface is not None:
                    previous_cwd = Path.cwd()
                    try:
                        os.chdir(invocation.cwd)
                        run_python_machine_interface(
                            target_interface,
                            ["--route-smoke"],
                        )
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
            "entrypoint": entrypoint,
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
            str(schema_root),
            json.dumps(
                [
                    [skill_root.as_posix(), entrypoint]
                    for skill_root, entrypoint in normalized
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
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
            normalized[0][1]
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
            normalized[0][1]
            if len(normalized) == 1
            else f"{len(normalized)} route-smoke specifications"
        )
        raise PythonRouteSmokeTraceError(
            f"route-smoke dependency trace returned invalid JSON for {subject}"
        ) from exc
    expected = set(normalized)
    expected_order = list(normalized)
    traces: dict[tuple[Path, str], tuple[Path, ...]] = {}
    if not isinstance(payload, list):
        raise PythonRouteSmokeTraceError(
            "route-smoke dependency trace returned invalid batch results"
        )
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or set(item) != {
            "skill_root",
            "entrypoint",
            "paths",
        }:
            raise PythonRouteSmokeTraceError(
                "route-smoke dependency trace returned invalid batch results"
            )
        skill_text = item["skill_root"]
        entrypoint = item["entrypoint"]
        path_texts = item["paths"]
        if (
            not isinstance(skill_text, str)
            or not isinstance(entrypoint, str)
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
            or entrypoint != expected_key[1]
        ):
            raise PythonRouteSmokeTraceError(
                "route-smoke dependency trace returned invalid batch paths"
            )
        key = (Path(skill_text), entrypoint)
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
    entrypoint: str,
) -> tuple[Path, ...]:
    """Return Python files loaded by one route-smoke dependency traversal."""

    repository_root = repo_root.resolve()
    normalized = _normalize_route_smoke_trace_specifications(
        repository_root,
        ((skill_dir, entrypoint),),
    )
    key = normalized[0]
    return trace_python_route_smoke_dependencies_batch(
        repository_root,
        normalized,
    )[key]


@dataclass(frozen=True)
class DispatchCall:
    """One declared cross-skill dispatch available to a machine interface."""

    caller_skill: str
    target_skill: str
    interface: str
    version: int = 1
    smoke_args: tuple[str, ...] = ("--route-smoke",)
    smoke_stdin: bool = False


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

    def collect(self, interface: "PythonMachineInterface") -> list[ResolvedDispatchDependency]:
        """Return all dispatch dependencies reachable from an interface."""

        return self.collect_from_dispatches(interface.dispatches)

    def collect_from_dispatches(
        self,
        dispatches: Mapping[str, DispatchCall],
    ) -> list[ResolvedDispatchDependency]:
        """Return all dispatch dependencies reachable from a declared dispatch map."""

        results: list[ResolvedDispatchDependency] = []
        visited_interfaces: set[tuple[str, str]] = set()
        self._collect(
            dispatches,
            depth=0,
            results=results,
            visited_interfaces=visited_interfaces,
        )
        return results

    def _collect(
        self,
        dispatches: Mapping[str, DispatchCall],
        *,
        depth: int,
        results: list[ResolvedDispatchDependency],
        visited_interfaces: set[tuple[str, str]],
    ) -> None:
        for key, call in sorted(dispatches.items()):
            resolved = self.resolve_call(call)
            dependency = ResolvedDispatchDependency(key=key, call=call, resolved=resolved, depth=depth)
            results.append(dependency)
            identity = (resolved.target_skill, resolved.target)
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
            )

    def resolve_call(self, call: DispatchCall) -> "ResolvedInvocationMetadata":
        """Resolve one declared dispatch through the canonical dispatcher checks."""

        from officina.dispatcher.core import _resolve_dispatch_metadata_for_trace

        kwargs = {
            "caller_skill": call.caller_skill,
            "args": list(call.smoke_args),
            "stdin_requested": call.smoke_stdin,
            "target_version": call.version,
            "repo_root": self.repo_root,
            "certification_view": self.certification_view,
        }
        if ".interface." not in call.interface:
            raise ValueError(
                "dispatch dependencies require a fully qualified "
                "`<module>.interface.<name>` target"
            )
        kwargs["target"] = call.interface
        if self.graph is not None:
            kwargs["graph"] = self.graph
        return _resolve_dispatch_metadata_for_trace(**kwargs)

    def load_resolved_python_interface(
        self,
        resolved: "ResolvedInvocationMetadata",
    ) -> "PythonMachineInterface | None":
        """Load a resolved runner target without reimplementing graph lookup."""

        from officina.runtime.python_machine_interface_runner import load_interface

        command = resolved.command
        if (
            len(command) < 4
            or command[1:3]
            != ["-m", "officina.runtime.python_machine_interface_runner"]
        ):
            return None
        entrypoint = command[3]
        previous_cwd = Path.cwd()
        try:
            for cached_name in list(sys.modules):
                if cached_name == "_rtx" or cached_name.startswith("_rtx."):
                    del sys.modules[cached_name]
            skill_path = str(resolved.cwd)
            sys.path[:] = [entry for entry in sys.path if entry != skill_path]
            sys.path.insert(0, skill_path)
            os.chdir(resolved.cwd)
            return load_interface(entrypoint)
        finally:
            os.chdir(previous_cwd)

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

        from officina.dispatcher import dispatch

        if ".interface." not in call.interface:
            raise ValueError(
                "dispatch dependencies require a fully qualified "
                "`<module>.interface.<name>` target"
            )
        return dispatch(
            caller_skill=call.caller_skill,
            target=call.interface,
            args=list(args or []),
            stdin=stdin,
            target_version=call.version,
            timeout=timeout,
            capture_output=capture_output,
            check=check,
            text=text,
            repo_root=repo_root,
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
