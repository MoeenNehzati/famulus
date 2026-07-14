"""Base contract for Python implementations of machine interfaces."""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Mapping, Sequence

if TYPE_CHECKING:
    from officina.dispatcher import ResolvedInvocationMetadata


@dataclass(frozen=True)
class DispatchCall:
    """One declared cross-skill dispatch available to a machine interface."""

    caller_skill: str
    target_skill: str
    interface: str
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

    def __init__(self, repo_root: Path | None = None) -> None:
        from officina.dispatcher.core import get_repo_root

        self.repo_root = get_repo_root(repo_root)

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
            identity = (resolved.target_skill, resolved.script_interface)
            if identity in visited_interfaces:
                continue
            visited_interfaces.add(identity)
            target_interface = self.load_python_interface(resolved.target_skill, resolved.script_interface)
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

        return resolve_dispatch_metadata(
            caller_skill=call.caller_skill,
            target_skill=call.target_skill,
            script_interface=call.interface,
            args=list(call.smoke_args),
            stdin_requested=call.smoke_stdin,
            repo_root=self.repo_root,
        )

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

        return dispatch(
            caller_skill=call.caller_skill,
            target_skill=call.target_skill,
            script_interface=call.interface,
            args=list(args or []),
            stdin=stdin,
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
