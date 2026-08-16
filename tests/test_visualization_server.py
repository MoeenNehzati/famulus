from __future__ import annotations

import importlib
import json

import pytest

server = importlib.import_module("officina.common.visualization.server")


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
