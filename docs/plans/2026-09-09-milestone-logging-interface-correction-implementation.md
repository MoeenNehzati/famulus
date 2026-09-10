# Milestone Logging Interface Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace milestone logging's two mode-heavy machine interfaces with nine explicit interfaces whose arguments have independent, additive effects.

**Architecture:** Keep the existing writer and timeline modules as shared implementations. Thin dispatcher adapter classes select one fixed operation each; source blueprints expose one contract and process-binding pattern per operation. Typed metadata is always part of the session record, while optional `run` adds identity fields and a byte-identical journal mirror.

**Tech Stack:** Python 3, `argparse`, pytest, Officina v6 behavioral-source blueprints, generated Famulus skill projections.

**Spec:** `docs/plans/2026-09-09-milestone-logging-interface-correction-design.md`

## Global Constraints

- Work only in the isolated implementation worktree; preserve unrelated changes in the primary checkout.
- Do not commit, push, merge, publish, or modify the installed plugin cache without separate user authorization.
- Delete the private exports `milestone-logging._rtx.interface.record` and `milestone-logging._rtx.interface.timeline`; keep `milestone-logging.interface.default@1`.
- Export exactly nine version-1 executable interfaces: `record-progress`, `record-completion`, `session-path`, `run-path`, `list-sessions`, `show-latest-session`, `show-session`, `show-run`, and `read-run-json`.
- No interface argument may conditionally validate, suppress, replace, or reinterpret another argument. Optional effects are additive.
- Recording always requires a non-empty role. Typed metadata is legal and retained without a run. Optional `run` adds `run`, `session`, and `agent` fields plus one byte-identical run-journal append.
- Reject over-budget fields independently using serialized-JSON byte quotas; never truncate or evict one field because another field is present.
- Preserve existing JSONL readability and existing safe run-ID validation.
- Do not change `skills/skill-maker/_rtx/_blueprint_syncer.py`, `mcp.json`, historical plans, or the installed plugin cache.
- Treat live session identity as a documented external-host blocker; do not add guessed transcript, request, client, or MCP-process identities.
- Use test-first red-green cycles for every production behavior change.

---

### Task 1: Writer operations and additive record serialization

**Files:**
- Modify: `skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py`
- Modify: `skills/milestone-logging/_rtx/_milestone_writer.py`
- Modify: `skills/milestone-logging/_rtx/_milestone_interface.py`

**Interfaces:**
- Consumes: existing `log_path(session, agent)`, `run_journal(run)`, and `_append_line(target, line)` helpers.
- Produces: adapter entries `RecordProgress`, `RecordCompletion`, `SessionPath`, and `RunPath`; shared callable `main(operation: str, argv: list[str] | None = None, *, prog: str) -> int`.

- [ ] **Step 1: Add failing adapter and required-role tests**

Add direct adapter tests that instantiate all four classes and assert:

```python
assert RecordProgress().run(["work", "--role", "worker"]) == 0
assert RecordCompletion().run(["finished", "--role", "worker"]) == 0
assert SessionPath().run([]) == 0
assert RunPath().run(["nightly-01"]) == 0
```

Add missing-role cases for both recording adapters and assert exit code 2 plus diagnostics beginning with `record-progress:` or `record-completion:` rather than `python_machine_interface_runner.py`.

- [ ] **Step 2: Run the focused tests and verify the new imports/classes fail**

Run:

```bash
pytest -q skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py -k 'adapter or role or program_name'
```

Expected: failure because the four adapter entries and fixed-operation writer API do not exist.

- [ ] **Step 3: Implement fixed writer operations and thin adapters**

Replace the single adapter with:

```python
class _WriterInterface(PythonArgvMachineInterface):
    operation: str

    def run(self, argv: list[str]) -> int:
        return _writer_main(self.operation, argv, prog=self.prog)

class RecordProgress(_WriterInterface):
    operation = "record-progress"
    prog = "record-progress"

class RecordCompletion(_WriterInterface):
    operation = "record-completion"
    prog = "record-completion"

class SessionPath(_WriterInterface):
    operation = "session-path"
    prog = "session-path"

class RunPath(_WriterInterface):
    operation = "run-path"
    prog = "run-path"
```

In `_milestone_writer.py`, build one parser per fixed operation. Progress accepts required `DOING`, optional `PREV`, required `--role`, optional `--run`, and optional typed metadata. Completion accepts required `RESULT`, required `--role`, optional `--run`, and the same typed metadata. Session path accepts no arguments; run path accepts one required run ID. Remove public handling for `--done` and `--path`.

- [ ] **Step 4: Add failing tests for typed metadata without run and additive run mirroring**

Write a real-filesystem test that records all typed fields without `run`, reads the session JSONL, and compares literal field values. Write a second test recording the same values with `run`; assert all pre-existing session fields retain the same literal values, assert only `run`, `session`, and `agent` are added, and assert the run-journal bytes equal the session-log bytes.

- [ ] **Step 5: Run those tests and verify the old dependency fails**

Run:

```bash
pytest -q skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py -k 'typed_without_run or additive_run_mirror'
```

Expected: the no-run case exits 2 with `--run is required`, proving the regression test catches the original defect.

- [ ] **Step 6: Make typed metadata unconditional and run mirroring additive**

Construct the record's typed fields before target selection and always merge them into the session record. When `run` is present, add `run`, `session`, and `agent`, serialize once, and pass the same `bytes` object to `_append_line` for the session and run targets. Remove the typed-without-run rejection.

- [ ] **Step 7: Add failing independent-budget tests**

Parameterize over role, doing/result, prev, cwd, event, task, state, step, attempt, session, agent, and evidence. Use JSON-escaped control characters and four-byte Unicode. Assert a value at its serialized quota succeeds, a value one encoded byte over fails because of that field alone, and varying every other field within its quota does not change evidence acceptance.

- [ ] **Step 8: Implement serialized-JSON quotas**

Add a helper that measures `len(json.dumps(value, ensure_ascii=False).encode("utf-8"))`. Enforce these value quotas independently: role/doing/result/prev 220, cwd 512, session/agent 128, run 66, event 80, task 128, state 64, step/attempt 24, timestamp 48, complete evidence array 1,400, at most 20 evidence entries, and at most 220 serialized bytes per evidence entry. Reject violations; do not truncate. Define and test a fixed structural-overhead constant proving every accepted record is at most `LINE_BUDGET`.

- [ ] **Step 9: Run all writer tests**

Run:

```bash
pytest -q skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py -k 'writer or record or path or run or evidence or budget or role'
```

Expected: all selected tests pass with no warnings.

---

### Task 2: Timeline operations and truthful rendering

**Files:**
- Modify: `skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py`
- Modify: `skills/milestone-logging/_rtx/_agent_timeline.py`
- Modify: `skills/milestone-logging/_rtx/_timeline_interface.py`

**Interfaces:**
- Consumes: Task 1's typed session records and safe run journals.
- Produces: `ListSessions`, `ShowLatestSession`, `ShowSession`, `ShowRun`, and `ReadRunJson`; fixed-operation timeline callable `main(operation: str, argv: list[str] | None = None, *, prog: str) -> int`.

- [ ] **Step 1: Add failing fixed-operation tests**

Add tests invoking the five adapters with their exact arities. Assert old flags `--list`, `--run`, and `--json` return exit code 2 on every replacement interface. Assert each error names the exact adapter program.

- [ ] **Step 2: Verify the tests fail against the single precedence parser**

Run:

```bash
pytest -q skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py -k 'timeline_adapter or old_timeline_flag or timeline_program_name'
```

Expected: failure because fixed timeline adapters do not exist.

- [ ] **Step 3: Implement five adapters and fixed timeline operations**

Use a shared `_TimelineInterface` matching Task 1's adapter structure. Give every class a fixed operation and `prog`. Replace the precedence branch with independently callable list, latest-session, selected-session, run-text, and run-JSON functions. `show-latest-session` takes only optional `--slow`; `show-session` requires `SESSION` and accepts optional `--slow`; run interfaces require one `RUN` positional.

- [ ] **Step 4: Add failing slow-annotation tests**

Create a session containing two records with a known 12-second gap. Assert no `[slow]` text without the option, assert `--slow 10` adds exactly one annotation without changing event text/order, and assert zero, negative, NaN, positive infinity, and negative infinity each exit 2.

- [ ] **Step 5: Implement independent slow validation and annotations**

Represent absent slow as `None`; render annotations only when it is not `None`. Validate with `math.isfinite(value) and value > 0`. Do not filter or reorder events based on the threshold.

- [ ] **Step 6: Add failing typed-rendering and count-label tests**

Assert session timeline output includes literal supplied event, step, task, state, attempt, and evidence values. Create two log files for one session and assert listing reports `(2 log files)` and contains no `agent` label.

- [ ] **Step 7: Preserve metadata and label files truthfully**

Carry typed keys through `read_milestones` and render them in a stable second line beneath the milestone. Keep `list_sessions()` returning `len(paths)`, but render that value as `log file`/`log files`.

- [ ] **Step 8: Run all timeline tests**

Run:

```bash
pytest -q skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py -k 'timeline or session or slow or list or render or run_json'
```

Expected: all selected tests pass.

---

### Task 3: Blueprint contracts, exports, and generated guidance

**Files:**
- Modify: `skills/milestone-logging/_rtx/blueprints/rtx-milestone-writer.yaml`
- Modify: `skills/milestone-logging/_rtx/blueprints/rtx-agent-timeline.yaml`
- Modify: `skills/milestone-logging/_rtx/blueprint.yaml`
- Modify: `skills/milestone-logging/blueprint.yaml`
- Modify: `skills/milestone-logging/blueprints/gateway.yaml`
- Regenerate: `skills/milestone-logging/SKILL.md`
- Regenerate: `references/blueprint-schema/runtime_dependencies.json`
- Modify: `skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py`
- Modify if required by a failing behavior assertion: `skills/skill-maker/_rtx/tests/test_blueprint_tools.py`

**Interfaces:**
- Consumes: the nine adapter entry class names and argument behavior from Tasks 1 and 2.
- Produces: nine source contracts, nine `_rtx` exports, nine gateway uses, and generated first-attempt invocation guidance.

- [ ] **Step 1: Add failing blueprint graph assertions**

Load the repository graph and assert the exact nine export IDs and source-interface mappings. Assert recording patterns require `--role`, progress arity is 1..2, completion arity is exactly 1, path/list arities are exact, `show-session` requires one positional, and no allowed-flags set contains `--done`, `--path`, `--list`, or `--json`.

- [ ] **Step 2: Verify the graph assertions fail against the two old exports**

Run:

```bash
pytest -q skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py -k 'blueprint or contract or binding'
```

Expected: assertion failure showing only `record` and `timeline` exist.

- [ ] **Step 3: Replace writer and timeline source contracts**

Use YAML anchors for shared argument, direct-I/O, output, and effect fragments. Declare mutating contracts only for record interfaces and read-only contracts for path/timeline interfaces. Each process binding has one pattern and the exact adapter entry. Describe typed metadata as session-retained and `run` as an additional mirror. Declare independent field limits and per-value validation.

- [ ] **Step 4: Replace module exports and gateway uses**

Map the exact nine source-interface IDs through `_rtx/blueprint.yaml`, the parent module's namespace/access map, and both gateway `uses_interfaces` lists. Keep the public default interface. Change the instruction gateway's conservative `state_effect` to `mutating` and describe both read and append outcomes.

- [ ] **Step 5: Regenerate projections using the registered sync interface**

Invoke `skill-maker._rtx.interface.sync-blueprints@1` through
`famulus_dispatcher.invoke` with caller `skill-maker`, empty positionals and
options, and null stdin. Do not hand-edit the generated block or runtime
inventory. Confirm the generated `SKILL.md` lists all nine IDs with correct
required fields. Update only hand-authored policy below the generated block to
select explicit interfaces and state the additive run rule.

- [ ] **Step 6: Run blueprint and generation tests**

Run:

```bash
pytest -q skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py -k 'blueprint or contract or binding'
pytest -q skills/skill-maker/_rtx/tests/test_blueprint_tools.py
```

Expected: all selected tests pass; generated-file checks report no drift.

---

### Task 4: Repository callers and original-symptom integration coverage

**Files:**
- Modify: `tests/test_famulus_mcp.py`
- Modify: `tests/test_setup_interface_manager_integration.py`
- Modify: `tests/test_officina_blueprint_authorization.py`
- Modify: `tests/fixtures/famulus_comprehension_payloads.json`
- Modify: `skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py`

**Interfaces:**
- Consumes: all nine final exports and generated caller guidance.
- Produces: repository-wide proof that callers use only explicit interfaces and that the reported `--task` failure cannot recur.

- [ ] **Step 1: Update tests first to call the explicit exports**

Replace old `record` targets according to intent: recording uses `record-progress` or `record-completion`; path lookup uses `session-path`; timeline listing uses `list-sessions`; selected/latest session and run reconstruction use their exact interfaces. Add `--role` to every recording call.

- [ ] **Step 2: Add the original-symptom regression at the MCP boundary**

Invoke `record-progress` through the real dispatcher envelope with positional `doing`, required role, and `--task` but no `--run`. Assert dispatcher acceptance, exit code 0, empty stderr, and a session JSONL record containing the literal task. Add dry-run cases for every new interface and assert the two removed IDs are unknown.

- [ ] **Step 3: Run the migrated integration tests and inspect every failure**

Run:

```bash
pytest -q tests/test_famulus_mcp.py tests/test_setup_interface_manager_integration.py tests/test_officina_blueprint_authorization.py -k 'milestone or dispatcher_argument_envelope or authorization'
```

Expected: the new original-symptom assertion passes against Tasks 1–3. Any
failure must identify a stale caller, envelope mismatch, or export-projection
gap and is corrected without changing the nine-interface contract.

- [ ] **Step 4: Update remaining fixtures and exact assertions**

Change comprehension cases so record-and-return-path expects two calls, and separate list/session/run intentions select one interface each. Replace the private source authorization sample with `...interface.record-progress`. Preserve all setup-manager semantics besides the target name and required role.

- [ ] **Step 5: Run affected integration tests**

Run:

```bash
pytest -q tests/test_famulus_mcp.py tests/test_setup_interface_manager_integration.py tests/test_officina_blueprint_authorization.py
```

Expected: all tests in the three affected files pass.

---

### Task 5: Full verification and live-source boundary report

**Files:**
- Verify only: all files changed by Tasks 1–4
- Do not modify: `mcp_server.py`, `src/officina/dispatcher/direct_runtime.py`, `mcp.json`, or installed plugin-cache files unless a separately approved identity-carrier task begins.

**Interfaces:**
- Consumes: the complete nine-interface implementation.
- Produces: fresh local verification evidence and an exact remaining identity blocker.

- [ ] **Step 1: Run the complete milestone-local suite**

Run:

```bash
pytest -q skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py
```

Expected: all tests pass.

- [ ] **Step 2: Run affected repository tests**

Run:

```bash
pytest -q tests/test_famulus_mcp.py tests/test_setup_interface_manager_integration.py tests/test_officina_blueprint_authorization.py skills/skill-maker/_rtx/tests/test_blueprint_tools.py
```

Expected: all tests pass.

- [ ] **Step 3: Run repository validation with eight workers**

Run:

```bash
python3 scripts/repo_checks.py --suite precommit --jobs 8
```

Expected: exit 0 with no weakened or skipped milestone tests.

- [ ] **Step 4: Verify exact scope and generated consistency**

Run `git status --short`, `git diff --check`, and `rg` for both obsolete export IDs outside historical documents. Confirm only the design, plan, milestone implementation, generated projections, and named tests changed.

- [ ] **Step 5: Report the external identity boundary without guessing**

Record that the interface defect is covered by the original-symptom test. Separately report that live non-`unknown` session identity still requires verified host-supplied per-call metadata; do not claim that issue fixed and do not synthesize an identity.
