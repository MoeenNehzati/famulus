"""Launch the Famulus MCP server with its dedicated interpreter."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / "requirements-mcp.txt"
BOOTSTRAP_CLUE = "Use the `bootstrap-dispatcher-runtime` skill's core setup route."


def _format_error(
    message: str, *, cause: str | None = None, clues: tuple[str, ...] = ()
) -> str:
    lines = [f"error: {message}"]
    if cause:
        detail = " | ".join(line.strip() for line in cause.splitlines() if line.strip())
        if detail:
            lines.append(f"Cause: {detail}")
    if clues:
        lines.append("Possible clues:")
        lines.extend(f"- {clue}" for clue in clues)
    return "\n".join(lines) + "\n"


def _render_error(
    message: str, *, cause: str | None = None, clues: tuple[str, ...] = ()
) -> None:
    print(_format_error(message, cause=cause, clues=clues), end="", file=sys.stderr)


def diagnose_runtime(python: Path, environment: dict[str, str]) -> str | None:
    if not python.is_file():
        return _format_error(
            "Famulus MCP startup's dedicated dispatcher "
            f"runtime is missing at {python}.",
            clues=(BOOTSTRAP_CLUE,),
        )

    result = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--quiet",
            "--report",
            "-",
            "--disable-pip-version-check",
            "--no-input",
            "--no-cache-dir",
            "--no-index",
            "-r",
            str(REQUIREMENTS),
        ],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return None

    detail = (result.stderr or result.stdout).strip()
    return _format_error(
        "Famulus MCP startup's dedicated dispatcher "
        f"runtime does not satisfy {REQUIREMENTS}.",
        cause=detail or None,
        clues=(BOOTSTRAP_CLUE,),
    )


def _check_runtime(python: Path, environment: dict[str, str]) -> bool:
    diagnosis = diagnose_runtime(python, environment)
    if diagnosis is None:
        return True
    print(diagnosis, end="", file=sys.stderr)
    return False


def main() -> int:
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from officina.common.famulus_paths import resolve_famulus_paths

        environment = os.environ.copy()
        paths = resolve_famulus_paths(
            platform=sys.platform, home=Path.home(), environ=environment
        )
        if not _check_runtime(paths.venv_python_path, environment):
            return 1
        return subprocess.run(
            [str(paths.venv_python_path), str(ROOT / "mcp_server.py")],
            env=environment,
        ).returncode
    except Exception as exc:
        _render_error(
            "Famulus MCP startup failed before the server became available.",
            cause=f"{type(exc).__name__}: {exc}",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
