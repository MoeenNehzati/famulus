"""Synthetic tests for catalog-driven generated skill documentation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import officina.blueprints.graph as blueprint_graph
from docs_tooling.catalog import SkillInfo, load_catalog, skills_by_domain
from docs_tooling.render import render_doc_with_updated_blocks, render_skill_index


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def live_catalog() -> tuple[SkillInfo, ...]:
    """Load the immutable repository catalog once for live contract checks."""

    return tuple(load_catalog(REPO_ROOT))


def _write_skill(
    root: Path,
    name: str,
    *,
    domain: str,
    topics: list[str],
    visibility: str,
    activated_by: list[str] | None = None,
    persistent_modifier: bool = False,
) -> None:
    config_path = root / "references" / "blueprint-schema" / "config.yaml"
    if not config_path.exists():
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            """blueprint_catalog:
  domains: [research, assistant-interaction]
  topics: [mathematical-reasoning, research-writing, scholarly-documents, session-management, reasoning-control]
  visibility: [featured, listed, hidden]
  activated_by: [user-request, skill-workflow, scheduled-job]
""",
            encoding="utf-8",
        )
    skill_root = root / "skills" / name
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when handling {name}.\n---\n",
        encoding="utf-8",
    )
    blueprint = {
        "schema_version": 6,
        "node_type": "module",
        "id": name,
        "version": 1,
        "maturity": "stable",
        "gateway": {"path": "SKILL.md", "language": "Markdown"},
        "content": [r"SKILL\.md"],
        "discovery": {
            "mechanism": "skill",
            "catalog": {
                "domain": domain,
                "topics": topics,
                "visibility": visibility,
            },
            "activated_by": activated_by or ["user-request"],
            "persistent_modifier": persistent_modifier,
        },
        "installation_tier": "core",
        "personal_preference": {"applies": False},
        "authority": {"owns_filesystem": []},
        "sources": {},
        "children": {},
        "namespace_exports": {},
        "exports": {},
    }
    (skill_root / "blueprint.yaml").write_text(
        yaml.safe_dump(blueprint, sort_keys=False),
        encoding="utf-8",
    )


def test_load_catalog_reuses_module_schema_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_skill(
        tmp_path,
        "proof-audit",
        domain="research",
        topics=["mathematical-reasoning"],
        visibility="featured",
    )
    _write_skill(
        tmp_path,
        "session-guide",
        domain="assistant-interaction",
        topics=["session-management"],
        visibility="listed",
    )
    loaded_schema_names: list[str] = []
    real_load_schema_validator = blueprint_graph._load_schema_validator

    def counted_load_schema_validator(schema_path: Path):
        loaded_schema_names.append(schema_path.name)
        return real_load_schema_validator(schema_path)

    monkeypatch.setattr(
        blueprint_graph,
        "_load_schema_validator",
        counted_load_schema_validator,
    )

    catalog = load_catalog(tmp_path)

    assert [
        (
            skill.name,
            skill.domain,
            skill.topics,
            skill.visibility,
            skill.activated_by,
            skill.persistent_modifier,
        )
        for skill in catalog
    ] == [
        (
            "proof-audit",
            "research",
            ("mathematical-reasoning",),
            "featured",
            ("user-request",),
            False,
        ),
        (
            "session-guide",
            "assistant-interaction",
            ("session-management",),
            "listed",
            ("user-request",),
            False,
        ),
    ]
    assert loaded_schema_names == ["module.schema.json"]


def test_domain_grouping_omits_hidden_skills_by_default() -> None:
    grouped = skills_by_domain(
        [
            SkillInfo(
                name="visible",
                domain="assistant-interaction",
                topics=("session-management",),
                visibility="listed",
                activated_by=("user-request",),
                persistent_modifier=False,
                summary="Visible summary",
                description="Use when the visible skill applies.",
            ),
            SkillInfo(
                name="hidden",
                domain="assistant-interaction",
                topics=("session-management",),
                visibility="hidden",
                activated_by=("user-request",),
                persistent_modifier=False,
                summary="Hidden summary",
                description="Use when the hidden skill applies.",
            ),
        ]
    )

    assert [skill.name for skill in grouped["assistant-interaction"]] == ["visible"]


def test_render_document_uses_supplied_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = [
        SkillInfo(
            name="proof-audit",
            domain="research",
            topics=("mathematical-reasoning",),
            visibility="featured",
            activated_by=("user-request",),
            persistent_modifier=False,
            summary="Audit a mathematical proof",
            description="Use when a mathematical proof needs auditing.",
        )
    ]
    doc_path = tmp_path / "docs" / "domains" / "research.md"
    doc_path.parent.mkdir(parents=True)
    doc_path.write_text(
        "<!-- BEGIN AUTO-GENERATED DOCS: research -->\n"
        "stale\n"
        "<!-- END AUTO-GENERATED DOCS: research -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "docs_tooling.render.load_catalog",
        lambda _repo_root: pytest.fail("catalog was reconstructed"),
    )

    rendered = render_doc_with_updated_blocks(
        tmp_path,
        Path("docs/domains/research.md"),
        catalog=catalog,
    )

    assert rendered == (
        "<!-- BEGIN AUTO-GENERATED DOCS: research -->\n"
        "> Generated from live blueprints. Do not edit this block by hand.\n\n"
        "- `proof-audit` — Audit a mathematical proof\n"
        "<!-- END AUTO-GENERATED DOCS: research -->\n"
    )


def test_multiline_description_uses_standalone_first_sentence_as_summary(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path,
        "notation-review",
        domain="research",
        topics=[
            "mathematical-reasoning",
            "research-writing",
            "reasoning-control",
        ],
        visibility="featured",
        activated_by=["user-request", "skill-workflow"],
        persistent_modifier=True,
    )
    _write_skill(
        tmp_path,
        "listed-skill",
        domain="research",
        topics=["scholarly-documents"],
        visibility="listed",
    )
    (tmp_path / "skills" / "notation-review" / "SKILL.md").write_text(
        """---
name: notation-review
description: |
  Use when mathematical notation needs review for clarity and consistency.

  Use when:
  - symbols should be unified

  Do not use when:
  - the task is prose editing
---
""",
        encoding="utf-8",
    )

    rendered = render_skill_index(tmp_path)

    assert "Mathematical notation needs review for clarity and consistency" in rendered
    assert "symbols should be unified" not in rendered
    assert "Do not use when" not in rendered
    assert "## Research" in rendered
    assert "### Featured" in rendered
    assert "`notation-review`" in rendered
    assert "research-writing" in rendered
    assert "activated by: user request, skill workflow" in rendered
    assert "persistent modifier" in rendered
    assert "### Listed" in rendered
    assert "`listed-skill`" in rendered
    assert "scholarly-documents" in rendered


def test_repository_multiline_skill_summaries_remain_catalog_safe(
    live_catalog: tuple[SkillInfo, ...],
) -> None:
    summaries = {skill.name: skill.summary for skill in live_catalog}

    assert summaries["notation-review"] == (
        "Review, simplify, or standardize mathematical notation"
    )
    assert summaries["technical-flow-review"] == (
        "For document-level review of technical structure, motivation, or reader flow"
    )
    descriptions = {skill.name: skill.description for skill in live_catalog}
    assert descriptions["regenerate-blueprints"] == (
        "Use when an existing skill blueprint needs regeneration, whether "
        "requested directly or required by another skill. Do not use for ordinary "
        "blueprint editing or synchronization."
    )


def test_catalog_errors_name_field_and_configured_choices(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "bad-topic",
        domain="research",
        topics=["not-configured"],
        visibility="listed",
    )

    try:
        load_catalog(tmp_path)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("invalid configured topic was accepted")

    assert "discovery.catalog.topics" in message
    assert "choose from:" in message
    assert "mathematical-reasoning" in message
