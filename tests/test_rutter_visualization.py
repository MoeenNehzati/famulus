"""Behavioral tests for extracting visual graphs from Rutter definitions."""

from __future__ import annotations

import json
from pathlib import Path

from officina.rutter import (
    LLMStep,
    MachineResult,
    MachineStep,
    Rutter,
    Terminal,
    TransitionHook,
    VoyageResult,
    after,
    on_transition,
)
from test_support.rutter_fixtures import response_schema as _response_schema
from officina.visualization.graph import Graph
from officina.visualization.from_rutter import (
    RutterVisualizer,
    build_rutter_payload,
)


REVIEW_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {"enum": ["approved", "revise"]},
        "comment": {"type": "string"},
        "requested_changes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["outcome"],
    "additionalProperties": False,
}


class ReviewRutter(Rutter):
    rutter_id = "review"
    definition_version = 3
    initial_evolution_id = "review"

    def define_evolutions(self):
        return {
            "review": LLMStep(
                "Review the proposed change.",
                response_schema=REVIEW_RESPONSE_SCHEMA,
                next_on_outcome={"approved": "publish", "revise": "edit"},
            ),
            "edit": MachineStep(
                record_requested_edits,
                mode="pure",
                next_on_outcome={"edited": "review"},
            ),
            "publish": Terminal(result=VoyageResult("published", {})),
        }

    def define_transition_hooks(self):
        return (
            TransitionHook(
                "audit-review",
                on=after("review"),
                child=HookRutter,
                charter_constructor=lambda context: {},
            ),
            TransitionHook(
                "check-approval",
                on=on_transition(
                    source="review", outcome="approved", target="publish"
                ),
                child=HookRutter,
                charter_constructor=lambda context: {},
            ),
        )


class HookRutter(Rutter):
    rutter_id = "hook"
    definition_version = 1
    initial_evolution_id = "done"

    def define_evolutions(self):
        return {"done": Terminal(result=VoyageResult("checked", {}))}


def record_requested_edits(context) -> MachineResult:
    """Record the requested edits before asking for another review."""
    return MachineResult("edited", {})


def test_payload_exposes_prompts_response_formats_transitions_and_hooks() -> None:
    """Dropping authored interaction data must make the visual contract fail."""
    payload = build_rutter_payload(ReviewRutter)
    entities = {entity["id"]: entity for entity in payload["entities"]}
    review = entities["review"]

    assert payload["schema_version"] == 2
    assert payload["graph_kind"] == "rutter"
    assert payload["metadata"] == {
        "rutter_id": "review",
        "definition_version": 3,
        "initial_evolution_id": "review",
    }
    assert review["description"] == "Review the proposed change."
    assert review["details"]["sections"][0] == {
        "title": "LLM step",
        "fields": [
            {
                "label": "Ask",
                "value": "Review the proposed change.",
                "format": "text",
            },
            {
                "label": "Response format",
                "value": json.dumps(REVIEW_RESPONSE_SCHEMA, indent=2, sort_keys=True),
                "format": "code",
                "copyable": True,
            },
        ],
    }
    edges = {edge["label"]: edge for edge in review["connects_to"]}
    assert edges["approved"]["to"] == "publish"
    assert edges["approved"]["description"] == (
        "Accepted answer outcome 'approved' selects this transition. "
        "Hooks: audit-review, check-approval."
    )
    assert edges["approved"]["metadata"]["hook_ids"] == [
        "audit-review",
        "check-approval",
    ]
    assert edges["revise"]["to"] == "edit"
    assert edges["revise"]["metadata"]["hook_ids"] == ["audit-review"]
    edit = entities["edit"]
    assert edit["description"] == (
        "Record the requested edits before asking for another review."
    )
    assert edit["connects_to"][0]["description"] == (
        "Machine outcome 'edited' selects this transition."
    )
    Graph().validate_graph(payload)


def test_callable_transition_uses_docstring_without_executing_callbacks() -> None:
    """Replacing dynamic-route explanation with execution must fail this test."""
    calls: list[str] = []

    def route(context):
        """Choose retry or completion from the accepted response evidence."""
        calls.append("route")
        return "done"

    class DynamicRutter(Rutter):
        rutter_id = "dynamic"
        definition_version = 1
        initial_evolution_id = "ask"

        def define_evolutions(self):
            return {
                "ask": LLMStep(
                    "Choose the next step.",
                    response_schema=_response_schema("answered"),
                    data=lambda context: calls.append("data") or {},
                    assess_response=lambda context: calls.append("assess"),
                    choose_next=route,
                ),
                "done": Terminal(result=VoyageResult("finished", {})),
            }

    payload = build_rutter_payload(DynamicRutter)
    ask = payload["entities"][0]

    assert calls == []
    assert ask["connects_to"] == []
    assert ask["details"]["sections"][1] == {
        "title": "Dynamic transition",
        "fields": [
            {"label": "Callable", "value": "route", "format": "code"},
            {
                "label": "Explanation",
                "value": (
                    "Choose retry or completion from the accepted response "
                    "evidence."
                ),
                "format": "text",
            },
            {
                "label": "Target",
                "value": "Determined at runtime",
                "format": "text",
            },
        ],
    }
    Graph().validate_graph(payload)


def test_contextual_terminal_uses_constructor_docstring_without_execution() -> None:
    """Dereferencing the absent fixed result or executing its constructor must fail."""
    calls: list[str] = []

    def complete(context) -> VoyageResult:
        """Complete using the active evolution context."""
        calls.append(context.evolution_id)
        return VoyageResult("finished", {})

    class ContextualTerminalRutter(Rutter):
        rutter_id = "contextual-terminal"
        definition_version = 1
        initial_evolution_id = "done"

        def define_evolutions(self):
            return {"done": Terminal(result_constructor=complete)}

    payload = build_rutter_payload(ContextualTerminalRutter)

    assert calls == []
    assert payload["entities"][0]["description"] == (
        "Complete using the active evolution context."
    )
    Graph().validate_graph(payload)


def test_visualizer_writes_json_and_html_from_the_same_payload(tmp_path: Path) -> None:
    """Bypassing the shared renderer or omitting JSON must fail artifact output."""
    paths = RutterVisualizer().build(ReviewRutter, output_dir=tmp_path)

    assert paths == [tmp_path / "review.json", tmp_path / "review.html"]
    assert all(path.is_file() for path in paths)
