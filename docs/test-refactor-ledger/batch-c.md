# Batch C Ledger

Pass 1 author: `/root/skill_test_review`. Pass 1 treats the dirty worktree as
the current proposed state and calls out material differences from `HEAD`.

## List manager

### `skills/list-manager/_rtx/tests/test_beautify.py`

- Canonical task: `tests:skills/list-manager/_rtx/tests`
- Item/behavior summary: Unit coverage for finished/open date badges and one executable rendering smoke that ensures `modified` never appears.
- Current pytest features: Plain tests, `tmp_path`, `monkeypatch`, `capsys`; direct helper calls plus one `subprocess.run`.
- Repeated preparation or process work: Six cheap pure calls; the sole subprocess is a distinct end-to-end renderer case.
- Mutable/global/process boundaries: Imported renderer is read-only in pure cases; the subprocess preserves script startup, stdin/stdout serialization, and UTF-8 rendering.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12. Keep the current split; broader fixtures or converting the only executable smoke would not remove material repetition safely.
- Required retained coverage: All state/date cases and the real `_list_beautify.py` process assertion excluding `modified` while showing `completed`.
- Focused verification command: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_beautify.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: The one subprocess is the required renderer boundary; pure badge cases have no material repeated setup.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; current assertions and documented process/platform boundaries remain.
- Final state: `already-efficient`

### `skills/list-manager/_rtx/tests/test_category_cache.py`

- Canonical task: `tests:skills/list-manager/_rtx/tests`
- Item/behavior summary: Protects runtime-loader isolation, cache countdown/refresh behavior, per-list file separation, atomic cache artifacts, and skill guidance.
- Current pytest features: `tmp_path`, `monkeypatch`, a fake-download factory, and repeated `load_runtime_module` calls.
- Repeated preparation or process work: The same `_category_cache.py` package is reloaded for four ordinary tests; only the foreign-`_rtx` restoration test needs a deliberately fresh load.
- Mutable/global/process boundaries: Runtime constants are inert; `cloud_transport.download_list` patches restore automatically and cache files are function-local. The foreign-package test must retain independent `sys.modules` characterization.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P11. Add a module-scoped runtime-module fixture for ordinary consumers; keep the loader-restoration test on its explicit fresh load.
- Required retained coverage: Foreign private-package restoration, separate `tmp_path` cache artifacts, decrement/refresh semantics, download call counts, and SKILL wording.
- Focused verification command: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_category_cache.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `disagree`
- Pass 2 rationale: The runtime loader is specifically under test and the module imports a private package; a shared module would couple cloud-transport patches and obscure restoration without enough repeated work to justify P11.
- Tie-breaker and decision: `/root/pass1_batch_a`, no-safe-change — P12. The private-package/runtime-loader restoration itself is asserted, and the small number of loads does not justify shared import state.
- Approved implementation: no change (P12).
- Files changed: none; no existing Batch C diff in this file.
- Focused result: not run; no implementation approved.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; the tie-break P12 isolation and retained coverage remain.
- Final state: `no-safe-change`

### `skills/list-manager/_rtx/tests/test_get_schema.py`

- Canonical task: `tests:skills/list-manager/_rtx/tests`
- Item/behavior summary: Exercises schema extraction, schema-specific enums, domain-category derivation, existence checks, and document validation.
- Current pytest features: Plain direct-call tests with immutable input dictionaries and explicit exception assertions.
- Repeated preparation or process work: Production calls intentionally reread/resolve the small schema files; bypassing those reads with test fixtures would stop exercising the API behavior under test.
- Mutable/global/process boundaries: Module constants and returned schema data are read-only in tests; no process or ambient-environment boundary exists.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12. Do not test-cache production schema reads; the current direct calls are the contract.
- Required retained coverage: Todo/triage/default distinctions, schema-derived category enums, missing schema/field behavior, and valid/invalid document validation.
- Focused verification command: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_get_schema.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Caching would remove production schema-read behavior, not redundant test preparation.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; direct API assertions retain production schema-read coverage.
- Final state: `already-efficient`

### `skills/list-manager/_rtx/tests/test_lists.py`

- Canonical task: `tests:skills/list-manager/_rtx/tests`
- Item/behavior summary: Broad argv-level coverage for list initialization, reads/filters/sorts, mutations, schema diagnostics, revisions, local/cloud locks, timeouts, and concurrent writers.
- Current pytest features: Function-scoped `todo_file`; `tmp_path`; current dirty diff replaces ordinary subprocesses with a module-global runtime and manual stdio capture; concurrency/timeout cases retain subprocesses.
- Repeated preparation or process work: `HEAD` launched roughly seventy redundant interpreters for ordinary parser/dispatch assertions. The dirty proposal removes that startup work but introduces a mutable test-owned module global and makes focused collection depend on parent `officina` importability.
- Mutable/global/process boundaries: Ordinary calls need reset stdin/stdout/stderr and characterized consecutive-call isolation. Real processes remain mandatory for advisory locks, environment timeout overrides, concurrent writers, cloud-lock coordination, and executable bootstrap.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P07, P11. Keep the in-process direction, but expose the audited runtime through a module fixture rather than a global, preserve ordinary repo-root focused pytest imports, and retain all three process-sensitive tests plus an executable smoke.
- Required retained coverage: Every existing assertion/diagnostic; two consecutive in-process mutations; real local/cloud competing writers; lock timeout via environment; script startup/import behavior.
- Focused verification command: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_lists.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `disagree`
- Pass 2 rationale: Current dirty code uses a mutable module global and removes all executable smoke coverage. It does retain the lock/concurrent-writer processes, but that does not satisfy P07's required smoke for ordinary CLI parser/startup; do not accept or extend it.
- Tie-breaker and decision: `/root/pass1_batch_a`, implement — P07/P11. Replace the test-owned runtime global with fresh in-process module loading per ordinary call and retain a real `_yaml_store.py` executable smoke; each repeated call is thereby isolated.
- Approved implementation: replace dirty global helper; retain lock/concurrency subprocesses and add executable smoke.
- Files changed: `skills/list-manager/_rtx/tests/test_lists.py`.
- Focused result: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_lists.py` — 70 passed in 3.36s.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: fresh `load_runtime_module()` call per ordinary invocation, executable smoke, lock-timeout, and local/cloud concurrent-writer subprocess nodes remain. Root integration fix shortened six docstrings without changing behavior or process boundaries; focused recheck: 70 passed.
- Final state: `optimized`

### `skills/list-manager/_rtx/tests/test_python_machine_interfaces.py`

- Canonical task: `tests:skills/list-manager/_rtx/tests`
- Item/behavior summary: End-to-end process-runner coverage for the schema and beautifier Python machine interfaces.
- Current pytest features: A subprocess helper with a minimal environment, explicit cwd, strict UTF-8, captured stdin/stdout/stderr.
- Repeated preparation or process work: Only two distinct interfaces run once each.
- Mutable/global/process boundaries: Process startup, package loading by the production runner, cwd-relative gateway resolution, strict encoding, and stdin/stdout serialization are the tested contract.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P12. In-process replacement would erase the machine-interface process boundary.
- Required retained coverage: Both gateway/class pairs, minimal inherited environment, strict UTF-8, schema YAML output, and beautifier stdin rendering.
- Focused verification command: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_python_machine_interfaces.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: These tests protect Python-machine-interface process contracts, so process replacement is unsafe.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; both Python machine-interface process tests and strict encoding boundary remain.
- Final state: `no-safe-change`

### `skills/list-manager/_rtx/tests/test_read_beautify.py`

- Canonical task: `tests:skills/list-manager/_rtx/tests`
- Item/behavior summary: Integration coverage for the read-to-render bridge across bullet, diff, table, filtering, sorting, IDs, and relative deadlines.
- Current pytest features: Function-scoped dated YAML fixture and a fresh bridge subprocess per case with strict UTF-8.
- Repeated preparation or process work: Seven process launches are visible, but each bridge invocation itself launches and serializes through the reader and renderer children.
- Mutable/global/process boundaries: The contract is orchestration across three processes, including exit propagation and YAML/text serialization; date-relative input remains function-local.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P12. Direct calls would no longer test the bridge's subprocess pipeline.
- Required retained coverage: All output modes, filters/sort forwarding, default/no-ID behavior, relative deadline semantics, and strict process encoding.
- Focused verification command: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_read_beautify.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: The read-to-render bridge is an intentional subprocess pipeline rather than redundant setup.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; three-process bridge, output cases, and strict encoding remain.
- Final state: `no-safe-change`

### `skills/list-manager/_rtx/tests/test_skill_contract.py`

- Canonical task: `tests:skills/list-manager/_rtx/tests`
- Item/behavior summary: Resolves the two update exports and checks blueprint descriptions against the authored mutation-patch contract.
- Current pytest features: The dirty proposal adds a module-scoped repository-graph fixture; both consumers are read-only.
- Repeated preparation or process work: `HEAD` rebuilt and validated the full repository graph twice; current state builds it once per worker.
- Mutable/global/process boundaries: The graph and export declarations are only read; worker-local lifetime is sufficient and no process behavior is tested.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P02. Approve the current module fixture after adjudication.
- Required retained coverage: Both canonical interface resolutions and every exact blueprint/SKILL mutation-format assertion.
- Focused verification command: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_skill_contract.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: The current dirty module-scoped graph fixture returns a read-only graph to two contract lookups; it matches P02 and does not share mutable consumers.
- Tie-breaker and decision: `not-needed`
- Approved implementation: accept existing P02 fixture; no further edit.
- Files changed: `skills/list-manager/_rtx/tests/test_skill_contract.py` (pre-existing accepted proposal).
- Focused result: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_skill_contract.py` — 2 passed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: module-scoped `repository_graph` is read-only for both consumers; focused command: 2 passed.
- Final state: `optimized`

### `skills/list-manager/_rtx/tests/test_validation.py`

- Canonical task: `tests:skills/list-manager/_rtx/tests`
- Item/behavior summary: Exhaustive JSON-Schema cases for entries, todo actions, triage actions, full todo/triage/default documents, dates, states, children, and fixed domain categories.
- Current pytest features: Class-grouped plain tests and helpers that repeatedly parse the same immutable schemas/fixtures and construct equivalent resolvers.
- Repeated preparation or process work: Entry/action/triage schema loaders and full-list fixture/schema loads repeat across many methods without mutation.
- Mutable/global/process boundaries: Loaded schema dictionaries and fixture documents must be treated read-only; any test that mutates a document needs a function-scoped deep copy.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P01, P03. Provide module-scoped immutable parsed-schema baselines, but construct each potentially stateful `RefResolver` and each mutable document from a function-scoped factory/copy.
- Required retained coverage: All existing error strings/cases, schema-specific enums, recursive children, date formats, extra fields, and exact domain subcategory constraints.
- Focused verification command: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_validation.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `disagree`
- Pass 2 rationale: The class `_load` methods deliberately couple schema and a fresh `RefResolver`; the broad immutable-baseline/copy-factory rewrite would touch many tests without a demonstrated safe resolver reset.
- Tie-breaker and decision: `/root/pass1_batch_a`, no-safe-change — P12. Each load deliberately creates a matching fresh resolver; no immutable-baseline/copy-factory safety proof exists.
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no implementation approved.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; fresh resolver construction and all schema diagnostics remain.
- Final state: `no-safe-change`

## Math dependency graph

### `skills/math-dependency-graph/_rtx/tests/test_graph_builder.py`

- Canonical task: `tests:skills/math-dependency-graph/_rtx/tests`
- Item/behavior summary: Pure transformation cases for derived categories, caller catalogs, and explicit entity categories.
- Current pytest features: Three small direct-call tests with independent mutable input dictionaries.
- Repeated preparation or process work: None material; inputs differ semantically and construction is trivial.
- Mutable/global/process boundaries: Production returns copied structures; keeping per-test inputs avoids accidental mutation coupling.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12. Parametrization would only compress prose, not preparation.
- Required retained coverage: Absent catalog derivation, caller-catalog non-invention, and explicit-category preservation.
- Focused verification command: `python3 -m pytest -q skills/math-dependency-graph/_rtx/tests/test_graph_builder.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Cases exercise distinct graph shapes; parametrization would only hide test intent.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; distinct mutable graph inputs and direct assertions remain.
- Final state: `already-efficient`

### `skills/math-dependency-graph/_rtx/tests/test_graph_server.py`

- Canonical task: `tests:skills/math-dependency-graph/_rtx/tests`
- Item/behavior summary: Parser boundary tests for invalid low/high TCP ports and the maximum valid port.
- Current pytest features: Parametrized invalid cases and a runtime module loaded into a test-owned module global.
- Repeated preparation or process work: Runtime loading occurs once already; the issue is lifecycle ownership rather than repeated work.
- Mutable/global/process boundaries: Parser calls are read-only and create fresh parsers; server startup/socket binding is intentionally not invoked.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P11. Replace the mutable module global with a module-scoped fixture while retaining existing parametrization.
- Required retained coverage: Both invalid endpoints, nonzero `SystemExit`, and acceptance of port 65535 without binding a socket.
- Focused verification command: `python3 -m pytest -q skills/math-dependency-graph/_rtx/tests/test_graph_server.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `disagree`
- Pass 2 rationale: Three parser-only calls do not justify a fixture, and replacing the loaded module global is cosmetic rather than a safe preparation reduction.
- Tie-breaker and decision: `/root/pass1_batch_a`, no-safe-change — P12. Replacing a three-call parser module global with fixture injection is cosmetic and not a preparation reduction.
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no implementation approved.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; parser cases and no-bind boundary remain.
- Final state: `no-safe-change`

### `skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py`

- Canonical task: `tests:skills/math-dependency-graph/_rtx/tests`
- Item/behavior summary: Recursive TeX macro extraction plus CLI artifact writing and complete graph-renderer macro generation/merge.
- Current pytest features: `unittest.TestCase`, temporary copied fixture trees, two subprocess CLIs, JSON/artifact assertions.
- Repeated preparation or process work: The two subprocesses cover different executable entrypoints and artifact pipelines.
- Mutable/global/process boundaries: Filesystem copies isolate generated `_build` state; process startup, CLI JSON serialization, recursive includes, and renderer output are material.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P12. Preserve both end-to-end executable cases.
- Required retained coverage: Recursive/mid-document macros, default macro path, JSON stdout, generated macro/HTML files, and caller macro precedence in rendered HTML.
- Focused verification command: `python3 -m pytest -q skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Both cases validate external executable extraction/rendering behavior.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; both executable artifact pipelines remain.
- Final state: `no-safe-change`

## PDF to Markdown

### `skills/pdf-to-markdown/_rtx/tests/test_skill_contract.py`

- Canonical task: `tests:skills/pdf-to-markdown/_rtx/tests`
- Item/behavior summary: Parser defaults and explicit output-directory handling for the arXiv source-fetcher interface.
- Current pytest features: Two direct parser tests; each manually reconstructs the same runtime module.
- Repeated preparation or process work: Identical importlib module load is repeated; network/extraction code is not run.
- Mutable/global/process boundaries: The source-fetcher module has only imports/classes/functions and fresh parser construction; consumers do not mutate it.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P11. Use a module-scoped source-fetcher fixture.
- Required retained coverage: Omitted output defaults to `.`, explicit output is preserved, and both parse through `Interface.build_parser()`.
- Focused verification command: `python3 -m pytest -q skills/pdf-to-markdown/_rtx/tests/test_skill_contract.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `disagree`
- Pass 2 rationale: There are only two parser calls; module loading is part of the isolated source-file interface check and no measurable repetition is established by the ledger.
- Tie-breaker and decision: `/root/pass1_batch_a`, no-safe-change — P12. Two parser checks do not warrant a shared module lifecycle.
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no implementation approved.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; isolated parser-load cases remain.
- Final state: `no-safe-change`

## Recurring tasks

### `skills/recurring-tasks/_rtx/tests/test_assistant_desktop_notify.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Cross-platform notification dispatch/fallbacks, quoting, legacy log messages, logging, and CLI parsing/return behavior.
- Current pytest features: Repeated runtime loads, `mock.patch`, temporary files/directories, mocked subprocess results, and direct `main()` calls.
- Repeated preparation or process work: `_assistant_desktop_notify.py` is loaded for nearly every test although patched attributes restore and tests pass explicit log paths.
- Mutable/global/process boundaries: Low-level notification subprocesses are intentionally mocked; module sharing is safe only because `_ensure_linux_gui_env` is patched in tests that could mutate `os.environ`. Temporary logs remain function-local.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P11. Use a module fixture after characterizing two consecutive patched calls and confirming no environment mutation leaks.
- Required retained coverage: Linux/macOS/Windows dispatch, fallback command shapes, quoting, five-error cap, every log assertion, CLI diagnostics, and zero return on notification failure.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_assistant_desktop_notify.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `disagree`
- Pass 2 rationale: The module's platform dispatch and subprocess collaborators are repeatedly patched across many cases; no existing fixture proves two calls restore all module/global behavior, so P11 is premature.
- Tie-breaker and decision: `/root/pass1_batch_a`, no-safe-change — P12. Cross-platform dispatch tests patch module/environment collaborators too broadly for an audited shared module.
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no implementation approved.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; patched cross-platform collaborators and environment isolation remain.
- Final state: `no-safe-change`

### `skills/recurring-tasks/_rtx/tests/test_enable_disable.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: CLI-level enable/disable persistence, unknown/exact-name failure, and isolation of sibling job records.
- Current pytest features: `HEAD` used fresh processes and manual named-temp cleanup; dirty proposal uses a test-owned module global and direct `main()` for every case.
- Repeated preparation or process work: Five interpreter startups and repeated temp-file boilerplate are removable, but the dirty proposal retains no executable smoke.
- Mutable/global/process boundaries: Job YAML must be function-local; consecutive mutations must not share parser/module state. One real runtime-module/script process must retain startup, exit-code, and persisted-file coverage.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P04, P07, P11. Use `tmp_path` plus a module fixture/direct-call helper for ordinary cases, remove the mutable global, and retain at least one subprocess smoke. The current dirty diff is incomplete against the plan invariants.
- Required retained coverage: Enable and disable persistence, untouched sibling, exact-name rejection, nonzero unknown-job exit, two consecutive calls, and one executable process.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_enable_disable.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `disagree`
- Pass 2 rationale: Current dirty code replaces every executable invocation with a mutable global module and omits the mandatory subprocess smoke; the helper also leaves output/error behavior uncharacterized.
- Tie-breaker and decision: `/root/pass1_batch_a`, implement — P04/P07/P11. Use `tmp_path` and fresh in-process runtime loading for ordinary calls, then retain an executable `_job_control.py` smoke; this removes the global and preserves startup coverage.
- Approved implementation: replace dirty global helper; use tmp_path and retain one executable smoke.
- Files changed: `skills/recurring-tasks/_rtx/tests/test_enable_disable.py`.
- Focused result: `python3 -m pytest -q --import-mode=importlib skills/recurring-tasks/_rtx/tests/test_enable_disable.py` — 6 passed in 0.21s after replacing all manual named-temp cleanup with function-scoped `tmp_path` files. The recorded default import mode collection-errors on `_rtx.tests.test_enable_disable`.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: all five ordinary cases now use function-scoped `tmp_path` job files; fresh `load_runtime_module()` calls and the real `_job_control.py` executable smoke remain. `python3 -m pytest -q --import-mode=importlib skills/recurring-tasks/_rtx/tests/test_enable_disable.py` — 6 passed in 0.25s.
- Final state: `optimized`

### `skills/recurring-tasks/_rtx/tests/test_ensure_agent_env.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Linux environment-file generation, dry-run, absence of legacy shell output, unreachable systemd manager, and derived session environment.
- Current pytest features: Direct imported runtime, `tmp_path`, `monkeypatch` platform/environment changes, and mocked systemctl subprocesses.
- Repeated preparation or process work: No repeated expensive setup; each filesystem/environment case is distinct.
- Mutable/global/process boundaries: Platform and environment patches restore automatically; home-scoped output must stay function-local; no real systemd mutation occurs.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P05, P12. Existing monkeypatch ownership is appropriate.
- Required retained coverage: Linux-only paths, dry-run non-write, systemctl-unreachable behavior, explicit session bus/runtime environment, and no legacy `_agent_env.sh`.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_ensure_agent_env.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Existing monkeypatch ownership properly restores environment and platform state.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P05/P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; monkeypatch restores platform/environment and artifacts remain function-local.
- Final state: `already-efficient`

### `skills/recurring-tasks/_rtx/tests/test_healthcheck.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Healthcheck preflight, scheduler drift, logs/run records, freshness/in-flight logic, schedule parsing, main reporting, and exit status.
- Current pytest features: A fresh runtime load per test with redirected module globals, temporary directories, extensive `mock.patch`, and direct calls.
- Repeated preparation or process work: Runtime loading repeats, but `_load()` intentionally rewrites `LOG_DIR`, `HEALTHCHECK_LOG`, and `JOBS_FILE` for every mutable case.
- Mutable/global/process boundaries: These module globals and log/run-record files are mutated; broad sharing risks cross-test contamination and order dependence.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P12. Retain function-scoped fresh modules unless Pass 2 first proves a reset factory restores every redirected global and artifact.
- Required retained coverage: All scheduler/environment diagnostics, stale/fresh/in-flight records, manual/cron logging, disabled jobs, load failures, schedule parser cases, and exact return codes.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_healthcheck.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Healthcheck module state and artifacts require its existing fresh-load isolation.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; fresh runtime globals and artifacts remain per test.
- Final state: `no-safe-change`

### `skills/recurring-tasks/_rtx/tests/test_healthcheck_environment_invariance.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Proves systemd session-environment derivation, unit PATH independence, registration consistency, and a live probe's invariant verdict under rich versus poor invocation environments.
- Current pytest features: `tmp_path`, `monkeypatch`, real rendered units, a subprocess probe, and platform-aware helpers.
- Repeated preparation or process work: Rich/poor environment setup is intentionally duplicated as the comparison under test.
- Mutable/global/process boundaries: Environment inheritance, executable search, separate-process import/bootstrap, and unit registration paths are material.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P05, P12. Preserve the paired environments and live subprocess probe.
- Required retained coverage: Usable/unusable runtime dirs, derived DBus values, unit-owned PATH, registration agreement, and identical verdict lines across invocation environments.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_healthcheck_environment_invariance.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Paired environments and live subprocess probing are the tested invariant.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P05/P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; paired environments and live subprocess probe remain.
- Final state: `no-safe-change`

### `skills/recurring-tasks/_rtx/tests/test_job_control_contract.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Two explicit command variants verify the machine interface honors a custom jobs file without scheduler synchronization.
- Current pytest features: Parametrization with explicit cases, `tmp_path`, and direct interface invocation.
- Repeated preparation or process work: Minimal shared shape already expressed by parametrization; each case needs its own mutable YAML artifact.
- Mutable/global/process boundaries: Function-local jobs file; scheduler side effects are deliberately disabled.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P09, P12. Keep current parametrization and isolation.
- Required retained coverage: Both enable and disable commands, custom-file mutation, and no-sync behavior.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_job_control_contract.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Existing explicit parametrization preserves distinct contract examples with readable IDs.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P09/P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; explicit parametrization IDs and function-local YAML remain.
- Final state: `already-efficient`

### `skills/recurring-tasks/_rtx/tests/test_job_executor.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Command parsing/resolution, process execution, run boundaries/status contracts, spawn failure, kill/timeout behavior, stale status rejection, tolerated exits, and log rotation.
- Current pytest features: Direct imported modules, `tmp_path`, mock/monkeypatch, one bootstrap subprocess, and real child processes for execution/kill/timeout cases.
- Repeated preparation or process work: Setup is already function-local and process launches correspond to behavior, not harness overhead.
- Mutable/global/process boundaries: Preserve no-`PYTHONPATH` bootstrap, child exit status/output, killed executor leaving in-flight state, timeout termination, timestamps, and filesystem persistence.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12. Keep unit-level direct calls and process-sensitive cases as separated now.
- Required retained coverage: Every command/platform parsing case, executable resolution, run/log/status record lifecycle, spawn/kill/timeout failures, stale/current statuses, success contracts, and rotation cap.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_job_executor.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Direct cases and the signal/timeout process cases are already separated by their material boundaries.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; signal/timeout/bootstrap process boundaries remain.
- Final state: `already-efficient`

### `skills/recurring-tasks/_rtx/tests/test_linux_registration_check.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Linux registration diagnostics for missing service files and service/timer content drift.
- Current pytest features: Direct backend calls, small context/job factories, and `tmp_path` artifacts.
- Repeated preparation or process work: None material.
- Mutable/global/process boundaries: Unit directory is function-local; no live systemd calls are made.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12. Keep direct filesystem checks.
- Required retained coverage: Missing service message and simultaneous service/timer drift diagnostics.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_linux_registration_check.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Direct isolated unit-directory checks have no repeated expensive setup.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; function-local unit artifacts and diagnostics remain.
- Final state: `already-efficient`

### `skills/recurring-tasks/_rtx/tests/test_manage_job.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Function and CLI behavior for jobs load/save, enable/disable sync routing, immediate-test polling, logs, status, sync, parser dispatch, and errors.
- Current pytest features: Repeated runtime loads, manual named-temp cleanup, `mock.patch`, `capsys`, and direct `main()` calls; executable enable/disable coverage lives in the companion file.
- Repeated preparation or process work: `_job_control.py` is loaded for nearly every test despite only constants/functions and restored patches; temp-file boilerplate also repeats.
- Mutable/global/process boundaries: `JOBS_FILE`, `LOG_DIR`, backend functions, time, and run-record readers are patched and must restore; YAML/log state must remain per test. Companion subprocess smoke must remain.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P04, P11. Use a module-scoped runtime fixture plus `tmp_path` factories, after consecutive-call characterization; do not absorb the companion executable smoke.
- Required retained coverage: All subcommand routing/exit codes, exact jobs-file passthrough, polling freshness/run-id collision, timeout, scheduler rejection/job failure, log tail/status output, and restored module patches.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_manage_job.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `disagree`
- Pass 2 rationale: Repeated fresh loads protect import-time paths and subprocess collaborators; proposed P11 does not include the required consecutive-call state characterization.
- Tie-breaker and decision: `/root/pass1_batch_a`, no-safe-change — P12. Import-time paths and patched runtime collaborators are central; no safe consecutive-call characterization accompanies the proposal.
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no implementation approved.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; import-time paths and patched collaborators remain isolated.
- Final state: `no-safe-change`

### `skills/recurring-tasks/_rtx/tests/test_no_posix_entrypoints.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Enforces no shell runtime interfaces/files and verifies generated services execute Python without POSIX shell syntax.
- Current pytest features: Blueprint/file-tree inspection and one `tmp_path` unit-generation case.
- Repeated preparation or process work: None material.
- Mutable/global/process boundaries: Repository inventory is read-only; generated unit files are function-local; platform-neutral policy is the contract.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12. Keep explicit policy assertions.
- Required retained coverage: Blueprint languages/process bindings, absence of `.sh`/shell files, and shell-free generated service content.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_no_posix_entrypoints.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Static policy assertions are already minimal and deliberate.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; static policy checks and function-local generated unit remain.
- Final state: `already-efficient`

### `skills/recurring-tasks/_rtx/tests/test_schedule_backend.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Cross-platform backend selection, cron conversion, Linux units, launchd plists, Windows wrappers/tasks, resolver deployment, cleanup, and registration/search-path policy.
- Current pytest features: Direct backend imports, extensive `tmp_path` and mock/monkeypatch use, parametrized systemd calendar cases, platform skip for live `systemd-analyze`, and explicit Windows/macOS/Linux simulations.
- Repeated preparation or process work: Similar-looking cases encode different native scheduler commands and platform semantics; existing parametrization covers the genuinely identical calendar shape.
- Mutable/global/process boundaries: Preserve native line endings/quoting, platform path rules, mocked command sequences, optional real `systemd-analyze` subprocess, resolver artifacts, and per-test unit directories.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P09, P10, P12. Current lifecycle and platform annotations are appropriate.
- Required retained coverage: Every backend command/content assertion, cleanup/idempotency, CRLF and percent quoting, resolver fallbacks/errors, platform defaults, and systemd capability skip annotation.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_schedule_backend.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Platform-specific command and quoting cases are not identical setup; existing parameterization/skip handling is correct.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P09/P10/P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; platform-specific cases, existing parametrization IDs, and skip remain.
- Final state: `already-efficient`

### `skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Opt-in native Linux/macOS/Windows scheduler smoke tests that install, fire, observe, replace stale state, and clean up real scheduler entries.
- Current pytest features: Platform/capability skips, temporary resolver/job/marker artifacts, real subprocess commands, polling timeouts, and `finally` cleanup.
- Repeated preparation or process work: Every command and wait belongs to the live native contract; this is not ordinary unit-test overhead.
- Mutable/global/process boundaries: Real systemd/launchd/Task Scheduler state, process environment, wall-clock scheduling, marker persistence, timeouts, stale-label replacement, and cleanup are indispensable.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P08, P10, P12. Preserve opt-in gating, live processes, polling, and cleanup.
- Required retained coverage: Native scheduler firing on each OS, macOS stale prior-location replacement, resolver preflight, marker/log diagnostics, bounded waits, and verified deletion/unload in cleanup.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Native scheduler state, polling, and finally cleanup are entirely material live-test work.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P08/P10/P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; opt-in platform skips, live processes, and cleanup remain.
- Final state: `no-safe-change`

### `skills/recurring-tasks/_rtx/tests/test_setup_runner.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Healthcheck cron rendering/install/migration/idempotency, setup delegation, crontab read classification, and refusal to overwrite unreadable crontabs.
- Current pytest features: Direct imported module, `tmp_path`, `monkeypatch`, and scoped `mock.patch` for crontab/backend calls.
- Repeated preparation or process work: Small per-test command strings and mocks; no repeated expensive load or filesystem template.
- Mutable/global/process boundaries: Crontab writes are mocked; function-local directories and patch contexts prevent ambient scheduler mutation.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P05, P12. Keep current direct/mocked lifecycle.
- Required retained coverage: Resolver/env/notification cron content, preservation/migration/idempotency, setup handoffs, absent versus unreadable crontab diagnostics, and no write after read failure.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_setup_runner.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Small per-case mocked crontab setups are already function-isolated and not a shared expensive template.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P05/P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; monkeypatch/mock cleanup and function-local artifacts remain.
- Final state: `already-efficient`

### `skills/recurring-tasks/_rtx/tests/test_sync_units.py`

- Canonical task: `tests:skills/recurring-tasks/_rtx/tests`
- Item/behavior summary: Cron conversion and offline unit generation, enabled/disabled/multiple jobs, shell-free executor content, orphan removal, and idempotency.
- Current pytest features: Repeated runtime loads, manual temporary files/directories, one special logical-path/chdir loader test, and monkeypatch of launcher lookup.
- Repeated preparation or process work: `_unit_writer.py` is reloaded in conversion tests and every `_run_sync`; equivalent jobs YAML and unit-directory setup repeats.
- Mutable/global/process boundaries: `DEFAULT_UNIT_DIR` is import-time/platform derived, so the logical-path test needs an independent load. Ordinary offline sync uses explicit jobs/unit/log paths; launcher patch restores automatically.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P04, P11. Share an audited module fixture for ordinary calls and use `tmp_path` factories; retain the special fresh-load/chdir case.
- Required retained coverage: All cron conversions/errors, enabled/disabled/multiple generation, persistent/calendar content, shell-free stable resolver, orphan removal, idempotency, and logical-path absolute `SKILL_DIR`.
- Focused verification command: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_sync_units.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `disagree`
- Pass 2 rationale: The ordinary calls share import-time platform/unit constants with the special logical-path case; the proposed mixed fixture/factory has no proof it cannot couple these paths.
- Tie-breaker and decision: `/root/pass1_batch_a`, no-safe-change — P12. The special logical-path import case and path-bearing unit state make mixed fixture lifetime unsafe without a copy/rebind proof.
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no implementation approved.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; logical-path fresh load and path-bearing unit isolation remain.
- Final state: `no-safe-change`

## Skill certifier

### `skills/skill-certifier/_rtx/tests/test_certifier.py`

- Canonical task: `tests:skills/skill-certifier/_rtx/tests`
- Item/behavior summary: End-to-end certification invariants covering v4/v5 payloads, keys/logs, pooled review, exact targets, atomic writes, races, concurrency, postorder/dependency audits, route audits, mechanical gates, and public CLI selection.
- Current pytest features: One loaded certifier module; function-local Git repositories; shared fixture helpers; `tmp_path`, monkeypatch, parametrized race/atomic cases, threads/barriers, and filesystem mode checks.
- Repeated preparation or process work: `create_v4_repository` repeats substantial Git/schema/graph/hash setup, but 37 callers then mutate worktree/index/HEAD, certificates, keys, logs, callbacks, and concurrency state in materially different ways.
- Mutable/global/process boundaries: Fresh Git identity/commit/index/worktree, exact bytes/modes, append-only predecessor state, signing keys, race timing, thread concurrency, and post-append rederivation are core security contracts.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P12. Do not broaden the mutable repository fixture without a separately proven clone/template design; current per-test construction is the safe default.
- Required retained coverage: Every target/version/gate finding, atomic fallback, byte/mode provenance, pre/post-append races, concurrent writers, key rotation, dependency order, route audit batching/mismatch, mechanical gate ordering, and public CLI fail-closed behavior.
- Focused verification command: `python3 -m pytest -q skills/skill-certifier/_rtx/tests/test_certifier.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `agree`
- Pass 2 rationale: Fresh repositories, keys, logs, races, and concurrent writers are core security contracts; broad reuse is unsafe.
- Tie-breaker and decision: `not-needed`
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no test code changed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; mutable repositories, keys, races, and concurrency boundaries remain.
- Final state: `no-safe-change`

## Skill drift

### `skills/skill-drift/_rtx/tests/test_drift_check.py`

- Canonical task: `tests:skills/skill-drift/_rtx/tests`
- Item/behavior summary: Exact drift/hash/status reports, schema delegation, non-atomic fallback, installed-plugin discovery/deduplication, unsupported schemas, and remediation diagnostics.
- Current pytest features: Test-owned runtime-module global, repeated certified-repository helper, `tmp_path`, monkeypatch/capsys, parametrized schema/plugin commands, and shared certification fixtures.
- Repeated preparation or process work: Seven `_certified` calls rebuild the same Git/schema/graph/hash/key/certificate baseline before tests apply isolated mutations or queries.
- Mutable/global/process boundaries: Certified repositories, public keys/logs, plugin registries/caches, and environment roots must be copied per test; checker patches/environment must restore. Graph nodes are read unless a test constructs an explicit variant.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P04, P11. Replace the mutable checker global with a module fixture and evaluate a `tmp_path_factory` certified template plus per-test copy/factory; rebind/reload path-bearing graph state after each copy.
- Required retained coverage: Exact current/stale reasons, read-only CLI outputs, schema 5/6 delegation, non-atomic propagation, canonical key location, child/global IDs, active-plugin registry selection/deduplication, malformed/unsupported remediation, and v4 rejection after cutover.
- Focused verification command: `python3 -m pytest -q skills/skill-drift/_rtx/tests/test_drift_check.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `disagree`
- Pass 2 rationale: The proposed template requires rebinding path-bearing graph/certificate state after copying, but no safe copy factory or baseline-immutability characterization exists.
- Tie-breaker and decision: `/root/pass1_batch_a`, no-safe-change — P12. Certified baseline copying would need graph/certificate rebinding and baseline-immutability evidence not present here.
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no implementation approved.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; certified baselines and checker state remain isolated.
- Final state: `no-safe-change`

## Skill maker

### `skills/skill-maker/_rtx/tests/test_blueprint_tools.py`

- Canonical task: `tests:skills/skill-maker/_rtx/tests`
- Item/behavior summary: Canonical blueprint template/sync loading, generated contract/interface/runtime-dependency views, facade bindings, consumer update planning, deterministic blocks, and no dispatch-state side effects.
- Current pytest features: Function-scoped `syncer` runtime fixture, `tmp_path`, monkeypatch, copied v5/managed-skill trees, and direct calls.
- Repeated preparation or process work: The same `_blueprint_syncer.py` module is loaded for every fixture consumer; test repositories remain intentionally function-local and mutable.
- Mutable/global/process boundaries: `REPO_ROOT`, `SKILLS_ROOT`, runtime-dependency path, and environment are monkeypatched and restored; generated graphs/documents are mutated in some tests, so only the module—not repository/graph results—may be shared.
- Pass 1 author: `/root/skill_test_review`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P11. Widen only `syncer` to module scope after a consecutive-patch restoration characterization; keep copied repositories and blueprints function-scoped.
- Required retained coverage: v5/v6 loading, facade access source, missing gateway-use finding, check/refresh cycle, dependency closure/descendant IDs, consumer placement/shared-gateway error, deterministic blocks, and no XDG dispatch state.
- Focused verification command: `python3 -m pytest -q skills/skill-maker/_rtx/tests/test_blueprint_tools.py`
- Pass 2 adjudicator: `/root/pass1_batch_b`
- Pass 2 verdict: `disagree`
- Pass 2 rationale: Every consumer patches `syncer` path roots and some mutate graph declarations; the missing consecutive-patch characterization makes module scope unsafe.
- Tie-breaker and decision: `/root/pass1_batch_a`, no-safe-change — P12. Consumers patch path roots and mutate graph declarations; module sharing is not established safe.
- Approved implementation: no change (P12).
- Files changed: none.
- Focused result: not run; no implementation approved.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_c`
- Pass 3 verdict: `pass`
- Pass 3 evidence/findings: Verified: no Batch C test diff; patched roots and mutable graph declarations remain function-isolated.
- Final state: `no-safe-change`
