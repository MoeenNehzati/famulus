from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from .. import _graph_server


SOURCE_DECLARATION = (
    Path(__file__).resolve().parents[1]
    / "blueprints"
    / "rtx-graph-server.yaml"
)
SOURCE_INTERFACE = (
    "math-dependency-graph-rtx.source.rtx-graph-server."
    "interface.scripts-serve-graph"
)


class _StoppedServer:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        pass


def test_declared_readiness_prefix_matches_emitted_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(_graph_server, "ReusableThreadingHTTPServer", _StoppedServer)

    _graph_server.main(["--directory", str(tmp_path)])

    stdout = capsys.readouterr().out
    declaration = yaml.safe_load(SOURCE_DECLARATION.read_text(encoding="utf-8"))
    matcher = declaration["interfaces"][SOURCE_INTERFACE]["contract"]["execution"][
        "long_running"
    ]["ready_when"]["matcher"]
    assert matcher["kind"] == "regex"
    assert matcher["dialect"] == "python"
    assert matcher["matching"] == "prefix"
    assert re.match(matcher["pattern"], stdout) is not None
