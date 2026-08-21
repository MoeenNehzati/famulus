#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_SRC = SKILL_DIR.parents[2] / "src"
SCRIPT_DIR = SKILL_DIR
FIXTURE_DIR = SKILL_DIR / "tests" / "fixtures" / "macro-paper"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SCRIPT_DIR))

if __package__ and __package__.count(".") >= 1:
    from .. import _tex_macro_reader as macro_reader
    from .._tex_macro_reader import default_output_path, dependency_closure, extract_macros
else:
    import _tex_macro_reader as macro_reader  # noqa: E402
    from _tex_macro_reader import default_output_path, dependency_closure, extract_macros  # noqa: E402


def script_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (env.get("PYTHONPATH"), str(REPO_SRC)) if part
    )
    return env


def bind_graph_to_semantic_ir(graph_path: Path) -> Path:
    """Attach a fresh semantic-artifact hash required by the renderer."""
    semantic_path = graph_path.with_name("semantic.json")
    semantic_path.write_text("{}\n", encoding="utf-8")
    graph_payload = json.loads(graph_path.read_text(encoding="utf-8"))
    graph_payload.setdefault("metadata", {})["semantic_ir_sha256"] = hashlib.sha256(
        semantic_path.read_bytes()
    ).hexdigest()
    graph_path.write_text(json.dumps(graph_payload), encoding="utf-8")
    return semantic_path


class MathJaxMacroExtractionTest(unittest.TestCase):
    def test_does_not_emit_identity_definitions_for_native_symbols(self) -> None:
        macros = dependency_closure(
            {"projectmacro": "\\Pi_x", "Pi": "\\Pi"},
            roots=["projectmacro"],
        )

        self.assertEqual(macros, {"projectmacro": "\\Pi_x"})

    def test_does_not_override_mathjax_commands_with_distribution_internals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                "\\usepackage{amsmath}\n"
                "The sequence is $x_1, \\dots, x_n$ and $\\sum_i x_i$.\n",
                encoding="utf-8",
            )

            macros = extract_macros(entrypoint)

            self.assertNotIn("sum", macros)
            self.assertNotIn("dotsi", macros)
            self.assertNotIn("vdots", macros)

    def test_resolves_bold_declared_symbol_by_font_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "custom.cls").write_text(
                "\\DeclareSymbolFont{bsymbols}{OMS}{cmsy}{b}{n}\n"
                "\\DeclareMathSymbol{\\BoldNabla}{\\mathord}{bsymbols}{\"72}\n",
                encoding="utf-8",
            )
            entrypoint = root / "main.tex"
            entrypoint.write_text(
                "\\documentclass{custom}\n"
                "The gradient is $\\BoldNabla f$.\n",
                encoding="utf-8",
            )

            macro_reader.canonical_math_symbols.cache_clear()
            self.addCleanup(macro_reader.canonical_math_symbols.cache_clear)
            with mock.patch.object(
                macro_reader, "tex_distribution_path", return_value=None
            ):
                macros = extract_macros(entrypoint)

            self.assertEqual(macros["BoldNabla"], "\\boldsymbol{\\nabla}")

    def test_does_not_treat_class_implementation_math_as_document_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "custom.cls").write_text(
                "\\newcommand{\\LayoutMacro}{Text $\\LayoutMacro$}\n",
                encoding="utf-8",
            )
            entrypoint = root / "main.tex"
            entrypoint.write_text(
                "\\documentclass{custom}\nDocument math is $x+y$.\n",
                encoding="utf-8",
            )

            macros = extract_macros(entrypoint)

            self.assertNotIn("LayoutMacro", macros)

    def test_resolves_referenced_macro_through_recursive_tex_package_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            package_dir = root / "texmf" / "tex" / "latex" / "macrofixture"
            project.mkdir()
            package_dir.mkdir(parents=True)
            (package_dir / "outerstyle.sty").write_text(
                "\\RequirePackage{innerstyle}\n",
                encoding="utf-8",
            )
            (package_dir / "innerstyle.sty").write_bytes(
                b"% Schr\xf6dinger notation\n"
                b"\\DeclareRobustCommand\\vectorstyle{internal implementation}\n"
                b"\\let\\boldsymbol\\vectorstyle\n"
                b"\\newcommand{\\layoutcommand}[1]{#1}\n"
                b"\\newcommand{\\pair}[2]{(#1,#2)}\n"
            )
            entrypoint = project / "main.tex"
            entrypoint.write_text(
                "\\usepackage{outerstyle}\n"
                "\\newcommand{\\vx}{\\vectorstyle{x}}\n"
                "\\layoutcommand{Heading}\n"
                "The vector is $\\vx$ and $\\pair{a}{b}$.\n",
                encoding="utf-8",
            )

            with mock.patch.dict(
                os.environ,
                {"TEXINPUTS": f"{root / 'texmf'}//:"},
            ), mock.patch.object(
                macro_reader,
                "tex_distribution_path",
                side_effect=lambda filename: {
                    "outerstyle.sty": package_dir / "outerstyle.sty",
                    "innerstyle.sty": package_dir / "innerstyle.sty",
                }.get(filename),
            ):
                macros = extract_macros(entrypoint)

            self.assertEqual(macros["vectorstyle"], [1, "\\boldsymbol{#1}"])
            self.assertEqual(macros["vx"], "\\vectorstyle{x}")
            # Installed package commands remain MathJax's responsibility unless
            # a project-owned macro depends on them. Promoting them as roots can
            # overwrite native MathJax commands with TeX-internal definitions.
            self.assertNotIn("pair", macros)
            self.assertNotIn("layoutcommand", macros)

    def test_extracts_recursive_and_mid_document_macros(self) -> None:
        macros = extract_macros(FIXTURE_DIR / "main.tex")

        self.assertEqual(macros["R"], "\\mathbb{R}")
        self.assertEqual(macros["BFn"], "\\mathbf{n}")
        self.assertEqual(macros["BFnabla"], "\\boldsymbol{\\nabla}")
        self.assertNotIn("dotsi", macros)
        self.assertNotIn("sum", macros)
        self.assertEqual(macros["ev"], "\\operatorname{ev}")
        self.assertEqual(macros["vQ"], "\\mathbf{Q}")
        self.assertEqual(macros["TC"], "\\operatorname{TC}")
        self.assertEqual(macros["QTC"], "\\vQ^{\\Pi_{\\TC_X}}")
        self.assertNotIn("Pi", macros)
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
            semantic_ir = bind_graph_to_semantic_ir(graph)
            html_out = work / "_build" / "graph.html"
            macro_file = work / "_build" / "main-mathjax-macros.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "_graph_builder.py"),
                    str(graph),
                    "--semantic-ir",
                    str(semantic_ir),
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
            self.assertIn('"BFnabla": "\\\\boldsymbol{\\\\nabla}"', html)
            self.assertNotIn('"dotsi":', html)
            self.assertNotIn('"sum":', html)
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
            semantic_ir = bind_graph_to_semantic_ir(graph)
            html_out = work / "_build" / "graph.html"
            macro_file = work / "_build" / "main-mathjax-macros.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "_graph_builder.py"),
                    str(graph),
                    "--semantic-ir",
                    str(semantic_ir),
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

    def test_renderer_rejects_canonical_graph_from_another_semantic_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "macro-paper"
            shutil.copytree(FIXTURE_DIR, work)
            graph = work / "graph.json"
            semantic_ir = bind_graph_to_semantic_ir(graph)
            semantic_ir.write_text('{"different": true}\n', encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "_graph_builder.py"),
                    str(graph),
                    "--semantic-ir",
                    str(semantic_ir),
                    "--html-out",
                    str(work / "_build" / "graph.html"),
                ],
                check=False,
                text=True,
                capture_output=True,
                env=script_env(),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match the supplied semantic IR", result.stderr)


if __name__ == "__main__":
    unittest.main()
