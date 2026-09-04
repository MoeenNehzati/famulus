from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest


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
        "document": {
            "title": "Detached renderer contract",
            "source_entrypoint": "stale/source/main.tex",
            "source_file": "stale/source/main.tex",
        },
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
                "connects_to": [
                    {"to": "middle-node", "type": "supports"},
                    {"to": "terminal-node", "type": "supports"},
                ],
            },
            {
                "id": "middle-node",
                "type": "lemma",
                "short_title": "Middle",
                "position": 1,
                "connects_to": [{"to": "terminal-node", "type": "supports"}],
            },
            {
                "id": "terminal-node",
                "type": "theorem",
                "short_title": "Terminal",
                "position": 2,
                "connects_to": [],
            },
        ],
    }
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _embedded_payload(html: str) -> dict:
    # Anchor on the next bootstrap declaration rather than a named one, so an
    # added const between docData and typeStyleCatalog does not widen the span.
    prefix = "    const docData = "
    suffix = ";\n    const "
    return json.loads(html.split(prefix, 1)[1].split(suffix, 1)[0])


def test_builder_renders_identical_html_from_copied_canonical_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "source-project"
    canonical = project / "canonical.json"
    _write_self_contained_graph(canonical)
    canonical_bytes = canonical.read_bytes()
    first_html = project / "graph.htm"

    detached = tmp_path / "detached" / "canonical.json"
    detached.parent.mkdir()
    shutil.copy2(canonical, detached)
    detached_bytes = detached.read_bytes()

    real_exists = Path.exists
    real_read_text = Path.read_text

    def reject_tex_exists(path: Path) -> bool:
        if path.suffix in {".tex", ".sty", ".cls"}:
            raise AssertionError(f"builder probed TeX source path: {path}")
        return real_exists(path)

    def reject_tex_read(path: Path, *args, **kwargs) -> str:
        if path.suffix in {".tex", ".sty", ".cls"}:
            raise AssertionError(f"builder read TeX source path: {path}")
        return real_read_text(path, *args, **kwargs)

    def reject_macro_reader(*args, **kwargs):
        raise AssertionError("builder invoked the TeX macro reader")

    with (
        mock.patch.object(Path, "exists", reject_tex_exists),
        mock.patch.object(Path, "read_text", reject_tex_read),
        mock.patch.object(
            graph_builder,
            "extract_macros",
            side_effect=reject_macro_reader,
            create=True,
        ),
        mock.patch.object(
            graph_builder,
            "write_macros",
            side_effect=reject_macro_reader,
            create=True,
        ),
        mock.patch(
            "officina.visualization.elk_html_renderer.time.time",
            return_value=1_700_000_000.0,
        ),
    ):
        graph_builder.main(
            [
                str(canonical),
                "--html-out",
                str(first_html),
                "--reduce-transitive-edges",
            ]
        )
    first_report = json.loads(capsys.readouterr().out)
    assert first_report["html"] == str(first_html.resolve())
    assert first_report["reduced"] is True
    assert first_report["removed_edges"] == 1
    expected_html = first_html.read_text(encoding="utf-8")
    reduced_entities = {
        entity["id"]: entity for entity in _embedded_payload(expected_html)["entities"]
    }
    assert reduced_entities["detached-node"]["connects_to"] == [
        {"to": "middle-node", "type": "supports"}
    ]
    assert reduced_entities["middle-node"]["connects_to"] == [
        {"to": "terminal-node", "type": "supports"}
    ]
    assert canonical.read_bytes() == canonical_bytes
    assert not first_html.with_suffix(".html").exists()
    assert not list(project.glob("*.rendered.json"))

    shutil.rmtree(project)
    detached_html = detached.parent / "graph.htm"
    with (
        mock.patch.object(Path, "exists", reject_tex_exists),
        mock.patch.object(Path, "read_text", reject_tex_read),
        mock.patch.object(
            graph_builder,
            "extract_macros",
            side_effect=reject_macro_reader,
            create=True,
        ),
        mock.patch.object(
            graph_builder,
            "write_macros",
            side_effect=reject_macro_reader,
            create=True,
        ),
        mock.patch(
            "officina.visualization.elk_html_renderer.time.time",
            return_value=1_700_000_000.0,
        ),
    ):
        graph_builder.main(
            [
                str(detached),
                "--html-out",
                str(detached_html),
                "--reduce-transitive-edges",
            ]
        )
    detached_report = json.loads(capsys.readouterr().out)
    assert detached_report["html"] == str(detached_html.resolve())
    assert detached_report["reduced"] is True
    assert detached_report["removed_edges"] == 1

    actual_html = detached_html.read_text(encoding="utf-8")
    assert actual_html == expected_html
    assert detached.read_bytes() == detached_bytes
    assert not detached_html.with_suffix(".html").exists()
    assert '"DetachedGlyph": "\\\\mathbb{D}"' in actual_html
    assert not list(detached.parent.glob("*mathjax-macros.json"))
    assert not list(detached.parent.glob("*.rendered.json"))


def test_builder_renders_the_default_quick_guide(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canonical = tmp_path / "project" / "canonical.json"
    _write_self_contained_graph(canonical)
    html_out = tmp_path / "project" / "graph.htm"

    graph_builder.main([str(canonical), "--html-out", str(html_out)])
    capsys.readouterr()

    html = html_out.read_text(encoding="utf-8")

    # The skill renderer carries the default guide, so the config must not be null.
    assert "const QUICK_GUIDE_CONFIG = null;" not in html
    assert '"read-graph"' in html
    assert "Nodes are items, and arrows and relations show how they connect." in html
    assert "Trace ancestors" in html
    assert "Trace successors" in html
    # Macros still arrive from the canonical JSON alone, not from TeX sources.
    assert '"DetachedGlyph": "\\\\mathbb{D}"' in html
