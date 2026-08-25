# Canonical Release Version Synchronization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep one hand-edited Famulus release version in `pyproject.toml` and synchronize every required committed copy automatically.

**Architecture:** `[project].version` is the sole canonical entry. A standard-library synchronizer updates only the required top-level `version` fields in both plugin manifests, and the existing pre-commit hook runs and stages those generated mirrors before repository checks.

**Tech Stack:** Python 3.11 standard library (`tomllib`, `json`), Git index, existing pre-commit hook and repository test runner.

**Spec:** `docs/plans/unified-release-mechanism.md`

**Scope:** This is the version-synchronization subplan only. Per the single-maintainer decision, it adds no independent version validator; changelog, tag, release documentation, payload, and public-install gates remain separate release work.

## Implementation

- [x] Add `scripts/sync-release-version.py`; resolve the active Git index, read staged `pyproject.toml`, and require canonical core SemVer `MAJOR.MINOR.PATCH` without leading zeros.
- [x] Parse both staged and working copies of `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`; reject missing, malformed, duplicate, deleted, or unmerged version inputs before mutation.
- [x] Generate separate staged and working outputs that change only each top-level `version`, preserving non-version edits in their original staged or unstaged view and preserving byte-identical files.
- [x] Publish working files with atomic replacement and both staged blobs in one index update; roll back changed working files if publication fails.
- [x] Invoke the synchronizer from `.githooks/pre-commit` immediately before `gitleaks protect --staged`, so every synchronized byte is secret-scanned.
- [x] Replace the README sentence containing `0.1.0` with version-neutral wording; rely on the published GitHub Release for the landing-page version.
- [x] Derive the fake wheel version in `test_support/uv_subprocess.py` from the source repository's canonical TOML value instead of hard-coding `0.1.0`.
- [x] Add `tests/test_sync_release_version.py` for success, idempotence, syntax/input failures, staged-versus-working divergence, non-version byte preservation, and injected rollback failures.
- [x] Extend `tests/test_repository_test_checks.py` with real-hook commits; reject `git commit --only` safely when synchronization is required because Git otherwise restores a stale caller index.
- [x] Run the focused shared checks and the working-tree precommit suite; rerun the suite outside the socket-restricted sandbox to verify OAuth loopback tests.
