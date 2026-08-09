from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def _yaml(relative: str) -> dict:
    value = yaml.safe_load((REPO_ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _normalized(relative: str) -> str:
    return " ".join((REPO_ROOT / relative).read_text(encoding="utf-8").split())


def test_skill_maker_queries_standards_and_builds_an_authoring_brief() -> None:
    gateway = _yaml("skills/skill-maker/blueprints/gateway.yaml")

    assert {
        use["interface"] for use in gateway["uses_interfaces"]
    } == {
        "common.interface.query-standard",
        "skill-maker._rtx.interface.sync-blueprints",
    }
    query_use = next(
        use
        for use in gateway["uses_interfaces"]
        if use["interface"] == "common.interface.query-standard"
    )
    assert query_use["version"] == 1

    skill = _normalized("skills/skill-maker/SKILL.md")
    for required in (
        "Standards retrieval",
        "common.interface.query-standard",
        "task.kind=author-skill",
        "--view requirements",
        "--view context",
        "--view evidence",
        "--view remedies",
        "--view full",
        "--refs-json",
        "--query-json",
        "requirements.true",
        "requirements.unknown",
        "context_index",
        "complete pinned import closure",
        "missing facts",
        "checks, tests, and assurances",
        "semantic_reviews",
        "artifacts",
        "remedies",
    ):
        assert required in skill
    for standard in (
        "python-module.standard.yaml",
        "python-behavioral-source.standard.yaml",
        "instruction-module.standard.yaml",
        "instruction-behavioral-source.standard.yaml",
    ):
        assert standard in skill
    assert "schema-minimum skill" in skill
    assert "items.true" not in skill
    assert len(skill.split()) < 700


def test_refactor_node_builds_a_refactoring_brief_from_each_selected_root() -> None:
    skill = _normalized("skills/refactor-node/SKILL.md")

    for required in (
        "Standards retrieval",
        "common.interface.query-standard",
        "task.kind=refactor",
        "--view requirements",
        "--view context",
        "--view evidence",
        "--view remedies",
        "--view full",
        "--refs-json",
        "--query-json",
        "requirements.true",
        "requirements.unknown",
        "context_index",
        "complete pinned import closure",
        "missing facts",
        "checks, tests, and assurances",
        "semantic_reviews",
        "artifacts",
        "remedies",
    ):
        assert required in skill
    for standard in (
        "python-module.standard.yaml",
        "python-behavioral-source.standard.yaml",
        "instruction-module.standard.yaml",
        "instruction-behavioral-source.standard.yaml",
    ):
        assert standard in skill
    assert "items.true" not in skill
    assert len(skill.split()) < 700


def test_language_routes_use_the_whole_applicable_closure() -> None:
    python = _normalized("skills/refactor-node/instructions/python-refactoring.md")
    instructions = _normalized(
        "skills/refactor-node/instructions/instruction-refactoring.md"
    )

    assert "all applicable rule assertions and guidance" in python
    assert "python-ood.* items for Python-specific diagnosis" in python
    assert "all applicable rule assertions and guidance" in instructions
    assert "semantic-review work" in python
    assert "semantic-review work" in instructions
