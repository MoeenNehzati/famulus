from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest import mock


RTX_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = RTX_DIR.parents[2]
REPO_SRC = REPO_ROOT / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(RTX_DIR))

import _graph_builder as graph_builder  # noqa: E402


def _write_self_contained_graph(path: Path) -> None:
    payload = {
        "schema_version": 2,
        "graph_kind": "math-dependency",
        "document": {"title": "Detached renderer contract"},
        "renderer_dependencies": [
            {
                "id": "mathjax",
                "version": "3",
                "configuration": {
                    "input": "tex",
                    "output": "svg",
                    "macros": {
                        "DetachedGlyph": r"\mathbb{D}",
                        "DetachedMap": [r"\DetachedGlyph_{#1}", 1],
                    },
                },
            }
        ],
        "entities": [
            {
                "id": "detached-node",
                "type": "definition",
                "short_title": r"$\DetachedMap{x}$",
                "description": "The only render input is this canonical JSON payload.",
                "position": 0,
                "connects_to": [],
            }
        ],
    }
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_builder_renders_identical_html_from_copied_canonical_json(
    tmp_path: Path,
    capsys,
) -> None:
    project = tmp_path / "source-project"
    canonical = project / "canonical.json"
    _write_self_contained_graph(canonical)
    first_html = project / "graph.html"

    detached = tmp_path / "detached" / "canonical.json"
    detached.parent.mkdir()
    shutil.copy2(canonical, detached)

    with mock.patch(
        "officina.visualization.elk_html_renderer.time.time",
        return_value=1_700_000_000.0,
    ):
        graph_builder.main([str(canonical), "--html-out", str(first_html)])
    capsys.readouterr()
    expected_html = first_html.read_text(encoding="utf-8")

    shutil.rmtree(project)
    detached_html = detached.parent / "graph.html"
    with mock.patch(
        "officina.visualization.elk_html_renderer.time.time",
        return_value=1_700_000_000.0,
    ):
        graph_builder.main([str(detached), "--html-out", str(detached_html)])
    capsys.readouterr()

    actual_html = detached_html.read_text(encoding="utf-8")
    assert actual_html == expected_html
    assert '"DetachedGlyph": "\\\\mathbb{D}"' in actual_html
    assert not list(detached.parent.glob("*mathjax-macros.json"))
