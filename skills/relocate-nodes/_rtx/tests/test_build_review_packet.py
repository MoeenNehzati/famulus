from __future__ import annotations

import json
from pathlib import Path

from .._build_review_packet import Interface


def test_review_packet_interface_writes_only_external_output(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "notes.md").write_text("# Current\nUse old-node.\n", encoding="utf-8")
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "semantic_occurrences": [
                    {
                        "occurrence_id": "one",
                        "path": "notes.md",
                        "line": 2,
                        "match": "old-node",
                        "candidate": "new-node",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "packet.json"

    assert Interface().run(Interface().build_parser().parse_args([
        "--root", str(repository), "--report", str(report), "--output", str(output)
    ])) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["summary"] == {
        "occurrences": 1,
        "review_units": 1,
    }
