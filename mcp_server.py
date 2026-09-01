"""Shared stdio MCP adapter for the existing Famulus Dispatcher."""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parent
CONTRACT = json.loads((ROOT / "mcp-core.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(ROOT / "src"))
os.environ["PYTHONPATH"] = str(ROOT / "src")
from officina.dispatcher.direct_runtime import _run_resolved_invocation, resolve_dispatch
from officina.dispatcher.errors import InvocationError
from officina.common.atomic_files import atomic_replace_bytes
from officina.common.famulus_paths import resolve_famulus_paths


@dataclass
class CompactArguments:
    positionals: list[str]
    options: dict[str, str | Literal[True]]
    stdin: str | None


@dataclass
class OrderedArguments:
    positionals: tuple[()]
    options: list[str]
    stdin: str | None


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    dispatcher: dict[str, Any]


def require_python(version: tuple[int, int] = sys.version_info[:2]) -> None:
    if version < (3, 11):
        raise RuntimeError("Famulus MCP requires python >=3.11")


def _confined_directory(root: Path, child: Path) -> None:
    """Create one plugin-data child only when it remains confined.

    Intent
    ------
    Reject aliases and non-directories before startup publishes their paths.
    Rationale
    ---------
    Explicit post-creation checks keep writes inside the client-owned data root.
    Pseudocode
    ----------
    - set child = created directory or existing filesystem entry
    - if child is unsafe:
      - raise runtime error
    Wraps
    -----
    - none
    """
    try:
        child.mkdir()
    except FileExistsError:
        pass
    if child.is_symlink() or not child.is_dir() or not child.resolve().is_relative_to(root.resolve()):
        raise RuntimeError(f"unsafe plugin-data directory: {child}")


def configure_plugin_persistence() -> None:
    """Project client-owned plugin data into this MCP subprocess.

    Intent
    ------
    Prepare private status and milestone paths from explicit plugin provenance.
    Rationale
    ---------
    Startup must complete confined atomic state creation before exporting logs.
    Pseudocode
    ----------
    - persistence_paths = resolve_famulus_paths(platform, home, environment)
    - @_confined_directory(plugin_root, logging_root)
    - @atomic_replace_bytes(status_file, status_bytes)
    - set ASSISTANT_LOGS = logging root
    Wraps
    -----
    - none
    CallsFromRepo
    -------------
    ._confined_directory:
      why:
        validates: "Rejects unsafe logging and setup parents before state publication."
    .officina.common.atomic_files.atomic_replace_bytes:
      why:
        writes: "Publishes deterministic private readiness bytes beneath the validated plugin root."
    InstantiationsFromRepo
    ----------------------
    .officina.common.famulus_paths.resolve_famulus_paths:
      why:
        constructs: "Builds the explicit host-scoped paths consumed by persistence startup."
    """
    if "FAMULUS_HOST" not in os.environ and "FAMULUS_PLUGIN_DATA" not in os.environ:
        return
    paths = resolve_famulus_paths(platform=sys.platform, home=Path.home(), environ=os.environ)
    assert paths.plugin_data and paths.assistant_host and paths.logging_path and paths.setup_status
    paths.plugin_data.mkdir(parents=True, exist_ok=True)
    if paths.plugin_data.is_symlink() or not paths.plugin_data.is_dir():
        raise RuntimeError(f"unsafe plugin-data directory: {paths.plugin_data}")
    _confined_directory(paths.plugin_data, paths.logging_path)
    _confined_directory(paths.plugin_data, paths.setup_status.parent)
    status = (json.dumps({"host": paths.assistant_host, "schema_version": 1, "status": "ready"}, sort_keys=True, separators=(",", ":")) + "\n").encode()
    atomic_replace_bytes(
        paths.setup_status, status, allowed_root=paths.plugin_data, mode=0o600
    )
    os.environ["ASSISTANT_LOGS"] = str(paths.logging_path)


def caller_argv(arguments: CompactArguments | OrderedArguments) -> list[str]:
    options = arguments.options
    if isinstance(options, list):
        if arguments.positionals:
            raise ValueError("ordered options require empty positionals")
        return options
    argv = list(arguments.positionals)
    for name, value in options.items():
        if isinstance(value, list):
            raise ValueError("compact options cannot contain lists; use ordered options")
        argv.append(name)
        if value is not True:
            argv.append(value)
    return argv


def invoke(caller: str, interface: str, version: int, arguments: CompactArguments | OrderedArguments, dry_run: bool = False) -> dict[str, Any] | ExecutionResult:
    """Invoke one authorized Famulus interface through the existing Dispatcher."""
    try:
        with resolve_dispatch(caller_skill=caller, target=interface, target_version=version, args=caller_argv(arguments), stdin_requested=arguments.stdin is not None, repository_config=ROOT / "officina.toml") as resolved:
            dispatcher = resolved.metadata().as_payload()
            if dry_run:
                return dispatcher
            result = _run_resolved_invocation(resolved, stdin=arguments.stdin, capture_output=True, text=True)
            return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "dispatcher": dispatcher}
    except InvocationError as error:
        payload = error.as_payload() if hasattr(error, "as_payload") else {"message": str(error)}
        return {"exit_code": 2, "stdout": "", "stderr": "", "dispatcher": payload}


def main() -> None:
    require_python()
    configure_plugin_persistence()
    from mcp.server.fastmcp import FastMCP
    server = FastMCP(CONTRACT["server"])
    server.tool()(invoke)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
