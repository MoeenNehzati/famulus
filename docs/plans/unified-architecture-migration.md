# Unified Node Architecture Migration Plan

> **Status:** Draft. Execute task by task with review at each stop gate.

**Goal:** Move the live repository to the `module` and `behavioral_source`
architecture in `docs/architecture.md` by generalizing existing machinery, not
by building parallel replacements.

## Current-system baseline

The migration starts from these live owners and changes them in place:

| Concern | Existing owner to retain and generalize |
| --- | --- |
| Schema primitives | `references/blueprint/common.schema.json`, `caller-contract.schema.json`, and `direct-io.schema.json` |
| Module/source schemas | `skill.schema.json`, `machine-module.schema.json`, and `behavior-source.schema.json` |
| Inventory and graph | `src/officina/common/blueprint_inventory.py`, `blueprint_graph.py`, and `blueprint_template.py` |
| Hashing and provenance | `artifact_health.py`, `git_provenance.py`, `skills/skill-drift/_rtx/_drift_hashes.py`, `skills/skill-drift/_rtx/_check_drift_state.py`, and `skills/skill-drift/references/policy-hash-roots.json` |
| Certification mechanics | `skills/skill-audit/_rtx/_audit_certifier.py`, `audit_records.py`, and `atomic_files.py` |
| Status and drift | `certification_view.py`, `pooled_blueprint.py`, and `skills/skill-drift/` |
| Interface resolution | `MachineInterfaceExport`, `resolve_machine_export()`, the existing `machine_interface_binding.py` renamed in place to `process_binding_compiler.py`, and `interface_projection.py` |
| Execution | `ResolvedInvocation`, `src/officina/dispatcher/`, and `src/officina/runtime/` |
| Migration disposition | `interface_injection_migration.py` and its existing test |
| Authoring and enforcement | `skill-maker`, `regenerate-blueprints`, `refactor-skills`, repository validators, generated standards, and `.githooks/skill/check-blueprints` |

The plan may rename or reshape these owners, but it must not introduce a second
graph, hash pipeline, certifier, interface DTO, binding compiler, projection,
dispatcher, migration engine, or validation authority.

## Deliberate changes to existing contracts

This plan implements certification once against the final graph. It supersedes
the unimplemented execution sequence in
`docs/plans/migrate_audit_to_certification.md`; it does not first certify the
legacy taxonomy and immediately invalidate those certificates.

`docs/certification_and_drift.md` remains the certificate-design base, with two
explicit changes required by `docs/architecture.md`:

1. The current broad `compute_policy_hash()` implementation is adapted and
   renamed to `compute_certification_basis_hash()`. Its result,
   `certification_basis_hash`, also covers the canonical project node-input
   policy. There is no separate policy hash in certificates or currentness.
2. A policy may include ignored or untracked directly owned inputs. Their
   digests and provenance are signed local-state claims; `source_commit`
   reproduces only the tracked subset. The certifier itself and every tracked
   target input must still match the recorded commits.

The documentation, schema, status output, and recovery claims must all state
that distinction before such a certificate can be issued.

Gateway identity remains a whole file. The existing Python `symbol` capability
is preserved as an interface-owned process-binding selector, not as part of the
gateway address or ownership identity.

### Certification authority contract

Task 0 resolves certification authority as a cooperative same-user contract.
The existing audit writer, renamed in place to `skill-certifier`, remains the
one signing and certificate-output owner. `skill-drift` remains read-only and
uses the public verification key through its supported code path. The existing
`install-assistant-tools` owner provisions the user-scoped installation on
Linux, macOS, and Windows; no broker, service identity, second writer, special
bootstrap, or parallel signing path is added.

Windows remains a target, not a completed certificate-write platform. The
current `src/officina/common/atomic_files.py` fails closed off POSIX. Before
Windows v4 certification, Task 3 must generalize that existing owner in place
to equivalent confined atomic and no-follow semantics and pass Windows-specific
certificate tests. Until then Windows certification and v4 dispatch are
unsupported and fail closed; no parallel Windows writer/provider is allowed.

Restrictive user-only permissions, append-only history, atomic no-follow
writes, and post-write verification remain required as defense-in-depth. They
do not create a security boundary between processes running as the same UID.
Signatures and currentness checks detect drift, corruption, and changes outside
the cooperative writer contract, but they do not defend against a malicious
same-UID process that can access signing material or certificate outputs.

## Creation rule

Before adding a file, record its functional predecessor and disposition in the
migration map. A new file is allowed only when no live owner exists. The only
planned new canonical artifacts are:

- `references/certification/node-hash-policy.yaml` and its schema;
- `references/blueprint/certificate.schema.json` for semantics not represented
  by the legacy health schema;
- `docs/plans/unified-architecture-migration-map.yaml`;
- `scripts/migrate-blueprints-v4.py`, a retained argument-only CLI over the
  existing migration engine;
- migrated node blueprints whose map entry proves no existing target path.

The cooperative authority decision creates no additional authority executable
or service-install artifact. Signing and output handling remain within the
existing `skill-certifier`, `officina`, and `install-assistant-tools` owners.

Version-4 schema ownership is obtained by modifying or renaming existing schema
files. Tests use the existing inline and `tmp_path` fixture patterns. The CLI
contains no migration logic and remains as the map-validation entrypoint.

## Global preservation requirements

The migration must preserve, unless the reviewed map explicitly retires a
semantic requirement:

- complete conversion of unversioned, version-2, and version-3 declarations;
  old formats remain migration inputs, not supported v4 runtime inputs;
- exact-target isolation and graph dependency traversal;
- regular-file, traversal, symlink, descriptor-safe, race, and package-import
  protections;
- caller authorization and caller-declared direct dependency checks;
- the full caller contract, `direct_io`, and process argument behavior;
- provider-specific entry selectors, `route_smoke()`, the validated runner,
  and the dependency reachability discovered by that evaluation;
- helper identity, bindings, authorization, bounded/read-only constraints,
  resolved-definition safety, closure, and projection-size limits;
- blueprint search over the v4 layout, with intentionally migrated generic
  selectors and corresponding CLI documentation;
- dependency kind, name, version, platform, and reason in the generated runtime
  dependency manifest and installer;
- direct and host-active plugin installed-source discovery without treating
  inactive cached versions as active nodes;
- staged-mirror validators that include every map-authorized new path, hooks,
  and generated-standard fidelity;
- public-key verification, the cooperative certifier-only writer contract,
  restrictive permissions as defense-in-depth, append-only history, atomic
  no-follow writes, and post-write verification;
- certificate-backed pooled review while pooled-review health authority is
  retired;
- `implicit_dependence` as a non-certification analysis overlay.

Authored conformance/admissibility evidence is retired, but each substantive
rule receives one disposition: structural validator, certifier-owned check, or
reviewed retirement. Mechanism deletion must not silently delete a safety rule.

## Task 0: Freeze authority, inventory, and conflicting plans

**Modify**

- `docs/architecture.md`
- `docs/certification_and_drift.md`
- `docs/plans/migrate_audit_to_certification.md`
- `docs/plans/machine-module-contract/README.md`
- `docs/plans/machine-module-contract/IMPLEMENT.md`
- `docs/plans/post-migration-simplification/README.md`
- `docs/plans/logical-resource-addressing.md`
- `docs/plans/osx_feedback_fix/*.md`
- `docs/plans/per-llm-interface-personal-preferences.md`
- `docs/plans/interface-metadata-refactor.md`
- `docs/plans/blueprint-schema-documentation-consolidation.md`

**Create**

- `docs/plans/unified-architecture-migration-map.yaml`

- [x] Record branch, commit, and dirty-path ownership. Stop on detached HEAD or
  unexplained overlapping work.
- [x] Mark the three older implementation sequences superseded by this plan;
  retain them as history rather than allowing competing execution guidance.
- [x] Defer every overlapping active plan pending version-4 adoption and an
  approved rebase. Freeze each execution entrypoint and require fresh
  functional-predecessor dispositions for its proposed artifacts.
- [x] Inventory every unversioned, version-2, and version-3 declaration and
  every schema field, public ID, caller, generated artifact, installed-source
  class, document, validator, hook, and test family.
- [x] Record the current owner and exact target/retirement disposition for each
  entry. Include blueprint search, installer dependency consumption, helper
  semantics, `route_smoke`, contributor validators, and dynamic plugin/direct
  source discovery.
- [x] Give every existing conformance/admissibility rule a validator,
  certifier-check, or reviewed-retirement disposition.
- [x] Record the resolved cooperative same-user certification authority:
  Linux/macOS/Windows targets, the existing `skill-certifier` signing/output
  owner, the existing installer owner, fail-closed certification behavior, the
  Task 3 Windows atomic-writer gate, and the explicit lack of protection from
  a malicious same-UID process.
- [x] Reject duplicate or missing dispositions and any proposed file lacking a
  functional-predecessor decision.
- [x] Run the unchanged baseline:

  ```bash
  python3 scripts/run-python-tests.py --suite full --verbose
  python3 validators/runner.py
  bash .githooks/skill/check-blueprints
  ```

  Record exact results. Stop on unexplained failure.

## Task 1: Evolve the existing schemas and hash policy

**Modify or rename in place**

- `references/blueprint/common.schema.json`
- `references/blueprint/caller-contract.schema.json`
- `references/blueprint/direct-io.schema.json`
- `references/blueprint/skill.schema.json` as a migration input
- stage `references/blueprint/module.schema.json` from
  `references/blueprint/machine-module.schema.json`
- stage `references/blueprint/behavioral-source.schema.json` from
  `references/blueprint/behavior-source.schema.json`
- `references/blueprint/schema.json`, `schema-meta.json`, `template.yaml`, and
  `README.md`
- `docs/certification_and_drift.md`
- existing schema/template tests

**Create**

- `references/certification/node-hash-policy.schema.json`
- `references/certification/node-hash-policy.yaml`
- `references/blueprint/certificate.schema.json`

- [ ] Extend `common.schema.json` rather than creating separate requirement,
  gateway, or content-selector schemas. Add the requirement grammar and
  generalize the existing gateway/content definitions with gateway language
  and optional machine requirements.
- [ ] Generalize `caller-contract.schema.json` as the one interface contract.
  Keep semantic arguments, preconditions, outcomes, effects, lifecycle,
  helpers, and `direct_io`; group argv/stdin, entry selection, output framing,
  exit signals, and cancellation under one process-binding definition.
- [ ] Preserve provider-specific entry selection in that binding without
  restricting selectors to Python identifiers. Natural-language file gateways
  need no extra binding.
- [ ] Generalize the existing machine-module schema into `module`, importing
  relevant skill-root metadata, discovery, authority, and default-interface
  facts. Evolve the existing behavior-source schema into
  `behavioral_source`. Old shapes remain inputs to the migration engine only;
  the v4 schemas do not contain compatibility branches.
- [ ] Keep the two pre-v4 predecessor schema files and live root routing
  unchanged through Task 4. Test the staged v4 schemas directly; Task 5 removes
  the predecessors and switches the root atomically.
- [ ] Make module export versions derived from their source interfaces. Keep
  caller access only on module exports and intrinsic contracts on sources.
- [ ] Keep source-wide `platform_support` and `runtime_dependencies` optional
  but paired, covering the behavioral-source gateway implementation and all
  intrinsic interfaces. Require generated, version-bound `machine_evidence`
  in certificates; the array may be empty.
- [ ] Define the ordered project hash policy. Start from Git-tracked direct
  ownership; sequential include/exclude uses last-match-wins; only include has
  `require_match`; includes may add ignored/untracked directly owned regular
  files; reserved certification outputs are always forbidden.
- [ ] Make blueprint, gateway, and same-owner authored-contract closure
  mandatory. A cross-owner contract becomes a dependency. Logs, caches,
  runtime state, build output, and certificate/audit/health artifacts receive
  explicit exclusions.
- [ ] Update certification documentation and the certificate schema for local
  inputs, partial Git reproducibility, and one `certification_basis_hash` that
  covers the node-input policy and all other certification machinery.
- [ ] Extend existing tests only for new v4 and policy semantics; preserve the
  already-covered schema and path-safety cases.
- [ ] Run:

  ```bash
  python3 -m pytest -o pythonpath=src -q tests/test_typed_blueprint_schemas.py tests/test_blueprint_schema_metadata.py tests/test_officina_blueprint_template.py
  ```

## Task 2: Generalize the existing graph, hash, interface, and runtime core

**Modify**

- `references/blueprint/interface-projection.schema.json`
- `references/blueprint/pooled-review.schema.json`
- `src/officina/common/blueprint_inventory.py`
- `src/officina/common/blueprint_graph.py`
- `src/officina/common/artifact_health.py`
- `src/officina/common/git_provenance.py`
- rename/generalize `src/officina/common/machine_interface_binding.py` to
  `src/officina/common/process_binding_compiler.py`
- `src/officina/common/interface_projection.py`
- `src/officina/common/certification_view.py`
- `src/officina/common/pooled_blueprint.py`
- `src/officina/common/blueprint_template.py`
- `skills/skill-drift/_rtx/_drift_hashes.py`
- `skills/skill-drift/_rtx/_check_drift_state.py`
- `skills/skill-drift/references/policy-hash-roots.json`
- `src/officina/dispatcher/`
- `src/officina/runtime/`
- `src/officina/blueprint_search.py` and `scripts/search_blueprints.py`
- `skills/regenerate-blueprints/` and their existing tests

- [ ] Extend existing `BlueprintNode` and `RepositoryBlueprintGraph`; generalize
  `MachineInterfaceExport` and `resolve_machine_export()`. Do not add a second
  graph or a redundant resolved-interface DTO.
- [ ] Preserve `ResolvedInvocation` as the concrete execution plan. Rename and
  generalize the existing binding implementation as the one process-binding
  compiler; its parsing phase validates caller tokens into semantic argument
  values, and its compilation phase produces deterministic process argv and
  stdin binding. Preserve the validated runtimes and compile gateway path plus
  the interface binding's entry selector internally.
- [ ] Add module containment, most-specific direct ownership, shared
  module/source gateway aliases, source-owned interfaces, module exports, and
  the certification projection. Preserve helper edges and projection safety.
- [ ] Generalize `interface-projection.schema.json` and
  `pooled-review.schema.json` atomically with their existing producers. Replace
  legacy machine/LLM IDs and health fields with v4 interface IDs and
  certificate fields rather than creating parallel derived-artifact schemas.
- [ ] Generalize the existing `NodeHashState` path and eliminate the separate
  machine-module hash path. One implementation resolves policy inputs, records
  their path, digest, and Git provenance, computes the local node hash, and
  records dependency hashes separately. Policy-internal kind or final-rule
  details are not certificate manifest fields.
- [ ] Adapt the current broad implementation-policy calculation into the one
  `certification_basis_hash` and include the canonical parsed node-input policy
  in its basis manifest. Drift and certification consume the same
  implementation and may not independently redefine the basis.
- [ ] Rename and adapt the live `compute_policy_hash()` implementation and its
  `policy-hash-roots.json` manifest; fold the old schema/policy currentness
  fields into `certification_basis_hash` instead of leaving parallel hashes.
- [ ] Keep the existing runtime path live before Task 5 and exercise v4 only
  against converted temporary repositories. At cutover, v4 graph and runtime
  readers accept v4 only; legacy normalization remains solely in the migration
  engine.
- [ ] Map every dependency discovered by Python `route_smoke()` to a directly
  owned input, explicit certification dependency, or certification-basis
  component. Reject unmapped paths and require pre/post route-trace equivalence
  for loaded helpers, package initializers, and recursively dispatched targets.
- [ ] Migrate blueprint search to the new layout and generic v4 interface
  selectors. Update its CLI, documentation, and golden query/result tests at
  cutover; do not emulate legacy `.machine.` or `.llm.` selectors.
- [ ] Make installed-source adapters return direct installs and only the plugin
  versions identified as active by their host. Inactive cache entries are not
  graph nodes; test direct, active-plugin, and stale-cache coexistence.
- [ ] Make pooled review consume the generalized graph and certificate view;
  preserve its rendering while removing pooled-review health authority.
- [ ] Keep blueprint template generation and `regenerate-blueprints` on the
  same v4 schema owner.
- [ ] Rename `test_machine_interface_binding.py` with its implementation to
  `test_process_binding_compiler.py`. Port other existing tests to generic
  names only when their implementations are cut over. Add cases only for new
  ownership, exports, policy order, local inputs, and module authorization.
- [ ] Run:

  ```bash
  python3 -m pytest -o pythonpath=src -q tests/test_blueprint_inventory.py tests/test_officina_blueprint_graph.py tests/test_officina_artifact_health.py tests/test_officina_git_provenance.py tests/test_machine_module_hashing.py tests/test_process_binding_compiler.py tests/test_interface_projection.py tests/test_officina_dispatcher.py tests/test_officina_python_machine_interface.py tests/test_dispatcher_route_smoke.py tests/test_blueprint_search.py tests/test_officina_pooled_blueprint.py
  ```

## Task 3: Convert the existing audit writer into the final certifier

**Modify now; rename only at cutover**

- `skills/skill-audit/`
- `skills/skill-drift/`
- `src/officina/common/audit_records.py`
- `src/officina/common/atomic_files.py`
- `src/officina/common/certification_view.py`
- certificate/drift tests identified by the map

- [ ] Reuse the existing bottom-up traversal, exact-target isolation, commit
  checks for tracked inputs, canonical hashing, atomic writes, no-follow
  protections, and post-write verification.
- [ ] Replace health-record semantics with the final certificate and signed,
  append-only history. Add public/private signing separation, key rotation,
  certifier identity, dependency hashes, machine evidence, and
  `certification_basis_hash`.
- [ ] Keep signing and certificate writes in the existing audit-writer owner
  renamed to `skill-certifier`. Its supported interface reconstructs the
  payload internally; `skill-drift` remains a public-key, read-only consumer.
  Test the supported writer/read-only contracts without claiming protection
  from a malicious same-UID process.
- [ ] Generalize the existing `src/officina/common/atomic_files.py` owner in
  place before Windows v4 certification. Provide Windows-equivalent confined
  atomic/no-follow semantics, including reparse-point and destination-escape
  rejection, atomic replacement, durability, restrictive ACL verification,
  and post-write verification. Until those Windows tests pass, certificate
  writes and v4 dispatch on Windows remain unsupported and fail closed. Do not
  add a parallel writer or platform provider file.
- [ ] Keep runtime fail-closed for missing or suspect certification. Initial
  `skill-certifier` certification uses the same certifier path and complete
  basis checks; add no special bootstrap, service, or second writer.
- [ ] Require the certifier implementation and tracked target inputs to match
  their commits. Bind included ignored/untracked inputs by pre-certification
  digest and abort on mutation before write.
- [ ] Make admissibility a versioned certifier check over the final v4 graph.
  Validators may report structure but cannot persist or assert admissible,
  conformant, certified, or suspect state.
- [ ] Do not issue intermediate certificates for legacy topology. Before
  cutover, exercise the new core against converted temporary repositories only.
- [ ] Keep the old public audit writer unchanged through Task 4. The final
  certificate path remains test-only and cannot write live state before the
  atomic cutover.
- [ ] Make drift a read-only consumer of the shared graph, hash, basis, and
  certificate APIs. It must not sign, repair, or recompute competing judgments.
- [ ] Test signatures, history linkage, the cooperative writer contract, exact
  dependency agreement, local-input provenance, machine evidence,
  mutation/HEAD races, and stale basis/policy behavior using the existing
  audit/drift test homes.
- [ ] Run:

  ```bash
  python3 -m pytest -o pythonpath=src -q tests/test_officina_artifact_health.py tests/test_officina_atomic_files.py tests/test_officina_git_provenance.py skills/skill-audit/tests skills/skill-drift/tests
  ```

## Task 4: Prove the mapped conversion without changing the live tree

**Modify**

- `src/officina/common/interface_injection_migration.py`
- `tests/test_interface_injection_migration.py`
- `docs/plans/unified-architecture-migration-map.yaml`
- `scripts/migrate-blueprints-v4.py`

- [ ] Extend the current disposition engine into the sole migration-map
  validator and converter. The new script only parses arguments and calls this
  engine; it may not contain conversion or validation logic.
- [ ] Convert every mapped unversioned/v2/v3 node to v4 modules and sources,
  `.machine.`/`.llm.` public IDs to `.interface.`, existing Python symbols to
  process-binding selectors, and every legacy field to its reviewed target.
- [ ] Preserve interface/helper semantics, runtime requirements, discovery,
  authority, installed-source classes, generated artifacts, mapped v4 search
  semantics, and validator/hook coverage. Retire authored conformance fields
  only after their substantive-rule dispositions are complete.
- [ ] Write candidates only to a newly created temporary directory. Reject an
  existing output directory, ambiguous ownership, collisions, orphan sources,
  traversal, unresolved references, and unmapped active paths.
- [ ] Compare pre/post public graph and runtime-dependency projections. Run the
  conversion twice and require the second result to be a no-op.
- [ ] Materialize the complete candidate in an isolated temporary Git checkout
  where every map-authorized created path is tracked. Run repository validators
  and the blueprint hook there; fail if any mapped path is absent or untracked.
- [ ] Run:

  ```bash
  scripts/migrate-blueprints-v4.py --check-map --temporary-output
  python3 -m pytest -o pythonpath=src -q tests/test_interface_injection_migration.py tests/test_typed_blueprint_schemas.py tests/test_officina_blueprint_graph.py
  ```

Stop for review of the map, candidate tree, and exact cutover path list. Record
an authorized committed rollback point before Task 5.

## Task 5: Perform one atomic repository cutover

**Modify only paths authorized by the migration map**

- live blueprints and sidecars;
- `skill-maker`, `refactor-skills`, validators, hooks, standards, and generated
  artifacts;
- dispatcher callers, injected guidance, installed-source adapters, blueprint
  search, and installer dependency consumption;
- runtime, projection, certification, and drift entrypoints;
- replace the retained pre-v4 `machine-module.schema.json` and
  `behavior-source.schema.json` routes with the staged v4 schemas;
- rename `skill-audit` to `skill-certifier` and migrate its public IDs.

- [ ] Regenerate the candidate tree from the reviewed map and copy only mapped
  outputs. Inspect the exact diff.
- [ ] Switch authoring, validation, search, runtime dependencies, dispatcher,
  projection, certification, drift, and all callers to the same v4 graph in one
  window. No old/new mixed repository state is supported between commits.
- [ ] Treat Tasks 5–6 as a maintenance window. V4 dispatch remains fail-closed
  and unavailable until Task 6 completes final verification, commits the final
  state, and issues fresh certificates; do not add an interim compatibility
  runtime or intermediate certificates.
- [ ] Preserve `SKILL.md` discovery as module discovery and bind it to the
  gateway behavioral source. A skill is not a node type or schema profile.
- [ ] Change cross-module authorization to module exports while preserving
  source-declared direct dependencies and helper authorization.
- [ ] Rename the existing audit writer into `skill-certifier`; do not run two
  writers or dual-write health and certificate records.
- [ ] Validate installed-source version combinations named by the map;
  inactive cache versions are ignored, while an unsupported active version
  fails with exact remediation rather than legacy execution.
- [ ] Stage exactly the map-authorized cutover paths before running the focused
  suites from Tasks 1–4, validators, and the blueprint hook. Assert that no
  mapped output remains untracked or absent from the validator mirror.
  Inspect and commit the mapped cutover only with user authorization.

## Task 6: Remove competing authorities and adopt the architecture

**Remove only after map and zero-reference proof**

- legacy node-type schemas and type-specific graph/hash/dispatcher code;
- obsolete machine/LLM interface namespaces and aliases;
- the old `skill-audit` public surface and legacy health readers/writers;
- authored conformance/admissibility schemas, operations, profiles, results,
  fixtures, and evaluator identities whose rules were dispositioned in Task 0;
- `docs/audit_and_drift.md` after all live references move.

**Retain**

- the migration map, converter, and regression test as adoption evidence;
- the thin migration-map CLI;
- historical plans marked superseded;
- all preserved contract, helper, runtime, search, installer, validation,
  security, and installed-source behavior.

- [ ] Run the migration engine's active-reference check. Every remaining old
  term or path must be classified as historical evidence or removed.
- [ ] Remove legacy readers outside the migration engine, obsolete authored conformance authority,
  and legacy health/audit surfaces. Port still-valid tests before deleting old
  test files; never delete a safety case merely because its old schema is gone.
- [ ] Update `docs/architecture.md`, `docs/certification_and_drift.md`,
  `docs/skill-blueprints.md`, `docs/blueprint_search.md`, logical-resource
  documentation, and active contributor/skill documentation.
- [ ] Build the final graph and require exactly `module` and
  `behavioral_source`, complete ownership/exports/dependencies, authorized
  cross-module edges, and an acyclic certification projection.
- [ ] Run one final verification ladder:

  ```bash
  scripts/migrate-blueprints-v4.py --check-map --check-active-references
  python3 scripts/run-python-tests.py --suite full --verbose
  python3 validators/runner.py
  bash .githooks/skill/check-blueprints
  git diff --check
  git diff --cached --check
  ```

- [ ] Inspect the exact final diff and commit only with user authorization.
  Re-certify from that committed state and require clean drift with matching
  node, dependency, basis, provenance, signature, history, machine, and
  certifier evidence.
- [ ] Only then mark `docs/architecture.md` adopted and this plan complete.

## Completion criteria

- The migration map has no unresolved active declaration, caller, installed
  source, generated artifact, validator, hook, document, or safety rule.
- One graph, hash/currentness implementation, certifier, binding compiler,
  projection, dispatcher, and migration engine remain.
- The only node kinds are `module` and `behavioral_source`; skill discovery is a
  module property and all interfaces are source-owned contracts exported by
  modules.
- Certification alone establishes admissibility/conformance and drift is
  read-only.
- Existing security, helper, runtime, search, installer, validation, discovery,
  and analysis behavior passes its retained tests.
- The final verification ladder, fresh certification, and drift all pass after
  legacy authority removal.
