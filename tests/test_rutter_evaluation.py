"""Exercise the controlled boundary for concrete authored callbacks."""

from __future__ import annotations

import pytest

from officina.rutter import evaluation
from officina.rutter.authoring import (
    EvolutionContext,
    LLMResponseContext,
    LLMStep,
    MachineContext,
    MachineStep,
    Rutter,
    SubRutter,
    Terminal,
    TransitionContext,
    TransitionHook,
    TransitionMatch,
)
from officina.rutter.history import HistoryView, TerminalRecord, Transition
from officina.rutter.values import (
    AnswerSpec,
    Charter,
    MachineResult,
    Message,
    Response,
    ValidationReport,
    VoyageResult,
)


class _Child(Rutter):
    rutter_id = "child"
    definition_version = 1
    initial_evolution_id = "done"

    def define_evolutions(self):
        return {"done": Terminal(VoyageResult("complete", {}))}


def _context() -> EvolutionContext:
    return EvolutionContext(Charter({}), "review", "entry-review", HistoryView(()))


def _message() -> Message:
    return Message(
        {"text": "Review.", "answer": {"approved": {}}},
        {
            "evolution": {
                "id": "review",
                "entry_id": "entry-review",
                "revision": 0,
            },
            "payload": {},
        },
    )


def test_llm_callbacks_return_exact_values_and_route_failures_are_typed() -> None:
    context = _context()
    step = LLMStep(
        "Review.",
        answer=AnswerSpec({"approved": {}}),
        data=lambda value: {"seen": value.evolution_id},
        validate=lambda value: ValidationReport(True),
        next_on_outcome="done",
    )
    response_context = LLMResponseContext(
        context,
        _message(),
        Response(0, "approved", {}),
    )

    assert evaluation.build_llm_data(context, step) == {"seen": "review"}
    assert evaluation.validate_llm_response(response_context, step) == ValidationReport(True)
    with pytest.raises(evaluation._RutterFault) as error:
        evaluation.evaluate_llm_route(
            response_context,
            lambda value: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    assert error.value.category == "routing"


@pytest.mark.parametrize(
    "operation",
    (
        lambda: evaluation.evaluate_llm_route(
            LLMResponseContext(
                _context(),
                _message(),
                Response(0, "approved", {}),
            ),
            lambda context: object(),
        ),
        lambda: evaluation.evaluate_machine_route(
            MachineContext(_context(), "machine-1"),
            MachineResult("stored", {}),
            lambda context, result: object(),
        ),
        lambda: evaluation.evaluate_subrutter_route(
            _context(),
            VoyageResult("complete", {}),
            lambda context, result: object(),
        ),
    ),
)
def test_route_callbacks_reject_non_string_results_as_typed_fault(operation) -> None:
    with pytest.raises(evaluation._RutterFault) as error:
        operation()

    assert error.value.category == "routing"


def test_llm_data_normalizes_the_declared_json_object() -> None:
    step = LLMStep(
        "Review.",
        answer=AnswerSpec({"approved": {}}),
        data=lambda context: {"items": [{"ready": True}]},
        next_on_outcome="done",
    )

    assert evaluation.build_llm_data(_context(), step) == {
        "items": ({"ready": True},),
    }


def test_llm_data_rejects_malformed_json_as_typed_fault() -> None:
    step = LLMStep(
        "Review.",
        answer=AnswerSpec({"approved": {}}),
        data=lambda context: {"bad": object()},
        next_on_outcome="done",
    )

    with pytest.raises(evaluation._RutterFault) as error:
        evaluation.build_llm_data(_context(), step)

    assert error.value.category == "materialization"


def test_llm_validator_rejects_non_report_as_typed_fault() -> None:
    step = LLMStep(
        "Review.",
        answer=AnswerSpec({"approved": {}}),
        validate=lambda context: {"valid": True},
        next_on_outcome="done",
    )
    response_context = LLMResponseContext(
        _context(),
        _message(),
        Response(0, "approved", {}),
    )

    with pytest.raises(evaluation._RutterFault) as error:
        evaluation.validate_llm_response(response_context, step)

    assert error.value.category == "contextual-validation"


def test_subrutter_charter_normalizes_the_declared_json_object() -> None:
    step = SubRutter(
        _Child,
        charter=lambda context: {"items": [{"ready": True}]},
        next_on_outcome="done",
    )

    assert evaluation.build_subrutter_charter(_context(), step) == {
        "items": ({"ready": True},),
    }


def test_subrutter_charter_rejects_malformed_json_as_typed_fault() -> None:
    step = SubRutter(
        _Child,
        charter=lambda context: {"bad": object()},
        next_on_outcome="done",
    )

    with pytest.raises(evaluation._RutterFault) as error:
        evaluation.build_subrutter_charter(_context(), step)

    assert error.value.category == "child-charter"


def test_transition_hooks_preserve_order_and_ignore_none_charters() -> None:
    events: list[str] = []

    class CountingMatch(TransitionMatch):
        def matches(self, transition):
            events.append("match")
            return super().matches(transition)

    def absent(context):
        events.append("absent")
        return None

    def selected(context):
        events.append("selected")
        return {"source": context.evolution.evolution_id}

    hooks = (
        TransitionHook(
            "absent",
            on=CountingMatch(source="review"),
            child=_Child,
            charter=absent,
        ),
        TransitionHook(
            "selected",
            on=CountingMatch(source="review"),
            child=_Child,
            charter=selected,
        ),
    )
    transition = Transition(
        "transition-review",
        "entry-review",
        "review",
        "approved",
        "done",
    )
    record = TerminalRecord(
        "record-review",
        "entry-review",
        "review",
        VoyageResult("complete", {}),
    )
    context = TransitionContext(_context(), transition.to_json(), record)

    selected_hooks = evaluation.select_transition_hooks(context, transition, hooks)

    assert events == ["match", "absent", "match", "selected"]
    assert tuple(hook.id for hook, charter in selected_hooks) == ("selected",)
    assert selected_hooks[0][1] == Charter({"source": "review"})


def test_transition_hook_matcher_rejects_non_boolean_as_typed_fault() -> None:
    class MalformedMatch(TransitionMatch):
        def matches(self, transition):
            return 1

    hook = TransitionHook(
        "malformed",
        on=MalformedMatch(source="review"),
        child=_Child,
        charter=lambda context: {},
    )
    transition = Transition(
        "transition-review",
        "entry-review",
        "review",
        "approved",
        "done",
    )
    record = TerminalRecord(
        "record-review",
        "entry-review",
        "review",
        VoyageResult("complete", {}),
    )
    context = TransitionContext(_context(), transition.to_json(), record)

    with pytest.raises(evaluation._RutterFault) as error:
        evaluation.select_transition_hooks(context, transition, (hook,))

    assert error.value.category == "case-matcher"
    assert error.value.transition_hook_ids == ("malformed",)


@pytest.mark.parametrize(
    ("operation", "category"),
    (
        (
            lambda: evaluation.run_machine(
                MachineContext(_context(), "machine-1"),
                MachineStep(
                    lambda context: (_ for _ in ()).throw(RuntimeError("boom")),
                    mode="pure",
                    next_on_outcome="done",
                ),
            ),
            "action-execution",
        ),
        (
            lambda: evaluation.build_terminal_result(
                _context(),
                Terminal(lambda context: (_ for _ in ()).throw(RuntimeError("boom"))),
            ),
            "done-projection",
        ),
    ),
)
def test_machine_and_terminal_failures_keep_stable_fault_categories(
    operation,
    category: str,
) -> None:
    with pytest.raises(evaluation._RutterFault) as error:
        operation()
    assert error.value.category == category


def test_machine_callback_returns_exact_domain_result() -> None:
    machine = MachineStep(
        lambda context: MachineResult("stored", {"machine": context.machine_id}),
        mode="pure",
        next_on_outcome="done",
    )

    assert evaluation.run_machine(
        MachineContext(_context(), "machine-1"), machine
    ) == MachineResult("stored", {"machine": "machine-1"})
