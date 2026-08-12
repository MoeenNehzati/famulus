# Repository Checks: Targeted Local and Remote CI Plan

**Goal:** Make the existing repository runner usable for efficient CI debugging
without replacing it or building a second orchestration system.

## Boundaries

- Preserve every existing local suite and invocation.
- Add selection and remote transport around the existing runner.
- Use exact pushed SHAs for remote work.
- Let GitHub Actions own hosted execution and artifacts.
- Let `repo_checks.py` own task validation and test invocation.
- Emit enough structured evidence to identify each failing matrix element and
  replay its failed test files.
- Do not add incident state, a failure ledger, a new test inventory, a remote
  shell, or a general workflow engine.

## Task 1: Refined local selection

Extend the existing CLI with:

```text
repo_checks.py --suite SUITE --task TASK [--selector NODE ...]
```

- Keep `--task-id` as a compatibility alias.
- Validate selectors as repository-relative pytest files or node IDs.
- Reject selectors outside the selected task.
- Keep validator selection on the existing `--validator` interface.
- Preserve existing suite behavior when no task or selector is supplied.
- Continue using the existing worker and repository-view policies.

Write failing CLI and command-construction tests first, then run the focused
repository-check tests and the unchanged precommit suite.

## Task 2: Dispatchable workflow

Add `workflow_dispatch` inputs for `mode`, `request_id`, `expected_sha`, `os`,
`task`, `selector`, `jobs`, and `profile`.

- Preserve current push and pull-request behavior.
- Matrix mode runs the existing complete matrix with `fail-fast: false`.
- Probe mode runs one validated OS/task and optional selector.
- Verify the checked-out commit equals `expected_sha` before repository code
  executes.
- Use `contents: read` and checkout with `persist-credentials: false`.
- Put user-controlled values in environment variables, not interpolated shell
  fragments.
- Always upload the runner timing report when it exists.

Write the workflow contract tests before editing the workflow.

## Task 3: Thin remote transport

Add:

```text
repo_checks.py remote matrix --ref REF --expected-sha SHA --output-dir DIR
repo_checks.py remote probe --ref REF --expected-sha SHA --os OS --task TASK \
  (--selector NODE | --from-report REPORT | --whole-element) --output-dir DIR
```

- Use `gh` argv calls without a shell.
- Preflight authentication and repository identity without printing raw remotes
  or credentials.
- Dispatch the workflow with a unique request ID and correlate by request ID,
  workflow, event, ref, and exact head SHA.
- Poll structured run state, download artifacts, and write `run-report.json`.
- Report every matrix job, its conclusion and URL, and failed test-file selectors
  obtained from the existing timing artifacts.
- `--from-report` replays the selected element's latest failed selectors.
- Exit `0` for green, `1` for a completed red run, and `2` for invalid input,
  authentication, correlation, or integrity failures.
- Machine JSON goes to stdout; progress and safe diagnostics go to stderr.

Mock every `gh` interaction in tests. No unit test contacts GitHub.

## Task 4: Verify the debugging loop

1. Run focused local, workflow, and remote-transport tests.
2. Run the existing precommit gate and record environmental failures separately.
3. Push an exact candidate only after explicit Git authorization.
4. Run one targeted probe, then its whole element, then the complete matrix.
5. Confirm the CI-debug skill can consume both reports and that only the full
   matrix can establish overall green.

## Acceptance

- Existing local commands remain compatible.
- A local invocation can select one task and one or more pytest selectors.
- A remote probe runs one OS/task without unrelated matrix jobs.
- A remote matrix reports every existing required job even when several fail.
- Reports contain enough selectors to drive the repair loop without copying
  console logs manually.
- Exact SHA checks, safe subprocess usage, and credential-safe diagnostics pass.
- No duplicated CI policy or persistent orchestration state is introduced.
