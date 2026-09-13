# Interactive LLM Testing Skill Implementation Plan

> **For agentic workers:** Execute this plan with `superpowers:subagent-driven-development`; use `superpowers:test-driven-development` for each implementation task, `famulus:git-workflow` for every repository change, and `superpowers:verification-before-completion` before claiming or committing completion.

**Goal:** Create a reusable `interactive-llm-testing` skill that turns scenario descriptions into isolated, interactive LLM test campaigns with typed oracles, preserved failure evidence, resumable state, redacted reports, and an initial Codex/Famulus adapter.

**Tech stack:** Python 3.11+, Officina V6 blueprints, `PythonMachineInterface`, JSON Schema, Codex JSONL events, pytest, and `repo_checks.py`.

**Spec:** This plan is self-contained. Its evidence base is the [source prompt](source-prompt.md), [interactive experiment report](experiment-report.md), [redacted evidence appendix](experiment-evidence.md), [redacted campaign index](campaign-index.json), and the original [setup-interface-manager plan](../2026-08-31-setup-interface-manager.md#task-10-run-isolated-interactive-setup-experiments).

## Distilled experiment lessons

These are requirements for the pipeline, not optional guidance.

| Distilled requirement | Experiment evidence | Pipeline consequence |
|---|---|---|
| Resolve the installed runtime and the worker-selected private-data path; do not trust controller environment hints. | [F00–F01](experiment-report.md#f00--validate-and-install-the-isolated-fixture) exposed a controller/worker path split. Raw retained attempts: `<repository-root>/.famulus/interactive-llm-testing/campaigns/setup-interface-manager-2026-09-01/fixture/evidence/F01-deep-setup*`. | Preflight must compare controller and worker provenance and reject a mismatch before mutation. |
| Exercise the ordinary product boundary in a fresh worker. Direct imports, manual dispatch, resumed threads, or permissive approval settings are not product evidence. | [P03](experiment-report.md#p03--unmanaged-target-executes-without-receipt-claim-or-flow-mutation) preserved two nonconforming attempts under `<repository-root>/.famulus/interactive-llm-testing/campaigns/setup-interface-manager-2026-09-01/evidence/P03-unmanaged/`. | Every interactive run gets a new process and thread; adapters declare the boundary and record tool discovery, Python, approval, and network provenance. |
| Source tests and repository validation do not prove the installed artifact. | [F00](experiment-report.md#f00--validate-and-install-the-isolated-fixture) caught missing package markers only during installed-cache smoke checks. | Installation produces a manifest, installed-file digest, import probe, getter probe, verifier probe, and boundary smoke result before scenarios can run. |
| Failed attempts must be frozen and classified before correction. Expectations must never be weakened in place. | [Failure classification](experiment-report.md#failure-classification) separates product, capability, experimental-setup, and fixture defects. | Attempt directories are immutable; retry creates a new attempt with an explicit predecessor and correction reason. |
| Production observations and synthetic-fixture claims have different evidentiary weight. | The [scenario inventory](experiment-report.md#scenario-inventory) separates P00–P09 from F00–F09. | Each scenario has a required `lane`; reports never promote synthetic results to production claims. |
| State transitions need before/after snapshots while workers are stopped, plus correlated exactly-once evidence. | [P09](experiment-report.md#p09--original-request-resumes-exactly-once) and [F09](experiment-report.md#f09--reverse-teardown-never-resumes-an-ordinary-call) correlate resume flags, launches, counters, and ledger bytes. Raw F09 evidence: `<repository-root>/.famulus/interactive-llm-testing/campaigns/setup-interface-manager-2026-09-01/fixture/evidence/F09-reverse-teardown/`. | Oracles operate on immutable snapshots and correlated event IDs, not prose-only judgments. |
| Dependency tests must cover order, staleness, shared claims, interruption, corruption, and teardown. | [F01](experiment-report.md#f01--deep-setup-through-an-owned-child), [F05](experiment-report.md#f05--stale-b-reruns-only-its-dependent-suffix), and [F07](experiment-report.md#f07--shared-c-survives-until-its-final-claimant) found failures missed by a single happy path. | The adapter supplies a declarative fixture DSL and assertion kinds for graphs, claims, action order, stale suffixes, recovery, and teardown. |
| Malformed authoritative state is restored offline, byte-for-byte; the product under test does not repair its own evidence. | [F08](experiment-report.md#f08--malformed-ledger-fails-closed-and-redacts) used an exact external restore. | Recovery supports controlled snapshot restore only after the failed attempt is frozen. |
| Redaction promises need an explicit surface. Raw prompts and original outputs can contain secrets. | [P08](experiment-report.md#p08--manager-and-ledger-redact-original-arguments) and the [evidence appendix](experiment-evidence.md#redacted-command-transcript) distinguish private raw evidence from reviewable artifacts. | Raw evidence stays mode `0700` in private campaign storage; only scoped, scanned, redacted derivatives are exportable. |
| Isolation includes installer side effects, not only target directories. | The [installation manifest](experiment-report.md#installation-and-isolation-manifest) records the isolated home and installed cache. | Adapters must set an isolated `HOME`, snapshot shell profiles, and fail if installation changes files outside declared roots. |
| Documentation review is a test gate. | [Final repository verification](experiment-report.md#final-repository-verification) caught an incorrect digest and a non-executable command template. | Finalization verifies links, command templates, artifact digests, and the staged repository view. |

The committed report, appendix, and campaign index are the durable, redacted evidence. The complete raw campaign is preserved outside Git at `<repository-root>/.famulus/interactive-llm-testing/campaigns/setup-interface-manager-2026-09-01/`; it is private and must not be copied into the repository. Its adjacent preservation record and 44,783-file SHA-256 manifest verify the copy.

## Proposed architecture

**Architecture:** These lessons imply a generic finite campaign engine for scenario validation, private state, isolation, evidence, evaluation, and reporting, plus registered host adapters for installation, worker launch, event capture, and product-boundary probes. Release 1 includes only `codex-famulus-v1`; scenario manifests cannot inject arbitrary commands or output paths.

### Boundary and non-goals

Release 1 implements this flow:

`scenario prose -> typed campaign manifest -> bounded fixture implementation -> typed execution (fresh workers for interactive cases) -> typed evidence -> machine verdicts -> redacted report and lessons`

The skill does not:

- execute scenario-supplied shell commands;
- reuse, resume, fork, or run ephemeral LLM threads;
- modify the product under test to obtain a pass;
- treat a synthetic fixture result as a production result;
- publish raw prompts, raw model output, credentials, or private state;
- repair product defects automatically; a product failure is frozen and handed off as a separate task.

### Components and ownership

| Component | Planned files | Responsibility |
|---|---|---|
| Human workflow | `skills/interactive-llm-testing/SKILL.md`, `blueprints/gateway.yaml` | Intake, approval boundaries, scenario compilation, campaign loop, failure handling, and report handoff. |
| Contracts | `references/interactive-llm-testing/{campaign.schema.json,scenario.schema.json,fixture.schema.json,campaign-state.schema.json,evidence.schema.json}` | Closed schemas for campaign envelopes, scenarios, declarative fixtures, state, attempts, evidence, and verdicts. |
| Campaign engine | `skills/interactive-llm-testing/_rtx/{_campaign.py,_campaign_state.py,_scenario.py}` | Finite operations, strict state transitions, compare-and-replace persistence, and retry lineage. |
| Isolation and evidence | `skills/interactive-llm-testing/_rtx/{_isolation.py,_evidence.py,_evaluation.py,_report.py}` | Private roots, provenance, snapshots, typed oracles, classification, redaction, digests, and reports. |
| Adapter registry | `skills/interactive-llm-testing/_rtx/_adapters.py` | Exact allowlist and protocol; rejects unknown adapters and arbitrary executables. |
| Initial adapter | `skills/interactive-llm-testing/_rtx/adapters/codex_famulus.py` | Isolated Famulus installation, declarative fixture generation, fresh Codex launch, JSONL event capture, and MCP boundary verification. |
| Templates | `skills/interactive-llm-testing/templates/{scenario.yaml,report.md}` | Human-readable inputs and deterministic report shape. |
| Tests | `skills/interactive-llm-testing/_rtx/tests/`, `skills/interactive-llm-testing/tests/`, `tests/test_interactive_llm_testing_integration.py` | Unit, contract, instruction, installed-cache, and end-to-end coverage. |
| Registration | `skills/interactive-llm-testing/{blueprint.yaml,_rtx/blueprint.yaml,blueprints/gateway.yaml,_rtx/blueprints/rtx-campaign.yaml}` | V6 module ownership, exported finite interfaces, dependency declarations, and process bindings. |

The engine stores campaigns only beneath the getter-selected plugin-data root:

```text
<plugin-data>/interactive-llm-testing/campaigns/<campaign-id>/
  manifest.json
  state.json
  install-manifest.json
  scenarios/<scenario-id>/attempts/<attempt-id>/
    card.json
    preflight.json
    events.jsonl
    final.txt
    controller-actions.jsonl
    manager-results.jsonl
    before/
    after/
    evidence.json
    verdict.json
  report.md
  lessons.json
```

The root and descendants are confined and mode `0700`. State writes use the repository atomic-file interface with exact-byte compare-and-replace. User-supplied campaign IDs, scenario IDs, paths, and adapter names are validated before path construction; symlinks and traversal are rejected.

### Scenario contract

The skill compiles all user scenarios and fixtures into one canonical `campaign.schema.json` request envelope: `{"schema_version": 1, "adapter": "codex-famulus-v1", "scenarios": [...], "fixtures": [...]}`. Each scenario entry is validated by `scenario.schema.json`; this interactive example is authoritative for its required fields:

```json
{
  "schema_version": 1,
  "id": "F01-deep-setup",
  "title": "Deep setup through an owned child",
  "lane": "synthetic",
  "execution": {"kind": "interactive"},
  "objective": "Invoke A's owned child and prove C, B, A setup order.",
  "boundary": {"kind": "mcp-interface", "target": "fixture-a.interface.child"},
  "prompt": "Invoke the target and follow any setup-required result.",
  "fixture_ref": "deep-v1",
  "preconditions": [{"kind": "ledger-absent"}],
  "permissions": [{"kind": "exact-managed-actions"}],
  "secrets": [],
  "expectations": [
    {"kind": "action-order", "required": true, "evidence": {"artifact": "controller-actions", "selector": "$.action"}, "value": ["C", "B", "A"]},
    {"kind": "resume-count", "required": true, "evidence": {"artifact": "manager-results", "selector": "$.resume_original"}, "value": 1},
    {"kind": "ledger-status", "required": true, "evidence": {"artifact": "after-ledger", "selector": "$.active_flow"}, "value": "ready"}
  ]
}
```

All objects use `additionalProperties: false`. `secrets` contains names only, never values. Each expectation requires `kind`, `required`, `evidence.artifact`, `evidence.selector`, and `value`; evaluation returns `pass`, `fail`, or `unavailable`. An unavailable required oracle makes the attempt `failed-experiment`, never pass. `campaign-state.schema.json` fixes campaign/scenario/attempt IDs, status, predecessor, correction class/reason, timestamps, and artifact digests. `evidence.schema.json` fixes provenance, worker identity, artifact inventory, oracle results, classification, and export eligibility.

`execution.kind` is one of:

- `preflight`: installation/isolation verification performed by `prepare`, with no boundary or worker; used by P00/F00;
- `interactive`: requires `boundary` and `prompt`; `run` launches exactly one fresh worker;
- `derived`: requires immutable `evidence_refs` to earlier terminal attempts and no boundary or prompt; `run` evaluates those frozen artifacts without launching a worker, as P09 did from P05.

`boundary.kind`, permission kinds, expectation kinds, and named fixture variants are adapter-registered enums. Free text is worker-prompt data only and is never interpreted as a controller command.

“Implements the tests” has an exact, code-free handshake:

1. `SKILL.md` converts scenario prose into the closed campaign envelope with scenario entries and either registered named fixtures or inline declarative fixture specs.
2. `validate` checks that one envelope and returns its canonical digest. The initial adapter's fixture DSL permits only nodes, owned interfaces, dependency edges, integer versions, marker/counter actions, and declared stale transitions; it permits no source text, executable, argv, import, path, or environment field.
3. `begin` persists those exact canonical envelope bytes. `prepare` passes each fixture spec to the adapter-owned deterministic generator, which writes only `skills/task-fixture-*` paths in a campaign-owned isolated worktree and then runs blueprint generation/validation and installed-cache smoke checks.
4. The generator records an ownership manifest, base SHA, resulting SHA, complete file list, binary diff digest, generated projections, and installed-cache identity. `run` refuses any worktree drift from that manifest.

Generic registered oracles compose all scenario assertions. A scenario requiring behavior outside the fixture DSL or oracle registry is returned as `unsupported`; adding a generator feature or oracle is a separate reviewed repository task, never campaign-authored code. Any product change likewise requires a separate user-authorized task and a new campaign baseline.

### Finite interfaces and state machine

One `_campaign.py` process adapter exposes these interfaces through separate V6 exports:

| Interface | Input | Effect |
|---|---|---|
| `validate` | one manifest on stdin | Read-only schema and adapter validation. |
| `begin` | validated manifest on stdin | Create the private campaign and return its opaque ID. |
| `prepare` | campaign ID | Create isolation roots, implement/validate the bounded fixture, install the exact candidate, and write provenance. |
| `run` | campaign ID and scenario ID | Create one immutable attempt; launch a fresh worker only for `interactive`, or evaluate frozen preflight/derived evidence; capture verdict. |
| `status` | campaign ID | Return redacted campaign/scenario state without mutation. |
| `recover` | campaign ID and scenario ID, plus `{action, predecessor, correction_class, correction_reason}` on stdin | Freeze the current attempt; for `retry-experiment` or `retry-capability`, restore a recorded baseline and create retry lineage; or `cancel`. |
| `finalize` | campaign ID | Require terminal scenarios, scan export surfaces, and render report plus lessons. |

States are `draft -> prepared -> running -> finalized`, with `blocked` reachable from preparation or running. Scenario outcomes are `passed`, `failed-product`, `failed-capability`, or `failed-experiment`. A capability or experiment failure may receive a successor attempt only after its immutable evidence is frozen, the correction is recorded, and a fresh preflight proves the changed capability or harness. Product failures are terminal for that campaign. Retry never edits or reclassifies its predecessor.

`prepare` returns a dry-run summary before any install, authentication copy, host-network call, or external process. `SKILL.md` obtains the exact required approval, then invokes the same operation with the returned nonce. Uncertain completion is resolved by `status` and artifact inspection before retry.

### Adapter protocol

`_adapters.py` defines a typed protocol with no generic command hook:

```python
class InteractiveAdapter(Protocol):
    name: str
    def validate_scenario(self, scenario: Scenario) -> None: ...
    def prepare(self, campaign: Campaign) -> InstallManifest: ...
    def preflight(self, campaign: Campaign, scenario: Scenario) -> PreflightEvidence: ...
    def launch_fresh(self, campaign: Campaign, scenario: Scenario, attempt: Attempt) -> WorkerEvidence: ...
    def snapshot(self, campaign: Campaign, scenario: Scenario, phase: str) -> SnapshotEvidence: ...
    def evaluate(self, scenario: Scenario, evidence: EvidenceBundle) -> list[OracleResult]: ...
```

The initial adapter uses an argv template owned by code, not by the manifest. It creates isolated `HOME`, Codex home, plugin-data, config, and worktree roots; leaves outer `FAMULUS_HOST` and `FAMULUS_PLUGIN_DATA` unset for the worker; resolves the installed Python and plugin ID; launches a new non-ephemeral Codex process; records the new thread ID and JSONL events; and verifies that the requested interface was reached through Famulus MCP.

Before any scenario is eligible as evidence, shared preparation must establish the exact repository SHA, fixture SHA/digest, installed-cache digest, selected Python, plugin ID, getter-selected data path, isolated profiles/roots, and installed import/getter/verifier smoke checks. State-bearing cases additionally require zero live workers during before/after ledger and marker/counter snapshots.

An `interactive` scenario must then establish a new process and thread, its declared approval and network modes, and the adapter evidence required by its `boundary.kind`:

- `mcp-interface`: required tool discovery and the exact ordinary target event;
- `mcp-unavailable-bootstrap`: the expected missing-MCP bootstrap behavior, without claiming an ordinary target event;
- `no-tool-control`: absence of tool/MCP events while testing inert prose;
- `lifecycle-interface`: the exact setup or teardown target and its redirect result.

Preflight and derived cases do not require worker, approval, tool-discovery, network, or boundary evidence unless their own expectations explicitly reference it.

### Evidence, verdicts, and reports

`evidence.json` references immutable artifacts by SHA-256 and records the worker/attempt correlation ID. Oracles are small registered evaluators such as `json-field`, `bytes-equal`, `file-absent`, `sequence`, `count`, `ledger-status`, `claim-set`, `resume-count`, and `redaction-scan`. Adapter-specific oracles extend this registry by name.

Failure classification is evidence-based:

- `failed-product`: the validated boundary ran with conforming preflight, but a product oracle failed;
- `failed-capability`: the host could not provide a required tool, permission, authentication, or network capability;
- `failed-experiment`: the harness, fixture, prompt, baseline, provenance, or evidence was invalid or incomplete.

Raw artifacts remain private. `finalize` exports only `report.md`, `lessons.json`, and a redacted campaign index. The report starts with scope and provenance, then scenario inventory, results by lane, frozen failure classifications, normal-state evidence limits, lessons, and a repeat recipe. Each claim links to an attempt ID and digest. A scoped secret scan covers every exportable byte and explicitly excludes the retained raw prompt/event surfaces from publication.

## Implementation tasks

Each task begins with a failing test, makes the smallest implementation needed, runs its focused gate, updates the relevant blueprint in the same commit, and commits only the listed paths.

### Task 1: Freeze schemas and golden evidence contracts

**Files:** Create `references/interactive-llm-testing/{campaign.schema.json,scenario.schema.json,fixture.schema.json,campaign-state.schema.json,evidence.schema.json}`, `tests/fixtures/interactive_llm_testing/{minimal-scenario.json,deep-scenario.json,valid-evidence.json}`, and the registered skeleton `skills/interactive-llm-testing/{SKILL.md,blueprint.yaml,blueprints/gateway.yaml,_rtx/blueprint.yaml,_rtx/blueprints/rtx-init.yaml,_rtx/__init__.py,_rtx/_scenario.py,_rtx/tests/test_scenario.py}`.

- [ ] Write failing tests for the campaign envelope; conditional preflight/interactive/derived fields and immutable evidence references; closed objects; ID/path confinement; required lane/oracle/evidence fields; required-vs-optional unavailable evidence; registered enum resolution; canonical serialization; the declarative fixture DSL; and rejection of source/command/path/environment fields.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_scenario.py --jobs 1`; expect FAIL because the parser and schemas do not exist.
- [ ] Implement immutable dataclasses and schema-backed parsing from stdin bytes. Preserve unknown adapter/oracle rejection as a hard error. The minimal parent `SKILL.md` states that the skill is unusable until the controller task; it exposes no invented execution path.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_scenario.py --jobs 1` and `./repo_checks.py --task validators --validator skill-maker/blueprints --jobs 1`; expect PASS.
- [ ] Commit as `feat: scaffold interactive testing contracts`.

### Task 2: Add confined campaign state and immutable attempts

**Files:** Create `_rtx/{_campaign_state.py,tests/test_campaign_state.py}` and update `campaign-state.schema.json`; modify `src/officina/common/blueprint.yaml` and the syncer-owned `references/blueprint-schema/runtime_dependencies.json` to grant `common.interface.atomic-files` version 2 to `interactive-llm-testing._rtx`.

- [ ] Write failing tests for mode `0700`, getter-selected root confinement, symlink/traversal rejection, compare-and-replace, legal transitions, one active attempt, immutable frozen attempts, retry lineage, and interruption recovery.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_scenario.py --selector skills/interactive-llm-testing/_rtx/tests/test_campaign_state.py --jobs 1`; expect FAIL because campaign storage and its caller grant do not exist.
- [ ] Implement campaign storage using `common.interface.famulus-paths-get` and `common.interface.atomic-files`; never accept an output root from a manifest or CLI.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_scenario.py --selector skills/interactive-llm-testing/_rtx/tests/test_campaign_state.py --jobs 1` and `./repo_checks.py --task validators --validator skill-maker/blueprints --jobs 1`; expect PASS. Commit as `feat: add resumable interactive campaign state`.

### Task 3: Implement isolation and provenance preflight

**Files:** Create `_rtx/{_isolation.py,tests/test_isolation.py}` and extend `evidence.schema.json`.

- [ ] Write failing tests for isolated `HOME`/Codex/plugin/config/worktree roots, outer host/plugin-data removal, worker-selected path equality, stopped-worker snapshots, installed-file digests, shell-profile detection, and cleanup limited to campaign-owned paths.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_isolation.py --jobs 1`; expect FAIL because isolation is absent.
- [ ] Implement preflight records and fail-closed provenance comparisons. Cleanup must refuse unknown paths and preserve failed-attempt evidence.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_isolation.py --jobs 1`; expect PASS. Commit as `feat: enforce interactive campaign isolation`.

### Task 4: Add typed evidence, oracles, and classification

**Files:** Create `_rtx/{_evidence.py,_evaluation.py,tests/test_evidence.py,tests/test_evaluation.py}`.

- [ ] Write failing tests for artifact digests, before/after snapshots, sequence/count/bytes/JSON/ledger/claim/resume/redaction oracles, unavailable required evidence, and the three failure classes.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_evidence.py --selector skills/interactive-llm-testing/_rtx/tests/test_evaluation.py --jobs 1`; expect FAIL because evidence and evaluation are absent.
- [ ] Make oracle evaluation deterministic and side-effect-free. Prohibit prose-only pass verdicts.
- [ ] Test exact secret canaries across only the declared export surface while proving raw files remain outside it.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_evidence.py --selector skills/interactive-llm-testing/_rtx/tests/test_evaluation.py --jobs 1`; expect PASS. Commit as `feat: evaluate typed interactive evidence`.

### Task 5: Implement the finite campaign controller

**Files:** Create `_rtx/{_campaign.py,tests/test_campaign.py,blueprints/rtx-campaign.yaml}` and update the registered `_rtx/{blueprint.yaml,__init__.py}` plus parent namespace exports.

- [ ] Write failing interface tests for `validate`, `begin`, `prepare`, `run`, `status`, `recover`, and `finalize`, including stdin shape, positional bounds, authorization nonce, exact output schema, uncertain completion, and illegal transitions.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_campaign.py --jobs 1`; expect FAIL because controller interfaces are absent.
- [ ] Implement the controller over Tasks 1–4. `run` creates an attempt before evaluation and freezes it on every exit path; only an `interactive` attempt launches a worker.
- [ ] Register each operation as a distinct V6 interface even though one Python module implements them.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_campaign.py --jobs 1` and `./repo_checks.py --task validators --validator skill-maker/blueprints --jobs 1`; expect PASS. Commit as `feat: add finite interactive campaign controller`.

### Task 6: Add the adapter registry and Codex/Famulus adapter

**Files:** Create `_rtx/{_adapters.py,adapters/__init__.py,adapters/codex_famulus.py,tests/test_adapters.py,tests/test_codex_famulus.py}`; update `rtx-campaign.yaml` dependencies.

- [ ] Write failing tests proving only `codex-famulus-v1` is selectable; manifest text cannot alter executable/argv; every launch gets a new process/thread; `resume`, `fork`, `last`, and ephemeral modes are rejected.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_adapters.py --selector skills/interactive-llm-testing/_rtx/tests/test_codex_famulus.py --jobs 1`; expect FAIL because the registry and adapter are absent.
- [ ] Add fixture-backed event streams for successful boundary use, missing tool discovery, wrong Python, approval denial, network denial, interrupted action, and malformed output.
- [ ] Implement installed-cache import/getter/verifier/boundary smoke checks and exact worker/controller path comparison.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_adapters.py --selector skills/interactive-llm-testing/_rtx/tests/test_codex_famulus.py --jobs 1`; expect PASS. Commit as `feat: add Codex Famulus interactive adapter`.

### Task 7: Add bounded fixture implementation and variants

**Files:** Create `_rtx/{_fixture.py,tests/test_fixture.py}` and `tests/fixtures/interactive_llm_testing/{fixture_specs/,campaigns/setup-manager-20.json}`; update adapter and evidence contracts.

- [ ] Write failing tests for deterministic DSL-to-overlay generation, ownership manifest, base/result SHA, complete file list, binary diff digest, generated projections, package markers, installed identity, and rejection of engine/adapter/product paths outside the generated overlay.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_fixture.py --jobs 1`; expect FAIL because fixture generation is absent.
- [ ] Encode the complete P00–P09/F00–F09 campaign as one reusable manifest. Mark P00/F00 `preflight`, P09 `derived` from the frozen P05 attempt, and the remaining scenarios `interactive`. The suite must cover bootstrap without MCP, inert setup prose, unmanaged calls, first-use suggestion with withheld permission, follow-and-resume, fresh-process persistence, exact setup/teardown redirection, argument redaction, exactly-once resumption, deep order, duplicate/busy, interrupted recovery, cancel, stale suffix, explicit invalidation, both shared-claim orders, malformed state, and reverse teardown.
- [ ] Provide declarative fixture specs for production-control, deep `A -> B -> C`, diamond `A/D -> C`, stale-B v2, interrupted action, malformed ledger, and reverse teardown; generate all overlay source from those specs.
- [ ] Require validators and installed-cache smoke before a variant becomes runnable. Preserve both setup-order histories for the diamond.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_fixture.py --jobs 1` and `./repo_checks.py --task validators --validator skill-maker/blueprints --jobs 1`; expect PASS. Commit as `test: add bounded interactive fixture variants`.

### Task 8: Register and pressure-test the skill workflow

**Files:** Rewrite the registered skeleton `skills/interactive-llm-testing/{SKILL.md,blueprint.yaml,blueprints/gateway.yaml}`; create `skills/interactive-llm-testing/{templates/scenario.yaml,templates/report.md,tests/test_instructions.py,tests/pressure_test_instructions.py}` and `docs/plans/interactive-llm-testing/pressure-tests.md`; update `docs/plans/interactive-llm-testing/README.md`, the parent `_rtx/blueprint.yaml`, and generated projections required by validation.

- [ ] Write instruction tests for trigger boundaries, manifest compilation, permission checkpoint, fresh-worker rule, failure freezing, lane distinction, offline restore, no product auto-repair, scoped redaction, and final report contents.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/tests/test_instructions.py --jobs 1`; expect FAIL because the skill workflow is absent.
- [ ] Write `SKILL.md` as a finite controller loop that calls only the registered machine interfaces and records a decision whenever scenario prose is ambiguous.
- [ ] Implement `pressure_test_instructions.py` as an executable adapter-driven runner with three fixed cases: normal campaign, capability failure, and adversarial direct-shell/bypass/resume request. It creates fresh workers through `run`, stores raw attempts in getter-selected campaign storage, and rewrites `docs/plans/interactive-llm-testing/pressure-tests.md` with only redacted case IDs, manifest digests, public-interface traces, verdicts, and lessons.
- [ ] Add the redacted pressure-test record to the dossier index.
- [ ] Run `<repository-root>/skills/interactive-llm-testing/tests/pressure_test_instructions.py --case all`; require exit 0, three distinct new thread IDs, first product actions through the public boundary, the expected capability classification, and zero forbidden direct-command/resume events. On failure, freeze evidence, revise instructions, and rerun a successor attempt.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/tests/test_instructions.py --jobs 1` and `./repo_checks.py --task validators --validator skill-maker/blueprints --jobs 1`; expect PASS. Commit as `feat: add interactive llm testing skill`.

### Task 9: Render reports and migrate the experiment lessons

**Files:** Create `_rtx/{_report.py,tests/test_report.py}` and `docs/plans/interactive-llm-testing/pipeline.md`; update `docs/plans/interactive-llm-testing/README.md` and the two setup-manager experiment documents only where a canonical-pipeline cross-link is needed.

- [ ] Write golden tests for deterministic inventory, per-lane claims, attempt/digest links, failure classification, normal-state limit, lessons, repeat recipe, missing artifacts, and secret-scan failure.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_report.py --selector skills/interactive-llm-testing/tests/test_instructions.py --jobs 1`; expect FAIL because report rendering is absent.
- [ ] Seed the documentation examples from the 20-scenario experiment without copying private raw logs. Link the durable report, appendix, and campaign index, and identify the preserved private campaign as non-repository evidence.
- [ ] Add the implemented pipeline guide to the dossier index.
- [ ] Verify every documented command is executable and every linked repository path exists.
- [ ] Run `./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_report.py --selector skills/interactive-llm-testing/tests/test_instructions.py --jobs 1` and `git diff --check`; expect PASS. Commit as `docs: document reusable interactive testing pipeline`.

### Task 10: End-to-end acceptance and final review

**Files:** Create `tests/test_interactive_llm_testing_integration.py`; modify only files proven necessary by failures.

- [ ] Run `./repo_checks.py --task tests:shared --selector tests/test_interactive_llm_testing_integration.py --jobs 1`; expect FAIL before the integration test and full public flow exist.
- [ ] In an isolated temporary home, execute and verify the complete P00–P09/F00–F09 manifest produced by Task 7 through public interfaces only.
- [ ] Assert new thread IDs, expected boundary events, exact action/counter/ledger transitions, immutable discarded attempts, failure class, redacted exports, and a repeatable second campaign.
- [ ] Run the focused matrix:

```bash
./repo_checks.py --task tests:shared --selector skills/interactive-llm-testing/_rtx/tests/test_scenario.py --selector skills/interactive-llm-testing/_rtx/tests/test_campaign_state.py --selector skills/interactive-llm-testing/_rtx/tests/test_isolation.py --selector skills/interactive-llm-testing/_rtx/tests/test_evidence.py --selector skills/interactive-llm-testing/_rtx/tests/test_evaluation.py --selector skills/interactive-llm-testing/_rtx/tests/test_campaign.py --selector skills/interactive-llm-testing/_rtx/tests/test_adapters.py --selector skills/interactive-llm-testing/_rtx/tests/test_codex_famulus.py --selector skills/interactive-llm-testing/_rtx/tests/test_fixture.py --selector skills/interactive-llm-testing/_rtx/tests/test_report.py --selector skills/interactive-llm-testing/tests/test_instructions.py --selector tests/test_interactive_llm_testing_integration.py --jobs 1
./repo_checks.py --task validators --validator skill-maker/blueprints --jobs 1
```

- [ ] Stage only Task 1–10 owned paths, inspect `git diff --cached --name-only` and `git diff --cached`, then run `./repo_checks.py --suite precommit --jobs 1 --repository-view staged`. If sandbox capability is the only failure, rerun the identical staged gate host-capably and retain both results.
- [ ] Obtain independent reviews for contract/safety, experiment alignment, executability, and documentation informativeness/conciseness. Fix findings and rerun the affected gate until every reviewer is green.
- [ ] Commit integration-only changes as `test: verify interactive llm testing pipeline`.

## Acceptance criteria

- A zero-context agent can supply scenario prose and obtain a validated manifest, bounded fixture implementation, isolated campaign, machine verdicts, redacted report, and repeat recipe without inventing commands.
- Every accepted interactive scenario uses a new non-ephemeral worker and satisfies its declared boundary-kind oracle; preflight and derived scenarios prove their declared frozen evidence without launching one.
- A pass is impossible when provenance, required evidence, or a required oracle is unavailable.
- Attempts are immutable; corrections create explicit retry lineage and retain the original failure class.
- Production, synthetic, capability, product, and experiment claims remain distinguishable in state and reports.
- Private roots, snapshots, and raw LLM material never enter exportable artifacts; scoped redaction tests include exact canaries.
- The initial Codex/Famulus adapter reproduces the experiment's deep, stale, shared, interrupted, corrupt-state, and teardown evidence patterns.
- Focused tests, blueprint validation, staged precommit, and all independent reviews are green for the exact candidate.
