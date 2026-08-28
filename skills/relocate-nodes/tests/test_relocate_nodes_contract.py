from __future__ import annotations

from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
RTX_ROOT = SKILL_ROOT / "_rtx"


def test_relocation_node_exports_only_plan_and_review_routes() -> None:
    parent = yaml.safe_load((SKILL_ROOT / "blueprint.yaml").read_text(encoding="utf-8"))
    child = yaml.safe_load((RTX_ROOT / "blueprint.yaml").read_text(encoding="utf-8"))

    assert parent["namespace_exports"]["_rtx"]["surface"]["only"] == {
        "relocate-nodes._rtx.interface.build-review-packet": 1,
        "relocate-nodes._rtx.interface.relocate": 2,
    }
    assert set(child["exports"]) == {
        "relocate-nodes._rtx.interface.build-review-packet",
        "relocate-nodes._rtx.interface.relocate",
    }


def test_relocation_runtime_stays_within_compact_production_budget() -> None:
    production = [path for path in RTX_ROOT.glob("*.py") if path.name != "__init__.py"]
    physical_lines = sum(
        len(path.read_text(encoding="utf-8").splitlines()) for path in production
    )
    assert physical_lines <= 500, (
        f"relocation runtime has {physical_lines} production lines"
    )


def test_default_workflow_declares_both_machine_routes() -> None:
    gateway = yaml.safe_load(
        (SKILL_ROOT / "blueprints/gateway.yaml").read_text(encoding="utf-8")
    )
    interface = gateway["interfaces"][
        "relocate-nodes.source.gateway.interface.default"
    ]

    assert interface["uses_interfaces"] == [
        {"interface": "relocate-nodes._rtx.interface.build-review-packet", "version": 1},
        {"interface": "relocate-nodes._rtx.interface.relocate", "version": 2},
    ]


def test_skill_documents_both_machine_routes_outside_generated_contract() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    instructions = skill.split("<!-- END BLUEPRINT CONTRACT -->", 1)[-1]

    assert "relocate-nodes._rtx.interface.build-review-packet@1" in instructions
    assert "relocate-nodes._rtx.interface.relocate@2" in instructions


def test_skill_documents_recipe_review_and_single_atomic_apply() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "List the mechanical recipe" in skill
    assert "Ask the user for help removing false positives" in skill
    assert "Apply once" in skill
    assert "failure-atomic transaction" in skill
