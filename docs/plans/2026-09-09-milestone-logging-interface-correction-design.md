# Milestone Logging Interface Correction Design

**Status:** Approved audited design. Implementation is in progress.

## Goal

Replace milestone logging's two mode-heavy machine interfaces with explicit
interfaces whose arguments have independent, additive effects. Eliminate the
`--run is required with --task` failure, prevent silent argument suppression,
make diagnostics name the invoked interface, and state the separate host
identity requirement needed to stop writing `unknown.session.jsonl`.

## Confirmed Problems

The failure was not caused by an invalid task value. The `record` blueprint
advertises `--task` and `--run` as independently optional, while the Python
runtime rejects every typed field unless `--run` is present. The generated
interface block omits the blueprint note containing that dependency. A caller
can therefore construct an invocation accepted by the dispatcher and rejected
only by the nested parser.

The same two interfaces contain further non-additive behavior:

- `--done` replaces both positional values.
- `--path` suppresses recording and returns early.
- `--list` and `--run` select different timeline operations by precedence.
- `--json` is silently ignored unless `--run` wins the precedence branch.
- `role` is optional even though the skill promises role-labelled records.
- record fields can evict evidence when the combined JSON line exceeds the
  shared line budget.
- the timeline calls a count of log files an agent count.
- nested `argparse` errors use `python_machine_interface_runner.py` rather than
  the invoked milestone interface name.
- the long-lived MCP process does not receive the Codex or Claude conversation
  identity, so the writer falls back to `unknown.session.jsonl`.

## Governing Invariant

For every exported interface:

1. The interface performs one operation.
2. Supplying an optional argument never makes another argument legal or
   illegal.
3. Supplying an optional argument never suppresses, replaces, or reinterprets
   another argument or its effect.
4. Each optional argument adds exactly its own field, annotation, or additional
   write.
5. Target selection, completion versus progress, path lookup, listing, and
   output format are separate interfaces when combining them would create a
   mode.
6. Runtime validation is per value. No validation depends on a combination of
   otherwise valid arguments.

## Exact Replacement Interface Surface

Delete these private exports and their source-interface declarations:

- `milestone-logging._rtx.interface.record`
- `milestone-logging._rtx.interface.timeline`

Keep `milestone-logging.interface.default@1` as the public instruction
interface. Add these nine private executable exports, each at version 1.

### `milestone-logging._rtx.interface.record-progress`

- Required positional: `DOING`, a non-empty progress description.
- Required option: `--role ROLE`, a non-empty role label.
- Optional positional: `PREV`, which adds only the preceding-result field.
- Optional options: `--run ID`, `--event EVENT`, `--step STEP`, `--task TASK`,
  `--state STATE`, `--attempt ATTEMPT`, and repeatable `--evidence PATH`.
- Always append one complete session record.
- Always retain supplied typed metadata in that session record.
- `--run` adds the `run`, `session`, and `agent` identity fields and one second
  append of the identical record to the validated run journal. It does not
  activate or alter typed metadata.
- Output is empty on success and an interface-named diagnostic on failure.

### `milestone-logging._rtx.interface.record-completion`

- Required positional: `RESULT`, a non-empty completion result.
- Required option: `--role ROLE`.
- Optional options: the same `--run`, typed metadata, and repeatable evidence
  options as `record-progress`.
- Append a session record with fixed `doing="(done)"` and `prev=RESULT`.
- Typed metadata and optional run mirroring have exactly the same semantics as
  `record-progress`.
- Completion is selected by the interface, not by an argument that replaces
  progress arguments.

### `milestone-logging._rtx.interface.session-path`

- Arguments: none.
- Return the absolute current session-log path.
- Perform no append.

### `milestone-logging._rtx.interface.run-path`

- Required positional: `RUN`.
- Validate `RUN` with the existing safe run-ID rule.
- Return the absolute run-journal path and perform no append.

### `milestone-logging._rtx.interface.list-sessions`

- Arguments: none.
- Return session groups ordered by newest recorded modification time.
- Report `len(paths)` as `log file`/`log files`. Do not call it an agent count:
  Claude can place several roles in one shared session file, while one agent can
  also contribute files on more than one date.

### `milestone-logging._rtx.interface.show-latest-session`

- Optional option: `--slow SECONDS`.
- Select the latest session independently of all caller input.
- Without `--slow`, render no slow-gap annotations.
- With `--slow`, add annotations for gaps meeting the finite positive threshold;
  it changes no event selection or event content.

### `milestone-logging._rtx.interface.show-session`

- Required positional: `SESSION`.
- Optional option: `--slow SECONDS`, with the same annotation-only behavior as
  `show-latest-session`.
- Render only the selected session.

### `milestone-logging._rtx.interface.show-run`

- Required positional: `RUN`.
- Return the human-readable reconstruction for that run.

### `milestone-logging._rtx.interface.read-run-json`

- Required positional: `RUN`.
- Return the stable JSON reconstruction for that run.
- JSON is an interface output contract, not a flag that changes another
  interface's output.

The separate latest-session interface is deliberate. An eight-interface design
could make `SESSION` optional on `show-session`, but the ninth interface removes
even that default-target mode and most strictly implements the governing
invariant.

## Record Representation and Independent Bounds

The writer must serialize one record once, write those exact bytes to the
session log, and, when `run` is supplied, write the same bytes to the run
journal. A partial second append remains a documented possible failure; callers
must inspect before retrying because appends are non-idempotent.

Replace character slicing and the combined evidence-dropping loop with
independent validation of each field's serialized JSON value. These are
deliberate new byte bounds, not claims that a byte limit preserves the current
Unicode character capacity:

- role, doing, result, and prev: 220 serialized bytes each
- cwd: 512 serialized bytes
- session and agent: 128 serialized bytes each
- run: 66 serialized bytes, including JSON quotes around the validated 64-byte
  ASCII identifier
- event: 80 serialized bytes
- task: 128 serialized bytes
- state: 64 serialized bytes
- step and attempt: 24 serialized bytes each
- timestamp: 48 serialized bytes
- the complete evidence-array JSON value: 1,400 serialized bytes, with at most
  20 entries and at most 220 serialized bytes per entry

Reject an over-budget scalar or evidence value with an interface-named
diagnostic. Do not truncate or drop supplied data. Because evidence validity is
computed solely from the evidence value, no other argument can change whether
evidence is accepted. Reserve the exact encoded keys, separators, braces, and
newline as a fixed structural constant. A maximum-record test must demonstrate
that the sum of that constant and every value quota is at most `LINE_BUDGET`.
Remove `evidence_dropped` and the loop that shrinks evidence based on actual
combined field sizes.

Integer validation remains independent: `step` and `attempt` must be
non-negative. `slow` must be finite and greater than zero. A run ID is valid or
invalid by itself.

## Exact File Changes

### Source blueprints and exports

- Modify
  `skills/milestone-logging/_rtx/blueprints/rtx-milestone-writer.yaml`:
  replace the single `record` contract with the two recording and two path
  contracts; anchor shared arguments, I/O, outcomes, and append semantics;
  require role; disclose independent byte/evidence bounds; give each interface
  one process-binding pattern and its exact entry class.
- Modify
  `skills/milestone-logging/_rtx/blueprints/rtx-agent-timeline.yaml`:
  replace `timeline` with the five reader contracts; remove all operation flags;
  give every interface a single output shape and exact entry class.
- Modify `skills/milestone-logging/_rtx/blueprint.yaml`: replace two exports with
  nine source-interface mappings.
- Modify `skills/milestone-logging/blueprint.yaml`: replace the two private
  namespace and access entries with the nine new exports. Keep the public
  default interface and module ownership unchanged.
- Modify `skills/milestone-logging/blueprints/gateway.yaml`: route all nine
  interfaces and conservatively declare the instruction gateway mutating,
  because its workflow may append records.

### Python adapters and shared runtime

- Modify `skills/milestone-logging/_rtx/_milestone_interface.py`: replace the
  single raw-argv `Interface` with `RecordProgress`, `RecordCompletion`,
  `SessionPath`, and `RunPath`. Each supplies a fixed operation and accurate
  `prog` to shared writer functions.
- Modify `skills/milestone-logging/_rtx/_timeline_interface.py`: replace its
  single class with `ListSessions`, `ShowLatestSession`, `ShowSession`,
  `ShowRun`, and `ReadRunJson`; each selects one fixed reader operation.
- Modify `skills/milestone-logging/_rtx/_milestone_writer.py`: keep path
  resolution, run-ID validation, and append primitives; replace the mode parser
  with operation-specific parsers/functions; remove the typed-without-run
  rejection, `--path` early return, and `--done` replacement branch; store typed
  metadata in every session record; make `run` only add a mirror; implement the
  independent serialized-JSON bounds; and pass the interface name to
  `argparse`.
- Modify `skills/milestone-logging/_rtx/_agent_timeline.py`: replace precedence
  dispatch with independently callable list/latest/session/run-text/run-JSON
  functions; retain and render typed fields from session records; validate
  `slow`; label the existing path count as log files rather than agents; and
  accept the adapter's exact program name for errors.
- Create no new runtime files and delete no files. Shared behavior remains in
  the two existing runtime modules.

### Generated instruction and inventories

- Regenerate `skills/milestone-logging/SKILL.md`. Its hand-authored policy must
  select `record-progress`, `record-completion`, `session-path`, `run-path`, or
  the exact reader interface; it must state that typed metadata is retained in
  the session record and `run` adds a mirror.
- Regenerate `references/blueprint-schema/runtime_dependencies.json` so it
  contains the nine new exports and neither old export.
- Do not modify `skills/skill-maker/_rtx/_blueprint_syncer.py`; it already emits
  one generated entry per exported interface. Update only its tests if an
  assertion names the old combined surface.

### Direct callers and tests

- Modify
  `skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py` to target
  the nine contracts and adapters. Add parameterized tests proving each
  recording option independently adds only its field, each pair retains both
  fields, metadata works without `run`, `run` adds exactly one byte-identical
  mirror, role is required, evidence retention is independent of other fields,
  old mode flags are rejected, errors name the selected interface, slow values
  reject zero/negative/NaN/infinity, typed fields render in session timelines,
  and session counts are accurate.
- Modify `tests/test_famulus_mcp.py`: replace all old interface targets, expand
  dry-run coverage to every new binding, assert generated required arguments,
  and verify old mode flags cannot compile against any replacement interface.
- Modify `tests/test_setup_interface_manager_integration.py`: use
  `record-progress` as the unmanaged milestone target and include its required
  role.
- Modify `tests/test_officina_blueprint_authorization.py`: replace the old
  private source-interface sample with `record-progress`.
- Modify `tests/fixtures/famulus_comprehension_payloads.json`: distinguish
  record, path, list, session, and run requests. A request to record and return
  a path must expect two explicit calls rather than one mode-combined call.
- Modify `skills/skill-maker/_rtx/tests/test_blueprint_tools.py` only where its
  generated-interface assertions assume the old two-interface surface.
- Keep historical plans and evidence unchanged.
- Never edit the installed plugin cache directly; refresh it through the normal
  plugin regeneration/reinstallation workflow only after repository tests pass.

## Conditional Session Identity Work

The interface refactor does not by itself fix live identity. The writer reads
`CLAUDE_CODE_SESSION_ID`, `CODEX_SESSION_ID`, and `CODEX_THREAD_ID` from its
process environment, but `mcp_server.invoke` receives no stable host
conversation identity and the plugin manifest supplies only static host and
plugin-data values.

The candidate transport is trusted per-call MCP request metadata containing a
stable `famulus_session_id` and `famulus_agent_id`. These are execution context,
not milestone interface arguments. This transport is not accepted until a live
Codex or Claude host demonstrably supplies stable values. After that proof, the
repository identity patch is exactly:

1. Make the FastMCP registration wrap `invoke` with request context.
2. Validate the two metadata values as safe bounded identifiers.
3. Add them to the resolved milestone subprocess environment as
   `FAMULUS_SESSION_ID` and `FAMULUS_AGENT_ID` without exposing them in the
   nine argument contracts.
4. Make the writer prefer those normalized variables, then retain the existing
   direct-run compatibility variables.
5. If no identity is available, return `milestone identity unavailable: host
   supplied no stable session identity` and do not write an `unknown` record.
6. Add an MCP integration test that supplies metadata for one session and two
   agents, records through both agents, and verifies filenames, grouping, and
   timeline reconstruction.

That conditional repository patch modifies `mcp_server.py`,
`src/officina/dispatcher/direct_runtime.py`,
`skills/milestone-logging/_rtx/_milestone_writer.py`,
`tests/test_famulus_mcp.py`, and the milestone-local journal tests. No identity
transport changes belong in the nine-interface patch before the host proof.

FastMCP request IDs, generic client IDs, a newly minted MCP-process UUID, and
the newest transcript on disk are not acceptable substitutes: none is proven
to equal the current Codex or Claude conversation. Therefore the repository
must not claim that live identity is fixed until the host carrier is observed
and tested. `mcp.json` remains unchanged unless the host specification adds an
explicit per-call placeholder; static environment configuration cannot solve
this boundary.

## Compatibility and Migration

The two obsolete `_rtx` exports receive no compatibility aliases, because an
alias would preserve the ambiguous contract. They are private interfaces; all
known repository callers and generated guidance move atomically to the new
names. Existing JSONL records remain readable. New typed fields in session
records are backward-compatible because readers already tolerate additional
JSON keys.

The direct compatibility command may remain non-exported if external human use
is documented and tested, but it must not appear in module exports or generated
agent guidance. Its legacy mode combinations are not part of the corrected
machine-interface contract.

## Verification Gates

The implementation is complete only when all of the following hold:

- Blueprint validation and generated-file checks pass.
- Every one of the nine bindings accepts its documented invocation and rejects
  old mode flags at dispatcher resolution.
- Pairwise recording-option tests find no conditional validity or suppression.
- A `--task` record without `--run` succeeds and the task appears in the session
  record and rendered timeline.
- Adding `--run` leaves every pre-existing session-record field unchanged, adds
  only the run identity fields, and appends a run-journal line byte-identical to
  the resulting session line.
- Maximum independent serialized-JSON values fit `LINE_BUDGET`; evidence is
  either accepted unchanged or rejected solely against its own declared quota.
- Diagnostics name the exact milestone interface and never
  `python_machine_interface_runner.py`.
- Session listing labels file count as log files, never as agents.
- The milestone-local suite passes, followed by affected repository tests and
  the repository's standard 8-worker pre-commit suite.
- A fresh installed-plugin process exposes the nine generated interfaces.
- Live identity is claimed fixed only after a real host call demonstrates a
  non-`unknown` session and correct per-agent grouping.

## Audited Size

For the nine-interface repository refactor, excluding host identity transport:

| Category | Low | Likely | High |
|---|---:|---:|---:|
| Pure removals | 90 | 130 | 180 |
| In-place modified lines | 170 | 240 | 335 |
| New lines | 380 | 525 | 715 |
| Logical LOC handled | 640 | **895** | 1,230 |
| Net repository growth | +290 | **+395** | +535 |
| Git additions | 545 | **765** | 1,035 |
| Git deletions | 260 | **375** | 520 |
| Total Git diff churn | 805 | **1,140** | 1,555 |

The repository side of identity-metadata propagation, after a verified host
carrier exists, adds approximately 55–185 logical LOC, with 110 likely. The
host change is outside this repository and is not included. The design document
itself is also excluded from the implementation estimate.

## Subagent Audit Resolution

Three independent read-only audits reviewed the proposal:

- The additivity audit found the typed-field/run dependency, default slow
  annotations, and combined evidence truncation to be additional violations.
  It recommended the strict nine-interface surface.
- The touchpoint audit initially proposed separate run-event interfaces, then
  accepted that storing typed metadata in every session record makes those
  interfaces redundant: optional `run` can be a purely additive mirror.
- The LOC audit verified that YAML anchors prevent contract boilerplate from
  dominating the patch and placed the likely implementation at approximately
  895 logical LOC, not 1,800–2,500.

The remaining difference between the eight- and nine-interface audits concerned
whether omission of `SESSION` may select the latest session. This design chooses
nine interfaces because separating `show-latest-session` from `show-session`
most literally satisfies the user's no-modes, additive-effects requirement.
