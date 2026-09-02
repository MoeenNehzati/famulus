from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest import mock

import jsonschema
import pytest


RTX_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = RTX_DIR.parents[2]
REPO_SRC = REPO_ROOT / "src"
SCHEMA_PATH = REPO_SRC / "officina" / "visualization" / "graph_specification.schema.json"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(RTX_DIR))

import _tex_macro_reader  # noqa: E402
from _extraction_finalizer import finalize_extraction  # noqa: E402
from officina.visualization.base_renderer_cli import main as render_canonical_html  # noqa: E402


PRIMARY_EXPRESSION = (
    r"$\RootBloom+\NestedQuill+\PairWeave{x}{y}+\ShadeFold[z]{w}$"
)

PRIMARY_MACROS = {
    "RootBloom": r"\mathbb{B}",
    "NestedQuill": r"\RootBloom^{\star}",
    "PairWeave": [r"\langle #1,#2\rangle", 2],
    "SupportSpore": r"\mathcal{S}",
    "AmbientThread": r"\mathcal{A}",
    "ShadeFold": [
        r"#1+\PairWeave{#2}{\SupportSpore}+\AmbientThread",
        2,
        "q",
    ],
}


def _write_tex(project: Path, source: str) -> Path:
    project.mkdir(parents=True)
    entrypoint = project / "main.tex"
    entrypoint.write_text(source, encoding="utf-8")
    return entrypoint


def _write_primary_tex_project(project: Path) -> tuple[Path, Path]:
    parts = project / "parts"
    texmf = project.parent / "controlled-texmf"
    parts.mkdir(parents=True)
    texmf.mkdir()
    entrypoint = project / "main.tex"
    entrypoint.write_text(
        r"""
        \documentclass{localframe}
        \usepackage{localtones,ambientweave}
        \input{parts/direct}
        \include{parts/nested}
        """,
        encoding="utf-8",
    )
    (project / "localframe.cls").write_text(
        r"\newcommand{\RootBloom}{\mathbb{B}}",
        encoding="utf-8",
    )
    (project / "localtones.sty").write_text(
        r"""
        \newcommand{\PairWeave}[2]{\langle #1,#2\rangle}
        \newcommand{\SupportSpore}{\mathcal{S}}
        """,
        encoding="utf-8",
    )
    (parts / "direct.tex").write_text(
        r"\newcommand{\NestedQuill}{\RootBloom^{\star}}",
        encoding="utf-8",
    )
    (parts / "nested.tex").write_text(
        r"""
        \newcommand{\ShadeFold}[2][q]{#1+\PairWeave{#2}{\SupportSpore}+\AmbientThread}
        \newcommand{\IdleComet}{\mathrm{idle}}
        """,
        encoding="utf-8",
    )
    ambient_package = texmf / "ambientweave.sty"
    ambient_package.write_text(
        r"\newcommand{\AmbientThread}{\mathcal{A}}",
        encoding="utf-8",
    )
    return entrypoint, ambient_package


def _write_draft(
    path: Path,
    expression: str,
    *,
    renderer_dependencies: list[dict] | None = None,
) -> Path:
    payload = {
        "schema_version": 2,
        "graph_kind": "math-dependency",
        "document": {"title": "Synthetic macro contract"},
        "entities": [
            {
                "id": "synthetic-result",
                "type": "definition",
                "short_title": expression,
                "description": f"Graph-visible statement: {expression}",
                "position": 0,
                "connects_to": [],
            }
        ],
    }
    if renderer_dependencies is not None:
        payload["renderer_dependencies"] = renderer_dependencies
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _mathjax_macros(payload: dict) -> dict:
    matches = [
        dependency
        for dependency in payload.get("renderer_dependencies", [])
        if dependency.get("id") == "mathjax"
    ]
    assert len(matches) == 1
    return matches[0]["configuration"]["macros"]


def test_finalizer_writes_schema_valid_detachable_relevant_macro_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "tex-project"
    entrypoint, ambient_package = _write_primary_tex_project(project)
    draft = _write_draft(project / "draft.json", PRIMARY_EXPRESSION)
    canonical = project / "canonical.json"
    original_resolver = _tex_macro_reader.resolve_tex_path

    def controlled_resolver(
        include_name: str,
        current_dir: Path,
        suffix: str = ".tex",
    ) -> Path:
        if include_name == "ambientweave":
            return ambient_package
        return original_resolver(include_name, current_dir, suffix)

    monkeypatch.setattr(_tex_macro_reader, "resolve_tex_path", controlled_resolver)

    finalize_extraction(
        draft_path=draft,
        tex_entrypoint=entrypoint,
        output_path=canonical,
        label_map_path=None,
    )

    payload = json.loads(canonical.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(schema).validate(payload)
    assert _mathjax_macros(payload) == PRIMARY_MACROS
    assert "SupportSpore" in _mathjax_macros(payload)
    assert "IdleComet" not in _mathjax_macros(payload)

    detached = tmp_path / "detached" / "canonical.json"
    detached.parent.mkdir()
    shutil.copy2(canonical, detached)
    shutil.rmtree(project)
    shutil.rmtree(ambient_package.parent)

    detached_payload = json.loads(detached.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(schema).validate(detached_payload)
    assert _mathjax_macros(detached_payload) == PRIMARY_MACROS
    detached_html = detached.with_suffix(".html")
    with mock.patch(
        "officina.visualization.elk_html_renderer.time.time",
        return_value=1_700_000_000.0,
    ):
        render_canonical_html([str(detached), "--html-out", str(detached_html)])
    capsys.readouterr()
    html = detached_html.read_text(encoding="utf-8")
    assert '"AmbientThread": "\\\\mathcal{A}"' in html
    assert '"ShadeFold": [' in html


def test_finalizer_rejects_unresolved_graph_visible_project_macro(
    tmp_path: Path,
) -> None:
    project = tmp_path / "unresolved-project"
    entrypoint = _write_tex(project, r"\newcommand{\KnownSprig}{\mathbb{K}}")
    draft = _write_draft(project / "draft.json", r"$\MissingNebula{x}$")

    with pytest.raises(ValueError) as caught:
        finalize_extraction(
            draft_path=draft,
            tex_entrypoint=entrypoint,
            output_path=project / "canonical.json",
            label_map_path=None,
        )

    message = str(caught.value)
    assert "unresolved" in message.lower()
    assert "MissingNebula" in message
    assert str(entrypoint) in message


def test_finalizer_rejects_cyclic_relevant_definitions(tmp_path: Path) -> None:
    project = tmp_path / "cycle-project"
    entrypoint = _write_tex(
        project,
        r"""
        \newcommand{\LoopAsh}{\LoopFern}
        \newcommand{\LoopFern}{\LoopAsh}
        """,
    )
    draft = _write_draft(project / "draft.json", r"$\LoopAsh$")

    with pytest.raises(ValueError) as caught:
        finalize_extraction(
            draft_path=draft,
            tex_entrypoint=entrypoint,
            output_path=project / "canonical.json",
            label_map_path=None,
        )

    message = str(caught.value)
    assert "cyclic" in message.lower() or "cycle" in message.lower()
    assert "LoopAsh" in message
    assert "LoopFern" in message
    assert str(entrypoint) in message


def test_finalizer_rejects_conflicting_preexisting_macro_value(
    tmp_path: Path,
) -> None:
    project = tmp_path / "conflict-project"
    entrypoint = _write_tex(
        project,
        r"\newcommand{\ConflictArc}[2]{#1+#2}",
    )
    dependencies = [
        {
            "id": "mathjax",
            "version": "3",
            "configuration": {
                "input": "tex",
                "output": "svg",
                "macros": {"ConflictArc": ["#1-#2", 2]},
            },
        }
    ]
    draft = _write_draft(
        project / "draft.json",
        r"$\ConflictArc{x}{y}$",
        renderer_dependencies=dependencies,
    )

    with pytest.raises(ValueError) as caught:
        finalize_extraction(
            draft_path=draft,
            tex_entrypoint=entrypoint,
            output_path=project / "canonical.json",
            label_map_path=None,
        )

    message = str(caught.value)
    assert "conflict" in message.lower()
    assert "ConflictArc" in message
    assert str(draft) in message
    assert str(entrypoint) in message


def test_finalizer_accepts_semantically_identical_legacy_and_native_tuples(
    tmp_path: Path,
) -> None:
    project = tmp_path / "tuple-project"
    entrypoint = _write_tex(
        project,
        r"\newcommand{\ConcordPair}[2]{#1+#2}",
    )
    dependencies = [
        {
            "id": "mathjax",
            "version": "3",
            "configuration": {
                "input": "tex",
                "output": "svg",
                "macros": {"ConcordPair": [2, "#1+#2"]},
            },
        }
    ]
    draft = _write_draft(
        project / "draft.json",
        r"$\ConcordPair{x}{y}$",
        renderer_dependencies=dependencies,
    )
    canonical = project / "canonical.json"

    finalize_extraction(
        draft_path=draft,
        tex_entrypoint=entrypoint,
        output_path=canonical,
        label_map_path=None,
    )

    payload = json.loads(canonical.read_text(encoding="utf-8"))
    assert _mathjax_macros(payload)["ConcordPair"] == ["#1+#2", 2]


def test_finalizer_rejects_duplicate_mathjax_dependencies(tmp_path: Path) -> None:
    project = tmp_path / "duplicate-project"
    entrypoint = _write_tex(project, r"\newcommand{\SingleFlare}{\mathbb{F}}")
    dependencies = [
        {"id": "mathjax", "version": "3", "configuration": {"input": "tex"}},
        {"id": "mathjax", "version": "3", "configuration": {"output": "svg"}},
    ]
    draft = _write_draft(
        project / "draft.json",
        r"$\SingleFlare$",
        renderer_dependencies=dependencies,
    )

    with pytest.raises(ValueError) as caught:
        finalize_extraction(
            draft_path=draft,
            tex_entrypoint=entrypoint,
            output_path=project / "canonical.json",
            label_map_path=None,
        )

    message = str(caught.value)
    assert "duplicate" in message.lower()
    assert "mathjax" in message.lower()
    assert "renderer_dependencies[0]" in message
    assert "renderer_dependencies[1]" in message


def test_finalizer_applies_labels_presentation_and_normalizes_embedded_macros(
    tmp_path: Path,
) -> None:
    project = tmp_path / "presentation-project"
    entrypoint = _write_tex(project, "Fixture without custom commands.")
    draft = project / "draft.json"
    draft_payload = {
        "schema_version": 2,
        "graph_kind": "math-dependency",
        "renderer_dependencies": [
            {
                "id": "mathjax",
                "version": "3",
                "configuration": {
                    "input": " delicate value replaced by schema default ",
                    "output": "svg",
                    "macros": {"EmbeddedPair": [1, "#1"]},
                },
            }
        ],
        "entities": [
            {
                "id": "premise",
                "type": "assumption",
                "short_title": r"See \eqref{eq:fixture}",
                "position": 0,
                "tex_label": "assumption:fixture",
                "connects_to": [
                    {"to": "result", "type": "supports"},
                ],
            },
            {
                "id": "result",
                "type": "result",
                "short_title": "Result",
                "position": 1,
                "connects_to": [],
            },
        ],
    }
    draft.write_text(json.dumps(draft_payload, indent=2) + "\n", encoding="utf-8")
    label_map = project / "labels.json"
    label_map.write_text(
        json.dumps(
            {
                "assumption:fixture": {"ref": "A.1"},
                "eq:fixture": {"ref": "7"},
            }
        ),
        encoding="utf-8",
    )
    canonical = project / "canonical.json"

    finalize_extraction(
        draft_path=draft,
        tex_entrypoint=entrypoint,
        output_path=canonical,
        label_map_path=label_map,
    )

    payload = json.loads(canonical.read_text(encoding="utf-8"))
    premise = payload["entities"][0]
    assert premise["ref"] == "A.1"
    assert premise["short_title"] == "See (7)"
    assert {item["id"] for item in payload["edge_categories"]} == {
        "supports",
        "exemplifies",
    }
    assert "edge_styles" in payload["ui"]
    assert "relation_semantics" in payload
    dependency = payload["renderer_dependencies"][0]
    assert dependency["version"] == "3"
    assert dependency["configuration"]["input"] == "tex"
    assert dependency["configuration"]["output"] == "svg"
    assert dependency["configuration"]["macros"]["EmbeddedPair"] == ["#1", 1]
    assert json.loads(draft.read_text(encoding="utf-8")) == draft_payload


def test_finalizer_accepts_embedded_root_and_extracts_its_source_dependency(
    tmp_path: Path,
) -> None:
    project = tmp_path / "embedded-root-project"
    entrypoint = _write_tex(
        project,
        r"\newcommand{\SourceLeaf}{\mathcal{L}}",
    )
    draft = _write_draft(
        project / "draft.json",
        r"$\EmbeddedRoot{x}$",
        renderer_dependencies=[
            {
                "id": "mathjax",
                "version": "3",
                "configuration": {
                    "input": "tex",
                    "output": "svg",
                    "macros": {"EmbeddedRoot": r"\SourceLeaf"},
                },
            }
        ],
    )
    canonical = project / "canonical.json"

    finalize_extraction(
        draft_path=draft,
        tex_entrypoint=entrypoint,
        output_path=canonical,
        label_map_path=None,
    )

    assert _mathjax_macros(json.loads(canonical.read_text(encoding="utf-8"))) == {
        "EmbeddedRoot": r"\SourceLeaf",
        "SourceLeaf": r"\mathcal{L}",
    }


def test_finalizer_validates_before_atomically_replacing_existing_output(
    tmp_path: Path,
) -> None:
    project = tmp_path / "invalid-project"
    entrypoint = _write_tex(project, "Fixture without custom commands.")
    draft = _write_draft(project / "draft.json", "No custom command.")
    payload = json.loads(draft.read_text(encoding="utf-8"))
    payload.pop("schema_version")
    draft.write_text(json.dumps(payload), encoding="utf-8")
    canonical = project / "canonical.json"
    canonical.write_text("preserved canonical bytes\n", encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        finalize_extraction(
            draft_path=draft,
            tex_entrypoint=entrypoint,
            output_path=canonical,
            label_map_path=None,
        )

    message = str(caught.value)
    assert "schema" in message.lower()
    assert "schema_version" in message
    assert canonical.read_text(encoding="utf-8") == "preserved canonical bytes\n"
