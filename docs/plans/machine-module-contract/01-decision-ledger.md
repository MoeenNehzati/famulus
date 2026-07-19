# Decision Ledger

This file is the single authority for cross-cutting names and decisions. Later
documents define structures in detail but must not select competing terms.

## Settled architecture

- `MOD-001` — The v3 executable blueprint node is `machine-module`, singular.
  One module owns one implementation boundary and exports a required nonempty
  `interfaces` map.
- `MOD-002` — A module owns `gateway`, `content`, `version`, platform support,
  runtime dependencies, behavior sources, shared `owns_filesystem`, and shared
  `uses_interfaces`. Module-level `direct_io` is invalid.
- `MOD-003` — A nested interface owns its public ID, public version,
  description, accessibility, invocation binding, caller contract,
  `direct_io`, optional private `owns_filesystem`, local `uses_interfaces`, and
  helpers.
- `MOD-004` — A module is the certification node. Its certificate covers every
  exported interface and records interface-targeted results. One failing export
  prevents module certification. Callers pin nested interface versions, not
  module versions.
- `MOD-005` — Shared gateway or content drift invalidates the module certificate
  and therefore every export. It requires a public interface version bump only
  when that export's breaking contract changes.
- `MOD-006` — Every module locates one conformance manifest covering all
  exports. It is certification evidence, not runtime content: its bytes/results
  are bound by the certificate evidence digest and live external probes are not
  required certification cases.
- `MOD-007` — The normalized graph retains ordinary authored node edges,
  export-runtime edges, helper edges, and derived module-certification edges as
  separate records. Each traversal uses only its named edge class.
- `MOD-008` — `node_hash` covers only the canonical blueprint and node-owned
  runtime `content`. A separate `contract_reference_hash` covers the canonical
  conformance-manifest locator/digest entry and complete transitive
  contract-definition locator/digest closure. Module certificate currentness
  requires both hashes; referenced-byte drift therefore makes a certificate
  suspect without changing node identity.

## Simple interfaces

- `IFC-001` — One interface is one coherent operation, not a command family.
- `IFC-002` — Remove `calls`, selectors, selector guards, call-local `accepts`,
  cross-argument constraints, conditional defaults, call visibility, and
  argument-conditioned behavior.
- `IFC-003` — Every argument's meaning is independent of every other argument.
  If one argument changes another's meaning, legality, authorization, effects,
  or interpretation, split the operation into separate interfaces.
- `IFC-004` — Static `required` and `default` belong on the argument. An
  implementation-only mode or presentation choice belongs in fixed invocation
  binding, not in a public mode argument.
- `IFC-005` — Interface `description` states the caller-visible operation or
  outcome. Module `description` states the implementation boundary. Skill
  routing prose remains a separate concept.
- `IFC-006` — `contract.title` is removed; the interface ID and description are
  the single naming authority.

## Invocation

- `BND-001` — `invocation_binding` is retained at two explicit scopes:
  `interface.invocation_binding` is module partial application;
  `argument.invocation_binding` maps one public value to terminal syntax.
- `BND-002` — Partial application may select, rename, order, or fix
  implementation parameters. It may not branch, transform arbitrary values, or
  contain business logic.
- `BND-003` — Fixed bindings are typed entries, not raw argv fragments. They
  cannot contain secrets, dispatcher-global options, or values overridable by
  callers. A fixed positional value declares its implementation position in the
  same collision domain as public positionals.
- `BND-004` — Argument bindings are `positional`, `option`, `switch`, or
  `stdin`. Arity describes value cardinality.
- `BND-005` — The caller supplies all public positionals first in increasing
  position, then named options and switches in any order, keeping each option
  adjacent to its values. Dispatcher compiles final argv by merging fixed values
  into their declared implementation slots. Interfaces cannot author an argv
  template that overrides this grammar.
- `BND-006` — `gateway.args_prefix` and dispatcher-global arguments are
  dispatcher-owned and absent from caller-visible syntax.
- `BND-007` — An unbounded positional language cannot overlap a following
  declared option/switch token. Standard `--` makes all remaining tokens
  positional and is usable only when no named value remains required.

## Arguments and filesystem values

- `ARG-001` — Terminal argument kinds are `string`, `integer`, `number`,
  value-bearing `boolean`, `flag`, `date`, `datetime`, `duration`, `enum`,
  `path`, `file`, `dir`, and `list`. Mapping/object are not terminal kinds.
- `ARG-002` — Recursive fields are `element_type`, `content_type`, and
  `entry_type`. The nested value is another type specification.
- `ARG-003` — `path`, `file`, and `dir` are siblings. `path` is the umbrella
  for either a file or directory.
- `ARG-004` — Filesystem `syntax` is `literal`, `glob`, or `regex`. Glob/regex
  match presented paths before symlink resolution. Selected links follow their
  targets; directory links are not traversed during enumeration by default.
- `ARG-005` — `flag` pairs only with a valueless `switch`. A value-bearing
  boolean must declare accepted terminal spellings.
- `ARG-006` — Enum values are inline typed entries or are obtained through an
  interface-local helper. Runtime values are not copied into generated YAML.
- `ARG-007` — Every argument declares a closed sensitivity class. Credentials
  and secrets cannot be fixed or carried in argv; a secret-bearing public input
  must use stdin or a referenced file with declared protections. Recursive
  element/content/entry types declare their own sensitivity when they contain
  data distinct from the terminal token, so a nonsecret file path can refer to
  secret file content explicitly.
- `ARG-008` — `content_protection: owner-only` is an implementation handling
  claim established per supported platform by semantic/conformance evidence;
  it is not a universal dispatcher preflight guarantee.
- `ARG-009` — A filesystem argument and its immediate dynamic I/O entry link in
  both directions through `direct_io_ref` and typed `path_source`; untyped path
  metavariables are invalid.
- `ARG-010` — `integer` and `number` may declare inclusive `minimum` and
  `maximum` bounds and a unit from the closed unit registry. Bounds must be
  ordered and defaults must satisfy them. Enumerated scalar values use `enum`
  rather than a second allowed-literals mechanism.
- `ARG-011` — Glob/regex filesystem values declare `match_count`, distinct from
  terminal invocation arity. This version fixes matching to presented paths,
  follows a selected symlink to its target, and does not traverse directory
  symlinks while enumerating; these are documented standard semantics, not
  per-interface switches.

## Preconditions, interaction, outputs, and outcomes

- `PRE-001` — Preconditions are stable entries with a check, unmet outcome, and
  caller action. Interaction is interface-wide and explicitly unattended or
  interactive; it cannot change the operation's meaning or effects.
- `OUT-001` — Outputs describe caller-visible material with stable IDs, closed
  channel/audience/encoding/framing, typed shape, cardinality, and empty-result
  meaning.
- `OUT-002` — Outcomes describe terminal behavioral cases with stable IDs,
  closed classes, typed signals, output/effect references, and caller actions.
  Structural validation requires applicable success and fallback cases;
  semantic certification establishes substantive completeness.
- `OUT-003` — Interface-wide `caller_warnings` preserve material wait, cost,
  rate-limit, and external-side-effect warnings. A warning cannot be conditional
  on a public argument; such a difference requires separate interfaces.
- `OUT-004` — Every output references its matching immediate `direct_io` write;
  output framing and meaning refine rather than replace the I/O declaration.
- `OUT-005` — Static validators reject intersecting exit-code sets with
  identical typed matchers; semantic certification decides overlaps requiring
  interpretation.

## Relationships, I/O, and execution

- `DEP-001` — `uses_interfaces` means direct machine tools required to run, not
  platform support.
- `DEP-002` — For interface `I`, the effective execution tool set is exactly
  `module.uses_interfaces union I.uses_interfaces`. Siblings and ordinary
  transitive dependencies add no authority.
- `DEP-003` — Helpers are interface-local relationship metadata. Their targets
  must already occur in the effective direct tool set. Only bounded helpers can
  broaden the caller-facing injected set.
- `DEP-004` — Injection expands helpers recursively through helper edges only,
  to a bounded acyclic fixed point; it never follows ordinary tool dependencies.
  A helper supplying enum values must target a read-only interface and return a
  finite, schema-bounded value set.
- `IO-001` — `direct_io` is immediate semantic I/O and belongs to each nested
  interface. Transitive I/O is an analytical view, never authored duplication.
- `IO-002` — Module ownership is shared by all its exports. Interface ownership
  is private to that export. Shared/private and sibling/private claims cannot
  overlap.
- `IO-003` — Relative `tmp/` and `logs/` access in the callee skill's private
  runtime namespace may be omitted only when purely ephemeral or diagnostic.
  This rule belongs in schema descriptions and blueprint docs, not injection.
- `IO-004` — A literal ownership path ending `/` owns the directory subtree; a
  non-directory literal owns exactly one normalized lexical path. Glob/regex
  match full normalized relative paths before no-escape target resolution.
- `IO-005` — Static checks prove only authored tmp/log path confinement.
  Semantic certification checks arbitrary gateway I/O; runtime enforcement is
  claimed only when a demonstrated sandbox covers that boundary.
- `EXE-001` — `state_effect: read-only|mutating` and
  `lifecycle: finite|long-running` are independent axes.
- `EXE-002` — Execution-level decisions are limited to the vocabularies in
  `EXE-003`; dispatcher behavior is not authored under `contract.execution`.
- `EXE-003` — Execution decisions use exactly one closed tag with a nonempty
  explanation. Active vocabularies are:
  `consistency = snapshot|per_source|best_effort|eventually_consistent`;
  `atomicity = atomic|per_effect_only|non_atomic`;
  `concurrent_invocations = safe|unsafe`;
  `idempotency = idempotent|non_idempotent`;
  `on_uncertain_completion = retry|verify_then_decide|stop`;
  `partial_effects_on_failure = impossible|possible`;
  `rollback_on_failure = automatic|available|unavailable`.
- `EXE-004` — Effects use `direct_io_ref`, `action`, `value_source`,
  `may_occur_in_outcomes`, `confirmation_evidence`, and a tagged
  `reversibility` explanation. Action is a closed vocabulary and value/evidence
  sources are discriminated reference objects, not encoded strings. Verification uses
  `method` and exactly one of `direct_io_ref`, `output_ref`, or `helper_ref`.
  Long-running fields are `ready_when`, `stop_method`, and
  `startup_failure_outcome`.
- `EXE-005` — Authored outcome effect lists and each effect's
  `may_occur_in_outcomes` set must be exact inverses.

## Discovery and injection

- `INV-001` — Discover blueprint roots and sidecars from the filesystem, not
  graph reachability. Parsing is deterministic, JSON-compatible, strict, and
  fail-before-yield by default.
- `INV-002` — `skip_parse_errors=True` exists only for diagnostics/search.
  Schema, identity, and relationship failures are never skippable parse errors.
- `INV-003` — Inventory never follows directory symlinks, opens selected files
  no-follow, checks lexical owner-root confinement, and yields only regular
  files.
- `INJ-001` — Injection selects normalized canonical YAML; it does not copy a
  raw file, translate to a second interface language, or render prose.
- `INJ-002` — Each LLM interface receives only its own direct grants and bounded
  helpers. Named LLM files and root `SKILL.md` receive separate generated
  blocks. No sibling or ordinary transitive leakage is allowed.
- `INJ-003` — Provider `uses_interfaces` is visible only as contract metadata
  needed to understand the selected export; it does not independently grant
  those tools to the LLM. A bounded helper edge is the only automatic
  expansion.
- `INJ-004` — Consumer locality controls prompt visibility, not dispatcher
  authorization granularity. Dispatcher continues to authorize by caller skill.
  Helper bindings are validated invocation guidance, not a runtime capability
  sandbox. If fixed helper arguments must be enforced, expose a separate fixed
  simple interface and grant that interface.
- `INJ-005` — A closed projection schema defines exact retained, conditional,
  normalized, and forbidden fields. Retained external definitions are embedded
  digest-bound and referenced by projection-local keys.
- `INJ-006` — Generated blocks use the two exact markers and deterministic
  consumer-owned placement; synchronization is atomic and byte-idempotent.
- `INJ-007` — Migration gives every formerly union-injected export exactly one
  disposition: add a direct edge, keep uninjected, or retire.
- `INJ-008` — A cross-skill LLM target carries a canonical `provider-skill`
  route; a same-skill target carries only its relative instruction gateway.
- `INJ-009` — Embedded definitions retain only the validation-equivalent
  reachable closure and caller annotations. A standalone export over 12,288
  UTF-8 bytes fails certification; a combined consumer block over 16,384 bytes
  fails only injection. Neither is silently truncated.
- `HOOK-001` — One combined SessionStart block defines only vocabulary needed
  by selected fragments and explains verified dispatcher-global arguments once.
  It includes the positional-before-named grammar and remains within 750
  characters.
- `HOOK-002` — The notation glossary distinguishes zero-or-more
  (`[<x>...]`) from one-or-more (`<x>...`). It states that `--dry-run` resolves
  and prints the compiled invocation without executing the gateway or reading
  stdin. `<skill>` remains a generic caller placeholder because one session may
  load more than one skill.

## Admissibility and gates

- `ADM-001` — `admissible` means the authored contract passes the formal rule
  profile. `certified` means semantic review established that the admissible
  contract is accurate and complete. `operationally healthy` is an
  environment-specific report, not another certificate status.
- `ADM-002` — Every rule has a stable ID/version, phase, scope, exact blocking
  consequences, validator owner, evidence requirements, and positive/negative
  tests. Use explicit `blocks`, not vague severity.
- `ADM-003` — Schema/security/binding/reference/ownership failures prevent
  repository indexing and all dispatcher execution. There is no public development
  bypass. Private gateway tests remain available outside dispatcher.
- `ADM-004` — Public dispatcher use and injection require a current module
  certificate with a passing result for the selected export. The certificate
  binds the admissibility profile hash and passing evidence digest.
- `ADM-005` — Heuristics may request semantic review but cannot prove semantic
  simplicity or completeness.
- `ADM-006` — Certification compares each export with its previous certified
  public contract. Known mechanical breaking changes require a version bump;
  semantic review decides ambiguous compatibility changes. Runtime-only or
  documentation-only changes invalidate certification but do not automatically
  increment the interface version.
- `ADM-007` — Previous-contract comparison reconstructs the exact committed
  canonical projection named by the predecessor certificate. First
  certification is not applicable; an unreproducible predecessor fails closed.
- `ADM-008` — Effect/no-undeclared-I/O conformance requires an injected adapter
  seam plus demonstrated sandbox coverage. A manifest declaration alone is not
  evidence of interception.
- `ADM-009` — The certificate-backed view verifies authoritative signature,
  source/node/dependency/profile hashes, exact export version, and passing
  result digest. Self-certification uses one exact private certifier bootstrap,
  never a public dispatcher bypass.
- `ADM-010` — Binding boundary cases are generated from the contract. Every
  semantic outcome has a manifest case or an explained
  `not-deterministically-inducible` result that remains subject to semantic
  review.

## Migration mechanics

- `MIG-001` — Phase 5 inventories live machine behavior and groups operations by
  normalized gateway identity. Existing v2 declarations may seed the checked
  map but are not authoritative; content, gateways, tests, and observed behavior
  decide the target. The map resolves collisions/splits and records target v3
  public export IDs.
- `MIG-002` — Phases 1 through 4 create and verify target v3 infrastructure but
  do not write live blueprints. Explicitly authorized Phase 5 creates v3 modules
  and applies certificate gates. Legacy health is never accepted as a
  certificate.
- `MIG-003` — Legacy path roots found in code, tests, or earlier declarations
  migrate explicitly: unprefixed paths become
  `relative_to: skill-root`; `$repo/...`, `$home/...`, and persistent `$tmp/...`
  become declared repository, home/config, or temporary filesystem resource
  references. Ephemeral skill-private `$tmp/...` becomes the private runtime
  namespace. Raw absolute paths are rejected unless represented by a declared
  resource reference.

## Deliberately deferred or rejected

- `DEF-001` — Behavior profiles and inherited behavior hierarchies are not part
  of this version. A future design may factor shared network wait/retry behavior.
- `DEF-002` — Logical-filesystem unification, external CLI-description
  standards, machine-module `__init__` gateway conventions, and mapping/object
  terminal arguments remain outside this project.
- `DEF-003` — Role/kind/display/search metadata from the older metadata plan is
  retained as an independent future workstream; it must not block the contract
  migration.
- `DEF-004` — `requires_serialization`, `idempotent_with_key`, `resume`,
  `serialization_key`, `idempotency_key_argument`, and `resume_token` are
  rejected from the current execution vocabulary. The underlying real behavior
  must be expressed through the active choices, verification, and caller action.
- `DEF-005` — Dynamic defaults are rejected. A runtime-selected value is modeled
  as a helper-backed explicit argument, a fixed export, or a separate interface;
  omission cannot hide a changing operational choice.
- `DEF-006` — Direct executable gateways are deferred. This version permits
  Python entrypoints only. A future command-file design must use tracked
  executables under `_cx` and define its conformance boundary before admission.
