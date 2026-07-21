"""Base contract for Python implementations of machine interfaces."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Mapping, Sequence

if TYPE_CHECKING:
    from officina.dispatcher import ResolvedInvocationMetadata


class PythonRouteSmokeTraceError(RuntimeError):
    """Raised when a Python route-smoke dependency trace cannot complete."""


def trace_python_route_smoke_dependencies(
    skill_dir: Path,
    repo_root: Path,
    entrypoint: str,
) -> tuple[Path, ...]:
    """Return Python files loaded by one route-smoke dependency traversal."""

    skill_root = skill_dir.resolve()
    repository_root = repo_root.resolve()
    source_root = Path(__file__).resolve().parents[2]
    trace_code = r"""
import contextlib
import io
import json
import os
import sys
from pathlib import Path

skill_dir = Path(sys.argv[1]).resolve()
src_root = Path(sys.argv[2]).resolve()
entrypoint = sys.argv[3]
repo_root = Path(sys.argv[4]).resolve()
officina_root = src_root / "officina"
skills_root = repo_root / "skills"
sys.path.insert(0, str(src_root))

from officina.runtime.python_machine_interface import DispatchDependencyResolver
from officina.runtime.python_machine_interface_runner import load_interface, run_python_machine_interface

def is_under(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True

paths = []

def collect_loaded_paths():
    for module in sys.modules.values():
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        path = Path(module_file).resolve()
        if path.suffix not in {".py", ".pyi"}:
            continue
        if is_under(path, repo_root) or is_under(path, officina_root):
            paths.append(path.as_posix())

os.chdir(skill_dir)
with contextlib.redirect_stdout(io.StringIO()):
    interface = load_interface(entrypoint)
    run_python_machine_interface(interface, ["--route-smoke"])
collect_loaded_paths()

from officina.common.certification_view import CertificationDecision

class TraceCertificationView:
    def check_export(self, module_id, interface_id, interface_version):
        return CertificationDecision(True, "route-smoke-trace", "Trace-only certification view.")

    def certificate_for(self, node_id):
        return None

resolver = DispatchDependencyResolver(
    repo_root=repo_root,
    certification_view=TraceCertificationView(),
)
with contextlib.redirect_stdout(io.StringIO()):
    dependencies = resolver.collect(interface)
    for dependency in dependencies:
        invocation = dependency.resolved
        for token in invocation.command:
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = invocation.cwd / candidate
            candidate = candidate.resolve()
            if candidate.exists() and is_under(candidate, skills_root):
                paths.append(candidate.as_posix())
        target_interface = resolver.load_resolved_python_interface(invocation)
        if target_interface is not None:
            previous_cwd = Path.cwd()
            try:
                os.chdir(invocation.cwd)
                run_python_machine_interface(target_interface, ["--route-smoke"])
            finally:
                os.chdir(previous_cwd)
            collect_loaded_paths()

print(json.dumps(sorted(set(paths))))
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
            str(skill_root),
            str(source_root),
            entrypoint,
            str(repository_root),
        ],
        cwd=skill_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=env,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PythonRouteSmokeTraceError(
            f"route-smoke dependency trace failed for {entrypoint}: {detail}"
        )
    try:
        paths = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PythonRouteSmokeTraceError(
            f"route-smoke dependency trace returned invalid JSON for {entrypoint}"
        ) from exc
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise PythonRouteSmokeTraceError(
            f"route-smoke dependency trace returned invalid paths for {entrypoint}"
        )
    return tuple(Path(path) for path in paths)


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

    def __init__(self, repo_root: Path | None = None, certification_view=None) -> None:
        from officina.dispatcher.core import get_repo_root

        self.repo_root = get_repo_root(repo_root)
        self.certification_view = certification_view

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

        from officina.dispatcher import resolve_dispatch_metadata

        kwargs = {
            "caller_skill": call.caller_skill,
            "args": list(call.smoke_args),
            "stdin_requested": call.smoke_stdin,
            "target_version": call.version,
            "repo_root": self.repo_root,
            "certification_view": self.certification_view,
        }
        if ".interface." in call.interface:
            kwargs["target"] = call.interface
        else:
            kwargs["target_skill"] = call.target_skill
            kwargs["script_interface"] = call.interface
        return resolve_dispatch_metadata(**kwargs)

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

    def load_python_interface(
        self,
        skill_name: str,
        interface_name: str,
    ) -> "PythonMachineInterface | None":
        """Load a target PythonMachineInterface, or return None for other runtimes."""

        from officina.dispatcher.core import expect_mapping, load_blueprint, resolve_machine_interface_surface
        from officina.runtime.python_machine_interface_runner import load_interface

        blueprint = load_blueprint(skill_name, repo_root=self.repo_root)
        interface_spec, _resolved_name = resolve_machine_interface_surface(blueprint, interface_name)
        invocation = expect_mapping(interface_spec.get("invocation"), "invocation")
        if invocation.get("kind") != "python_machine_interface":
            return None
        entrypoint = invocation.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            return None

        skill_dir = self.repo_root / "skills" / skill_name
        previous_cwd = Path.cwd()
        try:
            for cached_name in list(sys.modules):
                if cached_name == "_rtx" or cached_name.startswith("_rtx."):
                    del sys.modules[cached_name]

            skill_path = str(skill_dir)
            sys.path[:] = [entry for entry in sys.path if entry != skill_path]
            sys.path.insert(0, skill_path)
            os.chdir(skill_dir)
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

        target_kwargs = (
            {"target": call.interface}
            if ".interface." in call.interface
            else {
                "target_skill": call.target_skill,
                "script_interface": call.interface,
            }
        )
        return dispatch(
            caller_skill=call.caller_skill,
            **target_kwargs,
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
        """Return argv unchanged so the legacy CLI parser can handle it."""

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
