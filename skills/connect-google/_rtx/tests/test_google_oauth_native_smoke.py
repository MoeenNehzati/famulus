"""Opt-in native smoke tests for browser launch and same-port SSH transport."""

from __future__ import annotations

import http.server
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest


HELPER = Path(__file__).resolve().parents[1] / "_browser_helper.py"


def _stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


@pytest.fixture
def child_processes():
    processes: list[subprocess.Popen] = []
    yield processes
    for process in reversed(processes):
        _stop(process)


# famulus-skip: category=live-smoke-opt-in; reason=generic CI may not have an interactive desktop browser; alternate=helper lifecycle and diagnostic contract tests cover the portable mechanism
@pytest.mark.skipif(
    os.environ.get("FAMULUS_GOOGLE_BROWSER_SMOKE") != "1",
    reason="set FAMULUS_GOOGLE_BROWSER_SMOKE=1 on a desktop to run",
)
def test_native_default_browser_reaches_local_nonce(child_processes) -> None:
    reached = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/famulus-google-browser-smoke":
                reached.set()
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        process = subprocess.Popen(
            [sys.executable, str(HELPER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        child_processes.append(process)
        assert process.stdin is not None
        process.stdin.write(
            f"http://127.0.0.1:{port}/famulus-google-browser-smoke\n".encode()
        )
        process.stdin.close()
        assert reached.wait(timeout=30)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


# famulus-skip: category=live-smoke-opt-in; reason=generic CI has no disposable SSH host and reserved same port; alternate=loopback and exact tunnel rendering tests cover the shared contract
@pytest.mark.skipif(
    not os.environ.get("FAMULUS_GOOGLE_SSH_SMOKE_TARGET")
    or not os.environ.get("FAMULUS_GOOGLE_SSH_SMOKE_PORT"),
    reason="set FAMULUS_GOOGLE_SSH_SMOKE_TARGET and FAMULUS_GOOGLE_SSH_SMOKE_PORT",
)
def test_same_port_ssh_forward_reaches_remote_loopback(child_processes) -> None:
    target = os.environ["FAMULUS_GOOGLE_SSH_SMOKE_TARGET"]
    port = int(os.environ["FAMULUS_GOOGLE_SSH_SMOKE_PORT"])
    process = subprocess.Popen(
        [
            "ssh",
            "-o",
            "ExitOnForwardFailure=yes",
            "-L",
            f"127.0.0.1:{port}:127.0.0.1:{port}",
            target,
            "python3",
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_processes.append(process)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                assert response.status == 200
                return
        except OSError:
            time.sleep(0.2)
    pytest.fail("same-port SSH forward did not become reachable within 30 seconds")
