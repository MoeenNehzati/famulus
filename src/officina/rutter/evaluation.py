"""Controlled boundary for all author-supplied Rutter callbacks."""

from __future__ import annotations

from collections.abc import Callable

from officina.rutter.authoring import (
    EvolutionContext,
    LLMResponseContext,
    LLMStep,
    MachineContext,
    MachineStep,
    SubRutter,
    Terminal,
    TransitionContext,
    TransitionHook,
)
from officina.rutter.history import Transition
from officina.rutter.values import (
    Charter,
    JsonObject,
    MachineResult,
    ValidationReport,
    VoyageResult,
    _freeze_object,
)


class _RutterFault(Exception):
    """Private carrier for evaluation and transition-processing faults."""

    def __init__(
        self,
        category: str,
        *,
        target_evolution_id: str | None = None,
        transition_hook_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(category)
        self.category = category
        self.target_evolution_id = target_evolution_id
        self.transition_hook_ids = transition_hook_ids


def evaluate_llm_route(
    context: LLMResponseContext,
    choose_next: Callable[[LLMResponseContext], str],
) -> str:
    try:
        target = choose_next(context)
    except Exception as exc:
        raise _RutterFault("routing") from exc
    if type(target) is not str:
        raise _RutterFault("routing")
    return target


def evaluate_machine_route(
    context: MachineContext,
    result: MachineResult,
    choose_next: Callable[[MachineContext, MachineResult], str],
) -> str:
    try:
        target = choose_next(context, result)
    except Exception as exc:
        raise _RutterFault("routing") from exc
    if type(target) is not str:
        raise _RutterFault("routing")
    return target


def evaluate_subrutter_route(
    context: EvolutionContext,
    result: VoyageResult,
    choose_next: Callable[[EvolutionContext, VoyageResult], str],
) -> str:
    try:
        target = choose_next(context, result)
    except Exception as exc:
        raise _RutterFault("routing") from exc
    if type(target) is not str:
        raise _RutterFault("routing")
    return target


def build_llm_data(
    context: EvolutionContext,
    step: LLMStep,
) -> JsonObject:
    try:
        authored = step.data(context)
        return _freeze_object(authored, "LLMStep data")
    except Exception as exc:
        raise _RutterFault("materialization") from exc


def assess_llm_response(
    context: LLMResponseContext,
    step: LLMStep,
) -> ValidationReport:
    try:
        report = step.assess_response(context)
    except Exception as exc:
        raise _RutterFault("contextual-validation") from exc
    if not isinstance(report, ValidationReport):
        raise _RutterFault("contextual-validation")
    return report


def build_subrutter_charter(
    context: EvolutionContext,
    step: SubRutter,
) -> JsonObject:
    try:
        authored = step.charter_constructor(context)
        return _freeze_object(authored, "SubRutter charter")
    except Exception as exc:
        raise _RutterFault("child-charter") from exc


def select_transition_hooks(
    context: TransitionContext,
    transition: Transition,
    definitions: tuple[TransitionHook, ...],
) -> tuple[tuple[TransitionHook, Charter], ...]:
    selected: list[tuple[TransitionHook, Charter]] = []
    for hook in definitions:
        try:
            matches = hook.on.matches(transition)
        except Exception as exc:
            raise _RutterFault(
                "case-matcher",
                transition_hook_ids=(hook.id,),
            ) from exc
        if type(matches) is not bool:
            raise _RutterFault(
                "case-matcher",
                transition_hook_ids=(hook.id,),
            )
        if not matches:
            continue
        try:
            authored = hook.charter_constructor(context)
            if authored is not None:
                selected.append((hook, Charter(authored)))
        except Exception as exc:
            raise _RutterFault(
                "case-charter",
                transition_hook_ids=(hook.id,),
            ) from exc
    return tuple(selected)


def build_terminal_result(
    context: EvolutionContext,
    terminal: Terminal,
) -> VoyageResult:
    if terminal.result is not None:
        return terminal.result
    assert terminal.result_constructor is not None
    try:
        result = terminal.result_constructor(context)
    except Exception as exc:
        raise _RutterFault("done-projection") from exc
    if not isinstance(result, VoyageResult):
        raise _RutterFault("done-projection")
    return result


def run_machine(
    context: MachineContext,
    machine: MachineStep,
) -> MachineResult:
    try:
        result = machine.run(context)
    except Exception as exc:
        raise _RutterFault("action-execution") from exc
    if not isinstance(result, MachineResult):
        raise _RutterFault("action-result")
    return result


__all__ = (
    "build_llm_data",
    "build_subrutter_charter",
    "build_terminal_result",
    "evaluate_llm_route",
    "evaluate_machine_route",
    "evaluate_subrutter_route",
    "run_machine",
    "select_transition_hooks",
    "assess_llm_response",
)
