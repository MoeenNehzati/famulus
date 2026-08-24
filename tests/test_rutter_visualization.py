"""Behavioral tests for extracting visual graphs from Rutter definitions."""

from __future__ import annotations

from pathlib import Path

from officina.rutter import (
    AnswerSpec,
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
from officina.visualization.graph import Graph
from officina.visualization.from_rutter import (
    RutterVisualizer,
    build_rutter_payload,
)


class ReviewRutter(Rutter):
    rutter_id = "review"
    definition_version = 3
    initial_evolution_id = "review"

    def define_evolutions(self):
        return {
            "review": LLMStep(
                "Review the proposed change.",
                answer=AnswerSpec(
                    {
                        "approved": {"comment": "string"},
                        "revise": {"requested_changes": ["string"]},
                    }
                ),
                then={"approved": "publish", "revise": "edit"},
            ),
            "edit": MachineStep(
                record_requested_edits,
                mode="pure",
                then={"edited": "review"},
            ),
            "publish": Terminal(VoyageResult("published", {})),
        }

    def define_transition_hooks(self):
        return (
            TransitionHook(
                "audit-review",
                on=after("review"),
                child=HookRutter,
                charter=lambda context: {},
            ),
            TransitionHook(
                "check-approval",
                on=on_transition(
                    source="review", outcome="approved", target="publish"
                ),
                child=HookRutter,
                charter=lambda context: {},
            ),
        )


class HookRutter(Rutter):
    rutter_id = "hook"
    definition_version = 1
    initial_evolution_id = "done"

    def define_evolutions(self):
        return {"done": Terminal(VoyageResult("checked", {}))}


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
                "value": (
                    '{\n'
                    '  "approved": {\n'
                    '    "comment": "string"\n'
                    '  },\n'
                    '  "revise": {\n'
                    '    "requested_changes": [\n'
                    '      "string"\n'
                    '    ]\n'
                    '  }\n'
                    '}'
                ),
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
                    answer=AnswerSpec({"answered": {"choice": "string"}}),
                    data=lambda context: calls.append("data") or {},
                    validate=lambda context: calls.append("validate"),
                    then=route,
                ),
                "done": Terminal(VoyageResult("finished", {})),
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


def test_visualizer_writes_json_and_html_from_the_same_payload(tmp_path: Path) -> None:
    """Bypassing the shared renderer or omitting JSON must fail artifact output."""
    paths = RutterVisualizer().build(ReviewRutter, output_dir=tmp_path)

    assert paths == [tmp_path / "review.json", tmp_path / "review.html"]
    assert all(path.is_file() for path in paths)
