"""Specify the small immutable Rutter authoring model."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import inf, nan
from types import MappingProxyType
from typing import Mapping

import pytest

import officina.rutter.model as model
from officina.rutter.model import (
    Charter,
    EffectPolicy,
    Fix,
    Reckoning,
    RutterDefinitionError,
    RutterStateError,
    State,
    TerminalState,
    ValidationReport,
)


def _valid_state(*, effect_policy: EffectPolicy | None = None) -> State:
    """Return one literal nonterminal state definition."""

    return State(
        instruction=lambda: {"outcome": "done", "evidence": {}},
        input_validator=lambda value: ValidationReport(valid=bool(value)),
        next_state=lambda value: str(value["outcome"]),
        effect_policy=effect_policy,
    )


def test_authoring_dataclasses_expose_only_the_direct_contract_fields() -> None:
    """The model has no hidden route table or partially populated terminal state."""

    assert tuple(State.__dataclass_fields__) == (
        "instruction",
        "input_validator",
        "next_state",
        "description",
        "effect_policy",
    )
    assert tuple(TerminalState.__dataclass_fields__) == ("description",)
    assert tuple(Reckoning.__dataclass_fields__) == (
        "storage_version",
        "charter",
        "fix",
    )
    assert not hasattr(State, "destinations")
    assert not hasattr(State, "routes")


@pytest.mark.parametrize(
    "removed_name",
    ("Transition", "RutterState", "Await", "Perform", "Route"),
)
def test_removed_transition_vocabulary_is_not_part_of_the_model(
    removed_name: str,
) -> None:
    """Direct state functions replace the former transition object hierarchy."""

    assert not hasattr(model, removed_name)


def test_charter_copies_and_deeply_freezes_finite_json() -> None:
    """Mutable caller-owned containers cannot alter a bound undertaking."""

    source = {"paths": ["draft.md"], "options": {"strict": True}}
    charter = Charter("example", 1, source)
    source["paths"].append("late.md")
    source["options"]["strict"] = False

    assert charter.data == {
        "paths": ("draft.md",),
        "options": {"strict": True},
    }
    assert isinstance(charter.data, MappingProxyType)
    assert isinstance(charter.data["options"], MappingProxyType)
    with pytest.raises(TypeError):
        charter.data["new"] = True  # type: ignore[index]
    with pytest.raises(AttributeError):
        charter.data["paths"].append("other.md")  # type: ignore[union-attr]


@pytest.mark.parametrize("invalid", (nan, inf, -inf, object(), lambda: None))
def test_charter_rejects_non_finite_or_non_json_values(invalid: object) -> None:
    """A Charter cannot capture values the strict Reckoning codec cannot encode."""

    with pytest.raises(RutterDefinitionError):
        Charter("example", 1, {"invalid": invalid})  # type: ignore[dict-item]


def test_charter_and_fix_have_no_generic_mutable_workflow_state() -> None:
    """Domain results belong in explicit artifacts, never generic persisted bags."""

    forbidden = {"memory", "context", "state_data"}
    assert forbidden.isdisjoint(Charter.__dataclass_fields__)
    assert forbidden.isdisjoint(Fix.__dataclass_fields__)


def test_model_values_are_frozen() -> None:
    """Authoring and persisted authority cannot be reassigned after binding."""

    charter = Charter("example", 1, {})
    fix = Fix(current_state_id="review", revision=0, lifecycle="active")
    reckoning = Reckoning(1, charter, fix)

    with pytest.raises(FrozenInstanceError):
        charter.definition_version = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        fix.revision = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        reckoning.storage_version = 2  # type: ignore[misc]


def test_effect_policy_is_callable_only() -> None:
    """String instructions cannot claim framework effect-recovery authority."""

    with pytest.raises(RutterDefinitionError, match="callable"):
        State(
            instruction="Ask for evidence.",
            input_validator=lambda value: ValidationReport(valid=bool(value)),
            next_state=lambda value: str(value["outcome"]),
            effect_policy=EffectPolicy(repeat_safe=True),
        )

    assert _valid_state(effect_policy=EffectPolicy(repeat_safe=False)).effect_policy == (
        EffectPolicy(repeat_safe=False)
    )


def test_effect_recovery_record_is_not_public_authoring_vocabulary() -> None:
    """Framework recovery authority stays private to storage and the engine."""

    assert not hasattr(model, "EffectRecovery")
    assert hasattr(model, "_EffectRecovery")


def test_invalid_effect_recovery_state_id_is_a_persisted_state_error() -> None:
    """Malformed persisted recovery coordinates never become definition errors."""

    with pytest.raises(RutterStateError, match="effect state ID"):
        model._EffectRecovery(
            state_id="../escape",
            revision=0,
            disposition="planned",
            repeat_safe=True,
        )


@pytest.mark.parametrize("instruction", ("", "   ", 3, None))
def test_state_rejects_missing_or_invalid_instructions(instruction: object) -> None:
    """Every nonterminal state owns one usable string or callable instruction."""

    with pytest.raises(RutterDefinitionError):
        State(
            instruction=instruction,  # type: ignore[arg-type]
            input_validator=lambda value: ValidationReport(valid=bool(value)),
            next_state=lambda value: str(value["outcome"]),
        )


def test_reckoning_contains_no_callable_values() -> None:
    """Persisted authority is data-only even though the frozen graph has callables."""

    reckoning = Reckoning(
        storage_version=1,
        charter=Charter("example", 1, {"nested": [1, {"ok": True}]}),
        fix=Fix(current_state_id="review", revision=0, lifecycle="active"),
    )

    def walk(value: object) -> None:
        assert not callable(value)
        if isinstance(value, Mapping):
            for item in value.values():
                walk(item)
        elif isinstance(value, tuple):
            for item in value:
                walk(item)
        elif hasattr(value, "__dataclass_fields__"):
            for name in value.__dataclass_fields__:  # type: ignore[union-attr]
                walk(getattr(value, name))

    walk(reckoning)
