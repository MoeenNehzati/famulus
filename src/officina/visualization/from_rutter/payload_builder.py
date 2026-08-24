"""Build canonical visualization payloads from stateless Rutter definitions."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from typing import Any

from officina.rutter import (
    LLMStep,
    MachineStep,
    Rutter,
    RutterDefinitionError,
    SubRutter,
    Terminal,
    TransitionHook,
)
from officina.visualization.base_renderer import BaseRenderer


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _callable_name(callback: object) -> str:
    return getattr(callback, "__name__", type(callback).__name__)


def _callable_description(callback: object) -> str:
    return inspect.getdoc(callback) or "No explanation provided."


def _rutter_label(rutter: object, *, contextual: bool) -> str:
    if contextual:
        return "Determined at runtime"
    if isinstance(rutter, Rutter):
        return rutter.rutter_id
    if isinstance(rutter, type) and issubclass(rutter, Rutter):
        return rutter.rutter_id
    return "Determined at runtime"


def _evolution_kind(evolution: object) -> str:
    if isinstance(evolution, LLMStep):
        return "llm-step"
    if isinstance(evolution, MachineStep):
        return "machine-step"
    if isinstance(evolution, SubRutter):
        return "sub-rutter"
    if isinstance(evolution, Terminal):
        return "terminal"
    raise RutterDefinitionError(
        "Rutter evolutions must be LLMStep, MachineStep, SubRutter, or Terminal"
    )


def _evolution_description(evolution: object) -> str:
    if isinstance(evolution, LLMStep):
        return evolution.text
    if isinstance(evolution, MachineStep):
        return _callable_description(evolution.run)
    if isinstance(evolution, SubRutter):
        child = _rutter_label(evolution.rutter_constructor, contextual=True)
        return f"Enter child Rutter '{child}'."
    assert isinstance(evolution, Terminal)
    if evolution.result_constructor is not None:
        return _callable_description(evolution.result_constructor)
    assert evolution.result is not None
    return f"Complete with outcome '{evolution.result.outcome}'."


def _llm_step_section(evolution: LLMStep) -> dict[str, object]:
    response_format = json.dumps(
        _plain_json(evolution.response_schema), indent=2, sort_keys=True
    )
    return {
        "title": "LLM step",
        "fields": [
            {
                "label": "Ask",
                "value": evolution.text,
                "format": "text",
            },
            {
                "label": "Response format",
                "value": response_format,
                "format": "code",
                "copyable": True,
            },
        ],
    }


def _dynamic_section(callback: object) -> dict[str, object]:
    return {
        "title": "Dynamic transition",
        "fields": [
            {"label": "Callable", "value": _callable_name(callback), "format": "code"},
            {
                "label": "Explanation",
                "value": _callable_description(callback),
                "format": "text",
            },
            {
                "label": "Target",
                "value": "Determined at runtime",
                "format": "text",
            },
        ],
    }


def _evolution_sections(
    evolution_id: str, evolution: object, initial_evolution_id: str
) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    if isinstance(evolution, LLMStep):
        sections.append(_llm_step_section(evolution))
    if hasattr(evolution, "choose_next") and callable(evolution.choose_next):
        sections.append(_dynamic_section(evolution.choose_next))
    fields: list[dict[str, object]] = [
        {"label": "Evolution ID", "value": evolution_id, "format": "code"},
        {"label": "Kind", "value": _evolution_kind(evolution), "format": "text"},
        {
            "label": "Initial evolution",
            "value": evolution_id == initial_evolution_id,
            "format": "text",
        },
    ]
    if isinstance(evolution, MachineStep):
        fields.append({"label": "Mode", "value": evolution.mode, "format": "text"})
    if isinstance(evolution, SubRutter):
        fields.append(
            {
                "label": "Child Rutter",
                "value": _rutter_label(
                    evolution.rutter_constructor,
                    contextual=True,
                ),
                "format": "code",
            }
        )
    sections.append({"title": "Evolution", "fields": fields})
    return sections


def _routes(evolution: object) -> list[tuple[str, str]]:
    next_on_outcome = getattr(evolution, "next_on_outcome", None)
    if type(next_on_outcome) is str:
        if isinstance(evolution, LLMStep):
            return [("any accepted outcome", next_on_outcome)]
        return [("any outcome", next_on_outcome)]
    if isinstance(next_on_outcome, Mapping):
        return [
            (str(outcome), str(target)) for outcome, target in next_on_outcome.items()
        ]
    return []


def _matching_hooks(
    transition_hooks: tuple[TransitionHook, ...],
    source: str,
    outcome: str,
    target: str,
) -> list[TransitionHook]:
    matches = []
    for hook in transition_hooks:
        transition_match = hook.on
        if (
            (transition_match.source is None or transition_match.source == source)
            and (transition_match.outcome is None or transition_match.outcome == outcome)
            and (transition_match.target is None or transition_match.target == target)
        ):
            matches.append(hook)
    return matches


def _transition(
    *,
    source: str,
    source_kind: str,
    outcome: str,
    target: str,
    transition_hooks: tuple[TransitionHook, ...],
) -> dict[str, object]:
    hooks = _matching_hooks(transition_hooks, source, outcome, target)
    hook_ids = [hook.id for hook in hooks]
    outcome_source = {
        "llm-step": "accepted answer",
        "machine-step": "machine",
        "sub-rutter": "child",
    }.get(source_kind, "evolution")
    trigger = (
        f"Any {outcome_source} outcome selects this transition."
        if outcome in {"any outcome", "any accepted outcome"}
        else f"{outcome_source.capitalize()} outcome '{outcome}' selects this transition."
    )
    description = trigger
    if hook_ids:
        description += f" Hooks: {', '.join(hook_ids)}."
    hook_fields = [
        {
            "label": "Attached hooks",
            "value": [
                (
                    f"{hook.id} -> "
                    f"{_rutter_label(hook.rutter_constructor, contextual=True)}"
                )
                for hook in hooks
            ],
            "format": "list",
        }
    ]
    return {
        "to": target,
        "type": "transition",
        "label": outcome,
        "description": description,
        "metadata": {"outcome": outcome, "hook_ids": hook_ids},
        "details": {
            "summary": description,
            "sections": [
                {
                    "title": "Trigger",
                    "fields": [
                        {"label": "Outcome", "value": outcome, "format": "code"}
                    ],
                },
                {"title": "Hooks", "fields": hook_fields},
            ],
        },
    }


def build_rutter_payload(rutter_class: Rutter | type[Rutter]) -> dict[str, Any]:
    """Return schema-v2 graph JSON for one Rutter definition."""
    if not isinstance(rutter_class, Rutter) and not (
        isinstance(rutter_class, type) and issubclass(rutter_class, Rutter)
    ):
        raise TypeError("rutter_class must be a Rutter instance or class")
    try:
        definition = (
            rutter_class if isinstance(rutter_class, Rutter) else rutter_class()
        )
        evolutions = definition.define_evolutions()
        transition_hooks = definition.define_transition_hooks()
    except Exception as exc:
        raise RutterDefinitionError("Rutter definition extraction failed") from exc
    if not isinstance(evolutions, Mapping) or not evolutions:
        raise RutterDefinitionError(
            "define_evolutions() must return a nonempty mapping"
        )
    if type(transition_hooks) is not tuple:
        raise RutterDefinitionError("define_transition_hooks() must return a tuple")

    entities = []
    for position, (evolution_id, evolution) in enumerate(evolutions.items()):
        evolution_id = str(evolution_id)
        description = _evolution_description(evolution)
        entities.append(
            {
                "id": evolution_id,
                "type": _evolution_kind(evolution),
                "short_title": evolution_id,
                "label": evolution_id,
                "position": position,
                "description": description,
                "details": {
                    "summary": description,
                    "sections": _evolution_sections(
                        evolution_id, evolution, definition.initial_evolution_id
                    ),
                },
                "connects_to": [
                    _transition(
                        source=evolution_id,
                        source_kind=_evolution_kind(evolution),
                        outcome=outcome,
                        target=target,
                        transition_hooks=transition_hooks,
                    )
                    for outcome, target in _routes(evolution)
                ],
            }
        )

    payload: dict[str, Any] = {
        "schema_version": 2,
        "graph_kind": "rutter",
        "graph_id": f"rutter:{definition.rutter_id}:v{definition.definition_version}",
        "document": {"title": f"{definition.rutter_id} Rutter"},
        "metadata": {
            "rutter_id": definition.rutter_id,
            "definition_version": definition.definition_version,
            "initial_evolution_id": definition.initial_evolution_id,
        },
        "entities": entities,
    }
    BaseRenderer().validate(payload)
    return payload


__all__ = ["build_rutter_payload"]
