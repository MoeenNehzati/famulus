"""The shared MCP transport preserves the direct Dispatcher boundary."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import venv
from urllib.parse import quote
from urllib.request import urlopen

import pytest

from officina.common.famulus_paths import resolve_famulus_paths
from officina.common import atomic_files


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp_server.py"
LAUNCHER = ROOT / "mcp_launcher.py"
CORE = ROOT / "mcp-core.json"
REQUIREMENTS = ROOT / "requirements-mcp.txt"
COMPREHENSION_FIXTURE = ROOT / "tests" / "fixtures" / "famulus_comprehension_payloads.json"
# Real stdio cases finish in about 6s sequentially and at most 10.71s in an
# isolated -n8 run. Full-hook worker contention can exceed 15s while the MCP
# server is still progressing, so this remains a bounded capacity allowance,
# not a substitute for detecting a hung session.
REAL_MCP_INTEGRATION_TIMEOUT_SECONDS = 30
# Persistent recording performs MCP initialization plus a Dispatcher call. A
# focused pair finishes well below this bound, while full-hook contention can
# legitimately exceed the shorter single-probe allowance above.
REAL_MCP_PERSISTENCE_LIFECYCLE_TIMEOUT_SECONDS = 90


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_server(path: Path = SERVER):
    spec = importlib.util.spec_from_file_location("famulus_mcp_server", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _launcher_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    if sys.platform == "win32":
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    elif sys.platform != "darwin":
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    paths = resolve_famulus_paths(platform=sys.platform, home=home, environ=os.environ)
    return paths


def _create_runtime_interpreter(paths) -> None:
    paths.venv_python_path.parent.mkdir(parents=True)
    paths.venv_python_path.touch()


def _create_linux_runtime_bus(root: Path, uid: int):
    runtime = root / str(uid)
    runtime.mkdir(parents=True)
    runtime.chmod(0o700)
    bus = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bus.bind(str(runtime / "bus"))
    return runtime, bus


@pytest.mark.parametrize(
    ("platform", "environment"),
    [
        ("darwin", {"PRESERVED": "yes"}),
        ("linux", {"XDG_RUNTIME_DIR": "/explicit/runtime"}),
        ("linux", {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/explicit/bus"}),
        (
            "linux",
            {
                "XDG_RUNTIME_DIR": "/explicit/runtime",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/explicit/bus",
            },
        ),
    ],
)
def test_launcher_preserves_explicit_partial_or_non_linux_session_environment(
    platform: str, environment: dict[str, str], tmp_path: Path
) -> None:
    launcher = _load_server(LAUNCHER)
    original = environment.copy()
    launcher._hydrate_linux_session_environment(
        environment, platform=platform, uid=1234, runtime_root=tmp_path
    )
    assert environment == original


# famulus-skip: category=unsupported-platform; reason=Linux endpoint metadata; alternate=non-Linux no-op case
@pytest.mark.skipif(sys.platform != "linux", reason="Linux session repair")
@pytest.mark.parametrize(
    "unsafe_case",
    "missing_bus open_mode regular_bus runtime_symlink bus_symlink runtime_owner bus_owner".split(),
)
def test_launcher_rejects_unsafe_linux_session_endpoints(
    unsafe_case: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = _load_server(LAUNCHER)
    uid = os.getuid()
    runtime = tmp_path / str(uid)
    sockets: list[socket.socket] = []
    if unsafe_case == "runtime_symlink":
        target, bus = _create_linux_runtime_bus(tmp_path / "target", uid)
        sockets.append(bus)
        runtime.symlink_to(target, target_is_directory=True)
    else:
        runtime.mkdir(parents=True)
        runtime.chmod(0o755 if unsafe_case == "open_mode" else 0o700)
        if unsafe_case == "regular_bus":
            (runtime / "bus").write_text("not a socket", encoding="utf-8")
        elif unsafe_case == "bus_symlink":
            target = tmp_path / "socket-target"
            bus = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            bus.bind(str(target))
            sockets.append(bus)
            (runtime / "bus").symlink_to(target)
        elif unsafe_case != "missing_bus":
            bus = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            bus.bind(str(runtime / "bus"))
            sockets.append(bus)
    if unsafe_case in {"runtime_owner", "bus_owner"}:
        real_lstat = launcher.Path.lstat
        wrong_name = "bus" if unsafe_case == "bus_owner" else str(uid)

        def lstat(path):
            status = real_lstat(path)
            if path.name == wrong_name:
                return os.stat_result((*status[:4], uid + 1, *status[5:]))
            return status

        monkeypatch.setattr(launcher.Path, "lstat", lstat)
    environment: dict[str, str] = {}
    try:
        launcher._hydrate_linux_session_environment(
            environment, platform="linux", uid=uid, runtime_root=tmp_path
        )
    finally:
        for bus in sockets:
            bus.close()
    assert environment == {}


# famulus-skip: category=unsupported-platform; reason=Linux process environment; alternate=helper no-op case
@pytest.mark.skipif(sys.platform != "linux", reason="Linux session repair")
def test_launcher_passes_hydrated_environment_to_both_subprocesses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = _load_server(LAUNCHER)
    paths = _launcher_paths(monkeypatch, tmp_path)
    _create_runtime_interpreter(paths)
    runtime_root = tmp_path / "run-user"
    runtime, bus = _create_linux_runtime_bus(runtime_root, os.getuid())
    monkeypatch.setattr(launcher, "LINUX_RUNTIME_ROOT", runtime_root)
    monkeypatch.setenv("PRESERVED", "yes")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    environments: list[dict[str, str]] = []

    def run(argv, *, env, **_kwargs):
        environments.append(env)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    try:
        assert launcher.main() == 0
    finally:
        bus.close()
    expected = {
        "PRESERVED": "yes",
        "XDG_RUNTIME_DIR": str(runtime),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime / 'bus'}",
    }
    assert [{key: env[key] for key in expected} for env in environments] == [
        expected,
        expected,
    ]
    assert environments[0] is environments[1]


def test_launcher_uses_exact_executable_inherits_stdio_and_propagates_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = _load_server(LAUNCHER)
    paths = _launcher_paths(monkeypatch, tmp_path)
    _create_runtime_interpreter(paths)
    called: list[tuple[list[str], dict[str, object]]] = []

    def run(argv, **kwargs):
        called.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0 if len(called) == 1 else 17, "", "")

    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        run,
    )

    assert launcher.main() == 17
    assert called[0][0][0] == str(paths.venv_python_path)
    assert called[0][0][-2:] == ["-r", str(REQUIREMENTS)]
    assert called[0][1]["stdin"] is subprocess.DEVNULL
    assert called[1][0] == [str(paths.venv_python_path), str(ROOT / "mcp_server.py")]


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_launcher_uses_json_normalized_plugin_data(
    host: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launcher = _load_server(LAUNCHER)
    paths = _launcher_paths(monkeypatch, tmp_path)
    _create_runtime_interpreter(paths)
    plugin_data = tmp_path / "plugin-data"
    monkeypatch.setenv("FAMULUS_HOST", host)
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "wrong-codex-data"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "wrong-claude-data"))
    normalized: list[str | None] = []

    def run(argv, *, env, **_kwargs):
        normalized.append(env.get("FAMULUS_PLUGIN_DATA"))
        return subprocess.CompletedProcess(argv, 0 if len(normalized) == 1 else 17)

    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        run,
    )

    assert launcher.main() == 17
    assert normalized == [str(plugin_data), str(plugin_data)]


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_launcher_reports_missing_json_normalized_plugin_data(
    host: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    tmp_path: Path,
) -> None:
    launcher = _load_server(LAUNCHER)
    _launcher_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("FAMULUS_HOST", host)
    monkeypatch.delenv("FAMULUS_PLUGIN_DATA", raising=False)

    assert launcher.main() == 1
    assert capsys.readouterr().err == (
        "error: Famulus MCP startup failed before the server became available.\n"
        "Cause: InvalidFamulusPluginContextError: plugin host and data must be supplied together\n"
    )


def test_launcher_reports_missing_runtime_and_bootstrap_route(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    launcher = _load_server(LAUNCHER)
    paths = _launcher_paths(monkeypatch, tmp_path)

    assert launcher.main() == 1
    assert capsys.readouterr().err == (
        "error: Famulus MCP startup's dedicated dispatcher runtime "
        f"is missing at {paths.venv_python_path}.\n"
        "Possible clues:\n"
        "- Use the `bootstrap-dispatcher-runtime` skill's core setup route.\n"
    )


def test_runtime_diagnosis_is_reusable_without_writing_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    """Break caught: runtime health can only be consumed through launcher stderr."""
    launcher = _load_server(LAUNCHER)
    paths = _launcher_paths(monkeypatch, tmp_path)

    diagnosis = launcher.diagnose_runtime(
        paths.venv_python_path, os.environ.copy()
    )

    assert diagnosis == (
        "error: Famulus MCP startup's dedicated dispatcher runtime "
        f"is missing at {paths.venv_python_path}.\n"
        "Possible clues:\n"
        "- Use the `bootstrap-dispatcher-runtime` skill's core setup route.\n"
    )
    assert capsys.readouterr().err == ""


def test_launcher_reports_unsatisfied_requirements_before_starting_server(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    launcher = _load_server(LAUNCHER)
    paths = _launcher_paths(monkeypatch, tmp_path)
    _create_runtime_interpreter(paths)
    called: list[list[str]] = []

    def run(argv, **_kwargs):
        called.append(argv)
        return subprocess.CompletedProcess(
            argv,
            1,
            "",
            "ERROR: No matching distribution found for jsonschema<5,>=4\n",
        )

    monkeypatch.setattr(launcher.subprocess, "run", run)

    assert launcher.main() == 1
    assert len(called) == 1
    assert called[0][-2:] == ["-r", str(REQUIREMENTS)]
    assert capsys.readouterr().err == (
        "error: Famulus MCP startup's dedicated dispatcher "
        f"runtime does not satisfy {REQUIREMENTS}.\n"
        "Cause: ERROR: No matching distribution found for jsonschema<5,>=4\n"
        "Possible clues:\n"
        "- Use the `bootstrap-dispatcher-runtime` skill's core setup route.\n"
    )


def test_launcher_formats_unexpected_startup_failure(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    launcher = _load_server(LAUNCHER)
    paths = _launcher_paths(monkeypatch, tmp_path)
    _create_runtime_interpreter(paths)
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("cannot execute dedicated runtime")
        ),
    )

    assert launcher.main() == 1
    assert capsys.readouterr().err == (
        "error: Famulus MCP startup failed before the server became available.\n"
        "Cause: PermissionError: cannot execute dedicated runtime\n"
    )


@pytest.fixture(scope="module")
def server():
    """Load the immutable in-process MCP module once per isolation domain."""
    return _load_server()


def test_flow_lease_is_held_until_dispatcher_releases_it(
    server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plugin_data = tmp_path / "plugin-data"
    plugin_data.mkdir()
    monkeypatch.setenv("FAMULUS_HOST", "codex")
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))
    server._release_flow_lease("flow-1")
    server._acquire_flow_lease("flow-1")
    path, root = server._flow_lease_path("flow-1")
    with pytest.raises(atomic_files.AtomicLockUnavailable):
        with atomic_files.exclusive_file_lock(
            path, allowed_root=root, mode=0o600, blocking=False
        ):
            pass
    server._release_flow_lease("flow-1")
    with atomic_files.exclusive_file_lock(
        path, allowed_root=root, mode=0o600, blocking=False
    ):
        pass


def _arguments(server, payload: dict[str, object]):
    argument_type = (
        server.OrderedArguments
        if isinstance(payload.get("options"), list)
        else server.CompactArguments
    )
    return argument_type(**payload)


def _copy_plugin(plugin_root: Path, *, include_graph: bool = False) -> None:
    plugin_root.mkdir(parents=True)
    shutil.copy2(SERVER, plugin_root / SERVER.name)
    shutil.copy2(LAUNCHER, plugin_root / LAUNCHER.name)
    shutil.copy2(CORE, plugin_root / CORE.name)
    shutil.copy2(REQUIREMENTS, plugin_root / REQUIREMENTS.name)
    shutil.copy2(ROOT / "officina.toml", plugin_root / "officina.toml")
    shutil.copy2(ROOT / "plugin.json", plugin_root / "plugin.json")
    shutil.copy2(ROOT / "mcp.json", plugin_root / "mcp.json")
    shutil.copytree(ROOT / "src", plugin_root / "src")
    shutil.copytree(
        ROOT / "skills",
        plugin_root / "skills",
    )
    shutil.copytree(ROOT / "references", plugin_root / "references")
    shutil.copytree(ROOT / ".claude-plugin", plugin_root / ".claude-plugin")
    if include_graph:
        assert (plugin_root / "skills" / "math-dependency-graph").is_dir()


def _declared_launch(host: str, plugin_root: Path) -> tuple[str, list[str], Path | None]:
    if host == "claude":
        manifest = _json(plugin_root / ".claude-plugin" / "plugin.json")
        assert manifest["mcpServers"] == {
            "famulus_dispatcher": {
                "command": "python",
                "args": ["${CLAUDE_PLUGIN_ROOT}/mcp_launcher.py"],
                "env": {
                    "FAMULUS_HOST": "claude",
                    "FAMULUS_PLUGIN_DATA": "${CLAUDE_PLUGIN_DATA}",
                },
            }
        }
        # This is the documented result, not a replacement implementation of
        # Claude's loader.
        return "python", [str(plugin_root / "mcp_launcher.py")], None

    manifest = _json(plugin_root / "plugin.json")
    assert manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    servers = _json(plugin_root / "mcp.json")["mcpServers"]
    assert set(servers) == {"famulus_dispatcher"}
    declaration = servers["famulus_dispatcher"]
    assert declaration == {
        "type": "stdio",
        "command": "python",
        "args": ["${PLUGIN_ROOT}/mcp_launcher.py"],
        "cwd": "${PLUGIN_ROOT}",
        "env": {
            "FAMULUS_HOST": "codex",
            "FAMULUS_PLUGIN_DATA": "${PLUGIN_DATA}",
        },
    }
    return declaration["command"], [str(plugin_root / "mcp_launcher.py")], plugin_root


def _selected_environment(home: Path) -> dict[str, str]:
    """Expose the already-selected test interpreter through exact `python`."""
    environment = {
        key: value for key, value in os.environ.items() if key != "PYTHONPATH"
    }
    environment["PATH"] = os.pathsep.join(
        (str(Path(sys.executable).parent), environment.get("PATH", ""))
    )
    environment["HOME"] = str(home)
    if sys.platform == "win32":
        environment.update(
            {"USERPROFILE": str(home), "LOCALAPPDATA": str(home / "AppData" / "Local")}
        )
    paths = resolve_famulus_paths(platform=sys.platform, home=home, environ=environment)
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(paths.venv_path)
    return environment


def _only_broken_resource_errors(error: BaseException) -> bool:
    import anyio

    if isinstance(error, BaseExceptionGroup):
        return bool(error.exceptions) and all(
            _only_broken_resource_errors(child) for child in error.exceptions
        )
    return isinstance(error, anyio.BrokenResourceError)


@asynccontextmanager
async def _stdio_transport(parameters):
    """Ignore the MCP SDK's Windows-only clean-shutdown send race."""
    from mcp.client.stdio import stdio_client

    completed = False

    def mark_complete() -> None:
        nonlocal completed
        completed = True

    try:
        async with stdio_client(parameters) as streams:
            yield (*streams, mark_complete)
    except BaseExceptionGroup as error:
        if not completed or not _only_broken_resource_errors(error):
            raise


async def _invoke_through_mcp(host: str, plugin_root: Path, home: Path):
    from mcp import ClientSession, StdioServerParameters

    command, args, cwd = _declared_launch(host, plugin_root)
    environment = _selected_environment(home)
    environment.update(
        {"FAMULUS_HOST": host, "FAMULUS_PLUGIN_DATA": str(home / "plugin-data")}
    )
    parameters = StdioServerParameters(
        command=command,
        args=args,
        cwd=cwd,
        env=environment,
    )
    result = None
    async with _stdio_transport(parameters) as (read, write, mark_complete):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            called = await session.call_tool(
                "invoke",
                arguments={
                    "caller": "milestone-logging",
                    "interface": "common.interface.famulus-paths-get",
                    "version": 1,
                    "arguments": {
                        "positionals": ["logging-path"],
                        "options": {},
                        "stdin": None,
                    },
                },
            )
            unauthorized = await session.call_tool(
                "invoke",
                arguments={
                    "caller": "git-workflow",
                    "interface": "milestone-logging._rtx.interface.session-path",
                    "version": 1,
                    "arguments": {
                        "positionals": [],
                        "options": {},
                        "stdin": None,
                    },
                },
            )
            numeric = await session.call_tool(
                "invoke",
                arguments={
                    "caller": "milestone-logging",
                    "interface": "milestone-logging._rtx.interface.record-progress",
                    "version": 1,
                    "arguments": {
                        "positionals": ["numeric role"],
                        "options": {"--role": 7},
                        "stdin": None,
                    },
                },
            )
            ordered_positionals = await session.call_tool(
                "invoke",
                arguments={
                    "caller": "milestone-logging",
                    "interface": "milestone-logging._rtx.interface.session-path",
                    "version": 1,
                    "arguments": {
                        "positionals": ["unexpected"],
                        "options": [],
                        "stdin": None,
                    },
                },
            )
            after_rejections = await session.list_tools()
            result = (
                listed,
                called,
                unauthorized,
                numeric,
                ordered_positionals,
                after_rejections,
            )
            mark_complete()
    assert result is not None
    return result


def _persistent_launch(host: str, plugin_root: Path, plugin_data: Path):
    if host == "claude":
        declaration = _json(plugin_root / ".claude-plugin" / "plugin.json")[
            "mcpServers"
        ]["famulus_dispatcher"]
        args = [
            value.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))
            for value in declaration["args"]
        ]
        declared = {
            key: value.replace("${CLAUDE_PLUGIN_DATA}", str(plugin_data))
            for key, value in declaration["env"].items()
        }
        cwd = None
    else:
        declaration = _json(plugin_root / "mcp.json")["mcpServers"][
            "famulus_dispatcher"
        ]
        args = [
            value.replace("${PLUGIN_ROOT}", str(plugin_root))
            for value in declaration["args"]
        ]
        declared = {
            key: value.replace("${PLUGIN_DATA}", str(plugin_data))
            for key, value in declaration["env"].items()
        }
        cwd = plugin_root
    return declaration["command"], args, declared, cwd


async def _record_through_persistent_mcp(
    host: str, plugin_root: Path, home: Path, plugin_data: Path, canary: Path
):
    from mcp import ClientSession, StdioServerParameters
    command, args, declared, cwd = _persistent_launch(host, plugin_root, plugin_data)
    environment = _selected_environment(home)
    environment.update(declared)
    environment["ASSISTANT_LOGS"] = str(canary)
    parameters = StdioServerParameters(
        command=command, args=args, env=environment, cwd=cwd
    )
    result = None
    async with _stdio_transport(parameters) as (read, write, mark_complete):
        async with ClientSession(read, write) as session:
            await session.initialize()
            record_arguments = {
                "caller": "milestone-logging",
                "interface": "milestone-logging._rtx.interface.record-progress",
                "version": 1,
                "arguments": {
                    "positionals": ["persistent milestone"],
                    "options": {"--role": "task-3-test", "--task": "without-run"},
                    "stdin": None,
                },
            }
            result = await session.call_tool("invoke", arguments=record_arguments)
            mark_complete()
    assert result is not None
    return result


def _pid_is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:
                return False
            raise ctypes.WinError(error)
        try:
            status = int(kernel32.WaitForSingleObject(handle, 0))
            if status == 0x102:
                return True
            if status == 0:
                return False
            if status == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            raise OSError(f"unexpected Windows process wait status: {status}")
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _terminate_pid(pid: int) -> None:
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00000001 | 0x00100000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:
                return
            raise ctypes.WinError(error)
        try:
            status = int(kernel32.WaitForSingleObject(handle, 0))
            if status == 0:
                return
            if status == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            if status != 0x102:
                raise OSError(f"unexpected Windows process wait status: {status}")

            if not kernel32.TerminateProcess(handle, 1):
                termination_error = ctypes.get_last_error()
                status = int(kernel32.WaitForSingleObject(handle, 0))
                if status == 0:
                    return
                if status == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                raise ctypes.WinError(termination_error)

            status = int(kernel32.WaitForSingleObject(handle, 3000))
            if status == 0:
                return
            if status == 0x102:
                raise TimeoutError(f"Windows process {pid} did not terminate")
            if status == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            raise OSError(f"unexpected Windows process wait status: {status}")
        finally:
            kernel32.CloseHandle(handle)

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and _pid_is_alive(pid):
        time.sleep(0.05)
    assert not _pid_is_alive(pid)


def _wait_for_pid_exit(pid: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _pid_is_alive(pid):
        time.sleep(0.05)
    return not _pid_is_alive(pid)


def test_plugin_persistence_is_inert_without_plugin_context(
    server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: ordinary MCP startup rewrites direct-run logging state."""
    canary = tmp_path / "inherited-logs"
    monkeypatch.setenv("ASSISTANT_LOGS", str(canary))
    monkeypatch.setenv("XDG_DATA_HOME", "relative-but-irrelevant")
    monkeypatch.delenv("FAMULUS_HOST", raising=False)
    monkeypatch.delenv("FAMULUS_PLUGIN_DATA", raising=False)

    server.configure_plugin_persistence()

    assert os.environ["ASSISTANT_LOGS"] == str(canary)
    assert not canary.exists()


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_plugin_persistence_prepares_logs_without_claiming_manager_ledger(
    server, host: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: startup selects the wrong root, payload, or file mode."""
    plugin_data = tmp_path / host / "plugin data"
    canary = tmp_path / "inherited-canary"
    canary.mkdir()
    marker = canary / "marker.txt"
    marker.write_text("untouched", encoding="utf-8")
    monkeypatch.setenv("FAMULUS_HOST", host)
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))
    monkeypatch.setenv("ASSISTANT_LOGS", str(canary))

    server.configure_plugin_persistence()

    assert os.environ["ASSISTANT_LOGS"] == str(plugin_data / "milestones")
    assert (plugin_data / "milestones").is_dir()
    assert not (plugin_data / "setup").exists()
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert list(canary.iterdir()) == [marker]


def test_plugin_persistence_never_overwrites_existing_manager_ledger(
    server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: MCP startup replaces manager receipts with legacy readiness."""
    plugin_data = tmp_path / "plugin-data"
    status = plugin_data / "setup" / "status.json"
    status.parent.mkdir(parents=True)
    ledger = (
        b'{"active_flow":null,"interfaces":{"root.interface.setup":'
        b'{"required_by":["root.interface.setup"],"version":1}},'
        b'"schema_version":1}\n'
    )
    status.write_bytes(ledger)
    monkeypatch.setenv("FAMULUS_HOST", "codex")
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))

    server.configure_plugin_persistence()

    assert status.read_bytes() == ledger


@pytest.mark.parametrize(
    ("host", "data_kind"),
    [
        ("claude", None),
        (None, "absolute"),
        ("unknown", "absolute"),
        ("claude", "empty"),
        ("claude", "relative"),
    ],
)
def test_invalid_plugin_context_fails_before_partial_output(
    server,
    host: str | None,
    data_kind: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Break caught: invalid provenance mutates logs or creates partial state."""
    plugin_data = tmp_path / "must-not-exist"
    canary = tmp_path / "inherited-logs"
    monkeypatch.setenv("ASSISTANT_LOGS", str(canary))
    if host is None:
        monkeypatch.delenv("FAMULUS_HOST", raising=False)
    else:
        monkeypatch.setenv("FAMULUS_HOST", host)
    if data_kind is None:
        monkeypatch.delenv("FAMULUS_PLUGIN_DATA", raising=False)
    else:
        value = {"empty": "", "relative": "relative/path"}.get(
            data_kind, str(plugin_data)
        )
        monkeypatch.setenv("FAMULUS_PLUGIN_DATA", value)

    with pytest.raises(ValueError, match="plugin|FAMULUS_"):
        server.configure_plugin_persistence()

    assert os.environ["ASSISTANT_LOGS"] == str(canary)
    assert not canary.exists()
    assert not plugin_data.exists()


def test_plugin_persistence_rejects_unsafe_log_layout_before_publishing_logs(
    server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: unsafe startup publishes a writable external log root."""
    plugin_data = tmp_path / "plugin-data"
    outside = tmp_path / "outside"
    plugin_data.mkdir()
    outside.mkdir()
    marker = outside / "marker"
    marker.write_text("untouched", encoding="utf-8")
    try:
        (plugin_data / "milestones").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        # famulus-skip: category=platform-contract; reason=directory symlink creation is unavailable on some hosts; alternate=regular-directory confinement coverage remains
        pytest.skip(f"directory symlinks unavailable: {exc}")
    canary = tmp_path / "inherited-logs"
    monkeypatch.setenv("FAMULUS_HOST", "codex")
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))
    monkeypatch.setenv("ASSISTANT_LOGS", str(canary))

    with pytest.raises(server.DispatcherError) as caught:
        server.configure_plugin_persistence()

    assert caught.value.code == "dispatcher.mcp_persistence_invalid"
    assert str(caught.value) == (
        "Famulus MCP startup rejected an unsafe plugin-data child directory."
    )

    assert os.environ["ASSISTANT_LOGS"] == str(canary)
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert list(outside.iterdir()) == [marker]


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_real_mcp_persists_milestone_without_claiming_setup_ledger(
    host: str, tmp_path: Path
) -> None:
    """Break caught: persistence claims setup state or inherits the canary."""
    plugin = tmp_path / "Plugin Cache" / "famulus"
    plugin_data = tmp_path / "host-data" / host
    canary = tmp_path / "inherited-canary"
    canary.mkdir()
    marker = canary / "marker.txt"
    marker.write_text("untouched", encoding="utf-8")
    _copy_plugin(plugin)

    request = _record_through_persistent_mcp(
        host, plugin, tmp_path / "home", plugin_data, canary
    )
    called = asyncio.run(
        asyncio.wait_for(
            request, timeout=REAL_MCP_PERSISTENCE_LIFECYCLE_TIMEOUT_SECONDS
        )
    )

    result = called.structuredContent["result"]
    assert called.isError is False
    assert result["exit_code"] == 0, result
    assert not (plugin_data / "setup" / "status.json").exists()
    logs = sorted((plugin_data / "milestones").glob("*/*.jsonl"))
    assert len(logs) == 1
    records = [json.loads(line) for line in logs[0].read_text().splitlines()]
    assert records[0]["role"] == "task-3-test"
    assert records[0]["doing"] == "persistent milestone"
    assert records[0]["task"] == "without-run" and "run" not in records[0]
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert list(canary.iterdir()) == [marker]


async def _serve_graph_through_mcp(
    host: str, plugin_root: Path, home: Path, served: Path, port: int
) -> tuple[object, object, object, object, bytes, str, bool]:
    from mcp import ClientSession, StdioServerParameters

    command, args, cwd = _declared_launch(host, plugin_root)
    environment = _selected_environment(home)
    environment.update(
        {"FAMULUS_HOST": host, "FAMULUS_PLUGIN_DATA": str(home / "plugin-data")}
    )
    parameters = StdioServerParameters(
        command=command,
        args=args,
        cwd=cwd,
        env=environment,
    )
    pid: int | None = None
    completed = False
    result = None
    try:
        async with _stdio_transport(parameters) as (read, write, mark_complete):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                called = await session.call_tool(
                    "invoke",
                    arguments={
                        "caller": "math-dependency-graph",
                        "interface": (
                            "math-dependency-graph._rtx.interface.scripts-serve-graph"
                        ),
                        "version": 1,
                        "arguments": {
                            "positionals": [],
                            "options": {
                                "--directory": str(served),
                                "--host": "127.0.0.1",
                                "--port": str(port),
                            },
                            "stdin": None,
                        },
                        "dry_run": False,
                    },
                )
                ready = json.loads(called.structuredContent["result"]["stdout"])
                pid = ready["pid"]
                with urlopen(
                    ready["url"] + quote("known file.txt"), timeout=3.0
                ) as response:
                    body = response.read()
                    cache_control = response.headers["Cache-Control"]
                after = await session.list_tools()
                finite = await session.call_tool(
                    "invoke",
                    arguments={
                        "caller": "milestone-logging",
                        "interface": "milestone-logging._rtx.interface.session-path",
                        "version": 1,
                        "arguments": {
                            "positionals": [],
                            "options": {},
                            "stdin": None,
                        },
                        "dry_run": True,
                    },
                )
                alive_during_session = _pid_is_alive(pid)
                result = (
                    listed,
                    called,
                    after,
                    finite,
                    body,
                    cache_control,
                    alive_during_session,
                )
                mark_complete()
        completed = True
        assert result is not None
        return result
    finally:
        if not completed and pid is not None:
            if sys.platform == "win32":
                try:
                    if _pid_is_alive(pid) and not _wait_for_pid_exit(pid):
                        _terminate_pid(pid)
                except OSError:
                    pass
            elif _pid_is_alive(pid):
                _terminate_pid(pid)


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_graph_server_survives_invocation_and_follows_host_teardown_lifecycle(
    host: str, tmp_path: Path, free_tcp_port: int
) -> None:
    """Break caught: graph lifetime contradicts its platform host boundary."""
    plugin = tmp_path / "Plugin Cache" / "famulus"
    served = tmp_path / "served directory with spaces"
    served.mkdir()
    expected = b"real-mcp-task-four"
    (served / "known file.txt").write_bytes(expected)
    _copy_plugin(plugin, include_graph=True)
    pid: int | None = None
    try:
        listed, called, after, finite, body, cache_control, alive = asyncio.run(
            asyncio.wait_for(
                _serve_graph_through_mcp(
                    host, plugin, tmp_path / "home", served, free_tcp_port
                ),
                timeout=REAL_MCP_INTEGRATION_TIMEOUT_SECONDS,
            )
        )
        result = called.structuredContent["result"]
        ready = json.loads(result["stdout"])
        pid = ready["pid"]
        assert [tool.name for tool in listed.tools] == ["invoke"]
        assert called.isError is False
        assert result["exit_code"] == 0
        assert result["stderr"] == ""
        assert ready == {
            "serving": str(served.resolve()),
            "host": "127.0.0.1",
            "port": ready["port"],
            "url": f"http://127.0.0.1:{ready['port']}/",
            "cache": "disabled",
            "pid": pid,
        }
        assert isinstance(ready["port"], int)
        assert isinstance(pid, int) and pid > 0
        assert body == expected
        assert cache_control == "no-store, no-cache, must-revalidate, max-age=0"
        assert alive is True
        assert [tool.name for tool in after.tools] == ["invoke"]
        assert finite.isError is False
        assert finite.structuredContent["result"]["target"] == (
            "milestone-logging._rtx.interface.session-path"
        )
        if sys.platform != "win32":
            assert _pid_is_alive(pid)
    finally:
        if pid is not None:
            if sys.platform == "win32":
                exited_with_gateway = _wait_for_pid_exit(pid)
                if not exited_with_gateway:
                    try:
                        _terminate_pid(pid)
                    finally:
                        assert exited_with_gateway, (
                            "Windows graph process survived its MCP Job Object"
                        )
            elif _pid_is_alive(pid):
                _terminate_pid(pid)


# famulus-skip: category=platform-contract; reason=requires native Windows process handles; alternate=POSIX kill-zero liveness is exercised by both graph-server cases
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
def test_windows_pid_probe_is_nondestructive() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _pid_is_alive(process.pid)
        assert process.poll() is None
        assert _pid_is_alive(process.pid)
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.2)
        _terminate_pid(process.pid)
        process.wait(timeout=5)
        assert not _pid_is_alive(process.pid)
        _terminate_pid(process.pid)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_packaged_host_declaration_invokes_dispatcher_through_real_mcp(
    host: str, tmp_path: Path
) -> None:
    """Break caught: a host declaration can list the tool but not invoke Dispatcher."""
    plugin = tmp_path / "Plugin Cache" / "famulus"
    _copy_plugin(plugin)
    contract = _json(plugin / CORE.name)

    listed, called, unauthorized, numeric, ordered_positionals, after = asyncio.run(
        asyncio.wait_for(
            _invoke_through_mcp(host, plugin, tmp_path / "home"),
            timeout=REAL_MCP_INTEGRATION_TIMEOUT_SECONDS,
        )
    )

    assert [tool.name for tool in listed.tools] == [contract["tool"]["name"]]
    tool = listed.tools[0]
    assert tool.description.startswith("Invoke one authorized Famulus interface")
    schema = tool.inputSchema
    assert set(schema["properties"]) == set(contract["tool"]["required"]) | set(
        contract["tool"]["optional"]
    )
    assert schema["required"] == contract["tool"]["required"]
    assert schema["properties"]["dry_run"]["default"] is False
    argument_refs = {
        item["$ref"] for item in schema["properties"]["arguments"]["anyOf"]
    }
    assert argument_refs == {
        "#/$defs/CompactArguments",
        "#/$defs/OrderedArguments",
    }
    definitions = schema["$defs"]
    assert definitions["CompactArguments"]["required"] == [
        "positionals",
        "options",
        "stdin",
    ]
    compact_values = definitions["CompactArguments"]["properties"]["options"][
        "additionalProperties"
    ]["anyOf"]
    assert {value.get("type") for value in compact_values} == {"string", "boolean"}
    assert {value.get("const") for value in compact_values} == {None, True}
    ordered_properties = definitions["OrderedArguments"]["properties"]
    assert ordered_properties["options"]["items"] == {"type": "string"}
    assert ordered_properties["positionals"]["maxItems"] == 0
    assert {item.get("type") for item in definitions["CompactArguments"]["properties"]["stdin"]["anyOf"]} == {
        "string",
        "null",
    }
    output = tool.outputSchema
    assert "#/$defs/ExecutionResult" in {
        item.get("$ref") for item in output["properties"]["result"]["anyOf"]
    }
    assert set(output["$defs"]["ExecutionResult"]["properties"]) == {
        "exit_code",
        "stdout",
        "stderr",
        "dispatcher",
        "trace_id",
    }
    assert output["$defs"]["ExecutionResult"]["properties"]["dispatcher"][
        "additionalProperties"
    ] is True
    assert called.isError is False
    result = called.structuredContent["result"]
    assert result["exit_code"] == 0, result
    assert len(result["trace_id"]) == 32
    assert Path(result["stdout"].strip()) == (
        tmp_path / "home" / "plugin-data" / "milestones"
    )
    assert result["stderr"] == ""
    assert result["dispatcher"]["target"] == "common.interface.famulus-paths-get"
    assert result["dispatcher"]["warnings"]
    failure = unauthorized.structuredContent["result"]
    assert failure["dispatcher"]["code"] == "dispatcher.unauthorized_caller"
    assert failure["dispatcher"]["interface_id"] == (
        "milestone-logging._rtx.interface.session-path"
    )
    assert numeric.isError is True
    assert ordered_positionals.isError is True
    assert [tool.name for tool in after.tools] == [contract["tool"]["name"]]


def test_contract_keeps_mcp_metadata_separate_from_runtime_requirements() -> None:
    contract = _json(CORE)

    assert "core_packages" not in contract
    assert REQUIREMENTS.read_text(encoding="utf-8").splitlines() == [
        "mcp>=1,<2",
        "PyYAML>=6",
        "jsonschema>=4,<5",
    ]
    assert contract["fingerprint"] == [
        "sys.executable",
        "sys.prefix",
        "sys.base_prefix",
        "sys.version_info[:2]",
    ]
    assert "google" not in REQUIREMENTS.read_text(encoding="utf-8").casefold()
    assert "keyring" not in REQUIREMENTS.read_text(encoding="utf-8").casefold()


def test_packaged_server_imports_its_own_src_without_pythonpath(tmp_path: Path) -> None:
    plugin = tmp_path / "Plugin Cache" / "famulus"
    _copy_plugin(plugin)
    probe = (
        "import importlib.util; p='mcp_server.py'; "
        "s=importlib.util.spec_from_file_location('x', p); "
        "m=importlib.util.module_from_spec(s); import sys; sys.modules['x']=m; "
        "s.loader.exec_module(m); assert m.invoke"
    )
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=plugin,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_dry_run_matches_direct_dispatcher_resolution(server) -> None:
    from officina.dispatcher.direct_runtime import resolve_dispatch_metadata

    expected = resolve_dispatch_metadata(
        caller_skill="milestone-logging",
        target="milestone-logging._rtx.interface.session-path",
        target_version=1,
        args=[],
        repository_config=ROOT / "officina.toml",
    ).as_payload()

    assert server.invoke(
        "milestone-logging",
        "milestone-logging._rtx.interface.session-path",
        1,
        _arguments(server, {"positionals": [], "options": {}, "stdin": None}),
        dry_run=True,
    ) == expected


def test_generated_outer_payload_uses_real_tool_field_names() -> None:
    """Break caught: projection omits the required outer interface field."""
    syncer = ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py"
    spec = importlib.util.spec_from_file_location("projection_syncer", syncer)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    graph = module.load_blueprints()["milestone-logging"].repository_graph
    generated = module.generated_interface_block("milestone-logging", graph)
    # The two host parameters of the packaged declaration test retain the
    # physical MCP schema and accepted-request boundary for these field names.
    assert (
        "with required `caller` (caller skill), `interface`, `version`, and "
        "`arguments`; optional `dry_run` defaults to false"
    ) in generated
    assert all(
        fragment in generated
        for fragment in (
            '"positionals": ["DOING", "PREV"]',
            '"--role": "ROLE"',
            '"--task": "TASK"',
            "Omit optional positionals and options that are not needed.",
        )
    )


def test_llm_wakeup_skill_renders_every_public_wakeup_invocation(server) -> None:
    """Break caught: the gateway loses the source dependency for wakeup calls."""
    generated = (ROOT / "skills" / "llm-wakeup" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    expected = {
        "llm-wakeup._rtx.interface.auto-policy": (
            "default",
            {
                    "positionals": ["action", "provider", "session-id"],
                "options": {},
                "stdin": None,
            },
            {"positionals": ["on", "claude", "session-id"], "options": {}, "stdin": None},
        ),
        "llm-wakeup._rtx.interface.infer-schedule": (
            "default",
            {
                "positionals": [],
                "options": {
                    "--text": "timeout-or-resume-text",
                    "--message": "message",
                    "--delay": "duration",
                },
                "stdin": None,
            },
            {"positionals": [], "options": {}, "stdin": None},
        ),
        "llm-wakeup._rtx.interface.explicit-schedule": (
            "default",
            {
                    "positionals": ["provider", "session-id", "reset-time"],
                "options": {"--message": "message", "--delay": "duration"},
                "stdin": None,
            },
            {"positionals": ["claude", "session-id", "1 minute"], "options": {}, "stdin": None},
        ),
        # The managed lifecycle forbids arguments on a setup interface, so the
        # gateway renders one argument-free alternative and no teardown route:
        # exact managed calls are redirected through the setup manager.
        "llm-wakeup._rtx.interface.setup": (
            "default",
            {"positionals": [], "options": {}, "stdin": None},
            {"positionals": [], "options": {}, "stdin": None},
        ),
    }
    assert "Executable Interfaces:" in generated
    assert "Alternative: `default`" in generated
    assert "Alternative: `teardown`" not in generated
    for interface, (alternative, rendered_arguments, _invocation_arguments) in expected.items():
        interface_id = interface.removesuffix(" teardown")
        assert f"`{interface_id}`" in generated
        assert f"Alternative: `{alternative}`" in generated
        assert json.dumps(rendered_arguments, sort_keys=True) in generated

    FastMCP = pytest.importorskip("mcp.server.fastmcp").FastMCP
    mcp = FastMCP("famulus")
    mcp.tool()(server.invoke)
    assert asyncio.run(mcp.list_tools())[0].name == "invoke"
    concrete = {
        "action": "on",
        "provider": "claude",
        "session-id": "session-id",
        "timeout-or-resume-text": "quota resets in 1 minute",
        "duration": "1 minute",
        "message": "message",
        "reset-time": "1 minute",
        "setup": "setup",
        "teardown": "teardown",
        "FILE": "/opt/famulus/python",
        "DIR": "/opt/famulus",
    }
    for interface, (_alternative, rendered_arguments, _arguments) in expected.items():
        interface_id = interface.removesuffix(" teardown")
        arguments = {
            "positionals": [
                concrete[value] for value in rendered_arguments["positionals"]
            ],
            "options": {
                name: concrete[value]
                for name, value in rendered_arguments["options"].items()
            },
            "stdin": rendered_arguments["stdin"],
        }
        _content, payload = asyncio.run(
            mcp.call_tool(
                "invoke",
                {
                    "caller": "llm-wakeup",
                    "interface": interface_id,
                    "version": 1,
                    "arguments": arguments,
                    "dry_run": True,
                },
            )
        )
        assert payload["result"]["target"] == interface_id


def test_ordered_arguments_match_email_pattern_and_reject_mixed_alternative(server) -> None:
    """Break caught: a projected short-account alternative permits --account too."""
    accepted = server.invoke(
        "email-client",
        "email-client._rtx.interface.mail-attachments",
        1,
        server.OrderedArguments(positionals=(), options=["-a", "account", "42", "43"], stdin=None),
        dry_run=True,
    )
    rejected = server.invoke(
        "email-client",
        "email-client._rtx.interface.mail-attachments",
        1,
        server.OrderedArguments(
            positionals=(),
            options=["-a", "account", "--account", "other", "42"],
            stdin=None,
        ),
        dry_run=True,
    )

    assert accepted["target"] == "email-client._rtx.interface.mail-attachments"
    assert rejected["exit_code"] == 2
    assert rejected["dispatcher"]["code"] == "dispatcher.resolution_failed"

    folders = server.invoke(
        "email-client", "email-client._rtx.interface.mail-folders", 1,
        server.OrderedArguments(positionals=(), options=["--account", "account"], stdin=None),
        dry_run=True,
    )
    assert folders["target"] == "email-client._rtx.interface.mail-folders"


def test_comprehension_fixture_is_an_uncoached_generated_candidate() -> None:
    """Break caught: frozen cases drift or expose the controller oracle."""
    fixture = _json(COMPREHENSION_FIXTURE)

    assert "`famulus_dispatcher` MCP server" in fixture["session_start"]
    assert fixture["mcp_tool"] == "famulus_dispatcher.invoke"
    assert [case["case_id"] for case in fixture["cases"]] == [
        "T3C-A",
        "T3C-B",
        "T3C-C",
    ]
    assert [case["skill"] for case in fixture["cases"]] == [
        "skills/milestone-logging/SKILL.md",
        "skills/milestone-logging/SKILL.md",
        "skills/loose-mode/SKILL.md",
    ]
    assert "Executable Interfaces:" in (
        ROOT / fixture["cases"][0]["skill"]
    ).read_text(encoding="utf-8")
    assert "Executable Interfaces:" in (
        ROOT / fixture["cases"][1]["skill"]
    ).read_text(encoding="utf-8")
    instruction_only = (ROOT / fixture["cases"][2]["skill"]).read_text(
        encoding="utf-8"
    )
    assert "Used Interfaces: none" in instruction_only
    assert "Executable Interfaces:" not in instruction_only
    assert "Executable Interfaces:" not in (
        ROOT / fixture["cases"][2]["skill"]
    ).read_text(encoding="utf-8")
    for case in fixture["cases"]:
        assert set(case) == {"case_id", "skill", "user_task"}


def test_execution_captures_dispatcher_output_without_mcp_stdout(
    server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FAMULUS_HOST", "codex")
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    server.configure_plugin_persistence()
    result = server.invoke(
        "milestone-logging",
        "common.interface.famulus-paths-get",
        1,
        _arguments(
            server,
            {"positionals": ["logging-path"], "options": {}, "stdin": None},
        ),
    )

    assert result["exit_code"] == 0
    assert result["stdout"].strip() == str(tmp_path / "plugin-data" / "milestones")
    assert result["stderr"] == ""
    assert result["dispatcher"]["target"] == "common.interface.famulus-paths-get"


def test_structured_dispatcher_error_is_returned(server) -> None:
    result = server.invoke(
        "missing-caller",
        "milestone-logging._rtx.interface.record-progress",
        1,
        _arguments(server, {"positionals": ["work"], "options": {"--role": "test"}, "stdin": None}),
    )

    assert result["exit_code"] == 2
    assert result["dispatcher"]["code"] == "dispatcher.module_not_found"
    assert result["stdout"] == result["stderr"] == ""


@pytest.mark.parametrize(
    ("target", "arguments", "argv"),
    [
        ("milestone-logging._rtx.interface.record-progress", {"positionals": ["one"], "options": {"--role": "task"}, "stdin": None}, ["one", "--role", "task"]),
        ("milestone-logging._rtx.interface.record-completion", {"positionals": ["one"], "options": {"--role": "task"}, "stdin": None}, ["one", "--role", "task"]),
        ("milestone-logging._rtx.interface.session-path", {"positionals": [], "options": {}, "stdin": None}, []),
        ("milestone-logging._rtx.interface.show-latest-session", {"positionals": [], "options": {}, "stdin": None}, []),
    ],
)
def test_json_envelope_matches_direct_dispatcher(
    server, target: str, arguments: dict[str, object], argv: list[str]
) -> None:
    from officina.dispatcher.direct_runtime import resolve_dispatch_metadata

    typed_arguments = _arguments(server, arguments)
    assert server.caller_argv(typed_arguments) == argv
    assert server.invoke(
        "milestone-logging", target, 1, typed_arguments, dry_run=True
    ) == resolve_dispatch_metadata(
        caller_skill="milestone-logging",
        target=target,
        target_version=1,
        args=argv,
        repository_config=ROOT / "officina.toml",
    ).as_payload()


def test_python_prerequisite_has_no_platform_fallback(server) -> None:
    with pytest.raises(server.DispatcherError) as caught:
        server.require_python((3, 10))

    assert caught.value.as_payload() == {
        "schema_version": 1,
        "code": "dispatcher.mcp_python_unsupported",
        "message": "Famulus MCP startup requires Python 3.11 or newer; running 3.10.",
        "major": 3,
        "minor": 10,
    }


def test_missing_exact_python_path_has_no_declared_fallback(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "Plugin Cache" / "famulus"
    _copy_plugin(plugin)
    command, args, cwd = _declared_launch("codex", plugin)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()

    assert command == "python"
    environment = {"PATH": str(empty_path)}
    assert shutil.which(command, path=environment["PATH"]) is None
    if os.name == "nt":
        # CreateProcess lookup is not redirected by the child environment's
        # PATH, so a launch cannot establish this contract on Windows.
        assert not Path(command).is_absolute()
        return
    with pytest.raises(FileNotFoundError, match="python"):
        subprocess.run(
            [command, *args], cwd=cwd, env=environment, check=False
        )


def test_stdio_transport_ignores_only_a_clean_shutdown_send_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anyio
    import mcp.client.stdio

    @asynccontextmanager
    async def shutdown_race(_parameters):
        yield object(), object()
        raise BaseExceptionGroup("shutdown", [anyio.BrokenResourceError()])

    monkeypatch.setattr(mcp.client.stdio, "stdio_client", shutdown_race)

    async def use_transport() -> None:
        async with _stdio_transport(object()) as (_read, _write, mark_complete):
            mark_complete()

    asyncio.run(use_transport())

    async def nested_teardown_race() -> str:
        result = None
        async with _stdio_transport(object()) as (_read, _write, mark_complete):
            async with shutdown_race(object()):
                result = "produced value"
                mark_complete()
        assert result is not None
        return result

    assert asyncio.run(nested_teardown_race()) == "produced value"

    async def fail_in_body() -> None:
        async with _stdio_transport(object()):
            raise ValueError("body failure")

    with pytest.raises(ValueError, match="body failure"):
        asyncio.run(fail_in_body())

    async def broken_resource_in_body() -> None:
        async with _stdio_transport(object()):
            raise BaseExceptionGroup(
                "body failure", [anyio.BrokenResourceError()]
            )

    with pytest.raises(BaseExceptionGroup, match="body failure"):
        asyncio.run(broken_resource_in_body())

    @asynccontextmanager
    async def mixed_teardown_error(_parameters):
        yield object(), object()
        raise BaseExceptionGroup(
            "mixed teardown",
            [anyio.BrokenResourceError(), ValueError("body failure")],
        )

    async def fail_with_mixed_error() -> None:
        async with _stdio_transport(object()):
            async with mixed_teardown_error(object()):
                pass

    with pytest.raises(BaseExceptionGroup, match="mixed teardown") as caught:
        asyncio.run(fail_with_mixed_error())
    assert any(isinstance(error, ValueError) for error in caught.value.exceptions)


def test_invoke_through_mcp_preserves_result_when_session_teardown_breaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anyio
    import mcp
    import mcp.client.stdio

    @asynccontextmanager
    async def clean_transport(_parameters):
        yield object(), object()

    class TeardownRaceSession:
        def __init__(self, _read, _write) -> None:
            self.listed = iter(("listed before", "listed after"))
            self.called = iter(
                ("called", "unauthorized", "numeric", "ordered positionals")
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
            raise BaseExceptionGroup(
                "session shutdown", [anyio.BrokenResourceError()]
            )

        async def initialize(self) -> None:
            return None

        async def list_tools(self):
            return next(self.listed)

        async def call_tool(self, _name, **_kwargs):
            return next(self.called)

    monkeypatch.setattr(mcp.client.stdio, "stdio_client", clean_transport)
    monkeypatch.setattr(mcp, "ClientSession", TeardownRaceSession)
    plugin = tmp_path / "Plugin Cache" / "famulus"
    _copy_plugin(plugin)

    result = asyncio.run(
        _invoke_through_mcp("claude", plugin, tmp_path / "home")
    )

    assert result == (
        "listed before",
        "called",
        "unauthorized",
        "numeric",
        "ordered positionals",
        "listed after",
    )


def test_host_declarations_normalize_plugin_data_before_launcher() -> None:
    contract = _json(CORE)
    claude = _json(ROOT / ".claude-plugin" / "plugin.json")["mcpServers"][
        "famulus_dispatcher"
    ]
    codex = _json(ROOT / "mcp.json")["mcpServers"]["famulus_dispatcher"]

    assert not (ROOT / (".mcp" + ".json")).exists()
    assert contract["command"] == "python"
    assert contract["args"] == ["mcp_launcher.py"]
    assert claude["command"] == codex["command"] == contract["command"]
    assert claude["args"] == ["${CLAUDE_PLUGIN_ROOT}/" + contract["args"][0]]
    assert claude["env"] == {
        "FAMULUS_HOST": "claude",
        "FAMULUS_PLUGIN_DATA": "${CLAUDE_PLUGIN_DATA}",
    }
    assert codex["type"] == "stdio"
    assert codex["args"] == ["${PLUGIN_ROOT}/" + contract["args"][0]]
    assert codex["cwd"] == "${PLUGIN_ROOT}"
    assert codex["env"] == {
        "FAMULUS_HOST": "codex",
        "FAMULUS_PLUGIN_DATA": "${PLUGIN_DATA}",
    }


def test_packaged_fixture_has_complete_registered_repository_graph(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "plugin"
    _copy_plugin(plugin)
    packaged = {
        path.relative_to(plugin).as_posix()
        for path in (plugin / "skills").rglob("*")
        if path.is_file()
    }

    assert {
        "skills/milestone-logging/blueprint.yaml",
        "skills/setup-interface-manager/_rtx/_setup_manager.py",
        "skills/math-dependency-graph/blueprint.yaml",
    } <= packaged
    assert (
        "skills/setup-interface-manager/_rtx/tests/test_setup_manager.py"
        in packaged
    )


def test_ordered_options_preserve_literal_separator(server) -> None:
    arguments = {"positionals": [], "options": ["--", "--role"], "stdin": None}
    assert server.caller_argv(_arguments(server, arguments)) == ["--", "--role"]


def test_ordered_options_are_lossless_for_repeated_flags(server) -> None:
    from officina.dispatcher.direct_runtime import resolve_dispatch_metadata
    from officina.dispatcher.errors import InvocationError

    argv = [
        "ordered progress", "--run", "nightly", "--evidence", "first", "--evidence", "second", "--role", "task"
    ]
    arguments = {"positionals": [], "options": argv, "stdin": None}
    typed_arguments = _arguments(server, arguments)
    assert server.caller_argv(typed_arguments) == argv
    with pytest.raises(InvocationError) as direct:
        resolve_dispatch_metadata(
            caller_skill="milestone-logging",
            target="milestone-logging._rtx.interface.record-progress",
            target_version=1,
            args=argv,
            repository_config=ROOT / "officina.toml",
        )
    result = server.invoke(
        "milestone-logging",
        "milestone-logging._rtx.interface.record-progress",
        1,
        typed_arguments,
        dry_run=True,
    )
    assert result["exit_code"] == 2
    assert result["dispatcher"] == direct.value.as_payload()


def test_compact_options_reject_ambiguous_list_values(server) -> None:
    with pytest.raises(server.DispatcherError) as caught:
        server.caller_argv(
            server.CompactArguments(
                positionals=[],
                options={"--evidence": ["first", "second"]},
                stdin=None,
            )
        )

    assert caught.value.code == "dispatcher.invalid_request"
    assert str(caught.value) == (
        "MCP compact option values must be strings or `true`, not lists."
    )
