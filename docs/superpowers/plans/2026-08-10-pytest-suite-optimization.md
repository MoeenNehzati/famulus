# Pytest Skill-Suite Refactoring Plan

> **Status:** Completed as the fixture/refactoring pass recorded in the ledger.
> Later runner and scheduling work is documented by
> [Native Pytest Repository Runner Design](../specs/2026-08-10-native-pytest-runner-design.md).

> **Execution:** Use `superpowers:subagent-driven-development`. Pass authors,
> adjudicators, tie-breakers, and final verifiers must be distinct agents.

**Goal:** Review every canonical skill-owned pytest suite and apply clear
pytest-native refactors that remove repeated preparation or process startup,
without changing what behavior, diagnostics, or platform boundaries are tested.

**Time budget:** Two hours from the start of Pass 1. Favor a complete,
explanation-backed sweep over hotspot profiling or repeated performance trials.

**Authoritative artifacts:**

- `docs/test-refactor-ledger/README.md` — policy, state machine, assignments,
  progress, and final summary.
- `docs/test-refactor-ledger/batch-a.md`
- `docs/test-refactor-ledger/batch-b.md`
- `docs/test-refactor-ledger/batch-c.md`
- `docs/test-performance-audit.md` — prior observations and final suite timing.

The three batch files form one ledger. Agents edit only their assigned batch,
so parallel work never writes the same file.

## Completed foundation

- `b7cf40c` restored traversal-safe standard validation and repository-root
  pytest imports.
- `e32ba98` added canonical suite/task benchmark tooling, the fixture probe, and
  atomic wakeup-test discovery policy.
- `e4c1c86` made direct benchmark observations use fresh pytest caches and
  restored selected-root import state exactly.

The benchmark tooling remains available for diagnosis, but this plan does not
require hotspot profiling, per-candidate paired trials, or worktree matrices.

## Execution status

- Task 2 complete: 73 files inventoried in three conflict-free ledger batches.
- Task 3 complete: 73 Pass 1 explanations.
- Tasks 4-5 complete: 73 independent adjudications and 15 third-agent
  tie-breaks; 10 files ended optimized.
- Task 6 complete: fresh Pass 3 agents verified 73/73 entries after one focused
  P04 fix and a root integration docstring-policy fix round.
- Task 7 partially complete: precommit passed at default and one-job budgets.
  Full-suite observations remain non-green due unrelated native-keyring,
  dirty-tree, browser, and live-marketplace failures documented in the ledger
  and performance audit.

## Non-negotiable invariants

- Preserve every behavioral case, assertion, expected diagnostic, platform
  policy, and focused-test workflow.
- Keep subprocess coverage when the contract is process startup, environment
  inheritance, executable discovery, serialization across a process boundary,
  signals, timeouts, concurrency, or persistence across invocations.
- A direct `main()` or function call may replace repeated subprocesses only
  when process isolation is not the behavior under test. Retain at least one
  executable smoke test for each converted CLI boundary.
- Function scope is the default for mutable state. Broader fixtures expose only
  immutable values, factories, or automatically reset state.
- Module/session fixtures are worker-local under xdist. Do not claim or depend
  on one construction across all workers.
- Keep `--dist worksteal`; do not add xdist grouping, `loadfile`, or `loadscope`.
- Do not introduce test-owned `lru_cache`, mutable module globals, or cleanup
  that depends on test order.
- Focused skill tests must run from the repository root with ordinary pytest.
- Do not edit production code merely to make a test refactor convenient.
- Preserve unrelated dirty paths. Root stages and commits exact approved paths;
  subagents do not push.

## Pytest refactor catalog

Each proposal must name one or more catalog IDs.

| ID | Repetition or ownership problem | Preferred pytest mechanism | Required safety evidence |
| --- | --- | --- | --- |
| P01 | Repeated immutable YAML/JSON/schema parsing | module-scoped fixture | Consumers never mutate the returned value |
| P02 | Repeated immutable repository graph/catalog construction | module or session fixture | Worker-local lifetime is sufficient; consumers are read-only |
| P03 | Shared baseline with mutable consumers | broad read-only fixture plus function-scoped copy factory | Base remains unchanged after every consumer |
| P04 | Repeated filesystem template creation | `tmp_path_factory` template plus per-test copy/factory | Mutations cannot leak between tests |
| P05 | Repeated environment setup/restoration | `monkeypatch` fixture or composed fixture | Parent environment is restored automatically |
| P06 | Repeated stdout/stderr/log capture | `capsys`, `capfd`, or `caplog` | Exact output assertions remain |
| P07 | Repeated CLI subprocess where process behavior is not tested | fixture calling parser/`main()` in-process | Two consecutive calls are characterized; required process smoke remains |
| P08 | Repeated cleanup code | `yield` fixture/finalizer | Cleanup runs on success and failure |
| P09 | Repeated identical case shape | `pytest.mark.parametrize` with explicit IDs | Inputs, assertions, and understandable node IDs are preserved |
| P10 | Optional capability/platform setup | built-in markers/fixtures with standing skip annotations | No newly skipped canonical behavior |
| P11 | Repeated runtime-module loading | module fixture only after state audit | Globals, registries, caches, handlers, and environment reads are reset or proven inert |
| P12 | Existing pytest lifecycle already appropriate | no change | Explain why broader scope or direct calls would weaken isolation |

Parametrization alone is not a performance claim. Fixtures are used only when
their lifetime matches real preparation, ownership, or cleanup.

## Ledger entry contract

Every canonical test file under an assigned skill suite receives one entry with
all fields below. No implementation starts while a Pass 1 field is missing.

```markdown
### `relative/path/to/test_file.py`

- Canonical task:
- Item/behavior summary:
- Current pytest features:
- Repeated preparation or process work:
- Mutable/global/process boundaries:
- Pass 1 author:
- Pass 1 state: `propose` | `already-efficient` | `no-safe-change`
- Pass 1 recommendation and catalog IDs:
- Required retained coverage:
- Focused verification command:
- Pass 2 adjudicator:
- Pass 2 verdict: `agree` | `disagree`
- Pass 2 rationale:
- Tie-breaker and decision: `not-needed` or agent, verdict, rationale
- Approved implementation:
- Files changed:
- Focused result:
- Pass 3 verifier:
- Pass 3 verdict: `pass` | `fail`
- Pass 3 evidence/findings:
- Final state: `optimized` | `already-efficient` | `no-safe-change` | `blocked`
```

## Batch ownership

### Batch A

- `skills/cloud-files/_rtx/tests`
- `skills/connect-google/_rtx/tests`
- `skills/daily-plan/_rtx/tests`
- `skills/email-client/_rtx/tests`
- `skills/email-triage/_rtx/tests`
- `skills/find-handoff-candidates/_rtx/tests`

### Batch B

- `skills/g-calendar/_rtx/tests`
- `skills/initialize-tdd/_rtx/tests`
- `skills/install-assistant-tools/_rtx/tests`

### Batch C

- `skills/list-manager/_rtx/tests`
- `skills/math-dependency-graph/_rtx/tests`
- `skills/pdf-to-markdown/_rtx/tests`
- `skills/recurring-tasks/_rtx/tests`
- `skills/skill-certifier/_rtx/tests`
- `skills/skill-drift/_rtx/tests`
- `skills/skill-maker/_rtx/tests`

## Task 2: Initialize the durable ledger — 10 minutes

- [ ] Create the ledger policy/index and three batch files.
- [ ] Populate every current `test_*.py` and `validate_*.py` file under the 16
  canonical skill test directories. Generated inventory, not prose counts, is
  authoritative.
- [ ] Record each suite's canonical runner task and focused pytest command.
- [ ] Record Pass 1, Pass 2, tie-break, and Pass 3 agent assignments before
  agents start. No agent may review or verify its own earlier work.
- [ ] Record start time, the 90-minute implementation freeze, and the two-hour
  stop time in `README.md`.

## Task 3: Pass 1 — explain the current suite and propose action — 25 minutes

Run three diagnosis agents concurrently, one per batch. Pass 1 is read-only
except for its assigned ledger batch.

For every test file, the agent must:

- [ ] Read the complete file and directly used test helpers.
- [ ] Explain what behavior the file protects and which process/platform
  boundaries are material.
- [ ] Inventory current fixtures, scopes, parametrization, monkeypatch/capture
  use, temporary-path setup, parsed artifacts, repository/runtime loading, and
  subprocess calls.
- [ ] Identify concrete repetition that a catalog mechanism can remove, or
  explain why the current lifecycle is already appropriate.
- [ ] Select exactly one Pass 1 state and give a file-specific recommendation.
- [ ] State required retained coverage and an exact focused command.

Pass 1 agents do not edit tests and do not use timing as a gate.

## Task 4: Pass 2 — independently adjudicate and implement — 45 minutes

Use three fresh agents concurrently. Rotate ownership so no Pass 2 agent reads
the batch it authored in Pass 1. Each agent reads the policy, every completed
entry in its assigned batch, and the corresponding test code.

For every entry:

- [ ] Independently decide `agree` or `disagree` and write the rationale before
  editing code.
- [ ] If agreeing with `already-efficient` or `no-safe-change`, verify and close
  the implementation field without editing.
- [ ] If agreeing with `propose`, add a focused characterization first when
  mutable state, repeated in-process calls, or process-boundary retention needs
  proof; observe RED where the recommendation describes a missing guarantee.
- [ ] Implement the smallest approved refactor using the cited catalog IDs.
- [ ] Run the entry's focused command and record pass/skip totals.
- [ ] Record exact changed files and retained subprocess nodes.
- [ ] If disagreeing, make no code change for that entry, record the contrary
  analysis, and mark it for tie-break.

Agents edit only skill paths and the ledger batch assigned to them. Batch paths
are disjoint, so parallel implementation cannot overlap.

## Task 5: Resolve disagreements — included in the Pass 2 window

For each disagreement, use a third fresh agent who was neither Pass 1 author
nor Pass 2 adjudicator.

- [ ] Read the policy, both explanations, complete test file, and directly used
  helpers.
- [ ] Write a definitive `implement`, `already-efficient`, or `no-safe-change`
  decision with catalog IDs and retained-coverage reasoning.
- [ ] If the decision is `implement`, add any missing characterization,
  implement the smallest change, run the focused command, and record evidence.
- [ ] If implementation cannot be completed safely inside 15 minutes, record
  `blocked` with the exact unresolved contract rather than weakening the test.

The tie-breaker decides technical substance; it does not choose by vote count.

## Task 6: Pass 3 — verify that approved decisions were achieved — 20 minutes

Use fresh verification agents, again one per batch and not a Pass 2 implementer
for that batch. Pass 3 agents are read-only except for their ledger verdicts.

For every entry:

- [ ] Compare the final code with the approved Pass 2 or tie-break decision.
- [ ] Confirm the cited pytest feature is genuinely used with the documented
  scope and ownership.
- [ ] Check that assertions, inputs, diagnostics, parametrization IDs, skips,
  and material subprocess/platform boundaries remain.
- [ ] Check mutable fixtures return copies or restore automatically and that two
  consecutive in-process CLI calls remain isolated.
- [ ] Run the focused command for every changed suite, not necessarily once per
  file when one command covers the batch.
- [ ] Record `pass` or `fail` with concrete evidence.

On failure, return the finding to the responsible Pass 2/tie-break agent for one
fix round, then have the same Pass 3 verifier recheck the exact finding.

## Task 7: Final integration and suite verification — 15 minutes

- [ ] Root reviews the ledger for missing fields and unresolved Pass 3 failures.
- [ ] Stage only ledger-approved skill-test changes and reporting artifacts.
- [ ] Run pre-commit and full once at the live default job budget.
- [ ] Run pre-commit and full once with `--jobs 1` if the two-hour stop permits;
  otherwise run pre-commit at one job and record full one-job as deferred.
- [ ] Record wall times as observations only; do not make statistical speedup
  claims from single runs.
- [ ] Reconcile pass/skip totals and investigate any coverage change.
- [ ] Update `docs/test-performance-audit.md` with final suite results and a
  ledger summary.
- [ ] Commit the exact approved batch on `master`; never push.

## Progress and time control

- Report after ledger initialization, each completed pass, each tie-break, each
  fix round, and final suite verification. During longer work report at least
  every 15 minutes.
- At 60 minutes, report completed Pass 1 entries and Pass 2 decisions.
- At 90 minutes, freeze new implementation. Remaining time is for Pass 3,
  fixes, canonical suites, and reporting.
- At 110 minutes, start final suite verification even if some entries are
  `blocked`; all files must still have complete explanations and decisions.
- At two hours, stop and report exact completed, blocked, and failed-verification
  entries. Do not hide incompleteness or weaken a test to meet the clock.

## Completion criteria

- Every canonical skill test file has a complete three-pass ledger entry.
- Every implementation was independently agreed or tie-broken before editing.
- Every changed entry passes independent Pass 3 verification.
- Pytest fixture scopes match real ownership and remain safe under xdist.
- Material subprocess, concurrency, timeout, signal, encoding, platform, and
  persistence boundaries remain covered.
- Pre-commit and full suites pass at the live default job budget.
- Performance claims are limited to directly observed suite timings.
