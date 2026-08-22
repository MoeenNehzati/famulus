"""Literal definitions and values shared by Rutter tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from officina.rutter.model import (
    AnswerSpec,
    Call,
    Charter,
    Done,
    Message,
    Prompt,
    RunResult,
    Rutter,
    StateContext,
)


def example_message() -> Message:
    return Message(
        instructions={"text": "Report.", "answer": {"reported": {}}},
        data={
            "state": {
                "id": "report",
                "entry_id": "entry-report",
                "revision": 1,
            },
            "payload": {"chunk": "A"},
        },
    )


def report_data(context: StateContext) -> Mapping[str, object]:
    del context
    return {"chunk": "A"}


def child_charter(context: StateContext) -> Mapping[str, object]:
    del context
    return {"scope": "child"}


@dataclass(frozen=True)
class CaseMakerProbe:
    """Stand in for the Task 8 public CaseMaker value at the binding seam."""

    id: str
    child: type[Rutter]
    charter: Callable[[object], Mapping[str, object] | None]


class DirectChildRutter(Rutter):
    rutter_id = "direct-child"
    definition_version = 1
    start_state = "complete"

    def define_states(self):
        return {"complete": Done(RunResult("completed", {}))}


class GrandchildRutter(Rutter):
    rutter_id = "grandchild"
    definition_version = 1
    start_state = "complete"

    constructions = 0

    def __init__(self) -> None:
        type(self).constructions += 1

    def define_states(self):
        return {"complete": Done(RunResult("completed", {}))}


class AttachedChildRutter(Rutter):
    rutter_id = "attached-child"
    definition_version = 1
    start_state = "delegate"

    def define_states(self):
        return {
            "delegate": Call(
                GrandchildRutter,
                charter=child_charter,
                then="complete",
            ),
            "complete": Done(RunResult("completed", {})),
        }


class DiscoveryRootRutter(Rutter):
    rutter_id = "discovery-root"
    definition_version = 1
    start_state = "delegate"

    def define_states(self):
        return {
            "delegate": Call(
                DirectChildRutter,
                charter=child_charter,
                then="complete",
            ),
            "complete": Done(RunResult("completed", {})),
        }

    def define_case_makers(self):
        return (
            CaseMakerProbe(
                "attached",
                AttachedChildRutter,
                child_charter,
            ),
        )


class ExampleRutter(Rutter):
    rutter_id = "example"
    definition_version = 1
    start_state = "report"

    def define_states(self):
        return {
            "report": Prompt(
                "Report.",
                answer=AnswerSpec({"reported": {}}),
                data=report_data,
                then="complete",
            ),
            "complete": Done(RunResult("completed", {})),
        }
