"""Local static server utilities for rendered graph artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import socket
import subprocess
import sys
import time


@dataclass
class GraphServer:
    """Handle for a reusable background graph server."""

    process: subprocess.Popen
    host: str
    port: int
    directory: Path

    @property
    def url(self) -> str:
        """Return the base URL for the running server."""
        return f"http://{self.host}:{self.port}/"

    def stop(self) -> None:
        """Stop the background server."""
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=1.0)


def is_port_open(host: str, port: int) -> bool:
    """Return ``True`` when a TCP port is currently accepting connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def next_open_port(host: str, start_port: int, *, attempts: int = 512) -> int:
    """Find the first open port, starting at ``start_port``."""
    for port in range(start_port, start_port + attempts):
        if not is_port_open(host, port):
            return port
    raise RuntimeError(
        f"Could not find an open port in {start_port}-{start_port + attempts - 1}."
    )


def start_graph_server(
    directory: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    port_scan: bool = True,
    startup_wait: float = 1.0,
) -> GraphServer:
    """Start a local HTTP server rooted at ``directory`` and return a handle."""
    selected_port = next_open_port(host, port) if port_scan else port
    if not port_scan and is_port_open(host, selected_port):
        raise RuntimeError(
            f"TCP port {selected_port} is not available on {host}; "
            "pass --serve-port or allow auto-port scan."
        )

    serving_directory = Path(directory).resolve()
    if not serving_directory.is_dir():
        raise RuntimeError(f"Serve directory not found: {serving_directory}")

    command = [
        sys.executable,
        "-m",
        "http.server",
        str(selected_port),
        "--bind",
        host,
        "--directory",
        str(serving_directory),
    ]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.perf_counter() + startup_wait
    while time.perf_counter() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Graph server process exited while starting on port {selected_port}."
            )
        if is_port_open(host, selected_port):
            return GraphServer(
                process=process,
                host=host,
                port=selected_port,
                directory=serving_directory,
            )
        time.sleep(0.05)

    process.terminate()
    raise RuntimeError(f"Graph server did not start in {startup_wait:.1f}s on port {selected_port}.")


__all__ = [
    "GraphServer",
    "is_port_open",
    "next_open_port",
    "start_graph_server",
]
