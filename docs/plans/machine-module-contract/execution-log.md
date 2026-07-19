# Machine-Module Contract Execution Log

## Phase 1: Schema, standards, and fixture foundation

Status: gate passed and accepted before Phase 2.

Branch: `codex/machine-module-phases-1-3` in an isolated worktree.

Target schema artifacts:

- `machine-module.schema.json`
- `caller-contract.schema.json`
- `direct-io.schema.json`
- `interface-conformance.schema.json`
- `conformance-boundary-operations.schema.json`
- `conformance-boundary-operations.yaml`
- operation schemas for filesystem, clock, network, helpers, subprocess,
  calendar, and email, plus their shared error-envelope schema
- `interface-admissibility-profile.schema.json`
- `interface-admissibility.profile.yaml`
- `interface-admissibility-result.schema.json`

Executable fixture evidence:

- two machine-module fixtures: one valid and one removed-`calls` negative;
- the two committed simple/advanced module examples;
- the two committed simple/advanced conformance examples;
- 18 positive and 18 negative boundary-operation envelope instances.

Standards and metadata:

- target v3 root selection now selects `machine-module`;
- the single validation-rule catalog discriminates
  `repository-validation` from `interface-admissibility` entries;
- one ordered `machine-export-admissibility@1` profile pins only admissibility
  rules and hashes their resolved meanings;
- the canonical skill standard contains an authoritative v3 machine-module
  family while preserving imported v2 source-fidelity history;
- the generated Markdown view and blueprint hook are aligned.

Requirements covered by the Phase 1 schema and fixtures: `MOD-001` through
`MOD-006`, `IFC-001` through `IFC-006`, `BND-001` through `BND-007`, `ARG-001`
through `ARG-011`, `PRE-001`, `OUT-001` through `OUT-005`, `DEP-001` through
`DEP-004`, `IO-001` through `IO-005`, `EXE-001` through `EXE-005`, and the
Phase 1 data-contract portions of `ADM-001` and `ADM-002`. Cross-reference,
graph, compilation, and runtime enforcement remain assigned to later phases.

Gate evidence:

- schema/catalog gate: 68 passed;
- schema/template/catalog gate: 84 passed;
- standards gate: 46 passed;
- `.githooks/skill/check-blueprints`: passed;
- `git diff --check`: passed.

No live skill blueprint was migrated or treated as target authority.

## Phase 2: Inventory, graph, dispatcher, and currentness

Status: gate passed and accepted before Phase 3.

Implemented APIs:

- strict buffered inventory with diagnostic issue aggregation and no-follow
  regular-file reads;
- normalized repository nodes, nested exports, export/helper edges, module
  certification edges, scoped runtime authority, and public export resolution;
- pure caller-argument parsing and deterministic fixed/public argv compilation;
- nested-export dispatcher routing through the owning Python module gateway;
- a narrow certification view with a fail-closed production placeholder;
- module template generation and separate `node_hash` and
  `contract_reference_hash` computation.

Currentness contract:

- `node_hash` commits canonical module declaration plus module-owned runtime
  content;
- `contract_reference_hash` commits the canonical ordered locator/digest map
  for the conformance manifest and resolved schema/format closure;
- both hashes must match for currentness; every nested export shares its owning
  module state.

Gate evidence:

- Phase 2 combined test gate: 247 passed;
- template/artifact compatibility and two-hash gate: 75 passed;
- `git diff --check`: passed.

The production certification view rejects target module dispatch with
`certification-unavailable`; tests inject explicit passing/failing views. No
live skill blueprint was migrated or used as target-v3 evidence.

## Phase 3: Consumer-local injection

Status: local gate passed; two GitHub-install cases are externally blocked;
awaiting review before Phase 4.

Implemented APIs:

- `project_consumer_interfaces()` and `standalone_export_size()` select exact
  direct grants, bounded helper closure, consumer-local LLM routes, and a
  digest-bound reachable definition closure;
- `generated_used_interfaces_block()`, `sync_used_interfaces_block()`,
  `plan_consumer_interface_updates()`, and
  `apply_consumer_interface_updates()` plan deterministic root/named gateway
  changes and apply only a fully planned set atomically;
- `render_dispatcher_context()` emits one host-neutral SessionStart payload;
- `build_interface_injection_migration_report()` produces the read-only exact
  disposition report.

Projection and synchronization behavior:

- missing/stale certification, version mismatch, helper cycles, unsafe enum
  helpers, unresolved/escaping definitions, and combined projection overflow
  fail closed;
- cross-skill LLM dependencies expose only a provider-skill route; same-skill
  named dependencies expose an owner-relative instruction gateway;
- generated blocks use the exact `BLUEPRINT USED INTERFACES` markers, root
  placement immediately after the contract block, named placement before the
  authored body, stale-block removal, and byte-identical resynchronization;
- a one-export sample generated block is 373 UTF-8 bytes;
- the required SessionStart core is 485 characters and a representative
  selected-vocabulary payload is 684 characters, within the 500/750 bounds.

Requirements covered by the selector, block writer, hook, and migration report:
`INJ-001` through `INJ-009`, `DEP-004`, `HOOK-001`, `HOOK-002`, and the
projection portions of `ADM-004` and `ADM-009`.

Gate evidence:

- projection, full skill-maker blueprint tools, migration report, hook, and
  local Claude/Codex install/dev-link/uninstall gate: 76 passed;
- GitHub-install cases: 2 externally blocked before exercising repository
  code—Claude marketplace clone failed on host SSH configuration/credentials,
  and Codex marketplace clone could not resolve `github.com`;
- `git diff --check`: passed;
- root and named marker placement plus byte-identical second synchronization:
  passed in fixture tests.

No live skill gateway or blueprint was synchronized or migrated. Existing
blueprints remain non-authoritative migration inputs only.
