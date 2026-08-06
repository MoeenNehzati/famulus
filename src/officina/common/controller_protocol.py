"""Define the wire boundary between an agent wrapper and its controller.

Role
----
``officina.common.controller`` defines the controller's private graph,
snapshot, ledger, and transition-evaluation objects.  This module defines the
smaller protocol that may cross a process boundary or be translated by any
host adapter.  Wrappers should depend on these wire messages rather than
inspecting a :class:`~officina.common.controller.ControllerSnapshot` or
invoking a transition directly.

The protocol is transport-neutral.  It can be carried over a one-shot command
that reads one JSON request from standard input and writes one JSON response to
standard output, a long-lived local process, or another request-response
transport.  Transport choice must not change the meaning of the messages.

Authority and information boundary
----------------------------------
The wrapper is an executor, not a workflow engine.  A
:class:`TurnResponse` is authoritative about the current public state, ordered
instruction plan, cancellations, and the one instruction the wrapper may act
on.  The wrapper returns an :class:`AgentResult` describing the observed
outcome and evidence; it never names a destination state.  After every result,
the wrapper discards its previous authority and follows the newly returned
turn.

Only public summaries cross this boundary.  :class:`StateSummary` exposes a
state ID and state-entry epoch, while the controller retains its full context,
ledger, transition objects, and persistence representation.  This separation
allows those internal structures to evolve without making every host adapter a
controller implementation.

Message families
----------------
Requests flow from the wrapper to the controller:

* :class:`StartRequest` creates a new run from controller-specific input;
* :class:`SubmitRequest` reports one result under the current turn token;
* :class:`ResumeRequest` recovers the current authoritative turn after restart
  or an uncertain transport outcome; and
* :class:`InspectRequest` reads progress without granting execution authority.

Responses flow from the controller to the wrapper:

* :class:`TurnResponse` carries an executable, terminal, or faulted turn;
* :class:`InspectionResponse` carries a deliberately non-executable view; and
* :class:`ErrorResponse` reports protocol or adapter failure with explicit
  retry and resume guidance.

The supporting :class:`AgentInstruction`, :class:`AgentResult`,
:class:`StateSummary`, :class:`WireFault`, and :class:`WireError` values are
wire projections, not aliases for the richer private controller objects.

Correlation, replay, and concurrency
------------------------------------
``request_id`` identifies one idempotent transport operation; ``run_id``
identifies one controller execution; ``turn_token`` authorizes submission
against the currently executable turn; and ``revision`` identifies the
committed snapshot version.  These values serve different purposes and should
not be collapsed into one identifier.

A controller implementation is expected to return the previously committed
response when an identical request is replayed and to mark such a
``TurnResponse`` with ``replayed=True``.  A stale or conflicting token is a
protocol error, not permission for the wrapper to guess which instruction is
still active.  When delivery is uncertain, the safe recovery operation is
``ResumeRequest``.

Subagent boundary
-----------------
The protocol connects exactly one wrapper to exactly one controller.  An
``AgentInstruction`` may tell the attached agent to delegate or communicate
with a subagent, but this controller protocol does not address that subagent
directly.  If the child needs controlled progress, it uses a separate instance
of this same wrapper-controller protocol with its own controller and run IDs.

JSON contract
-------------
Every top-level message derives from :class:`ControllerMessage`.  The base
class centrally owns strict parsing and deterministic serialization:

* the root value must be a JSON object;
* duplicate object keys and non-finite numbers are rejected;
* ``message_type`` must be a non-empty registered discriminator;
* ``protocol_version`` must equal :data:`PROTOCOL_VERSION`;
* keys are sorted and compact separators are used for stable output; and
* Unicode is preserved rather than escaped unnecessarily.

Calling ``ConcreteMessage.from_json`` requires the matching discriminator.
Calling ``ControllerMessage.from_json`` dispatches through the closed
``_MESSAGE_TYPES`` registry.  All concrete classes must convert nested enums,
tuples, and dataclasses through ``to_mapping`` and ``from_mapping``; they must
not introduce independent JSON parsing rules.

Implementation status
---------------------
The common JSON envelope is implemented and tested.  Concrete production
message classes currently inherit explicit ``to_mapping`` and ``from_mapping``
stubs from :class:`ControllerMessage`; their field-level conversion and
validation will be implemented incrementally.  Persistence, transaction
boundaries, request receipt storage, CLI framing, and host-specific adapters
remain outside this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import ClassVar, Mapping, Self

from .controller import ControllerStatus, InstructionAction, JsonValue


PROTOCOL_VERSION = 1


class ControllerMessageError(Exception):
    """Identify failures while encoding or decoding controller messages.

    Intent
    ------
    Give transports one exception family for all wire-message failures.

    Rationale
    ---------
    The protocol layer should not leak JSON-library exceptions or confuse wire
    failures with controller-domain failures.

    Pseudocode
    ----------
    - set error = supplied wire-message diagnostic

    Wraps
    -----
    - none
    """


class InvalidControllerMessageError(ControllerMessageError):
    """Report malformed JSON or an invalid common message envelope.

    Intent
    ------
    Reject messages that cannot safely be interpreted as protocol values.

    Rationale
    ---------
    Duplicate keys, non-object roots, non-finite numbers, and contradictory
    type declarations would otherwise make interpretation host-dependent.

    Pseudocode
    ----------
    - set error = supplied invalid-message diagnostic

    Wraps
    -----
    - none
    """


class UnsupportedProtocolVersionError(ControllerMessageError):
    """Report a message using an unsupported protocol version.

    Intent
    ------
    Stop incompatible peers before concrete payload conversion begins.

    Rationale
    ---------
    Explicit version rejection is safer than accidentally interpreting a new
    contract according to old field semantics.

    Pseudocode
    ----------
    - set error = supplied unsupported-version diagnostic

    Wraps
    -----
    - none
    """


class UnknownMessageTypeError(ControllerMessageError):
    """Report an unregistered message discriminator.

    Intent
    ------
    Reject base-class dispatch when no declared message class owns the type.

    Rationale
    ---------
    A closed registry prevents arbitrary class construction from untrusted
    input and makes protocol evolution explicit.

    Pseudocode
    ----------
    - set error = supplied unknown-type diagnostic

    Wraps
    -----
    - none
    """


def _reject_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    """Reject duplicate object members while constructing a decoded mapping.

    Intent
    ------
    Make every JSON object unambiguous before protocol interpretation.

    Rationale
    ---------
    JSON decoders otherwise retain one duplicate value according to library
    policy, which can make validation disagree with what another peer saw.

    Pseudocode
    ----------
    - set result = mapping containing each unique pair
    - return unique-key mapping

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .InvalidControllerMessageError:
      why:
        constructs: "Reports the first duplicate object member."
    """
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidControllerMessageError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> None:
    """Reject a non-standard non-finite numeric JSON token.

    Intent
    ------
    Prevent NaN and infinity values from crossing the controller boundary.

    Rationale
    ---------
    Non-finite constants are not standard JSON and have inconsistent language
    and equality behavior.

    Pseudocode
    ----------
    - return invalid-message error for value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .InvalidControllerMessageError:
      why:
        constructs: "Reports the prohibited non-finite token."
    """
    raise InvalidControllerMessageError(f"non-finite JSON number: {value}")


def _decode_json_object(text: str) -> dict[str, JsonValue]:
    """Decode one strict JSON object for common-envelope validation.

    Intent
    ------
    Normalize JSON decoding failures and prohibit ambiguous object shapes.

    Rationale
    ---------
    All message classes need identical root, duplicate-key, and numeric rules.

    Pseudocode
    ----------
    - set value = strict JSON decoding of text
    - if value is not a mapping:
      - raise InvalidControllerMessageError
    - return decoded mapping

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .InvalidControllerMessageError:
      why:
        constructs: "Normalizes decoding and root-shape failures."
    """
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except InvalidControllerMessageError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise InvalidControllerMessageError(f"invalid controller JSON: {error}") from error

    if not isinstance(value, dict):
        raise InvalidControllerMessageError("controller message JSON must be an object")
    return value


class ControllerMessage:
    """Provide the shared JSON contract for every protocol message.

    Intent
    ------
    Centralize deterministic serialization, strict parsing, envelope checks,
    and discriminator-based dispatch for controller messages.

    Rationale
    ---------
    Concrete messages should define only their Python-to-mapping conversion;
    they should not independently choose JSON behavior or validation policy.

    Pseudocode
    ----------
    - set mapping = concrete message mapping
    - set envelope = validated common message fields
    - return canonical JSON or converted message

    Wraps
    -----
    - none
    """

    message_type: ClassVar[str]
    protocol_version: int

    def to_mapping(self) -> Mapping[str, JsonValue]:
        """Convert one concrete message to its JSON-compatible mapping.

        Intent
        ------
        Define the extension point used by the shared JSON encoder.

        Rationale
        ---------
        Concrete field conversion is intentionally separate from common JSON
        policy and remains unimplemented for the declared production messages.

        Pseudocode
        ----------
        - return concrete message mapping

        Wraps
        -----
        - none
        """
        raise NotImplementedError

    @classmethod
    def from_mapping(cls, value: Mapping[str, JsonValue]) -> Self:
        """Construct one concrete message from a validated mapping.

        Intent
        ------
        Define the extension point used after common envelope validation.

        Rationale
        ---------
        Each concrete message owns its required fields and nested value
        conversion while the superclass owns parsing and dispatch.

        Pseudocode
        ----------
        - return concrete message built from value

        Wraps
        -----
        - none
        """
        raise NotImplementedError

    def to_json(self) -> str:
        """Serialize this message as deterministic strict JSON.

        Intent
        ------
        Produce a stable wire representation suitable for logs, receipts, and
        host-independent transport.

        Rationale
        ---------
        Sorted keys, compact separators, preserved Unicode, and finite numbers
        remove avoidable differences between wrappers.

        Pseudocode
        ----------
        - set mapping = result of to_mapping
        - set envelope = validated common mapping fields
        - return canonical JSON encoding

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._validate_envelope:
          why:
            validates: "Checks the discriminator and supported protocol version."

        InstantiationsFromRepo
        ----------------------
        .InvalidControllerMessageError:
          why:
            constructs: "Normalizes JSON encoding failures."
        """
        mapping = dict(self.to_mapping())
        _validate_envelope(mapping, expected_type=self.message_type)
        try:
            return json.dumps(
                mapping,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as error:
            raise InvalidControllerMessageError(
                f"message contains a non-JSON value: {error}"
            ) from error

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Parse strict JSON and construct or dispatch a controller message.

        Intent
        ------
        Apply identical parsing and envelope checks before concrete payload
        conversion on every host.

        Rationale
        ---------
        Calling a concrete subclass requires its discriminator, while calling
        the base class selects a declared message class from a closed registry.

        Pseudocode
        ----------
        - set mapping = decoded strict JSON object
        - set envelope = validated common mapping fields
        - set selected_class = matching registered message class
        - return selected class result from from_mapping

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._validate_envelope:
          why:
            validates: "Checks the discriminator and supported protocol version."

        InstantiationsFromRepo
        ----------------------
        ._decode_json_object:
          why:
            constructs: "Builds the strict mapping supplied to envelope validation."
        .UnknownMessageTypeError:
          why:
            constructs: "Reports an unregistered base-dispatch discriminator."
        """
        mapping = _decode_json_object(text)
        expected_type = None if cls is ControllerMessage else cls.message_type
        _validate_envelope(mapping, expected_type=expected_type)

        message_class: type[ControllerMessage]
        if cls is ControllerMessage:
            message_type = mapping["message_type"]
            assert isinstance(message_type, str)
            try:
                message_class = _MESSAGE_TYPES[message_type]
            except KeyError as error:
                raise UnknownMessageTypeError(
                    f"unknown controller message_type: {message_type!r}"
                ) from error
        else:
            message_class = cls

        return message_class.from_mapping(mapping)  # type: ignore[return-value]


def _validate_envelope(
    value: Mapping[str, JsonValue], *, expected_type: str | None
) -> None:
    """Validate fields shared by every controller protocol message.

    Intent
    ------
    Require a non-empty discriminator and the one supported protocol version.

    Rationale
    ---------
    Common validation must finish before any class interprets payload fields.

    Pseudocode
    ----------
    - set message_type = value["message_type"]
    - if message_type is invalid:
      - raise InvalidControllerMessageError
    - if expected_type differs from message_type:
      - raise InvalidControllerMessageError
    - set protocol_version = value["protocol_version"]
    - if protocol_version is invalid:
      - raise InvalidControllerMessageError
    - if protocol_version is unsupported:
      - raise UnsupportedProtocolVersionError
    - return none

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .InvalidControllerMessageError:
      why:
        constructs: "Reports invalid common envelope fields."
    .UnsupportedProtocolVersionError:
      why:
        constructs: "Reports a well-formed but unsupported version."
    """
    message_type = value.get("message_type")
    if not isinstance(message_type, str) or not message_type:
        raise InvalidControllerMessageError("message_type must be a non-empty string")
    if expected_type is not None and message_type != expected_type:
        raise InvalidControllerMessageError(
            f"message_type must be {expected_type!r}, got {message_type!r}"
        )

    protocol_version = value.get("protocol_version")
    if isinstance(protocol_version, bool) or not isinstance(protocol_version, int):
        raise InvalidControllerMessageError("protocol_version must be an integer")
    if protocol_version != PROTOCOL_VERSION:
        raise UnsupportedProtocolVersionError(
            f"unsupported protocol_version: {protocol_version}"
        )


class ControllerRequest(ControllerMessage):
    """Group messages sent from an LLM-facing wrapper to a controller.

    Intent
    ------
    Mark the direction of commands that start, advance, resume, or inspect runs.

    Rationale
    ---------
    A directional base type lets adapters constrain APIs without coupling them
    to every concrete request class.

    Pseudocode
    ----------
    - set request = one declared wrapper-to-controller message

    Wraps
    -----
    - none
    """


class ControllerResponse(ControllerMessage):
    """Group messages sent from a controller to an LLM-facing wrapper.

    Intent
    ------
    Mark the direction of turns, inspections, and protocol errors.

    Rationale
    ---------
    A directional base type lets adapters constrain return values without
    coupling them to every concrete response class.

    Pseudocode
    ----------
    - set response = one declared controller-to-wrapper message

    Wraps
    -----
    - none
    """


@dataclass(frozen=True)
class AgentInstruction:
    """Describe one ordered action exposed to the attached agent.

    Intent
    ------
    Carry a run-specific identity, stable action, human-facing message, allowed
    outcomes, payload, and optional evidence shape across the wire.

    Rationale
    ---------
    The wrapper needs executable instructions, not the controller's private
    transition, state, or check objects.

    Pseudocode
    ----------
    - set instruction = supplied wire instruction fields

    Wraps
    -----
    - none
    """

    instruction_id: str
    action: InstructionAction
    message: str
    allowed_outcomes: tuple[str, ...]
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    evidence_schema: Mapping[str, JsonValue] | None = None


@dataclass(frozen=True)
class AgentResult:
    """Carry the agent's outcome and evidence for one instruction.

    Intent
    ------
    Submit evidence against exactly one run-specific instruction identity.

    Rationale
    ---------
    The agent reports observations but does not select controller states or
    transitions.

    Pseudocode
    ----------
    - set result = supplied instruction identity, outcome, and evidence

    Wraps
    -----
    - none
    """

    instruction_id: str
    outcome: str
    evidence: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class StateSummary:
    """Expose the minimal public identity of the current controller state.

    Intent
    ------
    Let wrappers display and correlate progress without receiving a private
    controller snapshot.

    Rationale
    ---------
    State epoch distinguishes repeated entries into the same logical state.

    Pseudocode
    ----------
    - set summary = supplied state identity and entry epoch

    Wraps
    -----
    - none
    """

    state_id: str
    state_epoch: int


@dataclass(frozen=True)
class WireFault:
    """Describe a valid run that entered a controller fault state.

    Intent
    ------
    Carry a stable machine code and structured diagnostic detail.

    Rationale
    ---------
    A run fault is domain state, unlike a malformed transport message or an
    adapter exception.

    Pseudocode
    ----------
    - set fault = supplied code and detail

    Wraps
    -----
    - none
    """

    code: str
    detail: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class WireError:
    """Describe a protocol or adapter failure returned to the wrapper.

    Intent
    ------
    Tell the wrapper what failed and whether retry or explicit resume is safe.

    Rationale
    ---------
    Recovery flags prevent wrappers from guessing after stale tokens,
    malformed submissions, or uncertain transport outcomes.

    Pseudocode
    ----------
    - set error = supplied code, message, and recovery flags

    Wraps
    -----
    - none
    """

    code: str
    message: str
    retryable: bool = False
    resume_required: bool = False


@dataclass(frozen=True)
class StartRequest(ControllerRequest):
    """Request creation of a new run for one controller definition.

    Intent
    ------
    Supply an idempotency identity, controller identity, and initial input.

    Rationale
    ---------
    The controller, rather than the wrapper, creates the run and chooses its
    initial state and first executable instruction.

    Pseudocode
    ----------
    - set request = supplied start fields

    Wraps
    -----
    - none
    """

    message_type: ClassVar[str] = "start_request"
    protocol_version: int
    request_id: str
    controller_id: str
    input: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class SubmitRequest(ControllerRequest):
    """Submit one instruction result against the current controller turn.

    Intent
    ------
    Bind agent evidence to a request, run, turn token, and instruction.

    Rationale
    ---------
    Explicit concurrency identities let the controller reject stale or
    conflicting submissions and replay committed responses safely.

    Pseudocode
    ----------
    - set request = supplied submission fields

    Wraps
    -----
    - none
    """

    message_type: ClassVar[str] = "submit_request"
    protocol_version: int
    request_id: str
    controller_id: str
    run_id: str
    turn_token: str
    result: AgentResult


@dataclass(frozen=True)
class ResumeRequest(ControllerRequest):
    """Request the current executable turn for an existing run.

    Intent
    ------
    Recover after wrapper restart, lost response, or uncertain transport state.

    Rationale
    ---------
    Resume asks the controller for authoritative progress instead of inviting
    the wrapper to reconstruct it locally.

    Pseudocode
    ----------
    - set request = supplied run identity fields

    Wraps
    -----
    - none
    """

    message_type: ClassVar[str] = "resume_request"
    protocol_version: int
    request_id: str
    controller_id: str
    run_id: str


@dataclass(frozen=True)
class InspectRequest(ControllerRequest):
    """Request a non-executable summary of an existing run.

    Intent
    ------
    Read controller-owned progress without issuing or resuming agent work.

    Rationale
    ---------
    Observability should not accidentally advance the controller lifecycle.

    Pseudocode
    ----------
    - set request = supplied run identity fields

    Wraps
    -----
    - none
    """

    message_type: ClassVar[str] = "inspect_request"
    protocol_version: int
    request_id: str
    controller_id: str
    run_id: str


@dataclass(frozen=True)
class TurnResponse(ControllerResponse):
    """Return the authoritative next turn or terminal run state.

    Intent
    ------
    Expose ordered instructions, the sole executable instruction, cancellations,
    lifecycle status, public state, and replay metadata.

    Rationale
    ---------
    One response must let a thin wrapper act without inferring which queued
    work remains valid after each submitted result.

    Pseudocode
    ----------
    - set response = supplied authoritative turn fields

    Wraps
    -----
    - none
    """

    message_type: ClassVar[str] = "turn_response"
    protocol_version: int
    request_id: str
    controller_id: str
    run_id: str
    revision: int
    replayed: bool
    state: StateSummary
    status: ControllerStatus
    turn_token: str | None
    executable_instruction_id: str | None
    instructions: tuple[AgentInstruction, ...] = ()
    cancelled_instruction_ids: tuple[str, ...] = ()
    terminal_outcome: str | None = None
    fault: WireFault | None = None


@dataclass(frozen=True)
class InspectionResponse(ControllerResponse):
    """Return a read-only summary of one controller run.

    Intent
    ------
    Expose revision, public state, lifecycle status, and terminal information.

    Rationale
    ---------
    Inspection omits executable instructions and turn tokens so observation
    cannot be mistaken for authority to act.

    Pseudocode
    ----------
    - set response = supplied inspection fields

    Wraps
    -----
    - none
    """

    message_type: ClassVar[str] = "inspection_response"
    protocol_version: int
    request_id: str
    controller_id: str
    run_id: str
    revision: int
    state: StateSummary
    status: ControllerStatus
    terminal_outcome: str | None = None
    fault: WireFault | None = None


@dataclass(frozen=True)
class ErrorResponse(ControllerResponse):
    """Return a structured failure when no normal response can be produced.

    Intent
    ------
    Correlate a protocol or adapter error with its request and known run scope.

    Rationale
    ---------
    Optional controller and run identities support failures that occur before
    all envelope fields can be trusted or resolved.

    Pseudocode
    ----------
    - set response = supplied correlation fields and wire error

    Wraps
    -----
    - none
    """

    message_type: ClassVar[str] = "error_response"
    protocol_version: int
    request_id: str
    error: WireError
    controller_id: str | None = None
    run_id: str | None = None


_MESSAGE_TYPES: dict[str, type[ControllerMessage]] = {
    message_class.message_type: message_class
    for message_class in (
        StartRequest,
        SubmitRequest,
        ResumeRequest,
        InspectRequest,
        TurnResponse,
        InspectionResponse,
        ErrorResponse,
    )
}


__all__ = [
    "PROTOCOL_VERSION",
    "AgentInstruction",
    "AgentResult",
    "ControllerMessage",
    "ControllerMessageError",
    "ControllerRequest",
    "ControllerResponse",
    "ErrorResponse",
    "InspectRequest",
    "InspectionResponse",
    "InvalidControllerMessageError",
    "ResumeRequest",
    "StartRequest",
    "StateSummary",
    "SubmitRequest",
    "TurnResponse",
    "UnknownMessageTypeError",
    "UnsupportedProtocolVersionError",
    "WireError",
    "WireFault",
]
