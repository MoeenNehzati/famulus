# Recurring Renderer Simplification Sequence

**Status:** Implemented on branch `recurring-renderer-simplification`; not committed.

**Goal:** Make the managed recurring runtime the sole scheduler-rendering authority without creating a new shared API solely to preserve obsolete compatibility code.

## Decisions

- Keep `src/officina/recurring/native.py` as the live native-rendering owner.
- Correct and test the live renderer before removing compatibility code.
- Prefer deleting an unused compatibility path over exporting a new cross-module renderer interface.
- Treat the current platform-neutral cron sentence as unresolved contract evidence, not as authority for widening every platform.
- Before implementation, decide whether it promises identical cross-platform grammar. If yes, stop and split a contract change. If no, amend the authored instructions to state the frozen Linux subset and the separately supported macOS and Windows subsets.
- Freeze the Linux subset as exactly five fields: minute and hour each accept `*`, `*/N` for positive `N`, or one bounded bare integer; day-of-month and month must be `*`; weekday accepts `*` or one bare integer from `0` through `7`.
- Keep stored schedules, enabled state, success semantics, and public interfaces unchanged; the specified Linux rendering and validation corrections are the only behavior changes.

## Known gaps

- The core renderer discards a fixed or stepped hour when the minute is stepped. For example, `*/15 9 * * *` becomes every fifteen minutes in every hour.
- Wildcard minutes such as `* 9 * * *` are not handled by the core renderer.
- Step zero and out-of-range minute/hour values are not rejected consistently.
- The skill-local Linux backend has stronger field-by-field rendering, but it belongs to retained compatibility code rather than the public managed execution path.
- Directly importing a private core helper into the skill backend would violate the current Officina module contract.
- The repository task `native:scheduler` still imports skill-local compatibility renderers; it must be migrated before those renderers are deleted.

## Recommended sequence

### 1. Freeze scope and inventory consumers

- Before changing more renderer code, inventory source imports, authored blueprint dependencies, repository task selectors, native-host smoke tests, and external string-path references to the skill-local backend.
- Classify each consumer as supported production, public delegation, migration-only, or test-only.
- Preserve existing delegation evidence for setup, sync, job control, and healthcheck. Add tests only for a distinct uncovered public boundary.
- Stop if any supported production route still requires the compatibility renderer.

**Gate:** The inventory accounts for every source and blueprint edge into the compatibility closure, identifies how `native:scheduler` will retain native-host coverage, and records the cross-platform documentation decision before renderer code changes begin.

### 2. Strengthen the live Linux core contract

- Define one strict conversion contract in the recurring core: wrong arity, unsupported syntax, non-wildcard day-of-month/month, invalid weekdays, zero or negative steps, and out-of-range minute/hour values raise `ValueError`.
- Render hour and minute independently so wildcard, fixed, and stepped combinations preserve both constraints.
- Keep weekday `0` and `7` equivalent to Sunday.
- Do not add a new public renderer interface.
- Stop and split a separate contract change if completing this work requires harmonizing macOS or Windows grammar.

**Gate:** Table-driven core tests cover the accepted grammar and rejection cases. Exact-output assertions catch valid-but-wrong calendars, and `systemd-analyze calendar` accepts representative outputs on systemd hosts.

### 3. Remove the compatibility renderer path

- Remove `skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py` from `native:scheduler` only after retained managed-runtime tests provide the required native-host coverage.
- Delete `_schedule_backend/`, `_schedule_context.py`, and `_install_owner.py` only if Step 1 confirms no remaining non-test consumer.
- Reduce `_setup_runner.py`, `_unit_writer.py`, `_job_control.py`, and `_healthcheck_probe.py` to their delegation-only entrypoints; keep `_managed_control.py`, `_job_executor.py`, and `_jobs_config.py` because execution compatibility is outside this renderer-only change.
- Remove tests and authored blueprint nodes whose only subject is the deleted compatibility closure. Preserve delegation tests and distinct managed-runtime behavior tests; move renderer behavior tests to the core owner.
- Do not replace deleted compatibility code with a shared `common` helper or a new exported native-renderer interface.

**Gate:** `rg` finds no production or test import of the deleted compatibility closure; `native:scheduler` names only retained managed-runtime tests; only the managed platform renderer implementations in `src/officina/recurring/native.py` remain; and every public skill interface still delegates with unchanged arguments and exit status.

### 4. Close repository ownership and certification

- Update authored blueprints to remove deleted sources, dependencies, content claims, and obsolete test ownership.
- Synchronize projections through the registered `skill-maker._rtx.interface.sync-blueprints` interface, then rerun it in check mode; do not hand-edit generated `SKILL.md` blocks or `references/blueprint-schema/runtime_dependencies.json`.
- Run repository-wide JSON drift status. Removed source IDs must be absent from traversal; affected retained nodes may report stale. Do not issue fresh certificates unless explicitly requested.

**Gate:** Blueprint synchronization and ownership validation pass; drift output contains no removed source IDs and accurately identifies affected retained nodes as current or stale.

### 5. Verify the complete change

Run focused core recurring tests, public recurring-task delegation tests, the native scheduler task, repository validators, and `git diff --check`. On Linux, validate representative generated calendars with `systemd-analyze calendar`.

Inspect the final changed-path set and keep unrelated dirty work outside the change.

## Non-goals

- Repairing the independent cron healthcheck sentinel's command-length failure.
- Changing stored schedules, enabled state, success contracts, or public interfaces.
- Changing managed macOS or Windows scheduling behavior; deletion is limited to compatibility code proven unreachable.
- Expanding `officina.common` or adding a public renderer API merely to deduplicate code.
- Combining this work with installer-context or development-activation repairs.

## Completion criteria

- The Linux core renderer implements the frozen Linux contract and rejects invalid values deterministically.
- Public Linux recurring operations use the corrected Linux renderer; public operations on every platform use only the managed renderers in `src/officina/recurring/native.py`.
- Obsolete compatibility rendering is deleted rather than preserved behind a wider API.
- Tests remain at the production owner and public boundaries, without duplicate implementation suites.
- Authored blueprints, generated projections, and certification drift agree with the final ownership graph.
