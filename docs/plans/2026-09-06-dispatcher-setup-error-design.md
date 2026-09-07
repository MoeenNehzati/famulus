# Dispatcher and Setup Error Refactor

Status: proposed implementation contract. Scope is dispatcher and managed
setup only.

Execution is specified in the bounded
[implementation plan](./2026-09-07-dispatcher-setup-error-implementation.md).

Today, generic dispatcher errors can replace a more useful setup-manager
diagnosis, and some setup failures are reported as normal statuses. This plan
defines the inventory, compatibility rules, and smallest refactor needed to
preserve an accurate diagnosis through every public boundary.

## Decisions

- Keep dispatcher and setup as separate error owners.
- Retain `InvocationError`, `DispatcherError`, and existing subclasses that
  have distinct construction or catch behavior. Add no category hierarchy.
- Give each owner one closed error-spec registry keyed by internal catalogue
  entry ID. Each spec defines the public code, factual message template, and
  allowed safe context. Multiple entries may share a public code only when
  their meaning and consumer handling are identical.
- Add optional fields compatibly to current wire versions. Version an
  interface only if the inventory proves a breaking change is unavoidable.
- A confirmed `cause` and tentative `clues` may describe an error. Recovery is
  not a generic error field; it belongs only to a recoverable setup-flow result.
- `setup_required` and `setup_busy` are successful status evaluations, not
  errors.

## Inventory before editing

Produce one table in two independent ways, then reconcile it:

1. Definition-first: trace every error class through raises, catches,
   translations, serializers, and tests.
2. Behavior-first: trace every public code, nonzero exit, rendered failure,
   setup status, and flow state back to its producer.

Cover dispatcher code, CLI validation, MCP startup and request handling, the
manager adapter, Python machine-interface runner, setup evaluation/state/
manager code, and tests. Warnings are out of scope for this error refactor and
will be audited separately. For every public
emission record its producer, carrier, consumer, current meaning, final
disposition, and coverage.

Inventory all public emissions, but migrate only arbitrary or overloaded
codes, raw public exceptions, manager-boundary translations, and status/
recovery misclassifications. Mark already-accurate structured errors as
retained and cover them with shared contract tests.

The reconciled wording and disposition inventory is the normative
[dispatcher and setup error message catalogue](./2026-09-06-dispatcher-setup-error-message-catalogue.md).
Implementation must update that catalogue if source inspection discovers an
additional public emission condition; it must not invent public wording at a
raise site.

## Error specifications

The registry, not a class hierarchy, is the source of truth for public errors.
Dispatcher codes use the `dispatcher.*` namespace; setup wire codes use
`setup.*`. Names in this document are normative only when shown in code font.

Replace arbitrary `DirectBlueprintError(code=...)` production with registered
specs. Split `dispatcher.runtime_misconfigured` only at raise sites where it
currently represents different facts, such as manager invocation, malformed
manager response, contradictory manager response, repository configuration,
or target runtime. Preserve a concrete subclass only if a caller catches it
differently or it enforces special context.

Existing setup exceptions remain when they control manager behavior. Add or
rename one only when the inventory finds a distinct catch path. Internal
invariant exceptions may remain ordinary Python errors, but the outer public
boundary must contain and redact them.

## Payload contracts

The dispatcher envelope retains schema version 1:

- required: `schema_version`, registered `code`, factual `message`;
- optional safe identifiers declared by that code's spec;
- optional `cause`: one reduced, confirmed lower-level diagnosis;
- optional `clues`: ordered possibilities, always rendered as tentative.

An embedded `cause` is exactly its registered `code`, factual `message`, any
`clues`, and only context fields allowed by that code's own registry spec. It
contains no nested cause or recovery.

A producer may attach clues only when a named, tested predicate observes the
supporting condition. A generic upstream failure alone is never evidence for
a bootstrap, missing-context, or setup clue. Do not expose tracebacks,
credentials, environment values, argv/stdin, ledger contents, or unfiltered
child output.

A setup diagnosis contains existing `error` text, registered `error_code`, and
optional reduced `cause` and `clues`. Keeping `error` preserves compatibility.
When translated, `error_code` becomes the outer error's allowlisted
`setup_error_code`, `error` supplies its factual message, diagnosis clues remain
top-level clues about that same failure, and the reduced cause becomes the
outer cause. The diagnosis is not wrapped as another cause, so the result stays
one hop. Python inheritance does not cross the subprocess boundary.

## Setup protocols

The setup manager owns two existing protocols:

| Protocol | Producer | Values | Consumer |
| --- | --- | --- | --- |
| Status | `status` evaluation | `unmanaged`, `ready`, `setup_required`, `setup_busy` | MCP preflight |
| Flow | mutating lifecycle operations | `ready`, `run-step`, `awaiting-settlement`, `authorized-markdown-call`, `busy`, `failed`, `recovery-required` | MCP lifecycle caller |

The repeated words are intentionally different fields: `code` describes a
read-only target evaluation; `state` describes one lifecycle operation. They
are never translated into each other implicitly. MCP preflight passes through
status. Lifecycle callers consume flow state directly.

Every valid status exits 0. Every status failure exits nonzero in the existing
flow-shaped response envelope with `state=failed`, a setup diagnosis, and no
status code. `setup_required` has a nonempty root, null flow ID, and a nonempty,
duplicate-free LIFO stack in reverse dependency
order: `pending_stack[-1]` is dependency-first and must belong to that root's
evaluated closure. Manager-produced `setup_busy` has a nonempty flow ID and no
pending stack. Manager evaluation establishes ordering and closure membership,
and manager tests verify them. The adapter checks only shape, safe step fields,
uniqueness, nonempty identifiers, and exit pairing before MCP constructs a
begin route or adds its passive manager identity `{interface, version}`.

`ready`, `run-step`, `awaiting-settlement`, and
`authorized-markdown-call` exit 0. `busy`, `failed`, and
`recovery-required` exit 2 and include a setup diagnosis. Usage errors exit 64
in the same flow-shaped envelope with `state=failed`; legacy null flow fields
may remain in schema 1. Contradictory state/exit pairs are invalid responses.

`setup_required` reports only the evaluated root and pending stack. MCP may
add the next step, begin route, and redacted continuation identity: caller,
interface, and version. Original arguments remain outside the manager.

## Recovery

Only flow-backed `recovery-required` may include this flat object:

```text
recovery: {interface, version, flow_id, actions: ["retry", "cancel"]}
```

The actions are fixed by the existing recovery interface, not calculated per
flow. On `begin` with a continuation, the manager verifies that runtime caller
identity equals the supplied continuation caller. The new ledger schema makes
that verification an invariant of every continuation-bearing flow eligible for
ordinary recovery. The manager later emits and accepts recovery only when
runtime caller identity matches that verified owner. Active flows from an
older schema and flows without a continuation expose no ordinary recovery
object; their repair remains an explicit operator concern. Negative
begin-spoofing, legacy-flow, and cross-caller tests are required.

`setup_busy` remains passive: its optional manager identity contains no action
or arguments. Bootstrap failures and uncertainty without a live, authorized
flow use the existing flow-shaped envelope with `state=failed`, facts, and
possibly clues. The refactor does not widen recovery authority.

## Boundary rules

One manager-adapter function parses redacted JSON before judging exit status.
It decodes exactly one of two existing variants: a status result containing
`code` or a flow-shaped result containing `state`. Status and internal-preflight
failures use the latter with `state=failed`; no third wire variant is added.

| Child result | Handling |
| --- | --- |
| Valid status | Preserve it for internal preflight |
| Successful flow result used internally | Continue the preflight operation |
| Non-success flow result used internally | Translate its diagnosis to a dispatcher error; never relabel it as a status |
| Valid flow result from a direct lifecycle call | Canonically re-serialize it into existing `stdout`, preserve its exit code, and expose empty `stderr` |
| Syntactically valid JSON object that fails variant validation or contradicts operation/exit | `dispatcher.manager_response_invalid` |
| Malformed or non-object payload with zero process status | `dispatcher.manager_response_malformed` |
| Resolution/start failure, typed dispatcher failure before a manager payload, or nonzero process status without a syntactically valid JSON object | `dispatcher.manager_invocation_failed` |

Public carriers are explicit:

| Boundary | Carrier |
| --- | --- |
| CLI parse and pre-dispatch | Selected text or JSON error format on stderr |
| MCP request | `dispatcher` field of the normal `invoke` result envelope |
| Manager subprocess | Validated setup JSON; never raw stdout/stderr |
| Captured runner/load failure | Registered dispatcher error without inferred cause; child output remains private |
| MCP pre-server startup | Sanitized stderr/host diagnostic and nonzero process exit, not a tool response |

Runner diagnoses use one dispatcher-created private pipe, never target output.
The dispatcher passes its writer as private `--diagnostic-writer TOKEN` before
the gateway operands; the token is a POSIX fd inherited with `pass_fds` or a
Windows handle inherited through the sole `STARTUPINFO` `handle_list` entry
with `close_fds=true`. The runner removes the option before interface parsing.
On Windows the child clears handle inheritance before converting the handle to
an owned CRT fd. The parent makes only a duplicated writer inheritable inside a
module-global launch lock and restores and closes it on every launch path; any
future repository-owned Windows launch using broad handle inheritance must use
that lock. Launch uses `Popen`: the parent closes its writer immediately,
drains the diagnostic reader concurrently with ordinary process I/O, retains
at most 16 KiB, and continues discarding to EOF while marking overflow invalid.
The runner writes at most one compact registered payload, closes the writer,
and exits with reserved code 70. On launch failure the parent closes both pipe
ends. Diagnostic reads are interruptible or
nonblocking; no reader join is unbounded. On timeout the parent terminates the
process, waits for a bounded grace period, then kills and finally waits if
necessary; it closes the reader and performs only a bounded final join. Once
the deadline expires, cleanup completes and the timeout error wins: no later
runner diagnosis, decoding error, or checked-process error is accepted. For a
non-timeout completion, the dispatcher collects ordinary output as bytes and
first validates any complete exit-70 diagnosis. An accepted diagnosis becomes
the registered dispatcher error. Only when none is accepted does processing
fall back to ordinary subprocess semantics: requested text decoding runs before
`check`, preserving the current precedence. The descriptor is not exposed
through environment variables or interface arguments.

Text rendering shows the factual message, confirmed cause, then a labelled
`Possible clues:` section. CLI/MCP parity applies only to shared in-process
dispatcher errors; adding setup mediation to the CLI is out of scope.

## Skill descriptions

Verify and tighten `bootstrap-dispatcher-runtime`; its current scope is already
narrow. Name only the final codes backed by missing/old Python or missing
declared-package evidence. Exclude routing, authorization, setup state, and
manager-response failures.

Update `setup-interface-manager` to document both protocols, exact evaluated
requirements, tentative clues, passive busy results, and authorized live-flow
recovery. It must never guess a requirement or retry automatically.

## Implementation and green audit

1. Reconcile the inventory and record compatibility decisions.
2. Add shared registry/payload tests; migrate only defective producers.
3. Add status/flow contract tests; fix setup failure classification and the
   single manager adapter.
4. Add boundary, redaction, clue-predicate, recovery-authority, and retained
   schema-version fixtures.
5. Update skill descriptions after final codes and behavior are verified.
6. Audit producers for factual accuracy, then consumers for faithful rendering.

Green requires: every public inventory row has a disposition; registries are
exhaustive for emitted codes; retained wire versions pass compatibility
fixtures; every raw public failure is contained; legal status/state and exit
pairs are tested; valid manager diagnoses survive translation; clues are
predicate-backed and tentative; and recovery is exact, flow-backed, and
authorized.
