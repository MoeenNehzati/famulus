"""Literal definitions and values shared by Rutter tests."""

from __future__ import annotations

from typing import Callable, Mapping

from officina.rutter.model import (
    AnswerSpec,
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
        instructions={"text": "Report.", "answer": {"reported": {}}},
        data={
            "evolution": {
                "id": "report",
                "entry_id": "entry-report",
                "revision": 1,
            },
            "payload": {"chunk": "A"},
        },
    )


def report_data(context: EvolutionContext) -> Mapping[str, object]:
    del context
    return {"chunk": "A"}


def child_charter(context: EvolutionContext) -> Mapping[str, object]:
    del context
    return {"scope": "child"}


def transition_hook_probe(
    hook_id: str,
    child: type[Rutter],
    charter: Callable[[object], Mapping[str, object] | None],
) -> TransitionHook:
    """Build a wildcard TransitionHook for shared binding fixtures."""

    return TransitionHook(
        hook_id,
        on=TransitionMatch(),
        child=child,
        charter=charter,
    )


class DirectChildRutter(Rutter):
    rutter_id = "direct-child"
    definition_version = 1
    initial_evolution_id = "complete"

    def define_evolutions(self):
        return {"complete": Terminal(VoyageResult("completed", {}))}


class GrandchildRutter(Rutter):
    rutter_id = "grandchild"
    definition_version = 1
    initial_evolution_id = "complete"

    constructions = 0

    def __init__(self) -> None:
        type(self).constructions += 1

    def define_evolutions(self):
        return {"complete": Terminal(VoyageResult("completed", {}))}


class AttachedChildRutter(Rutter):
    rutter_id = "attached-child"
    definition_version = 1
    initial_evolution_id = "delegate"

    def define_evolutions(self):
        return {
            "delegate": SubRutter(
                GrandchildRutter,
                charter=child_charter,
                then="complete",
            ),
            "complete": Terminal(VoyageResult("completed", {})),
        }


class DiscoveryRootRutter(Rutter):
    rutter_id = "discovery-root"
    definition_version = 1
    initial_evolution_id = "delegate"

    def define_evolutions(self):
        return {
            "delegate": SubRutter(
                DirectChildRutter,
                charter=child_charter,
                then="complete",
            ),
            "complete": Terminal(VoyageResult("completed", {})),
        }

    def define_transition_hooks(self):
        return (
            transition_hook_probe(
                "attached",
                AttachedChildRutter,
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
                answer=AnswerSpec({"reported": {}}),
                data=report_data,
                then="complete",
            ),
            "complete": Terminal(VoyageResult("completed", {})),
        }
