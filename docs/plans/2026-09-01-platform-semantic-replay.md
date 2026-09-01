# Platform Semantic Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replay only functional tests that implicitly reach the `famulus-paths` semantic boundary under Linux-hosted macOS and Windows policy models.

**Architecture:** Functional tests remain platform-independent. A context-local boundary observer records the exact passing pytest nodes that used implicit host policy during the existing serial portability task; the child pytest process publishes one alternate-model manifest. The repository runner adds that focused portability phase to the central precommit profile and launches fresh serial subprocesses for only the selected nodes. Native CI remains authoritative for physical operating-system behavior.

**Tech Stack:** Python 3.11+, `contextvars`, pytest protocol/report hooks, one atomic JSON manifest, and Officina repository checks.

**Spec:** `docs/plans/2026-09-01-platform-semantic-replay-design.md`

## Functional versus system-specific evidence

- Selected tests are ordinary functional tests. They contain no replay
  annotation, platform branch, alternate-platform parameter, or fault setup.
- The mechanism injects system specificity at the production boundary, not in
  the test. The same functional assertion runs under the Linux baseline and
  the applicable macOS and Windows policy models.
- A replay model proves only pure policy selected at that boundary. It does not
  prove real filesystem, process, pipe, socket, browser, keyring, permission,
  timing, or performance behavior.
- Explicit platform arguments remain boundary-conformance inputs and never
  trigger automatic replay.
- The central `precommit` suite runs a focused `tests:portability` phase
  after its combined phase, so the installed local hook performs replay
  automatically.

## Scope harness and three-digit LOC budget

This ledger is the harness against overscoping. It is an implementation
contract, not an estimate that may be silently exceeded. The implementation
may change only the 14 files below, may delete no file, and may introduce no
second boundary, framework, validator, standard, hook command, workflow, or
generated artifact. The plan document itself is not part of the implementation
diff.

The hard budget is **900 gross changed lines** across the implementation:
additions plus deletions, measured by `git diff --numstat` against the refreshed
implementation baseline. The per-file numbers below allocate 870 lines and
leave 30 lines of contingency. Contingency may be reassigned only among these
14 files and must be recorded in the task notes. Crossing 900 gross lines,
changing an unlisted file, exceeding one file's allocation by more than the
30-line total contingency, or discovering a required generated file is a stop
condition: do not broaden the patch; revise and re-review this plan first.

| File | Action | Exact planned delta | Add | Remove |
|---|---|---|---:|---:|
| `src/officina/platforms/__init__.py` | Create | Re-export only the public model, contract, context, and query functions from `model.py`; add no behavior. | 12 | 0 |
| `src/officina/platforms/model.py` | Create | Add the three model identities, immutable `famulus-paths` contract, compatibility host-name normalization, separate POSIX host-policy selection, one `ContextVar`, observation, replay context, and strict lookup errors. | 110 | 0 |
| `src/officina/platforms/blueprint.yaml` | Create | Register the module, own `__init__.py`, and relate it only to the model source blueprint. | 18 | 0 |
| `src/officina/platforms/blueprints/model.yaml` | Create | Register `platforms.source.model@1`; own `model.py`; export `platforms.interface.model@1` with `allow_all_modules: true` and no process binding. | 40 | 0 |
| `src/officina/dispatcher/platforms/__init__.py` | Modify | Remove the local platform mapping and delegate to the new compatibility normalizer while preserving the import path and unknown-token return behavior. | 6 | 8 |
| `src/officina/repository/checks/platform_replay.py` | Create | Add only the serial pytest options, per-item observation/report hooks, atomic manifest publication, replay context activation, and missing-boundary teardown failure. | 155 | 0 |
| `tests/test_platform_replay.py` | Create | Add focused model/context, dispatcher compatibility, serial discovery outcome, manifest, replay restoration, and missing-boundary diagnostic tests. | 190 | 0 |
| `src/officina/repository/checks/runner.py` | Modify | Replace the standalone portability-suite mapping and validator-only staged-view guard; add the focused phase, retained staged-view lifetime, plugin/manifest arguments, manifest validation, and serial alternate-model subprocesses using existing runner primitives. Remove no existing gate. | 95 | 12 |
| `tests/test_repository_test_checks.py` | Modify | Add focused runner tests for suite routing, staged/working views, commands, manifests, selectors, task results, failure propagation, and serial enforcement. | 105 | 0 |
| `src/officina/common/famulus_paths/__init__.py` | Modify | Replace the required-platform signatures and direct local policy selection with an optional platform routed through `boundary_model`; preserve explicit-token behavior and all existing errors. | 18 | 8 |
| `src/officina/common/famulus_paths/_get_interface.py` | Modify | Remove explicit `sys.platform` forwarding so the gateway takes the implicit boundary route; change nothing else. | 4 | 4 |
| `src/officina/common/blueprints/famulus-paths.yaml` | Modify | Replace the required/explicit-only platform contract and empty dependency/interface-use entries; make `platform` optional, add the pinned model-source dependency and both source/facet interface-use declarations, and document implicit versus explicit behavior. | 22 | 6 |
| `tests/test_officina_famulus_paths.py` | Modify | Add one annotation-free implicit functional pilot plus bounded macOS/Windows policy and explicit-observer compatibility cases. | 35 | 0 |
| `docs/ci-handbook.md` | Modify | Document local automatic replay, task labels, manifest failure behavior, bounded duplicate cost, and the semantic/native evidence boundary. | 22 | 0 |
| **Allocated implementation total** |  | **14 files; no deletions** | **832** | **38** |
| **Contingency** |  | **30 gross lines reserved across listed files** | **—** | **—** |
| **Hard ceiling** |  | **900 gross lines; total must remain three digits** | **—** | **—** |

No source file is deleted. The 38 removal lines are confined to replacing the
existing dispatcher mapping, runner routing/view conditions, required Famulus
platform selection, gateway forwarding, and Famulus blueprint metadata named
in the ledger.

At the end of every task, run both scope checks before committing:

```bash
git diff --name-only <implementation-baseline>
git diff --numstat <implementation-baseline>
```

The first output must be a subset of the ledger. The second must remain within
the recorded per-file allocation plus any explicitly assigned contingency and
at or below 900 gross lines. Tests or validators requesting an unlisted edit do
not authorize it; record the failure and stop for plan revision.

## Global constraints

- Support only canonical replay models `linux`, `macos`, and `windows`;
  reject unknown replay model names. Preserve the existing explicit Famulus
  contract: `darwin` selects macOS, `win32` selects Windows, and every other
  explicit token selects POSIX-style policy without observation.
- Keep host naming separate from policy selection: the dispatcher facade
  preserves unsupported host tokens, while implicit Famulus selection maps
  unsupported hosts to the `linux` model's POSIX policy.
- Register only the immutable `famulus-paths` contract, supporting alternate
  models `macos` and `windows`; add no dynamic registration API.
- Use one context-local replay state and always restore it with the returned
  `ContextVar` token in `finally`.
- Record only exact node IDs whose setup, call, and teardown all pass; exclude
  skips, xfail/XPASS items, and failed protocols.
- Per-item protocol state is in-process and the child manifest is
  schema-versioned, deterministic, and fail closed on malformed or conflicting
  data. The child writes only that manifest.
- Resolve contracts and replay groups inside the tested repository view. The
  parent runner consumes the child manifest without importing working-tree
  contracts.
- Replays are fresh, serial, Linux-only subprocesses with discovery disabled
  and separate cache, timing, and task labels.
- Enable replay by resolved task `tests:portability`, independent of the outer
  suite name. Preserve exact targeted selectors and start no replay after a red
  baseline.
- Keep the native CI matrix unchanged.
- Add no faults, platform-debt inventory, native-declaration scheme,
  canonical-standard revision, generic codec framework, or unrelated runtime
  migration.
- Refresh the clean implementation branch from current `master` before
  editing; the historical `codex/ci-integration-17aba16e` pointer is not an
  implementation baseline.

---

### Task 1: Minimal platform contract and context

**Files:**
- Create: `src/officina/platforms/__init__.py`
- Create: `src/officina/platforms/model.py`
- Create: `src/officina/platforms/blueprint.yaml`
- Create: `src/officina/platforms/blueprints/model.yaml`
- Modify: `src/officina/dispatcher/platforms/__init__.py`
- Create: `tests/test_platform_replay.py`

**Interfaces:**
- Produces:
  `PlatformModel(name: Literal["linux", "macos", "windows"])`;
  `BoundaryContract(boundary_id: str, supported_models: tuple[str, ...])`;
  `current_platform_name(token: str | None = None) -> str`;
  `boundary_model(boundary_id: str, *, explicit: str | None = None) -> PlatformModel`;
  `platform_replay(model_id: str, *, observer: Callable[[str], None] | None = None) -> ContextManager[None]`;
  and `supported_models(boundary_id: str) -> tuple[str, ...]`.
- Registered interface:
  `platforms.source.model.interface.python-api@1`, exported as
  `platforms.interface.model@1`, with `allow_all_modules: true` and no process
  binding.
- Contract: the only boundary ID is `famulus-paths`. Canonical replay model
  names are strict. Explicit Famulus tokens retain current behavior:
  `darwin -> macos`, `win32 -> windows`, and every other value selects
  POSIX-style policy without observation.

- [ ] Write focused failures in `tests/test_platform_replay.py` for canonical
  identities, unknown boundary/replay-model rejection, compatible explicit
  token mapping, implicit unsupported-host POSIX selection, explicit-input
  observer suppression, implicit observer notification, nested replay
  restoration, and restoration after an exception.
- [ ] Add a compatibility test proving
  `officina.dispatcher.platforms.current_platform_name()` delegates to the new
  normalizer while retaining its existing `linux`/`macos`/`windows`
  results and returns an unsupported host token unchanged.
- [ ] Run `pytest -q tests/test_platform_replay.py` and verify collection
  fails because `officina.platforms` does not exist.
- [ ] Add the module and behavioral-source blueprints. Own `__init__.py`
  at the module and `model.py` at the source; export the Python interface to
  all modules without a process binding so registered sources and the
  dispatcher compatibility facade share one authority.
- [ ] Implement the immutable fixed contract mapping and one `ContextVar`
  replay state. Keep contract storage process-global and immutable; expose no
  registration or mutation function. Keep the dispatcher-compatible host-name
  normalizer distinct from the internal host-policy selector: the former
  preserves unsupported tokens, while the latter maps them to the `linux`
  model's POSIX policy.
- [ ] Replace the dispatcher helper's normalization body with a delegating
  compatibility facade; keep its public import path and return values.
- [ ] Run `pytest -q tests/test_platform_replay.py` and verify the
  model/context cases pass.
- [ ] Run focused blueprint ownership and relationship validators for
  `src/officina/platforms`, repairing only ownership metadata required by the
  new package.
- [ ] Commit the Task 1 files.

### Task 2: Exact passing-node discovery plugin

**Files:**
- Create: `src/officina/repository/checks/platform_replay.py`
- Modify: `tests/test_platform_replay.py`

**Interfaces:**
- Consumes: `platform_replay()`, `supported_models()`, and implicit observer
  notifications from Task 1.
- Produces pytest options `--officina-platform-artifact-root`,
  `--officina-platform-run-id`, and
  `--officina-platform-replay-model`; and `manifest.json` mapping canonical
  model IDs to sorted exact node IDs.
- The artifact-root option names one dedicated run-private directory. The
  plugin derives `manifest.json` beneath it and passes that exact directory as
  `allowed_root`; it accepts no caller-selected manifest filename.
- Manifest schema:
  `{"schema_version": 1, "run_id": "run-8f3c2a", "models": {"macos": ["tests/test_paths.py::test_default"], "windows": ["tests/test_paths.py::test_default"]}}`.

- [ ] Add pytester failures proving that an unannotated implicit-boundary test
  is recorded, an untouched test is absent, and parametrized node IDs remain
  exact.
- [ ] Add protocol-outcome failures proving exclusion of setup failure, call
  failure, teardown failure, skip, xfail, and XPASS with xfail metadata.
- [ ] Add serial-protocol failures proving one test touching the same boundary
  repeatedly produces one node per model and observation state is restored
  between items and after exceptions.
- [ ] Add fail-closed failures for invalid protocol state, unknown boundaries,
  and unsupported model data in a child manifest.
- [ ] Implement protocol-scoped observation around each item's complete
  protocol with cleanup in `finally`. Use a `pytest_runtest_protocol`
  hookwrapper only to install/reset observation. Use a
  `pytest_runtest_makereport` hookwrapper to accumulate setup/call/teardown
  outcomes, `wasxfail`, and boundary observations in-process; retain an item
  only when all three phases pass without skip or xfail/XPASS metadata.
- [ ] Compute replay groups inside the child process so staged executions use
  staged contracts. At session finish, serialize canonical UTF-8 JSON and
  publish only
  `manifest.json` through
  `atomic_replace_bytes(path, data, allowed_root=artifact_root, mode=0o600)`
  with no non-atomic fallback.
- [ ] In replay mode, activate the requested model, disable discovery
  publication, and on its passing teardown report convert a selected test that
  did not reach an implicit boundary applicable to that model into a normal
  failed report with an explicit diagnostic. Test both pytest's nonzero exit
  status and that diagnostic; do not raise from the completed protocol wrapper.
- [ ] Run `pytest -q tests/test_platform_replay.py` and verify the serial
  discovery and replay cases pass.
- [ ] Commit the Task 2 files.

### Task 3: Focused portability runner and local gate

**Files:**
- Modify: `src/officina/repository/checks/runner.py`
- Modify: `tests/test_repository_test_checks.py`

**Interfaces:**
- Consumes: Task 2 plugin options and `manifest.json`.
- Produces a `tests:portability` phase after the combined phase in
  `SUITE_PHASES["precommit"]`; changes `SUITE_PHASES["portability"]` to
  that same focused phase; enables baseline discovery whenever the resolved
  phase is `tests:portability`; and produces follow-up task labels
  `tests:platform-replay:macos` and `tests:platform-replay:windows`.

- [ ] Write runner failures for the exact precommit run order
  `("combined", "tests:portability")`, plugin loading, a run-private
  manifest path and run ID, Linux-only follow-up, no follow-up after a red
  portability baseline,
  deterministic model order, exact node selectors, and discovery-disabled
  replay commands.
- [ ] Add hook-contract coverage proving the unchanged installed hook's
  `--suite precommit` command reaches the new focused phase through central
  suite policy; do not add a second shell command to the hook.
- [ ] Add failures proving replay uses the same staged or working execution root
  as the baseline, has a separate cache and JUnit timing path, appears as its
  own `_PhaseResult`, and propagates any nonzero status.
- [ ] Add a standalone
  `--suite full --task tests:portability --repository-view staged` failure
  proving explicit staged test-only runs prepare one staged mirror and keep it
  open through replay.
- [ ] Add targeted-selector coverage proving the manifest and replay command
  cannot expand beyond the baseline's exact selected nodes.
- [ ] Add manifest validation failures for absence after a green discovery run,
  malformed JSON, wrong schema/run ID, unknown model keys, duplicate nodes, and
  non-string node IDs.
- [ ] Append `tests:portability` to the central precommit phase tuple after
  pooled validators/shared tests and make the `portability` suite resolve to
  that same phase. Enable discovery only when the resolved phase is
  `tests:portability`, independent of whether the outer suite is
  `precommit`, `full`, or `portability`.
- [ ] Generalize staged-view preparation so an explicitly staged test-only run
  prepares the same staged mirror currently used by validator-containing runs.
  Do not alter auto-view selection for CI's working-tree task.
- [ ] Allocate a dedicated platform-replay artifact directory plus manifest,
  cache, timing, and Python-cache paths below the existing run-private artifact
  root. Register the new plugin through the existing pytest `-p` construction
  rather than a new launcher.
- [ ] Keep the `tests:portability` phase serial by passing `jobs=1` to its
  existing pytest-argument construction even when the outer precommit command
  requests more jobs. Add command coverage proving the phase contains no
  xdist `-n` argument.
- [ ] After a green baseline, validate the child-produced manifest and start
  one fresh serial pytest subprocess for each non-empty model in canonical
  order. Pass exact node IDs, activate one model, and do not pass discovery
  options.
- [ ] Keep the prepared repository view open until all replay subprocesses
  finish. Record and print each replay task using existing phase timing/status
  machinery.
- [ ] Run the exact new selectors in
  `tests/test_repository_test_checks.py`, then run the whole file.
- [ ] Commit the Task 3 files.

### Task 4: Real `famulus-paths` pilot

**Files:**
- Modify: `src/officina/common/famulus_paths/__init__.py`
- Modify: `src/officina/common/famulus_paths/_get_interface.py`
- Modify: `src/officina/common/blueprints/famulus-paths.yaml`
- Modify: `src/officina/repository/checks/runner.py`
- Modify: `tests/test_officina_famulus_paths.py`

**Interfaces:**
- Consumes: `boundary_model("famulus-paths", explicit=platform)`.
- Produces:
  `resolve_famulus_paths(*, platform: str | None = None, home: Path, environ: Mapping[str, str]) -> FamulusPaths`
  and
  `FamulusPaths.get(..., platform: str | None = None, ...) -> Path`.

- [ ] Preserve every existing explicit-platform test, including the current
  POSIX fallback for tokens other than `darwin` and `win32`. Prove explicit
  calls never notify the observer.
- [ ] Add one annotation-free functional test that omits `platform`, asserts
  only stable Famulus path-policy meaning, and is discovered for both alternate
  models.
- [ ] Add that exact functional node ID to `PORTABILITY_TESTS`; do not widen
  the portability gate to the complete Famulus path test file.
- [ ] Add contract tests for macOS and Windows directory-layout selection and
  environment-key normalization without claiming native path-object or
  filesystem behavior.
- [ ] Update `common.source.famulus-paths` metadata: make `platform`
  optional, describe implicit boundary selection and the retained explicit
  fallback, add a pinned dependency on `platforms.source.model@1` at
  `{base: repository-root, path: src/officina/platforms/blueprints/model.yaml}`,
  and add `{interface: platforms.interface.model, version: 1}` to both the
  source-level `uses_interfaces` and
  `interfaces.common.source.famulus-paths.interface.python-api.uses_interfaces`.
  Keep
  `common.interface.famulus-paths@1` because all existing explicit calls
  remain valid and the new argument form is additive.
- [ ] Make both public Python entry points accept an omitted platform and
  resolve it through `boundary_model`; keep `home` and `environ` explicit
  and preserve existing failure types.
- [ ] Remove the explicit `sys.platform` argument from the
  `famulus-paths-get` gateway so the production route reaches the implicit
  boundary. Do not change unrelated explicit callers in this release.
- [ ] Run
  `pytest -q tests/test_officina_famulus_paths.py tests/test_platform_replay.py`
  and verify the pilot is selected only when platform selection is implicit.
- [ ] Run focused blueprint ownership/route checks for
  `common.interface.famulus-paths-get`.
- [ ] Commit the Task 4 files.

### Task 5: Documentation and first-release verification

**Files:**
- Modify: `docs/ci-handbook.md`
- No other implementation file is authorized; an attributable failure that
  requires another file triggers the scope-harness stop condition.

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: documented local-precommit and CI-sentinel behavior plus green
  first-release evidence.

- [ ] Document that functional assertions remain platform-independent while
  the runner injects alternate pure policy at observed production boundaries.
- [ ] Document the semantic/native evidence boundary, exact replay task labels,
  fail-closed protocol/manifest behavior, and the bounded duplicate
  portability-baseline cost in precommit.
- [ ] Run focused model/plugin, runner, pilot, and blueprint-ownership
  selectors.
- [ ] Run
  `./repo_checks.py --suite full --task tests:portability --jobs 1 --repository-view staged`
  to exercise the same serial route as the CI sentinel.
- [ ] Run host-capable
  `./repo_checks.py --suite precommit --jobs 8 --repository-view staged` and
  require the output to contain the focused portability baseline followed by
  every non-empty replay task.
- [ ] From the pushed candidate branch, run the exact native probes below with
  distinct request IDs and the candidate's exact 40-character commit SHA:

  ```bash
  gh workflow run python-tests.yml --ref <candidate-branch> -f mode=probe -f request_id=<macos-request-id> -f expected_sha=<40-sha> -f os=macos-latest -f task=tests:shared -f selector=tests/test_officina_famulus_paths.py -f jobs=1 -f profile=serial
  gh workflow run python-tests.yml --ref <candidate-branch> -f mode=probe -f request_id=<windows-request-id> -f expected_sha=<40-sha> -f os=windows-latest -f task=tests:shared -f selector=tests/test_officina_famulus_paths.py -f jobs=1 -f profile=serial
  ```

  Require both runs to report the exact expected checkout SHA and pass,
  preserving native ownership of concrete path/filesystem evidence.
- [ ] Review the complete branch for Critical/Important correctness findings,
  repair only findings attributable to this plan, and rerun the affected gate
  after every repair.

## Explicitly deferred follow-ups

Do not add the following while executing this plan:

- automatic transparent or result-changing fault replay;
- `active_fault()` or fault-profile metadata;
- generic path, newline, PATH-separator, executable-suffix, or process codecs;
- `references/platform-boundary-debt.yaml`;
- semantic/native declaration schemas and `cross_platform.py` enforcement;
- canonical code-testing standard changes;
- thread/background-task attribution;
- xdist discovery/report transport;
- parallel replay, additional boundaries, broad native-CI reduction, or folding
  discovery into the combined validator/shared pytest session.
