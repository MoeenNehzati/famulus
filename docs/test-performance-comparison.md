# Test and Validator Performance Comparison

> **Historical benchmark:** These measurements compare the per-task runner at
> commit `b931bbe`. The current combined validator/test pool is documented in
> `TESTING.md` and the final sections of `docs/test-performance-audit.md`.

- Baseline tree: `/tmp/ai-pytest-baseline-e4c1c86`
- Current tree: `<repo>`
- Runner commit in both trees: `b931bbe`
- Suite policy: `full`; worker budget: `8`
- Repetitions: 3 paired observations per canonical task, alternating tree order
- Cache policy: fresh pytest cache per task observation; persistent tree-local bytecode/cache roots
- File time: pytest JUnit setup/call/teardown duration summed by source file
- Relative improvement: `(baseline - current) / baseline`; positive is faster
- Task wall time separately includes process startup, collection, and unattributed scheduler overhead
- Values below 0.010s are especially sensitive to the JUnit millisecond resolution

## Summary

- Coverage: 19 canonical tasks, 181 collected test files, and 27 collected validator files.
- Sum of canonical task medians, using the controlled validator rerun: 192.385s baseline versus 185.559s current, a 6.826s or 3.5% improvement.
- Excluding the validator task: 171.020s baseline versus 161.486s current, a 9.534s or 5.6% improvement.
- Largest passing task improvement: `tests:skills/list-manager/_rtx/tests`, 15.915s to 6.882s (56.8%).
- Largest passing test-file savings: `skills/list-manager/_rtx/tests/test_lists.py`, 11.760s to 3.370s (8.390s saved), and `tests/test_node_standards.py`, 6.938s to 0.468s (6.470s saved).
- Largest passing test-file regression: `tests/test_visualization_inspector_and_bezier_browser.py`, 13.558s to 32.899s (19.341s slower). The unchanged browser-heavy file is sensitive to machine load and needs a focused rerun before attributing the change to the refactor.
- Test-file status values containing `failures A/B` report baseline/current failure totals across the three observations.

### Controlled validator methodology

The original unmatched validator observations were discarded. The replacement benchmark staged the same 21 changed Python paths as additions in both isolated snapshots. Every other tracked byte and the runner commit were identical; only those paths' corresponding old versus refactored contents differed. Each snapshot received three alternating runs with fresh pytest, Python bytecode, XDG, and UV caches.

- Validator task wall median: 21.365s baseline versus 24.073s current, 2.708s or 12.7% slower.
- Sum of the 27 per-validator medians: 18.766s baseline versus 21.455s current, 2.689s or 14.3% slower.
- Both snapshots returned findings in all three runs. The baseline old files produced 22 docstring findings; the current files produced 13. JUnit timing artifacts were complete in all six observations.
- `validators/docstrings.py` accounts for 2.533s of the 2.689s summed increase. Its workload has the same paths but different source contents, which is the intended old/new comparison.

## Run diagnostics

- Task observations: 114 (108 original test observations plus 6 controlled validator observations)
- Nonzero observations: 16; no timing artifacts were missing or malformed

| Variant | Run | Task | Exit | Timing issue | Log |
|---|---:|---|---:|---|---|
| baseline | 1 | `tests:shared` | 1 | pytest failures recorded | `/tmp/pytest-old-new-comparison/01-baseline-tests-shared/run.log` |
| current | 1 | `tests:shared` | 1 | pytest failures recorded | `/tmp/pytest-old-new-comparison/01-current-tests-shared/run.log` |
| current | 2 | `tests:shared` | 1 | pytest failures recorded | `/tmp/pytest-old-new-comparison/02-current-tests-shared/run.log` |
| current | 3 | `tests:shared` | 1 | pytest failures recorded | `/tmp/pytest-old-new-comparison/03-current-tests-shared/run.log` |
| baseline | 1 | `tests:skills/install-assistant-tools/_rtx/tests` | 1 | pytest failures recorded | `/tmp/pytest-old-new-comparison/01-baseline-tests-skills-install-assistant-tools-_rtx-tests/run.log` |
| current | 1 | `tests:skills/install-assistant-tools/_rtx/tests` | 1 | pytest failures recorded | `/tmp/pytest-old-new-comparison/01-current-tests-skills-install-assistant-tools-_rtx-tests/run.log` |
| current | 2 | `tests:skills/install-assistant-tools/_rtx/tests` | 1 | pytest failures recorded | `/tmp/pytest-old-new-comparison/02-current-tests-skills-install-assistant-tools-_rtx-tests/run.log` |
| baseline | 2 | `tests:skills/install-assistant-tools/_rtx/tests` | 1 | pytest failures recorded | `/tmp/pytest-old-new-comparison/02-baseline-tests-skills-install-assistant-tools-_rtx-tests/run.log` |
| baseline | 3 | `tests:skills/install-assistant-tools/_rtx/tests` | 1 | pytest failures recorded | `/tmp/pytest-old-new-comparison/03-baseline-tests-skills-install-assistant-tools-_rtx-tests/run.log` |
| current | 3 | `tests:skills/install-assistant-tools/_rtx/tests` | 1 | pytest failures recorded | `/tmp/pytest-old-new-comparison/03-current-tests-skills-install-assistant-tools-_rtx-tests/run.log` |
| baseline | 1 | `validators` | 1 | 22 docstring findings recorded | `/tmp/validator-controlled-b931bbe-20260810-v3/artifacts/run-1-baseline/run.log` |
| current | 1 | `validators` | 1 | 13 docstring findings recorded | `/tmp/validator-controlled-b931bbe-20260810-v3/artifacts/run-1-current/run.log` |
| current | 2 | `validators` | 1 | 13 docstring findings recorded | `/tmp/validator-controlled-b931bbe-20260810-v3/artifacts/run-2-current/run.log` |
| baseline | 2 | `validators` | 1 | 22 docstring findings recorded | `/tmp/validator-controlled-b931bbe-20260810-v3/artifacts/run-2-baseline/run.log` |
| baseline | 3 | `validators` | 1 | 22 docstring findings recorded | `/tmp/validator-controlled-b931bbe-20260810-v3/artifacts/run-3-baseline/run.log` |
| current | 3 | `validators` | 1 | 13 docstring findings recorded | `/tmp/validator-controlled-b931bbe-20260810-v3/artifacts/run-3-current/run.log` |

## Canonical task wall time

| Task | Baseline median (s) | Current median (s) | Relative improvement | Baseline exits | Current exits |
|---|---:|---:|---:|---|---|
| `tests:skills/list-manager/_rtx/tests` | 15.915 | 6.882 | +56.8% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/connect-google/_rtx/tests` | 1.213 | 0.742 | +38.8% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/email-client/_rtx/tests` | 3.862 | 2.754 | +28.7% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/daily-plan/_rtx/tests` | 0.387 | 0.323 | +16.4% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/recurring-tasks/_rtx/tests` | 2.933 | 2.580 | +12.1% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/email-triage/_rtx/tests` | 1.223 | 1.085 | +11.2% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/initialize-tdd/_rtx/tests` | 0.331 | 0.295 | +10.9% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/math-dependency-graph/_rtx/tests` | 0.945 | 0.855 | +9.5% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/find-handoff-candidates/_rtx/tests` | 0.231 | 0.215 | +7.0% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/cloud-files/_rtx/tests` | 1.197 | 1.147 | +4.2% | [0, 0, 0] | [0, 0, 0] |
| `tests:performance` | 1.563 | 1.530 | +2.1% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/g-calendar/_rtx/tests` | 0.364 | 0.356 | +2.0% | [0, 0, 0] | [0, 0, 0] |
| `tests:shared` | 72.059 | 71.609 | +0.6% | [1, 0, 0] | [1, 1, 1] |
| `tests:skills/skill-drift/_rtx/tests` | 1.895 | 1.903 | -0.4% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/skill-certifier/_rtx/tests` | 24.646 | 24.980 | -1.4% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/pdf-to-markdown/_rtx/tests` | 0.289 | 0.297 | -3.1% | [0, 0, 0] | [0, 0, 0] |
| `tests:skills/install-assistant-tools/_rtx/tests` | 40.773 | 42.653 | -4.6% | [1, 1, 1] | [1, 1, 1] |
| `tests:skills/skill-maker/_rtx/tests` | 1.196 | 1.279 | -7.0% | [0, 0, 0] | [0, 0, 0] |
| `validators` | 21.365 | 24.073 | -12.7% | [1, 1, 1] | [1, 1, 1] |

## Test files

| File | Baseline median (s) | Current median (s) | Delta saved (s) | Relative improvement | Items old/new | Status |
|---|---:|---:|---:|---:|---:|---|
| `skills/email-client/_rtx/tests/test_smoke.py` | 0.001 | 0.000 | 0.001 | +100.0% | 3/3 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_install_manifest.py` | 0.686 | 0.031 | 0.655 | +95.5% | 12/12 | pass |
| `tests/test_node_standards.py` | 6.938 | 0.468 | 6.470 | +93.3% | 17/17 | pass |
| `tests/test_officina_blueprint_authorization.py` | 5.512 | 0.679 | 4.833 | +87.7% | 19/19 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_uninstall.py` | 2.140 | 0.368 | 1.772 | +82.8% | 13/13 | pass |
| `skills/list-manager/_rtx/tests/test_lists.py` | 11.760 | 3.370 | 8.390 | +71.3% | 69/70 | pass |
| `tests/test_blueprint_catalog_schema.py` | 0.700 | 0.210 | 0.490 | +70.0% | 13/13 | pass |
| `tests/test_typed_blueprint_schemas.py` | 1.267 | 0.419 | 0.848 | +66.9% | 40/40 | pass |
| `tests/test_visualization_projection_browser.py` | 7.888 | 2.609 | 5.279 | +66.9% | 1/1 | failures 1/1 |
| `skills/recurring-tasks/_rtx/tests/test_enable_disable.py` | 0.558 | 0.202 | 0.356 | +63.8% | 5/6 | pass |
| `skills/email-client/_rtx/tests/test_mail.py` | 0.066 | 0.025 | 0.041 | +62.1% | 41/41 | pass |
| `skills/connect-google/_rtx/tests/test_service_delegation.py` | 0.714 | 0.274 | 0.440 | +61.6% | 9/9 | pass |
| `tests/test_interface_projection.py` | 10.637 | 5.125 | 5.512 | +51.8% | 18/18 | pass |
| `skills/list-manager/_rtx/tests/test_get_schema.py` | 0.004 | 0.002 | 0.002 | +50.0% | 10/10 | pass |
| `tests/test_officina_blueprint_template.py` | 5.474 | 2.854 | 2.620 | +47.9% | 24/24 | pass |
| `skills/daily-plan/_rtx/tests/test_blueprint_platform_support.py` | 0.070 | 0.037 | 0.033 | +47.1% | 6/6 | pass |
| `skills/daily-plan/_rtx/tests/test_plan_runtime.py` | 0.007 | 0.004 | 0.003 | +42.9% | 11/11 | pass |
| `skills/g-calendar/_rtx/tests/test_calendar_oauth_transaction.py` | 0.005 | 0.003 | 0.002 | +40.0% | 4/4 | pass |
| `tests/test_docs_catalog.py` | 2.018 | 1.224 | 0.794 | +39.3% | 10/10 | pass |
| `skills/list-manager/_rtx/tests/test_validation.py` | 0.075 | 0.050 | 0.025 | +33.3% | 32/32 | pass |
| `skills/math-dependency-graph/_rtx/tests/test_graph_server.py` | 0.003 | 0.002 | 0.001 | +33.3% | 3/3 | pass |
| `skills/email-client/_rtx/tests/test_accounts.py` | 3.528 | 2.365 | 1.163 | +33.0% | 19/19 | pass |
| `tests/test_repository_test_checks.py` | 0.043 | 0.029 | 0.014 | +32.6% | 26/26 | pass |
| `tests/test_install_lifecycle.py` | 0.186 | 0.126 | 0.060 | +32.3% | 7/7 | pass |
| `skills/cloud-files/_rtx/tests/test_script_entrypoints.py` | 0.574 | 0.403 | 0.171 | +29.8% | 2/2 | pass |
| `skills/list-manager/_rtx/tests/test_skill_contract.py` | 2.052 | 1.470 | 0.582 | +28.4% | 2/2 | pass |
| `tests/test_direct_blueprint_v6_schemas.py` | 0.580 | 0.427 | 0.153 | +26.4% | 23/23 | pass |
| `tests/test_benchmark_test_suite.py` | 0.023 | 0.017 | 0.006 | +26.1% | 7/7 | pass |
| `tests/test_visualization_graph.py` | 0.027 | 0.020 | 0.007 | +25.9% | 17/17 | pass |
| `skills/cloud-files/_rtx/tests/test_oauth_transaction.py` | 0.004 | 0.003 | 0.001 | +25.0% | 4/4 | pass |
| `skills/email-client/_rtx/tests/test_oauth_tokens.py` | 0.004 | 0.003 | 0.001 | +25.0% | 8/8 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_dev_link_hooks.py` | 0.004 | 0.003 | 0.001 | +25.0% | 4/4 | pass |
| `tests/test_officina_install_info.py` | 0.004 | 0.003 | 0.001 | +25.0% | 2/2 | pass |
| `tests/validate_portable_dates.py` | 0.016 | 0.012 | 0.004 | +25.0% | 9/9 | pass |
| `src/officina/wakeup/tests/test_monitor.py` | 2.229 | 1.713 | 0.516 | +23.1% | 14/14 | pass |
| `skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py` | 0.063 | 0.049 | 0.014 | +22.2% | 6/6 | pass |
| `hooks/tests/test_inject_dispatcher_context.py` | 0.398 | 0.310 | 0.088 | +22.1% | 13/13 | pass |
| `tests/test_fixture_probe.py` | 0.845 | 0.659 | 0.186 | +22.0% | 2/2 | pass |
| `skills/email-triage/tests/test_rescan.py` | 0.211 | 0.165 | 0.046 | +21.8% | 18/18 | pass |
| `tests/test_officina_dates.py` | 0.005 | 0.004 | 0.001 | +20.0% | 4/4 | pass |
| `tests/test_visualization_containment_edges_browser.py` | 3.302 | 2.643 | 0.659 | +20.0% | 2/2 | pass |
| `tests/test_dispatcher_errors.py` | 0.006 | 0.005 | 0.001 | +16.7% | 5/5 | pass |
| `tests/test_blueprint_visualization.py` | 0.049 | 0.041 | 0.008 | +16.3% | 7/7 | pass |
| `tests/test_officina_certification_hashing.py` | 3.596 | 3.025 | 0.571 | +15.9% | 7/7 | pass |
| `src/officina/wakeup/tests/test_features.py` | 0.082 | 0.069 | 0.013 | +15.9% | 21/21 | pass |
| `skills/cloud-files/_rtx/tests/test_cloud_files.py` | 0.043 | 0.037 | 0.006 | +14.0% | 16/16 | pass |
| `skills/cloud-files/_rtx/tests/test_cloud_files_ensure_oauth.py` | 0.094 | 0.081 | 0.013 | +13.8% | 8/8 | pass |
| `skills/email-triage/_rtx/tests/test_fetch_filtered_envelopes.py` | 0.015 | 0.013 | 0.002 | +13.3% | 6/6 | pass |
| `skills/connect-google/_rtx/tests/test_client_config.py` | 0.016 | 0.014 | 0.002 | +12.5% | 18/18 | pass |
| `tests/test_officina_toml_io.py` | 0.008 | 0.007 | 0.001 | +12.5% | 7/7 | pass |
| `tests/test_visualization_projection_policy.py` | 0.008 | 0.007 | 0.001 | +12.5% | 4/4 | pass |
| `skills/refactor-node/tests/test_refactor_node_routing.py` | 0.188 | 0.165 | 0.023 | +12.2% | 13/13 | pass |
| `tests/validate_platform_neutral.py` | 4.544 | 4.015 | 0.529 | +11.6% | 33/33 | pass |
| `tests/test_skill_taxonomy_graph.py` | 0.009 | 0.008 | 0.001 | +11.1% | 2/2 | pass |
| `tests/test_standard_v6.py` | 5.307 | 4.740 | 0.567 | +10.7% | 26/26 | pass |
| `skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py` | 0.435 | 0.390 | 0.045 | +10.3% | 3/3 | pass |
| `skills/find-handoff-candidates/_rtx/tests/test_parsers.py` | 0.010 | 0.009 | 0.001 | +10.0% | 9/9 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_scaffold.py` | 0.010 | 0.009 | 0.001 | +10.0% | 26/26 | pass |
| `tests/test_officina_famulus_paths.py` | 0.010 | 0.009 | 0.001 | +10.0% | 6/6 | pass |
| `tests/validate_standard_documents.py` | 11.434 | 10.299 | 1.135 | +9.9% | 14/14 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_dev_link.py` | 0.180 | 0.163 | 0.017 | +9.4% | 15/15 | pass |
| `tests/test_standard_consumers.py` | 0.023 | 0.021 | 0.002 | +8.7% | 3/3 | pass |
| `tests/test_skill_refactoring_standard.py` | 1.568 | 1.434 | 0.134 | +8.5% | 9/9 | pass |
| `tests/test_nested_module_migration.py` | 42.878 | 39.382 | 3.496 | +8.2% | 61/61 | failures 0/3 |
| `tests/test_git_test_repository.py` | 0.062 | 0.057 | 0.005 | +8.1% | 4/4 | pass |
| `tests/test_benchmark_command.py` | 0.252 | 0.233 | 0.019 | +7.5% | 4/4 | pass |
| `skills/recurring-tasks/_rtx/tests/test_manage_job.py` | 0.152 | 0.141 | 0.011 | +7.2% | 27/27 | pass |
| `tests/test_officina_google_credentials.py` | 0.235 | 0.219 | 0.016 | +6.8% | 16/16 | pass |
| `tests/test_visualization_filtering.py` | 0.313 | 0.293 | 0.020 | +6.4% | 11/11 | pass |
| `skills/email-triage/tests/test_finalize_run.py` | 1.106 | 1.043 | 0.063 | +5.7% | 10/10 | pass |
| `skills/email-triage/_rtx/tests/test_filter_envelopes.py` | 0.172 | 0.163 | 0.009 | +5.2% | 13/13 | pass |
| `skills/list-manager/_rtx/tests/test_read_beautify.py` | 1.404 | 1.331 | 0.073 | +5.2% | 7/7 | pass |
| `skills/recurring-tasks/_rtx/tests/test_job_control_contract.py` | 0.061 | 0.058 | 0.003 | +4.9% | 2/2 | pass |
| `tests/test_process_binding_compiler.py` | 0.043 | 0.041 | 0.002 | +4.7% | 41/41 | pass |
| `skills/recurring-tasks/_rtx/tests/test_assistant_desktop_notify.py` | 0.029 | 0.028 | 0.001 | +3.4% | 25/25 | pass |
| `skills/g-calendar/_rtx/tests/test_g_calendar_guidance.py` | 0.030 | 0.029 | 0.001 | +3.3% | 2/2 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_codex_install.py` | 14.623 | 14.145 | 0.478 | +3.3% | 2/2 | pass |
| `tests/test_officina_certification_view.py` | 7.854 | 7.600 | 0.254 | +3.2% | 32/32 | pass |
| `tests/test_migrated_standards_fidelity.py` | 8.209 | 7.962 | 0.247 | +3.0% | 12/12 | pass |
| `skills/email-triage/_rtx/tests/test_watermark.py` | 0.707 | 0.686 | 0.021 | +3.0% | 18/18 | pass |
| `tests/validate_skill_md_dispatch.py` | 1.759 | 1.707 | 0.052 | +3.0% | 7/7 | pass |
| `skills/connect-google/_rtx/tests/test_authorize_services.py` | 0.034 | 0.033 | 0.001 | +2.9% | 7/7 | pass |
| `tests/test_officina_repository_configuration.py` | 0.115 | 0.112 | 0.003 | +2.6% | 20/20 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_codex_github_install.py` | 1.878 | 1.833 | 0.045 | +2.4% | 1/1 | failures 3/3 |
| `skills/recurring-tasks/_rtx/tests/test_schedule_backend.py` | 0.044 | 0.043 | 0.001 | +2.3% | 43/43 | pass |
| `tests/validate_blueprint_relationships.py` | 1.750 | 1.711 | 0.039 | +2.2% | 7/7 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_install.py` | 8.043 | 7.909 | 0.134 | +1.7% | 17/17 | pass |
| `tests/test_dispatcher_performance.py` | 1.308 | 1.287 | 0.021 | +1.6% | 6/6 | pass |
| `tests/test_repository_validator_checks.py` | 14.749 | 14.579 | 0.170 | +1.2% | 31/31 | pass |
| `skills/recurring-tasks/_rtx/tests/test_job_executor.py` | 1.451 | 1.435 | 0.016 | +1.1% | 31/31 | pass |
| `tests/test_visualization_browser.py` | 18.696 | 18.542 | 0.154 | +0.8% | 1/1 | pass |
| `skills/skill-drift/_rtx/tests/test_drift_check.py` | 1.593 | 1.582 | 0.011 | +0.7% | 21/21 | pass |
| `tests/test_officina_uv_bootstrap.py` | 0.016 | 0.016 | 0.000 | +0.0% | 10/10 | pass |
| `skills/recurring-tasks/_rtx/tests/test_healthcheck.py` | 0.084 | 0.084 | 0.000 | +0.0% | 34/34 | pass |
| `tests/validate_skill_body_execution.py` | 0.014 | 0.014 | 0.000 | +0.0% | 13/13 | pass |
| `skills/cloud-files/_rtx/tests/test_setup_oauth.py` | 0.002 | 0.002 | 0.000 | +0.0% | 2/2 | pass |
| `skills/g-calendar/_rtx/tests/test_g_calendar_ensure_oauth.py` | 0.006 | 0.006 | 0.000 | +0.0% | 5/5 | pass |
| `skills/initialize-tdd/_rtx/tests/test_host_links_interface.py` | 0.005 | 0.005 | 0.000 | +0.0% | 3/3 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_install_launcher.py` | 0.008 | 0.008 | 0.000 | +0.0% | 13/13 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_install_test_utils.py` | 0.002 | 0.002 | 0.000 | +0.0% | 1/1 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_link_utils.py` | 0.004 | 0.004 | 0.000 | +0.0% | 4/4 | pass |
| `skills/list-manager/_rtx/tests/test_beautify.py` | 0.035 | 0.035 | 0.000 | +0.0% | 7/7 | pass |
| `skills/math-dependency-graph/_rtx/tests/test_graph_builder.py` | 0.001 | 0.001 | 0.000 | +0.0% | 3/3 | pass |
| `skills/recurring-tasks/_rtx/tests/test_ensure_agent_env.py` | 0.004 | 0.004 | 0.000 | +0.0% | 5/5 | pass |
| `skills/recurring-tasks/_rtx/tests/test_setup_runner.py` | 0.008 | 0.008 | 0.000 | +0.0% | 10/10 | pass |
| `tests/test_benchmark_precommit.py` | 0.002 | 0.002 | 0.000 | +0.0% | 2/2 | pass |
| `tests/test_controller_protocol.py` | 0.006 | 0.006 | 0.000 | +0.0% | 6/6 | pass |
| `tests/test_officina_repository_paths.py` | 0.007 | 0.007 | 0.000 | +0.0% | 7/7 | pass |
| `tests/validate_skill_metadata.py` | 0.012 | 0.012 | 0.000 | +0.0% | 7/7 | pass |
| `tests/validate_skip_hygiene.py` | 0.017 | 0.017 | 0.000 | +0.0% | 9/9 | pass |
| `tests/validate_subprocess_text_encoding.py` | 0.017 | 0.017 | 0.000 | +0.0% | 8/8 | pass |
| `tests/validate_boundaries.py` | 0.019 | 0.019 | -0.000 | -0.0% | 7/7 | pass |
| `tests/test_v6_tooling_support.py` | 0.245 | 0.246 | -0.001 | -0.4% | 4/4 | pass |
| `skills/skill-certifier/_rtx/tests/test_certifier.py` | 24.137 | 24.237 | -0.100 | -0.4% | 57/57 | pass |
| `tests/test_blueprint_inventory.py` | 0.216 | 0.217 | -0.001 | -0.5% | 36/36 | pass |
| `tests/test_officina_pooled_blueprint.py` | 1.382 | 1.398 | -0.016 | -1.2% | 8/8 | pass |
| `tests/test_officina_blueprint_graph.py` | 6.759 | 6.846 | -0.087 | -1.3% | 50/50 | pass |
| `tests/test_officina_launcher_entry.py` | 1.799 | 1.836 | -0.037 | -2.1% | 22/22 | pass |
| `tests/validate_dependencies.py` | 0.229 | 0.237 | -0.008 | -3.5% | 11/11 | pass |
| `tests/test_officina_certificate_records.py` | 0.054 | 0.056 | -0.002 | -3.7% | 18/18 | pass |
| `tests/test_nested_module_v5_schemas.py` | 0.454 | 0.474 | -0.020 | -4.4% | 24/24 | pass |
| `tests/validate_toml_io_boundary.py` | 0.021 | 0.022 | -0.001 | -4.8% | 11/11 | pass |
| `tests/test_visualization_projection_arrangements_browser.py` | 96.640 | 101.291 | -4.651 | -4.8% | 24/24 | pass |
| `skills/recurring-tasks/_rtx/tests/test_sync_units.py` | 0.020 | 0.021 | -0.001 | -5.0% | 16/16 | pass |
| `skills/list-manager/_rtx/tests/test_python_machine_interfaces.py` | 0.211 | 0.222 | -0.011 | -5.2% | 2/2 | pass |
| `tests/test_node_certification_hashing.py` | 3.114 | 3.286 | -0.172 | -5.5% | 20/20 | pass |
| `tests/test_officina_python_machine_interface.py` | 5.808 | 6.130 | -0.322 | -5.5% | 57/57 | pass |
| `tests/validate_skill_runtime_doc_references.py` | 0.414 | 0.438 | -0.024 | -5.8% | 18/18 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_launchers.py` | 0.034 | 0.036 | -0.002 | -5.9% | 19/19 | pass |
| `skills/pdf-to-markdown/_rtx/tests/test_skill_contract.py` | 0.017 | 0.018 | -0.001 | -5.9% | 2/2 | pass |
| `tests/test_officina_secret_store.py` | 0.229 | 0.244 | -0.015 | -6.6% | 15/15 | pass |
| `tests/validate_personal_info.py` | 0.015 | 0.016 | -0.001 | -6.7% | 15/15 | pass |
| `tests/validate_documentation_validators.py` | 3.346 | 3.574 | -0.228 | -6.8% | 4/4 | pass |
| `tests/test_docstring_schema_dynamic_sections.py` | 6.296 | 6.742 | -0.446 | -7.1% | 37/37 | pass |
| `skills/recurring-tasks/_rtx/tests/test_no_posix_entrypoints.py` | 0.185 | 0.199 | -0.014 | -7.6% | 3/3 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py` | 0.974 | 1.053 | -0.079 | -8.1% | 5/5 | pass |
| `tests/validate_blueprints.py` | 1.897 | 2.057 | -0.160 | -8.4% | 15/15 | pass |
| `tests/test_officina_atomic_files.py` | 0.058 | 0.063 | -0.005 | -8.6% | 56/56 | pass |
| `tests/validate_interface_ids.py` | 0.687 | 0.749 | -0.062 | -9.0% | 6/6 | pass |
| `tests/test_interface_injection_migration.py` | 4.312 | 4.729 | -0.417 | -9.7% | 56/56 | pass |
| `skills/find-handoff-candidates/_rtx/tests/test_scan.py` | 0.010 | 0.011 | -0.001 | -10.0% | 9/9 | pass |
| `skills/skill-maker/_rtx/tests/test_blueprint_tools.py` | 0.944 | 1.041 | -0.097 | -10.3% | 14/14 | pass |
| `tests/test_docs_site.py` | 0.413 | 0.463 | -0.050 | -12.1% | 5/5 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_claude_github_install.py` | 2.855 | 3.201 | -0.346 | -12.1% | 1/1 | failures 3/3 |
| `skills/list-manager/_rtx/tests/test_category_cache.py` | 0.008 | 0.009 | -0.001 | -12.5% | 6/6 | pass |
| `tests/test_officina_git_provenance.py` | 1.109 | 1.251 | -0.142 | -12.8% | 40/40 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_google_onboarding.py` | 0.629 | 0.710 | -0.081 | -12.9% | 11/11 | pass |
| `skills/g-calendar/_rtx/tests/test_gcal.py` | 0.065 | 0.074 | -0.009 | -13.8% | 12/12 | pass |
| `tests/test_officina_managed_runtime.py` | 1.994 | 2.291 | -0.297 | -14.9% | 25/25 | pass |
| `tests/test_docstrings_validator.py` | 2.191 | 2.592 | -0.401 | -18.3% | 6/6 | pass |
| `tests/validate_cross_platform.py` | 3.944 | 4.669 | -0.725 | -18.4% | 24/24 | pass |
| `tests/test_duplicate_subcommand_tokens.py` | 2.750 | 3.274 | -0.524 | -19.1% | 10/10 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_claude_install.py` | 8.010 | 9.618 | -1.608 | -20.1% | 1/1 | pass |
| `tests/validate_skill_runtime_files.py` | 0.045 | 0.055 | -0.010 | -22.2% | 25/25 | pass |
| `tests/test_blueprint_schema_metadata.py` | 0.018 | 0.022 | -0.004 | -22.2% | 11/11 | pass |
| `tests/test_standard_extractor.py` | 4.446 | 5.468 | -1.022 | -23.0% | 7/7 | pass |
| `tests/test_standard_query.py` | 7.114 | 8.851 | -1.737 | -24.4% | 5/5 | pass |
| `tests/test_repo_checks_entrypoint.py` | 0.167 | 0.208 | -0.041 | -24.6% | 4/4 | pass |
| `tests/test_dispatcher_direct_authorization.py` | 0.589 | 0.735 | -0.146 | -24.8% | 27/27 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_rc_block.py` | 0.004 | 0.005 | -0.001 | -25.0% | 5/5 | pass |
| `tests/test_dispatcher_route_smoke.py` | 20.405 | 25.628 | -5.223 | -25.6% | 14/14 | pass |
| `skills/daily-plan/_rtx/tests/test_dispatch_contract.py` | 0.003 | 0.004 | -0.001 | -33.3% | 1/1 | pass |
| `skills/recurring-tasks/_rtx/tests/test_healthcheck_environment_invariance.py` | 0.003 | 0.004 | -0.001 | -33.3% | 7/7 | pass |
| `tests/test_blueprint_search.py` | 2.823 | 3.789 | -0.966 | -34.2% | 19/19 | pass |
| `tests/validate_dispatch_caller_module.py` | 0.670 | 0.907 | -0.237 | -35.4% | 16/16 | pass |
| `tests/validate_dispatcher_usage.py` | 0.011 | 0.015 | -0.004 | -36.4% | 8/8 | pass |
| `tests/test_dispatcher_direct_blueprints.py` | 0.024 | 0.033 | -0.009 | -37.5% | 19/19 | pass |
| `tests/test_runtime_module_test_support.py` | 0.042 | 0.059 | -0.017 | -40.5% | 2/2 | pass |
| `skills/email-triage/tests/test_llm_routing.py` | 0.037 | 0.053 | -0.016 | -43.2% | 7/7 | pass |
| `skills/install-assistant-tools/_rtx/tests/test_agent_launch.py` | 0.002 | 0.003 | -0.001 | -50.0% | 5/5 | pass |
| `skills/recurring-tasks/_rtx/tests/test_linux_registration_check.py` | 0.002 | 0.003 | -0.001 | -50.0% | 2/2 | pass |
| `tests/test_officina_oauth_json.py` | 0.004 | 0.006 | -0.002 | -50.0% | 3/3 | pass |
| `tests/validate_names.py` | 0.006 | 0.009 | -0.003 | -50.0% | 6/6 | pass |
| `tests/test_officina_runtime_pointer.py` | 0.019 | 0.029 | -0.010 | -52.6% | 15/15 | pass |
| `tests/test_python_source_cache.py` | 0.007 | 0.011 | -0.004 | -57.1% | 6/6 | pass |
| `tests/test_configured_schema.py` | 0.239 | 0.382 | -0.143 | -59.8% | 50/50 | pass |
| `tests/test_configuration_consumers.py` | 1.799 | 3.338 | -1.539 | -85.5% | 9/9 | pass |
| `tests/test_visualization_inspector_and_bezier_browser.py` | 13.558 | 32.899 | -19.341 | -142.7% | 3/3 | pass |
| `skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py` | 0.000 | 0.000 | 0.000 | — | 2/2 | pass |
| `src/officina/wakeup/tests/test_client_integration.py` | 0.000 | 0.000 | 0.000 | — | 2/2 | pass |
| `tests/test_legacy_migration.py` | 0.000 | 0.000 | 0.000 | — | 1/1 | pass |

## Validator files

| File | Baseline median (s) | Current median (s) | Delta saved (s) | Relative improvement | Items old/new | Status |
|---|---:|---:|---:|---:|---:|---|
| `validators/readme_user_contract.py` | 0.001 | 0.000 | 0.001 | +100.0% | 1/1 | pass |
| `validators/skill/dispatcher_usage.py` | 0.206 | 0.156 | 0.050 | +24.3% | 1/1 | pass |
| `validators/skill/names.py` | 0.009 | 0.007 | 0.002 | +22.2% | 1/1 | pass |
| `validators/skill/skill_md_dispatch.py` | 0.085 | 0.068 | 0.017 | +20.0% | 1/1 | pass |
| `validators/skill/dispatch_caller_module.py` | 0.325 | 0.265 | 0.060 | +18.5% | 1/1 | pass |
| `validators/skill/skill_body_execution.py` | 0.024 | 0.020 | 0.004 | +16.7% | 1/1 | pass |
| `validators/skill/boundaries.py` | 0.123 | 0.105 | 0.018 | +14.6% | 1/1 | pass |
| `validators/skill/interface_ids.py` | 0.079 | 0.069 | 0.010 | +12.7% | 1/1 | pass |
| `validators/skill/blueprints.py` | 1.877 | 1.665 | 0.212 | +11.3% | 1/1 | pass |
| `validators/standard_documents.py` | 3.159 | 2.900 | 0.259 | +8.2% | 1/1 | pass |
| `validators/skill/skill_metadata.py` | 0.051 | 0.048 | 0.003 | +5.9% | 1/1 | pass |
| `validators/contributor_docs_contract.py` | 0.210 | 0.200 | 0.010 | +4.8% | 1/1 | pass |
| `validators/user_docs_cover_blueprints.py` | 0.202 | 0.193 | 0.009 | +4.5% | 4/4 | pass |
| `validators/skill/blueprint_relationships.py` | 0.066 | 0.064 | 0.002 | +3.0% | 1/1 | pass |
| `validators/platform_neutral.py` | 1.377 | 1.346 | 0.031 | +2.3% | 1/1 | pass |
| `validators/skip_hygiene.py` | 0.145 | 0.142 | 0.003 | +2.1% | 1/1 | pass |
| `validators/skill/dependencies.py` | 0.224 | 0.229 | -0.005 | -2.2% | 1/1 | pass |
| `validators/toml_io_boundary.py` | 0.470 | 0.481 | -0.011 | -2.3% | 1/1 | pass |
| `validators/portable_dates.py` | 0.163 | 0.167 | -0.004 | -2.5% | 1/1 | pass |
| `validators/cross_platform.py` | 3.208 | 3.322 | -0.114 | -3.6% | 1/1 | pass |
| `validators/generated_skill_docs.py` | 0.239 | 0.258 | -0.019 | -7.9% | 1/1 | pass |
| `validators/personal_info.py` | 0.502 | 0.556 | -0.054 | -10.8% | 1/1 | pass |
| `validators/skill_runtime_doc_references.py` | 0.404 | 0.450 | -0.046 | -11.4% | 1/1 | pass |
| `validators/duplicate_subcommand_tokens.py` | 0.063 | 0.071 | -0.008 | -12.7% | 1/1 | pass |
| `validators/subprocess_text_encoding.py` | 0.172 | 0.198 | -0.026 | -15.1% | 1/1 | pass |
| `validators/docstrings.py` | 5.231 | 7.764 | -2.533 | -48.4% | 1/1 | failures 3/3 |
| `validators/skill_runtime_files.py` | 0.151 | 0.711 | -0.560 | -370.9% | 1/1 | pass |
