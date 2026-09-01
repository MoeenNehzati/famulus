from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import quote
from urllib.request import urlopen

import pytest

server = importlib.import_module("officina.visualization.server")


def _load_graph_interface():
    path = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "math-dependency-graph"
        / "_rtx"
        / "_graph_server.py"
    )
    spec = importlib.util.spec_from_file_location("graph_server_interface", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("port", ["0", "65536"])
def test_server_parser_rejects_ports_outside_tcp_range(port: str) -> None:
    with pytest.raises(SystemExit):
        server.parse_args(["--port", port])


def test_server_parser_accepts_highest_tcp_port() -> None:
    assert server.parse_args(["--port", "65535"]).port == 65535


def test_no_cache_handler_adds_response_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    headers: list[tuple[str, str]] = []
    handler = object.__new__(server.NoCacheRequestHandler)
    handler.send_header = lambda name, value: headers.append((name, value))
    monkeypatch.setattr(server.SimpleHTTPRequestHandler, "end_headers", lambda self: None)

    handler.end_headers()

    assert ("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0") in headers
    assert ("Pragma", "no-cache") in headers
    assert ("Expires", "0") in headers


def test_foreground_server_reports_ready_state_and_closes(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FakeServer:
        def __init__(self, address, handler) -> None:
            self.address = address
            self.handler = handler
            self.closed = False

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            self.closed = True

    instances: list[FakeServer] = []

    def make_server(address, handler):
        instance = FakeServer(address, handler)
        instances.append(instance)
        return instance

    monkeypatch.setattr(server, "ReusableThreadingHTTPServer", make_server)

    server.serve_graph_directory(tmp_path, host="127.0.0.1", port=9876)

    report = json.loads(capsys.readouterr().out)
    assert report == {
        "serving": str(tmp_path.resolve()),
        "url": "http://127.0.0.1:9876/",
        "cache": "disabled",
    }
    assert instances[0].address == ("127.0.0.1", 9876)
    assert instances[0].closed is True


def test_graph_interface_starts_background_server_and_reports_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Break caught: the graph interface enters foreground serve_forever."""
    interface = _load_graph_interface()
    calls: list[tuple[Path, str, int]] = []

    class FakeProcess:
        pid = 4321

    class FakeHandle:
        directory = tmp_path.resolve()
        host = "127.0.0.1"
        port = 8766
        url = "http://127.0.0.1:8766/"
        process = FakeProcess()

    def fake_start(directory, *, host, port):
        calls.append((Path(directory), host, port))
        return FakeHandle()

    monkeypatch.setattr(interface, "start_graph_server", fake_start)

    assert interface.Interface().run(
        ["--directory", str(tmp_path), "--host", "127.0.0.1", "--port", "8765"]
    ) == 0

    assert calls == [(tmp_path, "127.0.0.1", 8765)]
    assert json.loads(capsys.readouterr().out) == {
        "serving": str(tmp_path.resolve()),
        "host": "127.0.0.1",
        "port": 8766,
        "url": "http://127.0.0.1:8766/",
        "cache": "disabled",
        "pid": 4321,
    }


def test_background_graph_server_serves_no_cache_and_stops(tmp_path: Path) -> None:
    """Break caught: the background helper bypasses the no-cache handler."""
    served = tmp_path / "directory with spaces"
    served.mkdir()
    expected = b"task-four-known-bytes"
    (served / "known file.txt").write_bytes(expected)
    handle = server.start_graph_server(served)
    try:
        with urlopen(handle.url + quote("known file.txt"), timeout=3.0) as response:
            assert response.status == 200
            assert response.read() == expected
            assert response.headers["Cache-Control"] == (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            assert response.headers["Pragma"] == "no-cache"
            assert response.headers["Expires"] == "0"
        assert handle.directory == served.resolve()
        assert handle.url == f"http://{handle.host}:{handle.port}/"
        assert handle.process.pid > 0
        assert handle.process.poll() is None
    finally:
        handle.stop()
    assert handle.process.poll() is not None


@pytest.mark.parametrize(
    ("platform", "os_name"),
    [("linux", "posix"), ("darwin", "posix"), ("win32", "nt")],
)
def test_background_graph_server_detaches_protocol_pipes_and_preserves_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, os_name: str
) -> None:
    """Break caught: a platform branch inherits MCP pipes or constructs shell text."""
    served = tmp_path / "served directory with spaces"
    served.mkdir()
    calls: list[tuple[list[str], dict[str, object]]] = []

    class FakeProcess:
        pid = 1234
        terminated = False
        waited = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            self.waited = True
            return -15

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    states = iter((False, True))
    monkeypatch.setattr(server, "is_port_open", lambda host, port: next(states))
    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server.sys, "executable", "/runtime with spaces/python")
    monkeypatch.setattr(server, "os", SimpleNamespace(name=os_name))
    monkeypatch.setattr(server.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(server.subprocess, "DETACHED_PROCESS", 8, raising=False)

    handle = server.start_graph_server(
        served, host="127.0.0.1", port=9876, port_scan=False
    )

    command, kwargs = calls[0]
    assert command == [
        "/runtime with spaces/python",
        "-m",
        "officina.visualization.server",
        "--directory",
        str(served.resolve()),
        "--host",
        "127.0.0.1",
        "--port",
        "9876",
    ]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert "shell" not in kwargs
    if platform == "win32":
        assert "start_new_session" not in kwargs
        assert kwargs["creationflags"] == (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        assert kwargs["start_new_session"] is True
        assert "creationflags" not in kwargs
    assert handle.process.pid == 1234
    handle.stop()
    assert handle.process.terminated is True
    assert handle.process.waited is True


def test_background_graph_server_timeout_cleans_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: readiness timeout leaves the spawned child alive."""
    class FakeProcess:
        pid = 1234
        terminated = False
        waited = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            self.waited = True
            return -15

    process = FakeProcess()
    monkeypatch.setattr(server, "is_port_open", lambda host, port: False)
    monkeypatch.setattr(server.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(RuntimeError, match="did not start"):
        server.start_graph_server(
            tmp_path, port=9876, port_scan=False, startup_wait=0.0
        )

    assert process.terminated is True
    assert process.waited is True


def test_background_graph_server_default_wait_allows_loaded_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: the default budget kills a healthy child before a slow import."""

    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float) -> int:
            return -15

    elapsed = 0.0

    def perf_counter() -> float:
        return elapsed

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    monkeypatch.setattr(server, "is_port_open", lambda host, port: elapsed >= 4.0)
    monkeypatch.setattr(server.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(server.time, "perf_counter", perf_counter)
    monkeypatch.setattr(server.time, "sleep", sleep)

    handle = server.start_graph_server(tmp_path, port=9876, port_scan=False)

    assert handle.process.pid == 1234


def test_background_graph_server_reaps_child_that_exits_during_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: an early startup exit leaves an unreaped child process."""
    class FakeProcess:
        pid = 1234
        waited = False

        def poll(self):
            return 2

        def wait(self):
            self.waited = True
            return 2

    process = FakeProcess()
    monkeypatch.setattr(server, "is_port_open", lambda host, port: False)
    monkeypatch.setattr(server.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(RuntimeError, match="exited while starting"):
        server.start_graph_server(
            tmp_path, port=9876, port_scan=False, startup_wait=1.0
        )

    assert process.waited is True
