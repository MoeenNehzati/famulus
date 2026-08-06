# Remaining Validator Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the remaining material validator bottlenecks while preserving exact findings, ordering, public entry points, and staged-snapshot isolation.

**Architecture:** Optimize measured repeated preparation at its narrowest safe lifetime. Reuse the existing session blueprint graph, hoist validator-local path and regex preparation, and add a lazy session-scoped Python parse cache whose direct-call fallback remains local. Do not share traversal-dependent findings or broaden validation scope.

**Tech Stack:** Python, pytest fixtures, `ast`, existing repository validator collector.

## Global Constraints

- Performance refactor only; no validation-rule or coverage changes.
- Compare exact ordered finding lists on valid and invalid fixtures.
- Keep `validate(repo_root)` and existing `validate_with_graph` callers working.
- Report direct validator time separately from staged-runner overhead.
- Optimize items measured at 0.30 seconds or more; audit smaller validators and leave them unchanged unless they benefit from shared preparation.

---

### Task 0: Capture the committed baseline

**Files:**
- Record only; do not modify repository files.

- [ ] Record commit `acc1b4e` and verify a clean worktree and index.
- [ ] Verify the committed standard-document checkpoint contains prepared `best_match(iter_errors(...))` validation and the exact `children=[]` diagnostic regression before using it as baseline.
- [ ] Run the staged validator suite at least five times with pytest durations enabled, using the same repetition count and statistic planned for final measurements.
- [ ] Record total validator phase, pytest item setup/call times, staged snapshot and runner overhead, and full pre-commit phase wall time.
- [ ] Preserve the exact commands and environment for repetition against the final commit.

### Task 1: Reuse the session blueprint graph

**Files:**
- Modify: `validators/duplicate_subcommand_tokens.py`
- Test: `tests/test_duplicate_subcommand_tokens.py`

**Interfaces:**
- Consumes: runner-provided `graph` fixture and existing repository graph type.
- Produces: `REQUIRES_BLUEPRINT_GRAPH`, `validate_with_graph(repo_root, graph)`, and unchanged `validate(repo_root)` compatibility wrapper.

- [ ] Add a failing test proving suite execution does not call `load_repository_blueprint_graph` a second time and exact findings match the compatibility path.
- [ ] Convert the pytest path to `validate_with_graph`; retain direct `validate` behavior.
- [ ] Run focused tests, canonical docstrings, direct timing, and full validators.
- [ ] Commit the exact task files.

### Task 2: Hoist platform-neutral path preparation

**Files:**
- Modify: `validators/platform_neutral.py`
- Test: `tests/validate_platform_neutral.py`

**Interfaces:**
- Produces: unchanged `validate_with_graph(repo_root, graph) -> list[str]`.

- [ ] Add a failing counting test showing each scanned path is relativized once rather than during discovery and again per source line.
- [ ] Make `_iter_files` compute and yield `(path, relative_path)` once; reuse the supplied relative path throughout validation without changing discovery or message text.
- [ ] Run focused tests, exact-output characterization, timing, and full validators.
- [ ] Commit the exact task files.

### Task 3: Prepare runtime-reference regexes once

**Files:**
- Modify: `validators/skill_runtime_doc_references.py`
- Test: `tests/validate_skill_runtime_doc_references.py`

**Interfaces:**
- Produces: unchanged graph-backed validator entry points.

- [ ] Add a failing test proving stem and suffix patterns are constructed once per runtime stem, independent of Markdown-file and line counts.
- [ ] Build one per-skill prepared pattern table before the Markdown-file loop and reuse it during scanning.
- [ ] Run focused tests, exact-output characterization, timing, and full validators.
- [ ] Commit the exact task files.

### Task 4: Avoid unnecessary personal-info substitutions

**Files:**
- Modify: `validators/personal_info.py`
- Test: `tests/validate_personal_info.py`

**Interfaces:**
- Produces: unchanged `validate(repo_root) -> list[str]`.

- [ ] Add a failing counting test proving allow-pattern substitution runs only on lines containing a forbidden token candidate.
- [ ] Search for forbidden tokens first, then apply the existing allow-pattern scrubbing only to candidate paths or lines.
- [ ] Characterize exact output for allowed and forbidden occurrences in both orders.
- [ ] Run focused tests, timing, and full validators; commit the exact task files.

### Task 5: Share lazy Python parsing across AST validators

**Files:**
- Modify: `src/officina/repository_checks.py`
- Create or modify: `src/officina/common/python_source_cache.py`
- Modify: `validators/cross_platform.py`
- Modify: `validators/toml_io_boundary.py`
- Modify: `validators/skill/dispatch_caller_module.py`
- Modify: `validators/subprocess_text_encoding.py`
- Modify: `validators/portable_dates.py`
- Modify: `validators/skip_hygiene.py`
- Test: focused validator tests plus `tests/test_repository_validator_checks.py`

**Interfaces:**
- Produces: lazy `PythonSourceCache.read_parse(path) -> (source, ast.Module)` with replayed parse exceptions; session fixture `python_source_cache`; unchanged `validate(repo_root)` wrappers create a local cache; pytest-only `test_*` items require and receive the session fixture.

- [ ] Characterize each validator's discovery set, parse-error handling, and exact ordered findings before changing code.
- [ ] Add a failing runner test proving two validator items requesting the same file parse it once within one staged session and a second session starts fresh.
- [ ] Implement the lazy cache bound to the immutable staged root without eager discovery. Key by the exact absolute input path without resolving symlink aliases; replay each caller's existing `OSError`, `UnicodeError`, and `SyntaxError` boundary; never cache formatted findings.
- [ ] For each validator, keep `validate(repo_root)` as a local-cache compatibility wrapper, move logic to private `_validate(...)`, and expose a pytest `test_*` item whose required arguments include `python_source_cache`.
- [ ] For graph-backed `cross_platform` and `dispatch_caller_module`, keep both public `validate(repo_root)` and `validate_with_graph(repo_root, graph)` signatures unchanged; their pytest items require `(repo_root, graph, python_source_cache)` and call private `_validate(repo_root, graph, cache)`.
- [ ] Prove cached AST consumers are read-only with both-order execution tests, or provide isolated views whose measured copy cost remains below reparsing cost.
- [ ] Convert and commit one validator at a time. Compare the exact direct-wrapper and shared-cache finding lists for valid, invalid, malformed-Python, unreadable, and Unicode fixtures within that validator's existing exception behavior.
- [ ] Add cross-platform tests proving its three internal passes reuse one parsed tree per file.
- [ ] Reprofile parse and walk counts; consolidate validator-local traversals for any converted validator still at or above the repeated-run 0.30-second cutoff.
- [ ] Run the combined AST-validator subset, full validators, and direct/runner timing.
- [ ] Commit cache infrastructure first, then each converted validator as its own rollback checkpoint.

### Task 6: Audit blueprint subprocess overhead

**Files:**
- Inspect first: `validators/skill/blueprints.py`
- Test: `tests/validate_blueprints.py`

**Interfaces:**
- Preserve subprocess isolation and exact findings unless equivalence is proven.

- [ ] Measure the git-inventory and blueprint-sync subprocesses separately after prior tasks.
- [ ] Compare their outputs with any already-prepared graph or inventory state.
- [ ] If a replacement preserves process isolation and exact output, add a failing equivalence test and implement it; otherwise record the subprocess boundary as intentional overhead and make no code change.
- [ ] Run focused tests and full validators; commit only if code changed.

### Task 7: Final audit and performance report

**Files:**
- Modify: `docs/design/test-suite-performance.md`

- [ ] Re-profile every canonical validator at least five times through one fixed clean staged snapshot and classify the 0.30-second cutoff from the repeated range or median.
- [ ] Measure each optimized validator directly at least five times.
- [ ] Remeasure borderline `generated_skill_docs` and audit validators below the cutoff; do not micro-optimize unless shared preparation already improves them.
- [ ] Record four modes separately: standalone compatibility call, pytest item setup/call with session dependencies, full validator-phase wall time, and staged snapshot/runner overhead.
- [ ] Record shared graph preparation as residual previously optimized setup, even if unchanged by this plan.
- [ ] Use the same repetition count and statistic for baseline and final per-validator, validator-phase, runner-overhead, and pre-commit measurements; state cold and warm measurements separately when they differ.
- [ ] Record before/after median or range for each changed validator, remaining overhead, combined validator phase, and pre-commit phase.
- [ ] Run the full validator suite and full pre-commit hook from a clean staged checkpoint.
- [ ] Commit the concise final performance report.
