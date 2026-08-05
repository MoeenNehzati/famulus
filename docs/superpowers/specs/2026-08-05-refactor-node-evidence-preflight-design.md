# Refactor-Node Evidence Preflight Design

## Goal

Integrate the reusable lessons from the last refactor pass without enlarging the
canonical standards or duplicating provenance already exposed by the dispatcher.

## Verified diagnosis

The canonical standards already cover behavior preservation, ownership,
dependencies, authorization, effects, and validation. The remaining failures
are operational:

1. A standards query against an isolated worktree was executed by the installed
   runtime from the main checkout. The dispatcher dry-run already exposed the
   mismatch through its exact command, `cwd`, and `python_target.gateway_path`,
   but the refactor workflow did not require that preflight to be retained and
   checked.
2. When an evidence projection was empty, refactors improvised validator lists
   without distinguishing canonical evidence from owner-identified supplemental
   checks or from requirements that remained unmapped.
3. Structural refactors were sometimes judged as though they needed a fabricated
   behavioral failure. A behavior repair needs genuine RED evidence; a
   behavior-preserving structural move needs a standards-backed design pressure
   and green characterization before and after the move.

## Design

Change only the authored `refactor-node` router and its focused routing test.

1. Before every standards query, run and retain the exact dispatcher `--dry-run`.
   Resolve `cwd/python_target.gateway_path` and compare it with
   `<reviewed-root>/skills/refactor-node/_rtx/_closure_engine.py`. A mismatch is
   a sufficient rejection signal; a match identifies the selected gateway but
   does not prove the full imported runtime closure. On mismatch, select the
   reviewed checkout through the installed wrapper's supported
   `AI=<reviewed-root>` override, rerun the exact dry-run, and execute only after
   the gateway path matches. Also verify that the rendered `command` contains the
   intended target, repository root, facts, view, and refs. Retain that command
   for exact request replay.
2. Query evidence for every affected normative ref in every owner partition,
   plus the refs used to diagnose and remedy the pressure. Report three disjoint
   groups:
   - canonical evidence returned by the standards query;
   - supplemental change-relevant tests or validators, naming their actual owner
     and limitations, including directly affected consumer checks;
   - every requested normative ref with no mapped evidence.
3. State the defect-versus-structural RED distinction once in the change stage.
   This pass authorizes behavior-preserving refactors only. If diagnosis reveals
   a behavioral defect, report and stop that move; fixing it needs separately
   approved scope and genuine RED/GREEN evidence. Do not repeat route-specific
   behavior catalogs.
4. Recover space by removing the rarely used generic-query/debug rows and the
   verbose raw `refs-json` example. Keep the exact-ref rule and normal views.

## Why the standards stay unchanged

The observed omissions do not reveal missing normative policy. Adding generic
evidence profiles would invent records without an owner-specific basis. The
existing `tighten-description` remedy is reachable through a live
`trigger-remedy` link and must remain. The fix is better selection and labeling
of existing information, not more canonical rules.

## Rejected alternatives

- Do not add a second request object to query results. The retained dry-run
  command plus existing result fields already preserve the invocation.
- Do not hash only `_closure_engine.py`. Shared extractor, graph, and inventory
  modules also determine the result, so a one-file digest would overstate runtime
  provenance; hashing the full runtime closure is disproportionate here.
- Do not change the query schema, interface version, dispatcher, canonical
  standards, evidence records, or route-specific instruction files.

## Five-skill calibration pass

After this change is validator-green and independently approved, refactor these
registered skills sequentially:

1. `loose-mode`
2. `git-workflow`
3. `latex-workshop`
4. `connect-google`, including its registered `_rtx` implementation child
5. `update-standards`

For each skill: query the whole registered node, inspect every returned supported
behavioral source, record an affected-only preservation map, accept no-churn when
no concrete pressure exists, run all relevant validators until green, obtain an
independent review, commit only the accepted iteration, and then continue.

## Verification

- A focused routing-contract test must fail before the router edit and pass after.
- Live acceptance checks must show that the unqualified dry-run exposes the
  main-checkout/worktree mismatch, that the workflow rejects consuming it, and
  that `AI=<reviewed-root>` selects the reviewed gateway. Independent reviewers
  must confirm that the wording prevents unlabeled evidence substitution.
- Each later skill iteration must be green before it is consumed as the starting
  point for the next iteration.
- Finish with a combined diff audit, repository validator run, and a concise
  completion record separating changes, no-churn decisions, and reusable lessons.
