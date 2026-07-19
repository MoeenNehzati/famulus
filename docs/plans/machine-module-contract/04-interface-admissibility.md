# Interface Admissibility and Certification

## Definitions

- **Admissible**: the authored module/export satisfies a named, versioned formal
  rule profile at the current source revision.
- **Certified**: semantic review established that the admissible blueprint
  accurately and completely describes its implementation, and the signed result
  remains current.
- **Operationally healthy**: environment-specific tests or probes currently
  pass. This is report data, not an additional certificate state.

An admissible interface is structurally representable, deterministic to invoke,
authorized, internally consistent, and capable of producing required
conformance evidence. A certified interface additionally represents one true,
complete operation. A proper public interface is both admissible and certified.

## Canonical rule authority

Extend `schema-meta.json#/x-famulus/validation_rule_catalog` as the single
machine-readable rule catalog. Every schema `related_validation_rules` entry
must resolve to exactly one catalog rule. Do not introduce a second catalog.
Add `references/blueprint/interface-admissibility.profile.yaml` only to pin the
ordered rule IDs and versions required for certification.

Each `schema-meta.json` rule has this closed shape:

```yaml
interface.binding.unambiguous:
  version: 1
  phase: static
  scope: interface
  statement: Every caller and fixed binding has one collision-free terminal form.
  blocks: [indexing, dispatch, certification, injection]
  validator: binding-unambiguous
  applicability: {kind: always}
  evidence: [blueprint, normalized-binding]
  tests:
    positive: [tests/fixtures/interface_admissibility/binding-unambiguous.valid.yaml]
    negative: [tests/fixtures/interface_admissibility/binding-option-collision.invalid.yaml]
```

Phases are `schema`, `static`, `conformance`, `semantic`, and `advisory`.
Scopes are `module`, `interface`, or `repository`. `validator` is a closed
registry key, never a dynamically imported path. `applicability` is one of
`always`, `field-present`, or `field-equals`, using a schema-validated JSON
pointer and literal where applicable. `blocks` names exact
consequences; vague severity labels are forbidden. Advisory rules cannot block.
Changing a rule's meaning increments its version. A profile pins the complete
ordered rule/version set and has a canonical hash (`ADM-001`, `ADM-002`).

The canonical skill standard remains the author-facing policy authority. Each
machine-module/interface requirement in
`skill-guidelines.standard.yaml` references the applicable admissibility rule
IDs. The schema-meta catalog names each rule's enforcement owner. The generated
Markdown is derived. A rule is never independently redefined in schema prose,
hooks, validators, and docs.

## Diagnostic result

Add `references/blueprint/interface-admissibility-result.schema.json` for
complete machine diagnostics. Unlike legacy health checks, it permits failure
and checker errors:

```yaml
profile: machine-export-admissibility@1
profile_hash: sha256:<digest>
subject:
  module: example-skill.machine-module.records
  interface: example-skill.machine.inspect-records
  interface_version: 1
source_hash: sha256:<digest>
results:
  - id: interface.binding.unambiguous
    version: 1
    result: passed
    findings: []
    evidence: [blueprint:/interfaces/inspect-records/invocation_binding]
```

`result` is `passed`, `failed`, `not-applicable`, or `checker-error`.
Findings contain stable diagnostic codes, JSON pointers or graph subjects, and
concise messages. Checker errors never count as passes. A report is admissible
only when every profile-required rule is passed or validly not applicable.

## Schema and static rules

The initial profile contains these machine rules:

| Check ID | Required fact | Blocking gates |
|---|---|---|
| `interface.document.closed@1` | No unknown, deprecated, profile, draft, call-family, or dispatcher-consequence fields are authored. | indexing, dispatch, certification, injection |
| `interface.identity.canonical@1` | Module/export IDs, versions, and local IDs are canonical and unique. | indexing, dispatch, certification, injection |
| `interface.arguments.type-complete@1` | Every argument and recursive subtype has all kind-specific authored fields. | indexing, dispatch, certification, injection |
| `interface.arguments.defaults-valid@1` | Required/default/fixed values are compatible and type-valid. | indexing, dispatch, certification, injection |
| `interface.arguments.io-linked@1` | Filesystem arguments and dynamic direct-I/O paths have exact bidirectional typed references. | indexing, dispatch, certification, injection |
| `interface.binding.total@1` | Every public value has one binding and every declared fixed parameter compiles. | indexing, dispatch, certification, injection |
| `interface.binding.unambiguous@1` | Positions/names are unique; no fixed, caller, or dispatcher-option collision or raw argv fragment exists. | indexing, dispatch, certification, injection |
| `interface.binding.dispatch-order@1` | The compiled declared binding has a unique positional-first/named-after terminal form. | indexing, dispatch, certification, injection |
| `interface.references.resolved@1` | Every schema, format, resource, I/O, output, outcome, helper, and verification reference resolves with the right type/version. | indexing, dispatch, certification, injection |
| `interface.outputs.structured@1` | Output IDs, channels, framing, types, schemas, cardinality, and empty meaning are usable. | certification, injection |
| `interface.outputs.io-linked@1` | Every output resolves to a channel-compatible immediate direct-I/O write. | indexing, dispatch, certification, injection |
| `interface.outcomes.structured@1` | Outcome IDs/classes/signals/references/actions are valid and structural coverage exists. | certification, injection |
| `interface.execution.axes-consistent@1` | Effect/lifecycle axes admit exactly the applicable conditional blocks. | indexing, dispatch, certification, injection |
| `interface.execution.decisions-valid@1` | Tagged decisions have one allowed tag/explanation and satisfy cross-field retry/verification rules. | indexing, dispatch, certification, injection |
| `interface.effects.io-consistent@1` | Effects and verification point to compatible declared I/O, outputs, helpers, and outcomes. | indexing, dispatch, certification, injection |
| `interface.effects.outcome-inverse@1` | Outcome effect lists and effect occurrence sets are exact inverses. | indexing, dispatch, certification, injection |
| `interface.ownership.authorized@1` | Writes are in shared/private authority and ownership scopes do not overlap. | indexing, dispatch, certification, injection |
| `interface.dependencies.direct@1` | Effective tools are exactly the direct module/interface union without sibling or transitive inheritance. | indexing, dispatch, certification, injection |
| `interface.dependencies.authorized@1` | Target IDs/versions/platforms/caller permissions resolve and permit the edge. | indexing, dispatch, certification, injection |
| `interface.helpers.well-bound@1` | Helpers are direct, bounded, acyclic, type-compatible, safely routed, and contain no fixed secrets. | indexing, dispatch, certification, injection |
| `interface.sensitivity.transport-safe@1` | Authored secrets/credentials are absent from argv, fixed values, examples, and generated usage. | indexing, dispatch, certification, injection |
| `interface.direct-io.internal-path-safe@1` | Authored implicit tmp/log paths are relative, traversal-free, and confined. | indexing, dispatch, certification, injection |
| `interface.preconditions.structured@1` | Preconditions have stable IDs, checks, unmet outcomes, and caller actions. | certification, injection |
| `interface.interaction.structured@1` | Interaction mode, channels, unattended outcome, timeout, and cancellation fields satisfy the selected mode. | certification, injection |
| `interface.version.breaking-change@1` | Mechanically known breaking differences from the previous certified contract have a higher public version. | certification, injection |
| `interface.injection.closed@1` | Projection contains only the consumer's selected exports, bounded helpers, and required definitions. | injection |
| `interface.injection.export-size-bounded@1` | One export's validation-equivalent projection is at most 12,288 UTF-8 bytes without truncation. | certification, injection |
| `interface.injection.consumer-size-bounded@1` | One consumer's combined projection is at most 16,384 UTF-8 bytes without truncation. | injection |

JSON Schema owns closed local shape and conditional presence. Pure static
validators own cross-field checks. Graph validators own identity, edges,
authorization, and ownership. Projection validators own injection closure. A
rule has one primary enforcement owner even if an earlier layer also rejects an
obviously invalid instance.

## Conformance profile

Every public export supplies a standard fixture/probe manifest consumed through
the compiled dispatcher binding. The profile defines which probes are required
for its declared fields:

| Check ID | Runnable evidence |
|---|---|
| `interface.gateway.accepts-binding@1` | Representative, empty, minimum, maximum, and invalid invocations reach the expected parser outcomes. |
| `interface.gateway.enforces-fixed-values@1` | Callers cannot override or duplicate fixed parameters. |
| `interface.outputs.conform@1` | Captured streams satisfy declared channel, encoding, framing, type, and schema. |
| `interface.outcomes.conform@1` | Fixtures for applicable success/no-op/refusal/partial/error cases produce declared signals and references. |
| `interface.lifecycle.conform@1` | Declared finite fixtures terminate within their deadlines; long-running fixtures demonstrate readiness, startup failure, cancellation, stop, and cleanup behavior. |
| `interface.effects.conform@1` | Controlled probes observe declared effects and confirmation evidence. |
| `interface.protected-file.conform@1` | A covered fixture demonstrates no-follow and owner-private handling for each supported platform claimed by a protected-file export. |
| `interface.boundary.covered@1` | The injected seam and named sandbox demonstrate interception of every boundary used by effect or absence claims. |

Positive tests can prove that declared effects occur. They cannot prove the
absence of undeclared effects unless the probe uses a sandbox/tracer whose
coverage includes every relevant boundary. The result must state its evidence
scope. A plain unit test cannot certify `no undeclared I/O`.

## Semantic profile

These checks require LLM or human judgment over implementation, tests,
behavior sources, and the proposed blueprint:

| Check ID | Required judgment |
|---|---|
| `interface.semantic.single-operation@1` | The export is one coherent operation, not a hidden mode family. |
| `interface.semantic.argument-independent@1` | No public argument changes another's meaning, legality, authorization, effects, or interpretation. |
| `interface.semantic.descriptions-exact@1` | Module/interface/argument/output/outcome prose accurately describes behavior. |
| `interface.semantic.outcomes-complete@1` | Caller-visible terminal and uncertain-completion cases are complete and nonmisleading. |
| `interface.semantic.io-complete@1` | Direct I/O, effects, ownership, helpers, and direct tools omit no relevant behavior. |
| `interface.semantic.sensitivity-correct@1` | Classifications and transport choices match the substantive data. |
| `interface.semantic.execution-accurate@1` | Consistency, atomicity, concurrency, idempotency, partial effects, rollback, lifecycle, and verification claims are true. |
| `interface.semantic.invocation-compatible@1` | Fixed/public ordering and encoding preserve the gateway operation's meaning. |
| `interface.semantic.preconditions-complete@1` | All material preconditions and caller actions are represented. |
| `interface.semantic.interaction-accurate@1` | Prompting, unattended, timeout, cancellation, and warning claims match behavior. |
| `interface.semantic.version-compatible@1` | Ambiguous public-contract changes are classified correctly as breaking or nonbreaking. |

Text heuristics for `if`, `depending on`, `mode`, or references to another
argument can raise an advisory finding. They cannot pass or fail semantic
independence by themselves (`ADM-005`). Existing deterministic checks currently
named `semantic-exactness` must be reassigned to machine rule IDs; the semantic
label is reserved for genuine review.

## Gates

`indexing` means constructing the in-memory repository graph used by dispatcher;
there is no persistent registration service.

| Operation | Structural admissibility | Current certificate |
|---|---:|---:|
| Repository indexing | required | not required |
| Owner-skill dispatcher call | required | required |
| Cross-skill dispatcher call | required | required |
| LLM context injection | required | required |
| Private test gateway runner | intentionally outside public gates | not required |

1. Inventory and schema validation run before a module can enter the in-memory
   repository index.
2. Security-critical static and graph rules run at indexing and are
   rechecked by dispatcher. Failure blocks all public dispatch (`ADM-003`).
3. Full static and required conformance rules run before certification.
4. Semantic checks run only after machine admissibility passes.
5. Every export must pass; findings target exact export IDs.
6. Public dispatcher execution and injection require a current module
   certificate whose interface result and profile match the selected export
   (`ADM-004`).
7. Operational health probes may later report environment failures without
   rewriting the authored contract or certificate status.

## Conformance manifest and safety

Every module declares `conformance_manifest` as an explicit locator outside
runtime `content`:

```yaml
conformance_manifest:
  base: skill-root
  path: tests/interface-conformance.yaml
```

Only `skill-root` is allowed in this version; repository-root evidence is not.
`path` is a normalized relative literal path confined beneath that root. Every
component and the final target are opened no-follow, the target must be a
Git-tracked regular file owned by the same skill, and both locator and bytes are
hashed into the evidence digest. The manifest lists every export and applicable
conformance-rule cases. Missing exports prevent certification.

Standard conformance is fixture-only. Filesystem, clock, network, helper,
subprocess, calendar, and email effects require named closed-registry adapters;
live external mutation and real credentials are forbidden. The manifest selects
the only execution boundary supported by this version:

- `python-adapter-v1`: the private runner injects a typed adapter bundle into a
  declared gateway seam, while an OS sandbox denies direct filesystem, network,
  subprocess, and credential access outside the bundle.

The conformance runner must demonstrate that the chosen sandbox covers every
boundary claimed by an effect or no-undeclared-I/O assertion. A gateway without
an injection seam or demonstrated OS-sandbox coverage may test parsing and
captured outputs, but is ineligible for effect-conformance or absence claims.
Manifest declarations alone never establish interception. Long-running cases
require a process timeout, readiness deadline, stop deadline, cleanup action,
and post-cleanup assertion. Live probes belong to operational health and are not
required certification evidence.

The Python protocol is exact:

```python
class ConformanceAdapterBundle(Protocol):
    filesystem: FilesystemAdapter
    clock: ClockAdapter
    network: NetworkAdapter
    helpers: HelperAdapter
    subprocess: SubprocessAdapter
    calendar: CalendarAdapter
    email: EmailAdapter

class ConformanceBindableModule(Protocol):
    def bind_conformance_adapters(
        self, adapters: ConformanceAdapterBundle
    ) -> None: ...
```

Every named adapter implements one shared, schema-checked operation API:

```python
class BoundaryAdapter(Protocol):
    def invoke(
        self,
        operation: str,
        request: Mapping[str, JsonValue],
    ) -> Mapping[str, JsonValue]: ...
```

`references/blueprint/conformance-boundary-operations.yaml` is the single
closed, versioned registry. It maps each `boundary/operation` to request schema,
success schema, permitted stable errors, and effect class. Version 1 contains
filesystem `read|write|stat|list|delete`, clock `now|advance`, network
`request`, helpers `invoke`, subprocess `run`, calendar
`list|create|update|delete`, and email `list|read|search|send`. Schema files live
under `references/blueprint/conformance-operations/`; unknown operations and
unvalidated requests/results are checker errors.

Python calls validate directly against that registry. Initial stable error
codes are `invalid-request`, `unavailable`, `fixture-missing`, `forbidden`, and
`adapter-failure`; each registry operation lists its allowed subset.

For `python-adapter-v1`, gateway `conformance.adapter_protocol` is
`officina-python-adapters@1` and `bind_method` is exactly
`bind_conformance_adapters`. The private runner instantiates the gateway inside
the sandbox, calls the method exactly once before parsing/execution, requires a
`None` return, and revokes the bundle during cleanup. Missing/repeated binding,
an exception, a non-`None` return, or any post-revocation adapter call is a
checker error.

Sandbox backends are closed registry keys `linux-bwrap-v1`,
`macos-seatbelt-v1`, and `windows-appcontainer-v1`. A backend must prove sole
fixture-root write access, no ambient network or credentials, adapter-only
declared subprocess/helper access, process-tree cleanup, and no inherited
descriptors other than stdio. Missing backend support is a checker error for a
platform the module claims. The manifest records one backend key for every
claimed platform. The Python examples in `examples/` provide the positive
`python-adapter-v1` form.

Command gateways are outside this version. A later design may add them only as
tracked executables under `_cx/`, with a separately specified conformance
transport and execution boundary.

Binding-boundary cases are generated mechanically from the contract: minimum,
maximum when finite, missing required values, invalid typed values, unknown
options, stdin mismatch, and override/duplication of every fixed entry. The
manifest supplies semantic outcome/effect fixtures. Each declared outcome must
have a case or an explicit
`{kind: not-deterministically-inducible, reason: <nonempty>}` entry; the latter
produces a conformance `not-applicable` result and remains mandatory semantic
review evidence rather than being counted as a pass.

## Previous-contract retrieval

Compatibility comparison reconstructs the prior public contract from the
current predecessor certificate's `source_commit`, recorded module blueprint
path, export ID, and export version. It reads that exact committed file and runs
the same versioned canonical public-projection algorithm used for current
certification. First certification reports `not-applicable`. If a certificate
claims a predecessor but its commit, path, export, or canonical projection
cannot be reproduced, certification fails closed with `checker-error`; it never
silently treats the export as new.

## Certificate binding and legacy health

The target certificate payload binds:

- module node hash and direct dependency hashes;
- admissibility profile ID and hash;
- complete required rule IDs and versions;
- digest of the passing diagnostic/conformance/semantic evidence report;
- per-export result digests;
- the canonical locator/digest map for the conformance manifest and complete
  transitive closure of resolved contract/schema/format definitions;
- certifier identity and existing source/signature fields.

A profile or rule-version change makes earlier certificates suspect even when
module content is unchanged. The profile/catalog must therefore be included in
the certifier's behavior roots or bound explicitly as above; relying only on the
certifier node hash is insufficient when the catalog changes independently.
Module `node_hash` includes the blueprint, runtime content, conformance-manifest
bytes, and the resolved definition closure. Certificate status and projection
re-resolve every locator no-follow and compare the canonical digest map; any
missing, moved, or changed referenced byte makes the certificate suspect before
dispatch or injection. `behavior_sources` remains for explanatory evidence and
is not required merely to make contract references current.

Current `health.schema.json` is read-only legacy input during migration. Do not
dual-write admissibility results into it. Compatibility reporting may derive a
pass-only view from certificates and diagnostic reports without writing health
artifacts. The health format cannot record failures/checker errors, and the
certification migration already replaces it with certificates. No fourth status
system is added.
