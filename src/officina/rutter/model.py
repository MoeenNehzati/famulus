"""Define the immutable authoring and persisted values for direct Rutters.

A named Rutter supplies one explicit mapping from state IDs to :class:`State`
or :class:`TerminalState` values.  The framework binds a fresh, frozen copy of
that mapping to one immutable ``Charter + Fix = Reckoning``.  This module owns
the data-only authoring values; storage and reduction remain in their dedicated
modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import re
from types import MappingProxyType
from typing import Callable, Mapping, TypeAlias


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = (
    JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
)
InputValidator: TypeAlias = Callable[[Mapping[str, JsonValue]], "ValidationReport"]
NextState: TypeAlias = Callable[[Mapping[str, JsonValue]], str]
Instruction: TypeAlias = str | Callable[[], Mapping[str, JsonValue]]

_STATE_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_STORAGE_VERSION = 1


class RutterDefinitionError(Exception):
    """Report an invalid Charter or immutable Rutter definition."""


class RutterStateError(Exception):
    """Report persisted or runtime authority that cannot be interpreted safely."""


@dataclass(frozen=True)
class ValidationIssue:
    """Describe one validation failure without changing machine authority."""

    path: str
    code: str
    message: str

    def __post_init__(self) -> None:
        """Require stable nonempty diagnostic components."""

        for label, value in (
            ("path", self.path),
            ("code", self.code),
            ("message", self.message),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"validation issue {label} must be a non-empty string")


@dataclass(frozen=True)
class ValidationReport:
    """Return the complete, observational result of one pure validation."""

    valid: bool
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        """Reject contradictory or malformed validator reports."""

        if type(self.valid) is not bool:
            raise ValueError("validation report valid must be a boolean")
        if not isinstance(self.issues, tuple) or not all(
            isinstance(issue, ValidationIssue) for issue in self.issues
        ):
            raise ValueError("validation report issues must be ValidationIssue values")
        if self.valid and self.issues:
            raise ValueError("a valid report cannot contain issues")
        if not self.valid and not self.issues:
            raise ValueError("an invalid report must contain at least one issue")


@dataclass(frozen=True)
class InputValidatorContract:
    """Bind one validator to its finite source-free outcome vocabulary."""

    validator: InputValidator
    allowed_outcomes: tuple[str, ...]

    def __post_init__(self) -> None:
        """Copy and validate one nonempty unique outcome tuple."""

        if not callable(self.validator):
            raise RutterDefinitionError("validator outcome contract must be callable")
        raw_outcomes = self.allowed_outcomes
        if isinstance(raw_outcomes, str) or not isinstance(
            raw_outcomes, (list, tuple)
        ):
            raise RutterDefinitionError(
                "validator allowed outcomes must be a non-empty sequence"
            )
        outcomes = tuple(raw_outcomes)
        if not outcomes:
            raise RutterDefinitionError(
                "validator allowed outcomes must be a non-empty sequence"
            )
        if any(
            not isinstance(outcome, str)
            or _STATE_ID.fullmatch(outcome) is None
            or outcome == "unexpected"
            for outcome in outcomes
        ):
            raise RutterDefinitionError(
                "validator allowed outcomes must be lowercase kebab-case and "
                "exclude reserved unexpected"
            )
        if len(set(outcomes)) != len(outcomes):
            raise RutterDefinitionError("validator allowed outcomes must be unique")
        object.__setattr__(self, "allowed_outcomes", outcomes)

    def __call__(self, value: Mapping[str, JsonValue]) -> ValidationReport:
        """Delegate validation without changing the frozen outcome contract."""

        return self.validator(value)


class RutterValidationError(Exception):
    """Raise an invalid operation together with its complete validation report."""

    def __init__(self, report: ValidationReport) -> None:
        """Retain the report so callers need not parse exception text."""

        super().__init__("Rutter input is invalid")
        self.report = report


def _freeze_json(value: object, *, label: str) -> JsonValue:
    """Copy finite JSON into immutable built-in-compatible containers.

    Boolean values are checked before integers because ``bool`` is an ``int``
    subclass.  Mappings require string keys, sequences become tuples, and no
    arbitrary object or callable can enter a persisted model value.
    """

    if value is None or isinstance(value, (bool, str)):
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if isfinite(value):
            return value
        raise RutterDefinitionError(f"{label} contains a non-finite number")
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, label=f"{label}[{index}]")
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise RutterDefinitionError(f"{label} contains a non-string key")
            frozen[key] = _freeze_json(item, label=f"{label}.{key}")
        return MappingProxyType(frozen)
    raise RutterDefinitionError(f"{label} is not finite JSON")


def _freeze_json_mapping(value: object, *, label: str) -> Mapping[str, JsonValue]:
    """Return an immutable finite-JSON object rather than any other JSON value."""

    frozen = _freeze_json(value, label=label)
    if not isinstance(frozen, Mapping):
        raise RutterDefinitionError(f"{label} must be a JSON object")
    return frozen


def _require_identifier(value: object, *, label: str) -> str:
    """Require one nonempty lowercase kebab-case identity."""

    if not isinstance(value, str) or _STATE_ID.fullmatch(value) is None:
        raise RutterDefinitionError(
            f"{label} must be a non-empty lowercase kebab-case state ID"
        )
    return value


@dataclass(frozen=True)
class Charter:
    """Carry the immutable initial data for one Rutter undertaking."""

    rutter_id: str
    definition_version: int
    data: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        """Validate identity and detach all undertaking data from its caller."""

        _require_identifier(self.rutter_id, label="rutter ID")
        if type(self.definition_version) is not int or self.definition_version < 1:
            raise RutterDefinitionError(
                "Charter definition_version must be a positive integer"
            )
        object.__setattr__(
            self,
            "data",
            _freeze_json_mapping(self.data, label="Charter data"),
        )


@dataclass(frozen=True)
class EffectPolicy:
    """Declare whether an interrupted callable effect may run again safely."""

    repeat_safe: bool

    def __post_init__(self) -> None:
        """Reject truthy substitutes so persisted policy remains exact."""

        if type(self.repeat_safe) is not bool:
            raise RutterDefinitionError("effect repeat_safe must be a boolean")


@dataclass(frozen=True)
class _EffectRecovery:
    """Persist framework authority for one planned, completed, or uncertain effect."""

    state_id: str
    revision: int
    disposition: str
    repeat_safe: bool

    def __post_init__(self) -> None:
        """Keep effect recovery data finite, exact, and independent of callables."""

        try:
            _require_identifier(self.state_id, label="effect state ID")
        except RutterDefinitionError as error:
            raise RutterStateError(str(error)) from error
        if type(self.revision) is not int or self.revision < 0:
            raise RutterStateError("effect revision must be a non-negative integer")
        if self.disposition not in {"planned", "completed", "uncertain"}:
            raise RutterStateError("effect disposition is invalid")
        if type(self.repeat_safe) is not bool:
            raise RutterStateError("effect repeat_safe must be a boolean")


@dataclass(frozen=True)
class Fix:
    """Carry only the machine coordinate and framework recovery diagnostics."""

    current_state_id: str
    revision: int
    lifecycle: str
    effect: _EffectRecovery | None = None
    diagnostics: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        """Reject malformed persisted coordinates before graph interpretation."""

        try:
            _require_identifier(self.current_state_id, label="Fix state ID")
        except RutterDefinitionError as error:
            raise RutterStateError(str(error)) from error
        if type(self.revision) is not int or self.revision < 0:
            raise RutterStateError("Fix revision must be a non-negative integer")
        if self.lifecycle not in {"active", "complete", "faulted"}:
            raise RutterStateError("Fix lifecycle is invalid")
        if self.effect is not None and not isinstance(self.effect, _EffectRecovery):
            raise RutterStateError("Fix effect must be framework recovery data or None")
        if not isinstance(self.diagnostics, tuple) or not all(
            isinstance(issue, ValidationIssue) for issue in self.diagnostics
        ):
            raise RutterStateError("Fix diagnostics must be ValidationIssue values")


@dataclass(frozen=True)
class Reckoning:
    """Durably pair one immutable Charter with its current Fix."""

    storage_version: int
    charter: Charter
    fix: Fix

    def __post_init__(self) -> None:
        """Require the current exact storage shape and typed components."""

        if (
            type(self.storage_version) is not int
            or self.storage_version != _STORAGE_VERSION
        ):
            raise RutterStateError(
                f"Reckoning storage_version must be {_STORAGE_VERSION}"
            )
        if not isinstance(self.charter, Charter):
            raise RutterStateError("Reckoning charter must be a Charter")
        if not isinstance(self.fix, Fix):
            raise RutterStateError("Reckoning fix must be a Fix")


@dataclass(frozen=True)
class State:
    """Define one nonterminal instruction, validation, and direct successor rule."""

    instruction: Instruction
    input_validator: InputValidator
    next_state: NextState
    description: str = ""
    effect_policy: EffectPolicy | None = None

    def __post_init__(self) -> None:
        """Validate callable shape without invoking any author-owned function."""

        is_string = isinstance(self.instruction, str)
        if is_string:
            if not self.instruction.strip():
                raise RutterDefinitionError("string instruction must be non-empty")
        elif not callable(self.instruction):
            raise RutterDefinitionError("instruction must be a string or callable")
        if not callable(self.input_validator):
            raise RutterDefinitionError("input_validator must be callable")
        if not callable(self.next_state):
            raise RutterDefinitionError("next_state must be callable")
        if not isinstance(self.description, str):
            raise RutterDefinitionError("state description must be a string")
        if self.effect_policy is not None and not isinstance(
            self.effect_policy, EffectPolicy
        ):
            raise RutterDefinitionError("effect_policy must be EffectPolicy or None")
        if is_string and self.effect_policy is not None:
            raise RutterDefinitionError(
                "effect_policy is valid only for a callable instruction"
            )


@dataclass(frozen=True)
class TerminalState:
    """Mark one terminal graph entry without partially populating a State."""

    description: str = ""

    def __post_init__(self) -> None:
        """Require readable metadata to remain plain serializable text."""

        if not isinstance(self.description, str):
            raise RutterDefinitionError("terminal description must be a string")


def __getattr__(name: str) -> object:
    """Keep the Task 1 author import path while engine owns implementation."""

    if name == "BaseRutter":
        from officina.rutter.engine import BaseRutter

        return BaseRutter
    raise AttributeError(name)
