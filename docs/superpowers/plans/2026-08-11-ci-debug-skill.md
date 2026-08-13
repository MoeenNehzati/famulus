# CI Debug Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:writing-skills` for instruction changes and `superpowers:test-driven-development` for the thin Python adapters.

**Goal:** Implement the smallest skill that repeatedly runs complete CI, delegates failing matrix elements to repair subagents, and lets those subagents rerun only their assigned failures.

**Architecture:** `SKILL.md` owns the outer loop. `repair-element.md` owns the inner subagent loop. `run-ci` forwards to the repository runner's full remote matrix. `run-targeted-tests` forwards one OS/task plus one selector, a prior report containing the active failure set, or the whole element. The repository runner owns all suites, matrix definitions, GitHub transport, diagnostics, and reports.

## Constraints

- Do not build a second CI runner, task inventory, GitHub client, artifact parser, incident database, or failure ledger.
- Bind every run to an exact pushed SHA.
- Targeted green must be followed by whole-element green.
- Integrated patches must be followed by complete CI.
- Machine interfaces never mutate Git; agents use `git-workflow` for authorized branch, commit, push, integration, and cleanup actions.
- Stop an inner loop when the same failure set repeats without a relevant patch or condition change.

## Task 1: Instruction algorithm

Create:

- `skills/ci-debug/SKILL.md`
- `skills/ci-debug/instructions/repair-element.md`
- the parent/source blueprints
- `skills/ci-debug/tests/test_ci_debug_instructions.py`

Tests must prove:

- the gateway contains the full-CI outer loop and delegates one repair element;
- the repair route patches, runs targeted tests, replaces the failure set, then runs the whole element;
- only full CI can establish overall green; and
- Git authority stays outside machine interfaces.

## Task 2: Thin machine interfaces

Create:

- `skills/ci-debug/_rtx/__init__.py`
- `skills/ci-debug/_rtx/_run_ci.py`
- `skills/ci-debug/_rtx/_run_targeted_tests.py`
- the child/source blueprints
- `skills/ci-debug/_rtx/tests/test_runner_interfaces.py`

`run-ci` forwards:

```text
repo_checks.py remote matrix --ref REF --expected-sha SHA --output-dir DIR
```

`run-targeted-tests` forwards:

```text
repo_checks.py remote probe --ref REF --expected-sha SHA --os OS --task TASK \
  (--selector NODE | --from-report PATH | whole element) --output-dir DIR
```

Use an argv subprocess with no shell. Pass runner stdout, stderr, and exit status through unchanged. If the runner or its `remote` interface is unavailable, fail closed; do not call GitHub directly.

## Task 3: Validate and assess

1. Synchronize generated blueprint blocks.
2. Run the focused instruction/runtime tests and blueprint validation.
3. Confirm the live runner currently supports both remote commands. If not, record the independently planned runner upgrade as the only live-execution blocker.
4. After runner availability, prove one selected failure rerun, one whole-element rerun, and one full matrix on the exact integrated SHA.
5. Certify only after the final implementation and runner dependency are reviewable together.

## Acceptance criteria

- The skill is readable as the two nested loops in under a page of authored instructions.
- It exposes only `run-ci` and `run-targeted-tests` as machine interfaces.
- The runtime is a transparent runner adapter and contains no CI policy or orchestration state.
- Independent failing elements can be repaired in parallel.
- Inner loops avoid unrelated CI work; the outer loop still requires complete CI green.
