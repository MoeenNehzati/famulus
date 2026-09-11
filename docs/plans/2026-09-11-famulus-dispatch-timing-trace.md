# Famulus Dispatcher timing trace

**Status:** Implemented and locally verified on 2026-09-11; certificate
refresh follows the exact implementation commit.

## Goal

Make recurring Famulus latency attributable without asking the LLM to narrate
tool calls. The host records the outer tool call mechanically, Famulus records
the nested execution tree mechanically, and milestone logging records only
semantic agent intent and progress.

## Boundary

Record two central layers: each Dispatcher-launched process and each Python
interface body. Do not record arbitrary Python calls, HTTP requests, or raw
subprocesses. A slow leaf remains aggregated until evidence justifies narrower
instrumentation inside that leaf.

This is an internal execution trace, not an assistant lifecycle hook. Do not add
it to the cross-host hook registry, and never invoke a milestone-logging
interface from the trace path because that would re-enter Dispatcher.

## Minimal design

### 1. Establish exact correlation at the public invocation

At `mcp_server.py::invoke`, use a shared Officina trace scope to create and
reset one random `trace_id`. This is correlation only; do not add an `invoke`
timing record. Add the trace ID to every non-dry-run MCP result so the timeline
can join the exact host tool output to the exact Famulus trace without guessing
from timestamps. Keep dry-run output unchanged.

This deliberately extends `ExecutionResult` from four fields to five:
`exit_code`, `stdout`, `stderr`, `dispatcher`, and `trace_id`. Update every
non-dry-run success, setup-refusal, and normalized-error result plus the public
result-contract fixtures. This is an additive public contract change, not an
internal implementation detail.

The trace groups the top-level setup-status, authorization, and requested-target
calls. Without it those calls are unrelated siblings and concurrent invocations
cannot be reconstructed reliably.

### 2. Record every Dispatcher process in the shared executor

At `src/officina/dispatcher/direct_runtime.py::_run_resolved_invocation`:

1. Read the active `trace_id` and inherited parent span.
2. Create one `span_id` before pipe, thread, and process launch.
3. Add the trace and current span IDs to the child's confined environment.
4. Measure the complete executor with `time.perf_counter_ns()` through cleanup.
5. Record one completion after capturing duration and before returning or
   reraising.

Every managed setup call, public target, and declared nested
`PythonMachineInterface.dispatch()` already passes through this executor.

### 3. Record one Python interface-body span

At `src/officina/runtime/python_machine_interface_runner.py`, record one child
span around `interface.run(args)`. Make that body span active while it runs so
nested Dispatcher calls attach beneath it.

The process span minus the body span identifies interpreter, import, parser,
runner, and cleanup overhead. The body span minus its nested Dispatcher spans
identifies local skill work. This is the minimum second boundary needed to
distinguish slow cloud or list work from Python process overhead without
instrumenting HTTP or private helper subprocesses.

### 4. Use one shared, allowlisted trace record

Add one stdlib-only `src/officina/runtime/dispatch_trace.py` module shared by
MCP, the Dispatcher executor, and the Python interface runner. It owns trace
context, fixed-format ID generation and validation, environment bootstrap and
propagation, span timing, serialization, and best-effort storage.

Process records contain only:

```text
schema
layer                 process | interface_body
trace_id
span_id
parent_span_id
caller
interface
wall_started_ns
monotonic_started_ns
duration_ns
outcome
exit_code             optional
```

Interface-body records omit `caller`, `interface`, and `exit_code`; their
parent process span already owns that identity. They retain the layer, three
IDs, two starts, duration, and outcome.

Wall time aligns the trace with host events. The system-wide monotonic clock
orders cross-process spans and supplies durations and self-time calculations.
Never record arguments, stdin, stdout, stderr, environment, URLs, credentials,
working directories, or exception messages.

Write one completion record rather than separate start and end records. Normal
returns, nonzero exits, timeouts, killed children, and raised exceptions must
record an outcome when the parent survives. Add start records only if
parent-process death or permanently hung calls become a measured diagnostic
need.

`ContextVar` state does not cross processes. Each child must bootstrap from the
two inherited trace environment values, validate fixed lowercase hexadecimal
IDs before using them, and then set/reset local context. Invalid inherited IDs
disable that trace rather than escaping the trace root.

### 5. Keep storage isolated and contention-free

Store mechanical traces separately from semantic milestones, with one exclusive
file per completed span:

```text
$ASSISTANT_LOGS/dispatch/YYYY-MM-DD/<trace_id>/<span_id>.json
```

Use the existing `ensure_private_directory` and `atomic_create_bytes` helpers to
create each file once with mode `0600`. IDs, not caller data, form path
segments. Each span has one writer, so this needs no append lock or
milestone-writer extraction. Trace failure is silently dropped: it must not
change return values, exceptions, stdout, or stderr.

These files are Officina-owned infrastructure telemetry under its configured
logging root, not interface-owned business I/O. Do not add the trace write to
individual interfaces' `direct_io` declarations.

### 6. Render all three outer phases and the internal tree

Extend `skills/milestone-logging/_rtx/_agent_timeline.py` to:

1. Discover exact and latest host sessions from host transcripts independently
   of milestone files; merge semantic milestones when present, but require none.
2. Bind events to the same host turn using its recorded `turn_id`, pair calls
   and outputs by `call_id`, and select that turn's assistant message whose
   phase is `final_answer`; do not mistake commentary for the response.
3. Report request-to-call decision time, call-to-output execution time, and
   output-to-assistant response-creation time. The last interval does not claim
   to measure UI or network rendering after the assistant message exists.
4. Read `trace_id` from the non-dry-run tool output and open that exact trace.
5. Build the tree from `span_id` and `parent_span_id`.
6. Show inclusive duration and self/unattributed duration. Compute self time by
   subtracting the union of direct-child intervals so parallel children are not
   double-counted.
7. Skip malformed rows and show orphan spans without failing the timeline.

Keep the first implementation Codex-only for outer phase reconstruction.
Preserve the existing Claude timeline behavior, but defer Claude call/output
pairing until requested; do not spend this change's line budget on parity.

Expected shape:

```text
decision                              1.20s
Famulus call                          5.73s
|- setup status                       0.91s
|  `- interface body                  0.08s
|- setup authorize                    0.92s
|  `- interface body                  0.07s
`- list-manager process               3.74s
   `- interface body                  3.22s
      `- cloud-files process          1.90s
         `- interface body            1.31s
response creation                     0.35s
```

The host tool-call-to-output interval remains the end-to-end execution root.
Famulus spans explain its instrumented portion; the remainder is transport,
resolution, serialization, or other uninstrumented work.

### 7. Correct milestone guidance

In `skills/milestone-logging/SKILL.md`, remove the instruction to record every
few tool calls and add this rule:

> Tool calls and Dispatcher timing are recorded mechanically. Record semantic
> intent, decisions, blockers, and outcomes only; never log a milestone merely
> to announce or recap a tool call.

Do not change the milestone executable interface or semantic record schema.

## Expected source changes

Target: 460 nonblank authored lines across these 12 files. Hard cap: 500. Stop
and re-audit before exceeding the cap; mandatory production docstrings and
dependency declarations count toward it.

| File | Change | Cap |
|---|---|---:|
| `src/officina/runtime/dispatch_trace.py` | New context, validated environment propagation, documented secure storage using existing helpers, and two span kinds. | 105 |
| `mcp_server.py` | Establish trace context; add the exact fifth `ExecutionResult.trace_id` field to every non-dry-run result; no timing span. | 20 |
| `src/officina/dispatcher/direct_runtime.py` | Process span and required dependency documentation. | 20 |
| `src/officina/runtime/python_machine_interface_runner.py` | Interface-body span and required dependency documentation. | 12 |
| `skills/milestone-logging/_rtx/_agent_timeline.py` | Codex host-event pairing, three outer phases, tree assembly, interval-union self time, malformed-input handling, and rendering. | 140 |
| `skills/milestone-logging/_rtx/blueprints/rtx-agent-timeline.yaml` | Declare dispatch-trace JSON reads and transcript-first discovery. | 10 |
| `skills/milestone-logging/SKILL.md` | Semantic-only milestone guidance. | 2 |
| `references/certification-policy/certification-basis-roots.json` | Register the new runtime source. | 1 |
| `tests/test_officina_python_machine_interface.py` | Nested process/body/child hierarchy and sink-failure transparency. | 65 |
| `tests/test_famulus_mcp.py` | Public output-schema assertion. | 10 |
| `tests/test_mcp_setup_preflight.py` | Update exact non-dry-run result fixtures for `trace_id`. | 15 |
| `skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py` | No-milestone turn/call/result/tree fixture. | 60 |

The per-file caps sum to 460 lines; the remaining 40-line reserve is not a
target. Synchronized `_rtx/blueprint.yaml` and SKILL interface projections,
pooled review artifacts, and affected certificate journals are accepted
generated outputs and do not count as authored lines. Do not touch
`runtime/__init__.py`, `mcp-core.json`, per-interface blueprints, node standards,
or milestone writer code by hand.

## Acceptance criteria

1. One nested real-process test covers process -> body -> nested-process parent
   links, allowlisted records, and byte-identical behavior when the trace sink
   is unwritable.
2. Existing MCP schema and setup-preflight fixtures cover the fifth result field
   on every non-dry-run result shape while dry-run remains unchanged.
3. One Codex transcript fixture with no milestones joins `turn_id`, `call_id`,
   and returned `trace_id`; it reports all three outer phases and correct
   inclusive/self time without treating commentary as the final response.
4. Cold and warm before/after measurements show no material latency regression;
   reject or simplify the implementation if observer overhead is visible.
5. Blueprint synchronization, affected certificate regeneration, focused
   tests, and `python3 repo_checks.py --suite precommit --jobs 8` pass.

## Explicit exclusions

No `invoke` timing span, LLM-authored tool log, milestone-writer refactor,
lifecycle-hook registration, OpenTelemetry, external dependency, configuration
switch, database, dashboard, retention worker, global subprocess monkeypatch,
HTTP tracing, or speculative leaf instrumentation.
