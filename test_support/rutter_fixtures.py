"""Direct Rutter definitions shared by focused model and engine tests."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Mapping

from officina.rutter.model import (
    BaseRutter,
    Charter,
    EffectPolicy,
    Fix,
    InputValidatorContract,
    JsonValue,
    Reckoning,
    State,
    TerminalState,
    ValidationIssue,
    ValidationReport,
    RutterValidationError,
)


def example_charter() -> Charter:
    """Return one literal immutable undertaking charter."""

    return Charter(
        rutter_id="example",
        definition_version=1,
        data={"artifact": "draft.md", "options": ["careful"]},
    )


def example_reckoning(
    *,
    state_id: str = "review",
    revision: int = 0,
    lifecycle: str = "active",
) -> Reckoning:
    """Return one complete literal Reckoning for storage tests."""

    return Reckoning(
        storage_version=1,
        charter=example_charter(),
        fix=Fix(
            current_state_id=state_id,
            revision=revision,
            lifecycle=lifecycle,
        ),
    )


class ExampleRutter(BaseRutter):
    """Expose one direct string state and two terminal successors."""

    rutter_id = "example"
    definition_version = 1
    start_state = "review"

    define_calls = 0
    instruction_calls = 0
    validator_calls = 0
    next_state_calls = 0

    @classmethod
    def reset_calls(cls) -> None:
        """Reset definition and executable-hook observations."""

        cls.define_calls = 0
        cls.instruction_calls = 0
        cls.validator_calls = 0
        cls.next_state_calls = 0

    @staticmethod
    def validate_review(value: Mapping[str, JsonValue]) -> ValidationReport:
        """Accept the two literal review outcomes used by later engine tests."""

        ExampleRutter.validator_calls += 1
        outcome = value.get("outcome")
        if outcome in {"accepted", "revise"}:
            return ValidationReport(valid=True)
        return ValidationReport(
            valid=False,
            issues=(
                ValidationIssue(
                    path="outcome",
                    code="invalid_outcome",
                    message="outcome must be accepted or revise",
                ),
            ),
        )

    @staticmethod
    def next_after_review(value: Mapping[str, JsonValue]) -> str:
        """Select the direct successor named by the validated outcome."""

        ExampleRutter.next_state_calls += 1
        return "complete" if value["outcome"] == "accepted" else "revision-required"

    def define_states(self) -> Mapping[str, State | TerminalState]:
        """Return the complete readable state graph."""

        type(self).define_calls += 1
        return {
            "review": State(
                instruction="Review the current material.",
                input_validator=InputValidatorContract(
                    self.validate_review,
                    ("accepted", "revise"),
                ),
                next_state=self.next_after_review,
                description="Review the current material.",
            ),
            "complete": TerminalState(description="Review completed."),
            "revision-required": TerminalState(description="Revision is required."),
        }


class CallableProbeRutter(BaseRutter):
    """Fail visibly if graph binding executes any authored callable."""

    rutter_id = "callable-probe"
    definition_version = 1
    start_state = "probe"

    instruction_calls = 0
    validator_calls = 0
    next_state_calls = 0

    def instruction(self) -> Mapping[str, JsonValue]:
        """Record invocation and return one finite envelope fragment."""

        type(self).instruction_calls += 1
        return {"outcome": "done", "evidence": {}}

    def validate_probe(self, value: Mapping[str, JsonValue]) -> ValidationReport:
        """Record validation without imposing additional fixture semantics."""

        del value
        type(self).validator_calls += 1
        return ValidationReport(valid=True)

    def next_after_probe(self, value: Mapping[str, JsonValue]) -> str:
        """Record transition selection and return the terminal ID."""

        del value
        type(self).next_state_calls += 1
        return "complete"

    def define_states(self) -> Mapping[str, State | TerminalState]:
        """Return one callable state and its terminal successor."""

        return {
            "probe": State(
                instruction=self.instruction,
                input_validator=InputValidatorContract(
                    self.validate_probe,
                    ("done",),
                ),
                next_state=self.next_after_probe,
            ),
            "complete": TerminalState(),
        }


class PureChainRutter(BaseRutter):
    """Expose a string edge followed by two pure callable edges."""

    rutter_id = "pure-chain"
    definition_version = 1
    start_state = "collect"

    instruction_calls = 0
    validator_calls = 0
    next_state_calls = 0
    observed_revisions: list[int] = []

    @classmethod
    def reset_calls(cls) -> None:
        """Reset executable-hook observations for one behavior test."""

        cls.instruction_calls = 0
        cls.validator_calls = 0
        cls.next_state_calls = 0
        cls.observed_revisions = []

    @staticmethod
    def validate_collect(value: Mapping[str, JsonValue]) -> ValidationReport:
        """Accept only the literal collected outcome."""

        PureChainRutter.validator_calls += 1
        return _literal_outcome_report(value, "collected")

    @staticmethod
    def next_after_collect(value: Mapping[str, JsonValue]) -> str:
        """Cross from the string instruction to the first pure callable."""

        del value
        PureChainRutter.next_state_calls += 1
        return "normalize"

    @staticmethod
    def normalize() -> Mapping[str, JsonValue]:
        """Return one state-owned callable result without framework revision."""

        PureChainRutter.instruction_calls += 1
        return {"outcome": "normalized", "evidence": {"stage": 1}}

    @staticmethod
    def validate_normalize(value: Mapping[str, JsonValue]) -> ValidationReport:
        """Record the engine-attached revision and accept normalization."""

        PureChainRutter.validator_calls += 1
        revision = value.get("revision")
        if type(revision) is int:
            PureChainRutter.observed_revisions.append(revision)
        return _literal_outcome_report(value, "normalized")

    @staticmethod
    def next_after_normalize(value: Mapping[str, JsonValue]) -> str:
        """Cross to the second pure callable."""

        del value
        PureChainRutter.next_state_calls += 1
        return "finish"

    @staticmethod
    def finish() -> Mapping[str, JsonValue]:
        """Return the second state-owned callable result."""

        PureChainRutter.instruction_calls += 1
        return {"outcome": "finished", "evidence": {"stage": 2}}

    @staticmethod
    def validate_finish(value: Mapping[str, JsonValue]) -> ValidationReport:
        """Record the engine-attached revision and accept completion."""

        PureChainRutter.validator_calls += 1
        revision = value.get("revision")
        if type(revision) is int:
            PureChainRutter.observed_revisions.append(revision)
        return _literal_outcome_report(value, "finished")

    @staticmethod
    def next_after_finish(value: Mapping[str, JsonValue]) -> str:
        """Cross to the terminal graph entry."""

        del value
        PureChainRutter.next_state_calls += 1
        return "complete"

    def define_states(self) -> Mapping[str, State | TerminalState]:
        """Return the literal four-entry continued graph."""

        return {
            "collect": State(
                instruction="Collect one finite result.",
                input_validator=InputValidatorContract(
                    self.validate_collect,
                    ("collected",),
                ),
                next_state=self.next_after_collect,
            ),
            "normalize": State(
                instruction=self.normalize,
                input_validator=InputValidatorContract(
                    self.validate_normalize,
                    ("normalized",),
                ),
                next_state=self.next_after_normalize,
            ),
            "finish": State(
                instruction=self.finish,
                input_validator=InputValidatorContract(
                    self.validate_finish,
                    ("finished",),
                ),
                next_state=self.next_after_finish,
            ),
            "complete": TerminalState(description="Pure chain completed."),
        }


def _literal_outcome_report(
    value: Mapping[str, JsonValue], expected: str
) -> ValidationReport:
    """Validate one fixture outcome without sharing engine normalization logic."""

    if value.get("outcome") == expected:
        return ValidationReport(valid=True)
    return ValidationReport(
        valid=False,
        issues=(
            ValidationIssue(
                path="outcome",
                code="invalid_outcome",
                message=f"outcome must be {expected}",
            ),
        ),
    )


class LoopRutter(BaseRutter):
    """Expose one pure callable self-loop for settling-limit tests."""

    rutter_id = "loop"
    definition_version = 1
    start_state = "loop"
    instruction_calls = 0

    @staticmethod
    def loop() -> Mapping[str, JsonValue]:
        """Return one valid result forever."""

        LoopRutter.instruction_calls += 1
        return {"outcome": "again", "evidence": {}}

    @staticmethod
    def validate_loop(value: Mapping[str, JsonValue]) -> ValidationReport:
        """Accept the literal self-loop result."""

        return _literal_outcome_report(value, "again")

    @staticmethod
    def next_loop(value: Mapping[str, JsonValue]) -> str:
        """Select the same state without mutation."""

        del value
        return "loop"

    def define_states(self) -> Mapping[str, State | TerminalState]:
        """Return the one-state looping graph."""

        return {
            "loop": State(
                instruction=self.loop,
                input_validator=InputValidatorContract(
                    self.validate_loop,
                    ("again",),
                ),
                next_state=self.next_loop,
            )
        }


def _record_effect_attempt(rutter: BaseRutter) -> int:
    """Write one fixture artifact after proving planned authority is durable."""

    import json

    reckoning_path = Path(str(rutter.charter.data["reckoning_path"]))
    marker_path = Path(str(rutter.charter.data["marker_path"]))
    persisted = json.loads(reckoning_path.read_text(encoding="utf-8"))
    effect = persisted["fix"]["effect"]
    assert effect["disposition"] == "planned"
    assert effect["state_id"] == "publish"
    assert effect["revision"] == persisted["fix"]["revision"]
    attempt = int(marker_path.read_text(encoding="utf-8")) + 1 if marker_path.exists() else 1
    marker_path.write_text(str(attempt), encoding="utf-8")
    return attempt


class RepeatSafeEffectRutter(BaseRutter):
    """Expose one repeat-safe artifact-writing callable."""

    rutter_id = "repeat-safe-effect"
    definition_version = 1
    start_state = "publish"
    instruction_calls = 0
    interrupt_once = False
    result_outcome = "published"
    transition_raises = False
    raise_validation_before_return = False
    raise_during_normalization = False

    @classmethod
    def reset_calls(cls) -> None:
        """Restore the literal successful effect behavior."""

        cls.instruction_calls = 0
        cls.interrupt_once = False
        cls.result_outcome = "published"
        cls.transition_raises = False
        cls.raise_validation_before_return = False
        cls.raise_during_normalization = False

    def publish(self) -> Mapping[str, JsonValue]:
        """Write one artifact and optionally simulate interruption afterward."""

        type(self).instruction_calls += 1
        attempt = _record_effect_attempt(self)
        if type(self).raise_validation_before_return:
            raise RutterValidationError(
                ValidationReport(
                    valid=False,
                    issues=(
                        ValidationIssue(
                            path="effect",
                            code="authored_exception",
                            message="authored effect raised before return",
                        ),
                    ),
                )
            )
        if type(self).interrupt_once and attempt == 1:
            raise InterruptedError("interrupted after artifact write")
        if type(self).raise_during_normalization:
            return _NormalizationExplodingMapping()  # type: ignore[return-value]
        return {
            "outcome": type(self).result_outcome,
            "evidence": {"attempt": attempt},
        }

    @staticmethod
    def validate_publish(value: Mapping[str, JsonValue]) -> ValidationReport:
        """Accept only the successful artifact disposition."""

        return _literal_outcome_report(value, "published")

    @staticmethod
    def next_after_publish(value: Mapping[str, JsonValue]) -> str:
        """Select completion unless the fixture injects transition failure."""

        del value
        if RepeatSafeEffectRutter.transition_raises:
            raise RuntimeError("post-effect transition exploded")
        return "complete"

    def define_states(self) -> Mapping[str, State | TerminalState]:
        """Return one effectful state and its terminal successor."""

        return {
            "publish": State(
                instruction=self.publish,
                input_validator=InputValidatorContract(
                    self.validate_publish,
                    ("published",),
                ),
                next_state=self.next_after_publish,
                effect_policy=EffectPolicy(repeat_safe=True),
            ),
            "complete": TerminalState(description="Artifact published."),
        }


class _NormalizationExplodingMapping(Mapping[str, JsonValue]):
    """Return from an effect, then fail when the engine inspects mapping keys."""

    def __getitem__(self, key: str) -> JsonValue:
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("post-return mapping normalization exploded")

    def __len__(self) -> int:
        return 2


class NonRepeatSafeEffectRutter(BaseRutter):
    """Expose one non-repeat-safe artifact-writing callable."""

    rutter_id = "non-repeat-safe-effect"
    definition_version = 1
    start_state = "publish"
    instruction_calls = 0
    interrupt_once = False

    def publish(self) -> Mapping[str, JsonValue]:
        """Write one artifact and optionally interrupt before confirmation."""

        type(self).instruction_calls += 1
        attempt = _record_effect_attempt(self)
        if type(self).interrupt_once and attempt == 1:
            raise InterruptedError("uncertain non-repeat-safe effect")
        return {"outcome": "published", "evidence": {"attempt": attempt}}

    @staticmethod
    def validate_publish(value: Mapping[str, JsonValue]) -> ValidationReport:
        """Accept only the successful artifact disposition."""

        return _literal_outcome_report(value, "published")

    @staticmethod
    def next_after_publish(value: Mapping[str, JsonValue]) -> str:
        """Select the terminal graph entry."""

        del value
        return "complete"

    def define_states(self) -> Mapping[str, State | TerminalState]:
        """Return one non-repeat-safe state and its terminal successor."""

        return {
            "publish": State(
                instruction=self.publish,
                input_validator=InputValidatorContract(
                    self.validate_publish,
                    ("published",),
                ),
                next_state=self.next_after_publish,
                effect_policy=EffectPolicy(repeat_safe=False),
            ),
            "complete": TerminalState(description="Artifact published."),
        }


def create_example(path: Path) -> ExampleRutter:
    """Create one bound example voyage at ``path``."""

    return ExampleRutter.create(path, example_charter())
