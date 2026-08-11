# Batch A Ledger

Pass 1 is explanation-only. Commands run from the repository root.

## Cloud files

### `skills/cloud-files/_rtx/tests/test_cloud_files.py`

- Canonical task: cloud-files gateway unit contract.
- Item/behavior summary: validates path rejection, CLI routing, LLM-root selection, copy bytes, configuration, and shared/legacy token paths.
- Current pytest features: `unittest`; nested `mock.patch`; per-case `TemporaryDirectory`; in-process `main()` calls.
- Repeated preparation or process work: identical immutable `CloudFilesConfig` construction and temporary config trees recur; no subprocess startup.
- Mutable/global/process boundaries: patched module globals/stdin/stdout and filesystem are per case; remote/HTTP are mocked; no material child-process boundary.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `propose`.
- Pass 1 recommendation and catalog IDs: P01/P03: add a read-only config fixture plus function-scoped path/config factory; never share mock state or temp files.
- Required retained coverage: every routing call, byte-content assertion, config default, and shared-vs-legacy credential branch.
- Focused verification command: `pytest skills/cloud-files/_rtx/tests/test_cloud_files.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `disagree`.
- Pass 2 rationale: `CloudFilesConfig` is a frozen three-field dataclass and constructing it is negligible beside the mocked behavior under test; pytest fixtures would require converting or bridging a `unittest.TestCase`, while the temporary config trees are mutation-specific and must remain isolated. P01 does not apply because these cases do not repeatedly parse configuration, and P03 would not produce a meaningful canonical-suite saving.
- Tie-breaker and decision: `/root/pass1_batch_b` — `already-efficient` (P12). `CloudFilesConfig` construction is not parsed-artifact work and is negligible; each temporary config/credential tree is mutable test state. A fixture conversion would add cross-style plumbing to `unittest.TestCase` without safely removing a material lifecycle or process startup.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `16 passed` in the five-file Cloud Files focused run (`32 passed in 0.64s` total for the group).
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed the tie-break P12 decision: `unittest` cases retain per-case `TemporaryDirectory` and patch isolation; no changed code requires a focused rerun.
- Final state: `already-efficient`.

### `skills/cloud-files/_rtx/tests/test_cloud_files_ensure_oauth.py`

- Canonical task: cloud-files OAuth-ensure and shared-credential configuration contract.
- Item/behavior summary: protects noninteractive status, config merge/dry-run behavior, Drive-scope admission, and credential-id persistence.
- Current pytest features: function `tmp_path`; `capsys`; two credential fixtures; explicit file-path module loading.
- Repeated preparation or process work: each credential test constructs a fresh secret backend/registry; that registry is deliberately mutable.
- Mutable/global/process boundaries: unique import name avoids `sys.modules` collision; config/secret registry and stdout are per-test; no subprocess boundary.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `already-efficient`.
- Pass 1 recommendation and catalog IDs: P12: function scope correctly isolates mutable credentials and config; broader scope would leak registry state.
- Required retained coverage: dry-run non-write, preserved custom fields, insufficient-scope rejection, and merge-after-credential regression.
- Focused verification command: `pytest skills/cloud-files/_rtx/tests/test_cloud_files_ensure_oauth.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: the mutable credential registry and config artifacts are function-scoped by design; broadening them could leak authorization or merge state, and there is no repeated process startup to remove.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `8 passed` in the five-file Cloud Files focused run (`32 passed in 0.64s` total for the group).
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed function-scoped mutable credential registry/config and unique module loading remain intact; no changed code requires a focused rerun.
- Final state: `already-efficient`.

### `skills/cloud-files/_rtx/tests/test_oauth_transaction.py`

- Canonical task: OAuth credential persistence transaction unit contract.
- Item/behavior summary: verifies failed token/verification leaves prior credentials byte-identical, success persists only durable fields, and Drive bearer header formation.
- Current pytest features: `tmp_path`, `monkeypatch`, in-process imported module, fake URL response.
- Repeated preparation or process work: small repeated old-credential setup is intentionally file-local and mutation-specific; no startup work.
- Mutable/global/process boundaries: each case mutates a distinct credential file and temporary patched verifier/URL opener; no real network/process.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `already-efficient`.
- Pass 1 recommendation and catalog IDs: P12: factory `old_credentials` already removes only safe repetition while retaining independent transaction files.
- Required retained coverage: pre-write failure preservation, successful field projection, and exact bearer authorization request.
- Focused verification command: `pytest skills/cloud-files/_rtx/tests/test_oauth_transaction.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: each test needs an independent credential file to prove transactionality; the existing immutable payload helper already removes safe duplication without sharing mutation state.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `4 passed` in the five-file Cloud Files focused run (`32 passed in 0.64s` total for the group).
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed independent temporary credential files and function-scoped patches remain; no changed code requires a focused rerun.
- Final state: `already-efficient`.

### `skills/cloud-files/_rtx/tests/test_script_entrypoints.py`

- Canonical task: copied cloud-files wrapper executable integration contract.
- Item/behavior summary: runs copied wrapper scripts against a stub gateway to protect stdin/stdout, imports via `PYTHONPATH`, CLI error rendering, and file round trips.
- Current pytest features: `unittest`, `TemporaryDirectory`, copied scripts/stub module, `subprocess.run`.
- Repeated preparation or process work: each scenario repeats isolated temp script tree/environment; startup is the behavior being tested.
- Mutable/global/process boundaries: material executable, import-resolution, environment, stdin/stdout/stderr, and filesystem boundaries. Current dirty diff replaces the post-delete list subprocess (HEAD) with direct store inspection.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `no-safe-change`.
- Pass 1 recommendation and catalog IDs: P12: retain subprocesses; the current direct post-delete assertions are acceptable only because pre-delete list and missing-read executable paths remain, but do not convert further wrapper calls.
- Required retained coverage: real copied executable smoke for every wrapper, list glob output, delete/rm behavior, and sanitized missing-file error.
- Focused verification command: `pytest skills/cloud-files/_rtx/tests/test_script_entrypoints.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: executable startup, copied-script import resolution, environment, streams, and sanitized errors are material. The current dirty diff removes only redundant post-delete observation processes; pre-delete list execution, delete execution, missing-read error rendering, and direct persisted-artifact deletion checks retain the distinct contracts.
- Tie-breaker and decision: not needed.
- Approved implementation: accept the current two direct post-delete artifact assertions; make no further subprocess conversion.
- Files changed: `skills/cloud-files/_rtx/tests/test_script_entrypoints.py` (accepted existing dirty proposal).
- Focused result: `2 passed` in the five-file Cloud Files focused run (`32 passed in 0.64s` total for the group); retained copied-wrapper subprocess nodes passed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: `pytest skills/cloud-files/_rtx/tests/test_script_entrypoints.py`: `2 passed in 0.39s`. Diff replaces only post-delete list observations with direct store-existence assertions; copied wrapper, pre-delete listing, delete, and missing-read subprocess coverage remain.
- Final state: `optimized`.

### `skills/cloud-files/_rtx/tests/test_setup_oauth.py`

- Canonical task: OAuth setup input/parser unit contract.
- Item/behavior summary: validates installed-client JSON extraction and authorization URL Drive scope/state encoding.
- Current pytest features: `unittest`, per-test `TemporaryDirectory`, in-process module import.
- Repeated preparation or process work: two compact independent cases; one creates a mutable client file.
- Mutable/global/process boundaries: temporary JSON file only; no external network or subprocess.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `already-efficient`.
- Pass 1 recommendation and catalog IDs: P12: no material repetition; a broader fixture would obscure the distinct file-input case.
- Required retained coverage: installed JSON extraction and URL scope/client/state parameters.
- Focused verification command: `pytest skills/cloud-files/_rtx/tests/test_setup_oauth.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: only two distinct cases exist, and the temporary JSON input is specific to one; fixture extraction would not remove material work.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `2 passed` in the five-file Cloud Files focused run (`32 passed in 0.64s` total for the group).
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed the two distinct in-process cases retain their mutable file boundary; no changed code requires a focused rerun.
- Final state: `already-efficient`.

## Connect Google

### `skills/connect-google/_rtx/tests/test_authorize_services.py`

- Canonical task: loopback OAuth authorization service contract.
- Item/behavior summary: protects scopes/PKCE, result shape, partial grants, no-write failures, account matching, and dynamic default network/browser resolution.
- Current pytest features: `tmp_path`, `monkeypatch`, explicit fake secret backend, fake HTTP, real local loopback callback thread.
- Repeated preparation or process work: client/backend/fake-exchange setup repeats but each authorization writes mutable registry/secret state.
- Mutable/global/process boundaries: material local HTTP loopback and threading boundary; remote OAuth is fake; registry state must remain function-scoped.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `already-efficient`.
- Pass 1 recommendation and catalog IDs: P12: factories express variants without sharing mutable backend or registry; no in-process/process saving is available.
- Required retained coverage: real loopback callback, PKCE omission of verifier, persisted scopes, no credential after state/account failure, and no-real-network default path.
- Focused verification command: `pytest skills/connect-google/_rtx/tests/test_authorize_services.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: the live loopback callback and per-case mutable registry/backend state are behavior under test; existing factories already isolate variants without redundant child-process startup.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: initial sandboxed group run blocked six loopback cases with `PermissionError: [Errno 1] Operation not permitted`; rerun with localhost binding permitted: `7 passed in 0.06s`.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed real local-loopback/thread and function-scoped mutable registry/backend boundaries remain; no changed code requires a focused rerun.
- Final state: `already-efficient`.

### `skills/connect-google/_rtx/tests/test_client_config.py`

- Canonical task: shared Google client configuration unit contract.
- Item/behavior summary: validates payload rejection, secret extraction/storage, permissions, idempotence/replacement, and canonical/legacy discovery.
- Current pytest features: parametrization with explicit case data, `tmp_path`, platform conditional, fake secret backend, path helpers.
- Repeated preparation or process work: desktop payload and JSON writer helpers remove repeated immutable structure; each install mutation has independent files/backend.
- Mutable/global/process boundaries: credential files and fake secret storage are mutation-sensitive; POSIX permission assertion is platform conditional; no subprocess.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `already-efficient`.
- Pass 1 recommendation and catalog IDs: P12: current helpers/parametrization are appropriate; a broad fixture risks coupling sequential install cases.
- Required retained coverage: recursive token rejection, no-write malformed input, redaction, 0600 POSIX permission, and conflict/legacy paths.
- Focused verification command: `pytest skills/connect-google/_rtx/tests/test_client_config.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: helpers already share immutable payload shape, while each install/replace case needs isolated files and secret storage; broader state would alter mutation and permission semantics.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `18 passed` in the four-file Connect Google focused run; all non-loopback files contributed `34 passed` before the sandbox-only authorization failure.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed explicit parametrization and platform-conditional POSIX permission coverage retain function-local filesystem and fake-secret state; no changed code requires a focused rerun.
- Final state: `already-efficient`.

### `skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py`

- Canonical task: Connect Google blueprint and authored-routing contract.
- Item/behavior summary: checks module/export graph, declared I/O/flag patterns, and user-facing routing text/non-leakage.
- Current pytest features: YAML loaders and structural helpers; no fixtures, subprocesses, or temporary paths.
- Repeated preparation or process work: root/child YAML files are repeatedly parsed through helper calls; returned mappings are only inspected.
- Mutable/global/process boundaries: repository blueprint/Markdown files are immutable inputs during test; no runtime process boundary.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `propose`.
- Pass 1 recommendation and catalog IDs: P01: module-scoped read-only loader/cache fixture may parse each path once, but only if it is fixture-owned (not a test global cache) and exposes no mutable mapping.
- Required retained coverage: exact export/use-edge sets, flag values, declared reads, route wording, and forbidden dispatcher/secret text.
- Focused verification command: `pytest skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: repository blueprints are immutable test inputs and the same paths are parsed repeatedly. A module-scoped fixture-owned cache with deep-copy results satisfies P01/P03 without exposing mutable aliases or changing graph assertions.
- Tie-breaker and decision: not needed.
- Approved implementation: replace direct YAML loads with a module-scoped loader fixture that parses each path once and returns independent copies; retain all test node IDs and assertions.
- Files changed: `skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py`.
- Focused result: characterization `6 passed in 0.09s`; post-change `6 passed in 0.08s`; included again in the Connect Google group run.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: `pytest skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py`: `6 passed in 0.10s`. Module-scoped fixture owns the parsed cache and returns `deepcopy` values; graph, flags, and authored-text assertions retain their nodes.
- Final state: `optimized`.

### `skills/connect-google/_rtx/tests/test_service_delegation.py`

- Canonical task: cross-skill Google delegation/export contract.
- Item/behavior summary: verifies access restrictions, service gateway edges, opaque credential handoff, route guidance, and retained setup exports.
- Current pytest features: recursive YAML loader/export resolver; current dirty diff adds test-owned `@lru_cache` to `load`.
- Repeated preparation or process work: the same immutable blueprint paths are parsed repeatedly across structural assertions.
- Mutable/global/process boundaries: repository YAML/Markdown are read-only; cached dicts would be mutable aliases. HEAD reparsed on each call; current code's global cache violates the plan's no-test-owned-cache invariant.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `propose`.
- Pass 1 recommendation and catalog IDs: P01/P03: replace current `lru_cache` proposal with module-scoped immutable parsed representation or a read-only fixture plus copy factory; retain recursive facade resolution.
- Required retained coverage: all access/uses-interface checks, credential argument/outcome wording, exact calendar edges, and guidance text.
- Focused verification command: `pytest skills/connect-google/_rtx/tests/test_service_delegation.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: repeated repository blueprint parsing is safely shareable, but the current dirty module-global `lru_cache` returns mutable aliases and violates the plan invariant. Fixture-owned cached canonical values plus deep-copy results preserve recursive resolution without cross-test mutation.
- Tie-breaker and decision: not needed.
- Approved implementation: replace the current `lru_cache` with a module-scoped pytest loader fixture/factory returning independent copies; thread that loader through the resolver helpers.
- Files changed: `skills/connect-google/_rtx/tests/test_service_delegation.py`.
- Focused result: characterization `9 passed in 0.16s`; post-change `9 passed in 0.18s`; included again in the Connect Google group run.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: `pytest skills/connect-google/_rtx/tests/test_service_delegation.py`: `9 passed in 0.17s`. The module fixture replaces the prohibited global cache, returns copies, and is threaded through recursive export resolution; all structural and guidance assertions remain.
- Final state: `optimized`.

## Daily plan

### `skills/daily-plan/_rtx/tests/test_blueprint_platform_support.py`

- Canonical task: Daily Plan runtime blueprint platform-support contract.
- Item/behavior summary: checks all three runtime blueprints expose dependencies and source support on macOS and Windows.
- Current pytest features: two parametrized tests with explicit path IDs; YAML parsing per invocation.
- Repeated preparation or process work: each of three immutable YAML files is parsed twice.
- Mutable/global/process boundaries: repository YAML is read-only; no process/platform execution, only declarative platform policy.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `propose`.
- Pass 1 recommendation and catalog IDs: P01: module fixture can parse each immutable blueprint once; consumers must not mutate payloads.
- Required retained coverage: all three filenames, dependency presence, and both macOS/Windows true assertions.
- Focused verification command: `pytest skills/daily-plan/_rtx/tests/test_blueprint_platform_support.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: each immutable blueprint is parsed by both parametrized contracts. A fixture-owned cache returning copies removes the duplicate YAML parse while preserving separate dependency and platform-support node IDs and diagnostics.
- Tie-breaker and decision: not needed.
- Approved implementation: add a module-scoped loader fixture/factory that parses each blueprint once and returns independent copies to both tests.
- Files changed: `skills/daily-plan/_rtx/tests/test_blueprint_platform_support.py`.
- Focused result: characterization `6 passed in 0.08s`; post-change `6 passed in 0.06s`; Daily Plan focused group `18 passed in 0.15s`.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: `pytest skills/daily-plan/_rtx/tests/test_blueprint_platform_support.py`: `6 passed in 0.08s`. Module-scoped loader owns immutable YAML parsing and returns copies; explicit parametrized filename IDs and both platform assertions remain.
- Final state: `optimized`.

### `skills/daily-plan/_rtx/tests/test_dispatch_contract.py`

- Canonical task: Daily Plan dispatcher metadata unit contract.
- Item/behavior summary: verifies forced orchestrate target, selected pattern, and final command flag.
- Current pytest features: one direct resolver call; repository configuration path.
- Repeated preparation or process work: none.
- Mutable/global/process boundaries: reads repository config and imports dispatcher metadata; no subprocess or mutable test state.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `already-efficient`.
- Pass 1 recommendation and catalog IDs: P12: single focused assertion has no reusable preparation.
- Required retained coverage: exact target, `pattern_1`, and `--forced` command suffix.
- Focused verification command: `pytest skills/daily-plan/_rtx/tests/test_dispatch_contract.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: this is one direct resolver call with no reusable setup or process startup; extraction would only add indirection.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `1 passed` in the Daily Plan focused group (`18 passed in 0.15s`).
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed the single direct resolver assertion still protects target, pattern, and `--forced` suffix; no changed code requires a focused rerun.
- Final state: `already-efficient`.

### `skills/daily-plan/_rtx/tests/test_plan_runtime.py`

- Canonical task: Daily Plan runtime/state mutation unit contract.
- Item/behavior summary: protects date normalization, section/meta mutations, plan rendering, state-patch CLI adapter, and successful-write status semantics.
- Current pytest features: runtime-module loader, function `monkeypatch`, `capsys`, `tmp_path`, local copy lambdas.
- Repeated preparation or process work: `_state_patch`/`_plan_storage` modules are reloaded for tests that patch module state; baseline day model loads once.
- Mutable/global/process boundaries: module globals and imported runtime registries are material; monkeypatch and fresh runtime loads prevent leakage; no child process.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `already-efficient`.
- Pass 1 recommendation and catalog IDs: P12: do not broaden runtime loads without a state audit; each patched writer/state directory remains isolated.
- Required retained coverage: copied meta mutation, emitted state-patch stdout, master-list side effect, and status-write failure non-propagation.
- Focused verification command: `pytest skills/daily-plan/_rtx/tests/test_plan_runtime.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: fresh runtime-module loads isolate patched globals and writers. Sharing them would risk order dependence and change the state-mutation contract.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `11 passed` in the Daily Plan focused group (`18 passed in 0.15s`).
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed fresh runtime-module loaders, `monkeypatch`, `capsys`, and per-test paths retain module-global and writer-state isolation; no changed code requires a focused rerun.
- Final state: `already-efficient`.

## Email client

### `skills/email-client/_rtx/tests/test_accounts.py`

- Canonical task: email accounts executable integration contract.
- Item/behavior summary: executes account CLI commands for registration, validation, credential use, and persistence.
- Current pytest features: function `tmp_path` fixture, subprocess helper with isolated environment, JSON registry reader; current dirty diff replaces selected follow-up `resolve`/`list` subprocesses with direct registry reads.
- Repeated preparation or process work: every test gets fresh config; mutation tests often launch a command then a pure observation command.
- Mutable/global/process boundaries: material executable, environment (`EMAIL_CLIENT_CONFIG_DIR`, PATH, PYTHONPATH), and persisted registry boundaries; fresh config is essential. Current diff must be judged against HEAD's CLI-observation coverage.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `propose`.
- Pass 1 recommendation and catalog IDs: P07: retain the current direct registry inspection only for mutation outcome checks, while preserving existing dedicated executable `list` and `resolve` smoke/format cases; characterize two command invocations only if replacement expands.
- Required retained coverage: isolated env, every mutating command exit/diagnostic, at least one `list` and `resolve` real CLI output contract, and all credential redaction/scope cases.
- Focused verification command: `pytest skills/email-client/_rtx/tests/test_accounts.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: the current dirty P07 diff removes only follow-up observation launches after a real mutating command. Dedicated executable `list` success, `resolve` success/default projection, and `resolve` failure/diagnostic tests remain, as do every mutating command, isolated environment, persistence, secret-stdin, and platform-path boundary.
- Tie-breaker and decision: not needed.
- Approved implementation: accept the current `read_registry` helper and selected direct persisted-artifact assertions; do not replace dedicated `list`/`resolve` executable coverage.
- Files changed: `skills/email-client/_rtx/tests/test_accounts.py` (accepted existing dirty proposal).
- Focused result: `19 passed` in the Email Client focused group (`71 passed in 2.12s`); retained `list`, `resolve`, mutation, environment, persistence, and secret-stdin subprocess nodes passed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: `pytest skills/email-client/_rtx/tests/test_accounts.py`: `19 passed in 2.05s`. Function-scoped config/keyring fixtures remain; changed mutation observations read only `accounts.json`, while executable `list`, `resolve`, mutation, failure, stdin-secret, and POSIX-skip coverage remain. Root integration shortened three overlong docstring summaries without changing assertions or behavior; recheck `pytest skills/email-client/_rtx/tests`: `71 passed in 2.04s`.
- Final state: `optimized`.

### `skills/email-client/_rtx/tests/test_mail.py`

- Canonical task: email mail/SMTP/IMAP functional unit contract.
- Item/behavior summary: covers MIME read/body/filter behavior, parser/request construction, secret lookup, SMTP/TLS/XOAUTH2, delivery, and mocked transport failures.
- Current pytest features: imported runtime modules, `monkeypatch`, small fake clients, `tmp_path`, in-process parser/function calls.
- Repeated preparation or process work: message/envelope/fake-client helpers already capture reusable shape; cases intentionally vary protocol state.
- Mutable/global/process boundaries: patched SMTP/secret/network collaborators and temporary attachment files are function-local; no real network/subprocess.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `already-efficient`.
- Pass 1 recommendation and catalog IDs: P12: helpers are stateless and broader fixtures would conceal transport-sequence differences.
- Required retained coverage: exact MIME/header/attachment outputs, filter fallback, secret preference, TLS order, XOAUTH2 string, and delivery cleanup/error paths.
- Focused verification command: `pytest skills/email-client/_rtx/tests/test_mail.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: existing stateless helpers remove safe repetition, while test-local clients preserve protocol call ordering and failure variants; no child-process work remains to optimize.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `41 passed` in the Email Client focused group (`71 passed in 2.12s`).
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed stateless helpers and function-local mocked transport/attachment state retain protocol sequencing and failure coverage; no changed code requires a focused rerun.
- Final state: `already-efficient`.

### `skills/email-client/_rtx/tests/test_oauth_tokens.py`

- Canonical task: Gmail OAuth-token unit contract.
- Item/behavior summary: validates client JSON extraction, secret storage, refresh request encoding, XOAUTH2, shared credential route, legacy fallback, error wrapping, and exchange failure.
- Current pytest features: `tmp_path`, `monkeypatch`, fake context-managed response, in-process calls.
- Repeated preparation or process work: compact fake response and literal account payloads; no repeated process startup.
- Mutable/global/process boundaries: monkeypatched secret store/credential refresher/URL opener are per test; no real network or subprocess.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `already-efficient`.
- Pass 1 recommendation and catalog IDs: P12: fixture broadening offers no safe shared mutable state; existing doubles retain exact request assertions.
- Required retained coverage: secret keys, POST body/timeout, shared-route precedence, fallback, wrapped credential error, and missing access-token failure.
- Focused verification command: `pytest skills/email-client/_rtx/tests/test_oauth_tokens.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: request/secret/credential doubles are function-local because cases vary failure and precedence behavior; fixture broadening would not remove material setup and could share mutable calls.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `8 passed` in the Email Client focused group (`71 passed in 2.12s`).
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed function-local monkeypatched secret/credential/network doubles retain exact request and failure assertions; no changed code requires a focused rerun.
- Final state: `already-efficient`.

### `skills/email-client/_rtx/tests/test_smoke.py`

- Canonical task: email live-smoke adapter unit contract.
- Item/behavior summary: checks IMAP NOOP/logout, SMTP auth NOOP/context closure, and explicit self-send delivery request.
- Current pytest features: imported runtime module, `monkeypatch`, per-test fake connectors/clients/deliverer.
- Repeated preparation or process work: none beyond minimal test-local recording fakes.
- Mutable/global/process boundaries: simulated transport/context-manager boundaries are material; no real process/network.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `already-efficient`.
- Pass 1 recommendation and catalog IDs: P12: separate fakes make protocol sequencing legible and isolate call logs.
- Required retained coverage: NOOP/logout, auth-before-NOOP, context exit, and exact self-send recipient/subject/body.
- Focused verification command: `pytest skills/email-client/_rtx/tests/test_smoke.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: minimal test-local fakes make IMAP, SMTP, context-manager, and delivery sequencing explicit; there is no repeated expensive preparation.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `3 passed` in the Email Client focused group (`71 passed in 2.12s`).
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed per-test fakes still expose IMAP/SMTP/context-manager and self-send sequencing; no changed code requires a focused rerun.
- Final state: `already-efficient`.

## Email triage

### `skills/email-triage/_rtx/tests/test_fetch_filtered_envelopes.py`

- Canonical task: composite filtered-envelope runtime contract.
- Item/behavior summary: validates dispatch arguments, privacy filtering, empty result text, non-leaking dispatch/JSON failures, and declared interface boundary.
- Current pytest features: runtime-module reload helper, `tmp_path`, `capsys`, per-case fake Interface subclasses, `CompletedProcess` doubles.
- Repeated preparation or process work: state-isolation setup recurs in two stateful cases; module reload prevents altered nested globals from leaking.
- Mutable/global/process boundaries: dispatcher result boundary is simulated; envelope-gate state globals are patched on a fresh module; raw content confidentiality is material.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `already-efficient`.
- Pass 1 recommendation and catalog IDs: P12: retain fresh module/state isolation; a broad fixture risks nested module-global leakage.
- Required retained coverage: dispatch args, filtered-only stdout, all raw-payload non-leak errors, and interface ownership declaration.
- Focused verification command: `pytest skills/email-triage/_rtx/tests/test_fetch_filtered_envelopes.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: fresh module state is required where nested envelope-gate globals are patched, and existing completed-process doubles already avoid real dispatcher startup without losing privacy/error behavior.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `6 passed` in the Email Triage focused group (`37 passed in 1.03s`).
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed fresh module state and completed-process doubles retain privacy/non-leak and dispatch-error boundaries; no changed code requires a focused rerun.
- Final state: `already-efficient`.

### `skills/email-triage/_rtx/tests/test_filter_envelopes.py`

- Canonical task: envelope-gate watermark/filter CLI and unit contract.
- Item/behavior summary: protects watermark parsing/status repair, conservative filtering, isolated command behavior, and state-directory default/override.
- Current pytest features: module import, `monkeypatch`, `tmp_path`, subprocess helper with isolated environment.
- Repeated preparation or process work: subprocesses cover command entrypoint and env inheritance; isolated state setup is mutation-specific.
- Mutable/global/process boundaries: material executable/stdin/stdout/stderr/environment and persistent state-file boundaries; module globals are patched per test.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `no-safe-change`.
- Pass 1 recommendation and catalog IDs: P12: preserve CLI subprocess cases and function-scoped state; no direct-call conversion is safe for the environment/stream contract.
- Required retained coverage: cutoff boundary, 24-hour warning, no-date conservatism, status mutation, and explicit state-directory override.
- Focused verification command: `pytest skills/email-triage/_rtx/tests/test_filter_envelopes.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: the subprocess cases directly cover executable stdin/stdout/stderr, environment inheritance, and state-directory behavior; function-scoped paths prevent persistent watermark leakage.
- Tie-breaker and decision: not needed.
- Approved implementation: no change; retain all subprocess nodes.
- Files changed: none.
- Focused result: `13 passed` in the Email Triage focused group (`37 passed in 1.03s`); retained CLI subprocess nodes passed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed all executable stdin/stdout/stderr, environment, and state-directory subprocess nodes remain, with function-scoped paths; no changed code requires a focused rerun.
- Final state: `no-safe-change`.

### `skills/email-triage/_rtx/tests/test_watermark.py`

- Canonical task: watermark/failure executable persistence contract.
- Item/behavior summary: executes four script adapters to protect run-id idempotence, error blocking/clearing, persistence across invocations, and state-dir defaults.
- Current pytest features: subprocess helper, `tmp_path`, parametrized module default/override checks, dynamic module loader.
- Repeated preparation or process work: multi-invocation subprocesses are required to prove persisted state and command invocation boundaries.
- Mutable/global/process boundaries: material executable, environment, persistent state files, timestamps, and cross-invocation state; no safe in-process replacement.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `no-safe-change`.
- Pass 1 recommendation and catalog IDs: P12: retain every command sequence; function scope correctly isolates mutable state.
- Required retained coverage: same-run-id non-advance, no-id advance, failure block, clear recovery, two-run persistence, and all four state-dir module routes.
- Focused verification command: `pytest skills/email-triage/_rtx/tests/test_watermark.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: repeated command invocations are necessary to establish cross-process persistence, idempotence, failure blocking, and recovery; sharing state would invalidate those temporal claims.
- Tie-breaker and decision: not needed.
- Approved implementation: no change; retain every subprocess sequence.
- Files changed: none.
- Focused result: `18 passed` in the Email Triage focused group (`37 passed in 1.03s`); retained multi-invocation persistence subprocess sequences passed.
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed multi-invocation executable and persistent-state sequences, plus parametrized state-dir routes, remain; no changed code requires a focused rerun.
- Final state: `no-safe-change`.

## Find handoff candidates

### `skills/find-handoff-candidates/_rtx/tests/test_parsers.py`

- Canonical task: host parser and aggregation unit contract.
- Item/behavior summary: validates Claude/Codex home overrides/defaults, project/session extraction, resume syntax, and aggregate parser interface.
- Current pytest features: dynamic imports with explicit `sys.modules` eviction, `monkeypatch` environment, test-local aggregation import.
- Repeated preparation or process work: each parser load must evict `_rtx` modules to prevent host parser cache/order coupling.
- Mutable/global/process boundaries: `sys.path`/`sys.modules` and environment are material process-global boundaries; no child subprocess.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `no-safe-change`.
- Pass 1 recommendation and catalog IDs: P12: module reload lifecycle is the behavior-safe isolation mechanism; broader fixture/cache would reintroduce import-order dependence.
- Required retained coverage: both env/default home paths, nested Codex payload semantics, session fallback, resume format, and aggregate interface surface.
- Focused verification command: `pytest skills/find-handoff-candidates/_rtx/tests/test_parsers.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: explicit module eviction and re-import protect environment-dependent parser discovery from `sys.modules` ordering; caching or broader fixtures would weaken the contract.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `9 passed` in the Find Handoff Candidates focused group (`18 passed in 0.04s`).
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed explicit `_rtx` module eviction and environment isolation remain; no changed code requires a focused rerun.
- Final state: `no-safe-change`.

### `skills/find-handoff-candidates/_rtx/tests/test_scan.py`

- Canonical task: generic handoff scan unit contract.
- Item/behavior summary: protects exact sentinels, opaque-field accounting, scan gap/reset behavior, mtime selection, date windows, line floor, and real parser registry shape.
- Current pytest features: dynamic import with script-module eviction, `tmp_path`, generated JSONL files, `os.utime`, fake parser.
- Repeated preparation or process work: each case intentionally constructs a distinct transcript/time sequence; no subprocess startup.
- Mutable/global/process boundaries: import cache/path mutation, filesystem mtimes, generated transcripts, and date-sensitive scan state are material and function-local.
- Pass 1 author: `/root/pass1_batch_a`.
- Pass 1 state: `already-efficient`.
- Pass 1 recommendation and catalog IDs: P12: data sequences encode different temporal claims; sharing them would weaken clarity and risk state leakage.
- Required retained coverage: exact marker false-positive guard, post-complete reset/re-flag, mtime-not-directory selection, date set window, line floor, and real parser list sanity.
- Focused verification command: `pytest skills/find-handoff-candidates/_rtx/tests/test_scan.py`.
- Pass 2 adjudicator: `/root/skill_test_review`.
- Pass 2 verdict: `agree`.
- Pass 2 rationale: each generated transcript and mtime sequence represents a distinct temporal behavior, while module eviction prevents parser registry leakage; no redundant child-process startup exists.
- Tie-breaker and decision: not needed.
- Approved implementation: no change.
- Files changed: none.
- Focused result: `9 passed` in the Find Handoff Candidates focused group (`18 passed in 0.04s`).
- Pass 3 verifier: `/root/pass1_batch_a/pass3_batch_a`.
- Pass 3 verdict: `pass`.
- Pass 3 evidence/findings: Confirmed per-test generated transcript/mtime sequences and import-cache eviction remain; no changed code requires a focused rerun.
- Final state: `already-efficient`.
