from __future__ import annotations

from _graph_builder import prepare_math_payload


def test_prepare_math_payload_derives_categories_when_catalog_is_absent() -> None:
    payload = {
        "schema_version": 2,
        "entities": [{"id": "result", "label": "Result", "type": "theorem"}]
    }

    prepared = prepare_math_payload(payload)

    assert prepared["entities"][0]["category"] == "theorem"


def test_prepare_math_payload_does_not_invent_category_for_caller_catalog() -> None:
    payload = {
        "schema_version": 2,
        "categories": [
            {"id": "main-results", "label": "Main results", "color": "#123456"}
        ],
        "entities": [{"id": "result", "label": "Result", "type": "theorem"}],
    }

    prepared = prepare_math_payload(payload)

    assert prepared["categories"] == payload["categories"]
    assert "category" not in prepared["entities"][0]


def test_prepare_math_payload_preserves_explicit_entity_category() -> None:
    payload = {
        "schema_version": 2,
        "categories": [{"id": "main-results", "label": "Main results"}],
        "entities": [
            {
                "id": "result",
                "label": "Result",
                "type": "theorem",
                "category": "main-results",
            }
        ],
    }

    prepared = prepare_math_payload(payload)

    assert prepared["entities"][0]["category"] == "main-results"
