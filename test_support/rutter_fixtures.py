"""Literal definitions and values shared by Rutter tests."""

from __future__ import annotations

from typing import Callable, Mapping

from officina.rutter.model import (
    SubRutter,
    Charter,
    Terminal,
    Message,
    LLMStep,
    VoyageResult,
    Rutter,
    TransitionHook,
    TransitionMatch,
    EvolutionContext,
)


def example_message() -> Message:
    return Message(
        instructions={
            "text": "Report.",
            "response_schema": response_schema("reported"),
        },
        data={
            "evolution": {
                "id": "report",
                "entry_id": "entry-report",
            },
            "payload": {"chunk": "A"},
        },
    )


def report_data(context: EvolutionContext) -> Mapping[str, object]:
    del context
    return {"chunk": "A"}


def response_schema(*outcomes: str) -> Mapping[str, object]:
    """Return a minimal test schema that constrains only the routing outcome."""

    return {
        "type": "object",
        "properties": {"outcome": {"enum": list(outcomes)}},
        "required": ["outcome"],
    }


def child_charter(context: EvolutionContext) -> Mapping[str, object]:
    del context
    return {"scope": "child"}


def stable_rutter_constructor(
    source: Rutter | type[Rutter],
) -> Callable[[EvolutionContext], Rutter]:
    """Return one lazy, stable definition from a named context callback."""

    definition = source if isinstance(source, Rutter) else None

    def construct(context: EvolutionContext) -> Rutter:
        nonlocal definition
        del context
        if definition is None:
            definition = source()
        return definition

    return construct


def transition_hook_probe(
    hook_id: str,
    child: Rutter | type[Rutter],
    charter: Callable[[object], Mapping[str, object] | None],
) -> TransitionHook:
    """Build a wildcard TransitionHook for shared binding fixtures."""

    child_definition = child if isinstance(child, Rutter) else child()
    return TransitionHook(
        hook_id,
        on=TransitionMatch(),
        rutter_constructor=lambda context: child_definition,
        charter_constructor=charter,
    )


class DirectChildRutter(Rutter):
    rutter_id = "direct-child"
    definition_version = 1
    initial_evolution_id = "complete"

    def define_evolutions(self):
        return {"complete": Terminal(result=VoyageResult("completed", {}))}


class GrandchildRutter(Rutter):
    rutter_id = "grandchild"
    definition_version = 1
    initial_evolution_id = "complete"

    constructions = 0

    def __init__(self) -> None:
        type(self).constructions += 1

    def define_evolutions(self):
        return {"complete": Terminal(result=VoyageResult("completed", {}))}


class AttachedChildRutter(Rutter):
    rutter_id = "attached-child"
    definition_version = 1
    initial_evolution_id = "delegate"

    def define_evolutions(self):
        return {
            "delegate": SubRutter(
                stable_rutter_constructor(GrandchildRutter),
                charter_constructor=child_charter,
                next_on_outcome="complete",
            ),
            "complete": Terminal(result=VoyageResult("completed", {})),
        }


class StaticGrandchildCarrier(Rutter):
    rutter_id = "static-grandchild-carrier"
    definition_version = 1
    initial_evolution_id = "delegate"

    def define_evolutions(self):
        return {
            "delegate": SubRutter(
                stable_rutter_constructor(GrandchildRutter),
                charter_constructor=child_charter,
                next_on_outcome="complete",
            ),
            "complete": Terminal(result=VoyageResult("completed", {})),
        }


ATTACHED_CHILD_RUTTER = AttachedChildRutter()


class DiscoveryRootRutter(Rutter):
    rutter_id = "discovery-root"
    definition_version = 1
    initial_evolution_id = "delegate"

    def define_evolutions(self):
        return {
            "delegate": SubRutter(
                stable_rutter_constructor(DirectChildRutter),
                charter_constructor=child_charter,
                next_on_outcome="complete",
            ),
            "attached": SubRutter(
                stable_rutter_constructor(StaticGrandchildCarrier),
                charter_constructor=child_charter,
                next_on_outcome="complete",
            ),
            "complete": Terminal(result=VoyageResult("completed", {})),
        }

    def define_transition_hooks(self):
        return (
            transition_hook_probe(
                "attached",
                ATTACHED_CHILD_RUTTER,
                child_charter,
            ),
        )


class ExampleRutter(Rutter):
    rutter_id = "example"
    definition_version = 1
    initial_evolution_id = "report"

    def define_evolutions(self):
        return {
            "report": LLMStep(
                "Report.",
                response_schema=response_schema("reported"),
                data=report_data,
                next_on_outcome="complete",
            ),
            "complete": Terminal(result=VoyageResult("completed", {})),
        }
