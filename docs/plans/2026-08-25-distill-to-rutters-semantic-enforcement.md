# Distill-to-Rutters Semantic Enforcement Plan

> **For agentic workers:** implement Phase A in the current checkout. Do not begin Phase B until its public-runtime prerequisite is verified. Use `superpowers:test-driven-development` for implementation and request separate authorization before staging or committing.

**Goal:** Preserve the observable job of a source Markdown instruction while making the generated Rutter own every enforceable algorithmic decision. Human or external judgment may remain outside automation, but the Rutter must own when that judgment is requested, what evidence is accepted, and which transition the result authorizes.

**Architecture:** Keep `SKILL.md` as a one-stage router and keep each stage's substantive work in its own Markdown interface. Add a small deterministic artifact-contract module for parsing envelopes, hashing exact files, checking prerequisite freshness, validating typed outcomes, and choosing routes. Keep semantic interpretation in the stage interfaces and require explicit user validation of every stage artifact. Split delivery into Phase A, which can harden the distillation protocol now, and Phase B, which is blocked until the live public Rutter/Compass API can construct and bind the generated dispenser.

**Tech stack:** Markdown stage interfaces, schema-v6 Officina blueprints, a private Python artifact-contract helper, pytest contract and trace tests, and repository blueprint generators/validators.

## Goals and corresponding changes

| Goal | Change intended to achieve it |
|---|---|
| G1 — Complete semantic input | Compute a recursive, cycle-safe closure of all behavior-defining references, with authority and conflict resolution, before decomposition. |
| G2 — Rutter-owned algorithm | Require each normative behavioral obligation to map to a concrete public Rutter mechanism and a falsifying trace. |
| G3 — Sound validation gates | Bind user approval to the SHA-256 of exact artifact bytes; validate typed outcomes and transitive prerequisite freshness before routing. |
| G4 — Rutter-owned orchestration | Require coordinator transitions to authorize starts, joins, retries, cancellation, aggregation, release, and failure propagation across Voyages. |
| G5 — Preserve judgment authority | Record the original decision owner and require an interactive/external evolution plus evidence validation; never silently automate it. |
| G6 — Honest runtime compatibility | Stop at a typed design block on current `master`; do not invent missing public runtime or Compass construction APIs. |
| G7 — Verify the delivered entrypoint | Generate all derived files first, then write and invoke the exact final entrypoint; permit no mutation afterward. |
| G8 — Falsifiable equivalence | Separate structural contract tests from executable good/bad trace fixtures and a user-adjudicated live semantic comparison. |

## Non-negotiable constraints

- Preserve the original Markdown and unrelated dirty work.
- Do not add `status.md` or another mutable ledger.
- Every stage writes its result to its designated file and pauses for explicit user validation of that file's reported digest.
- A user may reject any artifact. Approval never converts a gap, partial result, stale result, or failed result into success.
- Public Markdown must not name private runtime paths or implementation filenames.
- `voyage_dispenser.py` remains a readable declaration of Rutters, evolutions, and transitions. Complex operations and validators go in `voyage_dispenser_support.py`.
- The deterministic artifact helper validates artifact shape and routing claims; it does not claim to understand arbitrary Markdown semantics.
- No Rutter-core redesign or compatibility shim belongs to this plan.
- Staging and commits are out of scope unless separately authorized.

## Artifact protocol used by every stage

Each Markdown artifact begins with a fenced YAML envelope containing:

```yaml
schema_version: distill-to-rutters/v1
stage: breakdown
outcome: breakdown-ready
prerequisites:
  - kind: source
    path: path/to/source.md
    sha256: <digest-of-exact-file-bytes>
body_schema: breakdown/v1
```

An artifact prerequisite additionally records `kind: artifact`, its `stage`, and its `schema_version`. Source and `deliverable` prerequisites are digest-checked leaves; artifact prerequisites are parsed and traversed recursively. `deliverable` is reserved for generated files such as the one-line entrypoint that intentionally have no envelope. All paths resolve to non-escaping real paths inside the repository; reject symlink escapes. The artifact does **not** contain its own digest. After writing it, the gateway computes SHA-256 over the complete file bytes exactly as stored. The user validates `(artifact path, full-file SHA-256, typed outcome)`. The next invocation supplies that approved digest and an explicit `approve` or `reject` decision; the gateway recomputes it and every prerequisite digest before routing. Identity is the raw byte sequence on disk, with no line-ending normalization or excluded fields.

Each artifact also contains one fenced `distill-contract` YAML block validated by its stage-body schema. Human-readable explanation may surround it, but the machine block is authoritative for required rows and identifiers. Schemas are `breakdown/v1`, `assignment/v1`, `graph/v1`, `logic-validation/v1`, `implementation-design/v1`, `implementation-report/v1`, `entrypoint/v1`, and `verification/v1`.

The helper returns one of these route-level results:

- `accepted`: schema valid, the outcome is a success for that stage, prerequisites are current, and the approval digest matches.
- `gap`: a semantic or design gap; non-advancing.
- `partial`: work or evidence is incomplete; non-advancing.
- `rejected`: the user rejected the exact artifact; non-advancing.
- `stale`: the artifact or earliest transitive prerequisite changed; non-advancing.
- `failed`: a product assertion or verification failed; non-advancing.
- `blocked`: execution could not be attempted because a named external capability or environment prerequisite is absent; non-advancing and distinct from failure.

Stage outcomes are fixed before any gateway routing changes:

| Stage | Success outcome | Non-success outcomes |
|---|---|---|
| breakdown | `breakdown-ready` | `breakdown-gap`, `partial`, `failed` |
| assign-rutters | `assignment-ready` | `assignment-gap`, `partial`, `failed` |
| extract-evolutions | `graph-ready` | `graph-gap`, `partial`, `failed` |
| validate-logic | `logic-captured` | `logic-gap`, `partial`, `failed` |
| design-implementation | `design-ready` | `design-gap`, `design-blocked`, `partial`, `failed` |
| implement | `implemented` | `implementation-gap`, `implementation-blocked`, `partial`, `failed` |
| finalize | `entrypoint-ready` | `entrypoint-gap`, `partial`, `failed` |
| verify | `verified` | `verification-failed`, `verification-blocked`, `partial` |

`rejected` and `stale` apply to every stage. Unknown stage/outcome pairs cannot route.

Routing has three distinct cases. Initial source preflight bootstraps `breakdown` without a prior artifact, after validating the source path and repository boundary. An `accepted` result alone may advance to the next stage. `gap`, `rejected`, and `stale` may authorize only a non-advancing repair route to the current or earliest owning stage; `partial`, `failed`, and `blocked` stop until the reported condition changes or the user explicitly starts the owning stage again.

For source `<source-dir>/<source-stem>.md`, the distillation workspace is `<source-dir>/<source-stem>_distillation/`. Its fixed sequence is `01_breakdown.md`, `02_rutter_assignment.md`, `03_evolutions_and_transitions.md`, `04_logic_validation.md`, `05_implementation_design.md`, `06_implementation_report.md`, `07_entrypoint.md`, and `08_verification.md`. Generated implementation files are `<owning-skill>/_rtx/voyage_dispenser.py` and `<owning-skill>/_rtx/voyage_dispenser_support.py`. The final entrypoint is `<source-dir>/<source-stem>_distilled.md`.

## Phase A — Protocol hardening possible on current `master`

### Task 0: Establish a coherent experimental baseline

**Goal:** Make later changes reproducible without advertising the unproven skill as stable.

**Files:**

- Modify: `skills/distill-to-rutters/blueprint.yaml`
- Account for every existing file under `skills/distill-to-rutters/`
- Regenerate: `skills/distill-to-rutters/SKILL.md`
- Regenerate: `references/blueprint-schema/runtime_dependencies.json`
- Regenerate: `docs/skills.md`
- Regenerate: `docs/contributors/README.md`

- [ ] Record the complete new-node closure and distinguish unrelated modifications from this node's generated projections.
- [ ] Set maturity to `experimental` immediately.
- [ ] Ensure the root blueprint owns every current instruction, source blueprint, and test; later tasks add `_rtx`, schemas, and fixtures.
- [ ] Work from an inventory-clean `master` checkout. First run `dispatcher --caller-skill skill-certifier skill-maker._rtx.interface.sync-blueprints --check`; if nested repositories or duplicate module IDs still block inventory, record the exact external blocker and do not claim Phase A complete.
- [ ] Regenerate with `dispatcher --caller-skill skill-certifier skill-maker._rtx.interface.sync-blueprints`, then rerun the same interface with `--check`; do not hand-edit generated blocks.
- [ ] Run focused skill tests. Enumerate untracked paths with `git ls-files --others --exclude-standard -- skills/distill-to-rutters docs/plans/2026-08-25-distill-to-rutters-semantic-enforcement.md`; run `git diff --no-index --check /dev/null <exact-path>` for each, treating empty whitespace diagnostics—not the expected nonzero difference exit—as clean. Once separately authorized staging makes the baseline tracked, use ordinary `git diff --check` as well.
- [ ] If the baseline cannot be isolated from existing generated-file changes, stop and request scope guidance; do not create a partial node commit.

### Task 1: Implement the deterministic artifact and routing contract

**Goal:** Achieve the mechanically enforceable portion of G3 before relying on prose promises.

**Files:**

- Create: `skills/distill-to-rutters/_rtx/artifact_contract.py`
- Create: `skills/distill-to-rutters/_rtx/interface.py`
- Create: `skills/distill-to-rutters/_rtx/__init__.py`
- Create: `skills/distill-to-rutters/_rtx/blueprint.yaml`
- Create: `skills/distill-to-rutters/_rtx/blueprints/rtx-artifact-contract.yaml`
- Create: `skills/distill-to-rutters/references/artifact-envelope.schema.json`
- Create: `skills/distill-to-rutters/references/breakdown-body.schema.json`
- Create: `skills/distill-to-rutters/references/assignment-body.schema.json`
- Create: `skills/distill-to-rutters/references/graph-body.schema.json`
- Create: `skills/distill-to-rutters/references/logic-validation-body.schema.json`
- Create: `skills/distill-to-rutters/references/implementation-design-body.schema.json`
- Create: `skills/distill-to-rutters/references/implementation-report-body.schema.json`
- Create: `skills/distill-to-rutters/references/entrypoint-body.schema.json`
- Create: `skills/distill-to-rutters/references/verification-body.schema.json`
- Create: `skills/distill-to-rutters/tests/test_artifact_contract.py`
- Modify: `skills/distill-to-rutters/blueprint.yaml`
- Modify: `skills/distill-to-rutters/SKILL.md`
- Modify: `skills/distill-to-rutters/blueprints/gateway.yaml`
- Modify: `skills/distill-to-rutters/instructions/breakdown.md`
- Modify: `skills/distill-to-rutters/instructions/assign-rutters.md`
- Modify: `skills/distill-to-rutters/instructions/extract-evolutions.md`
- Modify: `skills/distill-to-rutters/instructions/validate-logic.md`
- Modify: `skills/distill-to-rutters/instructions/design-implementation.md`
- Modify: `skills/distill-to-rutters/instructions/implement.md`
- Modify: `skills/distill-to-rutters/instructions/finalize.md`
- Modify: `skills/distill-to-rutters/instructions/verify.md`
- Modify: all eight stage source blueprints under `skills/distill-to-rutters/blueprints/`

The production helper exposes pure functions equivalent to:

```python
parse_envelope(path) -> ArtifactEnvelope
sha256_file(path) -> str
validate_artifact(path, expected_stage) -> ValidationResult
check_freshness(path) -> FreshnessResult
decide_route(stage, outcome, approval_digest, user_decision, artifact_path) -> RouteDecision
```

- [ ] Register source `distill-to-rutters._rtx.source.artifact-contract` in `_rtx/blueprint.yaml`, with its process binding in `_rtx/blueprints/rtx-artifact-contract.yaml`; export `distill-to-rutters._rtx.interface.validate-and-route@1` to the `distill-to-rutters` caller only. Add the `_rtx` child and dependency/version edge to the root blueprint.
- [ ] Give the interface inputs `artifact_path`, `expected_stage`, `approved_digest`, and `user_decision: approve|reject`; return JSON containing `status`, `artifact_digest`, `outcome`, `authorized_route`, and `earliest_stale_prerequisite`.
- [ ] Declare that interface/version in `blueprints/gateway.yaml` under `uses_interfaces`, producing the edge `distill-to-rutters.source.gateway -> distill-to-rutters._rtx.interface.validate-and-route@1`. Make `SKILL.md` invoke the injected interface before every post-bootstrap route; permit advancement only for `accepted`, and permit only the defined owning-stage repair routes otherwise. Do not expose a private filesystem path.
- [ ] Add the source-preflight bootstrap route and non-advancing repair routes described above. Test that neither bootstrap nor repair can skip a stage or authorize advancement.
- [ ] Update all eight live stage instructions—not only their blueprints—to write the required envelope, name their allowed outcomes, compute no self-digest, report the gateway-computed digest, and ask the user to validate that exact `(path, digest, outcome)` tuple.
- [ ] Define all eight body schemas and validate their required machine-readable rows: context closure, assignment/orchestration, evolution graph, enforcement matrix, public-interface design, implementation trace map, entrypoint binding, and verification evidence.
- [ ] RED: reject malformed envelopes or body contracts, duplicate/missing fields, unknown outcomes, mismatched stages, wrong approval hashes, explicit rejection, repository/symlink escapes, changed direct or transitive prerequisites, cycles, and advancement from non-success outcomes.
- [ ] Implement the parser, raw-byte hash, recursive freshness walk with cycle detection, fixed outcome registry, and pure route decision.
- [ ] Report the earliest stale prerequisite deterministically; preserve stale files but make them unusable.
- [ ] Update every producer blueprint before enabling the gateway route matrix.
- [ ] GREEN: run `pytest -q skills/distill-to-rutters/tests/test_artifact_contract.py` and the existing routing tests.

### Task 2: Close the normative context recursively

**Goal:** Achieve G1 and prevent decomposition of an incomplete semantic source.

**Files:**

- Modify: `skills/distill-to-rutters/instructions/breakdown.md`
- Modify: `skills/distill-to-rutters/blueprints/instructions-breakdown.yaml`
- Modify: `skills/distill-to-rutters/tests/test_distill_to_rutters_routing.py`
- Create: `skills/distill-to-rutters/tests/fixtures/context-closure/root.md`
- Create: `skills/distill-to-rutters/tests/fixtures/context-closure/chain.md`
- Create: `skills/distill-to-rutters/tests/fixtures/context-closure/cycle.md`
- Create: `skills/distill-to-rutters/tests/fixtures/context-closure/generated-normative.md`
- Create: `skills/distill-to-rutters/tests/fixtures/context-closure/conflict.md`

- [ ] Define fixed-point traversal: resolve every referenced instruction, schema, standard, template, asset, and interface that can change behavior; recurse until no new reference remains; record visited identities to terminate cycles.
- [ ] Record `path`, `digest`, `authority` (`normative` or `informative`), provenance (`source` or `generated projection`), `why behavior-defining`, and `resolution`.
- [ ] Resolve conflicts by source authority, not generated status. A generated file may expose normative behavior but must name its governing source; contradictory authorities yield `breakdown-gap` for user resolution.
- [ ] Give every normative item a stable obligation ID. `breakdown-ready` is impossible while a normative reference or conflict is unresolved.
- [ ] RED/GREEN instruction and body-schema tests require representations for a reference chain, cycle, missing reference, generated normative projection, and contradictory authorities. Task 5 supplies the fixture-specific behavioral oracle.
- [ ] Write `01_breakdown.md`, report its full-file digest, and pause for user validation.

### Task 3: Define Rutter-owned decomposition and orchestration

**Goal:** Achieve G4 before extracting per-Rutter evolution graphs.

**Files:**

- Modify: `skills/distill-to-rutters/instructions/breakdown.md`
- Modify: `skills/distill-to-rutters/instructions/assign-rutters.md`
- Modify: `skills/distill-to-rutters/blueprints/instructions-breakdown.yaml`
- Modify: `skills/distill-to-rutters/blueprints/instructions-assign-rutters.yaml`
- Modify: `skills/distill-to-rutters/tests/test_distill_to_rutters_routing.py`

- [ ] Require one Rutter when parts are not behaviorally independent. Shared logic may share a Rutter only when state and transition semantics are identical.
- [ ] For multiple Voyages, require a coordinator Rutter contract covering starts, dependencies, joins, aggregate results, retries, cancellation, failure propagation, authorization, and release.
- [ ] The dispenser may mechanically execute an authorized action, but may not choose ordering, branching, retry, cancellation, join, or release policy. Final-result validation does not substitute for transition authorization.
- [ ] Map every cross-part obligation to a coordinator transition and evidence checked before advancement.
- [ ] Write `02_rutter_assignment.md`, report its digest, and pause for user validation.
- [ ] RED/GREEN instruction and assignment-schema tests require fields for inseparability, independent workflows with a join, partial failure, and retry ownership. Task 5 supplies the fixture-specific behavioral oracle.

### Task 4: Make semantic capture a capability-checked enforcement claim

**Goal:** Achieve G2 and G5 without claiming mechanisms the public runtime cannot express.

**Files:**

- Modify: `skills/distill-to-rutters/instructions/extract-evolutions.md`
- Modify: `skills/distill-to-rutters/instructions/validate-logic.md`
- Modify: `skills/distill-to-rutters/blueprints/instructions-extract-evolutions.yaml`
- Modify: `skills/distill-to-rutters/blueprints/instructions-validate-logic.yaml`
- Create: `skills/distill-to-rutters/tests/fixtures/enforcement/good.md`
- Create: `skills/distill-to-rutters/tests/fixtures/enforcement/missing-validator.md`
- Create: `skills/distill-to-rutters/tests/fixtures/enforcement/automated-judgment.md`
- Create: `skills/distill-to-rutters/tests/fixtures/enforcement/unowned-coordinator.md`
- Create: `skills/distill-to-rutters/tests/fixtures/enforcement/unavailable-capability.md`
- Create: `skills/distill-to-rutters/tests/test_enforcement_contract.py`

- [ ] Define an enforcement matrix with obligation ID, original decision owner, automation permission, public runtime capability/version, owning evolution, exact mechanism, precondition, postcondition, failure result, observable evidence, positive trace, and negative trace.
- [ ] Permit `logic-captured` only when every normative behavioral obligation has a mechanism verified against the current public API. Prompt text, operation names, wrapper prose, and schema shape alone do not count.
- [ ] A human/LLM/external decision remains owned by that actor. A Rutter evolution requests it, validates evidence, and exclusively authorizes the resulting transition; deterministic automation cannot substitute its answer.
- [ ] A preserved wrapper requirement is a constraint, not an enforcement class. If no public mechanism can request and observe it, return `logic-gap`.
- [ ] Write and validate `03_evolutions_and_transitions.md`, then `04_logic_validation.md`; report each digest and pause at each gate.
- [ ] RED/GREEN tests reject missing validators, schema-only semantic checks, automated human judgment, unowned coordinator decisions, and capabilities absent from the live API.

### Task 5: Add honest, falsifiable acceptance layers

**Goal:** Achieve the Phase-A portion of G8 without pretending a generic parser proves arbitrary semantic equivalence.

**Files:**

- Create: `skills/distill-to-rutters/tests/fixtures/scenarios/inseparable/source.md`
- Create: `skills/distill-to-rutters/tests/fixtures/scenarios/inseparable/good-contract.md`
- Create: `skills/distill-to-rutters/tests/fixtures/scenarios/inseparable/missing-validator.md`
- Create: `skills/distill-to-rutters/tests/fixtures/scenarios/inseparable/oracle.yaml`
- Create: `skills/distill-to-rutters/tests/fixtures/scenarios/multipart/source.md`
- Create: `skills/distill-to-rutters/tests/fixtures/scenarios/multipart/good-contract.md`
- Create: `skills/distill-to-rutters/tests/fixtures/scenarios/multipart/missing-join.md`
- Create: `skills/distill-to-rutters/tests/fixtures/scenarios/multipart/oracle.yaml`
- Create: `skills/distill-to-rutters/tests/fixtures/scenarios/judgment/source.md`
- Create: `skills/distill-to-rutters/tests/fixtures/scenarios/judgment/good-contract.md`
- Create: `skills/distill-to-rutters/tests/fixtures/scenarios/judgment/automated-judgment.md`
- Create: `skills/distill-to-rutters/tests/fixtures/scenarios/judgment/oracle.yaml`
- Create: `skills/distill-to-rutters/tests/_scenario_oracle.py`
- Create: `skills/distill-to-rutters/tests/test_distillation_scenarios.py`
- Modify: `skills/distill-to-rutters/blueprint.yaml`
- Modify: `skills/distill-to-rutters/instructions/verify.md`

- [ ] Layer 1 tests instruction contracts: routing text, required fields, stage ordering, and public/private boundary.
- [ ] Layer 2 tests the production parser, digest chain, outcome registry, and route function directly.
- [ ] Layer 3 parses known-good and independently mutated enforcement contracts and compares them with fixture-specific obligation/trace oracles in `_scenario_oracle.py`. It proves artifact claims for these fixtures, not execution by a runtime absent from current `master`.
- [ ] Phase-A mutations remove a semantic validator, omit a join, automate human judgment, or stale a prerequisite. Each fails its artifact validator or fixture oracle. Move executable Rutter/Voyage mutations to Phase B.
- [ ] Keep live agent/user comparison separate from pytest. It is acceptance evidence, not a deterministic semantic oracle.
- [ ] Root blueprint ownership explicitly includes every helper, schema, fixture, and test created in Tasks 1–5.

### Task 6: Probe the public runtime and write the design result

**Goal:** Achieve the honest blocking portion of G6 within Phase A.

**Files:**

- Modify: `skills/distill-to-rutters/instructions/design-implementation.md`
- Modify: `skills/distill-to-rutters/blueprints/instructions-design-implementation.yaml`
- Modify: `skills/distill-to-rutters/SKILL.md`
- Modify: `skills/distill-to-rutters/blueprints/gateway.yaml`
- Modify: `skills/distill-to-rutters/blueprint.yaml`
- Modify: `skills/distill-to-rutters/tests/test_distill_to_rutters_routing.py`
- Create: `skills/distill-to-rutters/tests/test_runtime_compatibility.py`

- [ ] Probe for:

- a public concrete Rutter construction contract;
- a public Voyage/VoyageDispenser construction and execution contract;
- an exact public binding or construction handoff accepted by `using-compass` (an interface/version name is insufficient); and
- one real bound instance advancing through an authorized public transition (dispatcher dry-run is insufficient).

- [ ] Define the stage behavior: during a real distillation, write `<source-dir>/<source-stem>_distillation/05_implementation_design.md` with `design-ready` only if every probe passes. Otherwise use `design-blocked`, name exact missing exports, report its digest, and pause for user validation. Do not add core exports, adapters, or shims.
- [ ] In Phase A itself, do not fabricate a live `05` artifact without an approved `01`–`04` chain. Instead, `test_runtime_compatibility.py` deterministically probes the checked-out public exports and Compass binding contract and asserts the current result is `design-blocked` with the missing capabilities named.
- [ ] Update the authored router and routing-test manifest to use the fixed artifact sequence and safe order `implement -> finalize -> verify`; unknown or old filenames cannot authorize routing.

### Phase A terminal result

On current `master`, Phase A must end as `hardening-complete; runtime-blocked`. The protocol, artifacts, routing, fixture oracles, and deterministic capability probe are tested; no live `05` artifact is claimed without an approved run. Dispenser implementation and Compass handoff remain unclaimed. This is not a successful distillation release, and maturity remains `experimental`.

## Phase B — Begins only after the Task 6 probe returns `design-ready`

### Task 7: Implement against verified public capabilities

**Goal:** Achieve G6 and produce a transparent Rutter implementation.

**Files:**

- Modify: `skills/distill-to-rutters/instructions/implement.md`
- Modify: `skills/distill-to-rutters/blueprints/instructions-implement.yaml`
- Generate: `<owning-skill>/_rtx/voyage_dispenser.py`
- Generate: `<owning-skill>/_rtx/voyage_dispenser_support.py`

- [ ] Put only readable Rutter declarations, evolutions, transitions, and composition in `voyage_dispenser.py`; put validators and operational complexity in `voyage_dispenser_support.py`.
- [ ] Ensure every enforcement row names its implemented symbol and executable trace. Support code may implement mechanics but may not choose routes outside Rutter authorization.
- [ ] Put the dispenser CLI invocation in `voyage_dispenser.py`'s `main` and exercise the actual public argument/result contract.
- [ ] Write `<source-dir>/<source-stem>_distillation/06_implementation_report.md`, report its digest, and pause for user validation.

### Task 8: Finalize generated projections before the entrypoint

**Goal:** Prevent later generation from invalidating the delivered interface.

**Files:**

- Modify: `skills/distill-to-rutters/instructions/finalize.md`
- Modify: `skills/distill-to-rutters/blueprints/instructions-finalize.yaml`
- Modify: `skills/distill-to-rutters/instructions/verify.md`
- Modify: `skills/distill-to-rutters/blueprints/instructions-verify.yaml`
- Modify: `skills/distill-to-rutters/SKILL.md`
- Modify: `skills/distill-to-rutters/blueprints/gateway.yaml`
- Modify: `skills/distill-to-rutters/blueprint.yaml`
- Modify: `skills/distill-to-rutters/tests/test_distill_to_rutters_routing.py`
- Regenerate: the target skill's root and source-blueprint generated blocks
- Regenerate: `references/blueprint-schema/runtime_dependencies.json`
- Regenerate: `docs/skills.md`
- Regenerate: `docs/contributors/README.md`
- Create: `<source-dir>/<source-stem>_distilled.md`
- Write: `<source-dir>/<source-stem>_distillation/07_entrypoint.md`

- [ ] Regenerate target-skill blueprints, ownership, runtime dependencies, and documentation before creating the entrypoint candidate.
- [ ] Run `dispatcher --caller-skill skill-certifier skill-maker._rtx.interface.sync-blueprints --check` from an inventory-clean checkout. If inventory crosses nested worktrees or duplicate IDs, report `verification-blocked`; do not call the repository green.
- [ ] Create the one-line entrypoint only if the verified live Compass contract accepts it. Use its exact public binding handoff, not the obsolete `interface@version` guess.
- [ ] If an authorization or interaction wrapper cannot be represented by that handoff, report `entrypoint-gap`.
- [ ] Make the `07_entrypoint.md` envelope name `<source-dir>/<source-stem>_distilled.md` as a `kind: deliverable` leaf prerequisite with its raw-byte digest. Record candidate path, source outcome, and gateway interpretation; pause for user validation. Routing to verification rechecks both report and entrypoint digests.

### Task 9: Verify exact delivery and perform live acceptance

**Goal:** Achieve G7 and complete G8 against the exact delivered bytes.

**Files:**

- Write: `<source-dir>/<source-stem>_distillation/08_verification.md`

- [ ] Consume the user-approved candidate path and full-file digest from `07_entrypoint.md`; reject any different path or current digest before testing.
- [ ] In a disposable clean worktree, distill an explicitly named fixture through every user-validation gate. Discard target changes afterward; retain only evidence owned by this skill.
- [ ] Invoke that exact final entrypoint through the public Compass route and run positive and negative traces against the original fixture oracle.
- [ ] Execute Phase-B mutations that remove a live semantic validator, reverse a transition, omit a coordinator join, automate human judgment, and weaken a terminal result. Each must fail the runtime trace oracle.
- [ ] Have the user adjudicate obligations requiring semantic interpretation. A completed Voyage without semantic agreement is `verification-failed`.
- [ ] Run artifact-contract, routing, and scenario tests; blueprint sync; documentation and ownership checks; repository validators; and `git diff --check`.
- [ ] Write `08_verification.md` with exact commands, results, the approved candidate path/digest, trace evidence, and typed outcome. It is evidence outside the target skill's verified delivery closure.
- [ ] After writing the report, run all check-only repository gates and invoke the approved exact entrypoint once more as the final operation. Permit no later repository mutation. Any target-closure mutation returns to Task 8; any evidence/report mutation requires rerunning the final checks and invocation.
- [ ] Promote maturity only under separate scope after all public-runtime, exact-entrypoint, semantic-adjudication, and repository gates pass.

## Completion criteria

Phase A is complete only when:

- envelopes, raw-byte digests, transitive freshness, typed outcomes, and routing refusal are executable production behavior;
- normative context reaches a conflict-resolved fixed point;
- every claimed obligation identifies an available Rutter-owned mechanism and exclusive decision owner;
- every cross-Voyage algorithmic decision is assigned to a coordinator-Rutter mechanism in a schema-valid contract, or the artifact reports `logic-gap`; and
- structural, artifact-contract, and executable fixture-oracle tests pass.

The full skill is complete only when Phase B also proves:

- the public runtime can construct, bind, and advance the generated dispenser;
- executable good/bad Rutter and Voyage traces confirm the coordinator and enforcement contracts;
- `voyage_dispenser.py` transparently owns topology and algorithmic transitions while support code owns only mechanics;
- the exact `<original_name>_distilled.md` entrypoint is invoked after all generated mutations;
- deliberate semantic mutations fail; and
- the user validates the source-to-Rutter semantic comparison.
