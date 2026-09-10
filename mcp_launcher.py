"""Launch the Famulus MCP server with its dedicated interpreter."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / "requirements-mcp.txt"
BOOTSTRAP_CLUE = "Use the `bootstrap-dispatcher-runtime` skill's core setup route."
LINUX_RUNTIME_ROOT = Path("/run/user")


def _hydrate_linux_session_environment(
    environment: dict[str, str],
    *,
    platform: str = sys.platform,
    uid: int | None = None,
    runtime_root: Path | None = None,
) -> None:
    if (
        platform != "linux"
        or "XDG_RUNTIME_DIR" in environment
        or "DBUS_SESSION_BUS_ADDRESS" in environment
    ):
        return
    selected_uid = os.getuid() if uid is None else uid
    runtime = (LINUX_RUNTIME_ROOT if runtime_root is None else runtime_root) / str(
        selected_uid
    )
    bus = runtime / "bus"
    try:
        runtime_status = runtime.lstat()
        bus_status = bus.lstat()
    except OSError:
        return
    if (
        not stat.S_ISDIR(runtime_status.st_mode)
        or stat.S_IMODE(runtime_status.st_mode) != 0o700
        or runtime_status.st_uid != selected_uid
        or not stat.S_ISSOCK(bus_status.st_mode)
        or bus_status.st_uid != selected_uid
    ):
        return
    environment["XDG_RUNTIME_DIR"] = str(runtime)
    environment["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"


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
        _hydrate_linux_session_environment(environment)
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
