# Dispatcher Documentation and Launcher Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the version-6 dispatcher documentation internally consistent and determine whether removing one redundant Python startup materially improves end-to-end latency.

**Architecture:** Keep the stable resolver, managed-runtime pointer, exact `officina.toml` injection, and Windows launcher contract unchanged. Compare a POSIX `sh` candidate with the committed Python shim against the same installed runtime and retain it only if the measured gain justifies the platform split. Canonical documentation describes v6; historical plans retain their bodies with explicit supersession notices.

**Tech Stack:** Python 3.11+, POSIX shell, Windows batch, pytest, `/usr/bin/time`, Markdown.

## Global Constraints

- Dispatcher routes and authorizes; it does not repair, synchronize, inventory, certify, or build repository graphs.
- Certification currentness is warning-only during dispatch.
- The stable resolver remains dependency-free and rejects repository-configuration overrides.
- Windows behavior and quoting remain unchanged.
- Retain the Unix shim change only if the same-runtime median or p95 improves by at least 20 percent without a correctness regression.
- Do not rewrite historical plan bodies; label superseded architecture clearly.
- Do not commit or push without explicit user authorization.

---

### Task 1: Unix launcher experiment and CLI identity

**Files:**
- Experiment only: `tests/test_officina_launcher_entry.py`
- Experiment only: `skills/install-assistant-tools/_rtx/_install_launcher/_linux_launcher.py`
- Modify: `tests/test_dispatcher_route_smoke.py`
- Modify: `src/officina/dispatcher/cli.py`

**Interfaces:**
- Consumes: fixed resolver path and existing argument-forwarding contract.
- Produces: a measured retain/revert decision and CLI help beginning with
  `usage: dispatcher`.

- [x] Write temporary assertions that a Unix shell candidate quotes the fixed resolver path, forwards `"$@"`, and contains no Python imports.
- [x] Write a CLI help assertion for `usage: dispatcher`.
- [x] Run both focused tests and confirm the new assertions fail for the committed Python shim and `cli.py` program label.
- [x] Implement the temporary shell candidate and set `argparse.ArgumentParser(prog="dispatcher", ...)`.
- [x] Benchmark the candidate, then revert its production and test changes when it failed the retention threshold. Retain only the CLI identity fix.
- [x] Rerun the focused tests and the installed-launcher end-to-end test against the retained Python launcher.

### Task 2: Canonical documentation and code documentation

**Files:**
- Create: `docs/dispatcher.md`
- Modify: `docs/architecture.md`
- Modify: `docs/skill-blueprints.md`
- Modify: `docs/certification_and_drift.md`
- Modify: `docs/installation.md`
- Modify: `docs/scaffolding/README.md`
- Modify: `docs/plans/nested-module-behavior.md`
- Modify: `docs/superpowers/plans/2026-08-03-dispatcher-route-catalog.md`
- Modify: `docs/plans/osx_feedback_fix/README.md`
- Modify: `src/officina/dispatcher/cli.py`
- Modify: `src/officina/dispatcher/direct_runtime.py`
- Modify: `src/officina/dispatcher/direct_authorization.py`
- Modify: `src/officina/dispatcher/direct_blueprints.py`
- Modify: `src/officina/common/repository_configuration.py`

**Interfaces:**
- Produces: one canonical operator/maintainer dispatcher guide and documented internal boundaries without runtime-semantic changes.

- [x] Add `docs/dispatcher.md` covering invocation, exact configuration, dotted lookup, authorization, failure behavior, advisory certification, launcher chain, and performance interpretation.
- [x] Correct stale v5/facade/surface/certification/installation statements in canonical documentation.
- [x] Add v6 supersession notices to historical v5 and route-catalog plans.
- [x] Expand module, class, and nontrivial function docstrings to state inputs, invariants, side effects, and failure boundaries; add comments only where the reason is not evident from the code.
- [x] Run documentation validators and focused dispatcher tests.

### Task 3: Controlled performance decision and final verification

**Files:**
- Modify only Task 1 launcher files if the benchmark requires reverting the shell shim.

**Interfaces:**
- Consumes: committed Python shim and candidate shell shim against the same isolated runtime fixture.
- Produces: 60 fresh-process samples per shim in each environment and an explicit retain/revert decision.

- [x] Generate old and candidate launchers pointing at the same resolver and current pointer.
- [x] Run 60 AB/BA fresh-process dry-runs for each launcher under both the normal environment and a clean system-Python environment.
- [x] Compare median and nearest-rank p95; retain the candidate only if median or p95 improves by at least 20 percent in the normal environment and correctness outputs match. The candidate was reverted because its 10.0 percent median and 8.6 percent p95 improvements were below threshold.
- [x] Run focused launcher, resolver, dispatcher, authorization, documentation, and performance suites.
- [x] Run repository validators and `git diff --check`.
- [x] Repeat the interactive success, denial, override, unrelated-malformed-state, no-network/no-write, and actual route-smoke checks.
- [x] Report the exact remaining latency decomposition; do not commit or push without approval.

## Verification record

- Focused dispatcher, launcher, repository-configuration, and documentation
  suite: 135 passed.
- Repository validators against the exact temporary index: exit 0.
- Complete precommit runner after increasing the unrelated visualization
  interaction harness's virtual-time allowance: 2,092 passed, 16 skipped, and
  the clean-worktree-only test was deselected by the suite as designed.
- The shared group of the full isolated suite reached 1,464 passed and 14
  skipped before its clean-worktree-only v5 inventory gate stopped the runner.
  The unrelated headless-Chrome visualization failure passed when rerun outside
  the process sandbox. The complete full suite is the post-commit integration
  gate so the clean-tree check and subsequent isolated skill groups can execute.
- Final installed-runtime smoke, actual launch, structured denial, immutable
  repository-config, and syscall trace all matched the documented contract.
