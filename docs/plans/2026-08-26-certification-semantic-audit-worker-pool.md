# Certification Semantic-Audit Worker Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:test-driven-development` while implementing each task. Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` only after the user selects an execution mode. Do not stage or commit without separate authorization.

**Goal:** Make repository-wide semantic certification use fresh, bounded audit subagents at the host's available concurrency without exposing or scheduling the dependency DAG in an LLM context.

**Architecture:** Deterministic code derives the semantic-audit task graph internally from the existing schema-v6 certification graph and drift evidence. A process-safe lease allocator exposes only currently ready tasks, accepts schema-validated final reports, and unlocks successors after passing results; it never serializes the whole DAG to the orchestrating LLM. The `skill-certifier` gateway determines host capacity, fills those slots with fresh subagents, and submits each result back to the allocator.

**Tech stack:** Python 3.11, schema-v6 Officina blueprints, Draft 2020-12 JSON Schema, `jsonschema`, Officina atomic-file and exclusive-lock helpers, dispatcher interfaces, Markdown audit instructions, and pytest.

**Spec:** The user-approved requirements in the originating conversation are restated under Global Constraints; no separate specification file was requested.

## Global Constraints

- The full semantic-audit DAG is code-private. No interface may return it, and the orchestrating LLM must not reconstruct it.
- Code owns task derivation, prerequisite readiness, deterministic ordering, leases, result validation, and terminal run state.
- The host agent runtime owns subagent creation and concurrency. Repository code must not depend on a Codex- or Claude-specific subagent API.
- Every audit attempt uses a fresh subagent. A completed, failed, or `needs-context` worker is never reused for another attempt.
- The gateway discovers currently available host concurrency, excluding itself and already-active agents. It falls back to one worker when capacity cannot be discovered. It must not hard-code the current Codex limit of three audit workers.
- A worker receives exactly one leased task, one reviewed repository and commit, bounded subject evidence, completed prerequisite results, and any prior `needs-context` result. It never receives the complete worklist or DAG.
- Audit workers do not recurse, delegate, sign, certify, modify repository state, or inspect unrelated nodes.
- A task is leasable only after every internal prerequisite audit task has passed. Pending or leased prerequisites keep the task invisible to the gateway.
- A `needs-context` result creates a new attempt for the same task and a fresh worker. Only the requested bounded context and the prior result are added.
- A semantic rejection stops certification. An invalid task packet, malformed report, snapshot mismatch, worker failure, unauthorized scope expansion, or unresolved evidence gap aborts the audit run.
- Certificate currentness is not used as the audit scheduler's readiness signal. Dependencies are signed only after semantic review completes, so requiring current dependency certificates during review would deadlock the existing audit-all-then-sign flow.
- Mechanical certification remains unchanged in this plan. It runs only after the audit pool reports completion and continues to issue certificates dependency-first.
- Binding audit-report digests or worker identities into signed certificate payloads is explicitly out of scope.
- Preserve all unrelated dirty work. This plan currently overlaps no modified certification file; implementation must recheck that condition before every task.

## File and ownership map

| File | Responsibility |
|---|---|
| `skills/skill-certifier/_rtx/_semantic_audit_pool.py` | Strict run-state codec, hidden task derivation, process-safe claiming, report submission, abort, and compact status. |
| `skills/skill-certifier/_rtx/schemas/semantic-audit-result.schema.json` | Canonical final-report contract shared by every audit kind. |
| `skills/skill-certifier/_rtx/tests/test_semantic_audit_pool.py` | Unit and concurrency tests for derivation, leases, prerequisites, retries, aborts, and snapshot pinning. |
| `skills/skill-certifier/_rtx/tests/test_semantic_audit_result_schema.py` | Direct positive and negative schema tests. |
| `skills/skill-certifier/_rtx/blueprints/rtx-semantic-audit-pool.yaml` | Process binding and contract for the private pool interface. |
| `skills/skill-certifier/_rtx/blueprint.yaml` | Registers and exports the pool interface only to the parent `skill-certifier` module. |
| `skills/skill-certifier/SKILL.md` | Capacity discovery, fresh-subagent pool loop, submission, and final certification handoff. |
| `skills/skill-certifier/instructions/audit-interface.md` | One non-recursive interface audit and schema-conforming final report. |
| `skills/skill-certifier/instructions/audit-behavioral-source.md` | One non-recursive source audit consuming supplied interface results. |
| `skills/skill-certifier/instructions/audit-module.md` | One non-recursive module audit consuming supplied child results. |
| `skills/skill-certifier/blueprints/gateway.yaml` | Declares use of the pool interface and owns the orchestration contract. |
| `skills/skill-certifier/blueprints/instructions-audit-*.yaml` | Changes audit output format from Markdown to schema-validated JSON and bumps the three audit interfaces. |
| `skills/skill-certifier/blueprint.yaml` | Updates content ownership and module/source versions. |
| `references/blueprint-schema/runtime_dependencies.json` | Regenerated dependency projection after interface and version changes. |

The new schema belongs to `skill-certifier._rtx.source.semantic-audit-pool`, together with the code that validates it. The three Markdown audit sources promise that output contract; the parent gateway submits their results to the validating pool interface.

## Public behavior and private state

Export one parent-private interface:

```text
skill-certifier._rtx.interface.semantic-audit-pool@1
```

It supports five operations through one dispatcher surface:

```text
initialize --reviewed-repository PATH --reviewed-commit SHA [TARGET ...]
claim RUN_ID --limit N
submit RUN_ID LEASE_ID --report-file PATH
status RUN_ID
abort RUN_ID --reason TEXT
```

The gateway may invoke it; no other module may. `initialize` returns only run identity and counts. `claim` returns at most `N` ready task packets and opaque lease IDs. `status` returns counts, terminal state, active lease IDs, and a terminal reason; it does not return task edges or the pending task inventory.

Run state is stored beneath:

```text
skills/skill-certifier/_build/semantic-audit-runs/<run-id>.json
```

Every mutating operation acquires `<run-id>.json.lock` with `officina.common.atomic_files.exclusive_file_lock`, strictly decodes the complete state, rechecks the repository/commit pin, applies one transition, and atomically replaces the state file. Leases are random 128-bit tokens and identify exactly one `(run_id, task_id, attempt)` tuple.

`_semantic_audit_pool.py` exposes these testable Python boundaries:

```python
@dataclass(frozen=True)
class AuditTask:
    task_id: str
    audit_kind: Literal["interface", "behavioral-source", "module"]
    subject_id: str
    subject_version: int
    node_id: str
    facet_id: str | None
    prerequisites: tuple[str, ...]
    causes: tuple[str, ...]
    evidence_paths: tuple[str, ...]

@dataclass(frozen=True)
class AuditLease:
    lease_id: str
    task_id: str
    attempt: int
    packet: Mapping[str, object]

def derive_audit_tasks(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    currentness: CertificateCurrentnessReport,
    target_node_ids: Sequence[str],
) -> tuple[AuditTask, ...]: ...

def initialize_run(
    repo_root: Path,
    reviewed_commit: str,
    targets: Sequence[str],
) -> Mapping[str, object]: ...

def claim_ready(run_id: str, limit: int) -> tuple[AuditLease, ...]: ...

def submit_report(
    run_id: str,
    lease_id: str,
    report: Mapping[str, object],
) -> Mapping[str, object]: ...

def abort_run(run_id: str, reason: str) -> Mapping[str, object]: ...

def run_status(run_id: str) -> Mapping[str, object]: ...
```

Task ordering is deterministic by `(stale_worklist_index, audit_kind_rank, subject_id, facet_id)`, with `interface < behavioral-source < module`. Readiness is determined only from private prerequisite IDs. Concurrent `claim` calls cannot lease the same task.

## Canonical subagent final report

Every subagent's final response is exactly one JSON object, with no Markdown fence or surrounding prose. The schema file contains this complete contract:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://nullkit.dev/schemas/skill-certifier/semantic-audit-result-v1.json",
  "title": "Semantic Audit Result",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "run_id",
    "lease_id",
    "task_id",
    "attempt",
    "snapshot",
    "subject",
    "status",
    "verdict",
    "consumed_results",
    "evidence",
    "findings",
    "requested_context"
  ],
  "properties": {
    "schema_version": {
      "const": "skill-certifier.semantic-audit-result/v1"
    },
    "run_id": {"type": "string", "minLength": 1},
    "lease_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
    "task_id": {"type": "string", "minLength": 1},
    "attempt": {"type": "integer", "minimum": 1},
    "snapshot": {
      "type": "object",
      "additionalProperties": false,
      "required": ["repository", "commit"],
      "properties": {
        "repository": {"type": "string", "minLength": 1},
        "commit": {"type": "string", "pattern": "^[0-9a-f]{40,64}$"}
      }
    },
    "subject": {
      "type": "object",
      "additionalProperties": false,
      "required": ["audit_kind", "id", "version", "node_id", "facet_id"],
      "properties": {
        "audit_kind": {
          "enum": ["interface", "behavioral-source", "module"]
        },
        "id": {"type": "string", "minLength": 1},
        "version": {"type": "integer", "minimum": 1},
        "node_id": {"type": "string", "minLength": 1},
        "facet_id": {"type": ["string", "null"]}
      }
    },
    "status": {"enum": ["completed", "needs-context", "abort"]},
    "verdict": {"enum": ["pass", "reject", null]},
    "consumed_results": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["task_id", "subject_id", "verdict"],
        "properties": {
          "task_id": {"type": "string", "minLength": 1},
          "subject_id": {"type": "string", "minLength": 1},
          "verdict": {"const": "pass"}
        }
      }
    },
    "evidence": {
      "type": "array",
      "items": {"type": "string", "minLength": 1}
    },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["code", "message"],
        "properties": {
          "code": {"type": "string", "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"},
          "message": {"type": "string", "minLength": 1},
          "path": {"type": ["string", "null"]}
        }
      }
    },
    "requested_context": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["scope", "reason"],
        "properties": {
          "scope": {"type": "string", "minLength": 1},
          "reason": {"type": "string", "minLength": 1}
        }
      }
    }
  },
  "allOf": [
    {
      "if": {"properties": {"status": {"const": "completed"}}},
      "then": {
        "properties": {
          "verdict": {"enum": ["pass", "reject"]},
          "requested_context": {"maxItems": 0}
        },
        "required": ["verdict"]
      }
    },
    {
      "if": {"properties": {"status": {"const": "needs-context"}}},
      "then": {
        "properties": {
          "verdict": {"type": "null"},
          "requested_context": {"minItems": 1}
        }
      }
    },
    {
      "if": {"properties": {"status": {"const": "abort"}}},
      "then": {
        "properties": {
          "verdict": {"type": "null"},
          "findings": {"minItems": 1},
          "requested_context": {"maxItems": 0}
        }
      }
    },
    {
      "if": {
        "properties": {
          "status": {"const": "completed"},
          "verdict": {"const": "pass"}
        }
      },
      "then": {
        "properties": {
          "evidence": {"minItems": 1},
          "findings": {"maxItems": 0}
        }
      }
    },
    {
      "if": {
        "properties": {
          "status": {"const": "completed"},
          "verdict": {"const": "reject"}
        }
      },
      "then": {
        "properties": {"findings": {"minItems": 1}}
      }
    }
  ]
}
```

The pool additionally compares every identity field against the active lease. JSON Schema validity alone cannot establish that the worker reviewed the leased task.

---

### Task 1: Introduce and test the final-report schema

**Files:**

- Create: `skills/skill-certifier/_rtx/schemas/semantic-audit-result.schema.json`
- Create: `skills/skill-certifier/_rtx/tests/test_semantic_audit_result_schema.py`
- Modify: `skills/skill-certifier/_rtx/blueprint.yaml`

**Interfaces:**

- Consumes: Draft 2020-12 validation through the repository's existing `jsonschema` dependency.
- Produces: schema ID `skill-certifier.semantic-audit-result/v1` and a reusable test fixture factory `valid_semantic_audit_result(**overrides) -> dict[str, object]`.

- [ ] **Step 1: Write failing positive and conditional schema tests.** Cover one valid interface pass, source rejection, module `needs-context`, and worker abort. Assert rejection of additional properties, malformed lease/commit IDs, pass without evidence, pass with findings, rejection without findings, `needs-context` without requested context, and abort without a finding.
- [ ] **Step 2: Run the schema tests and verify failure because the schema file is absent.**

  Run: `pytest -q skills/skill-certifier/_rtx/tests/test_semantic_audit_result_schema.py`

  Expected: failure naming the missing schema path.

- [ ] **Step 3: Add the exact schema above and load it with `Draft202012Validator.check_schema`.** Keep the fixture report's repository and commit concrete; do not use placeholder values.
- [ ] **Step 4: Run the schema tests.**

  Run: `pytest -q skills/skill-certifier/_rtx/tests/test_semantic_audit_result_schema.py`

  Expected: all tests pass.

- [ ] **Step 5: Register the schema as content of the new semantic-audit-pool source introduced in Task 2.** Until Task 2 adds that source, keep the ownership edit in the same implementation branch and do not regenerate partial blueprints.
- [ ] **Step 6: If commit authorization has been given, stage only the schema, its test, and their source-registration changes and commit with `Add semantic audit result schema`.**

### Task 2: Build the private, process-safe ready-task allocator

**Files:**

- Create: `skills/skill-certifier/_rtx/_semantic_audit_pool.py`
- Create: `skills/skill-certifier/_rtx/tests/test_semantic_audit_pool.py`
- Create: `skills/skill-certifier/_rtx/blueprints/rtx-semantic-audit-pool.yaml`
- Modify: `skills/skill-certifier/_rtx/blueprint.yaml`
- Modify: `skills/skill-certifier/_rtx/__init__.py`

**Interfaces:**

- Consumes: `derive_repository_certification_state`, `certificate_stale_worklist`, schema-v6 graph topology and facet drift, `exclusive_file_lock`, `atomic_replace_bytes`, and the Task 1 report schema.
- Produces: `skill-certifier._rtx.interface.semantic-audit-pool@1` and the Python boundaries declared under Public behavior and private state.

- [ ] **Step 1: Write failing derivation tests.** Use small schema-v6 graph fixtures for a chain, diamond, shared provider, source with two interface facets, remainder-only drift, module ancestor, certification-basis mismatch, reusable unchanged facet, and sole mechanical `certified-under` drift. Assert exact private prerequisites and deterministic ordering. Assert that the public initialize/status payloads contain counts but no `tasks`, `prerequisites`, `edges`, or `stale_worklist` field.
- [ ] **Step 2: Run the derivation tests and verify the module is absent.**

  Run: `pytest -q skills/skill-certifier/_rtx/tests/test_semantic_audit_pool.py -k 'derive or initialize or status'`

  Expected: collection or import failure for `_semantic_audit_pool`.

- [ ] **Step 3: Implement strict immutable task and run-state values.** Reject unknown keys, duplicate IDs, missing referenced prerequisites, self-dependencies, dependency cycles, non-v6 graphs, paths escaping the reviewed repository, and empty causes for a scheduled task.
- [ ] **Step 4: Implement deterministic task derivation.** Apply the existing certifier rules: interface facet drift creates interface tasks; remainder/source-wide drift creates a source task; required interface tasks precede their source; affected child/source tasks precede their module; ordinary stale dependency nodes precede consumers; a basis mismatch schedules every required semantic layer; a sole mechanical `certified-under` cause schedules no semantic task. Keep the complete graph only inside serialized run state.
- [ ] **Step 5: Make the focused derivation tests pass.**

  Run: `pytest -q skills/skill-certifier/_rtx/tests/test_semantic_audit_pool.py -k 'derive or initialize or status'`

  Expected: all selected tests pass.

- [ ] **Step 6: Write failing lease tests.** Cover `limit=1`, `limit` larger than the ready frontier, deterministic ready selection, no consumer lease while a provider is pending or leased, immediate slot refill after provider pass, and no whole-DAG fields in a lease packet. Assert that each packet includes only snapshot identity, one subject, its causes and paths, passing prerequisite results, and any prior context request.
- [ ] **Step 7: Write a concurrent-claim test.** Use two `ThreadPoolExecutor` callers against one run and assert disjoint lease IDs and task IDs, with total leases no larger than the ready frontier.
- [ ] **Step 8: Implement state transactions.** Lock the sidecar, read and strictly validate the authoritative state, compare the reviewed repository and commit with the live snapshot, apply one transition, and atomically replace exact bytes. Generate lease IDs with `secrets.token_hex(16)`.
- [ ] **Step 9: Implement `claim_ready`.** Validate `1 <= limit <= 64`; lease only pending tasks whose private prerequisites all have passing reports; never return internal prerequisite IDs except the passing results actually consumed by the leased task.
- [ ] **Step 10: Run lease and concurrency tests.**

  Run: `pytest -q skills/skill-certifier/_rtx/tests/test_semantic_audit_pool.py -k 'claim or lease or concurrent'`

  Expected: all selected tests pass.

- [ ] **Step 11: Write failing submission-state tests.** Cover pass unlocking successors, rejection terminating the run, `needs-context` incrementing the same task's attempt and returning it only through a fresh lease, abort terminating the run, malformed JSON aborting the run, identity mismatch on an active lease aborting the run, stale/unknown lease rejection without state mutation, reviewed-commit drift aborting the run, and completion only after every task passes.
- [ ] **Step 12: Implement report loading and submission.** Read `--report-file` as a confined regular file, validate the schema, compare every run/lease/task/attempt/snapshot/subject field with the active lease, validate consumed result IDs against the exact private prerequisites, and apply the terminal transitions defined in Global Constraints.
- [ ] **Step 13: Implement explicit abort and compact status.** `abort` records one nonempty reason and prevents future claims/submissions. `status` reports `pending_count`, `leased_count`, `passed_count`, `attempt_count`, `state`, `active_lease_ids`, and `terminal_reason` only.
- [ ] **Step 14: Run all pool tests.**

  Run: `pytest -q skills/skill-certifier/_rtx/tests/test_semantic_audit_pool.py skills/skill-certifier/_rtx/tests/test_semantic_audit_result_schema.py`

  Expected: all tests pass.

- [ ] **Step 15: Declare the process interface.** Bind `initialize`, `claim`, `submit`, `status`, and `abort` under `skill-certifier._rtx.interface.semantic-audit-pool@1`; allow only caller `skill-certifier`; declare `_build/semantic-audit-runs/` as private operational state and the schema as source-owned read content.
- [ ] **Step 16: If commit authorization has been given, stage only Task 2 files plus the Task 1 ownership registration and commit with `Add semantic audit ready-task allocator`.**

### Task 3: Make all semantic audit workers bounded, non-recursive, and JSON-only

**Files:**

- Modify: `skills/skill-certifier/instructions/audit-interface.md`
- Modify: `skills/skill-certifier/instructions/audit-behavioral-source.md`
- Modify: `skills/skill-certifier/instructions/audit-module.md`
- Modify: `skills/skill-certifier/blueprints/instructions-audit-interface.yaml`
- Modify: `skills/skill-certifier/blueprints/instructions-audit-behavioral-source.yaml`
- Modify: `skills/skill-certifier/blueprints/instructions-audit-module.yaml`
- Create: `skills/skill-certifier/_rtx/tests/test_semantic_audit_instructions.py`

**Interfaces:**

- Consumes: one pool-issued task packet and schema `skill-certifier.semantic-audit-result/v1`.
- Produces: versions 2 of the three existing audit interfaces, each returning exactly one JSON report.

- [ ] **Step 1: Write failing instruction-contract tests.** For all three files require: exactly one assigned subject; exact snapshot/task/lease identity; no recursion or delegation; no repository mutation, signing, or certification; consumption only of supplied passing prerequisite results; abort on invalid packet/snapshot/scope; and a final response consisting solely of schema-conforming JSON. Reject text that permits workers to discover or audit their own dependencies.
- [ ] **Step 2: Run the instruction tests and verify they fail against the current Markdown-result instructions.**

  Run: `pytest -q skills/skill-certifier/_rtx/tests/test_semantic_audit_instructions.py`

  Expected: failures naming the missing isolation and JSON-report requirements.

- [ ] **Step 3: Rewrite the shared worker boundary in each instruction.** Use this normative text in all three, specialized only by subject kind:

  ```md
  Audit exactly the leased subject in the supplied task packet. Do not discover,
  schedule, audit, or delegate any other subject. Do not modify repository state,
  sign, certify, or invoke the mechanical certifier. Consume only the supplied
  passing prerequisite results. If task, lease, attempt, snapshot, subject, or
  permitted-scope identity is inconsistent, stop and return `status: abort`.

  Your final response must be exactly one JSON object conforming to
  `skill-certifier.semantic-audit-result/v1`, with no Markdown fence or surrounding
  prose. Copy identity fields from the task packet exactly.
  ```

- [ ] **Step 4: Preserve the substantive audit criteria.** Interface workers still judge one interface; source workers still consume supplied interface results and reusable facet evidence; module workers still consume supplied child results. Remove instructions that tell a worker to recursively obtain missing child audits.
- [ ] **Step 5: Map outcomes exactly.** Semantic accuracy yields `status: completed` with `verdict: pass|reject`; the smallest legitimate evidence expansion yields `status: needs-context` with `verdict: null`; invalid task identity, unauthorized scope, or an execution/evidence condition that makes the audit unreliable yields `status: abort` with `verdict: null` and at least one finding.
- [ ] **Step 6: Update the three source blueprints.** Change output format from `markdown` to `json`, describe the shared schema, bump each audit interface and source from version 1 to version 2, and update caller actions for `abort` and `needs-context`.
- [ ] **Step 7: Run instruction tests.**

  Run: `pytest -q skills/skill-certifier/_rtx/tests/test_semantic_audit_instructions.py skills/skill-certifier/_rtx/tests/test_semantic_audit_result_schema.py`

  Expected: all tests pass.

- [ ] **Step 8: If commit authorization has been given, stage only the three instructions, three blueprints, and focused tests and commit with `Constrain semantic audit workers`.**

### Task 4: Replace `SKILL.md` steps 2–4 with the leased fresh-subagent pool loop

**Files:**

- Modify: `skills/skill-certifier/SKILL.md`
- Modify: `skills/skill-certifier/blueprints/gateway.yaml`
- Modify: `skills/skill-certifier/blueprint.yaml`
- Modify: `skills/skill-certifier/_rtx/tests/test_semantic_audit_instructions.py`

**Interfaces:**

- Consumes: `skill-drift._rtx.interface.drift-status@3`, `skill-certifier._rtx.interface.semantic-audit-pool@1`, the three audit interfaces at version 2, and `skill-certifier._rtx.interface.certify@2`.
- Produces: a gateway algorithm that never schedules from a DAG and invokes mechanical certification only after pool completion.

- [ ] **Step 1: Add failing gateway assertions.** Require the algorithm to initialize the code-owned pool from the exact drift target and reviewed commit; discover available host capacity; claim only ready tasks; spawn one fresh subagent per lease; submit exact JSON; refill released capacity; use a new worker after `needs-context`; abort on worker/tool/report failure; and call mechanical certification only when `status` is complete. Reject any instruction to receive, derive, sort, traverse, or retain the complete audit DAG.
- [ ] **Step 2: Replace current steps 2–4 with this algorithm:**

  ```md
  2. Invoke the semantic-audit-pool interface to initialize a run for the exact
     reviewed repository, commit, and requested targets. The pool privately derives
     semantic tasks and prerequisites from canonical drift state. Do not request,
     reconstruct, or retain its task graph.

  3. Determine currently available subagent capacity, excluding the orchestrator
     and already-active agents. If the host does not expose capacity, use one. Claim
     at most that many ready tasks from the pool. Spawn one fresh isolated subagent
     for each returned lease and pass it only that lease's packet and named audit
     interface. Never reuse a subagent, including after `needs-context`.

     As workers finish, require their final response to be exactly the schema-defined
     JSON report and submit it with the lease ID. Fill each released slot by claiming
     another ready task. If no task is currently claimable while leases remain active,
     wait for one of those workers; do not infer or bypass a prerequisite. Continue
     until the pool reports complete, rejected, or aborted.

  4. A `needs-context` submission causes the pool to create a new attempt containing
     the prior result and only its requested context. Claim that attempt normally and
     assign a fresh subagent. Stop immediately on semantic rejection. On malformed
     output, worker failure, task or snapshot mismatch, unauthorized scope expansion,
     unresolved evidence, or any pool abort, abort remaining workers, report the exact
     cause, and do not invoke mechanical certification.
  ```

- [ ] **Step 3: Keep step 5's deterministic certifier invocation, but require a completed pool run ID and the same reviewed repository/commit immediately before invocation.** Do not pass caller-authored certificate data or worker reports into the current signer.
- [ ] **Step 4: Update the gateway blueprint.** Add the pool interface, bump the three audit uses to version 2, describe capacity-limited fresh-subagent orchestration, and bump the gateway source and root skill versions.
- [ ] **Step 5: Run focused gateway and instruction tests.**

  Run: `pytest -q skills/skill-certifier/_rtx/tests/test_semantic_audit_instructions.py skills/skill-certifier/_rtx/tests/test_semantic_audit_pool.py`

  Expected: all tests pass.

- [ ] **Step 6: If commit authorization has been given, stage only the gateway instruction, blueprint/version changes, and focused tests and commit with `Pool semantic certification audits`.**

### Task 5: Regenerate contracts and verify repository integration

**Files:**

- Regenerate: `skills/skill-certifier/SKILL.md` generated contract block
- Regenerate: `references/blueprint-schema/runtime_dependencies.json`
- Regenerate only other files reported by the authoritative blueprint synchronizer
- Verify: all files changed in Tasks 1–4

**Interfaces:**

- Consumes: completed Tasks 1–4.
- Produces: synchronized schema-v6 graph, validated dispatcher routes, and an implementation-ready certification workflow.

- [ ] **Step 1: Record exact pre-regeneration status.** Separate pre-existing math-dependency-graph, relocate-nodes, runtime-dependency, and plan changes from this implementation's files. Stop if another session has modified any certification file in this plan.
- [ ] **Step 2: Run the authoritative blueprint synchronizer in check mode through its declared dispatcher interface.** Record the exact generated drift; do not hand-edit generated blocks.
- [ ] **Step 3: Run the synchronizer in apply mode only after confirming its output will not overwrite unrelated session work.** Then rerun check mode and require a clean result.
- [ ] **Step 4: Run focused tests.**

  Run: `pytest -q skills/skill-certifier/_rtx/tests skills/skill-drift/_rtx/tests/test_drift_check.py`

  Expected: all tests pass.

- [ ] **Step 5: Run repository validators.**

  Run: `python3 repo_checks.py --suite validators`

  Expected: exit 0. If sandbox limitations prevent a check, report that separately from product failure.

- [ ] **Step 6: Run the authoritative repository checks appropriate to the changed blueprint and Python closure.** Use focused selectors first, then the full required gate only after the dirty worktree is isolated or stabilized.
- [ ] **Step 7: Exercise one temporary certification fixture end-to-end.** Initialize a run containing a provider and consumer; claim the provider only; submit pass; claim the consumer with the provider result; submit `needs-context`; claim a fresh second consumer attempt; submit pass; require complete status; then invoke the existing mechanical certifier against the unchanged reviewed commit. Assert that no pool response exposes the private DAG.
- [ ] **Step 8: Verify abort behavior end-to-end.** Submit a schema-invalid active-lease report and separately simulate worker failure through `abort`; require terminal abort status and prove that the mechanical certifier was not invoked.
- [ ] **Step 9: Run `git diff --check` for tracked changes and equivalent whitespace checks for new untracked files.** Inspect the exact changed-file list and confirm that no unrelated dirty file was staged, restored, or rewritten.
- [ ] **Step 10: Do not claim the existing repository certified.** The current checkout contains unrelated dirty certification inputs. Fresh repository-wide certification belongs to a later clean, stable reviewed commit.
- [ ] **Step 11: If commit authorization has been given and every required check passed, stage only the implementation-owned files and exact generated projections, show the staged name list, and commit with `Add pooled semantic certification audits`.**

## Acceptance criteria

- The LLM never receives or implements the complete semantic-audit DAG.
- Two concurrent claimers cannot lease the same task.
- A consumer cannot be leased while any required provider audit is pending or active.
- The gateway fills available agent slots and refills them as workers finish.
- Every attempt is assigned to a fresh subagent.
- `needs-context` preserves the bounded prior result but uses a new worker.
- All worker final responses validate against one shared JSON Schema and exactly match their active lease identity.
- Audit instructions prohibit recursion, delegation, mutation, signing, and mechanical certification.
- Rejection and operational problems stop the run and prevent signing.
- The existing deterministic certifier remains responsible for dependency-first issuance after semantic completion.
- No current certificate payload or signing schema changes as part of this plan.

## Rejected alternative: worker fail-fast on uncertified dependencies

Do not implement a worker-side check that refuses to audit whenever a dependency certificate is stale. The current certifier performs semantic review first and signs the selected stale closure afterward. Every consumer of a stale dependency would therefore fail before the dependency could be signed, unless certification were changed to interleave semantic review and issuance node by node. That would enlarge this project into a signing-protocol redesign.

The selected lease allocator provides the intended safety without that deadlock: code keeps non-ready tasks private, workers see only ready tasks, and the gateway waits when all remaining work is blocked by active leases.
