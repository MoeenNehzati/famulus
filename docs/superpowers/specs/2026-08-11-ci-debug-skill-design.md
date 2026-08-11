# CI Debug Skill Design

## Goal

Create a repository-owned `ci-debug` skill that turns remote CI repair into a
repeatable, evidence-driven workflow. It must isolate the smallest failing
boundary, preserve sufficient evidence, constrain the repair to evidence-backed
paths, and expand verification through the exact required GitHub Actions matrix
before user-approved integration.

The system covers pytest failures and non-pytest failures such as setup,
dependency installation, collection, validators, native smoke checks, timeouts,
infrastructure, and artifact failures.

## Verified Problem

The current workflow is useful as a certification gate but inefficient as a
debugger:

- every `master` push starts the full operating-system matrix;
- the workflow has no default-branch `workflow_dispatch` entry point, so a
  feature branch cannot bootstrap dispatch by adding one only on that branch;
- repository tasks cannot select one test path or node;
- hosted runners disappear without preserving structured failure state;
- JUnit data produced by `repo_checks.py` is currently temporary;
- child-process output is streamed without durable, redacted phase logs;
- `--sequential` is a compatibility no-op, not a serial execution control;
- native smoke checks are workflow-owned rather than runner-owned; and
- a repair cannot be certified on its branch against an exact, complete matrix
  before reaching `master`.

The August 11 Windows failure is representative: four jobs passed, while the
shared Windows task failed after seven minutes because a migration test found
its repository dirty. The vanished runner did not preserve the dirty paths at
the moment of failure or provide a clean, condition-controlled reproducer.

A security audit also found credential-bearing HTTP(S) userinfo in the local
configured remote. Its value must never be printed or persisted. Sanitizing
that configuration is a separate prerequisite requiring explicit user
authorization; the implementation must fail closed until it is resolved.

## Design Principles

1. `repo_checks.py` owns execution and emits one versioned diagnostic schema.
2. GitHub Actions only selects reviewed targets, supplies conditions, and
   uploads runner output.
3. The skill runtime dispatches, validates, stores, and summarizes evidence; it
   does not reimplement repository suites.
4. Every conclusion is bound to an exact candidate and an exact attempt.
5. Classification reruns use a separate fresh hosted job/VM at the same commit,
   never the contaminated failed host or checkout.
6. Incident evidence constrains the allowed repair paths and any expansion is
   explicit and recorded.
7. CI green is evidence, not Git authority. Commit, push, and integration remain
   governed by `git-workflow` and explicit user approval.
8. Diagnostic output is untrusted and secret-bearing until it passes redaction
   and a deterministic prohibited-data scan.

## Registered Node Architecture

The exact node graph is part of the design rather than an implementation
detail.

### Discoverable instruction node: `ci-debug`

- Gateway source: `ci-debug.source.gateway`.
- Public instruction interface: `ci-debug.interface.default`.
- The interface owns the decision procedure, safety policy, verification graph,
  and assistant-facing incident workflow.
- `git-workflow` is a required background sub-skill. The instruction interface
  delegates every Git mutation to that skill rather than authorizing the
  runtime to commit, push, merge, or modify worktrees autonomously.
- The instruction node invokes runtime interfaces through `dispatcher`; it
  never imports or executes runtime implementation files directly.

### Non-discoverable Python child node: `ci-debug._rtx`

- Node marker: `skills/ci-debug/_rtx/blueprint.yaml`.
- Its exact v6 source and interface graph is:

  | Behavioral source | Source interface | Exported interface |
  | --- | --- | --- |
  | `ci-debug._rtx.source.rtx-incident-state` | `ci-debug._rtx.source.rtx-incident-state.interface.incident-state` | `ci-debug._rtx.interface.incident-state` v1 |
  | `ci-debug._rtx.source.rtx-remote-dispatch` | `ci-debug._rtx.source.rtx-remote-dispatch.interface.remote-dispatch` | `ci-debug._rtx.interface.remote-dispatch` v1 |
  | `ci-debug._rtx.source.rtx-remote-inspection` | `ci-debug._rtx.source.rtx-remote-inspection.interface.remote-inspection` | `ci-debug._rtx.interface.remote-inspection` v1 |
  | `ci-debug._rtx.source.rtx-artifact-collection` | `ci-debug._rtx.source.rtx-artifact-collection.interface.artifact-collection` | `ci-debug._rtx.interface.artifact-collection` v1 |

- Each source declares complete arguments, outputs, effects, sensitivity,
  process binding, dependencies, and outcomes.
- Each child export sets `allow_all_modules: false` and
  `allowed_callers: [ci-debug]`.
- Cross-module calls use declared `uses_interfaces` and
  `PythonMachineInterface.dispatch()`, never the dispatcher CLI or direct
  imports of another node's internals.
- The parent's `_rtx` namespace export is version 1. Its `surface.only` map
  contains exactly the four exported interfaces above at version 1. Each
  `interface_access` entry sets `allow_all_modules: false` and
  `allowed_callers: []`. The runtime is not independently discoverable.

The runtime reuses authorized common interfaces rather than duplicating them.
The exact `uses_interfaces` edges are:

- `rtx-incident-state` uses `common.interface.atomic-files` and
  `common.interface.repository-paths`;
- `rtx-remote-dispatch` uses `common.interface.repository-paths` and a new
  `common.interface.git-provenance-read`;
- `rtx-remote-inspection` uses `common.interface.git-provenance-read`; and
- `rtx-artifact-collection` uses `common.interface.atomic-files` and
  `common.interface.repository-paths`.

The common blueprint must authorize `ci-debug._rtx` on those exact interfaces.
The existing `common.interface.git-provenance` is too broad because it includes
Git execution and materialization operations. Before authorizing the runtime,
split out `common.interface.git-provenance-read`, backed by
`common.source.git-provenance.interface.read-only`. Its only operations resolve
the repository root, read HEAD/tree/blob object IDs, read porcelain status,
list diff paths between two objects, and report a boolean for HTTP(S) remote
userinfo. It returns no raw remote URL and exposes no generic Git execution,
ref mutation, pinning, or commit-tree materialization. These common-node changes
are part of the implementation scope and certification dependency graph.

Before authoring either node, `skill-maker` must retrieve the four applicable
standards roots and their complete pinned closures and facts:

- instruction module;
- instruction behavioral source;
- Python module; and
- Python behavioral source.

## Ownership Boundaries

### `repo_checks.py`

The canonical repository runner owns:

- the dispatch table for suites, tasks, validators, native checks, and valid
  selector combinations;
- target resolution and exact child-process argv;
- worker count, repeat count, phase ordering, and timeout policy;
- durable, redacted phase logs, JUnit, timing data, and failure hooks;
- Git state before execution, at the moment of failure, and after execution;
- classification-plan generation and experiment execution inside the separate
  hosted job/VM provisioned by the workflow; and
- one versioned diagnostic bundle schema shared by automatic and manual runs.

`src/officina/repository_checks.py` remains the underlying repository-check
implementation where appropriate. The public runner and implementation must not
diverge in task or diagnostic semantics.

### `ci-debug._rtx`

The deterministic runtime owns:

- starting or resuming an incident from a workflow run;
- validating candidate identity and GitHub metadata;
- validating typed selection inputs against the runner-owned dispatch table;
- dispatching targeted or full runs through `gh` without shell interpolation;
- correlating a dispatch with its exact run and attempt;
- watching, downloading, validating, and recording artifacts;
- enforcing evidence-backed repair scope before dispatch or integration; and
- producing a concise redacted incident summary.

It may inspect and record Git state. It may not commit, push, merge, rewrite
history, or integrate.

### GitHub Actions

The workflow owns only:

- trigger and permission declarations;
- a reviewed mapping from typed inputs to runner invocations;
- a dependency-free diagnostic prelude that creates a minimal bundle before
  checkout or any other fallible setup step;
- environment and dependency setup, with every repository-owned setup command
  executed through a redacting capture wrapper after checkout;
- invoking `repo_checks.py`; and
- finalizing and attempting to upload the already scanned diagnostic bundle
  with `always()`.

The workflow does not parse pytest failures, classify incidents, or construct a
second test inventory. Third-party actions such as checkout and setup-python are
fully pinned, receive no diagnostic free-form input or repository secrets, and
run before repository code is available. Their platform-owned console output is
outside the total-redaction guarantee and is explicitly identified as such;
secrets must never be passed to those steps. The dependency-free prelude ensures
that their failures still leave safe, schema-minimal evidence for the finalizer
to publish when publication succeeds.

## Two-Stage Rollout

GitHub only accepts `workflow_dispatch` for a workflow present on the default
branch. Therefore the feature cannot be proven by first adding dispatch only on
its development branch.

### Stage 0: default-branch dispatch bootstrap

Land a workflow-only adapter through the existing full CI path. It adds an
inert, typed manual entry point using only current task-level and full-matrix
capabilities. It does not add arbitrary selectors or depend on unmerged runner
code. Workflow policy tests prove that push and pull-request behavior is
unchanged.

After that adapter is merged to the default branch, create the implementation
branch from the adapter commit. Branch dispatch can then select the branch ref
and exercise the evolving workflow and runner.

### Stage 1: diagnostic implementation

Add runner-owned selection and diagnostics, the registered skill/runtime, state
handling, workflow artifact upload, and policy tests. Re-certify every affected
node and repository certification root before relying on live results.

### Stage 2: exact candidate certification

Run the full required matrix against the implementation candidate, integrate
only through an explicitly approved `git-workflow` action, then monitor the
automatic default-branch run for confirmation.

Actor-restricted interactive access is deferred until after the core workflow
is accepted. It is not a v1 dependency or acceptance criterion.

## Typed Selection Model

One runner-owned machine-readable dispatch table enumerates valid combinations
across separate axes:

- `suite`: current repository suites such as `validators`, `tests`,
  `precommit`, `pre-push`, `portability`, and `full`;
- `task_id`: compatible tasks such as `validators`, `tests:shared`,
  `tests:browser`, and `tests:performance`;
- `validator_ids`: a bounded subset of registered validators;
- `functional_selectors`: tracked test paths or pytest node IDs;
- `native_check`: first-class keyring or scheduler smoke tasks;
- `os`, supported Python version, worker count, repeat count, and named
  condition profile.

`portability` is a suite currently mapped to the shared-test task, not a task
ID. Browser membership differs between existing suite profiles and must be
represented explicitly in the dispatch table. Native smokes move under
`repo_checks.py` as first-class tasks so the canonical runner remains the sole
execution owner.

Functional selectors are invalid for validators and native checks unless the
dispatch table explicitly defines a compatible selector type. A pytest selector
must resolve to a tracked test under approved test roots and belong to the
selected suite/task. An optional keyword expression is passed as one validated
argv value. Arbitrary pytest flags, environment assignments, and commands are
rejected.

Serial execution means `jobs=1`. The workflow must not rely on the existing
`--sequential` compatibility alias; the alias may remain explicitly documented
as a no-op until removed.

## Failure and Condition Identity

The runner emits two separate identifiers:

- `failure_signature`: an OS/task-independent identity derived from failure
  kind, failing node or phase, exception/result type, and top repository frame;
- `condition_key`: the OS, task, worker count, order seed, suite/profile,
  dependency-lock digest, runner image, and tool versions for one observation.

Failure kinds include setup/dependency, collection, validator, ordinary test,
native smoke, timeout/hang, cancellation, infrastructure, and
diagnostic/artifact failure. A new signature creates a linked child incident
rather than silently redefining the original failure.

Classification labels remain observational:

- `serial-reproduced`;
- `enclosing-selection-correlated`;
- `parallel-associated`; and
- `cross-platform-differential`.

Stronger causal language requires repeated observations and an order
permutation or another discriminating experiment. A difference between hosted
operating systems is not by itself proof of platform causation.

## Persistent Incident State

Canonical incident state lives under the owning skill node at
`skills/ci-debug/_rtx/_state/<incident-id>/` and is excluded from Git by an
exact ignore rule. The runtime authority declares the exact owned-filesystem
regular expression. `_build/ci-debug/` contains only reproducible derived local
reports and downloaded copies; it is never the canonical state store.

Each incident records:

- immutable incident ID, parent/child relationships, and failure signature;
- failing base SHA and tree SHA;
- current expected head SHA, tree SHA, workflow blob SHA, and request
  fingerprint;
- workflow, run ID, attempt, event, actor, job ID, required-job manifest, and
  intended artifact names, bundle-content digests, and post-upload GitHub
  artifact IDs/digests;
- typed target, condition key, actual runner image, dependency and tool
  versions;
- evidence-linked allowed paths and separately approved scope expansions;
- repair commits observed; and
- every verification result with provenance.

Incident epistemic/disposition state is separate from operational attempt
state.

Incident states are `captured`, `reproducing`, `reproduced`, `unreproduced`,
`inconclusive`, `diagnosing`, `repairing`, `target-green`, `scope-green`,
`matrix-green`, `integrated`, `confirmed`, `blocked`, `abandoned`, and
`superseded`.

Attempt states are `prepared`, `queued`, `running`, `collecting`, `completed`,
`interrupted`, `cancelled-before-start`, `cancelled-running`,
`superseded-before-start`, and `infrastructure-failed`. Classification outcome
is a separate enum: `not-attempted`, `reproduced`, `not-reproduced`,
`inconclusive-contaminated`, and `inconclusive-infrastructure`. Bundle
publication is also separate: `not-produced`, `bundle-produced`,
`bundle-publication-refused`, and `upload-failed`.

The normative transition and invalidation rules are:

| Event | Required prior state | Result | Invalidation/effect |
| --- | --- | --- | --- |
| incident capture | none | `captured` | Records failing-base identity |
| reproducer dispatch | `captured`, `unreproduced`, or `inconclusive` | `reproducing` | Creates an independent attempt |
| reproducer result | `reproducing` | `reproduced`, `unreproduced`, or `inconclusive` | Records separate classification outcome |
| evidence review | `reproduced` or explicitly accepted `inconclusive` | `diagnosing` | Establishes allowed path set |
| approved repair begins | `diagnosing` | `repairing` | Creates a new candidate identity |
| post-repair checks pass | `repairing`, `target-green`, or `scope-green` | next green state | Bound only to the unchanged candidate |
| any check fails | any post-repair green state | `diagnosing` or child incident | Invalidates that state and all downstream states |
| candidate SHA/tree/workflow/input changes | any post-repair state | `repairing` | Invalidates all post-repair green states |
| exact matrix passes | `scope-green` | `matrix-green` | Makes candidate eligible for approved integration |
| approved integration observed | `matrix-green` | `integrated` | Records the distinct integrated identity |
| integrated matrix passes | `integrated` | `confirmed` | Closes the incident |
| explicit stop/replacement | any nonterminal state | `blocked`, `abandoned`, or `superseded` | No green state transfers |

Attempt transitions proceed in order from `prepared` to `queued`, `running`,
`collecting`, and `completed`, with the declared interruption, cancellation,
supersession, or infrastructure terminal reachable only from applicable earlier
states. A queued concurrency supersession becomes `superseded-before-start`.
Manual cancellation of a running job becomes `cancelled-running`; its bundle
publication is best-effort. An interrupted monitor can resume from its persisted
run ID. Tests exhaust every allowed and rejected transition.

## Candidate and Matrix Binding

Every attempt is bound to:

- expected commit SHA and tree SHA;
- workflow blob SHA;
- normalized typed inputs and request fingerprint;
- run ID and attempt;
- exact required-job manifest; and
- intended artifact name and bundle-content digest, followed after upload by the
  GitHub artifact ID and GitHub artifact digest in canonical incident state.

Branch names are locators, not identities. Inspection must prove that the run's
`headSha` equals the incident's expected SHA. Every dispatch includes a
high-entropy request ID as a typed input and projects it into `run-name`. The
dispatcher correlates on that queryable request ID plus exact SHA, event, and
actor, or uses a directly returned API run ID; it never assumes the newest run
is the requested run. The artifact manifest repeats the request ID for final
verification. If repository policy permits, an immutable temporary tag may be
used as an additional locator.

`matrix-green` requires every required job and required conditional check in the
manifest to succeed for that exact candidate. Missing, cancelled, stale,
unexpectedly skipped, or extra-required jobs prevent certification. Push/PR and
manual-full workflows derive from the same machine-readable matrix manifest, or
policy tests prove their manifests identical.

If the default branch advances or integration changes the commit/tree through a
merge or squash, either certify the prospective integration commit/tree before
integration or treat the integrated result as a new candidate and rerun the
required matrix. Branch green never transfers implicitly to a different tree.

## Diagnostic Bundle and Redaction

Automatic push/PR failures and all manual runs attempt publication of a
short-retention bundle. The unique intended artifact name includes run ID,
attempt, OS, task, and job identity. Upload uses `if-no-files-found: error`.
Queued runs superseded before starting cannot produce a bundle. Manual
cancellation while running, prohibited-data refusal, and upload failure are
explicit safe non-artifact outcomes rather than false preservation guarantees.
GitHub's run/job/step conclusion remains the outer evidence for those outcomes.

The versioned bundle contains:

- a manifest with schema version, exact candidate and request identity, job ID,
  intended artifact name, condition key, and a canonical digest over the
  payload-file digest list (excluding the manifest itself);
- a concise summary and verification history;
- redacted durable phase logs, JUnit XML, and timing JSON;
- structured argv with sensitive values replaced, never a reconstructed raw
  shell command;
- allowlisted runtime, dependency, runner-image, and owned-process metadata;
- Git status and changed/untracked paths before execution, at failure-hook time,
  and after execution; and
- bounded target evidence such as screenshots or native-check diagnostics.

The three Git snapshots show when a change was observed; without additional
filesystem/process attribution they do not claim which process wrote it.

Owned child-process output is captured and redacted before it reaches durable
logs or the console. Local-variable dumps and `--showlocals` are disabled by
default. Redaction covers stdout/stderr, console summaries, phase logs, JUnit,
JSON, Markdown, nested structures, filenames, argv, and error messages. Remote
URLs and unrestricted process command lines are never collected. Process
metadata is limited to allowlisted descendants started by the runner.

Before upload, a deterministic prohibited-data scan covers the entire bundle.
Any hit sets `bundle-publication-refused`, prevents upload, and emits only a safe
diagnostic code. A successful upload returns the GitHub artifact ID and digest;
those values are stored as outer metadata in canonical incident state and
validated during collection. They are never embedded self-referentially in the
artifact whose bytes determine the digest. Tests seed secrets through stdout,
logs, JUnit, summaries, argv, and nested records and prove none survive.

Workflow input values are placed in environment variables and read by Python;
free-form values are never interpolated as `${{ inputs.selector }}` inside a
`run:` script. Selectors reject control characters and paths outside approved
test roots. Artifact names use a selector hash, never the raw selector.

## Clean Classification Reruns

After a failed attempt, the runner first finalizes evidence from that failed
workspace and emits a bounded classification plan. The workflow provisions a
separate hosted job/VM, checks out the same expected SHA, uses a new cache
namespace, downloads the failed-attempt evidence, and invokes `repo_checks.py`
with that plan. The workflow provisions isolation; the runner still owns the
experiment and schema. Classification never runs again on the failed host or in
its checkout.

The smallest reproducing selection depends on failure kind: a pytest node or
file, validator, native check, setup phase, or bounded task. The runner reruns
the original failing selection under the same controllable conditions before
varying workers, selection, order, or operating system. Hosted `*-latest`
images and fresh VMs are not identical conditions; actual image/tool versions
are recorded and drift is reported as a confounder.

If a separate clean hosted job cannot be established or exact identity cannot be
proved, classification outcome is `inconclusive-contaminated` or
`inconclusive-infrastructure` as applicable. Classification does not proceed.
Repeats and order permutations are explicit bounded experiments, not implicit
retry loops.

Controllable dependencies are pinned. Python caching uses the reviewed CI
requirements lock/path. Node caching is enabled only with a lockfile and
`npm ci`; Claude/Codex CLI versions are checked into a reviewed manifest and
their installation is skipped for irrelevant tasks.

## Repair Scope and Git Policy

An incident begins with an evidence-linked allowed path set. Before every remote
dispatch and before proposed integration, the runtime compares the complete
diff from the failing base to the current candidate against that set. An
unrelated path fails closed. A scope expansion requires explicit approval and
is recorded with its evidence and approving action.

The skill creates or reuses an isolated named worktree only through
`git-workflow`. It never edits a dirty primary checkout or stages unrelated
files. Pushing a branch and integrating a repair require explicit user approval.
The runtime can recommend those steps and verify their outcomes but cannot
perform them.

The skill does not use these as default repairs:

- skipping, deselecting, or weakening the failing test;
- increasing a timeout without evidence about consumed time;
- permanently disabling parallelism because a serial observation passes;
- broad dependency upgrades;
- reducing operating-system or native integration coverage; or
- combining a new failure signature into an unrelated patch.

## Verification Graph

Verification is an ordered graph, not a fixed node-only ladder:

1. **Pre-repair diagnosis:** bind observations to
   `failing_base_sha/tree/workflow`, reproduce the smallest applicable selection
   under the original controllable conditions, and establish the evidence-backed
   repair scope.
2. **Repair transition:** after approval through the interactive workflow, make
   the scoped repair and record a distinct `candidate_sha/tree/workflow/input`
   identity. No failing-base green or classification state transfers to it.
3. **Post-repair target:** run the repaired target with bounded repeats, then
   rerun the original failing selection exactly.
4. **Scope expansion:** run the enclosing selection, affected task, and
   explicitly coupled tasks or native checks.
5. **Candidate certification:** run the full required matrix for the exact
   candidate.
6. **Integration confirmation:** after user-approved integration, bind the
   integrated SHA/tree as another distinct candidate and run its required
   default-branch matrix.

Inapplicable nodes are recorded as such with a policy reason, never silently
skipped. Any failure stops expansion and returns the incident to diagnosis or
creates a child incident for a new signature. `matrix-green` is evidence that
the candidate is eligible for a user-approved integration action, not authority
to integrate it. Any candidate commit, tree, workflow, or normalized-input
change restarts the complete applicable post-repair subgraph.

## Workflow Safety and Performance

- Permissions are least-privilege and explicit.
- `gh` has a declared minimum version and required token scopes.
- Monitoring uses `gh run watch --exit-status` and validates the exact run and
  attempt before recording a conclusion.
- `run-name` includes the typed high-entropy request ID used for correlation.
- Targeted-run concurrency groups are partitioned by incident. They may
  supersede an obsolete queued attempt but set `cancel-in-progress: false` so a
  running attempt can finalize evidence. Full certification uses a distinct
  group and is never cancelled by a targeted run.
- Pip caching keys `requirements-ci.txt` (or its reviewed replacement).
- Node downloads are cached only from the reviewed lock/manifest.
- CLI installation is skipped where the dispatch table says it is irrelevant.
- Timing data may support balanced deterministic Windows shards, but shard
  membership becomes part of the runner-owned manifest.
- Worker count remains an explicit condition; increasing workers is not a
  substitute for isolation.

Interactive `tmate` access is deferred. If designed later, it requires a
dedicated manual-only job, `contents: read`, checkout with
`persist-credentials: false`, no repository/organization/environment secrets,
actor restriction, registered-key preflight, a short timeout, a fully pinned
action SHA, and removal of credential-bearing Git configuration before the
shell starts. It receives a separate security review and acceptance gate.

## Error Handling

The runtime fails closed when a ref is absent, `headSha` differs, a selector is
invalid, the branch is not pushed, GitHub authentication is missing, the `gh`
version/scopes are insufficient, a remote contains HTTP(S) userinfo, artifacts
do not match the requested attempt, the matrix is incomplete, prohibited data
is detected, scope contains unapproved paths, or workflow metadata is
ambiguous. Errors expose a safe code and the unresolved boundary without
printing credentials or raw remotes.

Network and GitHub API failures do not alter incident conclusions. Artifact
upload runs under `always()` but does not overwrite the test result; simultaneous
test and artifact failures are recorded as separate outcomes.

## Testing and Mandatory Certification

Implementation follows test-driven slices:

1. Stage 0 workflow policy tests and live default-branch dispatch bootstrap;
2. dispatch-table, parser, selector, control-character, path-escape, and
   incompatible-axis rejection;
3. failure identity, condition identity, exhaustive incident/attempt/
   classification/publication transitions, and candidate invalidation;
4. mocked `gh` dispatch correlation, watch, interruption, and artifact download;
5. dependency-free setup-failure bundles, durable runner diagnostics, and Git
   failure-hook capture;
6. separate-hosted-job classification and same-host rerun rejection;
7. whole-bundle seeded-secret redaction and prohibited-data scanning;
8. evidence-backed path enforcement and approved scope expansion;
9. workflow equivalence, unique artifacts, concurrency partitioning, and exact
   full-matrix completeness;
10. pytest and at least one non-pytest incident;
11. one live branch-scoped targeted run followed by the exact full branch
    matrix; and
12. default-branch confirmation after user-approved integration.

Changes to `repo_checks.py` and `src/officina/repository_checks.py` affect
declared repository certification-basis roots. Therefore certification is not
optional: implementation must perform dependency-first recertification of all
affected nodes, a full-repository certification migration for the changed
roots, and fresh certification of `ci-debug` and `ci-debug._rtx`. Existing
precommit and pre-push gates also remain required.

## Acceptance Criteria

- The default branch first contains the task-level/manual dispatch adapter, and
  branch dispatch is proven from a descendant of that commit.
- One skill invocation captures an existing failed run and prints the smallest
  supported reproducer without exposing secrets or raw remotes.
- A pushed debugging branch runs one selected test on one selected OS without
  running unrelated suites.
- Automatic failures and manual successes/failures attempt publication of a
  schema-valid, redacted artifact; cancellation, scan refusal, and upload failure
  produce their exact safe non-artifact states without claiming preservation.
- Checkout/setup/dependency failures can produce the dependency-free minimal
  schema without relying on repository code.
- Classification runs in a separate hosted job/VM at the exact failed SHA;
  inability to establish it produces the applicable inconclusive outcome.
- At least one pytest and one non-pytest incident traverse the state model.
- Seeded secrets are absent from repository-owned console output, logs, JUnit,
  summaries, argv, nested records, filenames, and uploaded artifacts. Pinned
  third-party setup steps receive no secret-bearing values.
- An unrelated changed path blocks dispatch/integration, while an explicitly
  approved evidence-backed expansion succeeds and is recorded.
- Every required job succeeds for the exact candidate SHA/tree/workflow/input
  fingerprint; missing, stale, cancelled, or unexpectedly skipped jobs block
  `matrix-green`.
- A changed candidate invalidates prior downstream green states.
- Request correlation is unique by queryable `run-name` request ID plus exact
  SHA/event/actor, and the manifest repeats the request ID.
- Mandatory dependency-first and full-repository recertification succeeds.
- Git mutations occur only through explicitly approved `git-workflow` actions.
- The primary checkout and unrelated dirty state remain untouched.
- The integrated default-branch candidate reaches `confirmed` only after its own
  required matrix passes.

## Implementation Sequence

1. With separate explicit authorization, sanitize the credential-bearing local
   remote configuration without printing its value.
2. Land the Stage 0 workflow-only dispatch adapter through existing CI.
3. Branch from that default-branch adapter commit.
4. Retrieve the four standards closures and implement the runner diagnostics,
   registered skill/runtime, workflow upload, and policy tests in TDD slices.
5. Perform mandatory dependency-first and repository-wide recertification.
6. Run the exact complete matrix for the immutable candidate.
7. Integrate only through a user-approved `git-workflow` action.
8. Monitor the integrated candidate to `confirmed`.
9. Consider separately reviewed interactive access only if artifact-driven
   debugging remains insufficient.

## Non-goals

- Replacing GitHub Actions or the canonical repository runner.
- Automatically repairing arbitrary failures without evidence review.
- Granting the runtime autonomous Git mutation authority.
- Providing a general-purpose remote shell service in v1.
- Persisting hosted runners after workflow completion.
- Introducing self-hosted runner infrastructure in v1.
- Treating targeted debug runs as a substitute for exact full-matrix
  certification.
