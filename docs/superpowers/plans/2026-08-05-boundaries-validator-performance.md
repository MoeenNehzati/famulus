# Boundaries Validator Performance Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove repeated per-line regex preparation from the cross-skill boundary validator without changing its API, findings, ordering, or accepted inputs.

**Architecture:** Compile three repository-wide regex patterns once per `validate` call. Each pattern captures referenced skill names; the scan collects direct-path and `sys.path.insert` violations, then retains the existing alphabetical per-skill decision order.

**Tech Stack:** Python 3, `re.Pattern`, pytest.

## Global Constraints

- Preserve `validate(repo_root: Path) -> list[str]`.
- Preserve exact finding text and finding order.
- Preserve `_rtx`, `_cx`, same-skill, comment, decoding, and `sys.path.insert` behavior.
- Keep prepared matchers local to one validation invocation.
- Treat this as a performance refactor; do not broaden conformance semantics.

---

### Task 1: Prepare repository-wide boundary matchers once

**Files:**
- Modify: `validators/skill/boundaries.py`
- Test: `tests/validate_boundaries.py`

**Interfaces:**
- Produces: `_compile_direct_runtime_patterns(skill_names: list[str]) -> tuple[re.Pattern[str], ...]`
- Preserves: `validate(repo_root: Path) -> list[str]`

- [x] **Step 1: Add characterization and preparation-count tests**

Add tests that construct caller, same-skill, `alpha-skill`, and `zeta-skill`
fixtures. Assert that:

```python
assert errors == [
    "skills/caller-skill/_rtx/run.py:1: direct cross-skill runtime path to alpha-skill is forbidden"
]
```

when a line names `zeta-skill` before `alpha-skill`, preserving alphabetical
selection. Add a `sys.path.insert` line where an alphabetically earlier skill
competes with a later direct-path violation and assert the earlier
`sys.path.insert` finding remains selected. Monkeypatch `re.compile`, validate a
multi-line file, and assert exactly three boundary matchers are compiled.

- [x] **Step 2: Run the focused tests and verify the preparation test fails**

Run:

```bash
PYTHONPATH=src python3 -m pytest -q tests/validate_boundaries.py
```

Expected: existing semantic tests pass; the matcher-preparation test fails
because `validate` does not yet call `re.compile` three times outside the line
loop.

- [x] **Step 3: Implement the minimal prepared matcher boundary**

Add:

```python
def _compile_direct_runtime_patterns(
    skill_names: list[str],
) -> tuple[re.Pattern[str], ...]:
    alternatives = "|".join(
        re.escape(name) for name in sorted(skill_names, key=lambda name: (-len(name), name))
    )
    target = rf"(?P<skill>{alternatives})"
    return (
        re.compile(rf"(?:^|[^A-Za-z0-9_-])(?:\.\./)+{target}/_(?:rtx|cx)/"),
        re.compile(rf"(?:^|[^A-Za-z0-9_-])skills/{target}/_(?:rtx|cx)/"),
        re.compile(rf"/skills/{target}/_(?:rtx|cx)/"),
    )
```

Call it once after discovering `skill_names`. For each non-comment line, collect
all captured direct targets from `finditer`, discard `skill_name`, collect
`sys.path.insert` substring targets under the existing guard, then iterate
`other_skills` once to emit the first violation with the current wording.

- [x] **Step 4: Run focused behavioral tests**

Run:

```bash
PYTHONPATH=src python3 -m pytest -q tests/validate_boundaries.py
```

Expected: all tests pass.

- [x] **Step 5: Verify repository behavior and performance**

Run the full validator suite and five warm direct timings of
`skill-maker/boundaries`. Confirm zero findings and compare against the `6.66s`
direct baseline. Run five repository-runner timings to separate the remaining
fixed staged-mirror and pytest overhead.

- [x] **Step 6: Commit after explicit authorization**

Stage only the spec, plan, validator, and focused test. Do not commit until the
user explicitly requests it.
