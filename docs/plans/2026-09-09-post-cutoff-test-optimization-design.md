# Post-cutoff test optimization design

## Objective

Reduce the canonical repository test-suite wall time without weakening unique
behavioral evidence, isolation, failure behavior, or gate ownership. The audit
boundary is every current test or validator module touched after commit
`edbe6264f2871a4aa31eee7d2da25e7a2a91cb28`.

All work occurs on branch `perf/test-optimization-20260909` in the isolated
worktree `<repository>/.worktrees/test-optimization-20260909`, based on
`bf1f23ce68a1f449657e170c61abb887e144343e`. Nothing is merged or pushed by
this project.

## Binding constraints

- Follow `docs/contributors/optimizing-code-tests.md` and query the code-test
  standard with `task.optimizes-test-performance=true` before choosing a
  remedy.
- Treat one test or validator module as one optimization task. Shared direct
  fixtures may change in that task only when the module owns or directly
  consumes them.
- Dispatch one fresh implementer subagent for every qualifying module. Never
  reuse an implementer for another module and never run implementers in
  parallel.
- Give each implementer only its generated task brief, required interface
  context, report path, and applicable prior rulings. Do not fork conversation
  history into implementers.
- Use a separate reviewer subagent after every task. Reviewer agents do not
  count as the module's implementer and may not modify code.
- Preserve collected node identities, parameters, supported contracts, and
  physical-boundary evidence unless the task proves that another enforced
  owner supplies identical or stronger evidence.
- Retain real Git bytes and modes, process transport, sockets, races, signals,
  file descriptors, and browser behavior when those are the observable.
- Run socket- and Chrome-dependent measurements outside the restricted sandbox.
- Do not accept worker-count tuning as an optimization. Remove repeated work.
- Do not commit, merge, or push without explicit user authorization.
- Maintain the durable cross-session audit index at
  `~/Desktop/famulus-test-optimization-ledger.md`. Only the
  controller writes this file, and only after a module's audit and review state
  is settled.

## Materiality gate

The minimum material improvement is 0.5 seconds per module.

1. Use the host-capable full-suite timing artifact to compute the module's
   maximum possible saving. A module below 0.5 aggregate file-seconds is an
   automatic skip and receives no implementer.
2. For every remaining module, a fresh implementer records the guide's compact
   evidence row, runs focused serial and eight-worker measurements, and maps
   the supported contracts and expensive physical setup.
3. Before editing, estimate the removable work after required copying,
   serialization, locking, and isolation. If its upper bound is below 0.5
   seconds, report `SKIPPED_BELOW_CAP` and make no change.
4. A candidate change is retained only when repeated comparable before/after
   measurements show at least 0.5 seconds improvement in median enclosing-runner
   wall time in a mode where the module contributes to the critical path. It
   must also reduce or leave unchanged aggregate item work.
5. If variance crosses the claimed improvement, gather additional paired runs.
   An inconclusive result is a skip, not an accepted optimization.

The previous host-capable observation automatically excludes 48 of the 101
post-cutoff modules. Their combined aggregate work was 5.175 seconds, but no
individual module could improve by 0.5 seconds.

## Initial worktree baseline

The first host-capable worktree run passed all canonical phases at eight
workers:

- performance: 1.15 seconds runner wall;
- shared: 85.08 seconds runner wall, 3,733 passed and 22 skipped;
- browser: 198.78 seconds runner wall, 75 passed;
- total phase wall: 285.01 seconds, with no failures.

An immediately preceding host-capable observation in the source checkout took
229.56 seconds, principally because its browser phase took 139.92 rather than
198.78 seconds. This variance is material. Neither observation alone is an
accepted before/after baseline; task decisions use paired focused measurements,
and the final full-suite claim requires repeated comparable observations.

## Per-module lifecycle

Each task executes this sequence:

1. Freeze the task base commit and generate a single-module brief.
2. Dispatch a fresh implementer with no inherited conversation history.
3. Record the evidence row: contract owner, initial state, action, observable,
   physical setup, serial and eight-worker measurements, host/revision/view,
   proposed remedy, and 0.5-second threshold.
4. Screen the removable-work cap. Stop without edits when it cannot clear the
   threshold.
5. Query only the relevant code-test standard contexts and remedies.
6. Preserve an exact pre-change collection and parameter/contract map.
7. For production-helper changes, write and observe a failing regression test
   before implementation. For test-only refactors, use the unchanged production
   behavior and the pre-change collection/contract map as the red-side oracle.
8. Apply the smallest remedy in the guide's decision order.
9. Run focused serial and eight-worker verification, compare collection and
   contracts, and collect paired timing evidence.
10. Revert the task's edits if correctness, isolation, or materiality fails.
11. If retained, obtain task-scoped spec and quality review before advancing.

No next module starts while the current module has an unresolved load-bearing
review finding.

## Durable audit ledger

Two ledgers serve different purposes:

- The plan-owned SDD ledger inside the worktree stores detailed current-run
  briefs, reports, review packages, fix rounds, commits, and controller rulings.
- `~/Desktop/famulus-test-optimization-ledger.md` is the concise,
  durable cross-session index. It remains available after the worktree or SDD
  scratch workspace is removed.

The Desktop ledger begins with the audit cutoff, source revision, current
optimization branch, benchmark command, host capabilities, materiality
threshold, and links or paths to retained evidence. It then contains one row
for every one of the 101 current post-cutoff modules with these fields:

| Field | Meaning |
| --- | --- |
| Module | Exact repository-relative path. |
| Initial file seconds | Aggregate file time from the first valid host run. |
| Status | `PENDING`, `AUTO_SKIPPED_BELOW_CAP`, `AUDITED_BELOW_CAP`, `REJECTED_NO_MATERIAL_GAIN`, `RETAINED_AND_REVIEWED`, or `BLOCKED`. |
| Contract owner | Test or enforced gate that owns the retained evidence. |
| Physical setup | Repository, graph, schema, scan, copy, subprocess, browser, or other measured boundary. |
| Focused measurements | Serial and eight-worker baseline/candidate walls, or browser-serial walls. |
| Remedy | Applied standard remedy, or the reason no remedy was attempted. |
| Improvement | Accepted median wall reduction, or measured rejection result. |
| Revision | Retained task commit when commits are authorized; otherwise the exact diff/base reference. |
| Review | Reviewer result and review artifact path. |
| Audited | Date and host capability context. |
| Resume note | Exact next action when status is `PENDING` or `BLOCKED`. |

The controller writes a row only after reconciling the implementer report,
measurements, repository state, and reviewer verdict. Subagents write their
reports inside the plan-owned worktree workspace and never edit the Desktop
ledger directly.

At the start of a later optimization run, the controller verifies the recorded
cutoff and revisions against Git, remeasures drift-prone timings, skips settled
rows whose owning files have not changed, and resumes from the highest-cost
`PENDING` or newly changed module. A previously settled module returns to
`PENDING` only when its test, direct fixture/helper, owned production boundary,
or enforced selection policy has changed.

## Campaign waves and task order

The current campaign is intentionally bounded so it does not take days:

- Wave 1 contains tasks 1-14 (each initially at least 10 file-seconds). They
  account for 402.263 of 502.898 aggregate touched-module seconds, or 80.0%.
- After Wave 1, run a host-capable checkpoint and compare retained savings with
  the remaining opportunity.
- Wave 2 contains tasks 15-19 (each initially at least 5 file-seconds) and is
  optional. Completing it would cover 438.667 aggregate seconds, or 87.2%.
- Tasks 20-53 stay `PENDING` in the Desktop ledger for a later campaign unless
  the Wave 1 checkpoint shows unusually high expected value.
- Stop the current campaign after three consecutive audited modules produce no
  retained material improvement, or when the remaining collective critical-path
  opportunity is below 0.5 seconds.

Tasks are ordered by the first host-capable full-suite aggregate file time.
The order is a scheduling priority, not a performance claim; every task must
remeasure its own stable baseline.

1. `skills/skill-maker/_rtx/tests/test_blueprint_tools.py`
2. `tests/test_dispatcher_route_smoke.py`
3. `tests/test_famulus_mcp.py`
4. `tests/test_visualization_projection_arrangements_browser.py`
5. `tests/test_benchmark_html_renderer.py`
6. `tests/test_visualization_inspector_and_bezier_browser.py`
7. `tests/test_bootstrap_dispatcher_runtime_skill.py`
8. `tests/test_skill_certifier_instruction_interfaces.py`
9. `tests/test_visualization_browser.py`
10. `tests/test_blueprint_visualization_browser.py`
11. `tests/test_setup_interface_manager_coverage.py`
12. `tests/test_visualization_quick_guide_browser.py`
13. `tests/test_unified_pytest_collection.py`
14. `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py`
15. `tests/test_docstrings_validator.py`
16. `tests/test_node_certification_hashing.py`
17. `tests/test_officina_python_machine_interface.py`
18. `tests/test_visualization_node_readability_browser.py`
19. `skills/distill-to-rutters/tests/test_artifact_contract.py`
20. `tests/test_visualization_projection_browser.py`
21. `skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py`
22. `tests/test_dispatcher_direct_setup.py`
23. `tests/test_standard_extractor.py`
24. `skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py`
25. `tests/test_visualization_containment_edges_browser.py`
26. `skills/connect-google/_rtx/tests/test_selected_credential_runtime_interfaces.py`
27. `skills/email-client/_rtx/tests/test_accounts.py`
28. `tests/test_mcp_setup_preflight.py`
29. `tests/test_visualization_bootstrap.py`
30. `tests/test_officina_setup_requirements.py`
31. `tests/test_repo_checks_remote.py`
32. `tests/validate_skill_md_dispatch.py`
33. `tests/test_repository_test_checks.py`
34. `skills/cloud-files/_rtx/tests/test_drive_readiness_runtime_interfaces.py`
35. `tests/validate_documentation_validators.py`
36. `tests/test_docs_site.py`
37. `skills/distill-to-rutters/tests/test_runtime_compatibility.py`
38. `tests/test_officina_git_provenance.py`
39. `skills/connect-google/_rtx/tests/test_service_delegation.py`
40. `tests/test_dispatcher_direct_authorization.py`
41. `skills/distill-to-rutters/tests/test_distillation_scenarios.py`
42. `tests/test_sync_release_version.py`
43. `tests/test_dispatcher_cli.py`
44. `tests/validate_cross_platform.py`
45. `tests/test_repo_checks_entrypoint.py`
46. `skills/math-dependency-graph/_rtx/tests/test_extraction_finalizer.py`
47. `tests/test_node_standards.py`
48. `skills/cloud-files/_rtx/tests/test_drive_credential_file_binding.py`
49. `hooks/tests/test_inject_dispatcher_context.py`
50. `skills/math-dependency-graph/_rtx/tests/test_graph_builder.py`
51. `skills/distill-to-rutters/tests/test_distill_to_rutters_routing.py`
52. `skills/ci-debug/_rtx/tests/test_runner_interfaces.py`
53. `tests/test_setup_interface_manager_integration.py`

## Verification and checkpoints

- Per task: exact module, direct fixtures/helpers, focused serial run, focused
  eight-worker run, collection and contract parity, paired performance result,
  `git diff --check`, and independent task review.
- After each coherent five-to-ten-task wave: host-capable canonical relevant
  phase benchmark, with unchanged modules checked for systemic slowdown.
- At project end: host-capable `full` suite at eight workers, repeated if
  variance is material, compared with the frozen worktree baseline.
- Record skipped modules, rejected experiments, retained changes, benchmark
  artifacts, commits if authorized, reviewer findings, and controller rulings
  in the plan-owned SDD ledger.

## Completion criterion

The current campaign is complete when all Wave 1 tasks reached a reviewed
terminal result, the host-capable checkpoint is green, its timing claim is
supported by comparable artifacts, and the controller has recorded a ruling on
whether Wave 2 is worth executing. The remaining candidates stay explicit in
the Desktop ledger rather than silently becoming complete. Integration into
`master` remains a separate user decision.
