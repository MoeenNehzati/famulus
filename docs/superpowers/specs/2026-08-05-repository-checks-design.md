# Repository Checks Design

`repo_checks.py` is the repository's only public verification entry point. It
selects ordinary pytest tests, conformance validators, and the combined
precommit and pre-push gates without delegating to legacy executable runners.

Reusable implementation lives in a non-executable `officina.repository_checks`
module. Validator checks retain the captured staged-index mirror; ordinary tests
retain working-tree discovery and the existing nested-runtime process isolation.

The migration deletes `validators/runner.py`, `scripts/run-python-tests.py`, and
`repo_tests.py`. CI, pre-commit, pre-push, and skill-specific hooks invoke only
`repo_checks.py`. Tests assert suite membership, staged behavior, CLI behavior,
legacy-file absence, and exact hook bindings.
