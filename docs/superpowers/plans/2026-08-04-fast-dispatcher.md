# Fast Dispatcher Implementation Plan

> **Execution:** Same-session implementation authorized by the user. Follow TDD and make one final scoped commit only after all verification passes.

**Goal:** Make host dispatcher resolution a bounded, read-only authorization path backed by an atomically activated snapshot, with certification status advisory only.

**Architecture:** Add a versioned dispatcher snapshot store and a separate builder that derives route records from the canonical blueprint graph. Host dispatch loads an exact caller/target record and never inventories, rebuilds, hashes, invokes Git, or writes. Blueprint synchronization and managed-runtime installation call the builder directly before activating generated state.

**Tech stack:** Python 3.11+, JSON, existing Officina blueprint graph/authorization/process-binding APIs, pytest.

---

## Task 1: Specify snapshot failure and storage behavior

**Files:**
- Create: `tests/test_dispatcher_snapshot.py`
- Create: `src/officina/install/dispatch_snapshot.py`
- Modify: `src/officina/dispatcher/errors.py`

1. Write failing tests for an absent pointer, malformed pointer/manifest/route record, unsupported format or authorization-semantics version, containment violations, and an exact caller/target route hit.
2. Run `python3 scripts/run-python-tests.py tests/test_dispatcher_snapshot.py` and verify each new test fails for the missing implementation.
3. Implement immutable snapshot dataclasses, deterministic JSON encoding, confined reads, generation-directory writes, reload verification, and atomic `current.json` activation.
4. Add a structured `DispatcherSnapshotError` carrying a stable dispatcher error code and the direct builder repair command.
5. Rerun the focused tests until green, then refactor without expanding behavior.

## Task 2: Build complete authorization route records outside dispatch

**Files:**
- Modify: `tests/test_dispatcher_snapshot.py`
- Create: `src/officina/install/dispatch_snapshot_builder.py`
- Modify: `src/officina/dispatcher/catalog.py`

1. Add failing tests that build from the v5 authorization fixtures and compare snapshot route outcomes with `resolve_interface_authorization`, including owner, descendant, sibling, public, private, namespace, facade, terminal, missing caller, and denied caller cases.
2. Add a failing test proving candidate generation does not replace a working active snapshot when generation or reload validation fails.
3. Implement the direct builder: load and validate the repository graph once; enumerate eligible discoverable host callers and exported interfaces; resolve every candidate with the canonical authorization resolver; compact allowed routes; serialize normalized graph facts plus advisory certification decisions; validate the candidate; atomically activate it.
4. Reuse only explicit allowlisted catalog serialization helpers; remove time-based negative-decision expiry from generated snapshot data.
5. Provide `python -m officina.install.dispatch_snapshot_builder --repo-root ... --snapshot-root ...` as the repair/bootstrap entry point. It must not call dispatcher.
6. Run the focused snapshot tests until green.

## Task 3: Make host dispatch snapshot-only and fail closed

**Files:**
- Modify: `tests/test_officina_dispatcher.py`
- Modify: `tests/test_dispatcher_route_smoke.py`
- Modify: `src/officina/dispatcher/core.py`
- Modify: `src/officina/dispatcher/cli.py`

1. Add failing tests proving host dry-run and execution load the active route, reevaluate authorization, and compile arguments correctly.
2. Add operation-guard tests that make repository inventory, repository graph loading, Git/certification derivation, catalog writes, and network calls raise if touched on the host invocation path.
3. Add failing tests proving absent/malformed/stale snapshot state returns a structured error and never rebuilds or writes.
4. Change `_resolve_host_dispatch_metadata` and `_dispatch_host` to load exact snapshot route state before calling the existing resolver with an explicit graph and stored advisory certification view.
5. Keep the canonical graph-based resolver available for builders, validators, route-smoke tooling, and explicit test injection; remove all host-path calls to catalog rebuild and certification derivation.
6. Ensure every non-current certification decision becomes an `InvocationDiagnostic` warning and never a denial.
7. Run dispatcher unit and route-smoke tests until green.

## Task 4: Activate snapshots during synchronization and installation

**Files:**
- Modify: `skills/skill-maker/_rtx/tests/test_blueprint_tools.py`
- Modify: `skills/skill-maker/_rtx/_blueprint_syncer.py`
- Modify: `tests/test_install_lifecycle.py`
- Modify: `src/officina/install/managed_runtime.py`

1. Query the repository’s author-skill standards before editing the skill-owned syncer and record the applicable requirement IDs in code comments only where they clarify a non-obvious boundary.
2. Add failing syncer tests proving normal sync generates/activates a snapshot and `--check` validates generation without changing active state.
3. Add failing installation lifecycle tests proving snapshot build completes before runtime activation and a snapshot failure preserves the prior runtime pointer and prior dispatch snapshot.
4. Call the builder directly from blueprint synchronization and managed-runtime candidate construction; never route the builder through dispatcher.
5. Run the focused syncer and install lifecycle tests until green.

## Task 5: Correct architecture documentation

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/specs/2026-08-04-fast-dispatcher-design.md`

1. Replace statements that make certification an availability condition with the verified warning-only rule.
2. Replace short-lived negative cache and dispatcher rebuild descriptions with activation snapshot and fail-closed behavior.
3. Mark the approved design implemented only after Tasks 1-4 pass.
4. Run documentation validators relevant to the changed files.

## Task 6: Verify latency, correctness, and scope

**Files:**
- Modify or create only if needed: `tests/test_dispatcher_snapshot.py`

1. Run the focused dispatcher, snapshot, route-smoke, syncer, and install lifecycle suites.
2. Run the repository’s standard Python test runner.
3. Build a fresh snapshot, then benchmark fresh-process `dispatcher --dry-run` calls for one first route and repeated routes. Record median and p95; require p95 below 100 ms and median below 50 ms on the reference host, excluding snapshot construction and gateway execution.
4. Trace at least one dry-run and verify zero Git subprocesses, repository walks, graph construction, certification derivation, writes, and network calls on the dispatcher path.
5. Inspect `git status` and `git diff`; confirm unrelated pre-existing node-standard files and `interface-contract-review.md` are untouched and unstaged.

## Task 7: Commit the verified dispatcher change

1. Read and follow `superpowers:verification-before-completion` immediately before any completion claim.
2. Stage only the dispatcher snapshot implementation, its tests, installation/sync integration, and the two dispatcher design/architecture documents.
3. Review the staged diff and rerun the complete verification command against the staged tree.
4. Create one commit with a dispatcher-specific message. Do not push.
