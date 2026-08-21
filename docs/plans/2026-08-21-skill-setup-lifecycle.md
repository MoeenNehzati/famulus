# Skill Setup Lifecycle Implementation Plan

**Goal:** Add fast, blueprint-declared setup checks and durable setup/teardown orchestration through a lifecycle Rutter.

**Architecture:** Lifecycle projects blueprints to a skill graph and stores one receipt per lifecycle-bearing skill. A serialized lifecycle session gateway binds and advances one globally coordinated Rutter voyage across dispatcher processes; Markdown only performs returned instructions. Generic graph traversal lives in common.

**Spec:** `docs/plans/2026-08-21-skill-setup-lifecycle-design.md`

## Fixed decisions

- Lifecycle roles are paired annotations on exported Markdown interfaces, with canonical descriptions.
- Setup walks dependencies. Teardown walks set-up dependents and never automatically removes dependencies.
- `has-been-set-up` computes effective state from local receipts; it performs no remote checks.
- One installation-wide active voyage prevents concurrent lifecycle actions against shared dependencies.
- Reckonings, setup receipts, and skill resource provenance are separate state.
- Compass uses a serializable lifecycle session; no Python Rutter object crosses the dispatcher boundary.
- Shared flows have a dedicated registered owner and generated repository-level projections.
- Deterministic fixture tests run throughout; one fresh nested-Codex smoke runs at the end.

---

### Task 0: Build the isolated fixture harness

**Files:**
- Create: `tests/fixtures/lifecycle-session/` with fixture repository configuration and four dummy skills.
- Create: `tests/lifecycle_session_harness.py`
- Create: `tests/test_lifecycle_session_harness.py`

Use this fixture graph:

```text
fixture-setup-root -> fixture-plain-middle -> fixture-setup-leaf
fixture-plain-standalone
```

The root and leaf have paired instructions that print `I'm setup of <skill>.` and `I'm teardown of <skill>.`; the other skills have no lifecycle pair.

Implementation requirements:

- Copy the fixture skills plus required Officina source/references into a temporary repository; do not use symlinked module roots.
- Create a temporary `dispatcher` launcher that invokes worktree code with the fixture's exact `--repository-config`.
- Isolate `HOME`, `CODEX_HOME`, XDG roots, temp paths, and on Windows `USERPROFILE` and `LOCALAPPDATA`; use an explicit environment allowlist.
- Verify `codex debug prompt-input` includes all fixture skills and excludes the complete production skill inventory. Verify a dispatcher dry run resolves a fixture route.
- Do not copy credentials. A live smoke may pass an existing ambient credential channel without storing or logging it.

**Acceptance:** harness tests pass on supported platforms, repository configuration loads without symlink exceptions, and prompt discovery and dispatcher routing are proved separately.

### Task 1: Add lifecycle metadata, graph projection, and receipts

**Files:**
- Modify: `references/blueprint/behavioral-source.schema.json`
- Modify: `references/blueprint/schema.annotated-draft.json`
- Modify: `references/blueprint/schema-meta.json`
- Modify: `src/officina/blueprints/graph.py` and owned blueprint metadata.
- Create: `src/officina/common/directed_graph.py` and its common blueprint registration.
- Create: `src/officina/lifecycle/{__init__,graph,state}.py` and lifecycle blueprints.
- Add focused schema, graph, and state tests under `tests/`.

Implement:

- validation of exactly one exported setup/teardown pair per lifecycle-bearing skill;
- deterministic common traversal with explicit cycle paths;
- lifecycle-specific blueprint-to-skill projection;
- dependency-first setup targets and dependent-first teardown targets;
- strict confined receipts at `<state-root>/<skill>/setup.json`; and
- `lifecycle.interface.has-been-set-up`, returning effective local state for the invoking root.

The fixture must prove that setup of the root emits leaf then root, while teardown of the root emits only root. Teardown of the leaf must emit root then leaf through the non-lifecycle middle node.

**Acceptance:** focused tests pass, malformed receipts and cycles fail closed, and regenerated blueprints have no drift.

### Task 2: Implement the coordinated lifecycle Rutter session

**Files:**
- Create: `src/officina/lifecycle/{rutter,coordinator,session,gateway}.py` and owned blueprints.
- Modify: `skills/using-compass/SKILL.md` and its gateway blueprint/tests.
- Modify dispatcher runtime context only as needed to preserve the original caller as policy metadata.
- Add focused Rutter, coordinator, session, restart, and concurrency tests.

Implement one `SkillLifecycleRutter` whose immutable Charter contains root, operation, graph fingerprint, and ordered action records with exact instruction text and digest. Use Charter data to generate states; do not create setup and teardown subclasses.

The lifecycle session gateway must:

- accept and return finite JSON only;
- select or resume the installation-wide active voyage under a short coordinator lock;
- create a random claim ID and require it for every operation on the active voyage;
- report `busy` to callers without that claim ID, including otherwise matching requests;
- atomically issue each instruction with a revision and nonce, return `awaiting-evidence` rather than reissuing unresolved text, and tie corrected evidence to that nonce;
- rotate a lost claim only after explicit reconciliation of any outstanding instruction, never a timeout;
- reopen the Rutter for each process call;
- hold the coordinator lock across claim checking, one complete Python Rutter step, and locator updates, but never while the LLM performs Markdown;
- distinguish `start`, handle-bearing `step`, and confirmed `recover`, and replay terminal status for historical handle retries;
- reconcile active locators against authoritative Reckonings on entry;
- return only `instruction`, `awaiting-evidence`, `invalid`, `complete`, `faulted`, `uncertain`, or `busy`; and
- retain faulted/uncertain voyages for reconciliation while clearing completed active locators.

Update Compass to drive `lifecycle.interface.compass-session` through the bound voyage ID and claim ID rather than an in-memory object. The claim prevents accidental concurrent operation; it is not a security credential. Registry instances, Reckoning paths, and Charter construction remain private to lifecycle Python.

Before `define_states()` creates instructions or effects, validate the persisted Charter against the current graph, ordered targets, interface text digests, and derived confined receipt paths. Stale or malformed authority fails closed.

**Acceptance:** fixture tests prove claim-bearing restart recovery, single issuance per instruction nonce, invalid-evidence repair without re-execution, busy responses for competing callers, explicit lost-claim reconciliation, terminal-response replay, locator crash recovery, new voyage allocation after terminal completion, and receipt reconciliation after an interrupted write.

### Task 3: Add minimal flows, header injection, and installation

**Files:**
- Create: registered `src/officina/lifecycle_flow/` owner with canonical `setup-flow.md` and `teardown-flow.md` sources.
- Generate: `instructions/setup-flow.md` and `instructions/teardown-flow.md`.
- Modify: `skills/skill-maker/_rtx/_blueprint_syncer.py` and focused tests/blueprints.
- Modify: `skills/install-assistant-tools/_rtx/_install_scaffold.py` and focused tests/blueprints.
- Add flow and generated-header tests.

Inject exactly one short gate:

```markdown
Lifecycle: for teardown, follow `../../instructions/teardown-flow.md` for `<skill-id>`. Otherwise call `lifecycle.interface.has-been-set-up`; if false or setup was explicitly requested, follow `../../instructions/setup-flow.md` for `<skill-id>`. Continue only after the applicable flow completes.
```

The flow files only start the correct lifecycle session and use Compass until terminal status. Compass performs only a newly issued `instruction`, never repeats work for `awaiting-evidence`, repairs evidence from `invalid` issues, and stops on busy, faulted, or uncertain results. The flows contain no graph traversal or receipt mutation rules.

Header eligibility is graph-derived: root, leaf, and the transitive plain middle receive the gate; the standalone fixture does not. Installation must preserve the two relative flow paths.

**Acceptance:** generated-skill validation, flow tests, fixture prompt inspection, installer tests, and blueprint drift checks pass.

### Task 4: Bind list-manager as the canary

**Files:**
- Add `skills/list-manager/instructions/setup.md` and `teardown.md`.
- Add a small private provenance helper and tests under `skills/list-manager/_rtx/`.
- Update and regenerate list-manager blueprints and `SKILL.md`.

Setup checks `todo` and `triage` independently, creates only a list proven absent, records each created resource, and verifies final readability. Teardown removes only recorded resources, verifies the result, and clears provenance only after success. Neither instruction traverses blueprints or writes the generic receipt.

**Acceptance:** focused list-manager tests cover preservation of existing lists, partial setup recovery, provenance confinement, and safe teardown; route smoke and generated artifacts pass.

### Task 5: Run final acceptance and prepare adoption

**Files:**
- Create: `tests/test_skill_lifecycle_integration.py`
- Create: `docs/plans/skill-lifecycle-adoption-inventory.md`

Deterministic integration tests cover chains, diamonds, shared dependencies, non-lifecycle intermediates, cycles, interruption, busy coordination, setup skip, dependent-cascade teardown, and a new setup after teardown.

Then build a completely fresh isolated fixture session and run one `codex exec --json` smoke. Assert the event stream used the fixture dispatcher, ordered Rutter transitions and receipts are correct, setup is skipped on the second invocation, and root teardown leaves the leaf set up. Set the root up again, then explicitly tear down the leaf and verify the cascade runs root before leaf. Treat printed messages as secondary evidence.

Finally audit production skills for durable setup assumptions. Record evidence, dependencies, verification, teardown scope, and provenance needs. Names such as `init`, `connect`, or `install` are only search clues. Produce separate exact migration plans for accepted skills in dependency-first batches.

**Acceptance:** focused tests, configured shared tests, blueprint/generated-artifact checks, and the repository default gate pass. Environmental live-smoke failures are reported separately and never substituted for deterministic evidence.

## Completion criteria

- Normal invocation performs only the local effective-state check.
- Setup and teardown ordering matches the fixed graph semantics.
- Competing lifecycle requests cannot duplicate shared external actions.
- The dispatcher path is serializable and restart-safe.
- Generated instructions remain short.
- The isolated fixture and list-manager canary both pass before broader adoption.
