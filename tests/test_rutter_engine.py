"""Specify durable construction and the bound Rutter reduction engine."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from pathlib import Path
from typing import Mapping

import pytest

import officina.rutter.model as model_module
from officina.rutter.model import (
    BaseRutter,
    Charter,
    Fix,
    JsonValue,
    Reckoning,
    RutterDefinitionError,
    RutterStateError,
    RutterValidationError,
    State,
    TerminalState,
    ValidationIssue,
    ValidationReport,
    _EffectRecovery,
)
import officina.rutter.storage as storage_module
from test_support.rutter_fixtures import (
    CallableProbeRutter,
    ExampleRutter,
    LoopRutter,
    NonRepeatSafeEffectRutter,
    PureChainRutter,
    RepeatSafeEffectRutter,
    example_charter,
)


def test_base_rutter_is_implemented_by_engine_and_reexported_from_model() -> None:
    """Moving engine ownership must not break the Task 1 author import path."""

    assert importlib.util.find_spec("officina.rutter.engine") is not None
    engine = importlib.import_module("officina.rutter.engine")

    assert BaseRutter is engine.BaseRutter


def test_public_surface_has_only_two_construction_and_three_operation_routes() -> None:
    """Compatibility run/move/wait APIs cannot survive the simplified contract."""

    assert tuple(inspect.signature(BaseRutter.create).parameters) == (
        "reckoning_path",
        "charter",
    )
    assert tuple(inspect.signature(BaseRutter.open).parameters) == ("reckoning_path",)
    assert tuple(inspect.signature(BaseRutter.get_instruction).parameters) == ("self",)
    assert tuple(inspect.signature(BaseRutter.validate).parameters) == ("self", "input")
    assert tuple(inspect.signature(BaseRutter.advance).parameters) == (
        "self",
        "input",
        "continue_",
        "dry_run",
    )
    for removed in (
        "describe",
        "give_instructions",
        "validate_result",
        "update",
        "run",
        "move",
        "wait",
    ):
        assert not hasattr(BaseRutter, removed)


def test_create_persists_initial_fix_and_open_only_rebinds(
    tmp_path: Path,
) -> None:
    """A new process can open exact authority without reconstructing or advancing it."""

    ExampleRutter.reset_calls()
    path = tmp_path / "durable.reckoning.json"

    created = ExampleRutter.create(path, example_charter())
    created_bytes = path.read_bytes()
    opened = ExampleRutter.open(path)

    assert created.fix == Fix("review", 0, "active")
    assert opened.reckoning == created.reckoning
    assert opened.fix.revision == 0
    assert path.read_bytes() == created_bytes
    assert ExampleRutter.define_calls == 2
    assert ExampleRutter.instruction_calls == 0
    assert ExampleRutter.validator_calls == 0
    assert ExampleRutter.next_state_calls == 0


def test_bound_operations_reject_repeated_path_or_charter_arguments(
    tmp_path: Path,
) -> None:
    """Construction data is bound once and cannot be supplied again to operations."""

    rutter = ExampleRutter.create(tmp_path / "bound.reckoning.json", example_charter())
    envelope = {"revision": 0, "outcome": "accepted", "evidence": {}}

    with pytest.raises(TypeError):
        rutter.get_instruction(example_charter())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        rutter.validate(tmp_path / "other.reckoning.json", envelope)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        rutter.advance(envelope, example_charter())  # type: ignore[call-arg]


def test_string_instruction_renders_bound_context_and_exact_contract(
    tmp_path: Path,
) -> None:
    """Compass can act from the instruction without inspecting Rutter source."""

    rutter = ExampleRutter.create(tmp_path / "instruction.reckoning.json", example_charter())

    instruction = rutter.get_instruction()

    assert isinstance(instruction, str)
    assert "Review the current material." in instruction
    assert '"artifact":"draft.md"' in instruction
    assert "Current state: review" in instruction
    assert "Current revision: 0" in instruction
    assert '{"revision":0,"outcome":"<allowed outcome>","evidence":{}}' in instruction
    assert "accepted, revise, unexpected" in instruction
    assert "revision mismatch" in instruction
    assert "external work is not rolled back" in instruction


def test_string_state_requires_explicit_validator_outcome_contract(
    tmp_path: Path,
) -> None:
    """A plain callable cannot render complete source-free outcome diagnostics."""

    class MissingOutcomeContractRutter(BaseRutter):
        rutter_id = "missing-outcome-contract"
        definition_version = 1
        start_state = "start"

        @staticmethod
        def validate_input(value: Mapping[str, JsonValue]) -> ValidationReport:
            del value
            return ValidationReport(valid=True)

        @staticmethod
        def finish(value: Mapping[str, JsonValue]) -> str:
            del value
            return "complete"

        def define_states(self) -> Mapping[str, State | TerminalState]:
            return {
                "start": State("Act.", self.validate_input, self.finish),
                "complete": TerminalState(),
            }

    with pytest.raises(RutterDefinitionError, match="outcome contract"):
        MissingOutcomeContractRutter.create(
            tmp_path / "missing-outcomes.reckoning.json",
            Charter("missing-outcome-contract", 1, {}),
        )


@pytest.mark.parametrize(
    "outcomes",
    ((), ("",), ("accepted", "accepted"), ("unexpected",), (3,)),
)
def test_validator_outcome_contract_rejects_malformed_outcomes_at_binding(
    tmp_path: Path,
    outcomes: tuple[object, ...],
) -> None:
    """Allowed outcomes are one nonempty unique finite tuple, excluding reserved mismatch."""

    contract_type = getattr(model_module, "InputValidatorContract", None)
    assert contract_type is not None

    def validate_input(value: Mapping[str, JsonValue]) -> ValidationReport:
        del value
        return ValidationReport(valid=True)

    class MalformedOutcomeContractRutter(BaseRutter):
        rutter_id = "malformed-outcome-contract"
        definition_version = 1
        start_state = "start"

        def define_states(self) -> Mapping[str, State | TerminalState]:
            return {
                "start": State(
                    "Act.",
                    contract_type(validate_input, outcomes),
                    lambda value: "complete",
                ),
                "complete": TerminalState(),
            }

    with pytest.raises(RutterDefinitionError, match="allowed outcomes"):
        MalformedOutcomeContractRutter.create(
            tmp_path / "malformed-outcomes.reckoning.json",
            Charter("malformed-outcome-contract", 1, {}),
        )


def test_bound_outcomes_are_detached_from_authored_contract_mutation(
    tmp_path: Path,
) -> None:
    """Late mutation cannot change a voyage's already-frozen diagnostics."""

    contract_type = getattr(model_module, "InputValidatorContract", None)
    assert contract_type is not None

    def validate_input(value: Mapping[str, JsonValue]) -> ValidationReport:
        del value
        return ValidationReport(valid=True)

    contract = contract_type(validate_input, ("accepted", "stopped"))

    class DetachedOutcomeRutter(BaseRutter):
        rutter_id = "detached-outcome"
        definition_version = 1
        start_state = "start"

        def define_states(self) -> Mapping[str, State | TerminalState]:
            return {
                "start": State(
                    "Act.",
                    contract,
                    lambda value: "complete",
                ),
                "complete": TerminalState(),
            }

    rutter = DetachedOutcomeRutter.create(
        tmp_path / "detached-outcomes.reckoning.json",
        Charter("detached-outcome", 1, {}),
    )
    object.__setattr__(contract, "allowed_outcomes", ("tampered",))

    instruction = rutter.get_instruction()

    assert isinstance(instruction, str)
    assert "accepted, stopped, unexpected" in instruction
    assert "tampered" not in instruction


def test_outcome_contract_rejects_undeclared_outcome_before_authored_hooks(
    tmp_path: Path,
) -> None:
    """A permissive validator cannot authorize an undeclared graph outcome."""

    class PermissiveContractRutter(BaseRutter):
        rutter_id = "permissive-contract"
        definition_version = 1
        start_state = "start"
        validator_calls = 0
        transition_calls = 0

        @staticmethod
        def validate_any(value: Mapping[str, JsonValue]) -> ValidationReport:
            del value
            PermissiveContractRutter.validator_calls += 1
            return ValidationReport(valid=True)

        @staticmethod
        def finish(value: Mapping[str, JsonValue]) -> str:
            del value
            PermissiveContractRutter.transition_calls += 1
            return "complete"

        def define_states(self) -> Mapping[str, State | TerminalState]:
            return {
                "start": State(
                    "Act.",
                    model_module.InputValidatorContract(
                        self.validate_any,
                        ("declared",),
                    ),
                    self.finish,
                ),
                "complete": TerminalState(),
            }

    path = tmp_path / "undeclared-outcome.reckoning.json"
    rutter = PermissiveContractRutter.create(
        path,
        Charter("permissive-contract", 1, {}),
    )
    before = path.read_bytes()
    undeclared = {"revision": 0, "outcome": "forged", "evidence": {}}

    report = rutter.validate(undeclared)
    with pytest.raises(RutterValidationError) as dry_run:
        rutter.advance(undeclared, continue_=False, dry_run=True)
    with pytest.raises(RutterValidationError) as advance:
        rutter.advance(undeclared, continue_=False)

    assert report.valid is False
    assert tuple(issue.code for issue in report.issues) == ("undeclared_outcome",)
    assert dry_run.value.report == report
    assert advance.value.report == report
    assert PermissiveContractRutter.validator_calls == 0
    assert PermissiveContractRutter.transition_calls == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("rutter_kind", ("string", "callable"))
def test_string_and_callable_states_share_strict_finite_envelope_validation(
    tmp_path: Path,
    rutter_kind: str,
) -> None:
    """Both instruction kinds reject malformed framework authority identically."""

    if rutter_kind == "string":
        rutter = ExampleRutter.create(tmp_path / "string.reckoning.json", example_charter())
    else:
        rutter = CallableProbeRutter.create(
            tmp_path / "callable.reckoning.json",
            Charter("callable-probe", 1, {}),
        )
    malformed = {
        "revision": True,
        "outcome": "done",
        "evidence": {"score": float("nan")},
        "extra": "forbidden",
    }

    report = rutter.validate(malformed)  # type: ignore[arg-type]

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"invalid_envelope"}


def test_validate_is_pure_and_does_not_select_a_successor(tmp_path: Path) -> None:
    """Dropping the pure-validation boundary would change bytes or call routing."""

    ExampleRutter.reset_calls()
    path = tmp_path / "validate.reckoning.json"
    rutter = ExampleRutter.create(path, example_charter())
    before = path.read_bytes()

    report = rutter.validate(
        {"revision": 0, "outcome": "accepted", "evidence": {}}
    )

    assert report == ValidationReport(valid=True)
    assert path.read_bytes() == before
    assert ExampleRutter.validator_calls == 1
    assert ExampleRutter.next_state_calls == 0


def test_each_operation_reloads_authoritative_fix_before_using_it(
    tmp_path: Path,
) -> None:
    """A stale bound cache cannot overrule a peer's durable successor."""

    PureChainRutter.reset_calls()
    path = tmp_path / "reload.reckoning.json"
    stale = PureChainRutter.create(
        path,
        Charter("pure-chain", 1, {"artifact": "draft.md"}),
    )
    peer = PureChainRutter.open(path)
    peer.advance(
        {"revision": 0, "outcome": "collected", "evidence": {}},
        continue_=False,
    )

    status = stale.get_instruction()
    report = stale.validate(
        {"revision": 0, "outcome": "normalized", "evidence": {}}
    )

    assert status == {
        "status": "callable",
        "state": "normalize",
        "revision": 1,
        "message": "Callable instruction is ready; call advance(continue_=True).",
    }
    assert stale.fix == Fix("normalize", 1, "active")
    assert report.valid is False
    assert tuple(issue.code for issue in report.issues) == ("stale_revision",)
    with pytest.raises(RutterValidationError) as caught:
        stale.advance(
            {"revision": 0, "outcome": "normalized", "evidence": {}},
            continue_=False,
        )
    assert tuple(issue.code for issue in caught.value.report.issues) == (
        "stale_revision",
    )


def test_validator_rejection_and_exception_preserve_predecessor_bytes(
    tmp_path: Path,
) -> None:
    """Invalid domain evidence and broken validators cannot move authority."""

    path = tmp_path / "rejected.reckoning.json"
    rutter = ExampleRutter.create(path, example_charter())
    before = path.read_bytes()

    with pytest.raises(RutterValidationError) as rejected:
        rutter.advance(
            {"revision": 0, "outcome": "unknown", "evidence": {}},
            continue_=False,
        )

    assert tuple(issue.code for issue in rejected.value.report.issues) == (
        "undeclared_outcome",
    )
    assert path.read_bytes() == before

    class RaisingValidatorRutter(BaseRutter):
        rutter_id = "raising-validator"
        definition_version = 1
        start_state = "start"

        @staticmethod
        def reject(value: Mapping[str, JsonValue]) -> ValidationReport:
            del value
            raise RuntimeError("validator exploded")

        @staticmethod
        def finish(value: Mapping[str, JsonValue]) -> str:
            del value
            return "complete"

        def define_states(self) -> Mapping[str, State | TerminalState]:
            return {
                "start": State(
                    "Act.",
                    model_module.InputValidatorContract(
                        self.reject,
                        ("done",),
                    ),
                    self.finish,
                ),
                "complete": TerminalState(),
            }

    broken_path = tmp_path / "validator-error.reckoning.json"
    broken = RaisingValidatorRutter.create(
        broken_path,
        Charter("raising-validator", 1, {}),
    )
    broken_before = broken_path.read_bytes()

    report = broken.validate(
        {"revision": 0, "outcome": "done", "evidence": {}}
    )
    with pytest.raises(RutterValidationError) as validator_error:
        broken.advance(
            {"revision": 0, "outcome": "done", "evidence": {}},
            continue_=False,
        )

    assert tuple(issue.code for issue in report.issues) == ("validator_error",)
    assert tuple(issue.code for issue in validator_error.value.report.issues) == (
        "validator_error",
    )
    assert broken_path.read_bytes() == broken_before


@pytest.mark.parametrize("failure", ("missing", "exception", "mutation"))
def test_transition_failure_or_input_mutation_preserves_exact_bytes(
    tmp_path: Path,
    failure: str,
) -> None:
    """A next-state function can neither mutate validated input nor publish failure."""

    def validate(value: Mapping[str, JsonValue]) -> ValidationReport:
        del value
        return ValidationReport(valid=True)

    def next_state(value: Mapping[str, JsonValue]) -> str:
        if failure == "missing":
            return "absent"
        if failure == "exception":
            raise RuntimeError("transition exploded")
        value["outcome"] = "changed"  # type: ignore[index]
        return "complete"

    class BrokenTransitionRutter(BaseRutter):
        rutter_id = "broken-transition"
        definition_version = 1
        start_state = "start"

        def define_states(self) -> Mapping[str, State | TerminalState]:
            return {
                "start": State(
                    "Act.",
                    model_module.InputValidatorContract(
                        validate,
                        ("done",),
                    ),
                    next_state,
                ),
                "complete": TerminalState(),
            }

    path = tmp_path / f"{failure}.reckoning.json"
    rutter = BrokenTransitionRutter.create(
        path,
        Charter("broken-transition", 1, {}),
    )
    before = path.read_bytes()

    with pytest.raises(RutterStateError, match="successor|next_state"):
        rutter.advance(
            {"revision": 0, "outcome": "done", "evidence": {}},
            continue_=False,
        )

    assert path.read_bytes() == before


def test_continue_false_crosses_one_edge_without_invoking_callable_successor(
    tmp_path: Path,
) -> None:
    """The diagnostic one-edge mode must stop before deterministic work."""

    PureChainRutter.reset_calls()
    path = tmp_path / "one-edge.reckoning.json"
    rutter = PureChainRutter.create(path, Charter("pure-chain", 1, {}))

    successor = rutter.advance(
        {"revision": 0, "outcome": "collected", "evidence": {}},
        continue_=False,
    )
    reopened = PureChainRutter.open(path)

    assert successor == "normalize"
    assert reopened.fix == Fix("normalize", 1, "active")
    assert PureChainRutter.instruction_calls == 0


def test_dry_run_requires_one_produced_input_and_never_writes_or_invokes(
    tmp_path: Path,
) -> None:
    """Dry run is exactly one observational transition from supplied evidence."""

    ExampleRutter.reset_calls()
    path = tmp_path / "dry-run.reckoning.json"
    rutter = ExampleRutter.create(path, example_charter())
    before = path.read_bytes()

    with pytest.raises(RutterStateError, match="continue"):
        rutter.advance(
            {"revision": 0, "outcome": "accepted", "evidence": {}},
            dry_run=True,
        )
    with pytest.raises(RutterValidationError) as missing:
        rutter.advance(continue_=False, dry_run=True)
    successor = rutter.advance(
        {"revision": 0, "outcome": "accepted", "evidence": {}},
        continue_=False,
        dry_run=True,
    )

    assert tuple(issue.code for issue in missing.value.report.issues) == (
        "input_required",
    )
    assert successor == "complete"
    assert path.read_bytes() == before
    assert ExampleRutter.instruction_calls == 0
    assert ExampleRutter.next_state_calls == 1


def test_dry_run_rejects_unexpected_evidence_without_selecting_successor(
    tmp_path: Path,
) -> None:
    """Reserved mismatch evidence has no transition that dry run can preview."""

    ExampleRutter.reset_calls()
    path = tmp_path / "dry-unexpected.reckoning.json"
    rutter = ExampleRutter.create(path, example_charter())
    before = path.read_bytes()
    unexpected = {
        "revision": 0,
        "outcome": "unexpected",
        "evidence": {
            "observed": "two roots were found",
            "conflict": "one root is required",
            "why_no_outcome_fits": "neither root can be selected",
            "uncertainty": "the intended root is unknown",
        },
    }

    with pytest.raises(RutterValidationError) as caught:
        rutter.advance(unexpected, continue_=False, dry_run=True)

    assert tuple(issue.code for issue in caught.value.report.issues) == (
        "unexpected_has_no_successor",
    )
    assert "cannot be previewed" in caught.value.report.issues[0].message
    assert path.read_bytes() == before
    assert ExampleRutter.next_state_calls == 0
    assert rutter.fix == Fix("review", 0, "active")


def test_omitted_input_at_string_state_is_invalid(tmp_path: Path) -> None:
    """Continuation cannot execute an asynchronous string instruction."""

    rutter = ExampleRutter.create(tmp_path / "missing-input.reckoning.json", example_charter())

    with pytest.raises(RutterValidationError) as caught:
        rutter.advance()

    assert tuple(issue.code for issue in caught.value.report.issues) == (
        "input_required",
    )


def test_continued_pure_chain_increments_each_edge_with_one_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Intermediate pure Fixes must never be separately published."""

    PureChainRutter.reset_calls()
    path = tmp_path / "continued.reckoning.json"
    rutter = PureChainRutter.create(path, Charter("pure-chain", 1, {}))
    real_replace = storage_module.atomic_files.atomic_replace_bytes
    replacements = []

    def count_replace(*args: object, **kwargs: object) -> None:
        replacements.append(1)
        real_replace(*args, **kwargs)

    monkeypatch.setattr(
        storage_module.atomic_files,
        "atomic_replace_bytes",
        count_replace,
    )

    successor = rutter.advance(
        {"revision": 0, "outcome": "collected", "evidence": {}},
        continue_=True,
    )
    reopened = PureChainRutter.open(path)

    assert successor == "complete"
    assert reopened.fix == Fix("complete", 3, "complete")
    assert PureChainRutter.instruction_calls == 2
    assert PureChainRutter.observed_revisions == [1, 2]
    assert replacements == [1]


def test_callable_state_continues_without_external_input(tmp_path: Path) -> None:
    """No-input continuation executes the currently authorized pure callable."""

    CallableProbeRutter.instruction_calls = 0
    path = tmp_path / "callable-start.reckoning.json"
    rutter = CallableProbeRutter.create(
        path,
        Charter("callable-probe", 1, {}),
    )

    successor = rutter.advance()

    assert successor == "complete"
    assert rutter.fix == Fix("complete", 1, "complete")
    assert CallableProbeRutter.instruction_calls == 1


def test_terminal_and_fault_coordinates_return_source_free_status(
    tmp_path: Path,
) -> None:
    """Compass can diagnose stopped authority without calling authored work."""

    terminal_path = tmp_path / "terminal.reckoning.json"
    terminal = ExampleRutter.create(terminal_path, example_charter())
    terminal.advance(
        {"revision": 0, "outcome": "accepted", "evidence": {}},
        continue_=False,
    )

    assert terminal.get_instruction() == {
        "status": "terminal",
        "state": "complete",
        "revision": 1,
        "lifecycle": "complete",
        "description": "Review completed.",
    }
    with pytest.raises(RutterStateError, match="terminal"):
        terminal.advance(continue_=False)

    fault_path = tmp_path / "fault.reckoning.json"
    fault = ExampleRutter.create(fault_path, example_charter())
    issue = ValidationIssue("state", "injected_fault", "diagnostic detail")
    faulted = Reckoning(
        1,
        example_charter(),
        Fix("review", 0, "faulted", diagnostics=(issue,)),
    )
    with fault._store.transaction() as previous:
        fault._store.replace(previous, faulted)

    assert fault.get_instruction() == {
        "status": "fault",
        "state": "review",
        "revision": 0,
        "lifecycle": "faulted",
        "diagnostics": (
            {"path": "state", "code": "injected_fault", "message": "diagnostic detail"},
        ),
    }
    with pytest.raises(RutterStateError, match="faulted"):
        fault.advance(continue_=False)


def test_reserved_unexpected_requires_complete_evidence_and_faults_durably(
    tmp_path: Path,
) -> None:
    """An observation outside outcomes is recorded rather than guessed."""

    ExampleRutter.reset_calls()
    path = tmp_path / "unexpected.reckoning.json"
    rutter = ExampleRutter.create(path, example_charter())
    incomplete = {
        "revision": 0,
        "outcome": "unexpected",
        "evidence": {"observed": "ambiguous"},
    }
    evidence = {
        "observed": "two roots were found",
        "conflict": "one root is required",
        "why_no_outcome_fits": "neither can be selected",
        "uncertainty": "the intended root is unknown",
    }

    invalid = rutter.validate(incomplete)
    stopped_at = rutter.advance(
        {"revision": 0, "outcome": "unexpected", "evidence": evidence},
        continue_=False,
    )
    reopened = ExampleRutter.open(path)

    assert tuple(issue.code for issue in invalid.issues) == (
        "invalid_unexpected_evidence",
    )
    assert stopped_at == "review"
    assert reopened.fix.lifecycle == "faulted"
    assert reopened.fix.revision == 0
    assert reopened.fix.diagnostics[0].code == "unexpected"
    assert all(value in reopened.fix.diagnostics[0].message for value in evidence.values())
    assert ExampleRutter.next_state_calls == 0


def test_settling_limit_fails_without_publishing_partial_loop(tmp_path: Path) -> None:
    """An unbounded pure loop cannot expose its staged intermediate revisions."""

    LoopRutter.instruction_calls = 0
    path = tmp_path / "loop.reckoning.json"
    rutter = LoopRutter.create(path, Charter("loop", 1, {}))
    before = path.read_bytes()

    with pytest.raises(RutterStateError, match="settling limit of 100"):
        rutter.advance()

    assert LoopRutter.instruction_calls == 100
    assert path.read_bytes() == before


def _effect_charter(
    rutter_id: str,
    reckoning_path: Path,
    marker_path: Path,
) -> Charter:
    """Return exact immutable paths consumed by fixture effect callables."""

    return Charter(
        rutter_id,
        1,
        {
            "reckoning_path": str(reckoning_path),
            "marker_path": str(marker_path),
        },
    )


def test_effectful_artifact_write_is_planned_then_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a Rutter-owned artifact write crosses both durable effect phases."""

    RepeatSafeEffectRutter.reset_calls()
    path = tmp_path / "effect.reckoning.json"
    marker = tmp_path / "artifact.txt"
    rutter = RepeatSafeEffectRutter.create(
        path,
        _effect_charter("repeat-safe-effect", path, marker),
    )
    real_replace = storage_module.atomic_files.atomic_replace_bytes
    replacements = []

    def count_replace(*args: object, **kwargs: object) -> None:
        replacements.append(1)
        real_replace(*args, **kwargs)

    monkeypatch.setattr(
        storage_module.atomic_files,
        "atomic_replace_bytes",
        count_replace,
    )

    ready = rutter.get_instruction()
    successor = rutter.advance()
    reopened = RepeatSafeEffectRutter.open(path)

    assert ready == {
        "status": "effectful_callable",
        "state": "publish",
        "revision": 0,
        "repeat_safe": True,
        "message": "Effect authority will be planned before advance invokes work.",
    }
    assert successor == "complete"
    assert marker.read_text(encoding="utf-8") == "1"
    assert reopened.fix.current_state_id == "complete"
    assert reopened.fix.revision == 1
    assert reopened.fix.lifecycle == "complete"
    assert reopened.fix.effect is not None
    assert reopened.fix.effect.state_id == "publish"
    assert reopened.fix.effect.revision == 0
    assert reopened.fix.effect.disposition == "completed"
    assert reopened.fix.effect.repeat_safe is True
    assert replacements == [1, 1]


def test_repeat_safe_planned_effect_reopens_and_retries(tmp_path: Path) -> None:
    """Interrupted repeat-safe authority remains planned until confirmed completion."""

    RepeatSafeEffectRutter.reset_calls()
    RepeatSafeEffectRutter.interrupt_once = True
    path = tmp_path / "repeat.reckoning.json"
    marker = tmp_path / "repeat-attempts.txt"
    rutter = RepeatSafeEffectRutter.create(
        path,
        _effect_charter("repeat-safe-effect", path, marker),
    )

    with pytest.raises(InterruptedError, match="artifact write"):
        rutter.advance()
    reopened = RepeatSafeEffectRutter.open(path)
    pending = reopened.get_instruction()
    successor = reopened.advance()

    assert pending == {
        "status": "pending_effect",
        "state": "publish",
        "revision": 0,
        "repeat_safe": True,
        "next_action": "retry_effect",
        "authorized_operation": "advance(continue_=True)",
        "will_invoke_effect": True,
        "message": "The planned effect may be retried by advance(continue_=True).",
    }
    assert successor == "complete"
    assert marker.read_text(encoding="utf-8") == "2"
    assert RepeatSafeEffectRutter.instruction_calls == 2
    assert reopened.fix.effect is not None
    assert reopened.fix.effect.disposition == "completed"


def test_non_repeat_safe_planned_effect_becomes_uncertain_without_retry(
    tmp_path: Path,
) -> None:
    """Reopening an unconfirmed non-repeat-safe effect never invokes it twice."""

    NonRepeatSafeEffectRutter.instruction_calls = 0
    NonRepeatSafeEffectRutter.interrupt_once = True
    path = tmp_path / "non-repeat.reckoning.json"
    marker = tmp_path / "non-repeat-attempts.txt"
    rutter = NonRepeatSafeEffectRutter.create(
        path,
        _effect_charter("non-repeat-safe-effect", path, marker),
    )

    with pytest.raises(InterruptedError, match="uncertain"):
        rutter.advance()
    reopened = NonRepeatSafeEffectRutter.open(path)
    pending = reopened.get_instruction()
    stopped_at = reopened.advance()
    uncertain = reopened.get_instruction()
    report = reopened.validate(
        {"revision": 0, "outcome": "published", "evidence": {}}
    )

    assert stopped_at == "publish"
    assert pending == {
        "status": "pending_effect",
        "state": "publish",
        "revision": 0,
        "repeat_safe": False,
        "next_action": "record_uncertainty",
        "authorized_operation": "advance(continue_=True)",
        "will_invoke_effect": False,
        "message": (
            "Call advance(continue_=True) to record uncertainty without "
            "invoking the effect."
        ),
    }
    assert uncertain == {
        "status": "uncertain_effect",
        "state": "publish",
        "revision": 0,
        "repeat_safe": False,
        "next_action": "manual_reconciliation",
        "authorized_operation": None,
        "will_invoke_effect": False,
        "message": (
            "The effect may have happened; manual reconciliation is required. "
            "No public recovery transition is authorized."
        ),
    }
    assert tuple(issue.code for issue in report.issues) == ("uncertain_effect",)
    assert marker.read_text(encoding="utf-8") == "1"
    assert NonRepeatSafeEffectRutter.instruction_calls == 1
    with pytest.raises(RutterStateError, match="uncertain"):
        reopened.advance()


@pytest.mark.parametrize(
    ("failure", "expected_exception", "diagnostic_code"),
    (
        ("validation", RutterValidationError, "post_effect_validation"),
        ("transition", RutterStateError, "post_effect_transition"),
    ),
)
def test_post_effect_failure_persists_completed_disposition_and_fault(
    tmp_path: Path,
    failure: str,
    expected_exception: type[Exception],
    diagnostic_code: str,
) -> None:
    """Non-rollbackable success cannot be represented as an untouched predecessor."""

    RepeatSafeEffectRutter.reset_calls()
    if failure == "validation":
        RepeatSafeEffectRutter.result_outcome = "malformed"
    else:
        RepeatSafeEffectRutter.transition_raises = True
    path = tmp_path / f"post-effect-{failure}.reckoning.json"
    marker = tmp_path / f"post-effect-{failure}.txt"
    rutter = RepeatSafeEffectRutter.create(
        path,
        _effect_charter("repeat-safe-effect", path, marker),
    )

    with pytest.raises(expected_exception):
        rutter.advance()
    reopened = RepeatSafeEffectRutter.open(path)

    assert marker.read_text(encoding="utf-8") == "1"
    assert reopened.fix.lifecycle == "faulted"
    assert reopened.fix.revision == 0
    assert reopened.fix.effect is not None
    assert reopened.fix.effect.disposition == "completed"
    assert reopened.fix.diagnostics[0].code == diagnostic_code


@pytest.mark.parametrize(
    ("current_state_id", "revision", "lifecycle"),
    (
        ("publish", 0, "active"),
        ("complete", 1, "complete"),
    ),
)
def test_open_rejects_completed_effect_at_active_or_complete_revision(
    tmp_path: Path,
    current_state_id: str,
    revision: int,
    lifecycle: str,
) -> None:
    """Forged equal-revision completion cannot authorize effect reinvocation."""

    NonRepeatSafeEffectRutter.instruction_calls = 0
    path = tmp_path / f"forged-completed-{lifecycle}.reckoning.json"
    marker = tmp_path / f"forged-completed-{lifecycle}.txt"
    charter = _effect_charter("non-repeat-safe-effect", path, marker)
    NonRepeatSafeEffectRutter.create(path, charter)
    forged = Reckoning(
        storage_version=1,
        charter=charter,
        fix=Fix(
            current_state_id=current_state_id,
            revision=revision,
            lifecycle=lifecycle,
            effect=_EffectRecovery(
                state_id="publish",
                revision=revision,
                disposition="completed",
                repeat_safe=False,
            ),
        ),
    )
    forged_bytes = storage_module._canonical_reckoning_bytes(forged)
    path.write_bytes(forged_bytes)

    with pytest.raises(RutterStateError, match="completed effect"):
        NonRepeatSafeEffectRutter.open(path)

    assert path.read_bytes() == forged_bytes
    assert NonRepeatSafeEffectRutter.instruction_calls == 0
    assert not marker.exists()


def test_authored_effect_validation_exception_before_return_remains_planned(
    tmp_path: Path,
) -> None:
    """Exception type cannot turn an unconfirmed effect return into completion."""

    RepeatSafeEffectRutter.reset_calls()
    RepeatSafeEffectRutter.raise_validation_before_return = True
    path = tmp_path / "authored-validation-error.reckoning.json"
    marker = tmp_path / "authored-validation-error.txt"
    rutter = RepeatSafeEffectRutter.create(
        path,
        _effect_charter("repeat-safe-effect", path, marker),
    )

    with pytest.raises(RutterValidationError) as caught:
        rutter.advance()
    reopened = RepeatSafeEffectRutter.open(path)

    assert tuple(issue.code for issue in caught.value.report.issues) == (
        "authored_exception",
    )
    assert marker.read_text(encoding="utf-8") == "1"
    assert reopened.fix.lifecycle == "active"
    assert reopened.fix.effect is not None
    assert reopened.fix.effect.disposition == "planned"
    assert reopened.fix.diagnostics == ()


def test_post_return_normalization_exception_records_completed_fault(
    tmp_path: Path,
) -> None:
    """A returned effect result confirms work even when Mapping inspection fails."""

    RepeatSafeEffectRutter.reset_calls()
    RepeatSafeEffectRutter.raise_during_normalization = True
    path = tmp_path / "normalization-error.reckoning.json"
    marker = tmp_path / "normalization-error.txt"
    rutter = RepeatSafeEffectRutter.create(
        path,
        _effect_charter("repeat-safe-effect", path, marker),
    )

    with pytest.raises(RuntimeError, match="post-return mapping"):
        rutter.advance()
    reopened = RepeatSafeEffectRutter.open(path)

    assert marker.read_text(encoding="utf-8") == "1"
    assert reopened.fix.lifecycle == "faulted"
    assert reopened.fix.effect is not None
    assert reopened.fix.effect.disposition == "completed"
    assert reopened.fix.diagnostics[0].code == "post_effect_validation"
    assert "RuntimeError" in reopened.fix.diagnostics[0].message


def test_validation_and_dry_run_never_invoke_effectful_callable(
    tmp_path: Path,
) -> None:
    """Observational operations cannot create planned authority or artifacts."""

    RepeatSafeEffectRutter.reset_calls()
    path = tmp_path / "effect-observation.reckoning.json"
    marker = tmp_path / "should-not-exist.txt"
    rutter = RepeatSafeEffectRutter.create(
        path,
        _effect_charter("repeat-safe-effect", path, marker),
    )
    before = path.read_bytes()
    envelope = {"revision": 0, "outcome": "published", "evidence": {}}

    report = rutter.validate(envelope)
    successor = rutter.advance(envelope, continue_=False, dry_run=True)

    assert report.valid is True
    assert successor == "complete"
    assert RepeatSafeEffectRutter.instruction_calls == 0
    assert not marker.exists()
    assert path.read_bytes() == before


@pytest.mark.parametrize("rutter_kind", ("pure", "effectful"))
def test_non_dry_advance_cannot_bypass_callable_invocation_with_input(
    tmp_path: Path,
    rutter_kind: str,
) -> None:
    """Only the engine may attach authority to a callable instruction result."""

    if rutter_kind == "pure":
        CallableProbeRutter.instruction_calls = 0
        path = tmp_path / "pure-bypass.reckoning.json"
        rutter = CallableProbeRutter.create(
            path,
            Charter("callable-probe", 1, {}),
        )
        outcome = "done"
    else:
        RepeatSafeEffectRutter.reset_calls()
        path = tmp_path / "effect-bypass.reckoning.json"
        marker = tmp_path / "effect-bypass.txt"
        rutter = RepeatSafeEffectRutter.create(
            path,
            _effect_charter("repeat-safe-effect", path, marker),
        )
        outcome = "published"
    before = path.read_bytes()

    with pytest.raises(RutterStateError, match="callable"):
        rutter.advance(
            {"revision": 0, "outcome": outcome, "evidence": {}},
            continue_=False,
        )

    assert path.read_bytes() == before
    if rutter_kind == "pure":
        assert CallableProbeRutter.instruction_calls == 0
    else:
        assert RepeatSafeEffectRutter.instruction_calls == 0
        assert not marker.exists()


@pytest.mark.parametrize("storage_version", (0, 2, True, 1.0, "1"))
def test_reckoning_rejects_every_unsupported_storage_version(
    storage_version: object,
) -> None:
    """Positive but unknown versions cannot enter the bound model."""

    with pytest.raises(RutterStateError, match="storage_version"):
        Reckoning(
            storage_version=storage_version,  # type: ignore[arg-type]
            charter=example_charter(),
            fix=Fix(current_state_id="review", revision=0, lifecycle="active"),
        )


def test_binding_preserves_the_exact_reckoning_value(tmp_path: Path) -> None:
    """Diagnostics expose the exact bound authority, not a reconstructed value."""

    reckoning = Reckoning(
        storage_version=1,
        charter=example_charter(),
        fix=Fix(current_state_id="review", revision=0, lifecycle="active"),
    )

    rutter = ExampleRutter._bind(tmp_path / "exact.reckoning.json", reckoning)

    assert rutter.reckoning is reckoning


def test_create_and_open_each_freeze_one_fresh_definition(tmp_path: Path) -> None:
    """Every bound voyage evaluates ``define_states`` exactly once."""

    ExampleRutter.reset_calls()
    path = tmp_path / "example.reckoning.json"

    created = ExampleRutter.create(path, example_charter())
    opened = ExampleRutter.open(path)

    assert ExampleRutter.define_calls == 2
    assert created.charter == opened.charter == example_charter()
    assert created.fix == opened.fix
    assert created.reckoning == opened.reckoning


def test_bound_state_mapping_is_a_frozen_copy(tmp_path: Path) -> None:
    """A consumer cannot mutate the canonical graph after binding."""

    rutter = ExampleRutter.create(tmp_path / "frozen.reckoning.json", example_charter())

    with pytest.raises(TypeError):
        rutter._states["other"] = TerminalState()  # type: ignore[index]


def test_binding_validates_graph_without_executing_authored_functions(
    tmp_path: Path,
) -> None:
    """Definition checks cannot perform work or choose a route."""

    CallableProbeRutter.instruction_calls = 0
    CallableProbeRutter.validator_calls = 0
    CallableProbeRutter.next_state_calls = 0

    CallableProbeRutter.create(
        tmp_path / "probe.reckoning.json",
        Charter("callable-probe", 1, {}),
    )

    assert CallableProbeRutter.instruction_calls == 0
    assert CallableProbeRutter.validator_calls == 0
    assert CallableProbeRutter.next_state_calls == 0


@pytest.mark.parametrize("state_id", ("", "Bad", "two words", "../escape", "end_"))
def test_binding_rejects_empty_or_invalid_state_ids(
    tmp_path: Path, state_id: str
) -> None:
    """Every mapping key, including terminal keys, uses one stable safe ID."""

    class InvalidIdRutter(BaseRutter):
        rutter_id = "invalid-id"
        definition_version = 1
        start_state = "start"

        def define_states(self) -> Mapping[str, State | TerminalState]:
            return {"start": TerminalState(), state_id: TerminalState()}

    with pytest.raises(RutterDefinitionError, match="state ID"):
        InvalidIdRutter.create(
            tmp_path / f"invalid-{state_id!r}.reckoning.json",
            Charter("invalid-id", 1, {}),
        )


def test_binding_rejects_missing_or_terminal_start_state(tmp_path: Path) -> None:
    """A voyage must start at one present nonterminal state."""

    class TerminalStartRutter(BaseRutter):
        rutter_id = "terminal-start"
        definition_version = 1
        start_state = "complete"

        def define_states(self) -> Mapping[str, State | TerminalState]:
            return {"complete": TerminalState()}

    with pytest.raises(RutterDefinitionError, match="start state"):
        TerminalStartRutter.create(
            tmp_path / "terminal-start.reckoning.json",
            Charter("terminal-start", 1, {}),
        )


def test_create_rejects_a_missing_start_id_as_a_definition_error(
    tmp_path: Path,
) -> None:
    """Missing definition metadata fails closed before any graph hook runs."""

    class MissingStartRutter(BaseRutter):
        rutter_id = "missing-start"
        definition_version = 1

        def define_states(self) -> Mapping[str, State | TerminalState]:
            raise AssertionError("define_states must not run without a start ID")

    with pytest.raises(RutterDefinitionError, match="start state ID"):
        MissingStartRutter.create(
            tmp_path / "missing-start.reckoning.json",
            Charter("missing-start", 1, {}),
        )


def test_successor_guard_rejects_an_absent_returned_state_without_other_calls(
    tmp_path: Path,
) -> None:
    """A returned successor must name an entry in the same frozen mapping."""

    CallableProbeRutter.instruction_calls = 0
    CallableProbeRutter.validator_calls = 0
    CallableProbeRutter.next_state_calls = 0
    rutter = CallableProbeRutter.create(
        tmp_path / "successor.reckoning.json",
        Charter("callable-probe", 1, {}),
    )

    with pytest.raises(RutterStateError, match="successor"):
        rutter._require_successor_state("missing")
    assert CallableProbeRutter.instruction_calls == 0
    assert CallableProbeRutter.validator_calls == 0
    assert CallableProbeRutter.next_state_calls == 0


@pytest.mark.parametrize(
    "operation",
    ("create", "open", "get_instruction", "validate", "advance"),
)
def test_definition_cannot_override_engine_operations(operation: str) -> None:
    """Named Rutters own graph hooks, never construction or reduction."""

    namespace = {
        "rutter_id": f"override-{operation}",
        "definition_version": 1,
        "start_state": "start",
        "define_states": lambda self: {"start": TerminalState()},
        operation: lambda *args, **kwargs: None,
    }

    with pytest.raises(RutterDefinitionError, match=operation):
        type(f"Override{operation.title()}Rutter", (BaseRutter,), namespace)


@pytest.mark.parametrize("hook", ("_validate_reckoning", "_synchronize"))
def test_definition_cannot_override_engine_owned_hooks(hook: str) -> None:
    """Authored hooks cannot bypass semantic validation or authority caching."""

    namespace = {
        "rutter_id": f"override-{hook.strip('_').replace('_', '-')}",
        "definition_version": 1,
        "start_state": "start",
        "define_states": lambda self: {"start": TerminalState()},
        hook: lambda *args, **kwargs: None,
    }

    with pytest.raises(RutterDefinitionError, match=hook):
        type("OverrideEngineHookRutter", (BaseRutter,), namespace)


def test_definition_cannot_supply_singleton_new() -> None:
    """Binding cannot reuse a definition-owned singleton across voyages."""

    singleton = object.__new__(BaseRutter)

    def singleton_new(cls: type[BaseRutter]) -> BaseRutter:
        del cls
        return singleton

    namespace = {
        "rutter_id": "singleton-new",
        "definition_version": 1,
        "start_state": "start",
        "define_states": lambda self: {"start": TerminalState()},
        "__new__": singleton_new,
    }

    with pytest.raises(RutterDefinitionError, match="__new__"):
        type("SingletonNewRutter", (BaseRutter,), namespace)


def test_definition_must_directly_and_exclusively_subclass_base_rutter() -> None:
    """Named definitions cannot inherit another Rutter or add a mixin base."""

    class DirectRutter(BaseRutter):
        rutter_id = "direct"
        definition_version = 1
        start_state = "start"

        def define_states(self) -> Mapping[str, State | TerminalState]:
            return {"start": TerminalState()}

    with pytest.raises(RutterDefinitionError, match="directly and exclusively"):
        type(
            "IndirectRutter",
            (DirectRutter,),
            {
                "rutter_id": "indirect",
                "definition_version": 1,
                "start_state": "start",
            },
        )

    class DefinitionMixin:
        pass

    with pytest.raises(RutterDefinitionError, match="directly and exclusively"):
        type(
            "MixedRutter",
            (DefinitionMixin, BaseRutter),
            {
                "rutter_id": "mixed",
                "definition_version": 1,
                "start_state": "start",
                "define_states": lambda self: {"start": TerminalState()},
            },
        )


def test_definition_rejects_mixin_shadowing_of_an_engine_operation() -> None:
    """Resolved engine methods must remain owned by ``BaseRutter``."""

    class ShadowingMixin:
        def advance(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

    with pytest.raises(RutterDefinitionError, match="advance"):
        type(
            "ShadowedRutter",
            (ShadowingMixin, BaseRutter),
            {
                "rutter_id": "shadowed",
                "definition_version": 1,
                "start_state": "start",
                "define_states": lambda self: {"start": TerminalState()},
            },
        )
