#!/usr/bin/env python3
"""Contract tests for maintainer-only graph evaluation references."""

from pathlib import Path

import yaml


SKILL_DIR = Path(__file__).resolve().parents[2]


def test_skill_exposes_gold_and_experiment_playbooks_without_worker_contamination() -> None:
    """Evaluation guidance must be discoverable without entering worker prompts."""

    experiment_path = SKILL_DIR / "references" / "experimental-improvement.md"
    gold_path = SKILL_DIR / "references" / "gold-standard-extraction.md"
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    inventory_text = (SKILL_DIR / "instructions" / "inventory.md").read_text(
        encoding="utf-8"
    )
    extract_text = (SKILL_DIR / "instructions" / "extract.md").read_text(
        encoding="utf-8"
    )
    blueprint = yaml.safe_load(
        (SKILL_DIR / "blueprint.yaml").read_text(encoding="utf-8")
    )

    assert experiment_path.is_file()
    assert gold_path.is_file()
    assert "references/experimental-improvement.md" in skill_text
    assert "references/gold-standard-extraction.md" in skill_text
    assert "references/" in blueprint["content"][0]
    assert "gold-standard" not in inventory_text
    assert "gold-standard" not in extract_text
    assert "experimental-improvement" not in inventory_text
    assert "experimental-improvement" not in extract_text


def test_experimental_playbook_adjudicates_discrepancies_before_designing_fixes() -> None:
    """The improvement loop must decide each mismatch before grouping causes."""

    text = (
        SKILL_DIR / "references" / "experimental-improvement.md"
    ).read_text(encoding="utf-8")
    normalized = text.lower()

    required = (
        "potentially flawed gold",
        "context-free",
        "queue time",
        "worker time",
        "node recall",
        "direct-edge recall",
        "contested",
        "tie-breaker",
        "one hypothesis",
        "do not dispatch compaction",
        "adjudicate every discrepancy",
        "worker is wrong",
        "gold is wrong",
        "both are wrong",
        "underlying cause",
        "proposed solution",
    )
    for phrase in required:
        assert phrase in normalized

    assert "render HTML" not in text
    assert "Inspect the HTML" not in text
    assert "rendered HTML" not in text


def test_experimental_playbook_measures_proof_reconciliation_without_leaking_gold() -> None:
    """Proof normalization is a distinct measured production stage, never evaluator context."""

    text = (SKILL_DIR / "references" / "experimental-improvement.md").read_text(
        encoding="utf-8"
    ).lower()
    for phrase in (
        "proof-reconciliation worker",
        "proof target",
        "proof bundle",
        "proof provenance",
        "normalization stage",
        "model id",
        "queue time",
        "worker time",
    ):
        assert phrase in text
    assert "graph workers are context-free" in text
    assert "evaluator sees both artifacts" in text


def test_gold_playbook_requires_canonical_evidence_audit_and_render() -> None:
    """Gold construction must be independent, canonical, evidenced, and inspectable."""

    text = (
        SKILL_DIR / "references" / "gold-standard-extraction.md"
    ).read_text(encoding="utf-8")

    required = (
        "production inventory and extract instructions",
        "canonical graph JSON",
        "every entity",
        "every direct edge",
        "source evidence",
        "independent reviewers",
        "tie-breaker",
        "schema-valid",
        "HTML",
    )
    for phrase in required:
        assert phrase in text
