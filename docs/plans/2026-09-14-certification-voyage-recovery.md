# Recoverable certification Voyage

Status: reviewed design; correctness, public-contract, and Ponytail audits GREEN
on 2026-09-14. This supersedes the current terminal-on-audit-failure policy when
implemented; it does not change runtime behavior by itself.

## Outcome

Keep one public certification Voyage available after an expected mechanical,
worker, or semantic-audit failure. Return the exact problem to its assigned agent,
wait while the repository or environment is fixed, then resume through `next`.
Do not mutate an attempt's immutable Charter or require the controller to create a
second public Voyage.

## Smallest durable design

Retain the existing `certification@1` Rutter unchanged for legacy Reckonings.
Define `certification-attempt@1` with the same attempt graph and behavior,
changing only its failure result as described below.
Add one certification-local outer `certification-session@1` Rutter:

```text
bind-attempt
  ready   -> attempt (certification-attempt@1 SubRutter)
  failed  -> problem

attempt
  complete -> complete
  failed   -> problem

problem (LLMStep)
  resume -> bind-attempt
  abort  -> aborted
```

The outer Charter contains only stable request intent: repository, requested
targets, worker capacity, run ID, and retry interval. Syntactically invalid mode
arguments remain initiation errors. Repository-dependent preparation belongs in
the repeat-safe `bind-attempt` machine step so those failures occur after the
outer Voyage exists.

`bind-attempt` calls the existing `make_charter()` and stores its result for the
child Charter constructor. Use that machine record's replay-stable `machine_id`
as the attempt run namespace. Every resume therefore creates fresh packet/task
IDs and a fresh child history without an epoch registry or mutable shadow state.

The existing attempt already converts expected preparation, validation, worker,
audit, drift, and signing errors to its `failed` result. Route that result to the
single outer `problem` step. Add the parsed report to that result only after
schema and task validation succeeds, and have its existing terminal result
constructor calculate outstanding task IDs as packet IDs minus retained-report
IDs minus the triggering failed task ID. The outer step then forwards this
durable child result unchanged. Do not mine archived history or create a failure
class hierarchy.

The problem message tells the controller to cancel and reap outstanding workers,
relay the failure to the agent or user, and fix the cause. After the fix, call
`next` with `{"outcome":"resume"}` and the problem message's `responding_to`
entrance. A response-free `next` returns the same problem message. `abort`
terminates explicitly.

## One recovery route

| Source | Existing result | New handling |
| --- | --- | --- |
| Initial graph, scope, runtime, or mechanical preparation | `make_charter()` error | `bind-attempt -> problem` |
| Per-node mechanical checks, drift, or signing guard | attempt `failed` | child return -> `problem` |
| Worker loss or malformed worker output | attempt `failed` | child return -> `problem` |
| Semantic `reject` or `abort` | attempt `failed` | child return -> `problem` with report |
| Unknown, duplicate, or stale envelope | validation rejection without mutation | remain at the current message |
| Corrupt Reckoning, invalid Rutter definition, or engine invariant | Rutter fault | remain blocked; do not recast as repairable |
| User chooses not to repair | `abort` response | terminal `aborted` |

Resume always starts a new immutable child attempt. It does not continue at the
failed inner evolution: the repair may have changed the commit, graph, scope,
audited inputs, preparation, or dependencies. The fresh attempt recomputes those
bindings and reuses the existing currentness path to skip valid certificates.
Signed certificates issued before a later failure remain available. Discard
unsigned reports from the failed attempt; cross-attempt report reuse needs no new
logic unless measured audit cost later justifies it.

## Core correction required by nesting

Rutter currently reads `automatic_transition_limit` only from the root Charter.
That would ignore the certification attempt's existing graph-sized limit once it
becomes a child. Fix this once in the engine: during each automatic iteration,
charge the active run against a counter keyed by its `run_id`, using that run's
own Charter limit. Counters last for one `next`/`advance` call, as today. A child's
larger budget must neither enlarge nor reset its parent's budget.

This is the only core Rutter change. `SubRutter` already provides fresh child
identity, Charter, history, archival, and result routing. Do not add an
`AttemptSubRutter`, `RecoverableSubRutter`, new evolution kind, or generic recovery
framework.

## Compatibility

The controller contract changes, so publish the recovered behavior as
`certification-voyage@3`. Keep `certification@1` untouched for legacy Reckonings,
use `certification-attempt@1` for new child attempts with enriched failure
results, and give the outer Rutter the distinct identity
`certification-session@1`. Register the outer session under the existing name
`certification` and the old `certification@1` under a secondary legacy name in
the same registry. Their Rutter IDs are distinct, so the existing binder can
retain both without new registry-selection logic. Old terminal failures do not
become retroactively resumable.

## Minimum implementation and checks

Change only the existing certification Voyage/support, the shared Rutter
continuation counter, their focused tests, and the public/generated contract and
canonical certification documentation required by the interface version bump.

Required checks:

1. Mechanical initialization failure returns `problem`; after repair, `resume`
   completes through the same public Voyage ID.
2. Semantic rejection returns the validated report and outstanding IDs; after a
   repository fix, `resume` uses fresh task IDs and can complete.
3. A buffered event from the failed attempt is rejected without mutation.
4. A certificate issued before a later failure remains current and is skipped by
   the fresh attempt.
5. A persisted problem state reopens and resumes.
6. Parent and child automatic-transition budgets remain independent, including a
   large mechanical-only certification attempt.
7. A retained `certification@1` Reckoning still opens and can be released.

## Non-goals

No automatic repair, polling for repository changes, cross-attempt unsigned-report
cache, new scheduler, mutable Charter, public retry command, second Voyage, or
generic recovery abstraction. Add shared authoring sugar only after a second
workflow needs this exact graph.
