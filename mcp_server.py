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
    from mcp.server.fastmcp import FastMCP
    server = FastMCP(CONTRACT["server"])
    server.tool()(invoke)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
