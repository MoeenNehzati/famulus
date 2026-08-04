#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_SRC = SKILL_DIR.parents[2] / "src"
SCRIPT_DIR = SKILL_DIR
FIXTURE_DIR = SKILL_DIR / "tests" / "fixtures" / "macro-paper"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SCRIPT_DIR))

if __package__ and __package__.count(".") >= 1:
    from .._tex_macro_reader import default_output_path, extract_macros
else:
    from _tex_macro_reader import default_output_path, extract_macros  # noqa: E402


def script_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_SRC)
    return env


class MathJaxMacroExtractionTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
