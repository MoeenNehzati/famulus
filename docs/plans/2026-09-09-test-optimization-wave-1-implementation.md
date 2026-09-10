# Test Optimization Wave 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit and materially optimize the 14 highest-cost post-cutoff test modules without weakening evidence or accepting less than 0.5 seconds of enclosing-runner improvement per module.

**Architecture:** One fresh implementer owns one module and may change only that module plus direct fixtures/helpers or the production boundary it demonstrably owns. The controller freezes each task base, reconciles timing and contract evidence, dispatches an independent reviewer, and updates both the plan workspace ledger and the durable Desktop index.

**Tech Stack:** Python, pytest, pytest-xdist, repository `repo_checks.py`, host-capable Chrome/browser execution where selected.

**Spec:** `docs/plans/2026-09-09-post-cutoff-test-optimization-design.md`

## Global Constraints

- Follow `docs/contributors/optimizing-code-tests.md` and query the code-test standard with `task.optimizes-test-performance=true` before selecting a remedy.
- The minimum accepted improvement is 0.5 seconds in median enclosing-runner wall time in a mode where the module contributes to the critical path.
- Preserve collection identities, parameters, unique behavioral evidence, isolation, failure behavior, and enforced gate ownership.
- Preserve physical Git bytes/modes, process transport, sockets, races, signals, file descriptors, and browser behavior when observed.
- Use eight workers for target-worker measurements; browser selectors use the canonical browser task and host capabilities.
- Do not retain an edit when variance obscures the claimed saving or unchanged files slow systemically.
- The implementer must not commit, merge, push, or edit the Desktop ledger. The controller preserves accepted diffs and records exact base references.
- Every implementer and reviewer must invoke `famulus:milestone-logging` with `--role` before substantive work.

## Common task protocol

Every task below performs these exact actions in order:

- [ ] Record `git rev-parse HEAD`, `git status --short`, the exact selector, host capabilities, and competing test processes in the task report.
- [ ] Write the guide's compact evidence row and capture pre-change collection identities plus parameter/contract ownership.
- [ ] Run the selector through `./repo_checks.py` with `--repository-view working`, first in its serial canonical task and then at the target worker count where supported, writing timing JSON under the task workspace.
- [ ] Estimate removable work after isolation/copy/locking costs. If the upper bound is below 0.5 seconds, make no edits and report `AUDITED_BELOW_CAP`.
- [ ] Query only the relevant standard contexts and remedies, then apply the first sound remedy in the guide's decision order.
- [ ] For a production-helper change, add and observe a failing regression test before implementation. For a test-only refactor, use pre-change collection/contract parity as the red-side oracle.
- [ ] Re-run exact collection, focused serial verification, focused target-worker verification, direct fixture/helper consumers, and paired timing. Repeat pairs when variance is material.
- [ ] Revert only this task's edits when correctness or materiality fails; otherwise leave the accepted diff in the worktree for controller review.
- [ ] Run `git diff --check` and write a report containing status, paths changed, raw artifact paths, before/after medians, aggregate work, contract-parity evidence, risks, and an exact proposed ledger row.

### Task 1: Skill-maker blueprint tools

**Files:** Audit and potentially modify `skills/skill-maker/_rtx/tests/test_blueprint_tools.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes the common task protocol. Produces a reviewed terminal audit result for initial aggregate file time 60.538 seconds.

**Selector:** `skills/skill-maker/_rtx/tests/test_blueprint_tools.py`

**Canonical task:** `tests:shared`

### Task 2: Dispatcher route smoke

**Files:** Audit and potentially modify `tests/test_dispatcher_route_smoke.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes Task 1's retained worktree state but no implementation context. Produces a reviewed terminal audit result for initial aggregate file time 50.170 seconds.

**Selector:** `tests/test_dispatcher_route_smoke.py`

**Canonical task:** `tests:shared`

### Task 3: Famulus MCP

**Files:** Audit and potentially modify `tests/test_famulus_mcp.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes prior retained worktree state only. Produces a reviewed terminal audit result for initial aggregate file time 37.807 seconds.

**Selector:** `tests/test_famulus_mcp.py`

**Canonical task:** `tests:shared`

### Task 4: Visualization projection arrangements browser

**Files:** Audit and potentially modify `tests/test_visualization_projection_arrangements_browser.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes prior retained worktree state only. Produces a reviewed terminal audit result for initial aggregate file time 36.998 seconds.

**Selector:** `tests/test_visualization_projection_arrangements_browser.py`

**Canonical task:** `tests:browser` on a host-capable run.

### Task 5: HTML renderer benchmark

**Files:** Audit and potentially modify `tests/test_benchmark_html_renderer.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes prior retained worktree state only. Produces a reviewed terminal audit result for initial aggregate file time 33.360 seconds.

**Selector:** `tests/test_benchmark_html_renderer.py`

**Canonical task:** `tests:performance`

### Task 6: Visualization inspector and Bezier browser

**Files:** Audit and potentially modify `tests/test_visualization_inspector_and_bezier_browser.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes prior retained worktree state only. Produces a reviewed terminal audit result for initial aggregate file time 33.140 seconds.

**Selector:** `tests/test_visualization_inspector_and_bezier_browser.py`

**Canonical task:** `tests:browser` on a host-capable run.

### Task 7: Bootstrap dispatcher runtime skill

**Files:** Audit and potentially modify `tests/test_bootstrap_dispatcher_runtime_skill.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes prior retained worktree state only. Produces a reviewed terminal audit result for initial aggregate file time 30.109 seconds.

**Selector:** `tests/test_bootstrap_dispatcher_runtime_skill.py`

**Canonical task:** `tests:shared`

### Task 8: Skill certifier instruction interfaces

**Files:** Audit and potentially modify `tests/test_skill_certifier_instruction_interfaces.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes prior retained worktree state only. Produces a reviewed terminal audit result for initial aggregate file time 27.213 seconds.

**Selector:** `tests/test_skill_certifier_instruction_interfaces.py`

**Canonical task:** `tests:shared`

### Task 9: Visualization browser

**Files:** Audit and potentially modify `tests/test_visualization_browser.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes prior retained worktree state only. Produces a reviewed terminal audit result for initial aggregate file time 21.894 seconds.

**Selector:** `tests/test_visualization_browser.py`

**Canonical task:** `tests:browser` on a host-capable run.

### Task 10: Blueprint visualization browser

**Files:** Audit and potentially modify `tests/test_blueprint_visualization_browser.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes prior retained worktree state only. Produces a reviewed terminal audit result for initial aggregate file time 19.659 seconds.

**Selector:** `tests/test_blueprint_visualization_browser.py`

**Canonical task:** `tests:browser` on a host-capable run.

### Task 11: Setup-interface-manager coverage

**Files:** Audit and potentially modify `tests/test_setup_interface_manager_coverage.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes prior retained worktree state only. Produces a reviewed terminal audit result for initial aggregate file time 16.119 seconds.

**Selector:** `tests/test_setup_interface_manager_coverage.py`

**Canonical task:** `tests:shared`

### Task 12: Visualization Quick Guide browser

**Files:** Audit and potentially modify `tests/test_visualization_quick_guide_browser.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes prior retained worktree state only. Produces a reviewed terminal audit result for initial aggregate file time 13.208 seconds.

**Selector:** `tests/test_visualization_quick_guide_browser.py`

**Canonical task:** `tests:browser` on a host-capable run.

### Task 13: Unified pytest collection

**Files:** Audit and potentially modify `tests/test_unified_pytest_collection.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes prior retained worktree state only. Produces a reviewed terminal audit result for initial aggregate file time 11.276 seconds.

**Selector:** `tests/test_unified_pytest_collection.py`

**Canonical task:** `tests:shared`

### Task 14: Setup manager runtime tests

**Files:** Audit and potentially modify `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py` and only its direct owned fixtures/helpers or production boundary.

**Interfaces:** Consumes prior retained worktree state only. Produces a reviewed terminal audit result for initial aggregate file time 10.772 seconds.

**Selector:** `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py`

**Canonical task:** `tests:shared`

## Wave checkpoint

- [ ] Reconcile all 14 task reports and reviews into the Desktop ledger.
- [ ] Run the host-capable canonical full suite with `./repo_checks.py --suite full --jobs 8 --repository-view working --timing-output <workspace-artifact-path>`.
- [ ] Repeat comparable runs when browser or unchanged-file variance is material.
- [ ] Compare retained critical-path and aggregate savings with the 36.404 seconds of aggregate opportunity in optional Wave 2 tasks 15-19.
- [ ] Record a controller ruling to execute or defer Wave 2; do not silently expand the campaign.
