# Simple Certification Audit Pool Plan

**Goal:** Certify a repository without loading or traversing its dependency graph in LLM context. Code returns ready audit tasks; the orchestrator runs each task in one fresh subagent and refills a bounded pool.

**Scope rule:** This is coordination state, not a new security or snapshot system. Existing certification code remains responsible for repository freshness, dependency-first certificate issuance, signatures, and rejecting changed inputs.

**Implementation status:** Implemented on `feat/certification-audit-pool`. The
authoritative blueprint sources and focused tests are current. Generated
contract blocks and the runtime dependency manifest still require the public
blueprint synchronization interface in this worktree.

## 1. Shared neutral DAG format

Create `references/certification-policy/certification-dependency-dag.schema.json`:

```json
{
  "schema_version": "officina.certification-dependency-dag/v1",
  "repository": "/absolute/resolved/repository/path",
  "nodes": [
    {
      "id": "example.source.gateway.interface.run",
      "kind": "interface",
      "owner_node_id": "example.source.gateway",
      "dependencies": ["provider.source.gateway.interface.read"]
    }
  ]
}
```

Rules:

- `nodes` contains every module, behavioral source, and declared interface facet in the selected repository.
- `kind` is `module`, `behavioral-source`, or `interface`; `owner_node_id` is required for interfaces and `null` otherwise.
- `dependencies` contains sorted, unique direct prerequisites.
- Interface dependencies resolve to their terminal source-interface ID. Unresolved interfaces are errors, not widened to their source.
- Structural dependencies are included: interface before owning source, source before owning module, and child module before parent module.
- Evidence-only relations such as `certified-under` are omitted because they do not order semantic audits.
- Nodes are sorted by ID. Duplicate IDs, unknown dependencies, invalid owners, and cycles are errors.
- The format contains no `audited`, `in_progress`, worker, certificate, or verdict fields.

Projection uses existing final node-hash/facet attribution: modules use their direct dependencies; behavioral sources use remainder-facet dependencies; interfaces use their named facet dependencies. The source's complete top-level list is not projected again.

`skill-drift` and the certification scheduler import the same decoder/validator.

## 2. Drift output

Bump the exact-repository interface to `skill-drift._rtx.interface.drift-status@4` and add:

```text
--dag-file PATH
```

For one exact `--repo-root`, code atomically writes the complete DAG and adds to JSON stdout:

```json
{
  "dag_file": "/absolute/path/to/dag.json",
  "stale_vertices": [
    "example.source.gateway.interface.run",
    "example.source.gateway",
    "example"
  ]
}
```

`stale_vertices` is sorted and unique. Code derives it conservatively for every node in the existing stale worklist:

- sole mechanical `certified-under` drift adds no semantic vertex;
- exact interface-facet drift adds that interface, its behavioral source, and module ancestors;
- exact remainder-facet drift adds its behavioral source and module ancestors;
- an unattributed behavioral-source cause—including missing, suspect, or basis-mismatched evidence—adds every owned interface, the source, and module ancestors;
- an unattributed module cause adds that module and its module ancestors;
- ordering dependencies already stale by these rules are included and ordered by the DAG.

This is drift data; it uses no audit terminology or audit state. Conservative fallback may audit extra vertices but never asks the LLM to infer scope.

Without `--dag-file`, current output is unchanged. Multi-repository discovery rejects the option.

## 3. Certification scheduler

Create `skill-certifier._rtx.interface.semantic-audit-scheduler@1`:

```text
initialize PREFIX --dag-file DAG.json --drift-file DRIFT.json
claim PREFIX --capacity K
complete PREFIX TASK_ID --report-file REPORT.json
fail PREFIX TASK_ID --reason TEXT
status PREFIX
abort PREFIX --reason TEXT
```

`PREFIX` is confined below `skills/skill-certifier/_build/semantic-audit-runs/`. The scheduler owns `<PREFIX>.dag.json`, `<PREFIX>.state.json`, `<PREFIX>.inputs/`, and `<PREFIX>.lock`.

The atomic state contains:

```json
{
  "schema_version": "skill-certifier.semantic-audit-state/v1",
  "repository": "/absolute/resolved/repository/path",
  "dag_digest": "sha256:...",
  "required": ["task-a", "task-b"],
  "audited": ["task-a"],
  "in_progress": ["task-b"],
  "reports": {"task-a": {}},
  "evidence_paths": {"unchanged-node": "/repository/.certificates/unchanged-node.jsonl"},
  "status": "active",
  "reason": null
}
```

All operations hold the same exclusive file lock. State updates use temp-file plus atomic replace.

### Initialize

- Validate the DAG with the shared decoder and every `stale_vertices` ID against it.
- Copy the DAG under the prefix and set `required = stale_vertices`, with empty `audited` and `in_progress`.
- Return existing identical state unchanged; reject conflicting prefix reuse.
- Empty `required` initializes as `complete`.

### Claim

Under the lock:

```python
slots = max(capacity - len(in_progress), 0)
ready = [
    task_id
    for task_id in required
    if task_id not in audited
    and task_id not in in_progress
    and all(
        dependency not in required or dependency in audited
        for dependency in dag[task_id].dependencies
    )
]
selected = ready[:slots]
in_progress.update(selected)
```

- Validate `0 <= capacity <= 64`.
- For each selected task, write one bounded input JSON file containing the resolved repository, task ID/kind/owner, direct prerequisite passing reports from scheduler state, and identifiers plus existing certificate-evidence locations for direct dependencies outside `required`. These locations come from existing drift/currentness data; the scheduler does not rediscover evidence.
- Return only `selected` items shaped as `{task_id, kind, input_file}`, plus `in_progress_count`, `audited_count`, and status—never the DAG or pending list.
- Lexicographic task order is sufficient; no scheduling optimization is required.
- Concurrent claims are disjoint because selection and update share one lock.

### Completion and failure

- `complete` accepts only an in-progress task and a schema-valid report with the same `task_id`; a passing report moves the task to `audited` and is stored atomically.
- When `audited == required`, status becomes `complete`.
- A reject/abort report or malformed completion sets terminal status `failed`, clears `in_progress`, and records the reason. `fail` does the same only for an active in-progress task.
- `abort` does the same for an active run with an operator reason. Terminal claims return no work; terminal completions, failures, and aborts are rejected.
- `status` returns counts, current in-progress IDs, status, and reason. It does not return the DAG or pending tasks.
- There are no retries, leases, heartbeats, capability tokens, replay journals, or context-request protocol. A lost worker calls `fail`; repository changes are caught by existing certification freshness checks.

## 4. Audit report and worker boundary

Create `skills/skill-certifier/_rtx/schemas/semantic-audit-result.schema.json`:

```json
{
  "schema_version": "skill-certifier.semantic-audit-result/v1",
  "task_id": "example.source.gateway.interface.run",
  "verdict": "pass",
  "summary": "The assigned interface agrees with its declaration and dependencies.",
  "evidence": ["skills/example/instructions/run.md agrees with the interface declaration"],
  "consumed_dependencies": [
    {"task_id": "provider.source.gateway.interface.read", "verdict": "pass"}
  ],
  "findings": []
}
```

`verdict` is `pass`, `reject`, or `abort`; `summary` is nonempty; `evidence` and `findings` contain strings; `consumed_dependencies` contains only `{task_id, verdict: "pass"}` objects. Findings are empty for pass and nonempty otherwise. On completion, code requires consumed required dependencies to match the direct prerequisite reports in the task input. Additional properties are rejected.

Update `audit-interface.md`, `audit-behavioral-source.md`, and `audit-module.md`:

```md
Audit only the assigned task. Its ordering dependencies have already passed or
have current reusable certification evidence. Do not recursively audit,
schedule, or delegate dependencies. If required dependency evidence is missing,
inconsistent, or cannot be evaluated, return `verdict: "abort"`. Do not modify
or certify repository state. Return exactly one
`skill-certifier.semantic-audit-result/v1` JSON object and no surrounding prose.
```

Bump the three audit interfaces from version 1 to version 2.

## 5. Gateway pool algorithm

Replace `skill-certifier/SKILL.md` steps 2–4 with:

1. Run `drift-status@4` once with `--dag-file`; save its JSON for scheduler initialization.
2. Initialize the scheduler from the DAG and drift output.
3. Determine available subagent slots `K`, excluding the orchestrator. If unknown, use one.
4. Call `claim --capacity K`.
5. Spawn one fresh subagent for each returned item, select the audit interface from `kind`, and pass `input_file` unchanged. Write its exact final JSON to a report file and call `complete PREFIX TASK_ID --report-file FILE`.
6. Never reuse a subagent. After one finishes, call `claim --capacity K` to refill.
7. If claim returns nothing while work is in progress, wait. Never infer dependency readiness.
8. On spawn failure, worker loss, malformed output, reject, or abort, fail the run, stop remaining workers, and do not certify.
9. On scheduler `complete`, invoke the existing mechanical certifier normally. It remains authoritative for final freshness and dependency-first issuance.

The orchestrator sees task IDs, kinds, bounded input-file handles, and counts—not the DAG. Code owns traversal and concurrency state.

## 6. Implementation sequence

1. Add failing tests for the DAG schema/projection, facet attribution, structural edges, sorting, invalid targets, and cycles; implement the shared encoder/decoder.
2. Add failing drift tests for `--dag-file`, complete graph, exact `stale_vertices`, legacy-output compatibility, and neutral terminology; implement `drift-status@4`.
3. Add failing scheduler tests for initialization, chain/diamond readiness, capacity, concurrent disjoint claims, complete/fail/abort, malformed reports, and no DAG/pending leakage; implement the scheduler.
4. Add failing result-schema and instruction tests; update the three audit interfaces and gateway.
5. Regenerate authoritative blueprints; run focused tests, repository validators, and an end-to-end chain/diamond simulation.

## Acceptance criteria

- Producer and consumer share one neutral DAG format.
- `skill-drift` contains no `audited` or `in_progress` state.
- The LLM never receives or traverses the DAG.
- Scheduler claims at most `K - P` ready tasks; concurrent claims are disjoint.
- Required dependencies are audited before a consumer is returned.
- Every task uses a fresh subagent and one standardized JSON report.
- Audit instructions prohibit recursive dependency auditing and abort on missing evidence.
- Any failure prevents mechanical certification.
- Existing mechanical certification is unchanged and performs final freshness checks.
