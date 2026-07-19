# Machine-Module and Caller-Contract Design

Requirement IDs in this document are normative and correspond to the decision
ledger and verification matrix.

## Module and export boundary

A `machine-module` is a certification and implementation node. A nested
interface is the independently grantable dispatcher operation. The module is
not callable by ID and its siblings are not implicitly visible.

```yaml
schema_version: 3
node_type: machine-module
id: email-triage.machine-module.watermark-writer
version: 1
description: Implements watermark state mutations.
gateway:
  kind: python-entrypoint
  path: _rtx/_watermark_writer.py
  symbol: Interface
  args_prefix: []
  conformance:
    adapter_protocol: officina-python-adapters@1
    bind_method: bind_conformance_adapters
    sandbox_profile: officina-isolated-effects@1
content: ['_rtx/_watermark_writer\.py']
conformance_manifest:
  base: skill-root
  path: tests/interface-conformance.yaml
platform_support: {linux: true, macos: true, windows: true}
dependencies: []
behavior_sources: []
owns_filesystem:
  - path: state/
    syntax: literal
    allowed_readers: []
uses_interfaces: []
interfaces:
  update-watermark:
    id: email-triage.machine.scripts-update-watermark
    version: 1
    description: Advance the mailbox watermark after successful processing.
    allow_all_skills: false
    allowed_callers: [email-triage]
    invocation_binding:
      fixed:
        - {kind: option, name: --format, value: json, type: {kind: string, format: {named: literal}}}
    uses_interfaces: []
    helpers: []
    direct_io:
      reads: []
      writes: []
      network: []
    owns_filesystem: []
    contract: {}
```

The abbreviated `contract` above is expanded by the sections below. The module
version identifies the internal module contract for migration and
certification. Public dependency edges pin the nested interface version. A
gateway-only refactor invalidates certification through hashing but does not
automatically change a public version. `conformance_manifest` locates the
fixture-only certification evidence described in the admissibility design; it
is not runtime content.

Access control is interface-local. `allow_all_skills` and `allowed_callers`
retain their existing mutual constraint. An owner-only export names only its
own skill in `allowed_callers`; there is no separate visibility field.

## Interface simplicity

An interface is admissible only if its arguments, preconditions, outputs,
outcomes, effects, authorization, execution decisions, and helpers describe one
unchanging operation (`IFC-001` through `IFC-004`). The schema makes multimodal
structures unrepresentable: there are no call selectors, conditional accepts,
or cross-argument constraints. Semantic certification checks that prose has not
hidden the same structure.

Presentation options are allowed only when they do not change output meaning,
framing, effects, caller action, or execution guarantees. Otherwise the module
exports separate interfaces with fixed presentation bindings.

## Interface partial application

`interface.invocation_binding.fixed` is a list of discriminated typed entries:

- `positional`: one fixed implementation value plus its implementation
  `position`;
- `option`: one fixed implementation option plus a typed scalar value;
- `switch`: one fixed implementation switch.

Every entry declares an implementation-owned name or position and a value type
where applicable. Public positional bindings also name implementation positions;
the caller supplies only their values, in increasing position order. Raw argv
strings, dispatcher options, secrets, caller/fixed collisions, duplicate names,
duplicate positions, and conditional entries are invalid. The binding compiler
merges fixed and public implementation positionals by position, then emits
named entries, producing one deterministic argv/stdin plan. It never executes
arbitrary transformations (`BND-001` through `BND-006`).

The fixed `--format json` example above is an implementation option. It must not
be confused with dispatcher-global `--dry-run`; dispatcher-global options are
not valid fixed entries.

## Arguments

Every argument has:

- a stable local key;
- a nonempty semantic description;
- `required: true|false`;
- an optional typed `default`, permitted only when not required;
- a closed `sensitivity` value;
- one `argument.invocation_binding`;
- one recursive `type` specification.

The sensitivity vocabulary is `public`, `user-private`, `derived-private`,
`credential`, and `secret`. `credential` and `secret` values cannot appear in
fixed bindings, argv, generated usage, logs, or injected examples. Direct
secret-bearing public inputs are limited to stdin or a protected file
reference; account nicknames and similar identifiers are enums, not secrets.
Sensitivity applies recursively: an argument's sensitivity classifies its
terminal token, while `element_type`, `content_type`, and `entry_type` classify
the values they contain. A public path to a secret file therefore declares
`sensitivity: public` on the argument and `sensitivity: secret` on
`content_type`. A file with credential/secret content also requires
`content_protection: owner-only`. This is an authored handling requirement, not
a universal dispatcher preflight guarantee: semantic certification must verify
the gateway's no-follow/private-permission handling on every supported platform,
and conformance may claim it only through a boundary that safely opens the
fixture. A platform without demonstrated handling cannot certify that export.

Argument binding variants are:

- `positional`: `position` and positive `arity`;
- `option`: `name` and positive `arity`;
- `switch`: `name`, no value or arity, and `type.kind: flag`;
- `stdin`: encoding and framing, with at most one stdin-bound argument.

The binding compiler must be able to emit the global dispatcher ordering without
changing the gateway's meaning. Named options have no authored relative order.
For an unbounded positional followed by named bindings, parsing stops at a
recognized declared option/switch. Its element type must therefore exclude
those exact tokens. The standard `--` terminator may instead precede the
positional sequence, making every remaining token positional; that form is
valid only when no named value must be supplied. The compiler round-trips both
forms and rejects a contract whose accepted value language remains ambiguous.

## Recursive types

The terminal type set and recursive fields follow `ARG-001` through `ARG-007`.
Every kind has a closed conditional schema so irrelevant fields are rejected.

- Scalar strings may use `named`, `template`, or `regex` formats. Named formats
  come from the schema's closed registry; templates type every capture; regexes
  declare dialect, full/prefix semantics, and an example.
- Integers and numbers may declare inclusive `minimum` and `maximum` bounds and
  a unit from the closed unit registry. The schema rejects reversed bounds and
  out-of-range defaults. A closed set of scalar values is an `enum`; scalar
  branches do not duplicate enum semantics with `allowed_literals`.
- Dates declare one or more accepted formats. Datetimes additionally declare
  timezone semantics. Durations declare syntax and unit semantics.
- Lists require `element_type`; list element cardinality and terminal arity are
  distinct.
- Files may use `content_type`; directories may use `entry_type`. Structured
  content can reference a repository-confined JSON Schema and must still
  summarize the caller-relevant shape. Opaque content is explicitly marked.
- Enums use either nonempty inline `{value, description}` entries or one helper
  reference. The two sources are mutually exclusive.

Filesystem types flatten `syntax`, `relative_to`, `must_exist`, `access`, and
matching fields into the type. `relative_to` is a closed base: `caller-cwd`,
`skill-root`, or a declared filesystem resource reference. Glob/regex branches
require `match_count: {minimum, maximum}`, where `maximum: null` is unbounded;
this is selected-path cardinality and is independent of invocation arity.
Matching is always against presented paths before symlink resolution. A
selected symlink is followed to its target, but directory symlinks are not
traversed while enumerating. Escaped resolved targets fail confinement. These
fixed rules replace per-interface `match_against`, `symlink_policy`, and
`follow_directory_symlinks` switches in this version (`ARG-004`, `ARG-011`).
An argument whose filesystem value is read or written declares
`direct_io_ref`. The referenced direct-I/O entry uses either a literal `path`
or `path_source: {kind: argument, argument_ref: <argument-id>}`; the latter must
point back to that exact argument. Untyped metavariable path strings are invalid.

## Preconditions and interaction

`contract.preconditions` is a list of stable, cross-referenceable entries. Each
entry identifies the condition, how it is checked, the outcome emitted when it
is unmet, and the caller action. A free-form precondition without a check and
outcome is invalid (`PRE-001`).

`contract.interaction` is interface-wide, not call-local. It is discriminated as:

- `unattended`: no prompt; optional cancellation and timeout semantics;
- `interactive`: declared channel, unattended refusal outcome, cancellation,
  and timeout behavior.

An interaction mode cannot alter the operation's effects or meaning. If
interactive confirmation changes authorization or execution behavior, export a
separate interface.

`contract.caller_warnings` contains interface-wide tagged explanations for
`may-wait`, `may-incur-cost`, `rate-limited`, and `external-side-effect`.
Conditional warnings are forbidden by the simple-interface invariant.

## Outputs and outcomes

An output is caller-visible material. It has a stable ID, channel, audience,
encoding, closed framing, recursive type or schema, cardinality, ordering,
limits/pagination/truncation where applicable, and empty-result meaning.
Framing values are a closed schema vocabulary rather than arbitrary prose.
Every output references the matching immediate `direct_io` write entry; output
metadata refines its framing and caller meaning rather than replacing I/O
declaration.
Compatibility is closed: `stdout` requires medium `stdout`, `stderr` requires
medium `stderr`, `file` requires a filesystem write, and `event-stream`
requires a declared streaming medium (`stdout` in this version). The referenced
entry's format and sensitivity must agree with the output schema and audience.

An outcome is a terminal behavioral case. It has a stable ID, closed class,
typed signal, output references, effect references, and required caller action.
Typed signals are exit-code sets plus optional structured stdout/stderr matches;
arbitrary signal objects are invalid. Outcomes include applicable success,
no-op, refusal, partial, and error cases plus one fallback operational failure.
A validator checks unique IDs, reference integrity, required coverage, and
detectable signal overlap. Semantic certification checks substantive
exhaustiveness (`OUT-001`, `OUT-002`).
The outcome/effect relation is authored in both directions for caller
readability and must be an exact inverse: an outcome lists effect `E` iff its ID
occurs in `E.may_occur_in_outcomes`. Mismatch is a structural error.

## Execution

`contract.execution` always contains `state_effect`, `lifecycle`, and
`consistency`. The four combinations of effect and lifecycle are valid.

- `read-only` forbids effects and `mutation_safety`.
- `mutating` requires nonempty effects and `mutation_safety`.
- `finite` forbids `long_running`.
- `long-running` requires `long_running`.

Each tagged decision is an object with exactly one active key from the closed
vocabulary and a nonempty explanation as its value. Generic `policy`, `state`,
`key`, and `condition` objects are invalid. `retry` is invalid for a
non-idempotent operation. `verify_then_decide` requires at least one applicable
verification target.

Effects and verification use the canonical names in `EXE-004`. An effect
references one declared direct-I/O entry and applicable outcomes. `action` is
closed to `create|update|append|delete|send|execute|configure` and must be
compatible with the referenced entry's access (`write|read-write`, `delete`,
`send`, `execute`, or `configure` respectively; create/update/append use a write
access). `value_source` and `confirmation_evidence` are discriminated reference
objects, never colon-delimited strings. Verification
selects exactly one declared output, direct-I/O entry, or helper. A long-running
block references declared readiness and startup-failure signals and defines a
bounded stop method. Every effect also declares one explained reversibility tag:
`reversible`, `compensatable`, or `irreversible`. This describes recovery after
a completed effect; `rollback_on_failure` separately describes automatic or
available recovery during a failed invocation.

## Direct I/O and ownership

Every interface declares all three `direct_io` lists, even when empty. Entries
have stable IDs so effects and verification can reference them. Local writes
must fall under the union of module-shared and interface-private ownership.
External read-only inputs need not be owned. Network entries remain explicit
even when a semantic read/write entry describes the returned or mutated object.

Module content is source ownership used for hashing and certification;
`direct_io.*.content` is a semantic resource classification. These are distinct
namespaces. The canonical direct-I/O definition lives in
`direct-io.schema.json`; other schemas reference it rather than copying it.

The private `tmp/` and `logs/` exception follows `IO-003`. Authored implicit
paths must be relative and traversal-free. Certification checks the gateway's
actual confinement; a sandboxed runner may enforce no-follow containment, but
the schema/dispatcher does not claim to intercept arbitrary internal Python I/O.
Any temp/log state affecting output, verification, persistence, or later
invocations remains behaviorally relevant and must be declared.

Ownership entries use `path`, `syntax: literal|glob|regex`, and
`allowed_readers`. Module ownership automatically authorizes all its exports;
interface ownership automatically authorizes only that export. `allowed_readers`
adds exact external interface IDs and does not grant sibling write authority.
Legacy ownership `match` migrates directly to `syntax`; omitting
`allowed_readers` normalizes to an empty list.
For `syntax: literal`, a path ending in `/` owns that directory and every
descendant; any other literal owns exactly one lexical path. Glob/regex scopes
match complete normalized relative paths. Matching happens before selected-link
resolution, and an escaped resolved target is never authorized.

## Tools and helpers

Module `uses_interfaces` is reserved for a direct tool required by every export.
Interface `uses_interfaces` adds only direct local tools. Duplicate references,
version conflicts, unresolved targets, unauthorized targets, and sibling or
transitive inheritance are invalid.

Each helper relationship has a local ID, role, pinned target interface, fixed or
forwarded input mapping, typed result selector, destination routing, empty
behavior, freshness semantics when applicable, and failure-to-outcome mapping.
Helpers may route only to a declared argument, precondition, output,
verification, or subsequent helper input. Targets must be in the effective
direct tool set. Cycles, incompatible mappings, fixed secrets, and unbounded
access to other target operations are invalid.

For caller-facing injection, helper expansion recursively follows helper edges
to a bounded fixed point. Each reached helper is checked against its own
provider interface; ordinary `uses_interfaces` edges are never traversed by
this closure. Cycles and projection-size overflow fail closed. A helper used as
an enum value source must call a `state_effect: read-only` interface and select
a finite result whose schema/cardinality supplies a mechanical upper bound
(`DEP-004`).

The binding restricts what generated prompt guidance presents, but dispatcher
still authorizes the caller skill rather than an individual LLM consumer. It is
not a security capability. When callers must be technically unable to vary
helper arguments, the provider exports a separate simple interface with those
values fixed in its own invocation binding.

Ordinary provider tool dependencies are execution metadata and do not grant
the LLM those tools. Only an LLM interface's own direct grant or a bounded helper
expansion creates caller-facing availability.

## Schema-shaped reference for conditional branches

The following shapes are normative. Optional fields shown with comments are
permitted only on their discriminated branch; schemas reject unknown fields.

```yaml
preconditions:
  - id: account-exists
    description: The selected account is registered.
    check:
      kind: helper-result       # argument | direct-io | helper-result
      helper_ref: list-accounts
      predicate: contains       # closed per check kind
      expected_from_argument: account
    unmet_outcome: unknown-account
    caller_action: Select an account returned by the helper.

interaction:
  mode: interactive             # unattended | interactive
  channel: tty                  # stdin | tty; interactive only
  unattended_outcome: confirmation-required
  timeout_seconds: 60
  cancellation:
    kind: dispatcher-cancel     # dispatcher-cancel | signal | unsupported
```

An interactive stdin channel conflicts with a stdin-bound argument. Timeout is
a positive integer or absent; absence means no interface-level timeout claim.
Argument predicates cannot compare two arguments.

```yaml
outputs:
  - id: records
    channel: stdout             # stdout | stderr | file | event-stream
    audience: machine           # machine | human | both
    encoding: utf-8
    framing: exactly-one-json-document
    description: Selected records.
    schema: {path: schemas/record-list.schema.json, fragment: '#'}
    direct_io_ref: records-stdout
    cardinality: {minimum: 0, maximum: null}
    ordering: stable            # stable | unspecified | sorted
    pagination: {kind: none}    # none | cursor | page
    truncation: {kind: none}    # none | bounded, with declared limit
    empty: An empty array means no record matched.

outcomes:
  - id: success
    class: success              # success | no-op | refusal | partial | error
    signal:
      exit_codes: [0]
      stdout_match:             # optional typed matcher
        kind: regex             # literal | regex
        pattern: '^\\['
        dialect: python
        matching: prefix
    outputs: [records]
    effects: []
    caller_action: Consume the result.
```

All outputs require `direct_io_ref`. Static overlap detection
checks intersecting exit-code sets and identical typed matchers; semantic review
handles overlaps requiring interpretation.

```yaml
execution:
  state_effect: mutating
  lifecycle: long-running
  consistency:
    per_source: Each source is internally consistent but sources may differ.
  effects:
    - id: state-write
      direct_io_ref: state-write
      action: update
      value_source: {kind: argument, argument_ref: new-state}
      may_occur_in_outcomes: [success, partial]
      confirmation_evidence: {kind: output, output_ref: receipt}
      reversibility:
        compensatable: Invoke the declared restore helper with the receipt.
  mutation_safety:
    atomicity:
      per_effect_only: Each write is atomic but the set is not.
    concurrent_invocations:
      unsafe: Concurrent writers may overwrite one another.
    idempotency:
      non_idempotent: Repeating may apply the update twice.
    on_uncertain_completion:
      verify_then_decide: Read the receipt/state before deciding whether to retry.
    partial_effects_on_failure:
      possible: Earlier writes may remain after a later failure.
    rollback_on_failure:
      available: The restore helper can compensate after verification.
  long_running:
    ready_when:
      kind: output-match        # output-match | outcome | helper-result
      output_ref: events
      matcher: {kind: literal, value: ready}
    stop_method:
      kind: dispatcher-cancel   # dispatcher-cancel | signal | helper
    startup_failure_outcome: startup-failure
  verification:
    - method: output-schema     # output-schema | direct-io-state | helper-check
      output_ref: receipt       # exactly one target ref
```

`mutation_safety` contains all six decisions for every mutating interface.
`long_running` is present only for long-running interfaces. Verification method
and target-ref combinations are closed by schema.

```yaml
helpers:
  - id: list-accounts
    role: Supplies valid account identifiers.
    interface: email-client.machine.accounts-list
    version: 1
    inputs: {}
    result:
      output_ref: accounts
      selector: {kind: json-pointer, value: '/accounts'}
    route:
      kind: argument-enum       # argument-enum | precondition | verification |
      target: account           # output | helper-input
    empty:
      outcome: no-accounts
      caller_action: Configure an account first.
    freshness: {maximum_age_seconds: 60}
    failure: {outcome: helper-failure}
```

Helper inputs use exactly one source: `fixed` nonsecret value, `argument_ref`,
or prior `helper_output_ref`. Selector kinds are closed and type-checked. The
enum argument uses `values_from_helper: list-accounts`; it has no inline values
or dynamic default.
