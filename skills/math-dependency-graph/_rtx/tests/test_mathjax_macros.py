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
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_SRC = SKILL_DIR.parents[2] / "src"
SCRIPT_DIR = SKILL_DIR
FIXTURE_DIR = SKILL_DIR / "tests" / "fixtures" / "macro-paper"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SCRIPT_DIR))

if __package__ and __package__.count(".") >= 1:
    from .. import _tex_macro_reader
    from .._tex_macro_reader import (
        default_output_path,
        extract_macros,
        extract_renderable_macros,
    )
else:
    import _tex_macro_reader  # noqa: E402
    from _tex_macro_reader import (  # noqa: E402
        default_output_path,
        extract_macros,
        extract_renderable_macros,
    )


def script_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (env.get("PYTHONPATH"), str(REPO_SRC)) if part
    )
    return env


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
        "AmbientThread",
        "DistroAlias",
        "DistroWrap",
        "DuplicatePulse",
        "CommonShadow",
        "ProjectAlias",
        "ProjectTarget",
        "MissingLeaf",
        "DistroClassMacro",
        "AfterInclude",
        "WideProjectMacro",
    )

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
        self.assertEqual(macros["OuterMacro"], ["\\InnerMacro(#1,#2)+\\QTC", 2])

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
            self.assertIn('"ev": "\\\\operatorname{ev}"', html)

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

    def test_extracts_only_the_recursive_closure_used_by_graph_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            parts = project / "parts"
            texmf = Path(tmp) / "controlled-texmf"
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
            original_resolver = _tex_macro_reader.resolve_tex_path

            def controlled_resolver(
                include_name: str,
                current_dir: Path,
                suffix: str = ".tex",
            ) -> Path:
                if include_name == "ambientweave":
                    return ambient_package
                return original_resolver(include_name, current_dir, suffix)

            with mock.patch.object(
                _tex_macro_reader,
                "resolve_tex_path",
                side_effect=controlled_resolver,
            ):
                macros = extract_renderable_macros(
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
                    "AmbientThread": r"\mathcal{A}",
                    "ShadeFold": [
                        r"#1+\PairWeave{#2}{\SupportSpore}+\AmbientThread",
                        2,
                        "q",
                    ],
                },
            )

    def test_same_extractor_handles_a_disjoint_macro_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            entrypoint = project / "second.tex"
            entrypoint.write_text(
                r"""
                \usepackage{copperbase}
                \input{tangent-defs}
                """,
                encoding="utf-8",
            )
            (project / "copperbase.sty").write_text(
                r"\newcommand{\CopperSeed}{\mathcal{C}}",
                encoding="utf-8",
            )
            (project / "tangent-defs.tex").write_text(
                r"\newcommand{\TangentNest}[1]{\CopperSeed_{#1}}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
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

    def test_extractor_resolves_distribution_alias_from_legacy_encoded_package(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(
                r"\usepackage{distro-alias}\newcommand{\DistroWrap}[1]{\DistroAlias{#1}}",
                encoding="utf-8",
            )
            package = texmf / "distro-alias.sty"
            package.write_bytes(
                b"% legacy package: \xe9\n\\let\\DistroAlias\\boldsymbol\n"
            )

            def controlled_distribution(filename: str) -> Path | None:
                return package if filename == "distro-alias.sty" else None

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\DistroWrap{x}$"],
                )

            self.assertEqual(
                macros,
                {
                    "DistroAlias": r"\boldsymbol",
                    "DistroWrap": [r"\DistroAlias{#1}", 1],
                },
            )

    def test_source_definition_shadows_a_common_mathjax_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\newcommand{\bm}[1]{\mathrm{local}(#1)}"
                r"\newcommand{\CommonShadow}[1]{\bm{#1}}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\CommonShadow{x}$"],
            )

            self.assertEqual(
                macros,
                {
                    "bm": [r"\mathrm{local}(#1)", 1],
                    "CommonShadow": [r"\bm{#1}", 1],
                },
            )

    def test_common_mathjax_commands_do_not_require_project_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text("No project macros.", encoding="utf-8")

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[
                    r"$\mathfrak{F}+\underbrace{x}_{y}+\overset{a}{b}$",
                ],
            )

            self.assertEqual(macros, {})

    def test_project_to_project_let_alias_is_general(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\newcommand{\ProjectTarget}[2]{#1+#2}"
                r"\let\ProjectAlias\ProjectTarget",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\ProjectAlias{x}{y}$"],
            )

            self.assertEqual(
                macros,
                {
                    "ProjectTarget": ["#1+#2", 2],
                    "ProjectAlias": r"\ProjectTarget",
                },
            )

    def test_unclassified_leaf_is_preserved_for_renderer_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\newcommand{\CommonShadow}{\MissingLeaf}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\CommonShadow$"],
            )

            self.assertEqual(macros, {"CommonShadow": r"\MissingLeaf"})

    def test_reachable_unsupported_project_arity_is_source_located(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\newcommand{\WideProjectMacro}[10]{#1}",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, r"WideProjectMacro.*arity.*10") as caught:
                extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\WideProjectMacro{x}$"],
                )

            self.assertIn(f"{entrypoint}:1:1", str(caught.exception))

    def test_reachable_malformed_project_arity_is_source_located(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\newcommand{\RootMacro}{\MalformedLeaf}" "\n"
                r"\newcommand{\MalformedLeaf}[x]{body}",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as caught:
                extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\RootMacro$"],
                )

            message = str(caught.exception)
            self.assertIn("MalformedLeaf", message)
            self.assertIn("malformed", message.lower())
            self.assertIn("arity", message.lower())
            self.assertIn(f"{entrypoint}:2:1", message)

    def test_extractor_does_not_reverse_tex_let_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            entrypoint = project / "main.tex"
            entrypoint.write_text(
                r"\newcommand{\RightHandGhost}{rhs}"
                r"\let\boldsymbol\RightHandGhost",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\boldsymbol$"],
            )

            self.assertEqual(
                macros,
                {
                    "RightHandGhost": "rhs",
                    "boldsymbol": r"\RightHandGhost",
                },
            )

    def test_extractor_resolves_a_distribution_class_definition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(
                r"\documentclass{distro-frame}"
                r"\newcommand{\DistroWrap}{\DistroClassMacro}",
                encoding="utf-8",
            )
            distribution_class = texmf / "distro-frame.cls"
            distribution_class.write_text(
                r"\newcommand{\DistroClassMacro}{\mathcal{D}}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return distribution_class if filename == "distro-frame.cls" else None

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\DistroWrap$"],
                )

            self.assertEqual(
                macros,
                {
                    "DistroClassMacro": r"\mathcal{D}",
                    "DistroWrap": r"\DistroClassMacro",
                },
            )

    def test_same_line_definition_after_include_has_absolute_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            entrypoint = project / "main.tex"
            included = project / "defs.tex"
            entrypoint.write_text(
                r"\input{defs}\newcommand{\AfterInclude}{main}",
                encoding="utf-8",
            )
            included.write_text(
                r"\newcommand{\AfterInclude}{included}",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Conflicting definitions") as caught:
                extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\AfterInclude$"],
                )

            self.assertIn(f"{entrypoint}:1:13", str(caught.exception))

    def test_extractor_closes_over_macros_used_by_optional_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            entrypoint = project / "main.tex"
            entrypoint.write_text(
                r"\newcommand{\DefaultSeed}{\mathbb{D}}"
                r"\newcommand{\OptionalSprout}[1][\DefaultSeed]{#1}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\OptionalSprout$"],
            )

            self.assertEqual(
                macros,
                {
                    "DefaultSeed": r"\mathbb{D}",
                    "OptionalSprout": ["#1", 1, r"\DefaultSeed"],
                },
            )

    def test_extractor_reports_both_locations_for_conflicting_definitions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            entrypoint = project / "main.tex"
            included = project / "defs.tex"
            entrypoint.write_text(
                r"\newcommand{\DuplicatePulse}{A}\input{defs}",
                encoding="utf-8",
            )
            included.write_text(
                r"\newcommand{\DuplicatePulse}{B}",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Conflicting definitions") as caught:
                extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\DuplicatePulse$"],
                )

            message = str(caught.exception)
            self.assertIn(str(entrypoint), message)
            self.assertIn(str(included), message)

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
