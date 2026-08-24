"""Catalog contract tests owned by the math-dependency-graph skill."""

from __future__ import annotations

from pathlib import Path

from docs_tooling.catalog import load_catalog


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_description_is_concise_trigger_only() -> None:
    skill = next(
        skill
        for skill in load_catalog(REPOSITORY_ROOT)
        if skill.name == "math-dependency-graph"
    )

    assert skill.description == (
        "Use when the user asks for a direct assumptions-to-results dependency "
        "graph of a TeX or Markdown mathematical document. Do not use for proof, "
        "notation, prose, or literature review."
    )
