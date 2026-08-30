"""Tests for version-4 generated SKILL.md dispatcher exposure."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil

from officina.blueprints.graph import (
    BlueprintNode,
    InterfaceExport,
    RepositoryBlueprintGraph,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = (
    REPO_ROOT
    / "validators"
    / "skill"
    / "skill_md_dispatch.py"
)
SPEC = importlib.util.spec_from_file_location("skill_md_dispatch", VALIDATOR)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

DISPATCH = (
    "dispatcher --caller-skill get-weather "
    "get-weather._rtx.interface.scripts-weather"
)


def _copy_weather_module(repo_root: Path) -> Path:
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint-schema",
        repo_root / "references" / "blueprint-schema",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )
    target = repo_root / "skills" / "get-weather"
    shutil.copytree(
        REPO_ROOT / "skills" / "get-weather",
        target,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return target


def test_repo_without_modules_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()

    assert MOD.validate(tmp_path) == []


def test_weather_repository_discovers_only_parent_prompt_export(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = _copy_weather_module(tmp_path)
    observed: list[tuple[Path, list[str], list[str], list[str]]] = []
    original = MOD._validate_skill_text

    def observe(
        skill_md: Path,
        skill_name: str,
        text: str,
        *,
        all_ids: list[str],
        visible_ids: list[str],
        dispatcher_targets: list[str],
    ) -> list[str]:
        observed.append((skill_md, all_ids, visible_ids, dispatcher_targets))
        return original(
            skill_md,
            skill_name,
            text,
            all_ids=all_ids,
            visible_ids=visible_ids,
            dispatcher_targets=dispatcher_targets,
        )

    monkeypatch.setattr(MOD, "_validate_skill_text", observe)
    skill_text = (skill / "SKILL.md").read_text(encoding="utf-8")
    interface_block = skill_text.split("<!-- BEGIN BLUEPRINT INTERFACES -->", 1)[
        1
    ].split("<!-- END BLUEPRINT INTERFACES -->", 1)[0]

    assert MOD.validate(tmp_path) == []
    assert observed == [
        (
            skill / "SKILL.md",
            ["get-weather.interface.default"],
            ["get-weather.interface.default"],
            [],
        )
    ]
    assert "get-weather.interface.default" in interface_block
    assert "get-weather._rtx.interface.scripts-weather" not in interface_block
    assert "dispatcher --caller-skill" not in interface_block


def test_skill_text_diagnostics_are_exact_and_side_effect_free() -> None:
    skill_md = Path("skills/demo/SKILL.md")
    block = (
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        "<!-- END BLUEPRINT INTERFACES -->"
    )
    cases = (
        (
            "missing generated block",
            "Skill: demo\n",
            ["demo.interface.default"],
            ["demo.interface.default"],
            [],
            [f"{skill_md}: missing generated blueprint interface block"],
        ),
        (
            "raw runtime path in generated block",
            block.replace("\n<!-- END", "\n_rtx/client.py\n<!-- END"),
            ["demo.interface.default"],
            ["demo.interface.default"],
            [],
            [
                f"{skill_md}: generated interface block must not expose raw runtime files"
            ],
        ),
        (
            "runtime invocation in authored body",
            block + "\nRun `_rtx/client.py` directly.\n",
            ["demo.interface.default"],
            ["demo.interface.default"],
            [],
            [
                f"{skill_md}: skill body must not invoke runtime files directly; "
                "reference dispatcher interface names instead"
            ],
        ),
        (
            "dispatcher command in authored body",
            block + f"\nRun `{DISPATCH}`.\n",
            ["demo.interface.default"],
            ["demo.interface.default"],
            [],
            [
                f"{skill_md}: skill body must not invoke dispatcher directly; "
                "interface invocations belong in the generated block "
                "(blueprint.yaml owns them)"
            ],
        ),
    )

    for label, text, all_ids, visible_ids, dispatcher_targets, expected in cases:
        assert MOD._validate_skill_text(
            skill_md,
            "demo",
            text,
            all_ids=all_ids,
            visible_ids=visible_ids,
            dispatcher_targets=dispatcher_targets,
        ) == expected, label


def test_two_process_exports_report_missing_commands_in_stable_order(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "demo"
    skill_root.mkdir(parents=True)
    skill_md = skill_root / "SKILL.md"
    skill_md.write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        "<!-- END BLUEPRINT INTERFACES -->\n",
        encoding="utf-8",
    )
    graph = RepositoryBlueprintGraph(
        nodes={
            "demo": BlueprintNode(
                node_id="demo",
                node_type="module",
                version=1,
                module_root=skill_root,
                blueprint_path=skill_root / "blueprint.yaml",
                gateway_path=None,
                declaration={},
            )
        },
        node_edges=(),
        exports={
            "demo.interface.zeta": InterfaceExport(
                interface_id="demo.interface.zeta",
                version=1,
                local_name="zeta",
                module_node_id="demo",
                declaration={"process_binding": {"kind": "process"}},
            ),
            "demo.interface.alpha": InterfaceExport(
                interface_id="demo.interface.alpha",
                version=1,
                local_name="alpha",
                module_node_id="demo",
                declaration={"process_binding": {"kind": "process"}},
            ),
        },
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
    )

    assert MOD.validate_with_graph(tmp_path, graph) == [
        f"{skill_md}: generated interface block is missing dispatcher command "
        "for `demo.interface.alpha`",
        f"{skill_md}: generated interface block is missing dispatcher command "
        "for `demo.interface.zeta`",
    ]
