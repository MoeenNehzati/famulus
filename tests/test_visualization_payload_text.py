"""Tests for producer-owned visible text in visualization payloads."""

from __future__ import annotations

import pytest

from officina.visualization.from_docstring.payload_builder import _build_entity


@pytest.mark.parametrize(
    ("entity_id", "entity_type", "short_title", "subtitle"),
    [
        ("package.module", "module", "module", "module"),
        ("package.module.Model", "class", "Model", "class"),
        ("package.module.Model.run", "callable", "run", "callable"),
        (
            "package.external.module",
            "external-module",
            "module",
            "external module",
        ),
        ("package.module.source", "source", "source", "source"),
    ],
)
def test_docstring_entity_emits_concise_title_and_kind_subtitle(
    entity_id: str,
    entity_type: str,
    short_title: str,
    subtitle: str,
) -> None:
    """Dropping or deriving visible text from a dotted entity id must fail."""
    entity = _build_entity(
        entity_id=entity_id,
        entity_type=entity_type,
        short_title=short_title,
        position=0,
    )

    assert (
        entity["short_title"],
        entity["subtitle"],
        entity["type"],
    ) == (short_title, subtitle, entity_type)
    assert "." not in entity["subtitle"]
