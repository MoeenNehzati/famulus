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
        "AdapterBranch",
        "AdapterRoot",
        "AdapterPrivate",
        "AdapterNoise",
        "AdapterLeaf",
        "AdapterShared",
        "AdapterDependencyRoot",
        "AdapterCycleLeaf",
        "NestedRawAlias",
        "OuterDelimited",
        "TransitiveRawRoot",
        "ConditionalRawRoot",
        "NativeBoundaryRoot",
        "NativeBoundarySaved",
        "SnapshotSource",
        "SnapshotAlias",
        "AdapterBoundaryRoot",
        "AdapterBoundarySaved",
        "ProjectBoundaryRoot",
        "ProjectBoundarySaved",
        "DormantLoader",
        "ExecutionScopedMacro",
        "ScopedSwitchMacro",
        "RestoredSwitchMacro",
        "CallbackBranchMacro",
        "SavedOrbit",
        "UsesSavedOrbit",
        "UnknownBranchMacro",
        "ParentStateRoot",
        "ChildStateRoot",
        "RobustCanopy",
        "AmbiguousProvide",
        "KnownProvide",
        "DistributionRobustMaybe",
        "DistributionProvideMaybe",
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

    def test_distribution_mathjax_adapter_precedes_raw_package_definition(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(
                r"\usepackage{distro-branch}"
                r"\newcommand{\AdapterRoot}[1]{\AdapterBranch{#1}}",
                encoding="utf-8",
            )
            package = texmf / "distro-branch.sty"
            package.write_text(
                r"\newcommand{\AdapterBranch}[1]{\AdapterPrivate{#1}}"
                r"\newcommand{\AdapterPrivate}[1]{\mathit{#1}}"
                r"\let\boldsymbol\AdapterBranch",
                encoding="utf-8",
            )
            adapter = texmf / "lwarp-distro-branch.sty"
            adapter.write_text(
                r"\newcommand{\AdapterNoise}{outside}"
                r"\CustomizeMathJax{"
                r"\newcommand{\AdapterPrivate}[1]{\mathit{#1}}"
                r"\newcommand{\AdapterBranch}[1]{\AdapterPrivate{#1}}"
                r"}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "distro-branch.sty": package,
                    "lwarp-distro-branch.sty": adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\AdapterRoot{x}$"],
                )

            self.assertEqual(
                macros,
                {
                    "AdapterPrivate": [r"\mathit{#1}", 1],
                    "AdapterBranch": [r"\AdapterPrivate{#1}", 1],
                    "AdapterRoot": [r"\AdapterBranch{#1}", 1],
                },
            )

    def test_adapter_dependency_declarations_supply_reachable_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-primary}", encoding="utf-8")
            package = texmf / "distro-primary.sty"
            package.write_text("", encoding="utf-8")
            adapter = texmf / "lwarp-distro-primary.sty"
            adapter.write_text(
                r"\LWR@origRequirePackage{lwarp-common-arbitrary}"
                r"\CustomizeMathJax{"
                r"\newcommand{\AdapterDependencyRoot}{\AdapterLeaf}"
                r"}",
                encoding="utf-8",
            )
            helper = texmf / "lwarp-common-arbitrary.sty"
            helper.write_text(
                r"\LWR@origRequirePackage{lwarp-distro-primary}"
                r"\newcommand{\AdapterNoise}{outside}"
                r"\CustomizeMathJax{\newcommand{\AdapterLeaf}{\mathsf{leaf}}}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "distro-primary.sty": package,
                    "lwarp-distro-primary.sty": adapter,
                    "lwarp-common-arbitrary.sty": helper,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\AdapterDependencyRoot$"],
                )

            self.assertEqual(
                macros,
                {
                    "AdapterLeaf": r"\mathsf{leaf}",
                    "AdapterDependencyRoot": r"\AdapterLeaf",
                },
            )

    def test_differing_adapter_definitions_report_both_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-one,distro-two}", encoding="utf-8")
            first_package = texmf / "distro-one.sty"
            second_package = texmf / "distro-two.sty"
            first_package.write_text("", encoding="utf-8")
            second_package.write_text("", encoding="utf-8")
            first_adapter = texmf / "lwarp-distro-one.sty"
            second_adapter = texmf / "lwarp-distro-two.sty"
            first_adapter.write_text(
                r"\CustomizeMathJax{\newcommand{\AdapterShared}{first}}",
                encoding="utf-8",
            )
            second_adapter.write_text(
                r"\CustomizeMathJax{\newcommand{\AdapterShared}{second}}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "distro-one.sty": first_package,
                    "distro-two.sty": second_package,
                    "lwarp-distro-one.sty": first_adapter,
                    "lwarp-distro-two.sty": second_adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                with self.assertRaisesRegex(
                    ValueError, r"Conflicting definitions.*AdapterShared"
                ) as caught:
                    extract_renderable_macros(
                        tex_entrypoint=entrypoint,
                        graph_text=[r"$\AdapterShared$"],
                    )

            message = str(caught.exception)
            self.assertIn(str(first_adapter), message)
            self.assertIn(str(second_adapter), message)

    def test_raw_distribution_definition_is_a_graph_visible_root_when_representable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-private}", encoding="utf-8")
            package = texmf / "distro-private.sty"
            package.write_text(
                r"\def\AdapterPrivate{\mathcal{P}}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return package if filename == "distro-private.sty" else None

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\AdapterPrivate$"],
                )

            self.assertEqual(macros, {"AdapterPrivate": r"\mathcal{P}"})

    def test_raw_root_is_retained_when_adapter_does_not_map_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-covered}", encoding="utf-8")
            package = texmf / "distro-covered.sty"
            package.write_text(
                r"\newcommand{\CoveredRawRoot}{\mathcal{C}}",
                encoding="utf-8",
            )
            adapter = texmf / "lwarp-distro-covered.sty"
            adapter.write_text(r"\CustomizeMathJax{}", encoding="utf-8")

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "distro-covered.sty": package,
                    "lwarp-distro-covered.sty": adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\CoveredRawRoot$"],
                )

            self.assertEqual(macros, {"CoveredRawRoot": r"\mathcal{C}"})

    def test_raw_distribution_cycle_reports_source_located_dependency_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-cycle}", encoding="utf-8")
            package = texmf / "distro-cycle.sty"
            package.write_text(
                r"\newcommand{\RawCycleRoot}{\RawCycleLeaf}" "\n"
                r"\newcommand{\RawCycleLeaf}{\RawCycleRoot}",
                encoding="utf-8",
            )

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=lambda filename: (
                    package if filename == "distro-cycle.sty" else None
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError, r"RawCycleRoot.*RawCycleLeaf.*RawCycleRoot"
                ) as caught:
                    extract_renderable_macros(
                        tex_entrypoint=entrypoint,
                        graph_text=[r"$\RawCycleRoot$"],
                    )

            self.assertIn(f"{package}:1:1", str(caught.exception))
            self.assertIn(f"{package}:2:1", str(caught.exception))

    def test_adapter_coverage_does_not_suppress_transitive_raw_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-parent}", encoding="utf-8")
            parent = texmf / "distro-parent.sty"
            parent.write_text(r"\RequirePackage{distro-child}", encoding="utf-8")
            child = texmf / "distro-child.sty"
            child.write_text(r"\newcommand{\TransitiveRawRoot}{child}", encoding="utf-8")
            adapter = texmf / "lwarp-distro-parent.sty"
            adapter.write_text(r"\CustomizeMathJax{}", encoding="utf-8")

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "distro-parent.sty": parent,
                    "distro-child.sty": child,
                    "lwarp-distro-parent.sty": adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\TransitiveRawRoot$"],
                )

            self.assertEqual(macros, {"TransitiveRawRoot": "child"})

    def test_nested_alias_in_unsupported_delimited_def_is_not_exported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-delimited}", encoding="utf-8")
            package = texmf / "distro-delimited.sty"
            package.write_text(
                r"\newcommand{\AdapterLeaf}{leaf}"
                r"\def\OuterDelimited#1\Stop{\let\NestedRawAlias\AdapterLeaf}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return package if filename == "distro-delimited.sty" else None

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\NestedRawAlias$"],
                )
                with self.assertRaisesRegex(
                    ValueError, r"OuterDelimited.*delimited"
                ) as caught:
                    extract_renderable_macros(
                        tex_entrypoint=entrypoint,
                        graph_text=[r"$\OuterDelimited{x}\Stop$"],
                    )

            self.assertEqual(macros, {})
            self.assertIn(str(package), str(caught.exception))

    def test_true_and_false_conditional_branches_select_live_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-conditional}", encoding="utf-8")
            package = texmf / "distro-conditional.sty"
            package.write_text(
                r"\iftrue"
                r"\newcommand{\TrueBranchRoot}{\mathcal{T}}"
                r"\else"
                r"\newcommand{\DeadTrueBranchRoot}{dead}"
                r"\fi"
                r"\iffalse"
                r"\newcommand{\DeadFalseBranchRoot}{dead}"
                r"\else"
                r"\newcommand{\FalseElseRoot}{\mathcal{F}}"
                r"\fi",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return package if filename == "distro-conditional.sty" else None

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[
                        r"$\TrueBranchRoot+\DeadTrueBranchRoot"
                        r"+\DeadFalseBranchRoot+\FalseElseRoot$"
                    ],
                )

            self.assertEqual(
                macros,
                {
                    "TrueBranchRoot": r"\mathcal{T}",
                    "FalseElseRoot": r"\mathcal{F}",
                },
            )

    def test_brace_and_begingroup_local_definitions_do_not_shadow_global_definition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"{\newcommand{\ScopedRoot}{brace-local}}"
                r"\begingroup\newcommand{\ScopedRoot}{group-local}\endgroup"
                r"\newcommand{\ScopedRoot}{\mathcal{G}}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\ScopedRoot$"],
            )

            self.assertEqual(macros, {"ScopedRoot": r"\mathcal{G}"})

    def test_uncertain_relevant_conditional_definition_fails_with_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\ifdefined\RuntimeFlag"
                r"\newcommand{\UncertainRoot}{first}"
                r"\else"
                r"\newcommand{\UncertainRoot}{second}"
                r"\fi",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError, r"UncertainRoot.*conditional.*Dependency chain"
            ) as caught:
                extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\UncertainRoot$"],
                )

            self.assertIn(f"{entrypoint}:1:", str(caught.exception))

    def test_newif_state_selects_only_the_live_definition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\newif\ifArbitrarySwitch"
                r"\ArbitrarySwitchtrue"
                r"\ifArbitrarySwitch"
                r"\newcommand{\NamedConditionalRoot}{live}"
                r"\else"
                r"\newcommand{\NamedConditionalRoot}{dead}"
                r"\fi",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\NamedConditionalRoot$"],
            )

            self.assertEqual(macros, {"NamedConditionalRoot": "live"})

    def test_global_def_inside_a_group_is_exported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"{\global\def\GlobalRoot{live}}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\GlobalRoot$"],
            )

            self.assertEqual(macros, {"GlobalRoot": "live"})

    def test_gdef_and_global_long_def_escape_their_groups(self) -> None:
        """Catch omission of graph-visible global primitive definitions."""
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"{\gdef\GlobalLeaf{leaf}}"
                r"{\global\long\def\GlobalLongRoot{\GlobalLeaf}}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\GlobalLongRoot$"],
            )

            self.assertEqual(
                macros,
                {
                    "GlobalLeaf": "leaf",
                    "GlobalLongRoot": r"\GlobalLeaf",
                },
            )

    def test_gdef_uses_primitive_redefinition_semantics(self) -> None:
        """Catch treating a global primitive override as a duplicate conflict."""
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\gdef\GlobalOverride{first}"
                r"\gdef\GlobalOverride{second}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\GlobalOverride$"],
            )

            self.assertEqual(macros, {"GlobalOverride": "second"})

    def test_raw_native_wrapper_through_external_snapshot_is_not_serialized(
        self,
    ) -> None:
        """Catch exporting a distribution wrapper around a pre-existing command."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            package = texmf / "native-boundary.sty"
            entrypoint.write_text(r"\usepackage{native-boundary}", encoding="utf-8")
            package.write_text(
                r"\let\NativeBoundarySaved\NativeBoundaryRoot"
                r"\gdef\NativeBoundaryRoot{\NativeBoundarySaved}",
                encoding="utf-8",
            )

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=lambda filename: (
                    package if filename == "native-boundary.sty" else None
                ),
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\NativeBoundaryRoot{x}$"],
                )

            self.assertEqual(macros, {})

    def test_uncertain_raw_native_wrapper_is_omitted_before_branch_error(
        self,
    ) -> None:
        """Catch surfacing an inactive implementation wrapper as a graph error."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            package = texmf / "uncertain-native-boundary.sty"
            entrypoint.write_text(
                r"\usepackage{uncertain-native-boundary}", encoding="utf-8"
            )
            package.write_text(
                r"\let\NativeBoundarySaved\NativeBoundaryRoot"
                r"\ifdefined\RuntimeFlag"
                r"\gdef\NativeBoundaryRoot{\NativeBoundarySaved}"
                r"\else"
                r"\gdef\NativeBoundaryRoot{\NativeBoundarySaved}"
                r"\fi",
                encoding="utf-8",
            )

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=lambda filename: (
                    package if filename == "uncertain-native-boundary.sty" else None
                ),
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\NativeBoundaryRoot{x}$"],
                )

            self.assertEqual(macros, {})

    def test_adapter_definition_supersedes_a_raw_native_wrapper(self) -> None:
        """Catch native-boundary suppression overriding a portable adapter."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            package = texmf / "adapter-boundary.sty"
            adapter = texmf / "lwarp-adapter-boundary.sty"
            entrypoint.write_text(r"\usepackage{adapter-boundary}", encoding="utf-8")
            package.write_text(
                r"\let\AdapterBoundarySaved\AdapterBoundaryRoot"
                r"\gdef\AdapterBoundaryRoot{\AdapterBoundarySaved}",
                encoding="utf-8",
            )
            adapter.write_text(
                r"\CustomizeMathJax{"
                r"\newcommand{\AdapterBoundaryRoot}{\mathsf{portable}}}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "adapter-boundary.sty": package,
                    "lwarp-adapter-boundary.sty": adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\AdapterBoundaryRoot$"],
                )

            self.assertEqual(
                macros,
                {"AdapterBoundaryRoot": r"\mathsf{portable}"},
            )

    def test_project_definition_supersedes_a_raw_native_wrapper(self) -> None:
        """Catch native-boundary suppression overriding a project shadow."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            package = texmf / "project-boundary.sty"
            entrypoint.write_text(
                r"\usepackage{project-boundary}"
                r"\renewcommand{\ProjectBoundaryRoot}{project}",
                encoding="utf-8",
            )
            package.write_text(
                r"\let\ProjectBoundarySaved\ProjectBoundaryRoot"
                r"\gdef\ProjectBoundaryRoot{\ProjectBoundarySaved}",
                encoding="utf-8",
            )

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=lambda filename: (
                    package if filename == "project-boundary.sty" else None
                ),
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\ProjectBoundaryRoot$"],
                )

            self.assertEqual(macros, {"ProjectBoundaryRoot": "project"})

    def test_known_let_binding_snapshots_before_later_renewal(self) -> None:
        """Catch resolving a let alias against a later replacement binding."""
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\newcommand{\SnapshotSource}{old}"
                r"\let\SnapshotAlias\SnapshotSource"
                r"\renewcommand{\SnapshotSource}{new}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\SnapshotAlias$"],
            )

            self.assertEqual(macros, {"SnapshotAlias": "old"})

    def test_inactive_symbol_font_does_not_override_the_live_font(self) -> None:
        """Catch branch-blind precollection of DeclareSymbolFont metadata."""
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\DeclareSymbolFont{probe}{LIVE}{family}{m}{n}"
                r"\iffalse"
                r"\DeclareSymbolFont{probe}{DEAD}{family}{m}{n}"
                r"\fi"
                r'\DeclareMathSymbol{\FontProbe}{\mathord}{probe}{"41}',
                encoding="utf-8",
            )

            with mock.patch.object(
                _tex_macro_reader,
                "_canonical_math_symbols",
                return_value={
                    ("LIVE", "family", 65): "LiveCanonical",
                    ("DEAD", "family", 65): "DeadCanonical",
                },
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\FontProbe$"],
                )

            self.assertEqual(macros, {"FontProbe": r"\LiveCanonical"})

    def test_inactive_adapter_customization_is_not_exported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-inactive}", encoding="utf-8")
            package = texmf / "distro-inactive.sty"
            package.write_text("", encoding="utf-8")
            adapter = texmf / "lwarp-distro-inactive.sty"
            adapter.write_text(
                r"\iffalse"
                r"\CustomizeMathJax{\newcommand{\InactiveAdapterRoot}{dead}}"
                r"\fi"
                r"\CustomizeMathJax{\newcommand{\ActiveAdapterRoot}{live}}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "distro-inactive.sty": package,
                    "lwarp-distro-inactive.sty": adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\InactiveAdapterRoot+\ActiveAdapterRoot$"],
                )

            self.assertEqual(macros, {"ActiveAdapterRoot": "live"})

    def test_real_siunits_deferred_squaren_is_not_silently_omitted(self) -> None:
        """Catch ignored CustomizeMathJax declarations in AtBeginDocument."""
        package = _tex_macro_reader.tex_distribution_path("SIunits.sty")
        adapter = _tex_macro_reader.tex_distribution_path("lwarp-SIunits.sty")
        if package is None or adapter is None:
            # famulus-skip: category=capability-unavailable; reason=TeX Live SIunits fixtures are unavailable; alternate=test_synthetic_deferred_adapter_conditional_is_source_located covers the same bounded callback path
            self.skipTest("TeX Live SIunits and lwarp-SIunits fixtures are unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(r"\usepackage{SIunits}", encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                r"squaren.*conditional.*Dependency chain",
            ) as caught:
                extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\squaren{x}$"],
                )

        self.assertIn(str(adapter), str(caught.exception))

    def test_synthetic_deferred_adapter_conditional_is_source_located(self) -> None:
        """Keep deferred-callback coverage when the real TeX fixture is absent."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            package = texmf / "deferred-probe.sty"
            adapter = texmf / "lwarp-deferred-probe.sty"
            entrypoint.write_text(r"\usepackage{deferred-probe}", encoding="utf-8")
            package.write_text("", encoding="utf-8")
            adapter.write_text(
                r"\AtBeginDocument{"
                r"\if@late"
                r"\CustomizeMathJax{\newcommand{\DeferredProbe}{live}}"
                r"\fi}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "deferred-probe.sty": package,
                    "lwarp-deferred-probe.sty": adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    r"DeferredProbe.*conditional.*Dependency chain",
                ) as caught:
                    extract_renderable_macros(
                        tex_entrypoint=entrypoint,
                        graph_text=[r"$\DeferredProbe$"],
                    )

            self.assertIn(str(adapter), str(caught.exception))

    def test_uninvoked_adapter_customization_body_is_not_exported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-dormant}", encoding="utf-8")
            package = texmf / "distro-dormant.sty"
            package.write_text("", encoding="utf-8")
            adapter = texmf / "lwarp-distro-dormant.sty"
            adapter.write_text(
                r"\newcommand{\DormantSetup}{"
                r"\CustomizeMathJax{\newcommand{\DormantAdapterRoot}{dead}}}"
                r"\CustomizeMathJax{\newcommand{\InvokedAdapterRoot}{live}}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "distro-dormant.sty": package,
                    "lwarp-distro-dormant.sty": adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\DormantAdapterRoot+\InvokedAdapterRoot$"],
                )

            self.assertEqual(macros, {"InvokedAdapterRoot": "live"})

    def test_uncertain_relevant_adapter_customization_fails_with_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-uncertain}", encoding="utf-8")
            package = texmf / "distro-uncertain.sty"
            package.write_text("", encoding="utf-8")
            adapter = texmf / "lwarp-distro-uncertain.sty"
            adapter.write_text(
                r"\ifdefined\RuntimeAdapterFlag"
                r"\CustomizeMathJax{\newcommand{\UncertainAdapterRoot}{first}}"
                r"\else"
                r"\CustomizeMathJax{\newcommand{\UncertainAdapterRoot}{second}}"
                r"\fi",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "distro-uncertain.sty": package,
                    "lwarp-distro-uncertain.sty": adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                with self.assertRaisesRegex(
                    ValueError, r"UncertainAdapterRoot.*conditional.*Dependency chain"
                ) as caught:
                    extract_renderable_macros(
                        tex_entrypoint=entrypoint,
                        graph_text=[r"$\UncertainAdapterRoot$"],
                    )

            self.assertIn(str(adapter), str(caught.exception))

    def test_adapter_raw_back_reference_cycle_fails_with_dependency_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-cycle}", encoding="utf-8")
            package = texmf / "distro-cycle.sty"
            package.write_text(
                r"\newcommand{\AdapterBranch}{\AdapterCycleLeaf}"
                r"\let\AdapterCycleLeaf\AdapterBranch",
                encoding="utf-8",
            )
            adapter = texmf / "lwarp-distro-cycle.sty"
            adapter.write_text(
                r"\CustomizeMathJax{\newcommand{\AdapterBranch}{\AdapterCycleLeaf}}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "distro-cycle.sty": package,
                    "lwarp-distro-cycle.sty": adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                with self.assertRaisesRegex(
                    ValueError, r"AdapterBranch.*AdapterCycleLeaf.*AdapterCycleLeaf"
                ) as caught:
                    extract_renderable_macros(
                        tex_entrypoint=entrypoint,
                        graph_text=[r"$\AdapterBranch$"],
                    )

            message = str(caught.exception)
            self.assertIn(str(adapter), message)
            self.assertIn(str(package), message)

    def test_adapter_recursively_includes_known_raw_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-bridge}", encoding="utf-8")
            package = texmf / "distro-bridge.sty"
            package.write_text(
                r"\newcommand{\RawBridgeLeaf}{\mathcal{R}}",
                encoding="utf-8",
            )
            adapter = texmf / "lwarp-distro-bridge.sty"
            adapter.write_text(
                r"\CustomizeMathJax{"
                r"\newcommand{\AdapterBridgeRoot}{\RawBridgeLeaf}"
                r"}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "distro-bridge.sty": package,
                    "lwarp-distro-bridge.sty": adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\AdapterBridgeRoot$"],
                )

            self.assertEqual(
                macros,
                {
                    "RawBridgeLeaf": r"\mathcal{R}",
                    "AdapterBridgeRoot": r"\RawBridgeLeaf",
                },
            )

    def test_adapter_known_unsupported_raw_dependency_fails_with_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            entrypoint.write_text(r"\usepackage{distro-unsupported}", encoding="utf-8")
            package = texmf / "distro-unsupported.sty"
            package.write_text(r"\def\UnsupportedRaw#1\Stop{#1}", encoding="utf-8")
            adapter = texmf / "lwarp-distro-unsupported.sty"
            adapter.write_text(
                r"\CustomizeMathJax{"
                r"\newcommand{\AdapterUnsupportedRoot}{\UnsupportedRaw{x}\Stop}"
                r"}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "distro-unsupported.sty": package,
                    "lwarp-distro-unsupported.sty": adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                with self.assertRaisesRegex(
                    ValueError, r"UnsupportedRaw.*delimited.*Dependency chain"
                ) as caught:
                    extract_renderable_macros(
                        tex_entrypoint=entrypoint,
                        graph_text=[r"$\AdapterUnsupportedRoot$"],
                    )

            self.assertIn(str(adapter), str(caught.exception))
            self.assertIn(str(package), str(caught.exception))

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
                    "ProjectAlias": ["#1+#2", 2],
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
                    "boldsymbol": "rhs",
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
                r"\newcommand{\ProjectClassWrap}{\DistroClassMacro}",
                encoding="utf-8",
            )
            distribution_class = texmf / "distro-frame.cls"
            distribution_class.write_text(
                r"\newcommand{\DistroClassLeaf}{\mathcal{D}}"
                r"\newcommand{\DistroClassMacro}{\DistroClassLeaf}",
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
                    graph_text=[r"$\ProjectClassWrap$"],
                )

            self.assertEqual(
                macros,
                {
                    "DistroClassLeaf": r"\mathcal{D}",
                    "DistroClassMacro": r"\DistroClassLeaf",
                    "ProjectClassWrap": r"\DistroClassMacro",
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

    def test_include_discovery_skips_uninvoked_macro_replacement_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            entrypoint = project / "main.tex"
            dormant = project / "dormant.tex"
            entrypoint.write_text(
                r"\gdef\DormantLoader{\input{dormant}}"
                r"\newcommand{\ExecutionScopedMacro}{live}",
                encoding="utf-8",
            )
            dormant.write_text(
                r"\newcommand{\ExecutionScopedMacro}{wrong}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\ExecutionScopedMacro$"],
            )

            self.assertEqual(macros, {"ExecutionScopedMacro": "live"})

    def test_group_local_newif_assignment_is_restored_but_global_def_survives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\newif\ifscopepick\scopepickfalse"
                r"{\scopepicktrue\ifscopepick"
                r"\global\def\ScopedSwitchMacro{inside}\fi}"
                r"\ifscopepick"
                r"\newcommand{\RestoredSwitchMacro}{wrong}"
                r"\else\newcommand{\RestoredSwitchMacro}{right}\fi",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\ScopedSwitchMacro+\RestoredSwitchMacro$"],
            )

            self.assertEqual(
                macros,
                {
                    "ScopedSwitchMacro": "inside",
                    "RestoredSwitchMacro": "right",
                },
            )

    def test_at_begin_document_scan_inherits_named_newif_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            package = texmf / "callback-switch.sty"
            adapter = texmf / "lwarp-callback-switch.sty"
            entrypoint.write_text(
                r"\usepackage{callback-switch}"
                r"\newcommand{\CallbackBranchRoot}{\CallbackBranchMacro}",
                encoding="utf-8",
            )
            package.write_text("% package declarations", encoding="utf-8")
            adapter.write_text(
                r"\newif\ifcallbackpick\callbackpickfalse"
                r"\AtBeginDocument{\ifcallbackpick"
                r"\CustomizeMathJax{\newcommand{\CallbackBranchMacro}{wrong}}"
                r"\else"
                r"\CustomizeMathJax{\newcommand{\CallbackBranchMacro}{right}}"
                r"\fi}",
                encoding="utf-8",
            )

            def controlled_distribution(filename: str) -> Path | None:
                return {
                    "callback-switch.sty": package,
                    "lwarp-callback-switch.sty": adapter,
                }.get(filename)

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=controlled_distribution,
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\CallbackBranchRoot$"],
                )

            self.assertEqual(
                macros,
                {
                    "CallbackBranchMacro": "right",
                    "CallbackBranchRoot": r"\CallbackBranchMacro",
                },
            )

    def test_external_let_snapshot_is_not_serialized_as_redefined_live_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\let\SavedOrbit\sin"
                r"\renewcommand{\sin}{\operatorname{changed}}"
                r"\newcommand{\UsesSavedOrbit}{\SavedOrbit}",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as caught:
                extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\UsesSavedOrbit$"],
                )

            message = str(caught.exception)
            self.assertIn("SavedOrbit", message)
            self.assertIn("snapshot", message.lower())
            self.assertIn(r"\sin", message)
            self.assertIn(f"{entrypoint}:1:1", message)

    def test_relevant_definition_under_unknown_internal_conditional_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\if@hiddenbranch"
                r"\newcommand{\UnknownBranchMacro}{uncertain}"
                r"\fi",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as caught:
                extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\UnknownBranchMacro$"],
                )

            message = str(caught.exception)
            self.assertIn("UnknownBranchMacro", message)
            self.assertIn("cannot be determined statically", message)
            self.assertIn(f"{entrypoint}:1:17", message)

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

    def test_project_newcommand_conflicts_with_distribution_newcommand(self) -> None:
        """Catch ownership precedence hiding an invalid duplicate declaration."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            package = texmf / "ownership-conflict.sty"
            entrypoint.write_text(
                r"\usepackage{ownership-conflict}"
                r"\newcommand{\OwnershipConflictRoot}{project}",
                encoding="utf-8",
            )
            package.write_text(
                r"\newcommand{\OwnershipConflictRoot}{distribution}",
                encoding="utf-8",
            )

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=lambda filename: (
                    package if filename == "ownership-conflict.sty" else None
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError, r"Conflicting definitions.*OwnershipConflictRoot"
                ) as caught:
                    extract_renderable_macros(
                        tex_entrypoint=entrypoint,
                        graph_text=[r"$\OwnershipConflictRoot$"],
                    )

            message = str(caught.exception)
            self.assertIn(str(entrypoint), message)
            self.assertIn(str(package), message)

    def test_load_discovery_consumes_let_rhs_before_following_input(self) -> None:
        """Catch a let RHS being mistaken for an executed conditional token."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            entrypoint = project / "main.tex"
            included = project / "live.tex"
            entrypoint.write_text(
                r"\newif\ifArbitrarySwitch"
                r"\let\ArbitraryAlias\ifArbitrarySwitch"
                r"\input{live}",
                encoding="utf-8",
            )
            included.write_text(
                r"\newcommand{\LetLoadRoot}{live}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\LetLoadRoot$"],
            )

            self.assertEqual(macros, {"LetLoadRoot": "live"})

    def test_parent_newif_state_controls_loads_inside_an_included_file(self) -> None:
        """Catch child load discovery discarding the parent's switch state."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            entrypoint = project / "main.tex"
            included = project / "included.tex"
            entrypoint.write_text(
                r"\newif\ifparentpick\parentpickfalse"
                r"\input{included}"
                r"\newcommand{\ParentStateRoot}{live}",
                encoding="utf-8",
            )
            included.write_text(
                r"\ifparentpick\input{included}\fi",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\ParentStateRoot$"],
            )

            self.assertEqual(macros, {"ParentStateRoot": "live"})

    def test_included_newif_state_controls_later_loads_in_the_parent(self) -> None:
        """Catch parent load discovery running before an included switch update."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            entrypoint = project / "main.tex"
            included = project / "included.tex"
            entrypoint.write_text(
                r"\input{included}"
                r"\ifchildpick\input{main}\fi"
                r"\newcommand{\ChildStateRoot}{live}",
                encoding="utf-8",
            )
            included.write_text(
                r"\newif\ifchildpick\childpickfalse",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\ChildStateRoot$"],
            )

            self.assertEqual(macros, {"ChildStateRoot": "live"})

    def test_repeated_input_executes_again_in_source_order(self) -> None:
        """Catch global include suppression changing repeated input semantics."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            entrypoint = project / "main.tex"
            included = project / "defs.tex"
            entrypoint.write_text(
                r"\input{defs}"
                r"\renewcommand{\RepeatedInputRoot}{middle}"
                r"\input{defs}",
                encoding="utf-8",
            )
            included.write_text(
                r"\def\RepeatedInputRoot{from-input}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\RepeatedInputRoot$"],
            )

            self.assertEqual(macros, {"RepeatedInputRoot": "from-input"})

    def test_repeated_package_load_is_include_once(self) -> None:
        """Catch recursion-only tracking reexecuting a package load."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            package = texmf / "load-once.sty"
            entrypoint.write_text(
                r"\usepackage{load-once}"
                r"\renewcommand{\LoadOnceRoot}{middle}"
                r"\usepackage{load-once}",
                encoding="utf-8",
            )
            package.write_text(
                r"\def\LoadOnceRoot{package}",
                encoding="utf-8",
            )

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=lambda filename: (
                    package if filename == "load-once.sty" else None
                ),
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\LoadOnceRoot$"],
                )

            self.assertEqual(macros, {"LoadOnceRoot": "middle"})

    def test_graph_visible_unsupported_named_declaration_fails_closed(self) -> None:
        """Catch a source-declared macro disappearing as an undeclared leaf."""
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\DeclareRobustCommand{\RobustCanopy}[1]{\mathbf{#1}}",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as caught:
                extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\RobustCanopy{x}$"],
                )

            message = str(caught.exception)
            self.assertIn("RobustCanopy", message)
            self.assertIn("DeclareRobustCommand", message)
            self.assertIn("unsupported", message.lower())
            self.assertIn(f"{entrypoint}:1:1", message)

    def test_first_providecommand_binding_fails_closed(self) -> None:
        """Catch providecommand overriding an unknown native renderer command."""
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\providecommand{\AmbiguousProvide}[2]{#1-#2}",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as caught:
                extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\AmbiguousProvide{x}{y}$"],
                )

            message = str(caught.exception)
            self.assertIn("AmbiguousProvide", message)
            self.assertIn("providecommand", message)
            self.assertIn("external", message.lower())
            self.assertIn(f"{entrypoint}:1:1", message)

    def test_providecommand_preserves_known_earlier_source_binding(self) -> None:
        """Catch providecommand replacing a binding already established by source."""
        with tempfile.TemporaryDirectory() as tmp:
            entrypoint = Path(tmp) / "main.tex"
            entrypoint.write_text(
                r"\newcommand{\KnownProvide}{first}"
                r"\providecommand{\KnownProvide}{second}",
                encoding="utf-8",
            )

            macros = extract_renderable_macros(
                tex_entrypoint=entrypoint,
                graph_text=[r"$\KnownProvide$"],
            )

            self.assertEqual(macros, {"KnownProvide": "first"})

    def test_distribution_robust_declaration_defers_to_renderer_oracle(self) -> None:
        """Catch raw native wrappers being serialized as project definitions."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            package = texmf / "robust-native-maybe.sty"
            entrypoint.write_text(r"\usepackage{robust-native-maybe}", encoding="utf-8")
            package.write_text(
                r"\DeclareRobustCommand{\DistributionRobustMaybe}[1]{\mathbf{#1}}",
                encoding="utf-8",
            )

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=lambda filename: (
                    package if filename == "robust-native-maybe.sty" else None
                ),
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\DistributionRobustMaybe{x}$"],
                )

            self.assertEqual(macros, {})

    def test_distribution_first_provide_defers_to_renderer_oracle(self) -> None:
        """Catch an optional raw package fallback overriding a native renderer command."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            texmf = Path(tmp) / "texmf"
            project.mkdir()
            texmf.mkdir()
            entrypoint = project / "main.tex"
            package = texmf / "provide-native-maybe.sty"
            entrypoint.write_text(r"\usepackage{provide-native-maybe}", encoding="utf-8")
            package.write_text(
                r"\providecommand{\DistributionProvideMaybe}[1]{\mathsf{#1}}",
                encoding="utf-8",
            )

            with mock.patch.object(
                _tex_macro_reader,
                "tex_distribution_path",
                side_effect=lambda filename: (
                    package if filename == "provide-native-maybe.sty" else None
                ),
            ):
                macros = extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\DistributionProvideMaybe{x}$"],
                )

            self.assertEqual(macros, {})

    def test_recursive_input_cycle_fails_with_both_paths(self) -> None:
        """Catch dependency recursion being silently truncated by a global seen set."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            entrypoint = project / "main.tex"
            included = project / "cycle.tex"
            entrypoint.write_text(r"\input{cycle}", encoding="utf-8")
            included.write_text(r"\input{main}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Cyclic TeX dependency") as caught:
                extract_renderable_macros(
                    tex_entrypoint=entrypoint,
                    graph_text=[r"$\CycleRoot$"],
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
