from __future__ import annotations

import importlib
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


class MissingFinalizerAPI(ModuleNotFoundError):
    """Expected RED checkpoint failure for the absent finalizer module."""


FINALIZER_XFAIL = pytest.mark.xfail(
    condition=importlib.util.find_spec("_extraction_finalizer") is None,
    reason="Task 2 has not added _extraction_finalizer yet",
    raises=MissingFinalizerAPI,
    strict=True,
)


def _finalize_extraction(**kwargs: Path | None) -> None:
    try:
        module = importlib.import_module("_extraction_finalizer")
    except ModuleNotFoundError as exc:
        if exc.name != "_extraction_finalizer":
            raise
        raise MissingFinalizerAPI(
            "missing _extraction_finalizer.finalize_extraction boundary: "
            f"{exc}"
        ) from exc
    finalizer = getattr(module, "finalize_extraction", None)
    assert callable(finalizer), (
        "missing finalize_extraction(draft_path=..., tex_entrypoint=..., "
        "output_path=..., label_map_path=...)"
    )
    finalizer(**kwargs)


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


@FINALIZER_XFAIL
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

    _finalize_extraction(
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


@FINALIZER_XFAIL
def test_finalizer_rejects_unresolved_graph_visible_project_macro(
    tmp_path: Path,
) -> None:
    project = tmp_path / "unresolved-project"
    entrypoint = _write_tex(project, r"\newcommand{\KnownSprig}{\mathbb{K}}")
    draft = _write_draft(project / "draft.json", r"$\MissingNebula{x}$")

    with pytest.raises(ValueError) as caught:
        _finalize_extraction(
            draft_path=draft,
            tex_entrypoint=entrypoint,
            output_path=project / "canonical.json",
            label_map_path=None,
        )

    message = str(caught.value)
    assert "unresolved" in message.lower()
    assert "MissingNebula" in message
    assert str(entrypoint) in message


@FINALIZER_XFAIL
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
        _finalize_extraction(
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


@FINALIZER_XFAIL
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
        _finalize_extraction(
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


@FINALIZER_XFAIL
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

    _finalize_extraction(
        draft_path=draft,
        tex_entrypoint=entrypoint,
        output_path=canonical,
        label_map_path=None,
    )

    payload = json.loads(canonical.read_text(encoding="utf-8"))
    assert _mathjax_macros(payload)["ConcordPair"] == ["#1+#2", 2]


@FINALIZER_XFAIL
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
        _finalize_extraction(
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
