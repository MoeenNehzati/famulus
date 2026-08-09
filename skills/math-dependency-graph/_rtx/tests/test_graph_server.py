from __future__ import annotations

from pathlib import Path
import sys

import pytest

TEST_ROOT = Path(__file__).resolve().parents[4] / "tests"
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from test_support.runtime_module import load_runtime_module


server = load_runtime_module(Path(__file__).resolve().parents[1] / "_graph_server.py")


@pytest.mark.parametrize("port", ["0", "65536"])
def test_graph_server_rejects_ports_outside_tcp_range(port: str) -> None:
    with pytest.raises(SystemExit):
        server.parse_args(["--port", port])


def test_graph_server_accepts_highest_tcp_port() -> None:
    assert server.parse_args(["--port", "65535"]).port == 65535
