"""Define the private execution model for controller-driven agents.

Role
----
This module is the deterministic half of a controller-driven skill.  Such a
skill is split into two cooperating parts:

* a Python controller that owns workflow state, evidence acceptance, and route
  selection; and
* an LLM-facing wrapper that performs requested work and reports observations.

The split replaces a monolithic instruction file with an executable workflow
definition and a comparatively thin natural-language adapter.  It is intended
to work behind different agent hosts without putting host-specific concepts
into the controller graph.

Authority boundary
------------------
The controller is authoritative for progress.  It decides which state a run is
in, which instructions are currently valid, whether a result satisfies an
instruction, and which state follows a resolved outcome.  The attached agent
may execute an instruction and return structured evidence, but it must not
choose a transition, declare the run complete, or reconstruct progress from
its conversation history.

A controller governs only its attached agent.  It may ask that agent to
delegate work, review several subagent reports, or wait for another actor, but
it does not reach through the agent to control those actors.  A subagent that
needs deterministic workflow control is launched as another agent-controller
pair with its own graph and snapshot.  Communication between the two pairs is
ordinary agent-to-agent evidence from each controller's perspective.

Graph model
-----------
The graph deliberately has no behavioral ``Edge`` class.  Each
:class:`ControllerState` owns at most one :class:`Transition`, and that
transition owns both:

* the checks or evidence needed to leave the state; and
* a mapping from every supported outcome to its destination state.

The entries in ``Transition.destinations`` are therefore the graph edges.  A
single transition can resolve a genuinely multi-valued decision without
duplicating nearly identical condition objects for each outgoing branch.
Terminal states have a ``terminal_outcome`` and no transition.

During evaluation, a transition returns exactly one of three values:

* :class:`Await` supplies the complete ordered plan of logical instructions
  still desired in the current state;
* :class:`Route` supplies a resolved outcome plus an optional deterministic
  context patch; or
* :class:`Fault` explains why evaluation cannot continue safely.

The transition resolves the meaning of accumulated evidence.  The controller
engine remains responsible for checking that a routed outcome exists in the
destination mapping and for entering the target state.

Run model and lifecycle
-----------------------
Controller definitions are intended to be immutable and reusable.  Mutable
progress belongs to a :class:`ControllerSnapshot`, not to a controller object.
The snapshot contains the run and definition identities, current state and
state-entry epoch, optimistic revision, skill-specific JSON context, and a
durable instruction ledger.

A normal interaction follows this sequence:

1. ``BaseController.start`` creates a snapshot at ``initial_state_id`` and
   evaluates until the run is waiting, terminal, or faulted.
2. If evidence is needed, the transition returns an ordered ``Await`` plan.
   Logical :class:`InstructionSpec` values are materialized as run-specific
   :class:`CheckInstruction` values.
3. The wrapper executes only the first currently executable instruction and
   returns one :class:`CheckResult`.
4. ``BaseController.submit`` validates and records that result, re-evaluates
   the transition, and returns a new authoritative :class:`ControllerTurn`.
5. Re-evaluation may retain the rest of the prior plan, cancel or replace some
   instructions, request new evidence, route through one or more states, or
   finish the run.

``state_epoch`` distinguishes separate visits to the same logical state;
``revision`` supports optimistic concurrency; and run-specific instruction
IDs prevent delayed evidence from satisfying work issued during another visit.
The ledger preserves completed, cancelled, and replaced instructions so that
resume and replay logic need not infer history from the pending plan.

Module boundary
---------------
The objects here are controller-internal contracts.  They may be persisted by
an implementation, but they are not the public transport format exposed to an
LLM wrapper.  The wire-facing request and response vocabulary lives in
``officina.controller.protocol`` and intentionally reveals only the
information required to execute or inspect a turn.

Implementation status
---------------------
This module currently defines the reusable object-oriented contracts only.
``Transition.evaluate`` and the controller-definition properties are subclass
extension points.  ``BaseController.start`` and ``BaseController.submit`` are
explicit stubs for the future shared engine.  Graph validation, persistence,
transactions, replay receipts, bounded automatic routing, host adapters, and
subagent launch mechanisms are intentionally outside this initial slice.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, TypeAlias


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class InstructionAction(str, Enum):
    """Name a stable wrapper interaction pattern.

    Intent
    ------
    Give controllers a small host-neutral vocabulary for how the attached agent
    should carry out an instruction.

    Rationale
    ---------
    Task-specific meaning belongs in the instruction message and payload, while
    the wrapper needs a stable pattern that does not depend on any agent host.

    Pseudocode
    ----------
    - set action = one supported interaction pattern

    Wraps
    -----
    - none
    """

    WORK = "work"
    DELEGATE = "delegate"
    REVIEW = "review"
    RUN = "run"
    REQUEST_INPUT = "request_input"
    WAIT = "wait"


class ControllerStatus(str, Enum):
    """Describe controller lifecycle after one evaluated turn.

    Intent
    ------
    Tell the wrapper whether internal routing continues, agent evidence is
    required, a domain terminal state was reached, or execution faulted.

    Rationale
    ---------
    Lifecycle status is distinct from a skill-specific terminal outcome such as
    complete or blocked.

    Pseudocode
    ----------
    - set status = lifecycle classification for the current turn

    Wraps
    -----
    - none
    """

    RUNNING = "running"
    WAITING = "waiting"
    TERMINAL = "terminal"
    FAULTED = "faulted"


class InstructionStatus(str, Enum):
    """Describe the durable lifecycle of one issued instruction.

    Intent
    ------
    Preserve whether issued work remains valid, completed, cancelled, or was
    superseded by a revised controller plan.

    Rationale
    ---------
    Explicit lifecycle records let resumed runs reject stale results instead of
    reconstructing history from the current desired instruction list.

    Pseudocode
    ----------
    - set status = latest controller-owned instruction disposition

    Wraps
    -----
    - none
    """

    ISSUED = "issued"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REPLACED = "replaced"


class ControllerError(Exception):
    """Identify controller definition, protocol, and runtime failures.

    Intent
    ------
    Give callers one exception family for bounded controller failures.

    Rationale
    ---------
    Callers may catch the family while diagnostics and tests distinguish the
    narrower failure classes.

    Pseudocode
    ----------
    - set error = supplied controller failure diagnostic

    Wraps
    -----
    - none
    """


class ControllerDefinitionError(ControllerError):
    """Report an inconsistent immutable controller graph.

    Intent
    ------
    Reject malformed states, destinations, outcomes, or terminal declarations
    before a controller run starts.

    Rationale
    ---------
    Definition failures are authoring errors rather than wrapper protocol or
    persisted-run failures.

    Pseudocode
    ----------
    - set error = supplied graph-definition failure diagnostic

    Wraps
    -----
    - none
    """


class ControllerProtocolError(ControllerError):
    """Report wrapper input that violates the exchange contract.

    Intent
    ------
    Reject malformed, unknown, stale, duplicate-conflicting, or out-of-order
    result submissions.

    Rationale
    ---------
    Transport failures must not be confused with invalid controller graphs or
    valid runs that reach a domain fault.

    Pseudocode
    ----------
    - set error = supplied wrapper-protocol failure diagnostic

    Wraps
    -----
    - none
    """


class ControllerStateError(ControllerError):
    """Report a persisted run that cannot advance safely.

    Intent
    ------
    Identify incompatible, corrupt, or internally ambiguous controller state.

    Rationale
    ---------
    Runtime-state failures require different recovery from authoring errors and
    invalid wrapper messages.

    Pseudocode
    ----------
    - set error = supplied execution-state failure diagnostic

    Wraps
    -----
    - none
    """


@dataclass(frozen=True)
class InstructionSpec:
    """Describe one transition-owned logical evidence request.

    Intent
    ------
    Declare the stable key, interaction pattern, task-specific message, allowed
    outcomes, and structured payload for work the attached agent may perform.

    Rationale
    ---------
    The stable key lets the controller reconcile a newly desired plan with work
    already issued in the current state epoch without binding the definition to
    a particular run-specific instruction ID.

    Pseudocode
    ----------
    - set specification = supplied logical instruction fields

    Wraps
    -----
    - none
    """

    key: str
    action: InstructionAction
    message: str
    allowed_outcomes: tuple[str, ...]
    payload: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckInstruction:
    """Materialize an instruction specification for one run and state entry.

    Intent
    ------
    Bind logical work to an unambiguous run, state, state epoch, and ordered
    position before exposing it to the wrapper.

    Rationale
    ---------
    Run-specific identity prevents results from earlier loops or retries from
    satisfying instructions issued during a later visit to the same state.

    Pseudocode
    ----------
    - set instruction = supplied specification plus run correlation fields

    Wraps
    -----
    - none
    """

    instruction_id: str
    run_id: str
    state_id: str
    state_epoch: int
    ordinal: int
    spec: InstructionSpec


@dataclass(frozen=True)
class CheckResult:
    """Carry one structured wrapper response to an issued instruction.

    Intent
    ------
    Return a declared outcome and supporting JSON-compatible evidence under the
    exact identifier of the instruction being answered.

    Rationale
    ---------
    Structured evidence supports multi-valued branching and auditability while
    leaving route selection with the controller.

    Pseudocode
    ----------
    - set result = supplied instruction ID outcome and evidence

    Wraps
    -----
    - none
    """

    instruction_id: str
    outcome: str
    evidence: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class InstructionRecord:
    """Record one instruction's durable lifecycle and accepted result.

    Intent
    ------
    Preserve issued work and the controller-owned disposition applied to it.

    Rationale
    ---------
    A ledger distinguishes completed, cancelled, and replaced work across
    restarts and plan revisions instead of inferring history from pending work.

    Pseudocode
    ----------
    - set record = supplied instruction status and optional result

    Wraps
    -----
    - none
    """

    instruction: CheckInstruction
    status: InstructionStatus
    result: CheckResult | None = None


@dataclass(frozen=True)
class ControllerSnapshot:
    """Carry the complete execution state of one controller run.

    Intent
    ------
    Persist run identity, definition compatibility, current state and epoch,
    instruction history, skill context, and optimistic revision together.

    Rationale
    ---------
    Controller definitions are reusable and immutable, so every independent run
    requires a self-contained snapshot that can be resumed without mutable
    process-local controller state.

    Pseudocode
    ----------
    - set snapshot = supplied run state ledger context and revision

    Wraps
    -----
    - none
    """

    run_id: str
    controller_id: str
    definition_version: str
    current_state_id: str
    state_epoch: int
    instruction_ledger: tuple[InstructionRecord, ...]
    context: Mapping[str, JsonValue] = field(default_factory=dict)
    revision: int = 0


@dataclass(frozen=True)
class Await:
    """Request additional evidence before a transition can resolve.

    Intent
    ------
    Return the complete desired logical instruction plan for the current state.

    Rationale
    ---------
    An explicit result separates incomplete evidence from unsatisfied checks or
    invalid routing.

    Pseudocode
    ----------
    - set evaluation = supplied ordered desired instruction specifications

    Wraps
    -----
    - none
    """

    desired: tuple[InstructionSpec, ...]


@dataclass(frozen=True)
class Route:
    """Return a resolved outcome and deterministic context patch.

    Intent
    ------
    Tell the controller which outcome key to map to a destination and which
    internal JSON-compatible context changes accompany that decision.

    Rationale
    ---------
    The transition resolves meaning, while the controller alone validates the
    destination map and enters the target state.

    Pseudocode
    ----------
    - set evaluation = supplied outcome and optional context patch

    Wraps
    -----
    - none
    """

    outcome: str
    context_patch: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Fault:
    """Report a transition evaluation that cannot continue safely.

    Intent
    ------
    Return a stable fault code and diagnostic when neither waiting nor routing
    is valid.

    Rationale
    ---------
    A fault must remain distinct from missing evidence and domain outcomes such
    as blocked or revision required.

    Pseudocode
    ----------
    - set evaluation = supplied fault code and detail

    Wraps
    -----
    - none
    """

    code: str
    detail: str


TransitionEvaluation: TypeAlias = Await | Route | Fault


class Transition(ABC):
    """Resolve one state's complete outbound branching decision.

    Intent
    ------
    Own the evidence requests and outcome-to-destination mapping needed to leave
    one controller state.

    Rationale
    ---------
    Modeling the whole branch avoids repeating variations of one multi-valued
    check across separate edge objects. Destination-map entries remain graph
    edges without acquiring behavior of their own.

    Pseudocode
    ----------
    - set evidence = accepted results from the current state epoch
    - return desired instructions one route outcome or one fault from evidence

    Wraps
    -----
    - none
    """

    @property
    @abstractmethod
    def destinations(self) -> Mapping[str, str]:
        """Expose the complete outcome-to-state mapping.

        Intent
        ------
        Let the controller validate and apply every possible resolved outcome.

        Rationale
        ---------
        Route ownership stays with the transition while state entry remains a
        controller operation.

        Pseudocode
        ----------
        - return each supported outcome key paired with one target state ID

        Wraps
        -----
        - none
        """

    @abstractmethod
    def evaluate(self, snapshot: ControllerSnapshot) -> TransitionEvaluation:
        """Evaluate accumulated evidence without mutating the snapshot.

        Intent
        ------
        Produce exactly one desired-plan, resolved-route, or fault result.

        Rationale
        ---------
        One pure operation prevents separately computed instructions and route
        resolution from disagreeing.

        Pseudocode
        ----------
        - set evidence = current state epoch context and accepted results
        - return await route or fault

        Wraps
        -----
        - none
        """


@dataclass(frozen=True)
class ControllerState:
    """Define one state and its complete outbound transition policy.

    Intent
    ------
    Pair a stable state identifier with either one branching transition or one
    terminal domain outcome.

    Rationale
    ---------
    Each state owns one coherent outbound decision while terminal states own no
    further transition behavior.

    Pseudocode
    ----------
    - set state = supplied state ID transition and terminal outcome

    Wraps
    -----
    - none
    """

    state_id: str
    transition: Transition | None
    terminal_outcome: str | None = None


@dataclass(frozen=True)
class ControllerTurn:
    """Return the authoritative wrapper-facing state after evaluation.

    Intent
    ------
    Provide the updated snapshot, lifecycle status, ordered instruction plan,
    and instructions invalidated by replanning.

    Rationale
    ---------
    The wrapper executes only the first instruction, submits one result, then
    discards this turn in favor of the newly returned authoritative turn. The
    remaining list exposes the plan without authorizing eager execution.

    Pseudocode
    ----------
    - set turn = supplied snapshot status instructions and cancellations

    Wraps
    -----
    - none
    """

    snapshot: ControllerSnapshot
    status: ControllerStatus
    instructions: tuple[CheckInstruction, ...]
    cancelled_instruction_ids: tuple[str, ...] = ()


class BaseController(ABC):
    """Define the stateless engine contract for skill controllers.

    Intent
    ------
    Centralize graph validation, instruction reconciliation, result acceptance,
    bounded automatic routing, and snapshot revision while subclasses supply
    only immutable states and transitions.

    Rationale
    ---------
    One engine keeps lifecycle semantics consistent across skills and leaves
    each run's mutable progress in an explicit snapshot rather than the reusable
    controller object.

    Pseudocode
    ----------
    - set graph = immutable controller graph supplied by the subclass
    - return a started or advanced run from shared lifecycle rules

    Wraps
    -----
    - none
    """

    @property
    @abstractmethod
    def controller_id(self) -> str:
        """Return the stable controller identifier.

        Intent
        ------
        Correlate snapshots with the controller definition that can interpret them.

        Rationale
        ---------
        Run IDs distinguish executions; controller IDs distinguish definitions.

        Pseudocode
        ----------
        - return the subclass-owned controller identifier

        Wraps
        -----
        - none
        """

    @property
    @abstractmethod
    def definition_version(self) -> str:
        """Return the controller-definition compatibility version.

        Intent
        ------
        Bind persisted snapshots to the graph and lifecycle contract that owns them.

        Rationale
        ---------
        Resumption must fail closed when a changed definition cannot safely
        interpret an older snapshot.

        Pseudocode
        ----------
        - return the subclass-owned definition version

        Wraps
        -----
        - none
        """

    @property
    @abstractmethod
    def initial_state_id(self) -> str:
        """Return the state entered by a new run.

        Intent
        ------
        Identify the unique starting node in the controller graph.

        Rationale
        ---------
        The controller engine must not infer an initial state from mapping order.

        Pseudocode
        ----------
        - return the subclass-owned initial state ID

        Wraps
        -----
        - none
        """

    @property
    @abstractmethod
    def states(self) -> Mapping[str, ControllerState]:
        """Return the complete state graph keyed by state ID.

        Intent
        ------
        Expose every state and outbound transition to shared graph validation.

        Rationale
        ---------
        One declared mapping is the controller's canonical flowchart authority.

        Pseudocode
        ----------
        - return every subclass-owned controller state by its stable ID

        Wraps
        -----
        - none
        """

    def start(
        self,
        *,
        run_id: str,
        context: Mapping[str, JsonValue] | None = None,
    ) -> ControllerTurn:
        """Create and evaluate a new controller run.

        Intent
        ------
        Validate the immutable graph, enter the initial state, and return its first
        authoritative wrapper-facing turn.

        Rationale
        ---------
        All skills must initialize run identity, state epoch, ledger, context, and
        revision through the same lifecycle boundary.

        Pseudocode
        ----------
        - set graph = validated controller graph
        - set snapshot = initial run state from validated inputs
        - set turn = evaluation until waiting terminal or faulted
        - return the first controller turn

        Wraps
        -----
        - none
        """

        raise NotImplementedError("controller start is not implemented")

    def submit(
        self,
        snapshot: ControllerSnapshot,
        result: CheckResult,
        *,
        expected_revision: int,
    ) -> ControllerTurn:
        """Accept one result and evaluate the next controller turn.

        Intent
        ------
        Validate one wrapper response, record it idempotently, re-evaluate the
        current transition, and return the updated authoritative turn.

        Rationale
        ---------
        Single-result submission lets the controller revise pending work after
        every observation without giving the wrapper authority over progress.

        Pseudocode
        ----------
        - set validated_result = result checked against snapshot and revision
        - set updated_snapshot = accepted result recorded without input mutation
        - set turn = reconciled instructions or bounded state routing
        - return the next controller turn

        Wraps
        -----
        - none
        """

        raise NotImplementedError("controller result submission is not implemented")
