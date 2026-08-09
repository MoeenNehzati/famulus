# Fast Dispatcher Semantic Integration Plan

## Run contract

| Field | Value |
|---|---|
| Repository | `$REPO_ROOT` |
| Target | `master` |
| Frozen target | `b984b50b89c1b438bee9130e3ffd08c2f158eddd` |
| Source | `codex/fast-dispatcher` |
| Frozen source | `eb780507bb6e2b60c19a8e288446f018a2d8eeee` |
| Merge base | `07218847c29f406a88e55150ed1ef147fc43f31e` |
| Divergence | 184 target-only commits; 14 source-only commits |
| Closure | Mandatory vacuous merge whose second parent is the frozen source |
| Current phase | Gate 2 planning |
| Active-time estimate | 6-8 hours; reapproval required at 9 hours |

The recurring-tasks worker owns a separate locked worktree. Its changes must not
be staged, modified, stashed, or copied into this integration. Implementation
uses `/tmp/ai-fast-dispatcher` on `integrate/fast-dispatcher`, created from the
approved target tip.

Exact mechanical accounting is stored beside this plan:

- `fast-dispatcher-accounting/target-commits.tsv`: all 184 target-only commits,
  with protection group, slice, and resolution;
- `fast-dispatcher-accounting/source-endpoint.tsv`: all 426 source endpoint
  paths, with disposition, slice, and effect class;
- `fast-dispatcher-accounting/target-endpoint.tsv`: all 263 target endpoint
  paths, with protection group, slice, and resolution;
- `fast-dispatcher-accounting/changed-on-both.tsv`: all 89 shared paths, with
  overlap class, slice, and semantic resolution; and
- `fast-dispatcher-accounting/source-tests.tsv`: every endpoint-visible changed
  source test artifact plus the transient snapshot test from source history,
  with disposition, slice, and assertion map; and
- `fast-dispatcher-accounting/deleted-dispatcher-test-assertions.tsv`: all 55
  tests from the deleted dispatcher, catalog, and snapshot suites, partitioned
  into adapted guarantees and explicitly superseded implementation mechanics.

If either frozen input moves, retain the frozen run or restart only after an
explicit decision. If `master` moves before closure, inventory the new target
delta and its overlaps before rebasing the semantic commits.

## Required outcome

The point of this integration is fast production dispatch, not merely importing
the source implementation. Closure requires all of the following:

- the live repository uses v6 name-addressable blueprints;
- dispatcher resolves names through direct, route-local blueprint lookup;
- the production route performs no repository walk, catalog reconstruction,
  routing-cache write, Git operation, or network operation;
- unrelated modules do not change the files read, probes attempted, or material
  dispatch time;
- warm in-process median dispatch is below 50 ms;
- fresh-process median dispatch is below 100 ms and p95 is below 150 ms;
- those thresholds pass after the complete live blueprint migration;
- authorization, typed failures, confinement, and managed-runtime guarantees
  from target remain intact; and
- the legacy catalog/snapshot route is not a permanent production fallback.

The old dispatcher may remain temporarily on the isolated integration branch
while v6 logic is reconstructed and tested. No intermediate state moves to
`master`.

## Preservation contract

### Target guarantees

The target is authoritative for current behavioral guarantees, not for blueprint
schema version. Preserve:

- the shared interface-authorization semantics and avoid a competing resolver;
- structured typed dispatcher failures, including JSON error output;
- confined runtime loading and process-bound execution;
- managed-runtime atomicity, verified candidates, and cross-platform behavior;
- copied-plugin and parent-Git isolation;
- centralized repository checks and suite tiers;
- all previously integrated macOS installer, TW, repository-check,
  performance, and deferred-fixes effects;
- every live skill's observable behavior during its blueprint migration; and
- unrelated recurring-tasks work that may join target before closure.

Current v5 blueprint representation is migration input, not a protected final
architecture. Existing v4 parsing may remain in offline migration or validation
tooling when it adds no production hot-path complexity. Production dispatch must
not silently fall back from v6 to the slow legacy route.

### Source objective

The source sought to replace snapshot/catalog dispatch with direct blueprint
routing based on canonical module names and an exact repository configuration.
Runtime work must be proportional to configured roots, caller/target ancestry,
and the selected source. Certification remains advisory. The source used a
repository-wide v6 migration to make direct name lookup possible; v6 is therefore
a required source effect, but the broad migration is deliberately delayed until
the reconstructed logic passes a representative pilot.

## Baseline

The frozen target passed `./repo_checks.py --suite precommit --jobs 8` outside
the sandbox: validators passed; shared tests reported 1,373 passed and 11
skipped; all skill-owned groups passed, including 174 recurring-tasks tests with
2 skips. The sandboxed run had six environmental failures because localhost
socket creation was denied in connect-google OAuth tests; the same tests passed
outside the sandbox. This limitation must be preserved when comparing later
candidate results.

The source's historical evidence is retained only as source evidence: 135
focused tests, successful validators, 2,092 precommit tests with 16 skips, and
the documented routing benchmarks. Integrated evidence must be rerun against the
reconstructed candidate and measured under the separated scopes below.

## Source commit accounting

| Commit | Patch-derived intent and anchor | Modules | Disposition | Consequence and evidence requirement |
|---|---|---|---|---|
| `446bf71` | Introduce activation-snapshot routing plus lazy imports, lightweight certification decisions, diagnostics, runtime separation, and sync/install support | dispatcher, common, runtime, installer | `adapted` | Preserve persistent latency, diagnostics, and runtime effects; supersede only snapshot construction, activation, and repair after direct-route parity passes. |
| `643762f` | Specify direct blueprint routing and bounded lookup | docs | `adapted` | Final docs must describe the target-native route and measured behavior. |
| `edf4aa5` | Add exact `officina.toml` module roots through `RepositoryConfiguration` | common configuration | `preserved` | Exact absolute configuration and confinement checks must remain. |
| `8fd2e97` | Define schema-version 6 and direct-routing blueprint fields | blueprint schemas | `preserved` | v6 is required for name-addressable direct routing. |
| `1aa8f9e` | Add `DirectBlueprintRepository` exact-path probes and ancestry-only loading | dispatcher, blueprint loading | `adapted` | Retain no-enumeration, no-write, symlink, ambiguity, and unrelated-defect isolation behavior. |
| `b53b6e4` | Add `resolve_direct_invocation`, route-local authorization, and binding compilation | dispatcher, authorization | `adapted` | Reuse target's shared authorization semantics where possible and prove parity. |
| `05929b9` | Carry exact repository configuration through CLI, runtime pointers, and nested calls | dispatcher hosts | `adapted` | No cwd, `$AI`, parent search, or ambient repository inference. |
| `789dc52` | Add lazy confined package finding/loading instead of recursive snapshots | runtime | `adapted` | Preserve target confinement while avoiding repository-scale preparation. |
| `4f79c4b` | Build and verify a managed direct-dispatch runtime | installer, managed runtime | `adapted` | Reconcile with target's newer atomic installer and candidate verification. |
| `d24ce0d` | Cut repository to direct v6 routing and retire catalog/snapshot machinery | repository-wide | `adapted` | Split logic, migration, and cutover so failures remain attributable. |
| `fdecd78` | Record final architecture, checks, syscall behavior, and performance | docs, evidence | `adapted` | Replace historical claims with evidence from the integrated candidate. |
| `9b4125e` | Require v6 in the live blueprint inventory gate | tests, validators | `adapted` | Activate only after full live migration. |
| `95a360e` | Limit live v6 inventory expectations to code-bearing skills | tests, validators | `adapted` | Reconcile with target's current module inventory rules. |
| `eb78050` | Fingerprint copied plugin sources without routing through ambient parent Git | installer, runtime | `adapted` | Preserve copied-source and parent-Git isolation tests. |

No source commit is rejected. Every adapted or superseded row must map to an
integration commit and focused evidence before closure.

### Mixed-commit effect resolutions

| Commit | Independent effect | Resolution |
|---|---|---|
| `446bf71` | Lazy graph imports, `CertificationDecision`, typed authorization diagnostics, host/maintenance separation, blueprint sync, managed runtime | Preserve or adapt in S2-S4 |
| `446bf71` | Snapshot construction, activation, repair, and routing | Supersede only in S7 after assertion-level parity |
| `4f79c4b` | Root packaging, manifest v2, wheel/source identity, clean probes, installation ordering | Reconcile against target installer matrix in S4 and regenerate final manifest in S6 |
| `d24ce0d` | v6-aware validators, certifier, drift, refactoring, and dotted-child dependencies | Reconstruct before migration in S2 |
| `d24ce0d` | Blueprint plus caller-ID and package-import migration | Apply atomically per module in S6 |
| `d24ce0d` | Catalog/snapshot deletion and production cutover | Delay to S7 |
| `fdecd78` | CLI program identity | Preserve in S3 |
| `fdecd78` | Browser-test virtual-time allowance | Keep target's newer browser behavior; do not restore stale unrelated test settings |
| `9b4125e` | v6 inventory gate and old-runner exclusion | Translate into current unified repository checks in S2/S6 |
| `eb78050` | Copied-source provenance plus Codex/Claude installation behavior | Reconcile in S4 with both host installation tests |

## Endpoint accounting

The merge-base-to-source endpoint contains 426 changed files: 375 modifications,
48 additions, and 3 deletions. The following independent effects cover that
endpoint; Phase 0 must retain the exact `git diff --name-status` inventory in the
run ledger before content changes.

| ID | Source endpoint effect | Planned resolution |
|---|---|---|
| `E01` | Exact absolute repository configuration and module roots | Preserve in S1 |
| `E02` | v6 schemas, fixtures, and name-addressable blueprint model | Preserve in S1 |
| `E03` | Exact-path route-local blueprint repository | Adapt in S2 |
| `E04` | Direct authorization and process-binding compilation | Adapt in S2 |
| `E05` | CLI, launcher, runtime-pointer, and nested-call configuration propagation | Adapt in S3 |
| `E06` | Lazy confined runtime loading | Adapt in S3 |
| `E07` | Managed runtime construction and candidate verification | Adapt in S3 |
| `E08` | Catalog/snapshot deletion and v6 production cutover | Delay to S6 |
| `E09` | Performance, no-scan/no-write, and unrelated-module isolation assertions | Preserve in S4 and S6 |
| `E10` | Copied plugin fingerprints and ambient-Git isolation | Adapt in S3 |
| `E11` | Repository-wide blueprint, contract, documentation, validator, and fixture migration | Delay to S5 and S7 |
| `E12` | v6-aware validators, certification, drift, refactoring, and dependency semantics | Reconstruct in S2 before live migration |
| `E13` | Packaging and runtime-manifest v2 contracts | Pilot form in S4; regenerate final form in S6 |
| `E14` | Deleted legacy tests and transient snapshot assertions | Assertion-level mapping across S3 and S7 |
| `E15` | Canonical caller IDs and package-mode imports in application code | Migrate atomically with owning module in S6 |

Additions, deletions, modes, dependencies, generated views, and persistent-state
effects must receive explicit rows in the live ledger before S1. Grouping may
compress prose but may not omit an endpoint path.

## Target-history protection

The integration branch starts from the frozen target, so target content is
retained by construction. This is not sufficient evidence by itself. The
accounting files enumerate all 184 target-only commits and all 263 target
endpoint paths independently and assign each to a protection group and slice.
The high-risk groups are:

| ID | Target history domain | Invariant |
|---|---|---|
| `T01` | v5 graph, module/source/export, namespace, and authorization work | Preserve behavior while translating representation to v6 |
| `T02` | Dispatcher typed errors, route correctness, catalog recovery, and confinement | Direct routing must provide equal or stronger behavior before catalog retirement |
| `T03` | Managed runtime and cross-platform installer history | Preserve atomicity, verified candidates, and platform-neutral launch |
| `T04` | Centralized checks and validator-performance history | Keep unified collection, suite policy, and performance improvements |
| `T05` | Integrated feature branches and their ancestry markers | Preserve complete target tree and ancestry |
| `T06` | Skill refactors, standards, docs, and generated contracts | Translate current contracts rather than restoring stale source copies |
| `T07` | Application-level fixes since the merge base | Preserve all observable skill behavior and tests |
| `T08` | New target movement during the run | Stop, inventory, approve, rebase semantic commits, and refresh evidence |

No unexpanded target range may remain at Gate 3.

### Critical target contract matrix

| Target commits | Contract that must survive | Owning slice and evidence |
|---|---|---|
| `8b1f5fc` plus current recurring-tasks work | `success.ignore_exit_codes` and `success.ignore_exit_log_patterns`, healthcheck behavior, scheduler behavior | Additive schema reconciliation in S1; complete recurring-tasks tests after S6 refresh |
| `5059dc4`, `282c46d`, `0979a65`, `e9f0a08` | Unified pooled repository checks, suite tiers, benchmark integration, rollout fixes | S2 translates v6 collection/exclusion semantics into `officina.repository_checks`; old runner remains deleted |
| `0ac064f` | Shared `standard_query.py` architecture replacing obsolete closure-engine logic | S2 makes target-native shared tooling v6-aware; never restore deleted source architecture |
| `57f4e17`, `c37c592`, `a49c9bc`, `34e4013`, `2dc3efd`, `693186d`, `7b1320d`, `65884cf` | Atomic pointer, one-batch manifest install, stable dependency-free resolver, verified candidate, Windows layout/deploy, Officina in managed runtime, installed dispatcher acceptance | S4 contract-by-contract tests; S6 final-manifest verification; retain cross-platform evidence |

## Changed-on-both accounting

Eighty-nine paths changed on both sides. The exact accounting file assigns every
path a class, slice, and target-native resolution. The classes are:

| Class | Shared surfaces | Resolution rule |
|---|---|---|
| `O01` | blueprint schemas, standards, runtime dependencies, certification roots | Start from target semantics and translate them deliberately to v6 |
| `O02` | dispatcher graph/configuration and route-smoke tests | Reconstruct source speed behavior around target authorization and diagnostics |
| `O03` | managed runtime, launcher, and installer tests | Reconcile source direct-runtime needs with target's newer installer |
| `O04` | live skill blueprints and generated `SKILL.md` contracts | Migrate only in S5 after the pilot gate |
| `O05` | validators, centralized runner, and collection tests | Preserve target runner mechanics while updating v6 expectations |
| `O06` | application implementation and tests | Avoid stale source restoration; change only what v6 routing requires |
| `O07` | recurring-tasks paths owned by another worker | Defer resolution until the target delta is committed and inventoried |
| `O08` | documentation and catalog views | Rewrite against the final implementation; do not restore historical plans |

Clean textual application is never sufficient evidence. The exact overlap-path
list captured from the frozen refs must be attached to these classes before S1.

## Source-test accounting

| Source test family | Assertion retained | Planned evidence |
|---|---|---|
| Direct blueprint repository | Exact probes, no enumeration/writes, symlink and ambiguity rejection, unrelated malformed modules unread | S3 focused collection and result |
| Direct authorization | self/ancestor/public rules, descendants, hop-local replacement, surfaces, versions, bindings | S3 focused collection and parity result |
| Repository configuration | exact absolute file, cwd and `$AI` independence, malformed and symlink rejection | S1 focused collection and result |
| Runtime confinement | selected package/source only; nested calls preserve repository identity | S3 focused collection and result |
| Managed runtime/install | verified candidate, runtime pointer, copied source, parent-Git isolation | S4 subsystem and cross-platform evidence |
| Performance | source-scope resolver and checkout CLI thresholds; separate installed-launcher timing; unrelated modules invariant | S5 pilot and S7 full-inventory benchmarks |
| v6 inventory | all required live code-bearing modules use valid v6 blueprints | S6 inventory and route-smoke result |
| Deleted dispatcher/catalog/snapshot tests | Map public API, typed failures, warnings, bindings, subprocess/dry-run, confinement, and obsolete implementation assertions separately | S3 direct tests and S7 retirement evidence |

The exact deleted-suite partition is fixed before implementation in
`fast-dispatcher-accounting/deleted-dispatcher-test-assertions.tsv`. Catalog
storage, snapshot activation/repair/generation, and v4 production fallback are
superseded. Canonical IDs, public metadata, shared authorization, versions,
typed failures, warnings, certification, no-write behavior, confinement,
UTF-8, subprocess semantics, launch normalization, symlink rejection, and
configured roots are adapted into direct-v6 coverage.

Each changed source test file requires its own live-ledger row, disposition,
resulting assertion, focused result, and proof that the centralized runner
collects it.

## Semantic slices

| Slice | Intended behavior | Principal modules | Validation | Estimate | Hard stop |
|---|---|---|---|---:|---:|
| `S0` | Isolate, recover, inventory, and baseline | Git refs, worktree, ledger, repository runner | Frozen refs, exact inventories, bounded baselines | 20 min | 40 min |
| `S1` | Establish v6 and exact repository configuration additively | `officina.toml`, schemas, common configuration | Configuration and schema tests, including recurring success policy | 35 min | 70 min |
| `S2a` | Make target graph primitives understand explicit v6 inputs while v5 remains live | graph, inventory, authorization | v6 topology, schema, inventory, and authorization tests | 45 min | 90 min |
| `S2b` | Make current target-native consumers understand v6 | unified runner, validators, standard query, certifier, drift, refactoring | Tooling, validator, collection, and dependency tests | 45 min | 90 min |
| `S3` | Resolve and authorize only the requested route | dispatcher locator, authorization, binding compilation, CLI | Direct lookup/authorization/public API tests and read accounting | 70 min | 140 min |
| `S4` | Launch through current target installer/runtime contracts | confined runtime, managed runtime, resolver, pilot manifest | Installer matrix, runtime, copied source, host-specific evidence | 60 min | 120 min |
| `S5` | Prove correctness and source-scope speed on a representative v6 pilot | pilot modules and focused fixtures | Resolver, checkout CLI, installed launcher, execution smoke, scale probe | 45 min | 90 min |
| `S6` | Migrate modules atomically and regenerate final manifest | blueprints, contracts, caller IDs, package imports, final manifest | Per-module checks, inventory, standards, route smoke, final installer verification | 75 min | 150 min |
| `S7` | Cut production to direct v6 and retire legacy routing | dispatcher core/CLI, catalog/snapshot code and tests | Full-inventory correctness, assertion map, and performance benchmark | 60 min | 120 min |
| `S8` | Reconcile final docs and stale expectations | docs, standards, tests | Complete affected subsystems | 30 min | 60 min |
| `S9` | Prove completeness and construct closure candidate | ledger, audits, exact committed tree | Two audits, full gates, vacuous-merge proofs | 75 min | 150 min |

## Pilot and migration gate

S6 cannot begin because the new modules import or because isolated unit tests are
green. S5 must first prove on representative real routes that:

- dispatch and denial behavior are correct;
- authorization and confinement match target guarantees;
- reads are proportional to configured roots and selected route depth;
- an unrelated malformed module is never read;
- no scans, subprocesses, routing writes, Git, or network occur in resolution;
- pilot latency meets every required threshold; and
- the centralized runner collects the adapted source tests.

After S6, S7 repeats correctness, read-accounting, and latency measurements on
the complete live inventory. A pilot pass is not final performance evidence.

### Performance measurement scopes

| Measurement | Required scope |
|---|---|
| Resolver | Preserve source sampling of `resolve_direct_invocation`; warm median below 50 ms |
| Checkout CLI | Fresh `python -m officina.dispatcher.cli --dry-run`; median below 100 ms and nearest-rank p95 below 150 ms |
| Installed launcher | Managed-runtime launcher plus resolver, reported separately rather than compared to resolver-only thresholds |
| Execution smoke | Correct selected-source execution; no routing-only latency threshold |
| Scale independence | Exact reads/probes and latency before and after unrelated inventory growth |

Record hardware, interpreter, environment, sample count, median, and p95.

## Validation strategy

Use the centralized repository runner and its existing suite policy. Do not
introduce a second test/validator orchestrator.

- Run the cheapest focused collection after each slice.
- After shared schema, fixtures, installer, runtime, or runner changes, run the
  complete affected subsystem once.
- Run repository-wide gates only on the exact committed closure candidate unless
  diagnosis requires otherwise.
- Record commands, collection evidence, durations, skips, warnings, and
  environmental limitations.
- Treat source tests as requirements; never delete a stale source test without a
  mapped stronger assertion or explicit rejection approval.
- Do not weaken correctness, authorization, schema validation, or confinement to
  obtain performance.
- Never restore `scripts/run-python-tests.py`; translate source v6 collection and
  exclusion behavior into the unified `officina.repository_checks` runner.
- Use a bounded pilot runtime manifest in S4/S5, regenerate the final v2 manifest
  from the fully migrated inventory in S6, and verify installation from that
  final manifest in S7.

Recurring-tasks is frozen at the approved target until its owner commits. Before
its S6 migration, compare the entire module against the new target tip. If that
work enters `master`, refresh its additive configuration reconciliation, all
blueprints/caller IDs/package imports, and complete focused tests, not only
textually conflicting paths.

## Performance failure policy

If a benchmark misses, attribute time to process startup, configuration loading,
blueprint probing, validation, authorization, binding compilation, or runtime
launch. Permit one focused diagnosis/correction cycle. Stop if the same blocker
fails twice. Do not start broad blueprint migration while pilot speed is
unresolved. A miss after full migration blocks closure.

## Progress and deviation controls

Report after every slice, after 30 active minutes without closing a slice or
ledger item, when a slice reaches its estimate, and immediately on repository
drift or material path expansion. Each report contains only accounting progress,
active time, current blocker, validation/performance state, plan credibility, and
the next adjustment.

Stop and require reapproval when:

- the same blocker fails twice;
- a slice reaches its hard stop;
- total active time reaches 10 hours;
- either frozen ref moves unexpectedly;
- work expands materially beyond approved modules or behavior; or
- an accepted source effect cannot be preserved without weakening a target
  guarantee.

## Assurance and closure

Before Gate 3, require complete traceability:

`source commit -> behavior -> endpoint -> target module -> integration commit -> evidence`

Run two independent audits:

1. Source-preservation: search for omitted source behavior, tests, files,
   deletions, dependencies, migrations, interfaces, and side effects.
2. Target-regression: search for weakened target behavior, stale source
   restoration, duplicate architecture, compatibility drag, and validation gaps.

After findings are resolved, create the closure candidate on the integration
branch with a vacuous merge such as:

```bash
git merge -s ours --no-ff eb780507bb6e2b60c19a8e288446f018a2d8eeee
```

The candidate's first parent must be the completed semantic tip, its second
parent the frozen source, and its tree exactly equal the first-parent tree. Run
the complete required gates on that exact committed candidate. Gate 3 approval
is invalidated by any candidate change or target movement other than the
authorized fast-forward to that exact candidate.

No branch deletion or push is implied by closure approval.

## Approval record

| Gate | Status | Scope |
|---|---|---|
| Gate 1: preservation contract | approved in conversation | v6 and measured fast dispatch are mandatory; migration follows a successful pilot |
| Gate 2: reconstruction authority | approved by instruction to proceed | Dispositions, corrected slices, limits, validation, and compatibility boundary |
| Gate 3: closure authority | pending | Approve exact tested vacuous-merge candidate and target fast-forward |

Gate 2 was independently re-audited after amendment. The source-history auditor
confirmed complete exact inventories, mixed-commit resolutions, benchmark scope,
and the 55-row deleted-suite assertion partition. The target-history auditor
confirmed target-native tooling order, unified-runner preservation, additive
recurring-tasks configuration, installer contracts, manifest sequencing, and
target-movement rules. Both reported no unresolved planning findings.

## Progress log

| Slice | Accounting resolved | Evidence | Integration commit | Status |
|---|---|---|---|---|
| `S0` | Frozen refs; 14 source commits; 426 source paths; 184 target commits; 263 target paths; 89 overlaps; 64 source test artifacts; 55 deleted-suite assertions | Clean 8-worker target baseline and two independent Gate 2 audits | `ac138b0` | complete |
| `S1` | `E01`, `E02`; additive `T01`/`T07` configuration preservation | 50 focused repository-configuration, v6-schema, and configuration-consumer tests passed | S1 checkpoint containing this row | complete |
| `S2a` | Explicit v6 graph, inventory, and shared authorization support; live v5 defaults preserved | 128 focused v6 topology plus complete graph, inventory, and authorization tests passed | S2a checkpoint containing this row | complete |
| `S2b` | Explicit v6 support in target-native validators, blueprint authoring/sync, certification, and drift; live v5 defaults preserved | 46 validator/tooling tests, 12 blueprint-maker tests, 57 certifier tests, and 20 drift tests passed; v6 migration schemas made self-contained for authoring | S2b checkpoint containing this row | complete |
| `S3` | Exact v6 module/source lookup, hop-local namespace authorization, terminal export authority, and direct process-binding compilation without repository inventory or writes | 104 focused direct lookup, authorization, typed-error, compiler, and shared-authorization tests passed | S3 checkpoint containing this row | complete |
| `S4` | Explicit-config v6 host routing, confined Python execution, validated runtime-pointer binding, and dependency-free launcher injection; implicit v5 routing preserved | Direct runtime 25, pointer/launcher 26, runner 57, legacy dispatcher 46, route-smoke 13, install lifecycle 17 passed/2 skipped, and managed-runtime units 16 passed; real-uv checks unavailable in sandbox because `~/.cache/uv` is read-only | S4 checkpoint containing this row | complete |
| `S5` | Representative nested v6 route proves exact lookup, authorization, confinement, no enumeration or writes, scale-independent work, and source-scope latency; CLI/package imports are lazy | Four isolated pilot performance gates passed: warm resolver median below 50 ms, fresh-checkout CLI median below 100 ms and p95 below 150 ms, unchanged reads/probes after 500 unrelated modules, and forbidden scan/subprocess/write operations absent. Numeric gates are full-only and receive the complete runner lease so pooled correctness work cannot corrupt wall-clock evidence | S5 checkpoint containing this row | complete |
