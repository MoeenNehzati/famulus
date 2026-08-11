# Batch B Ledger

Pass 1 is explanation-only. Commands are to be run from the repository root.

### `skills/g-calendar/_rtx/tests/test_calendar_oauth_transaction.py`

- Canonical task: `pytest -q skills/g-calendar/_rtx/tests`
- Item/behavior summary: protects credential-write transactionality and Calendar bearer-token verification.
- Current pytest features: module loaded once by explicit importlib spec; `tmp_path`, `monkeypatch`, and `pytest.raises`.
- Repeated preparation or process work: each case needs an independently mutable credentials file; no subprocess is used.
- Mutable/global/process boundaries: credential bytes and patched network function are per test; importlib module state is inert for these calls.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12 — per-test files and automatic monkeypatch rollback are the correct ownership.
- Required retained coverage: old credentials survive missing token/verification failure; only persistent fields are written; real request carries Bearer authorization.
- Focused verification command: `pytest -q skills/g-calendar/_rtx/tests/test_calendar_oauth_transaction.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Per-test credential files and monkeypatch rollback are the required transaction boundary; no reusable immutable setup is material.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision against final test code: per-case credential files and monkeypatch ownership remain function-local; no changed suite required focused execution.
- Final state: already-efficient

### `skills/g-calendar/_rtx/tests/test_g_calendar_ensure_oauth.py`

- Canonical task: `pytest -q skills/g-calendar/_rtx/tests`
- Item/behavior summary: covers legacy credential detection and binding a shared Google credential without secrets or field loss.
- Current pytest features: explicit unique runtime load; function-scoped credential fixtures, `tmp_path`, capture, and `raises`.
- Repeated preparation or process work: each registry fixture creates a fresh file-backed credential registry and secret backend; no process startup.
- Mutable/global/process boundaries: credential registry/config writes are stateful and must not cross cases; fixtures return IDs, not shared mutable registries.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12 — scope is already function-local and distinguishes granted from insufficient scope.
- Required retained coverage: configured/missing-client outcomes, credential-ID-only storage, insufficient-scope failure, and unrelated config-field preservation.
- Focused verification command: `pytest -q skills/g-calendar/_rtx/tests/test_g_calendar_ensure_oauth.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Credential/config creation is mutable per case and function scope is already correct.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision against final test code: registry/config setup remains fresh per test and fixtures expose IDs rather than shared mutable state; no changed suite required focused execution.
- Final state: already-efficient

### `skills/g-calendar/_rtx/tests/test_g_calendar_guidance.py`

- Canonical task: `pytest -q skills/g-calendar/_rtx/tests`
- Item/behavior summary: verifies authored process-binding documentation and runtime parser accept every documented gcal mode.
- Current pytest features: one runtime-module load; helper reparses immutable YAML for two tests; no fixtures or subprocesses.
- Repeated preparation or process work: `calendar_interface()` re-reads and parses the same immutable blueprint twice.
- Mutable/global/process boundaries: blueprint and parser are read-only; no CLI execution or network boundary is asserted here.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P01 — module-scoped `calendar_interface` fixture returning the read-only parsed interface; keep parser construction/case assertions local.
- Required retained coverage: all mode names, documented argv shapes, binding selection, and argparse command parsing.
- Focused verification command: `pytest -q skills/g-calendar/_rtx/tests/test_g_calendar_guidance.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: disagree
- Pass 2 rationale: The YAML is parsed twice only, and a module fixture would require fixture injection into both tests without eliminating a material repeated preparation path; retain direct loader clarity.
- Tie-breaker and decision: `/root/skill_test_review` — `already-efficient` (P12). The immutable blueprint is parsed only twice and produces a small mapping; fixture injection into both tests would save one parse while adding lifecycle plumbing, with no material canonical-suite benefit. Keeping `gcal.build_parser()` local preserves the independent authored-binding/runtime-parser check.
- Approved implementation: no change
- Files changed: none
- Focused result: `pytest -q skills/g-calendar/_rtx/tests/test_g_calendar_guidance.py` — `2 passed in 0.06s`.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified tie-break P12 decision: the two direct immutable-YAML reads and local parser construction remain, with both authored-binding and parser assertions intact; no changed suite required focused execution.
- Final state: already-efficient

### `skills/g-calendar/_rtx/tests/test_gcal.py`

- Canonical task: `pytest -q skills/g-calendar/_rtx/tests`
- Item/behavior summary: protects date ranges, parallel calendar aggregation, command payload/output, credential routes, and module help.
- Current pytest features: runtime module loaded once; per-test monkeypatch/capsys/tmp_path; one real `python -m ... --help` subprocess smoke.
- Repeated preparation or process work: fake API payloads are deliberately case-specific; only help crosses an executable/process boundary.
- Mutable/global/process boundaries: patched API/token/executor globals auto-restore; all-calendar concurrency and help-process import/path behavior are material.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12 — direct calls are appropriate for command logic and the executable smoke remains for the module boundary.
- Required retained coverage: empty/no-worker and capped-worker paths, sorted metadata, exact output/payloads, shared/legacy credentials, and help surface.
- Focused verification command: `pytest -q skills/g-calendar/_rtx/tests/test_gcal.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: The test already separates direct command logic from its executable smoke; process coverage cannot be reduced.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: case-local monkeypatch/capture/tmp-path state and the real `python -m` help smoke remain; concurrency and credential assertions are unchanged.
- Final state: already-efficient

### `skills/initialize-tdd/_rtx/tests/test_host_links_interface.py`

- Canonical task: `pytest -q skills/initialize-tdd/_rtx/tests`
- Item/behavior summary: checks interface parser, shared interface loading from skill root, and AGENTS-to-CLAUDE compatibility symlink creation.
- Current pytest features: explicit importlib load; `monkeypatch.chdir` and `tmp_path`; no subprocesses.
- Repeated preparation or process work: three distinct interface paths share no expensive construction.
- Mutable/global/process boundaries: CWD mutation is automatically restored; filesystem alias is isolated to each temporary project.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12 — current function scope protects CWD and filesystem state.
- Required retained coverage: parser accepts project directory, shared runner loads the named interface, and alias target is exactly `AGENTS.md`.
- Focused verification command: `pytest -q skills/initialize-tdd/_rtx/tests/test_host_links_interface.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: CWD/filesystem ownership is function-local and no repeated preparation is safely shareable.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: CWD mutation uses monkeypatch and every filesystem alias uses its own tmp path; no changed suite required focused execution.
- Final state: already-efficient

### `skills/install-assistant-tools/_rtx/tests/test_agent_launch.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: validates agent frontmatter parsing, runtime repo root, and AI/HOME worker-directory routing.
- Current pytest features: `tmp_path` and `monkeypatch`; direct private-function calls.
- Repeated preparation or process work: each minimal agent tree/environment is case-specific and cheap.
- Mutable/global/process boundaries: environment changes auto-restore; no executable boundary is under test.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12 — individual filesystem and environment ownership is correct.
- Required retained coverage: frontmatter/no-frontmatter outputs, repository-relative root, AI override, and Famulus fallback outside Documents.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_agent_launch.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Each case owns files/environment and existing helper setup is not a safe broad fixture.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: frontmatter trees and HOME/AI environment are case-local via tmp_path and monkeypatch; no changed suite required focused execution.
- Final state: already-efficient

### `skills/install-assistant-tools/_rtx/tests/test_claude_github_install.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: health-checks marketplace installation from published GitHub, installed assets/skills, dispatcher bootstrap, hook attachment, and removal.
- Current pytest features: unittest class-level CLI capability skip and one `TemporaryDirectory` lifecycle.
- Repeated preparation or process work: real Claude, GitHub, managed-runtime, and plugin subprocesses are the behavior under test.
- Mutable/global/process boundaries: network/published-default-branch, external CLI, unauthenticated session hook stream, cache/registration persistence, and platform executable discovery are material.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P12 — in-process replacement would erase the published-package and CLI boundaries.
- Required retained coverage: capability skip, GitHub source, cache-not-live path, assets/skills, hook response, unregister semantics, and retained upstream cache policy.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_claude_github_install.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Published-package and command process boundaries are the asserted behavior.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: class capability skip and real published-GitHub/Claude CLI, cache, hook, and removal boundaries remain.
- Final state: no-safe-change

### `skills/install-assistant-tools/_rtx/tests/test_claude_install.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: validates isolated local Claude marketplace/plugin install and uninstall against copied tracked content.
- Current pytest features: unittest capability skip, temporary HOME, helper-created isolated environment and CLI subprocesses.
- Repeated preparation or process work: the single lifecycle test intentionally starts real Claude commands and a packaged installer.
- Mutable/global/process boundaries: CLI registry/cache, temporary HOME, installed checkout, managed runtime, and hook events are material.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P12 — no redundant startup exists; process isolation is the contract.
- Required retained coverage: absent-before/install-visible/remove-absent state, packaged skill/assets, dispatcher availability, and session hook attachment.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_claude_install.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Process isolation is the contract and no redundant startup was identified.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: temporary HOME and real local Claude CLI install/uninstall lifecycle remain process-isolated.
- Final state: no-safe-change

### `skills/install-assistant-tools/_rtx/tests/test_codex_github_install.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: validates published-GitHub Codex marketplace install, prompt-visible skills, and full removal.
- Current pytest features: unittest capability skip; one isolated temporary home/workdir; real Codex subprocesses.
- Repeated preparation or process work: each command observes an evolving external plugin registry and cache.
- Mutable/global/process boundaries: GitHub source, Codex CLI, prompt-input JSON, plugin cache, registration persistence, and executable discovery are material.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P12 — retaining actual CLI processes is mandatory.
- Required retained coverage: no preinstall leakage, installed cache location/content, explicit skill visibility, remove invisibility, and marketplace removal.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_codex_github_install.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Actual CLI package-install execution is material coverage.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: real Codex CLI, prompt JSON, registry/cache, and removal lifecycle boundaries remain.
- Final state: no-safe-change

### `skills/install-assistant-tools/_rtx/tests/test_codex_install.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: exercises the local Codex plugin-install/bootstrap lifecycle and structured Windows command quoting.
- Current pytest features: unittest capability skip, temporary home/workdir, subprocess helper, plus isolated platform monkeypatch test.
- Repeated preparation or process work: the lifecycle is a single real install sequence; subprocesses verify external Codex behavior.
- Mutable/global/process boundaries: Codex registry/cache, managed runtime, generated launchers, prompt-input output, platform shell argv are material.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P12 — replacing CLI calls would weaken package-install coverage.
- Required retained coverage: Windows argv quoting and each install/bootstrap/skill-visibility/remove lifecycle assertion.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_codex_install.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: CLI process behavior is the package-install coverage under test.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: real Codex lifecycle subprocesses and isolated Windows argv-quoting path remain.
- Final state: no-safe-change

### `skills/install-assistant-tools/_rtx/tests/test_dev_link.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: protects development symlink installation, conflict preservation, git hooksPath, shell/registry state, and symlink-home guard.
- Current pytest features: unittest class capability skip, per-test `TemporaryDirectory`, captured stdout, mocks; raw git subprocess for actual config.
- Repeated preparation or process work: every case deliberately builds a disposable git repo/home because `dev_link.run` mutates both; git config is material in its targeted cases.
- Mutable/global/process boundaries: symlink replacement/conflict rules, live-git isolation, sys.modules platform mocks, rc/registry mutations, and OS symlink capability are material.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12 — a broad fixture risks leaking git/home/module state; current setup gives each destructive case an isolated boundary.
- Required retained coverage: dry run, foreign/local conflict preservation, correct-link replacement, codex-home symlink refusal, git checkout detection, POSIX rc and Windows registry routes.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_dev_link.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Git/home/module mutations require the existing isolated setup.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: every destructive git/home/module arrangement remains independently isolated and targeted git subprocess coverage remains.
- Final state: already-efficient

### `skills/install-assistant-tools/_rtx/tests/test_dev_link_hooks.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: verifies generated Claude commands and Codex managed hook blocks, including legacy/block replacement.
- Current pytest features: unittest `setUp` creates fresh temporary home/config for every test.
- Repeated preparation or process work: test-local JSON/config state is mutable and cheap; no subprocesses.
- Mutable/global/process boundaries: hook configuration persistence and replacement are isolated by per-test setup.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12 — setup scope matches mutable config ownership.
- Required retained coverage: registered commands, legacy migration, managed block writing, and idempotent block replacement.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_dev_link_hooks.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Fresh config is the appropriate ownership boundary for persistent hook replacement.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: unittest setUp still creates a fresh temporary configuration for persistent hook replacement cases.
- Final state: already-efficient

### `skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: end-to-end development install/uninstall checks across Claude/Codex homes, launchers, user skills, conflicts, and home restoration.
- Current pytest features: function-scoped `homes`, platform skip, direct calls plus real git/launcher subprocesses, explicit sys.path restoration.
- Repeated preparation or process work: each lifecycle needs a fresh home and fake repo; subprocess calls validate executable launchers/git state.
- Mutable/global/process boundaries: filesystem tree, symlink/copy behavior, git config, executable launcher, platform skip, and import-path mutation are material.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12 — the function fixture already gives correct per-lifecycle ownership; no process conversion is safe.
- Required retained coverage: both host visibility, executable smoke, user-skill/conflict preservation, uninstall roundtrip tree restoration, and explicit sys.path cleanup.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Fresh home/repository and real launcher/git invocations are required lifecycle coverage.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: function-scoped homes and explicit sys.path restoration remain; real git and launcher executable checks are retained.
- Final state: already-efficient

### `skills/install-assistant-tools/_rtx/tests/test_google_onboarding.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: checks ordered cross-skill dispatcher onboarding, binding/partial outcomes, secret redaction, and service failures.
- Current pytest features: function-scoped fake-dispatcher fixtures create an executable per test; `tmp_path` and `raises`.
- Repeated preparation or process work: the fake dispatcher is intentionally an executable because `run_google_onboarding` invokes it through subprocess; response/call logs are case-specific.
- Mutable/global/process boundaries: argv/JSON/stdout/stderr crossing to dispatcher, platform `.bat` launch behavior, and persisted call log are material.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P12 — an in-process fake would not test dispatch serialization or platform executable selection.
- Required retained coverage: ordered status/authorize calls, credential arguments, no dispatch dry run, redaction, Gmail nickname gate, denial, unknown service, and later binding failure.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_google_onboarding.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: The generated dispatcher executable verifies argv/JSON/stdout crossing and platform launcher selection.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: executable fake dispatcher, persisted call log, platform .bat path, and skip/diagnostic cases remain.
- Final state: no-safe-change

### `skills/install-assistant-tools/_rtx/tests/test_install.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: unit-proves phase ordering, managed-runtime failures, dev/plugin mode, optional dependencies, and Google onboarding integration.
- Current pytest features: direct `run()` calls with per-test `tmp_path` and explicit monkeypatched collaborators.
- Repeated preparation or process work: many tests repeat no-op `scaffold.run`/`launchers.run` patches and a call log before varying one phase outcome.
- Mutable/global/process boundaries: monkeypatch restores shared installer modules; tests intentionally mock runtime/network creation while asserting ordering and failure containment.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P05 — function-scoped fixture should install the common scaffold/launchers stubs and return its call log; tests needing different behavior override locally.
- Required retained coverage: phase order/stopping, dry-run non-build, managed pointer failure handling, optional-dependency choice, and nonfatal Google onboarding outcomes.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_install.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: disagree
- Pass 2 rationale: The repeated collaborator patches deliberately vary return/exception behavior; a default fixture would still need local overrides and would hide phase-specific setup rather than remove material work.
- Tie-breaker and decision: `/root/skill_test_review` — `already-efficient` (P12). The repeated patches are not one common environment: tests intentionally select no-op, ordered call recording, nonzero return, or exception behavior for scaffold, dev-link, launchers, managed-runtime, and onboarding phases. A default P05 fixture would be overridden frequently, hide each phase arrangement, and remove no process startup.
- Approved implementation: no change
- Files changed: none
- Focused result: `pytest -q skills/install-assistant-tools/_rtx/tests/test_install.py` — `13 passed, 4 failed in 0.07s`; the four failures reach the real managed-uv bootstrap and fail on blocked DNS before their mocked candidate builder, an existing hermeticity gap unrelated to the rejected scaffold/launcher fixture proposal.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified tie-break P12 decision: phase-specific monkeypatch arrangements remain explicit. The pre-existing managed-uv/DNS hermeticity gap is recorded, but no approved change applies to this file.
- Final state: already-efficient

### `skills/install-assistant-tools/_rtx/tests/test_install_launcher.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: protects generated Unix/Windows dispatcher and agent launchers, interpreter selection, and platform file layout.
- Current pytest features: `tmp_path`, one function-scoped `tmp_repo_root`, `pytest.raises`, and scoped `mock.patch`.
- Repeated preparation or process work: shared root fixture is immutable; remaining temporary binaries/files must be isolated per test.
- Mutable/global/process boundaries: generated executable content, Windows/POSIX launcher contracts, PATH lookup mocks, and filesystem modes are material.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12 — fixture scope already matches immutable path setup and platform cases require isolation.
- Required retained coverage: no embedded checkout/interpreter, resolver invocation, Windows py fallback/error, copy rather than symlink, and tw exclusion.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_install_launcher.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: The existing immutable root fixture and function-local generated files match their ownership.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: immutable tmp_repo_root setup remains scoped and all generated binaries/files stay test-local.
- Final state: already-efficient

### `skills/install-assistant-tools/_rtx/tests/test_install_manifest.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: protects manifest record/replay semantics, install-side recording, and stale/retargeted link cleanup.
- Current pytest features: `tmp_path`, module skip for symlink capability, explicit sys.path/sys.modules restoration; current dirty helper captures argv/stdout/stderr around `uninstall.main()`.
- Repeated preparation or process work: HEAD invoked four uninstall cases in fresh Python subprocesses; current dirty state invokes parser/main in-process, retaining fresh files per case.
- Mutable/global/process boundaries: manifest/home/filesystem are per test; import path/module state is restored. Current code has no subprocess smoke here, but `test_uninstall.py::test_report_lists_actions` retains one.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P07, P12 — current dirty refactor is appropriate only if the retained smoke remains; relative to HEAD it removes redundant subprocesses for manifest semantics.
- Required retained coverage: symlink-capability skip, record/dedupe/forget, dry-run, stale versus user-retargeted links, failed-entry persistence, manifest removal, and an executable uninstall smoke in `test_uninstall.py`.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_install_manifest.py skills/install-assistant-tools/_rtx/tests/test_uninstall.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Current dirty P07 keeps parser/main plus fresh filesystem isolation and `test_uninstall.py::test_report_lists_actions` retains executable smoke; HEAD's repeated interpreter starts were not material to manifest replay.
- Tie-breaker and decision: not-needed
- Approved implementation: retain current dirty P07 helper.
- Files changed: `skills/install-assistant-tools/_rtx/tests/test_install_manifest.py` (pre-existing dirty proposal retained)
- Focused result: `PYTHONPATH=skills/install-assistant-tools/_rtx/tests:src pytest -q skills/install-assistant-tools/_rtx/tests/test_install_manifest.py skills/install-assistant-tools/_rtx/tests/test_uninstall.py` — 25 passed in 0.46s. The recorded ordinary root command currently collection-errors because bare `install_test_utils` is not on `pytest.ini`'s pythonpath.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P07 implementation: both helpers call the real parser/main with context-restored argv/stdout/stderr and fresh tmp_path state; `test_uninstall.py::test_report_lists_actions` retains the real executable smoke. Focused command passed: 25 passed in 0.49s. Root integration recheck shortened validator-rejected docstrings only; same focused pair passed 25 in 0.81s.
- Final state: optimized

### `skills/install-assistant-tools/_rtx/tests/test_install_test_utils.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: verifies `run_command` resolves a command from the supplied environment PATH.
- Current pytest features: one `tmp_path` test and real lightweight platform shell executable.
- Repeated preparation or process work: none; subprocess PATH resolution is the single behavior asserted.
- Mutable/global/process boundaries: executable discovery and supplied environment must remain a real subprocess boundary.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `no-safe-change`
- Pass 1 recommendation and catalog IDs: P12 — direct invocation cannot establish PATH resolution.
- Required retained coverage: Windows/POSIX generated command and output from the passed PATH, not ambient PATH.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_install_test_utils.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Supplied-PATH executable discovery requires a real subprocess.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: supplied-PATH resolution is still exercised by a real lightweight subprocess.
- Final state: no-safe-change

### `skills/install-assistant-tools/_rtx/tests/test_launchers.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: covers agent launcher install selection, worker locations, profile rewrite/preservation, shell/registry default, closure, and platform exceptions.
- Current pytest features: helper `_make_repo`, `tmp_path`, `monkeypatch`, capture, `skip` annotations; direct runtime calls.
- Repeated preparation or process work: most install cases rebuild the same mutable miniature repo tree through `_make_repo(tmp_path)`.
- Mutable/global/process boundaries: each caller mutates its repo/home/bin/config; platform mutations and shell/registry routes must reset automatically.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `propose`
- Pass 1 recommendation and catalog IDs: P04 — provide a function-scoped `make_repo` factory (or retain helper renamed as fixture factory) so template creation is centralized while every invocation copies/builds a fresh tree; do not broaden mutable repo scope.
- Required retained coverage: plugin/development worker roots, Windows copy vs Unix links, user config preservation, rc/registry split, tw skips, closure, output diagnostics.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_launchers.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: disagree
- Pass 2 rationale: `_make_repo(tmp_path)` is already a clear function helper that creates a fresh mutable tree; fixture wrapping/renaming does not remove repeated preparation or safely broaden its lifetime.
- Tie-breaker and decision: `/root/skill_test_review` — `already-efficient` (P12). `_make_repo(tmp_path)` already is the P04-style function factory: every call creates a fresh mutable repository tree and centralizes its template. Wrapping or renaming it as a fixture factory would neither reduce the eleven tree builds nor permit safe broader scope because callers mutate platform-specific files, links, profiles, and registry/rc state.
- Approved implementation: no change
- Files changed: none
- Focused result: `pytest -q skills/install-assistant-tools/_rtx/tests/test_launchers.py` — collection error, `ModuleNotFoundError: No module named 'install_test_utils'`; this is the recorded bare-helper import gap and is intentionally not fixed by this tie-break.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified tie-break P12 decision: `_make_repo(tmp_path)` remains the fresh mutable-tree factory, and platform/rc/registry assertions retain their isolation. The existing bare-helper collection gap is recorded; no approved change applies to this file.
- Final state: already-efficient

### `skills/install-assistant-tools/_rtx/tests/test_link_utils.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: checks link/copy creation and conflict diagnostics preserving local content.
- Current pytest features: `tmp_path` and `capsys`; direct calls.
- Repeated preparation or process work: four tiny independent source/destination arrangements; no meaningful shared setup.
- Mutable/global/process boundaries: each filesystem mutation is function-local; captured diagnostics are exact.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12 — sharing paths would risk overwriting/preservation interaction.
- Required retained coverage: symlink resolve, missing-source skip text, copy content, and existing-copy preservation message.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_link_utils.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Tiny independent filesystem arrangements and exact diagnostics are already isolated.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: four independent filesystem arrangements and exact capsys diagnostics remain test-local.
- Final state: already-efficient

### `skills/install-assistant-tools/_rtx/tests/test_rc_block.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: protects shell managed-block creation, merge/replacement/idempotence, and dry-run output.
- Current pytest features: `tmp_path`, `capsys`, direct calls; sequential writes are intentional inside individual cases.
- Repeated preparation or process work: each test needs a separate mutable rc file and initial content.
- Mutable/global/process boundaries: no global state; file writes and exact captured output must not leak.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P12 — function-local rc files correctly model repeated caller behavior.
- Required retained coverage: non-clobber merge, same-key replacement, no blank-line accumulation, dry-run no-write, and diagnostics.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_rc_block.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Each test requires a distinct mutable rc file; sequential writes are intentional behavior.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P12 decision: each mutable rc file remains function-local and sequential-write/idempotence assertions remain intact.
- Final state: already-efficient

### `skills/install-assistant-tools/_rtx/tests/test_scaffold.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: verifies scaffold launchers, platform behavior, rc state, managed-runtime capability handling, dependency manifest parsing, signing material, and uv target selection.
- Current pytest features: `tmp_path`, `monkeypatch`, `capsys`, `raises`, and explicit parametrization for platform/version cases.
- Repeated preparation or process work: the runtime-dependency writer and temporary repositories produce mutable test-specific artifacts; identical matrix cases already use parametrization.
- Mutable/global/process boundaries: filesystem launchers/rc, platform mocks, certificate store interactions, and package-resolution errors are isolated per test.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P09, P12 — existing parametrization handles repeated cases; broader fixtures would expose mutable repo/config state.
- Required retained coverage: Unix/Windows launcher contracts, dry-run, idempotence, no ambient package install, capability warnings, manifest schema/versions, signing failure modes, and uv platform/machine errors.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_scaffold.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Existing parametrization covers matrix repetition; artifact/platform state remains per test.
- Tie-breaker and decision: not-needed
- Approved implementation: no change (P09/P12).
- Files changed: none
- Focused result: not run; no approved change
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P09/P12 decision: explicit platform/version parametrization and test-local mutable artifacts remain; skip and diagnostic assertions are unchanged.
- Final state: already-efficient

### `skills/install-assistant-tools/_rtx/tests/test_uninstall.py`

- Canonical task: `pytest -q skills/install-assistant-tools/_rtx/tests`
- Item/behavior summary: verifies installed-state cleanup, preservation, purge/dry-run/error paths, platform skips, and report output.
- Current pytest features: function-scoped `installed` factory fixture, symlink/platform skips, captured in-process CLI helper, and one dedicated subprocess helper.
- Repeated preparation or process work: HEAD ran every case as an uninstall subprocess; current dirty state calls `uninstall.main()` with isolated argv/streams and uses a fresh installed tree, while `test_report_lists_actions` remains a subprocess smoke.
- Mutable/global/process boundaries: filesystem state is per test; argv/stdout/stderr are restored by context managers. POSIX shell/Windows registry behavior and executable smoke are retained.
- Pass 1 author: `/root/pass1_batch_b`
- Pass 1 state: `already-efficient`
- Pass 1 recommendation and catalog IDs: P07, P12 — current dirty refactor is the appropriate split versus HEAD provided the report subprocess smoke remains.
- Required retained coverage: symlink/platform skips, all cleanup/preservation/purge/dry-run/error paths, report diagnostics through a real executable, and fresh installed state per case.
- Focused verification command: `pytest -q skills/install-assistant-tools/_rtx/tests/test_uninstall.py skills/install-assistant-tools/_rtx/tests/test_install_manifest.py`
- Pass 2 adjudicator: `/root/pass1_batch_a`
- Pass 2 verdict: agree
- Pass 2 rationale: Current dirty P07 isolates argv/streams and fresh tree per test, while the report test retains an executable smoke; HEAD's repeated startup was not the persistence behavior under test.
- Tie-breaker and decision: not-needed
- Approved implementation: retain current dirty P07 helpers and report subprocess smoke.
- Files changed: `skills/install-assistant-tools/_rtx/tests/test_uninstall.py` (pre-existing dirty proposal retained)
- Focused result: `PYTHONPATH=skills/install-assistant-tools/_rtx/tests:src pytest -q skills/install-assistant-tools/_rtx/tests/test_install_manifest.py skills/install-assistant-tools/_rtx/tests/test_uninstall.py` — 25 passed in 0.46s. The recorded ordinary root command currently collection-errors because bare `install_test_utils` is not on `pytest.ini`'s pythonpath.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_b`
- Pass 3 verdict: pass
- Pass 3 evidence/findings: Verified P07 implementation: function-scoped installed state, context-restored argv/stdout/stderr, platform/symlink skips, and `test_report_lists_actions` real-script smoke remain. Shared focused command passed: 25 passed in 0.49s. Root integration recheck shortened validator-rejected docstrings only; same focused pair passed 25 in 0.81s.
- Final state: optimized
