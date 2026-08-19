"""Bind direct Rutter definitions to durable Reckoning reduction authority."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Mapping, cast

from officina.rutter.model import (
    Charter,
    Fix,
    InputValidatorContract,
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
    _STATE_ID,
    _STORAGE_VERSION,
    _freeze_json_mapping,
    _require_identifier,
)
from officina.rutter.storage import _ReckoningStore


_ENVELOPE_KEYS = frozenset({"revision", "outcome", "evidence"})
_CALLABLE_RESULT_KEYS = frozenset({"outcome", "evidence"})
_UNEXPECTED_EVIDENCE_KEYS = frozenset(
    {"observed", "conflict", "why_no_outcome_fits", "uncertainty"}
)
_SETTLING_LIMIT = 100


class BaseRutter:
    """Bind one frozen direct definition to one durable Reckoning."""

    rutter_id: ClassVar[str]
    definition_version: ClassVar[int]
    start_state: ClassVar[str]
    _binding_token: ClassVar[object] = object()

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Require a direct base and retain ownership of every engine operation."""

        super().__init_subclass__(**kwargs)
        engine_owned = {
            name
            for name in BaseRutter.__dict__
            if not name.startswith("__") and name != "define_states"
        } | {"__init__", "__init_subclass__", "__new__"}
        for operation in sorted(engine_owned):
            owner = next(
                (base for base in cls.__mro__ if operation in base.__dict__),
                None,
            )
            if operation == "__new__" and owner is object:
                continue
            if owner is not BaseRutter:
                raise RutterDefinitionError(
                    f"Rutter definitions cannot override {operation}"
                )
        if cls.__bases__ != (BaseRutter,):
            raise RutterDefinitionError(
                "named Rutters must directly and exclusively subclass BaseRutter"
            )

    def __init__(
        self,
        reckoning_path: Path,
        reckoning: Reckoning,
        *,
        _token: object,
    ) -> None:
        """Bind exact authority and freeze the authored state mapping once."""

        if _token is not self._binding_token:
            raise RutterDefinitionError("use create() or open() to bind a Rutter")
        self._reckoning_path = Path(reckoning_path).absolute()
        self._reckoning = reckoning
        self._charter = reckoning.charter
        self._fix = reckoning.fix
        self._validate_identity()
        self._states = self._freeze_states(self.define_states())
        self._validate_reckoning(reckoning)
        self._store = _ReckoningStore(
            self._reckoning_path,
            semantic_validator=self._validate_reckoning,
        )

    @classmethod
    def _bind(cls, reckoning_path: Path, reckoning: Reckoning) -> "BaseRutter":
        """Construct through the engine initializer, bypassing subclass init."""

        instance = object.__new__(cls)
        BaseRutter.__init__(
            instance,
            Path(reckoning_path),
            reckoning,
            _token=cls._binding_token,
        )
        return instance

    @classmethod
    def create(cls, reckoning_path: Path, charter: Charter) -> "BaseRutter":
        """Persist and bind the initial active Reckoning for a new voyage."""

        if not isinstance(charter, Charter):
            raise RutterDefinitionError("create charter must be a Charter")
        start_state = _require_identifier(
            getattr(cls, "start_state", None), label="start state ID"
        )
        reckoning = Reckoning(
            storage_version=_STORAGE_VERSION,
            charter=charter,
            fix=Fix(current_state_id=start_state, revision=0, lifecycle="active"),
        )
        instance = cls._bind(Path(reckoning_path).absolute(), reckoning)
        instance._store.create(reckoning)
        return instance

    @classmethod
    def open(cls, reckoning_path: Path) -> "BaseRutter":
        """Strictly load and bind exact existing authority without advancing it."""

        path = Path(reckoning_path).absolute()
        reckoning = _ReckoningStore(path).read()
        return cls._bind(path, reckoning)

    @property
    def charter(self) -> Charter:
        """Expose the immutable undertaking data bound to this voyage."""

        return self._charter

    @property
    def fix(self) -> Fix:
        """Expose the most recently loaded immutable machine coordinate."""

        return self._fix

    @property
    def reckoning(self) -> Reckoning:
        """Expose the most recently loaded complete durable authority."""

        return self._reckoning

    def define_states(self) -> Mapping[str, State | TerminalState]:
        """Return the direct graph; every named Rutter must implement this."""

        raise NotImplementedError

    def _validate_identity(self) -> None:
        """Require class identity metadata to match the bound Charter exactly."""

        _require_identifier(getattr(type(self), "rutter_id", None), label="rutter ID")
        version = getattr(type(self), "definition_version", None)
        if type(version) is not int or version < 1:
            raise RutterDefinitionError(
                "definition_version must be a positive integer"
            )
        _require_identifier(
            getattr(type(self), "start_state", None), label="start state ID"
        )
        if self._charter.rutter_id != self.rutter_id:
            raise RutterDefinitionError("Charter rutter_id does not match definition")
        if self._charter.definition_version != self.definition_version:
            raise RutterDefinitionError(
                "Charter definition_version does not match definition"
            )

    def _freeze_states(
        self, authored: Mapping[str, State | TerminalState]
    ) -> Mapping[str, State | TerminalState]:
        """Validate and detach the explicit graph without executing authored code."""

        if not isinstance(authored, Mapping):
            raise RutterDefinitionError("define_states() must return a mapping")
        states: dict[str, State | TerminalState] = {}
        for state_id, state in authored.items():
            _require_identifier(state_id, label="state ID")
            if not isinstance(state, (State, TerminalState)):
                raise RutterDefinitionError(
                    f"state {state_id!r} must be State or TerminalState"
                )
            if isinstance(state, State):
                validator = state.input_validator
                if isinstance(state.instruction, str) and type(
                    validator
                ) is not InputValidatorContract:
                    raise RutterDefinitionError(
                        f"string state {state_id!r} requires a validator outcome "
                        "contract"
                    )
                if type(validator) is InputValidatorContract:
                    validator = InputValidatorContract(
                        validator.validator,
                        validator.allowed_outcomes,
                    )
                state = State(
                    instruction=state.instruction,
                    input_validator=validator,
                    next_state=state.next_state,
                    description=state.description,
                    effect_policy=state.effect_policy,
                )
            states[state_id] = state
        if not states:
            raise RutterDefinitionError("state mapping must not be empty")
        if not isinstance(states.get(self.start_state), State):
            raise RutterDefinitionError(
                "start state must name a present nonterminal State"
            )
        return MappingProxyType(states)

    def _validate_reckoning(self, reckoning: Reckoning) -> None:
        """Require durable identity and coordinates to match this bound graph."""

        if reckoning.charter != self._charter:
            raise RutterStateError("Reckoning Charter changed after binding")
        if reckoning.charter.rutter_id != self.rutter_id:
            raise RutterStateError("Charter rutter_id does not match definition")
        if reckoning.charter.definition_version != self.definition_version:
            raise RutterStateError(
                "Charter definition_version does not match definition"
            )
        if reckoning.fix.current_state_id not in self._states:
            raise RutterStateError("Fix current state is absent from the definition")
        coordinate = self._states[reckoning.fix.current_state_id]
        if reckoning.fix.lifecycle == "active" and isinstance(
            coordinate, TerminalState
        ):
            raise RutterStateError("an active Fix cannot name a terminal state")
        if reckoning.fix.lifecycle == "complete" and not isinstance(
            coordinate, TerminalState
        ):
            raise RutterStateError("a complete Fix must name a terminal state")
        if reckoning.fix.lifecycle == "faulted" and not reckoning.fix.diagnostics:
            raise RutterStateError("a faulted Fix requires diagnostics")
        effect = reckoning.fix.effect
        if effect is not None:
            effect_state = self._states.get(effect.state_id)
            if not isinstance(effect_state, State) or effect_state.effect_policy is None:
                raise RutterStateError(
                    "Fix effect must name a declared effectful callable state"
                )
            if effect.repeat_safe != effect_state.effect_policy.repeat_safe:
                raise RutterStateError("Fix effect policy differs from the definition")
            if effect.disposition in {"planned", "uncertain"} and (
                reckoning.fix.lifecycle != "active"
                or effect.state_id != reckoning.fix.current_state_id
                or effect.revision != reckoning.fix.revision
            ):
                raise RutterStateError(
                    "pending effect authority must match the active Fix coordinate"
                )
            if effect.disposition == "completed" and (
                effect.revision > reckoning.fix.revision
            ):
                raise RutterStateError("completed effect revision is in the future")
            if effect.disposition == "completed" and (
                effect.revision == reckoning.fix.revision
                and (
                    reckoning.fix.lifecycle != "faulted"
                    or effect.state_id != reckoning.fix.current_state_id
                )
            ):
                raise RutterStateError(
                    "completed effect must precede active or complete authority"
                )

    def _require_successor_state(self, successor: object) -> str:
        """Validate a runtime successor against the same frozen mapping."""

        if not isinstance(successor, str) or _STATE_ID.fullmatch(successor) is None:
            raise RutterStateError("successor must be a valid state ID")
        if successor not in self._states:
            raise RutterStateError(
                f"successor {successor!r} is absent from the frozen state mapping"
            )
        return successor

    def get_instruction(self) -> object:
        """Return the current instruction or a structured diagnostic status."""

        with self._store.transaction() as reckoning:
            self._synchronize(reckoning)
            state = self._states[reckoning.fix.current_state_id]
            if reckoning.fix.lifecycle == "faulted":
                return self._fault_status(reckoning.fix)
            effect = reckoning.fix.effect
            if effect is not None and effect.disposition == "planned":
                return {
                    "status": "pending_effect",
                    "state": reckoning.fix.current_state_id,
                    "revision": reckoning.fix.revision,
                    "repeat_safe": effect.repeat_safe,
                    "next_action": (
                        "retry_effect"
                        if effect.repeat_safe
                        else "record_uncertainty"
                    ),
                    "authorized_operation": "advance(continue_=True)",
                    "will_invoke_effect": effect.repeat_safe,
                    "message": (
                        "The planned effect may be retried by "
                        "advance(continue_=True)."
                        if effect.repeat_safe
                        else (
                            "Call advance(continue_=True) to record uncertainty "
                            "without invoking the effect."
                        )
                    ),
                }
            if effect is not None and effect.disposition == "uncertain":
                return {
                    "status": "uncertain_effect",
                    "state": reckoning.fix.current_state_id,
                    "revision": reckoning.fix.revision,
                    "repeat_safe": effect.repeat_safe,
                    "next_action": "manual_reconciliation",
                    "authorized_operation": None,
                    "will_invoke_effect": False,
                    "message": (
                        "The effect may have happened; manual reconciliation is "
                        "required. No public recovery transition is authorized."
                    ),
                }
            if isinstance(state, TerminalState):
                return {
                    "status": "terminal",
                    "state": reckoning.fix.current_state_id,
                    "revision": reckoning.fix.revision,
                    "lifecycle": reckoning.fix.lifecycle,
                    "description": state.description,
                }
            if callable(state.instruction):
                if state.effect_policy is not None:
                    return {
                        "status": "effectful_callable",
                        "state": reckoning.fix.current_state_id,
                        "revision": reckoning.fix.revision,
                        "repeat_safe": state.effect_policy.repeat_safe,
                        "message": (
                            "Effect authority will be planned before advance "
                            "invokes work."
                        ),
                    }
                return {
                    "status": "callable",
                    "state": reckoning.fix.current_state_id,
                    "revision": reckoning.fix.revision,
                    "message": (
                        "Callable instruction is ready; call "
                        "advance(continue_=True)."
                    ),
                }
            return self._render_string_instruction(reckoning, state)

    def validate(self, input: Mapping[str, JsonValue]) -> ValidationReport:
        """Validate one input without transition or durable mutation."""

        with self._store.transaction() as reckoning:
            self._synchronize(reckoning)
            state = self._states[reckoning.fix.current_state_id]
            unavailable = self._unavailable_report(reckoning.fix, state)
            if unavailable is not None:
                return unavailable
            assert isinstance(state, State)
            report, _ = self._validate_input(input, reckoning.fix.revision, state)
            return report

    def advance(
        self,
        input: Mapping[str, JsonValue] | None = None,
        *,
        continue_: bool = True,
        dry_run: bool = False,
    ) -> str:
        """Advance bound authority according to the current state definition."""

        if type(continue_) is not bool or type(dry_run) is not bool:
            raise RutterDefinitionError("continue_ and dry_run must be booleans")
        if dry_run and continue_:
            raise RutterStateError("dry_run is incompatible with continue_=True")
        with self._store.transaction() as authoritative:
            self._synchronize(authoritative)
            state = self._require_active_state(authoritative)
            if input is None:
                if isinstance(state.instruction, str) or not continue_ or dry_run:
                    raise RutterValidationError(self._input_required_report())
                normalized = None
            else:
                report, normalized = self._validate_input(
                    input,
                    authoritative.fix.revision,
                    state,
                )
                if not report.valid:
                    raise RutterValidationError(report)
                assert normalized is not None
                if callable(state.instruction) and not dry_run:
                    raise RutterStateError(
                        "non-dry advance cannot accept supplied input at a callable state"
                    )

            if dry_run:
                assert normalized is not None
                if normalized["outcome"] == "unexpected":
                    raise RutterValidationError(
                        self._unexpected_no_successor_report()
                    )
                return self._select_successor(state, normalized)

            published = authoritative
            staged = authoritative
            edge_count = 0
            if normalized is not None:
                if normalized["outcome"] == "unexpected":
                    staged = self._unexpected_reckoning(staged, normalized)
                else:
                    successor = self._select_successor(state, normalized)
                    staged = self._move_to(staged, successor)
                    edge_count = 1
                if not continue_ or staged.fix.lifecycle == "faulted":
                    self._store.replace(authoritative, staged)
                    self._synchronize(staged)
                    return staged.fix.current_state_id

            while continue_ and staged.fix.lifecycle == "active":
                current = self._states[staged.fix.current_state_id]
                if not isinstance(current, State):
                    break
                if isinstance(current.instruction, str):
                    break
                if edge_count >= _SETTLING_LIMIT:
                    raise RutterStateError(
                        f"continuation exceeded settling limit of {_SETTLING_LIMIT}"
                    )
                if current.effect_policy is not None:
                    effect = staged.fix.effect
                    if (
                        effect is not None
                        and effect.state_id == staged.fix.current_state_id
                        and effect.revision == staged.fix.revision
                        and effect.disposition == "uncertain"
                    ):
                        raise RutterStateError(
                            "cannot advance an uncertain non-repeat-safe effect"
                        )
                    if (
                        effect is not None
                        and effect.state_id == staged.fix.current_state_id
                        and effect.revision == staged.fix.revision
                        and effect.disposition == "planned"
                    ):
                        if not effect.repeat_safe:
                            uncertain = self._with_effect(
                                staged,
                                _EffectRecovery(
                                    state_id=effect.state_id,
                                    revision=effect.revision,
                                    disposition="uncertain",
                                    repeat_safe=False,
                                ),
                            )
                            if staged != published:
                                self._store.replace(published, staged)
                                published = staged
                            self._store.replace(published, uncertain)
                            self._synchronize(uncertain)
                            return uncertain.fix.current_state_id
                        planned = staged
                    else:
                        planned = self._with_effect(
                            staged,
                            _EffectRecovery(
                                state_id=staged.fix.current_state_id,
                                revision=staged.fix.revision,
                                disposition="planned",
                                repeat_safe=current.effect_policy.repeat_safe,
                            ),
                        )
                        self._store.replace(published, planned)
                        published = planned
                    staged = planned
                    self._synchronize(planned)
                    raw_result = self._invoke_effect_instruction(current)
                    try:
                        callable_input = self._normalize_callable_result(
                            raw_result,
                            planned.fix,
                        )
                    except Exception as exc:
                        faulted = self._post_effect_fault(
                            planned,
                            code="post_effect_validation",
                            message=(
                                "effect result normalization raised "
                                f"{type(exc).__name__}: {exc}"
                            ),
                        )
                        self._store.replace(published, faulted)
                        self._synchronize(faulted)
                        raise
                    report, normalized = self._validate_input(
                        callable_input,
                        planned.fix.revision,
                        current,
                    )
                    if not report.valid:
                        faulted = self._post_effect_fault(
                            planned,
                            code="post_effect_validation",
                            message="; ".join(issue.message for issue in report.issues),
                        )
                        self._store.replace(published, faulted)
                        self._synchronize(faulted)
                        raise RutterValidationError(report)
                    assert normalized is not None
                    completed_effect = _EffectRecovery(
                        state_id=planned.fix.current_state_id,
                        revision=planned.fix.revision,
                        disposition="completed",
                        repeat_safe=current.effect_policy.repeat_safe,
                    )
                    if normalized["outcome"] == "unexpected":
                        faulted = self._with_effect(
                            self._unexpected_reckoning(planned, normalized),
                            completed_effect,
                        )
                        self._store.replace(published, faulted)
                        self._synchronize(faulted)
                        return faulted.fix.current_state_id
                    try:
                        successor = self._select_successor(current, normalized)
                    except RutterStateError as exc:
                        faulted = self._post_effect_fault(
                            planned,
                            code="post_effect_transition",
                            message=str(exc),
                        )
                        self._store.replace(published, faulted)
                        self._synchronize(faulted)
                        raise
                    completed = self._with_effect(
                        self._move_to(planned, successor),
                        completed_effect,
                    )
                    self._store.replace(published, completed)
                    published = completed
                    staged = completed
                    self._synchronize(completed)
                    edge_count += 1
                    continue
                callable_input = self._invoke_pure_instruction(current, staged.fix)
                report, normalized = self._validate_input(
                    callable_input,
                    staged.fix.revision,
                    current,
                )
                if not report.valid:
                    raise RutterValidationError(report)
                assert normalized is not None
                if normalized["outcome"] == "unexpected":
                    staged = self._unexpected_reckoning(staged, normalized)
                    break
                successor = self._select_successor(current, normalized)
                staged = self._move_to(staged, successor)
                edge_count += 1

            if staged == published:
                return staged.fix.current_state_id
            self._store.replace(published, staged)
            self._synchronize(staged)
            return staged.fix.current_state_id

    def _synchronize(self, reckoning: Reckoning) -> None:
        """Update only the observational cache from one authoritative reload."""

        self._reckoning = reckoning
        self._fix = reckoning.fix

    def _require_active_state(self, reckoning: Reckoning) -> State:
        """Return a nonterminal active State or diagnose stopped authority."""

        state = self._states[reckoning.fix.current_state_id]
        if reckoning.fix.lifecycle == "faulted":
            raise RutterStateError("cannot advance a faulted Reckoning")
        if isinstance(state, TerminalState):
            raise RutterStateError("cannot advance a terminal Reckoning")
        return state

    @staticmethod
    def _input_required_report() -> ValidationReport:
        """Diagnose a missing already-produced state input."""

        return ValidationReport(
            valid=False,
            issues=(
                ValidationIssue(
                    path="$",
                    code="input_required",
                    message="the current state requires an already-produced input",
                ),
            ),
        )

    def _unavailable_report(
        self,
        fix: Fix,
        state: State | TerminalState,
    ) -> ValidationReport | None:
        """Return a diagnostic report when no result can be accepted."""

        if fix.lifecycle == "faulted":
            return ValidationReport(
                valid=False,
                issues=(
                    ValidationIssue(
                        path="state",
                        code="faulted",
                        message="the Reckoning is faulted and accepts no input",
                    ),
                ),
            )
        if fix.effect is not None and fix.effect.disposition in {
            "planned",
            "uncertain",
        }:
            code = (
                "pending_effect"
                if fix.effect.disposition == "planned"
                else "uncertain_effect"
            )
            return ValidationReport(
                valid=False,
                issues=(
                    ValidationIssue(
                        path="effect",
                        code=code,
                        message=(
                            "effect recovery must be resolved before accepting input"
                        ),
                    ),
                ),
            )
        if isinstance(state, TerminalState):
            return ValidationReport(
                valid=False,
                issues=(
                    ValidationIssue(
                        path="state",
                        code="terminal_state",
                        message="the Reckoning is terminal and accepts no input",
                    ),
                ),
            )
        return None

    def _validate_input(
        self,
        value: object,
        revision: int,
        state: State,
    ) -> tuple[ValidationReport, Mapping[str, JsonValue] | None]:
        """Normalize finite JSON, check authority, then call the state validator."""

        normalized = self._normalize_envelope(value)
        if normalized is None:
            return self._invalid_envelope_report(), None
        supplied_revision = normalized["revision"]
        assert type(supplied_revision) is int
        if supplied_revision != revision:
            return (
                ValidationReport(
                    valid=False,
                    issues=(
                        ValidationIssue(
                            path="revision",
                            code="stale_revision",
                            message=(
                                f"input revision {supplied_revision} does not match "
                                f"current revision {revision}"
                            ),
                        ),
                    ),
                ),
                normalized,
            )
        if normalized["outcome"] == "unexpected":
            evidence = normalized["evidence"]
            assert isinstance(evidence, Mapping)
            if not self._unexpected_evidence_valid(evidence):
                return (
                    ValidationReport(
                        valid=False,
                        issues=(
                            ValidationIssue(
                                path="evidence",
                                code="invalid_unexpected_evidence",
                                message=(
                                    "unexpected evidence requires non-empty observed, "
                                    "conflict, why_no_outcome_fits, and uncertainty"
                                ),
                            ),
                        ),
                    ),
                    normalized,
                )
            return ValidationReport(valid=True), normalized
        validator = state.input_validator
        if (
            type(validator) is InputValidatorContract
            and normalized["outcome"] not in validator.allowed_outcomes
        ):
            return (
                ValidationReport(
                    valid=False,
                    issues=(
                        ValidationIssue(
                            path="outcome",
                            code="undeclared_outcome",
                            message=(
                                "outcome is not declared by the current validator "
                                "contract"
                            ),
                        ),
                    ),
                ),
                normalized,
            )
        try:
            report = validator(normalized)
        except Exception as exc:
            return (
                ValidationReport(
                    valid=False,
                    issues=(
                        ValidationIssue(
                            path="$",
                            code="validator_error",
                            message=(
                                "current input_validator raised "
                                f"{type(exc).__name__}: {exc}"
                            ),
                        ),
                    ),
                ),
                normalized,
            )
        if not isinstance(report, ValidationReport):
            return (
                ValidationReport(
                    valid=False,
                    issues=(
                        ValidationIssue(
                            path="$",
                            code="validator_contract",
                            message="input_validator must return ValidationReport",
                        ),
                    ),
                ),
                normalized,
            )
        return report, normalized

    @staticmethod
    def _normalize_envelope(
        value: object,
    ) -> Mapping[str, JsonValue] | None:
        """Return one immutable exact finite input envelope or ``None``."""

        if not isinstance(value, Mapping) or set(value) != _ENVELOPE_KEYS:
            return None
        if type(value.get("revision")) is not int or cast(int, value["revision"]) < 0:
            return None
        if not isinstance(value.get("outcome"), str) or not cast(
            str, value["outcome"]
        ):
            return None
        if not isinstance(value.get("evidence"), Mapping):
            return None
        try:
            return _freeze_json_mapping(value, label="Rutter input")
        except RutterDefinitionError:
            return None

    @staticmethod
    def _invalid_envelope_report() -> ValidationReport:
        """Diagnose any structural, type, key, or finite-JSON envelope failure."""

        return ValidationReport(
            valid=False,
            issues=(
                ValidationIssue(
                    path="$",
                    code="invalid_envelope",
                    message=(
                        "input must be exact finite JSON with revision, outcome, "
                        "and evidence fields"
                    ),
                ),
            ),
        )

    @staticmethod
    def _unexpected_no_successor_report() -> ValidationReport:
        """Diagnose reserved mismatch evidence that selects no graph edge."""

        return ValidationReport(
            valid=False,
            issues=(
                ValidationIssue(
                    path="outcome",
                    code="unexpected_has_no_successor",
                    message=(
                        "unexpected mismatch evidence has no successor and cannot "
                        "be previewed as a transition"
                    ),
                ),
            ),
        )

    @staticmethod
    def _unexpected_evidence_valid(evidence: Mapping[str, JsonValue]) -> bool:
        """Require every reserved unexpected diagnostic to be informative."""

        return set(evidence) == _UNEXPECTED_EVIDENCE_KEYS and all(
            isinstance(evidence[name], str) and bool(evidence[name].strip())
            for name in _UNEXPECTED_EVIDENCE_KEYS
        )

    def _select_successor(
        self,
        state: State,
        normalized: Mapping[str, JsonValue],
    ) -> str:
        """Run the mutation-free transition and validate its direct successor."""

        try:
            successor = state.next_state(normalized)
        except Exception as exc:
            raise RutterStateError(
                f"next_state failed without moving authority: {type(exc).__name__}: {exc}"
            ) from exc
        return self._require_successor_state(successor)

    def _move_to(self, reckoning: Reckoning, successor: str) -> Reckoning:
        """Build one successor Fix and increment exactly one crossed edge."""

        lifecycle = (
            "complete"
            if isinstance(self._states[successor], TerminalState)
            else "active"
        )
        return Reckoning(
            storage_version=_STORAGE_VERSION,
            charter=reckoning.charter,
            fix=Fix(
                current_state_id=successor,
                revision=reckoning.fix.revision + 1,
                lifecycle=lifecycle,
                effect=reckoning.fix.effect,
            ),
        )

    def _invoke_pure_instruction(
        self,
        state: State,
        fix: Fix,
    ) -> Mapping[str, JsonValue]:
        """Invoke one declared-pure callable and attach framework revision."""

        assert callable(state.instruction)
        try:
            result = state.instruction()
        except Exception as exc:
            raise RutterStateError(
                "pure callable instruction failed without moving authority: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return self._normalize_callable_result(result, fix)

    def _invoke_effect_instruction(
        self,
        state: State,
    ) -> object:
        """Invoke one durably planned effect and return its raw confirmed result."""

        assert callable(state.instruction)
        return state.instruction()

    def _normalize_callable_result(
        self,
        result: object,
        fix: Fix,
    ) -> Mapping[str, JsonValue]:
        """Attach current framework authority to one callable-owned fragment."""

        if not isinstance(result, Mapping) or set(result) != _CALLABLE_RESULT_KEYS:
            raise RutterValidationError(self._invalid_envelope_report())
        envelope = {
            "revision": fix.revision,
            "outcome": result.get("outcome"),
            "evidence": result.get("evidence"),
        }
        normalized = self._normalize_envelope(envelope)
        if normalized is None:
            raise RutterValidationError(self._invalid_envelope_report())
        return normalized

    def _with_effect(
        self,
        reckoning: Reckoning,
        effect: _EffectRecovery,
    ) -> Reckoning:
        """Return the same coordinate with one exact recovery disposition."""

        return Reckoning(
            _STORAGE_VERSION,
            reckoning.charter,
            Fix(
                current_state_id=reckoning.fix.current_state_id,
                revision=reckoning.fix.revision,
                lifecycle=reckoning.fix.lifecycle,
                effect=effect,
                diagnostics=reckoning.fix.diagnostics,
            ),
        )

    def _post_effect_fault(
        self,
        reckoning: Reckoning,
        *,
        code: str,
        message: str,
    ) -> Reckoning:
        """Record confirmed non-rollbackable work with a diagnostic fault."""

        effect = reckoning.fix.effect
        assert effect is not None
        return Reckoning(
            _STORAGE_VERSION,
            reckoning.charter,
            Fix(
                current_state_id=reckoning.fix.current_state_id,
                revision=reckoning.fix.revision,
                lifecycle="faulted",
                effect=_EffectRecovery(
                    state_id=effect.state_id,
                    revision=effect.revision,
                    disposition="completed",
                    repeat_safe=effect.repeat_safe,
                ),
                diagnostics=(ValidationIssue("effect", code, message),),
            ),
        )

    def _unexpected_reckoning(
        self,
        reckoning: Reckoning,
        normalized: Mapping[str, JsonValue],
    ) -> Reckoning:
        """Persist complete reserved mismatch evidence as an explicit fault."""

        evidence = cast(Mapping[str, JsonValue], normalized["evidence"])
        message = " ".join(
            (
                f"Observed: {evidence['observed']}",
                f"Conflict: {evidence['conflict']}",
                f"Why no outcome fits: {evidence['why_no_outcome_fits']}",
                f"Uncertainty: {evidence['uncertainty']}",
            )
        )
        return Reckoning(
            _STORAGE_VERSION,
            reckoning.charter,
            Fix(
                current_state_id=reckoning.fix.current_state_id,
                revision=reckoning.fix.revision,
                lifecycle="faulted",
                effect=reckoning.fix.effect,
                diagnostics=(ValidationIssue("evidence", "unexpected", message),),
            ),
        )

    def _render_string_instruction(self, reckoning: Reckoning, state: State) -> str:
        """Render all source-free authority needed to execute one string state."""

        validator = state.input_validator
        assert type(validator) is InputValidatorContract
        outcomes = validator.allowed_outcomes
        displayed_outcomes = tuple(dict.fromkeys((*outcomes, "unexpected")))
        charter_json = json.dumps(
            self._mutable_json(reckoning.charter.data),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "\n".join(
            (
                cast(str, state.instruction).strip(),
                "",
                f"Charter JSON: {charter_json}",
                f"Current state: {reckoning.fix.current_state_id}",
                f"Current revision: {reckoning.fix.revision}",
                "Return exact finite JSON: "
                f'{{"revision":{reckoning.fix.revision},'
                '"outcome":"<allowed outcome>","evidence":{}}}',
                "Allowed outcomes: " + ", ".join(displayed_outcomes),
                "If the displayed revision does not match current authority, stop; "
                "the revision mismatch is stale input.",
                "If no declared outcome fits, return unexpected with non-empty "
                "observed, conflict, why_no_outcome_fits, and uncertainty evidence.",
                "Instruction work and other external work is not rolled back by "
                "Reckoning replacement.",
            )
        )

    @classmethod
    def _mutable_json(cls, value: JsonValue) -> object:
        """Convert immutable model JSON to plain containers for rendering only."""

        if isinstance(value, Mapping):
            return {key: cls._mutable_json(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [cls._mutable_json(item) for item in value]
        return value

    @staticmethod
    def _fault_status(fix: Fix) -> Mapping[str, JsonValue]:
        """Return complete fault diagnostics without source inspection."""

        return {
            "status": "fault",
            "state": fix.current_state_id,
            "revision": fix.revision,
            "lifecycle": fix.lifecycle,
            "diagnostics": tuple(
                {
                    "path": issue.path,
                    "code": issue.code,
                    "message": issue.message,
                }
                for issue in fix.diagnostics
            ),
        }
