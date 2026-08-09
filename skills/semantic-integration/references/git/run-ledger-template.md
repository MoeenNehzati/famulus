# Semantic integration run ledger

Keep entries concise. Link evidence or name commands; do not paste full patches or
logs.

## Run

| Field | Value |
|---|---|
| Repository | |
| Target branch | |
| Frozen target commit | |
| Source branch | |
| Frozen source commit | |
| Merge base | |
| Integration branch | |
| Closure-candidate branch | |
| Closure (`merge` or `rebase`) | |
| Portable bundle, if any | |
| Started | |
| Active-time estimate | |
| Current phase | |

### Recovery refs

| Ref | Expected frozen commit | Closure verification |
|---|---|---|
| | | pending |

## Intent

### Target guarantees

-

### Source objectives

-

## Baselines

| Tip | Evidence | Result | Pre-existing limitations |
|---|---|---|---|
| Target | | | |
| Source | | | |

## Source commit accounting

Use one row for every source-only commit. Every `superseded` or `rejected`
commit, endpoint item, or source test requires an approval reference.

Derive intent from the patch, not its message alone. Each row cites at least one
concrete diff anchor: a changed path plus a relevant symbol, test, configuration
key, migration, or deletion.

| Source commit | Intent | Concrete diff anchor | Dependencies | Modules or paths | Endpoint IDs | Disposition | Consequence | Integration commit | Evidence | Approval |
|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | unresolved | | | | |

## Endpoint accounting

Inventory the merge-base-to-source endpoint independently of commit history.
Include additions, modifications, deletions, renames, modes, dependencies,
configuration, interfaces, tests, fixtures, generated artifacts, migrations, and
persistent-state effects.

| ID | Category | Source effect | Paths or interfaces | Disposition | Consequence | Integration commit | Evidence | Approval |
|---|---|---|---|---|---|---|---|---|
| E001 | | | | unresolved | | | | |

## Target protection accounting

Index every target-only commit and endpoint effect. Entries may be grouped when
they share one guarantee and evidence, but each grouped row must enumerate every
covered commit ID and endpoint ID. Do not use an unexpanded range or prose
placeholder.

| ID | Target commit or endpoint effect | Guarantee to retain | Paths or interfaces | Slice or invariant | Evidence | Status |
|---|---|---|---|---|---|---|
| T001 | | | | | | unresolved |

## Changed-on-both accounting

Include clean textual applications; they still require semantic review.

| ID | Shared path or surface | Target effect | Source effect | Semantic resolution | Slice | Evidence | Status |
|---|---|---|---|---|---|---|---|
| O001 | | | | | | | unresolved |

## Source-test accounting

| Source test or file | Intended assertion | Disposition | Resulting test or evidence | Focused result | Runner collection evidence | Consequence | Approval |
|---|---|---|---|---|---|---|---|
| | | unresolved | | | | | |

## Semantic slices

| Slice | Source, target, and overlap IDs | Dependencies | Intended behavior | Planned paths or modules | Validation | Estimate | Hard stop | Actual | Status |
|---|---|---|---|---|---|---|---|---|---|
| S01 | | | | | | | | | pending |

## Approval gates

| Gate | Evidence presented | Decision | Approval reference | Time |
|---|---|---|---|---|
| Preservation contract | | pending | | |
| Reconstruction authority | | pending | | |
| Closure authority | | pending | | |

## Progress and deviations

| Time | Phase | Accounting complete | Slice or blocker | Active time | Retry count | Unplanned scope | Plan credible? | Adjustment |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

## Assurance audits

### Source-preservation audit

- Auditor or pass:
- Evidence reviewed:
- Findings:
- Resolutions:
- Remaining limitations:

### Target-regression audit

- Auditor or pass:
- Evidence reviewed:
- Findings:
- Resolutions:
- Remaining limitations:

## Final validation

- Tested closure-candidate commit:
- Tested closure-candidate tree:
- Approved target tip:
- Gate 3 invalidated by any candidate change or any target movement other than
  the authorized fast-forward to that exact candidate: yes

| Scope | Candidate commit | Command or evidence | Result | Duration | Skips, warnings, or limitations |
|---|---|---|---|---|---|
| Focused affected subsystems | | | | | |
| Repository integration gate | | | | | |
| Push-equivalent gate | | | | | |

## Closure

| Check | Evidence | Result |
|---|---|---|
| Target equals tested closure-candidate commit and tree | | pending |
| Target fast-forward created no post-gate commit | | pending |
| Ancestry-only merge changed no files | | pending or not applicable |
| Expected ancestry holds | | pending |
| Worktree clean | | pending |
| Recovery refs retained | | pending |
| Source and target inputs unchanged before closure | | pending |

### Residual risks

-

### Final disposition

- Target commit:
- Integration commit:
- Source ancestry status:
- Branch deletion authorized separately: no
- Push authorized separately: no
