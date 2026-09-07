# Dispatcher and Setup Error Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task by task. Track each
> checkbox and stop at every review gate.

**Goal:** Implement the dispatcher/setup error contract without expanding it
to warnings, other subsystems, or general exception redesign.

**Architecture:** Keep the current exception hierarchy and schema versions.
Add one closed error-spec registry per owner, preserve the existing setup
response envelope, and translate diagnoses exactly once at public boundaries.

**Tech stack:** Python 3.11+, dataclasses/standard library, pytest, and
`repo_checks.py`.

**Spec:**
[design](./2026-09-06-dispatcher-setup-error-design.md) and normative
[message catalogue](./2026-09-06-dispatcher-setup-error-message-catalogue.md).

## Global constraints and budget

- Work in an isolated named worktree; preserve unrelated dirty changes.
- The catalogue is the source of exact codes, messages, predicates, context,
  clues, recovery, and dispositions. Raise sites must not invent wording.
- Keep schema version 1 and the current setup flow-shaped failure envelope.
- Add no production dependency and no new error-category hierarchy.
- Touch at most the 14 production files named below, the two named skill files,
  and the named tests. Add no production module unless a review first records
  why the registry cannot remain in its owner file.
- Budgets below are ceilings, not targets. Generated/fixture data and pure
  catalogue rows do not count as logic lines. If a task exceeds a file, logic,
  abstraction, or test-case ceiling, stop and revise this plan before coding
  further.
- A test-case ceiling counts test functions or parametrized tables, not the
  rows inside a catalogue-wide table; each semantic row must still be asserted.
- A newly discovered public emission may add at most eight catalogue rows in
  Task 1. More means the audited scope was materially incomplete: stop.
- Do not commit, stage, regenerate blueprints, or certify nodes without
  separate authorization.

## File map

| Responsibility | Files |
| --- | --- |
| Dispatcher specs/payload/rendering | `src/officina/dispatcher/errors.py`, `src/officina/dispatcher/cli.py` |
| Dispatcher producers | `src/officina/dispatcher/core.py`, `src/officina/dispatcher/direct_blueprints.py`, `src/officina/dispatcher/direct_authorization.py` |
| Process and private runner boundary | `src/officina/dispatcher/direct_runtime.py`, `src/officina/runtime/python_machine_interface.py`, `src/officina/runtime/python_machine_interface_runner.py` |
| Setup owner | `skills/setup-interface-manager/_rtx/_setup_manager.py`, `_setup_state.py`, `_setup_evaluation.py`, `_setup_dispatches.py` |
| MCP manager adapter | `mcp_server.py` |
| Public skill routing | `skills/bootstrap-dispatcher-runtime/SKILL.md`, `skills/setup-interface-manager/SKILL.md` |

---

### Task 1: Reconcile the live inventory

**Files:** Modify only the message catalogue if live evidence differs.

**Budget:** 0 production files; 1 documentation file; at most 8 added rows; no
wording changes without a cited producer predicate.

- [x] Definition-first: enumerate every error class and follow its raises,
  catches, serializers, and tests:

```bash
rg -n 'class .*Error|raise |except |as_payload|error_code|recovery' src/officina/dispatcher src/officina/runtime mcp_server.py skills/setup-interface-manager/_rtx tests
```

- [x] Behavior-first: independently enumerate public codes, exits, states,
  stderr rendering, and MCP envelopes:

```bash
rg -n 'dispatcher\.|setup\.|return (2|64|70)|state=|"state"|"code"|stderr|Possible clues' src/officina/dispatcher src/officina/runtime mcp_server.py skills/setup-interface-manager/_rtx tests
```
- [x] Reconcile both lists against every D/R/S/E catalogue row. Record producer,
  carrier, consumer, predicate, and disposition for each discrepancy.
- [x] Run the existing focused baseline:

```bash
python3 repo_checks.py --task tests:shared --selector tests/test_dispatcher_errors.py --selector tests/test_dispatcher_cli.py --selector tests/test_mcp_setup_preflight.py --selector tests/test_officina_python_machine_interface.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_manager.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_state.py --jobs 8
```

**Gate:** Review the reconciled table before production edits. Stop if more than
eight rows or any third subsystem enters scope.

---

### Task 2: Add the dispatcher registry and semantic payload

**Files:** Modify `errors.py`, `cli.py`; test `test_dispatcher_errors.py`,
`test_dispatcher_cli.py`.

**Interfaces:** `DispatcherError.as_payload() -> dict[str, object]` keeps its
name. Add immutable `ErrorSpec`, closed `DISPATCHER_ERROR_SPECS` keyed by D/R
catalogue ID, and registry-backed construction on `DispatcherError`. A reduced
cause is one payload hop; clues are an ordered tuple of registered text.

**Budget:** 2 production files; 2 test files; at most 2 semantic types plus 1
catalogue-data helper; at most 260 net logic lines and 24 focused test cases;
no subclass removal.

- [x] Add failing parametrized tests proving every registered D/R entry has the
  catalogue code/template/context policy, duplicate IDs are impossible, and
  unknown IDs or context fields fail closed.
- [x] Add failing payload tests for one-hop cause reduction, ordered clues,
  secret redaction, schema 1, and absence of empty optional fields.
- [x] Add failing CLI tests asserting identical semantic fields in JSON and
  text, with text ordered as message, `Cause:`, then `Possible clues:`.
- [x] Run the two selectors with `--jobs 1`; confirm the new tests fail for the
  missing registry/fields.
- [x] Implement only the registry lookup, safe template rendering, payload
  reduction, and text rendering required by those tests. Preserve
  `InvocationError` catch compatibility and distinct subclasses.
- [x] Re-run both selectors with `--jobs 1`; require PASS.

**Gate:** Reject any generic `action`, nested cause, recovery field, traceback,
or arbitrary context dictionary.

---

### Task 3: Migrate dispatcher request and resolution producers

**Files:** Modify `core.py`, `direct_blueprints.py`,
`direct_authorization.py`, `src/officina/blueprints/direct_setup.py`, and the
D49-D55 startup/request portion of `mcp_server.py`; test
`test_dispatcher_errors.py`, `test_dispatcher_direct_blueprints.py`,
`test_dispatcher_direct_authorization.py`, and
`test_dispatcher_direct_setup.py`, plus `test_dispatcher_route_smoke.py` and
the D49-D55 cases in `test_mcp_setup_preflight.py`.

**Interfaces:** Producers select a D-row ID and supply only that row's named
safe context. `DirectBlueprintError` no longer accepts arbitrary public codes
or messages.

**Budget:** 5 production files; 6 test files; 0 new types/modules; at most 180
net logic lines and 34 parametrized cases; migrate only catalogue rows whose
disposition is Replace or whose construction is currently arbitrary.

- [x] Add failing table-driven tests mapping each affected producer predicate
  to its exact D01-D55 row and asserting forbidden implications are absent.
- [x] Add exact D38 cases for empty ancestry and wrong target ownership.
- [x] Run the affected selectors with `--jobs 1`; require the new assertions to
  fail on current arbitrary/overloaded production.
- [x] Replace each affected construction with its registry row. Retain already
  accurate structured errors and their catch behavior.
- [x] Re-run the affected selectors with `--jobs 1`; require PASS.

**Gate:** No broad rewrite of blueprint resolution, authorization, warnings,
or exception inheritance.

---

### Task 4: Implement the process and private runner boundary

**Files:** Modify `direct_runtime.py`, `python_machine_interface.py`,
`python_machine_interface_runner.py`; test
`test_officina_python_machine_interface.py` and
`test_dispatcher_direct_setup.py`.

**Interfaces:** `_run_resolved_invocation(...)` keeps its public behavior. The
dispatcher alone supplies neutral `--diagnostic-writer TOKEN`; its platform
adapter treats the token as a POSIX fd or Windows handle. The runner emits at
most one compact registered R01-R25 payload and reserves exit 70 for that
channel.

**Budget:** 3 production files; 2 test files; at most 4 private helpers; at
most 380 net logic lines and 28 focused cases; one pipe only; 16 KiB retained
diagnostic limit; no environment-variable or stdout/stderr transport.

- [x] Add failing tests for accepted diagnosis, malformed/multiple/trailing/
  oversized diagnosis, descriptor non-leakage, and ordinary target exit 70.
- [x] Add failing subprocess tests proving timeout D69 wins; cleanup uses
  terminate, bounded grace, kill, final wait; reader work never blocks return.
- [x] Add failing tests proving non-timeout precedence is registered diagnosis,
  D71 decode failure, D70 checked nonzero, then ordinary result.
- [x] Add six platform tests: native Windows round-trip, one-handle allowlist,
  success restoration/closure, partial/Popen-failure cleanup, grandchild
  noninheritance, and concurrent diagnostic isolation. POSIX uses `pass_fds`;
  Windows uses `close_fds=true` and `STARTUPINFO.lpAttributeList.handle_list`.
- [x] Run both selectors with `--jobs 1`; confirm failures.
- [x] Replace the relevant `subprocess.run` path with the minimal `Popen`
  implementation needed for concurrent ordinary-output and nonblocking private
  diagnosis collection. Add runner parsing/emission without changing interface
  argv.
- [x] Re-run both selectors with `--jobs 1`; require PASS.

**Gate:** Reject threads with an unbounded join, raw child output in an error,
automatic retry, or reclassification of an ordinary nonzero target result.

---

### Task 5: Make setup diagnoses exact and recovery authorized

**Files:** Modify `_setup_manager.py`, `_setup_state.py`,
`_setup_evaluation.py`, `_setup_dispatches.py`; test `test_setup_manager.py`,
`test_setup_state.py`, `test_setup_evaluation.py`, and
`tests/test_setup_interface_manager_integration.py`.

**Interfaces:** Keep `_response(...)` and schema 1. Add one closed setup spec
registry keyed by E-row. `state=failed` carries nonrecoverable diagnoses;
`recovery-required` carries recovery only after a fresh canonical ledger read
reconstructs the same flow, step, and verified continuation owner.

**Budget:** 4 production files; 4 test files; at most 2 new private helpers and
1 ledger field; at most 395 net logic lines and 32 focused cases; no new flow
state, recovery action, or setup protocol.

- [x] Add failing table-driven tests for S01-S08 and E00-E54 exact predicates,
  messages, causes, clues, exit pairing, and forbidden implications.
- [x] Add failing negative tests for spoofed owner, legacy/unowned flow,
  changed flow/step after reread, passive busy, and nonrecoverable failures.
- [x] Add failing permission tests that inspect only the exception itself and
  at most two explicit `__cause__` links for `PermissionError`.
- [x] Run the three setup selectors with `--jobs 1`; confirm failures.
- [x] Implement registry-backed diagnoses, status/flow classification, precise
  settlement/verification wording, and fresh-flow recovery authorization.
- [x] Re-run the three selectors with `--jobs 1`; require PASS.

**Gate:** No clue without its catalogue predicate; no recovery inferred from an
exception class alone; no `setup_required` emitted for a failed evaluation.

---

### Task 6: Replace the MCP manager adapter ambiguity

**Files:** Modify `mcp_server.py`; test `test_mcp_setup_preflight.py` and
`test_famulus_mcp.py`.

**Interfaces:** Keep `_manager_call(caller, operation, arguments)`. It parses
JSON before applying the catalogue's ordered D56-D68 classification and returns
only validated status/flow objects. `_ordinary_preflight(...)` preserves valid
statuses and translates a flow-shaped diagnosis exactly once.

**Budget:** 1 production file; 2 test files; at most 2 private validators; at
most 290 net logic lines and 22 focused cases; no MCP envelope version change.

- [x] Add failing matrix tests for launch/typed failure, missing payload,
  malformed/non-object JSON, variant-invalid object, invalid exit pairing,
  invalid pending stack, ready authorization, required/busy shape, and valid
  setup diagnosis preservation.
- [x] Assert D56 never implies bootstrap/setup, D57 never echoes output, D58
  owns every syntactically valid variant-invalid object, and D64 preserves only
  allowlisted reduced semantics.
- [x] Run both selectors with `--jobs 1`; confirm failures.
- [x] Implement one adapter validator and migrate `_manager_call`,
  `_safe_pending_stack`, and `_ordinary_preflight` to it.
- [x] Re-run both selectors with `--jobs 1`; require PASS.

**Gate:** No raw manager stdout/stderr, no status/flow coercion, and no second
manager-response parser.

---

### Task 7: Tighten skill routing descriptions

**Files:** Modify the two named `SKILL.md` files; test
`test_bootstrap_dispatcher_runtime_skill.py` and add description assertions to
`test_setup_interface_manager_coverage.py`.

**Budget:** 2 skill files; 2 test files; at most 40 net prose lines and 8
assertions; no workflow, executable interface, blueprint, or generated-artifact
change.

- [x] Add failing assertions that bootstrap names only evidence-backed missing/
  old Python and missing declared-package codes, and excludes routing,
  authorization, setup state, and manager-response failures.
- [x] Add failing assertions that setup manager describes exact requirements,
  tentative clues, passive busy, and owned live-flow recovery without guessing
  or automatic retry.
- [x] Run both selectors with `--jobs 1`; confirm failures.
- [x] Change only the descriptions/instructions needed for those assertions.
- [x] Re-run both selectors with `--jobs 1`; require PASS.

**Gate:** If blueprint-owned/generated content must change, stop and create a
separate authorized regeneration task.

#### Task 7A: Regenerate derived documentation

The canonical documentation validator required the two derived indexes to
reflect the changed skill descriptions. This is a separate mechanical task:
`python3 scripts/generate-doc-artifacts.py` updated only
`docs/skills.md` and `docs/domains/assistant-operations.md`. No blueprint or
certificate changed.

---

### Task 8: Compatibility and final accuracy audit

**Files:** Tests and catalogue only. Production fixes must remain within the
owner and budget of the task that introduced the defect.

**Budget:** 0 new production abstractions; at most 13 cross-boundary cases and
8 catalogue corrections; no warning audit or unrelated error expansion.

- [x] Add a cross-boundary contract matrix covering CLI text/JSON, MCP,
  manager subprocess, runner diagnosis, redaction, schema 1, and exact
  recovery authority.
- [x] Audit every migrated producer against its catalogue predicate and every
  consumer against its rendered fields. Correct facts; do not polish wording
  outside a demonstrated mismatch.
- [x] Run focused verification:

```bash
python3 repo_checks.py --task tests:shared --selector tests/test_dispatcher_errors.py --selector tests/test_dispatcher_cli.py --selector tests/test_dispatcher_direct_blueprints.py --selector tests/test_dispatcher_direct_authorization.py --selector tests/test_dispatcher_direct_setup.py --selector tests/test_officina_python_machine_interface.py --selector tests/test_mcp_setup_preflight.py --selector tests/test_famulus_mcp.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_manager.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_state.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py --selector tests/test_bootstrap_dispatcher_runtime_skill.py --selector tests/test_setup_interface_manager_coverage.py --jobs 8
python3 repo_checks.py --task tests:shared --selector tests/test_dispatcher_route_smoke.py --selector tests/test_setup_interface_manager_integration.py --jobs 8
```

- [x] Run repository gates:

```bash
python3 repo_checks.py --suite validators --jobs 8
python3 repo_checks.py --suite precommit --repository-view working --jobs 8
git diff --check
```

- [x] Inspect `git status --short` and the diff by named path. Confirm no
  unrelated file, warning behavior, protocol version, or generated artifact was
  absorbed.

**Final gate:** Every catalogue row is owned by one predicate and exact contract
test; all commands pass; all task budgets hold. Any exception requires a plan
revision and renewed review before claiming completion.

## Execution reconciliation

- Task 2 measured +163 executable lines after excluding catalogue rows. Its
  third helper is catalogue data, not another semantic abstraction.
- Task 3 required the existing direct-setup producer and its tests to close the
  D38 ownership predicate; the 180-line ceiling still holds.
- Task 4 measured under 380 executable lines and retains four private helpers.
- Task 5 has 448 net nonblank/non-comment lines; excluding the 56 pure registry
  rows leaves 392 executable lines under its revised 395-line ceiling.
- Task 6 remains under its 290-line ceiling after excluding D49-D55, which are
  attributed to Task 3. Its adapter result is consumed directly, not re-parsed.
- The cross-boundary matrix is complete. Host-capable focused verification
  passed 550 tests with 2 skips; the full working-tree pre-commit gate passed
  3,532 tests with 22 skips. Validators passed 29 tests.
