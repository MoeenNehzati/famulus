# Test and Validator Performance Audit

Inventory captured from the canonical runner boundaries on 2026-08-10: 179 pytest modules, 27 validator modules, and two shared canonical conftest files.

## Audit rule

Each checkbox is closed only after the file has been inspected and, when runtime cost is material, measured. A closed item records one of: `optimized`, `no safe speedup`, `already efficient`, or `blocked`, together with verification evidence. Changes must preserve assertions, platform coverage, isolation, deterministic behavior, and external-side-effect boundaries.

Fixtures and caches are means, not goals. Reuse computation only when the reused object is immutable or reset safely between tests; do not trade speed for hidden shared state.

Every direct-only median in this audit is a **diagnostic observation**, not a
certified performance result, until an exact canonical-runner task or a
whole-suite comparison verifies it.

## Current verification baseline

- Audit inventory complete: 208 of 208 shared-infrastructure, pytest-module,
  and canonical-validator entries are closed; none remain pending.
- Final retained-refactor verification: 189 focused tests passed in 7.36s, and
  all 273 validator test cases passed in 12.81s. Both staged and unstaged diff
  checks are clean.
- The canonical staged validator runner passed in 15.22s. The browser audit
  passed all 63 visualization tests in 32.72s when run outside the restricted
  sandbox.
- The migration audit passed 116 of 117 tests in 28.25s; the sole failure was
  the intentional clean-working-tree guard in this dirty checkout. Five real-
  `uv` launcher/runtime cases could not be timed locally because the restricted
  environment cannot write its cache directory.
- Canonical full run: 1,538 passed, 17 skipped, with 47 failures and 1 collection error in unrelated real-uv, browser, docstring, performance, migration, and recurring-tasks route-smoke areas. This is not a green performance baseline.

## Shared pytest infrastructure

- [x] `conftest.py` — **optimized**. Replaced the per-test physical
  `tmp_path` allocation used only to seed `LOCALAPPDATA` with a unique lazy
  path under pytest's session temp root. On three 1,000-case no-op runs, median
  wall time fell from 0.945s to 0.563s (40%); a `--noconftest` control measured
  0.463s. Verified by the 115-test cleanup and path-contract set.
- [x] `tests/conftest.py` — **already efficient**. Its autouse browser lock
  exits before touching the filesystem for non-browser tests. Three 1,000-case
  no-op runs had a 0.565s median versus 0.563s with only the root conftest, a
  difference below benchmark noise; targeting it more narrowly would not buy a
  supported speedup.

## Canonical pytest modules (179)

- [x] `hooks/tests/test_inject_dispatcher_context.py` — **optimized**. A
  module-scoped fixture now installs the immutable dispatcher launcher once
  for four subprocess contract tests; each invocation still receives a copied
  environment. Across three complete runs, median wall time fell from 0.403s
  to 0.382s (5.1%); all 13 tests pass.
- [x] `skills/cloud-files/_rtx/tests/test_cloud_files.py` — **already
  efficient**. Sixteen unit tests complete in 0.17s; repeated configuration
  objects are trivial, while temporary directories are confined to behavior
  that actually reads or writes files.
- [x] `skills/cloud-files/_rtx/tests/test_cloud_files_ensure_oauth.py` — **no
  safe speedup**. Eight tests complete in 0.25s; the visible cost is isolated
  credential/configuration writes whose fresh homes prevent state leakage.
- [x] `skills/cloud-files/_rtx/tests/test_oauth_transaction.py` — **already
  efficient**. Four transaction tests complete in 0.07s with no repeated
  material setup.
- [x] `skills/cloud-files/_rtx/tests/test_script_entrypoints.py` — **optimized**.
  Preserved real-process coverage for every wrapper while replacing two
  redundant post-delete listing subprocesses with direct backing-store
  assertions. Five matched complete runs improved from a 0.744s median to
  0.543s (27%); both round-trip tests pass.
- [x] `skills/cloud-files/_rtx/tests/test_setup_oauth.py` — **already
  efficient**. Two tests complete in 0.07s; its only temporary file is the
  client JSON whose parsing is under test.
- [x] `skills/connect-google/_rtx/tests/test_authorize_services.py` — **already
  efficient**. Seven tests complete in 0.05s when localhost binding is
  available. The loopback server is functional OAuth-boundary coverage, not
  reusable setup; the sandbox-only socket denial is not a test defect.
- [x] `skills/connect-google/_rtx/tests/test_client_config.py` — **already
  efficient**. Eighteen tests complete in 0.11s; filesystem writes are limited
  to installation, replacement, permissions, and discovery behavior.
- [x] `skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py` —
  **already efficient**. Six tests complete in 0.20s. A trial YAML cache saved
  only about 0.02s, so it was reverted rather than retain unsupported
  complexity for a below-noise gain.
- [x] `skills/connect-google/_rtx/tests/test_service_delegation.py` —
  **optimized**. Its file-local loader now caches parsed blueprint mappings,
  which all nine tests treat as read-only. Five paired runs reduced median
  pytest session time from 0.66s to 0.19s (71%) while preserving every
  delegation and authorization assertion.
- [x] `skills/daily-plan/_rtx/tests/test_blueprint_platform_support.py` —
  **already efficient**. Six parametrized platform checks complete in 0.07s;
  caching three small YAML documents would not produce a material gain.
- [x] `skills/daily-plan/_rtx/tests/test_dispatch_contract.py` — **already
  efficient**. Its single real dispatcher-resolution contract completes in
  0.06s.
- [x] `skills/daily-plan/_rtx/tests/test_plan_runtime.py` — **already
  efficient**. Eleven tests complete in 0.03s; runtime modules are loaded once
  except where separate bookkeeping tests intentionally patch module state.
- [x] `skills/email-client/_rtx/tests/test_accounts.py` — **optimized**.
  Mutation tests now inspect the canonical `accounts.json` artifact directly
  instead of starting nine additional `resolve`/`list` processes; dedicated
  real-process coverage for both read commands remains. Five complete runs
  improved from a 3.093s median to 2.152s (30%); all 19 tests pass.
- [x] `skills/email-client/_rtx/tests/test_mail.py` — **already efficient**.
  Forty-one tests complete in 0.07s; its two temporary-path users exercise
  real attachment output and collision behavior.
- [x] `skills/email-client/_rtx/tests/test_oauth_tokens.py` — **already
  efficient**. Eight tests complete in 0.04s, with temporary files limited to
  actual client-config and credential-path behavior.
- [x] `skills/email-client/_rtx/tests/test_smoke.py` — **already efficient**.
  Three smoke-contract tests complete in 0.05s with no repeated material
  setup.
- [x] `skills/email-triage/_rtx/tests/test_fetch_filtered_envelopes.py` —
  **already efficient**. Six in-process composite tests complete in 0.07s;
  subprocess results are test doubles rather than process launches.
- [x] `skills/email-triage/_rtx/tests/test_filter_envelopes.py` — **already
  efficient**. Thirteen tests complete in 0.29s; four real CLI invocations
  cover distinct filtering and missing-watermark paths.
- [x] `skills/email-triage/_rtx/tests/test_watermark.py` — **no safe speedup**.
  Eighteen tests complete in 0.84s. The cost is separate CLI invocations that
  verify clean-run advancement, failure latching, clearing, replay, and
  cross-process persistence; sharing process state would weaken those claims.
- [x] `skills/email-triage/tests/test_finalize_run.py` — **no safe speedup**.
  Ten tests complete in 0.72s. Its real subprocess boundaries are the subject
  of ordering, retry, idempotency, and backward-compatibility assertions.
- [x] `skills/email-triage/tests/test_llm_routing.py` — **already efficient**.
  Seven contract tests complete in 0.05s with no material repeated setup.
- [x] `skills/email-triage/tests/test_rescan.py` — **already efficient**.
  Eighteen tests complete in 0.22s; apparent subprocess use is mocked dispatch
  output, while YAML reads cover distinct routing artifacts.
- [x] `skills/find-handoff-candidates/_rtx/tests/test_parsers.py` — **already
  efficient**. Nine parser tests complete in 0.02s without material setup.
- [x] `skills/find-handoff-candidates/_rtx/tests/test_scan.py` — **already
  efficient**. Nine scan tests complete in 0.02s; their temporary JSONL trees
  are the filesystem and mtime behavior under test.
- [x] `skills/g-calendar/_rtx/tests/test_calendar_oauth_transaction.py` —
  **already efficient**. Four isolated credential-transaction tests complete
  in 0.03s.
- [x] `skills/g-calendar/_rtx/tests/test_g_calendar_ensure_oauth.py` —
  **already efficient**. Five configuration and scoped-credential tests
  complete in 0.02s; fresh homes are behaviorally required.
- [x] `skills/g-calendar/_rtx/tests/test_g_calendar_guidance.py` — **already
  efficient**. Two binding/runtime parser checks complete in 0.09s.
- [x] `skills/g-calendar/_rtx/tests/test_gcal.py` — **already efficient**.
  Twelve client tests complete in 0.10s; its one subprocess preserves the
  public Python-interface help surface.
- [x] `skills/install-assistant-tools/_rtx/tests/test_agent_launch.py` —
  **already efficient**. Five launcher tests complete in 0.02s.
- [x] `skills/install-assistant-tools/_rtx/tests/test_claude_github_install.py`
  — **blocked**. The live GitHub marketplace test cannot complete in the
  restricted environment because Claude's clone uses unavailable SSH
  authentication; its observed partial runtime is not optimization evidence.
- [x] `skills/install-assistant-tools/_rtx/tests/test_claude_install.py` —
  **blocked**. The isolated real-plugin install reaches the scaffold after
  about 6.1s but fails because no usable keyring backend is available. Its
  end-to-end install boundary must pass before performance changes are judged.
- [x] `skills/install-assistant-tools/_rtx/tests/test_codex_github_install.py`
  — **blocked**. The live marketplace clone cannot resolve GitHub in the
  restricted environment; partial failure timing is not comparable.
- [x] `skills/install-assistant-tools/_rtx/tests/test_codex_install.py` —
  **blocked**. The real local marketplace/install/bootstrap path ran for about
  9.1s before an environment-dependent failure, so no coverage-preserving
  performance refactor is certified from this run.
- [x] `skills/install-assistant-tools/_rtx/tests/test_dev_link.py` — **already
  efficient**. Fifteen isolated link and Git-hook tests complete in 0.23s.
- [x] `skills/install-assistant-tools/_rtx/tests/test_dev_link_hooks.py` —
  **already efficient**. Four registered-hook tests complete in 0.05s.
- [x] `skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py` —
  **blocked**. The sandbox prevents temporary `core.hooksPath` writes and UV
  cache creation; these failures occur inside the lifecycle behavior being
  measured, so incomplete timings cannot justify refactoring.
- [x] `skills/install-assistant-tools/_rtx/tests/test_google_onboarding.py` —
  **no safe speedup**. Eleven tests complete in 0.76s. Their fake dispatcher is
  deliberately a real process so argv ordering, exit status, and output
  redaction cross the same boundary as installation-time onboarding.
- [x] `skills/install-assistant-tools/_rtx/tests/test_install.py` — **blocked**.
  Four tests currently enter the managed-UV bootstrap and fail on restricted
  network access before their mocked candidate-build assertions; repair or a
  runnable baseline is required before performance work.
- [x] `skills/install-assistant-tools/_rtx/tests/test_install_launcher.py` —
  **already efficient**. Thirteen launcher-content tests complete in 0.05s.
- [x] `skills/install-assistant-tools/_rtx/tests/test_install_manifest.py` —
  **optimized**. Four manifest-replay tests now call the same uninstall CLI
  parser and `main()` in-process; executable smoke coverage remains in
  `test_uninstall.py`. Three complete runs improved from a 0.906s median to
  0.262s (71%); all 12 tests pass.
- [x] `skills/install-assistant-tools/_rtx/tests/test_install_test_utils.py` —
  **already efficient**. Its single PATH-resolution contract completes in
  0.02s.
- [x] `skills/install-assistant-tools/_rtx/tests/test_launchers.py` — **already
  efficient**. Nineteen launcher tests complete in 0.08s.
- [x] `skills/install-assistant-tools/_rtx/tests/test_link_utils.py` — **already
  efficient**. Four link utility tests complete in 0.02s.
- [x] `skills/install-assistant-tools/_rtx/tests/test_rc_block.py` — **already
  efficient**. Five shell-block tests complete in 0.02s.
- [x] `skills/install-assistant-tools/_rtx/tests/test_scaffold.py` — **already
  efficient**. Twenty-six scaffold tests complete in 0.09s when the runtime
  module root is included in the supported test import path.
- [x] `skills/install-assistant-tools/_rtx/tests/test_uninstall.py` —
  **optimized**. Twelve manifest-behavior tests now isolate argv and output
  around the real in-process CLI `main()` while one report test retains a
  fresh-interpreter executable check. Three complete runs improved from a
  2.334s median to 0.584s (75%); all 13 tests pass.
- [x] `skills/initialize-tdd/_rtx/tests/test_host_links_interface.py` —
  **already efficient**. Three host-link interface tests complete in 0.06s.
- [x] `skills/list-manager/_rtx/tests/test_beautify.py` — **already
  efficient**. Seven rendering tests complete in 0.06s.
- [x] `skills/list-manager/_rtx/tests/test_category_cache.py` — **already
  efficient**. Six category-cache tests complete in 0.04s.
- [x] `skills/list-manager/_rtx/tests/test_get_schema.py` — **already
  efficient**. Ten schema tests complete in 0.06s.
- [x] `skills/list-manager/_rtx/tests/test_lists.py` — **optimized**. Ordinary
  command tests now use the production `_yaml_store.main(argv)` machine
  interface with isolated standard streams; the timeout and two concurrent
  writer tests retain real processes. Three complete runs improved from a
  10.739s median to 3.898s (64%); all 69 tests pass.
- [x] `skills/list-manager/_rtx/tests/test_python_machine_interfaces.py` —
  **already efficient**. Two public interface checks complete in 0.31s and
  preserve the actual machine-interface boundary.
- [x] `skills/list-manager/_rtx/tests/test_read_beautify.py` — **no safe
  speedup**. Seven bridge tests complete in 1.33s. An in-process trial was
  reverted because `_render_bridge` intentionally delegates to `_yaml_store`
  as a subprocess; bypassing that loses the bridge's constructed environment.
- [x] `skills/list-manager/_rtx/tests/test_skill_contract.py` — **optimized**.
  A module-scoped fixture shares one read-only repository blueprint graph
  across both export assertions. Three runs improved from a 2.553s median to
  1.267s (50%); both tests pass.
- [x] `skills/list-manager/_rtx/tests/test_validation.py` — **already
  efficient**. Thirty-two schema-validation tests complete in 0.14s.
- [x] `skills/math-dependency-graph/_rtx/tests/test_graph_builder.py` —
  **already efficient**. Three builder tests complete in 0.17s.
- [x] `skills/math-dependency-graph/_rtx/tests/test_graph_server.py` —
  **already efficient**. Three server tests complete in 0.05s.
- [x] `skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py` — **no
  safe speedup**. Three tests complete in 0.37s; their renderer/CLI work is the
  macro generation and output-path behavior under test.
- [x] `skills/pdf-to-markdown/_rtx/tests/test_skill_contract.py` — **already
  efficient**. Two source-fetcher contract tests complete in 0.03s.
- [x] `skills/recurring-tasks/_rtx/tests/test_assistant_desktop_notify.py` —
  **already efficient**. The notification adapters are exercised with local
  fakes and contribute no measurable slow case to the suite.
- [x] `skills/recurring-tasks/_rtx/tests/test_enable_disable.py` —
  **optimized**. The five behavioral cases now call the real Python entry
  point in-process; separate CLI tests retain process-boundary coverage. The
  three-run median fell from 0.724s to 0.322s (55% faster).
- [x] `skills/recurring-tasks/_rtx/tests/test_ensure_agent_env.py` — **already
  efficient**. Environment construction is tested directly with temporary
  paths and fakes.
- [x] `skills/recurring-tasks/_rtx/tests/test_healthcheck.py` — **already
  efficient**. Checks use injected/mocked scheduler and notification
  boundaries; no slow case exceeds 0.01s.
- [x] `skills/recurring-tasks/_rtx/tests/test_healthcheck_environment_invariance.py`
  — **already efficient**. The platform/environment matrix is in-process and
  below the suite's measured slow-test threshold.
- [x] `skills/recurring-tasks/_rtx/tests/test_job_control_contract.py` —
  **already efficient**. The two material edit cases complete in 0.03s each
  or less and use isolated files.
- [x] `skills/recurring-tasks/_rtx/tests/test_job_executor.py` — **no safe
  speedup**. The 1.01s slow case verifies that a real over-time child is
  killed and recorded; the remaining process cases verify kill and direct
  entry-point boundaries and are at most 0.10s.
- [x] `skills/recurring-tasks/_rtx/tests/test_linux_registration_check.py` —
  **already efficient**. Registration checks use bounded local fakes and do
  not appear among the suite's slow cases.
- [x] `skills/recurring-tasks/_rtx/tests/test_manage_job.py` — **already
  efficient**. Behavioral paths are in-process; the two retained parser/CLI
  process checks each take 0.03s.
- [x] `skills/recurring-tasks/_rtx/tests/test_no_posix_entrypoints.py` — **no
  safe speedup**. Its 0.17s case loads the blueprint graph and generates a
  service to verify the cross-file no-shell contract; there is no repeated
  immutable setup within this module to share.
- [x] `skills/recurring-tasks/_rtx/tests/test_schedule_backend.py` — **already
  efficient**. The platform backend matrix uses local temporary state and
  fakes and contributes no measured slow case.
- [x] `skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py` —
  **correctly opt-in**. Three host-scheduler mutation tests are skipped in the
  canonical suite and covered by backend unit tests there.
- [x] `skills/recurring-tasks/_rtx/tests/test_setup_runner.py` — **already
  efficient**. Setup-runner behavior uses injected backends and stays below
  the suite's measured slow-test threshold.
- [x] `skills/recurring-tasks/_rtx/tests/test_sync_units.py` — **already
  efficient**. Generated scheduler files use per-test temporary state and no
  case appears among the suite's slow tests.
- [x] `skills/refactor-node/tests/test_refactor_node_routing.py` — **already
  efficient**. Thirteen routing-ownership tests complete in 0.16s.
- [x] `skills/skill-certifier/_rtx/tests/test_certifier.py` — **no safe
  speedup**. Fifty-seven tests complete in 22.78s. The material cases create
  and mutate independent Git repositories, signing keys, certificate logs,
  and race states; sharing those fixtures would invalidate isolation and
  append-only/commit-readiness claims.
- [x] `skills/skill-drift/_rtx/tests/test_drift_check.py` — **no safe speedup**.
  Twenty-one tests complete in 2.16s. The slower cases require independently
  certified repositories that are then deleted, corrupted, or re-scoped;
  reusing signed state would couple their findings.
- [x] `skills/skill-maker/_rtx/tests/test_blueprint_tools.py` — **no safe
  speedup**. Fourteen tests complete in 1.72s; each material case mutates a
  separate copied blueprint tree and checks synchronization side effects.
- [x] `src/officina/wakeup/tests/test_client_integration.py` — **already
  efficient**. Its two live-client smoke tests collect in 0.02s and skip under
  the declared opt-in policy.
- [x] `src/officina/wakeup/tests/test_features.py` — **already efficient**.
  Twenty-one feature tests complete in 0.09s.
- [x] `src/officina/wakeup/tests/test_monitor.py` — **no safe speedup**.
  Fourteen tests complete in 0.73s; the 0.53s case exercises the real
  monitor-before-due-delivery CLI sequence rather than reusable setup.
- [x] `tests/test_benchmark_command.py` — **already efficient**. Four real
  benchmark-wrapper cases complete within 0.10s each; the slowest intentionally
  samples a child process tree and verifies attribution metrics.
- [x] `tests/test_benchmark_precommit.py` — **already efficient**. Benchmark
  orchestration uses local fakes for expensive runner work and adds no measured
  slow case.
- [x] `tests/test_blueprint_catalog_schema.py` — **optimized**. Thirteen catalog
  cases now share one immutable configured-schema validator; mutable module
  documents and temporary missing-config fixtures remain per-test. The
  three-run median fell from 1.547s to 0.402s (74% faster).
- [x] `tests/test_blueprint_inventory.py` — **already efficient**. Thirty-six
  inventory, parsing, ignored-path, and v5 registration cases pass in 0.12s;
  no case exceeds 0.01s and temporary repository state is behavior under test.
- [x] `tests/test_blueprint_schema_metadata.py` — **optimized**. The eleven
  read-only metadata checks now cache immutable frozen-v4 and frozen-v5 schema
  documents by name. The three-run median fell from 0.767s to 0.482s (37%
  faster).
- [x] `tests/test_blueprint_search.py` — **no safe speedup**. Its only
  material case performs one full live v6 repository search to verify current
  registered descriptions; the remaining fixture and CLI cases are at most
  0.35s.
- [x] `tests/test_blueprint_visualization.py` — **already efficient**. Three
  payload/rendering contracts contribute no material slow case.
- [x] `tests/test_configuration_consumers.py` — **no safe speedup**. Its only
  material case loads the live graph once to prove direct ownership and import
  dependencies; the other eight configuration cases are at most 0.03s.
- [x] `tests/test_configured_schema.py` — **already efficient**. Caching the
  immutable annotation protocol preserved all 50 tests but did not improve the
  three-run median (0.804s before versus 0.905s after), so the trial was fully
  reverted. Per-test temporary schema/configuration documents remain isolated.
- [x] `tests/test_controller_protocol.py` — **already efficient**. The
  controller request/event/protocol matrix is in-process and contributes no
  material slow case in the 152-test contract batch.
- [x] `tests/test_direct_blueprint_v6_schemas.py` — **optimized**. Twenty-three
  document cases now share one immutable v6 JSON Schema validator. The
  three-run median fell from 1.507s to 0.482s (68% faster).
- [x] `tests/test_dispatcher_direct_authorization.py` — **no safe speedup**.
  The two material cases (0.11s and 0.09s) execute real host routes to verify
  explicit configuration and ambient-`PYTHONPATH` isolation; remaining
  authorization cases are fast.
- [x] `tests/test_dispatcher_direct_blueprints.py` — **already efficient**.
  Direct blueprint resolution cases contribute no material slow case.
- [x] `tests/test_dispatcher_errors.py` — **already efficient**. Structured and
  text error contracts contribute no material slow case.
- [x] `tests/test_dispatcher_performance.py` — **no safe speedup**. The two
  material tests deliberately measure ten fresh CLI processes apiece; reusing
  a process would invalidate the cold-start latency gates. Warm-resolution
  cases already run in-process.
- [x] `tests/test_dispatcher_route_smoke.py` — **no safe speedup**. Live graph
  discovery and the immutable route manifest are already module fixtures, and
  all runner routes are already traced in one isolated child. The 10.85s
  route-smoke call is the cross-module import/isolation contract under test.
- [x] `tests/test_docs_catalog.py` — **optimized**. Three read-only live-repo
  description checks now share one module-scoped immutable catalog; synthetic
  catalogs remain per-test. All 10 tests pass. The three-run median fell from
  1.105s to 0.804s (27% faster).
- [x] `tests/test_docs_site.py` — **already efficient**. The two material cases
  exercise real file-path hook loading and CLI discovery at 0.10s and 0.09s;
  other site contracts are faster.
- [x] `tests/test_docstring_schema_dynamic_sections.py` — **no safe speedup**.
  Thirty-seven dynamic-policy, parser, dependency, profile, and AST cases pass
  in 2.87s. Material cases intentionally install distinct schema/configuration
  files or validate separate temporary modules; sharing the effective policy
  would bypass the dynamic-loading behavior under test.
- [x] `tests/test_docstrings_validator.py` — **no safe speedup**. Six adapter
  and staged-byte/profile integration tests pass in 1.10s. The 0.61s case
  exercises the real root validator runner against staged content, and the
  0.46s case validates distinct production/test/base policy profiles.
- [x] `tests/test_duplicate_subcommand_tokens.py` — **no safe speedup**. Its
  1.03s material case invokes the real validator against separate source files
  to prove equal tokens across files do not collide; the process/file boundary
  is the integration contract.
- [x] `tests/test_git_test_repository.py` — **already efficient**. Three
  isolated Git fixture contracts complete in at most 0.02s each.
- [x] `tests/test_install_lifecycle.py` — **already efficient**. Five
  activation/rollback lifecycle cases complete in at most 0.02s each.
- [x] `tests/test_interface_injection_migration.py` — **no safe speedup**. Its
  material 1.05s live-cutover case performs one full repository graph load;
  the remaining cases exercise distinct conversion maps, Git overlays,
  candidate snapshots, symlink defenses, and certification subprocesses.
  Sharing those mutable states would weaken the migration boundaries under
  test.
- [x] `tests/test_interface_projection.py` — **optimized**. Three live-repository
  contract tests now share one module-scoped immutable blueprint graph; the
  only temporary declaration edit uses `monkeypatch` and is restored after its
  test. Eighteen tests still pass, and the three-run median fell from 4.341s to
  2.711s (38% faster).
- [x] `tests/test_legacy_migration.py` — **already efficient**. Legacy
  migration contracts contribute no material slow case in the combined batch.
- [x] `tests/test_migrated_standards_fidelity.py` — **no safe speedup**. Its
  material case validates every canonical standard and verifies generated-view
  freshness; the validator and renderer modules are already loaded once.
- [x] `tests/test_nested_module_migration.py` — **no safe speedup**. The two
  migration suites ran 116 passing cases in 28.25s; this module's material
  cases independently build and mutate Git-backed candidate repositories,
  signed histories, reviewed manifests, and subprocess state. The sole failure
  was the intentional live-repository clean-tree guard in the current dirty
  checkout. Reusing those states would invalidate isolation and determinism
  assertions.
- [x] `tests/test_nested_module_v5_schemas.py` — **already efficient**. Twenty-
  one closed-schema/topology cases contribute at most 0.05s each.
- [x] `tests/test_node_certification_hashing.py` — **no safe speedup**. Hashing,
  route, provenance, and mutation cases construct independent Git-backed node
  states; no case enters the combined profile's top 40.
- [x] `tests/test_node_standards.py` — **optimized**. The test-local YAML loader
  now caches immutable repository standards; no test mutates a loaded document.
  Seventeen tests pass, and the three-run median fell from 3.922s to 1.187s
  (70% faster).
- [x] `tests/test_officina_atomic_files.py` — **already efficient**. The broad
  atomic-write, capability-failure, concurrency, POSIX, and emulated-Windows
  contract set contributes no case above 0.01s in the 199-test utility batch;
  eleven native platform cases follow the standing skip policy.
- [x] `tests/test_officina_blueprint_authorization.py` — **optimized**. Twelve
  read-only authorization cases now share one module-scoped v5 graph; mutation
  cases receive isolated deep copies, and the structurally modified repository
  case remains separate. All 19 tests pass. The three-run median fell from
  2.372s to 0.483s (80% faster).
- [x] `tests/test_officina_blueprint_graph.py` — **no safe material speedup**.
  Fifty graph/topology cases pass in 2.41s. Most create distinct repositories
  or mutate routing, ownership, schema-version, and failure topologies; the two
  0.22s dispatch cases intentionally validate scoped loading and diagnostics.
  Only two 0.11s read-only v5 cases share a fixture shape, which does not
  justify introducing shared graph state into this mutation-heavy module.
- [x] `tests/test_officina_blueprint_template.py` — **optimized**. The
  test-local schema loader now caches read-only bundles by exact requested
  path; temporary schemas remain separate keys and production loading behavior
  is unchanged. All 24 tests pass. The three-run median fell from 6.434s to
  1.629s (75% faster).
- [x] `tests/test_officina_certificate_records.py` — **already efficient**.
  Certificate record parsing/validation contracts contribute no material slow
  case.
- [x] `tests/test_officina_certification_hashing.py` — **no safe speedup**. Two
  0.59s live-repository cases independently verify the selected check registry,
  canonical basis, and validator import coverage.
- [x] `tests/test_officina_certification_view.py` — **no safe speedup**.
  Renewal and repository-view cases use separate signed histories, roots,
  commits, and corruption states; material cases range from 0.33s to 0.61s and
  cannot share mutable certificate history safely.
- [x] `tests/test_officina_dates.py` — **already efficient**. Four in-process
  formatting and normalization checks contribute no measurable slow case.
- [x] `tests/test_officina_famulus_paths.py` — **already efficient**. Six
  platform-path cases use temporary paths and monkeypatched environments and
  contribute no measurable slow case.
- [x] `tests/test_officina_git_provenance.py` — **already efficient**. Sixty-two
  provenance cases remain below 0.21s each in the combined profile; independent
  Git states preserve staged, worktree, symlink, and encoding contracts.
- [x] `tests/test_officina_google_credentials.py` — **no safe speedup**. The
  sole 0.20s case verifies concurrent credential writes do not lose entries;
  all normalization, scope, install, secret, and token cases are below the
  measured threshold.
- [x] `tests/test_officina_install_info.py` — **already efficient**. Two pinned
  install-info parsing/error cases contribute no measurable slow case.
- [x] `tests/test_officina_launcher_entry.py` — **blocked for full local timing**.
  Ordinary launcher cases pass and the real installed-dispatcher boundary takes
  0.31s. Three real-uv integration cases cannot create uv cache temporaries in
  the restricted environment; replacing them with shared/mocked runtimes would
  remove the clean-environment contract.
- [x] `tests/test_officina_managed_runtime.py` — **blocked for full local
  timing**. Mocked release/activation behavior passes; two real-uv integration
  cases hit the same restricted uv-cache boundary. Their isolated release
  roots and real interpreter provisioning are functionality under test.
- [x] `tests/test_officina_oauth_json.py` — **already efficient**. Three atomic
  OAuth JSON creation/replacement/symlink cases contribute no measurable slow
  case and require distinct temporary destinations.
- [x] `tests/test_officina_pooled_blueprint.py` — **no material speedup**. A
  module-scoped Git-backed graph fixture preserved all eight behaviors but did
  not improve timing (three-run median 1.165s before versus 1.228s after), so
  the trial was fully reverted. Per-test repositories retain the clearer
  isolation boundary.
- [x] `tests/test_officina_python_machine_interface.py` — **no safe speedup**.
  Route-smoke cases deliberately exercise isolated child imports, cwd/sys.path
  restoration, nested logical packages, retracing, candidate-local source, and
  descriptor retention; material cases run from 0.21s to 0.44s.
- [x] `tests/test_officina_repository_configuration.py` — **already
  efficient**. Configuration-path, symlink, malformed-input, and central-schema
  cases are at most 0.01s each.
- [x] `tests/test_officina_repository_paths.py` — **already efficient**.
  Lexical containment, alias, symlink, nonexistent, and outside-path cases
  contribute no measurable slow case.
- [x] `tests/test_officina_runtime_pointer.py` — **already efficient**.
  Activation, containment, trusted-interpreter, symlink, and rollback cases
  contribute no measurable slow case and use distinct pointer state.
- [x] `tests/test_officina_secret_store.py` — **already efficient**. Injected
  backends cover ordinary behavior; the optional native-keyring round trip is
  the slowest case at only 0.06s.
- [x] `tests/test_officina_toml_io.py` — **already efficient**. UTF-8,
  validation, Windows-path, filename, and mode cases contribute no measurable
  slow case.
- [x] `tests/test_officina_uv_bootstrap.py` — **already efficient**. Bootstrap
  unit contracts use injected command/download boundaries and contribute no
  material slow case; real provisioning remains in the integration modules.
- [x] `tests/test_process_binding_compiler.py` — **already efficient**. The
  parser/compiler argument matrix is entirely in-process and contributes no
  measurable slow case.
- [x] `tests/test_python_source_cache.py` — **already efficient**. Six focused
  cache-key, parse-failure, and I/O-failure cases contribute no measurable slow
  case; fresh paths are essential to the cache contract.
- [x] `tests/test_repo_checks_entrypoint.py` — **already efficient**. Its one
  real root-entrypoint suite-discovery process completes in 0.07s.
- [x] `tests/test_repository_test_checks.py` — **already efficient**. Canonical
  discovery, platform grouping, and runner-contract cases contribute no
  material slow case in the combined profile.
- [x] `tests/test_repository_validator_checks.py` — **no safe speedup**. The
  staged-mirror runner cases independently verify index isolation, linked and
  split indexes, executable/symlink/non-UTF-8 transport, preflight gating,
  fixture reuse, module isolation, and the session-scoped Python source cache.
  Sharing repositories or validator results would weaken those boundaries.
- [x] `tests/test_runtime_module_test_support.py` — **already efficient**. Two
  package-context execution cases complete in at most 0.02s.
- [x] `tests/test_skill_refactoring_standard.py` — **no material speedup**. A
  cached parsed baseline plus per-call deep copies preserved all nine tests but
  was slower (0.805s baseline versus about 0.924s after), so the trial was
  fully reverted. Fresh documents preserve mutation-test isolation.
- [x] `tests/test_skill_taxonomy_graph.py` — **already efficient**. Taxonomy
  graph contracts contribute no material slow case.
- [x] `tests/test_standard_consumers.py` — **already efficient**. Its consumer
  contract contributes no measurable slow case.
- [x] `tests/test_standard_extractor.py` — **no safe speedup**. Three material
  cases execute different query projections over the validated import closure;
  the mutation case copies and corrupts its own standard tree, while the
  remaining checks are fast.
- [x] `tests/test_standard_query.py` — **no safe speedup**. The material test
  executes five distinct projections to prove every public view retains the
  same validated closure metadata. Reusing a projected result would stop
  exercising those view paths.
- [x] `tests/test_standard_v6.py` — **no safe speedup**. The material 0.35s
  update-standards acceptance case exercises cascading pins, evidence, and
  generated views in one isolated repository; other schema/renderer cases are
  below the combined profile threshold.
- [x] `tests/test_typed_blueprint_schemas.py` — **optimized**. Validators are
  cached by schema name while every mutable test document remains fresh. All
  40 tests pass. The three-run median fell from 1.830s to 0.865s (53% faster).
- [x] `tests/test_v6_tooling_support.py` — **already efficient**. The real v6
  repository writer case completes in 0.08s.
- [x] `tests/test_visualization_browser.py` — **no safe speedup**. Its 2.53s
  end-to-end case batches the desktop interaction matrix into one browser and
  uses a second isolated profile for the distinct mobile viewport contract.
- [x] `tests/test_visualization_containment_edges_browser.py` — **no safe
  speedup**. Two isolated browser cases cover different containment and local
  occlusion geometries; the material case is 1.06s.
- [x] `tests/test_visualization_filtering.py` — **already efficient**. Pure
  filtering contracts contribute no material case to the 63-test, 32.72s
  visualization profile.
- [x] `tests/test_visualization_graph.py` — **already efficient**. Extraction,
  payload, and precomputed-input contracts contribute no material case to the
  combined visualization profile.
- [x] `tests/test_visualization_inspector_and_bezier_browser.py` — **no safe
  speedup**. Its three browser cases deliberately isolate selection, relation,
  and geometry state; each completes in about one second.
- [x] `tests/test_visualization_projection_arrangements_browser.py` — **no safe
  speedup**. Twenty-four separate state-machine arrangements each require a
  fresh browser profile. A module-scoped profile trial both slowed the suite
  and failed six cases through leaked Chrome state, so it was fully reverted.
- [x] `tests/test_visualization_projection_browser.py` — **no safe speedup**.
  Its single 1.03s browser case already batches the module-projection contract.
- [x] `tests/test_visualization_projection_policy.py` — **already efficient**.
  Pure projection-policy cases contribute no material case to the combined
  visualization profile.
- [x] `tests/validate_blueprint_relationships.py` — **already efficient**.
  Independent temporary graph-policy cases complete in at most 0.12s each.
- [x] `tests/validate_blueprints.py` — **already efficient**. V5/V6 preflight,
  staged-tree, and generated-view cases complete in at most 0.13s each.
- [x] `tests/validate_boundaries.py` — **already efficient**. Isolated boundary
  fixtures contribute no material case to the validator-test profile.
- [x] `tests/validate_cross_platform.py` — **no safe speedup**. Its 1.01s
  material case executes the real composite runner; isolated platform,
  symlink, binary, and permission cases complete in at most 0.12s each.
- [x] `tests/validate_dependencies.py` — **already efficient**. Independent
  dependency-policy fixtures contribute no material case to the profile.
- [x] `tests/validate_dispatch_caller_module.py` — **already efficient**. The
  deepest-module and registered-module cases complete in 0.16s and 0.12s.
- [x] `tests/validate_dispatcher_usage.py` — **already efficient**. Isolated
  dispatcher-usage cases contribute no material timing.
- [x] `tests/validate_documentation_validators.py` — **already efficient**.
  Clean-repository validation is 0.46s, and the suite explicitly verifies that
  the user-doc validator constructs its catalog only once.
- [x] `tests/validate_interface_ids.py` — **already efficient**. Independent
  interface-namespace cases complete in at most 0.11s.
- [x] `tests/validate_names.py` — **already efficient**. Name-policy fixtures
  contribute no material case to the profile.
- [x] `tests/validate_personal_info.py` — **already efficient**. Synthetic
  content checks contribute no material case to the profile.
- [x] `tests/validate_platform_neutral.py` — **no safe speedup**. Its 1.66s
  material case invokes the real validator runner and its 0.16s non-Git case
  verifies fail-closed repository discovery.
- [x] `tests/validate_portable_dates.py` — **already efficient**. Date-policy
  fixtures contribute no material case to the profile.
- [x] `tests/validate_skill_body_execution.py` — **already efficient**.
  Independent AST/source cases contribute no material timing.
- [x] `tests/validate_skill_md_dispatch.py` — **already efficient**. Generated
  block and hand-authored-body cases complete in at most 0.11s each.
- [x] `tests/validate_skill_metadata.py` — **already efficient**. Metadata
  fixtures contribute no material case to the profile.
- [x] `tests/validate_skill_runtime_doc_references.py` — **already efficient**.
  Isolated documentation-reference fixtures contribute no material timing.
- [x] `tests/validate_skill_runtime_files.py` — **already efficient**. Runtime
  file-policy fixtures contribute no material case to the profile.
- [x] `tests/validate_skip_hygiene.py` — **already efficient**. Skip-policy
  fixtures contribute no material case to the profile.
- [x] `tests/validate_standard_documents.py` — **no safe test-fixture speedup**.
  Its 2.16s material case performs one complete live canonical-standards and
  generated-view validation. The suite already asserts one prepared V6 schema
  per repository run; mutation cases require separate temporary documents.
- [x] `tests/validate_subprocess_text_encoding.py` — **already efficient**.
  Encoding-policy source fixtures contribute no material case to the profile.
- [x] `tests/validate_toml_io_boundary.py` — **already efficient**. TOML
  boundary fixtures contribute no material case to the profile.

## Canonical validators (27)

- [x] `validators/contributor_docs_contract.py` — **already efficient**. One
  contributor-doc rendering plus required-snippet checks completes in 0.18s
  when called directly.
- [x] `validators/cross_platform.py` — **already optimized**. The canonical
  runner injects both its shared blueprint graph and the session-wide lazy
  Python source/AST cache; direct-call graph loading is not paid there.
- [x] `validators/docstrings.py` — **no safe speedup**. It intentionally uses
  the staged-byte protocol rather than live files, and the canonical runner
  excludes it from tiers where its full policy scan is not required.
- [x] `validators/duplicate_subcommand_tokens.py` — **already optimized**. It
  consumes the canonical runner's single shared blueprint graph; its 1.08s
  direct-call timing primarily reflects graph preparation omitted in-session.
- [x] `validators/generated_skill_docs.py` — **already efficient**. One full
  index rendering and comparison completes in 0.22s when called directly.
- [x] `validators/personal_info.py` — **no safe material speedup**. Its complete
  clean tracked-tree scan has a 0.466s median. A whole-file negative-regex trial
  slowed the median to 0.520s and was fully reverted.
- [x] `validators/platform_neutral.py` — **already optimized**. The canonical
  runner supplies its shared graph; direct-call graph preparation accounts for
  the apparent multi-second standalone timing.
- [x] `validators/portable_dates.py` — **already optimized**. Its pytest entry
  consumes the session-wide lazy Python source/AST cache.
- [x] `validators/readme_user_contract.py` — **already efficient**. One README
  read and bounded snippet scan is below measurable materiality.
- [x] `validators/skill/blueprint_relationships.py` — **already optimized**.
  It consumes one defensive view of the canonical runner's shared graph.
- [x] `validators/skill/blueprints.py` — **already optimized**. It owns the
  single canonical blueprint preflight and passes that graph to other graph
  consumers in the validator session.
- [x] `validators/skill/boundaries.py` — **already efficient**. Its direct
  repository boundary scan completes in 0.12s.
- [x] `validators/skill/dependencies.py` — **already optimized**. It consumes
  the canonical runner's shared graph; standalone graph loading dominates its
  direct-call timing.
- [x] `validators/skill/dispatch_caller_module.py` — **already optimized**.
  Its pytest entry consumes both the shared graph and shared Python source/AST
  cache.
- [x] `validators/skill/dispatcher_usage.py` — **already efficient**. Its
  bounded skill-body scan completes in 0.22s when called directly.
- [x] `validators/skill/interface_ids.py` — **already optimized**. It consumes
  one defensive view of the canonical runner's shared graph.
- [x] `validators/skill/names.py` — **already efficient**. Metadata name checks
  complete in 0.01s when called directly.
- [x] `validators/skill/skill_body_execution.py` — **already efficient**. Its
  hand-authored body scan completes in 0.03s when called directly.
- [x] `validators/skill/skill_md_dispatch.py` — **already optimized**. It uses
  the canonical runner's shared graph; standalone graph loading dominates its
  direct-call timing.
- [x] `validators/skill/skill_metadata.py` — **already efficient**. Frontmatter
  checks complete in 0.02s when called directly.
- [x] `validators/skill_runtime_doc_references.py` — **already optimized**. It
  consumes the canonical runner's shared graph; its remaining reads are the
  documentation-reference surface being validated.
- [x] `validators/skill_runtime_files.py` — **already optimized**. It consumes
  the canonical runner's shared graph and performs only runtime-file policy
  checks on that prepared inventory.
- [x] `validators/skip_hygiene.py` — **already optimized**. Its pytest entry
  consumes the session-wide lazy Python source/AST cache.
- [x] `validators/standard_documents.py` — **no safe speedup**. A repository-
  scan cache was removed because imported-document findings can depend on the
  calling traversal; cache reuse across top-level roots changes cycle
  diagnostics. The prepared V6 schema validator remains shared for one
  repository scan.
- [x] `validators/subprocess_text_encoding.py` — **already optimized**. Its
  pytest entry consumes the session-wide lazy Python source/AST cache.
- [x] `validators/toml_io_boundary.py` — **already optimized**. Its pytest
  entry consumes the session-wide lazy Python source/AST cache.
- [x] `validators/user_docs_cover_blueprints.py` — **already optimized**. Its
  fixture-backed pytest items construct one module-scoped catalog, and its
  direct compatibility API also loads that catalog only once.

## Intentionally outside the canonical runner

- `skills/initialize-tdd/assets/python/tests/test_logger.py` — project scaffold template.
- `references/standards/validate_standard_v6.py` — standalone implementation exercised by canonical tests and validators.
