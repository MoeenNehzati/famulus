#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

import pytest


SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_SRC = SKILL_DIR.parents[2] / "src"
SCRIPT_DIR = SKILL_DIR
FIXTURE_DIR = SKILL_DIR / "tests" / "fixtures" / "macro-paper"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SCRIPT_DIR))

if __package__ and __package__.count(".") >= 1:
    from .. import _tex_macro_reader
    from .._tex_macro_reader import default_output_path, extract_macros
else:
    import _tex_macro_reader  # noqa: E402
    from _tex_macro_reader import default_output_path, extract_macros  # noqa: E402


def script_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (env.get("PYTHONPATH"), str(REPO_SRC)) if part
    )
    return env


class MissingRenderableExtractorAPI(AssertionError):
    """Expected RED checkpoint failure for the absent relevant extractor."""


EXTRACTOR_XFAIL = pytest.mark.xfail(
    condition=not callable(getattr(_tex_macro_reader, "extract_renderable_macros", None)),
    reason="Task 2 has not added extract_renderable_macros yet",
    raises=MissingRenderableExtractorAPI,
    strict=True,
)


class MathJaxMacroExtractionTest(unittest.TestCase):
    SYNTHETIC_NAMES = (
        "RootBloom",
        "NestedQuill",
        "PairWeave",
        "ShadeFold",
        "SupportSpore",
        "IdleComet",
        "CopperSeed",
        "TangentNest",
        "KnownSprig",
        "MissingNebula",
        "LoopAsh",
        "LoopFern",
        "ConflictArc",
        "ConcordPair",
        "SingleFlare",
        "DetachedGlyph",
        "DetachedMap",
    )

    def renderable_extractor(self) -> Callable[..., dict[str, object]]:
        extractor = getattr(_tex_macro_reader, "extract_renderable_macros", None)
        if not callable(extractor):
            raise MissingRenderableExtractorAPI(
                "missing extract_renderable_macros(tex_entrypoint=..., graph_text=...) "
                "for relevant project-macro closure"
            )
        return extractor

    def test_extracts_recursive_and_mid_document_macros(self) -> None:
        macros = extract_macros(FIXTURE_DIR / "main.tex")

        self.assertEqual(macros["R"], "\\mathbb{R}")
        self.assertEqual(macros["BFn"], "\\mathbf{n}")
        self.assertEqual(macros["ev"], "\\operatorname{ev}")
        self.assertEqual(macros["vQ"], "\\mathbf{Q}")
        self.assertEqual(macros["TC"], "\\operatorname{TC}")
        self.assertEqual(macros["QTC"], "\\vQ^{\\Pi_{\\TC_X}}")
        self.assertEqual(macros["MidMacro"], "\\QTC\\circ\\ev")
        self.assertEqual(macros["InnerMacro"], "\\operatorname{inner}")
        self.assertEqual(macros["OuterMacro"], [2, "\\InnerMacro(#1,#2)+\\QTC"])

    def test_cli_writes_default_build_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "macro-paper"
            shutil.copytree(FIXTURE_DIR, work)
            entrypoint = work / "main.tex"
            expected = default_output_path(entrypoint)

            result = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "_tex_macro_reader.py"), str(entrypoint)],
                check=True,
                text=True,
                capture_output=True,
                env=script_env(),
            )
            payload = json.loads(result.stdout)

            self.assertEqual(Path(payload["out"]), expected)
            self.assertTrue(expected.exists())
            written = json.loads(expected.read_text(encoding="utf-8"))
            self.assertIn("QTC", written)
            self.assertEqual(written["BFn"], "\\mathbf{n}")
            self.assertIn("OuterMacro", written)

    def test_renderer_generates_and_merges_default_macro_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "macro-paper"
            shutil.copytree(FIXTURE_DIR, work)
            graph = work / "graph.json"
            html_out = work / "_build" / "graph.html"
            macro_file = work / "_build" / "main-mathjax-macros.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "_graph_builder.py"),
                    str(graph),
                    "--tex-entry",
                    str(work / "main.tex"),
                    "--html-out",
                    str(html_out),
                ],
                check=True,
                text=True,
                capture_output=True,
                env=script_env(),
            )
            payload = json.loads(result.stdout)

            self.assertEqual(Path(payload["macro_file"]).resolve(), macro_file.resolve())
            self.assertTrue(macro_file.exists())
            self.assertTrue(html_out.exists())
            html = html_out.read_text(encoding="utf-8")
            self.assertIn('"BFn": "\\\\mathbf{n}"', html)
            self.assertIn('"QTC": "\\\\vQ^{\\\\Pi_{\\\\TC_X}}"', html)
            self.assertIn('"OuterMacro": [', html)
            self.assertIn('"ev": "\\\\operatorname{eval}"', html)

    def test_renderer_uses_document_source_file_for_default_macros(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "macro-paper"
            shutil.copytree(FIXTURE_DIR, work)
            graph = work / "graph.json"
            graph_payload = json.loads(graph.read_text(encoding="utf-8"))
            graph_payload["document"].pop("source_entrypoint")
            graph_payload["document"]["source_file"] = str(work / "main.tex")
            graph.write_text(json.dumps(graph_payload), encoding="utf-8")
            html_out = work / "_build" / "graph.html"
            macro_file = work / "_build" / "main-mathjax-macros.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "_graph_builder.py"),
                    str(graph),
                    "--html-out",
                    str(html_out),
                ],
                check=True,
                text=True,
                capture_output=True,
                env=script_env(),
            )
            payload = json.loads(result.stdout)

            self.assertEqual(Path(payload["macro_file"]).resolve(), macro_file.resolve())
            self.assertGreater(payload["macros_from_file"], 0)
            html = html_out.read_text(encoding="utf-8")
            self.assertIn('"QTC": "\\\\vQ^{\\\\Pi_{\\\\TC_X}}"', html)

    @EXTRACTOR_XFAIL
    def test_extracts_only_the_recursive_closure_used_by_graph_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"""
                \newcommand{\RootBloom}{\mathbb{B}}
                \newcommand{\NestedQuill}{\RootBloom^{\star}}
                \newcommand{\PairWeave}[2]{\langle #1,#2\rangle}
                \newcommand{\SupportSpore}{\mathcal{S}}
                \newcommand{\ShadeFold}[2][q]{#1+\PairWeave{#2}{\SupportSpore}}
                \newcommand{\IdleComet}{\mathrm{idle}}
                """,
                encoding="utf-8",
            )

            macros = self.renderable_extractor()(
                tex_entrypoint=entrypoint,
                graph_text=[
                    r"$\RootBloom+\NestedQuill+\PairWeave{x}{y}+\ShadeFold[z]{w}$"
                ],
            )

            self.assertEqual(
                macros,
                {
                    "RootBloom": r"\mathbb{B}",
                    "NestedQuill": r"\RootBloom^{\star}",
                    "PairWeave": [r"\langle #1,#2\rangle", 2],
                    "SupportSpore": r"\mathcal{S}",
                    "ShadeFold": [r"#1+\PairWeave{#2}{\SupportSpore}", 2, "q"],
                },
            )

    @EXTRACTOR_XFAIL
    def test_same_extractor_handles_a_disjoint_macro_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "second.tex"
            entrypoint.write_text(
                r"""
                \newcommand{\CopperSeed}{\mathcal{C}}
                \newcommand{\TangentNest}[1]{\CopperSeed_{#1}}
                """,
                encoding="utf-8",
            )

            macros = self.renderable_extractor()(
                tex_entrypoint=entrypoint,
                graph_text=[r"The second fixture uses $\TangentNest{k}$ only."],
            )

            self.assertEqual(
                macros,
                {
                    "CopperSeed": r"\mathcal{C}",
                    "TangentNest": [r"\CopperSeed_{#1}", 1],
                },
            )

    def test_synthetic_macro_names_are_fixture_only(self) -> None:
        production_paths = list(SCRIPT_DIR.glob("*.py"))
        visualization_root = REPO_SRC / "officina" / "visualization"
        production_paths.extend(
            path
            for path in visualization_root.rglob("*")
            if path.suffix in {".py", ".js", ".html"} and "vendor" not in path.parts
        )

        for path in production_paths:
            source = path.read_text(encoding="utf-8")
            for name in self.SYNTHETIC_NAMES:
                self.assertNotIn(name, source, f"{name} was hard-coded in {path}")


if __name__ == "__main__":
    unittest.main()
