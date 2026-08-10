# Google Credential File Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every supported Google connection create one timestamped credential descriptor file, bind selected Calendar, Drive, and Gmail consumers directly to that path, and report completion only after each selected service verifies live access.

**Architecture:** Extend the existing Google credential, OAuth, and service-binding sources. The supported write path is one `connect-services` coordinator: authorize once, create one descriptor, dispatch the path to the selected service owners, and return one transparent JSON result. Existing credential-ID and legacy OAuth configurations remain runtime-readable only; they are no longer used by supported authorization, installer, or gateway flows.

**Tech Stack:** Python 3.11+, stdlib JSON/path/datetime/UUID/urllib APIs, existing `officina.common.secret_store`, Officina `PythonMachineInterface` and `DispatchCall`, schema-version-6 blueprints, pytest.

## Global Constraints

- Follow [the approved design](../specs/2026-08-10-google-credential-file-binding-design.md).
- The mechanism is a direct path, not a symlink, alias, credential name, or new registry layer.
- Descriptor files contain metadata and secret-store references only. Raw client secrets, refresh tokens, and access tokens never enter descriptor files or the new authorization, binding, and coordinator outputs.
- Extend existing owners: `google_credentials.py`, `_loopback_oauth.py`, Calendar/Drive `_ensure_oauth.py`, and email `_email_accounts.py`. Add only one new runtime source: the cross-service coordinator.
- Service owners validate scope/account, perform their own live API probe, and write their own configuration. The coordinator never imports service implementation modules or writes service config.
- A present `credential_file` key is authoritative even when its value is invalid. Invalid, missing, insufficient-scope, or unusable paths fail; they never fall through to a credential ID or legacy token.
- Legacy `credential_id` and legacy OAuth records remain readable only when no `credential_file` key exists. Supported workflows no longer create or bind credential IDs.
- Implement in a separate named worktree so the current dirty worktree remains byte-for-byte untouched. Copy only this plan and its approved spec into that worktree before implementation; do not stage, restore, or alter the user's current dirty files.
- Before editing skill nodes, use `famulus:skill-maker` to retrieve the live requirements for every affected module/source type and synchronize blueprints. Do not hand-edit generated SKILL contract blocks.
- Do not commit, stage, or push without separate user authorization. There is one approval-gated commit checkpoint at the end.

## Supported user flow

```text
connect-services(services, optional Gmail nickname, home)
  -> Google consent
  -> one new credential descriptor file
  -> each selected-and-granted service binds and probes that exact path
  -> one JSON result: complete or incomplete with per-service errors

bind-credential-file(existing path, incomplete services, home)
  -> retry binding/probes only; never repeat consent
```

No supported route exposes a separate ID-producing authorization or asks an agent to remember a later handoff.

---

## Task 1: Extend the canonical credential source with descriptor files

**Files:**

- Modify: `src/officina/common/google_credentials.py:55`
- Modify: `src/officina/common/blueprints/google-credentials.yaml`
- Modify through blueprint synchronization: `src/officina/common/blueprint.yaml`
- Create: `tests/test_officina_google_credential_files.py`

**Python API:**

```python
@dataclass(frozen=True)
class GoogleCredentialFile:
    path: Path
    created_at: str
    subject: str
    account: str
    client_id: str
    token_uri: str
    granted_services: tuple[str, ...]
    granted_scopes: frozenset[str]
    client_secret_ref: str
    refresh_token_ref: str
```

Add `create_credential_file`, `load_credential_file`, and `refresh_access_token_from_file` operations to the existing source contract. Do not create a second common source or a descriptor registry.

- [ ] **Step 1: Write failing creation and secrecy tests**

  Call creation twice for the same subject/account with a fixed UTC time and different eight-character lowercase-hex IDs. Require two absolute paths under `<config-root>/connect-google/credentials/`, require the first file to remain byte-identical, and require names such as `2026-08-10T14-52-10Z-a1b2c3d4.json`.

  Require this exact JSON shape:

  ```json
  {
    "schema_version": 1,
    "created_at": "2026-08-10T14:52:10Z",
    "subject": "google-subject",
    "account": "person@example.com",
    "client_id": "client-id",
    "token_uri": "https://oauth2.googleapis.com/token",
    "granted_services": ["calendar", "drive"],
    "granted_scopes": ["openid"],
    "client_secret_ref": "oauth-client:client-id:client-secret",
    "refresh_token_ref": "credential-file:2026-08-10T14-52-10Z-a1b2c3d4:refresh-token"
  }
  ```

  The fixture may include the full sorted granted-scope set; the example abbreviates that array only. Assert the raw client secret, refresh token, and any access token are absent from serialized JSON and outputs.

- [ ] **Step 2: Implement exclusive creation and strict loading**

  Resolve the credential directory through `resolve_famulus_paths`. Generate UTC timestamp plus random eight-character lowercase hex; injected time/ID are test seams only. The full file stem is the artifact identity and the secret reference is `credential-file:<full-file-stem>:refresh-token`, so equal short IDs at different timestamps cannot share a secret. Check/reserve the exclusive filename before writing the secret, so a same-filename collision cannot overwrite the prior file's effective credential.

  Loading resolves the supplied path strictly to an absolute regular file and validates the complete schema, field types, schema version, services, and scopes. Unknown/missing fields fail explicitly.

- [ ] **Step 3: Implement path-based token refresh**

  Check `required_scopes` against the descriptor before network access. Resolve the descriptor's two secret references with `secret_store.require`, then reuse the existing refresh-token exchange helper. Do not read `credentials.json` in this path.

- [ ] **Step 4: Update the existing common-source blueprint**

  Add the three operations and their arguments/results. Declare descriptor-directory/file reads and exclusive writes, secret-store refresh-token writes/reads, and token-endpoint network access. Keep legacy load/refresh operations documented for read compatibility. Stop advertising `store-credential` as part of the supported connection workflow; retain implementation only if needed to read/test legacy records.

- [ ] **Step 5: Cover failures and run the core suite**

  Test missing/malformed files, wrong schema/field types, insufficient scopes before network access, missing secret references, same-filename collision before secret-store mutation, equal short IDs at different timestamps, and same-account repeated creation.

  Run:

  ```text
  python3 -m pytest -q -o pythonpath=src tests/test_officina_google_credential_files.py tests/test_officina_google_credentials.py
  python3 -m pytest -q -o pythonpath=src tests/test_typed_blueprint_schemas.py tests/test_interface_projection.py
  ```

---

## Task 2: Make file authorization canonical and add one coordinator

**Files:**

- Modify: `skills/connect-google/_rtx/_loopback_oauth.py:33`
- Modify: `skills/connect-google/_rtx/blueprints/rtx-authorize-services.yaml`
- Modify: `skills/connect-google/_rtx/tests/test_authorize_services.py`
- Create: `skills/connect-google/_rtx/_connect_services.py`
- Create: `skills/connect-google/_rtx/blueprints/rtx-connect-services.yaml`
- Create: `skills/connect-google/_rtx/tests/test_connect_services.py`
- Modify: `skills/connect-google/instructions/connect-services.md`
- Modify: `skills/connect-google/blueprints/instructions-connect-services.yaml`
- Modify: `skills/connect-google/blueprints/gateway.yaml`
- Modify: `skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py`
- Modify through blueprint synchronization: `skills/connect-google/_rtx/blueprint.yaml`
- Modify through blueprint synchronization: `skills/connect-google/blueprint.yaml`
- Modify through blueprint synchronization: `skills/connect-google/SKILL.md`

**Supported machine interfaces:**

```text
connect-google._rtx.interface.connect-services
  --services SERVICE... --home DIR
  [--account-hint EMAIL] [--gmail-nickname NAME]
  [--allow-account-change SERVICE...]

connect-google._rtx.interface.bind-credential-file
  --credential-file PATH --services SERVICE... --home DIR
  [--gmail-nickname NAME] [--allow-account-change SERVICE...]
```

- [ ] **Step 1: Change the existing OAuth source to create a descriptor**

  Reuse the current consent, code exchange, token, and userinfo logic. Replace the final `store_google_credential` call with `create_credential_file`; the authorization result contains `credential_file`, requested/granted/denied services, account, and subject. Do not add another behavioral-source blueprint for `_loopback_oauth.py`.

  The old credential-ID authorizer is no longer an externally supported interface. Remove installer/external access to it and stop exporting it from the module-facing connect flow. Legacy registry load/refresh remains available only to service runtime compatibility branches.

- [ ] **Step 2: Write the coordinator tests first**

  Cover all-success, denied consent, one failed binder, missing Gmail nickname, account-change confirmation required, and retry. Assert:

  - only selected-and-granted services dispatch;
  - the exact absolute descriptor path and `home` reach every binder;
  - an unselected service is never called;
  - partial failure returns the descriptor path and `complete: false`;
  - `bind-credential-file` retries with that path and never invokes authorization.

- [ ] **Step 3: Implement one fixed dispatch map**

  `_connect_services.py` owns only orchestration and declares exact `DispatchCall` dependencies to the three service file binders. It directly calls the local OAuth function, then dispatches by this fixed map:

  ```text
  calendar -> g-calendar._rtx.interface.use-google-credential-file
  drive    -> cloud-files._rtx.interface.use-google-credential-file
  gmail    -> email-client._rtx.interface.accounts-use-google-credential-file
  ```

  If Gmail was selected and granted but no nickname is available, authorization may still create the descriptor and other selected services still dispatch. Gmail itself is not dispatched; the coordinator records `missing-gmail-nickname` under `incomplete_services`, returns `complete: false`, and retains the descriptor for a later `bind-credential-file` retry with an explicit nickname. If Google denied Gmail, report only the denial; nickname absence is irrelevant. Account-change approval is forwarded only for the named services.

- [ ] **Step 4: Define transparent machine results**

  Every service binder returns exit zero plus:

  ```json
  {
    "service": "calendar",
    "credential_file": "/absolute/path.json",
    "account": "person@example.com",
    "bound": true,
    "verified": true
  }
  ```

  A failure returns nonzero with a stable code/message and `bound: false`, `verified: false`; no failed binder persists config. The coordinator parses these declared results and returns:

  ```json
  {
    "credential_file": "/absolute/path.json",
    "requested_services": ["calendar", "drive"],
    "granted_services": ["calendar", "drive"],
    "denied_services": [],
    "bound_services": ["calendar"],
    "verified_services": ["calendar"],
    "incomplete_services": {"drive": {"code": "live-check-failed", "message": "HTTP 403"}},
    "complete": false
  }
  ```

  `complete` is true only when every requested service was granted, bound, and verified.

- [ ] **Step 5: Make this the only connect-google user route**

  The instruction gateway uses `connect-services` for new setup and `bind-credential-file` for retry. In the ordinary interactive route it obtains a Gmail nickname before starting a Gmail-inclusive request and treats only `complete: true` as success. The installer may call without a nickname; the coordinator then follows the explicit incomplete/deferred behavior above without blocking other services. Replace the gateway blueprint's direct `authorize-services` dependency and update `test_connect_google_llm_routing.py` to assert the coordinator route. Remove guidance and public access that invite callers to run an ID-producing authorization or perform an LLM-mediated bind.

- [ ] **Step 6: Synchronize and run focused tests**

  Run:

  ```text
  python3 -m pytest -q -o pythonpath=src skills/connect-google/_rtx/tests/test_authorize_services.py skills/connect-google/_rtx/tests/test_connect_services.py
  python3 -m pytest -q -o pythonpath=src tests/test_officina_blueprint_authorization.py tests/test_interface_projection.py tests/test_node_standards.py
  ```

---

## Task 3: Extend existing service binders and enforce path precedence

**Files:**

- Modify: `skills/g-calendar/_rtx/_ensure_oauth.py:126`
- Modify: `skills/g-calendar/_rtx/blueprints/rtx-ensure-oauth.yaml`
- Modify: `skills/g-calendar/_rtx/_gcal_client.py:39`
- Modify: `skills/g-calendar/_rtx/blueprints/rtx-gcal-client.yaml`
- Create: `skills/g-calendar/_rtx/tests/test_calendar_credential_file_binding.py`
- Modify: `skills/cloud-files/_rtx/_ensure_oauth.py:158`
- Modify: `skills/cloud-files/_rtx/blueprints/rtx-ensure-oauth.yaml`
- Modify: `skills/cloud-files/_rtx/_drive_gateway.py:35`
- Modify: `skills/cloud-files/_rtx/blueprints/rtx-init.yaml`
- Create: `skills/cloud-files/_rtx/tests/test_drive_credential_file_binding.py`
- Modify: `skills/email-client/_rtx/_email_accounts.py:203`
- Modify: `skills/email-client/_rtx/blueprints/rtx-email-accounts.yaml`
- Modify: `skills/email-client/_rtx/_oauth_tokens.py:129`
- Modify: `skills/email-client/_rtx/blueprints/rtx-init.yaml`
- Create: `skills/email-client/_rtx/tests/test_gmail_credential_file_binding.py`
- Modify through blueprint synchronization: each affected service's `_rtx/blueprint.yaml` and root `blueprint.yaml`

- [ ] **Step 1: Add file binding to the existing owners**

  Add `use-google-credential-file` beside the existing Calendar/Drive binding functions and `accounts-use-google-credential-file` beside the existing email account functions. Inputs are an absolute/resolvable descriptor path, `home`, optional account-change approval, and Gmail nickname where applicable.

  Retain legacy ID-binding implementation only for compatibility with already-installed/local callers, but remove it from supported gateway and installer dependencies. The new coordinator receives access only to the file-binding interfaces.

- [ ] **Step 2: Validate, probe, then persist**

  Each binder loads the descriptor, checks its exact service scope, refreshes an access token, and performs one service-owned authenticated read before writing:

  ```text
  Calendar: GET https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1
  Drive:    GET https://www.googleapis.com/drive/v3/files?pageSize=1&fields=files(id)
  Gmail:    GET https://gmail.googleapis.com/gmail/v1/users/me/profile
  ```

  Accept injected `urlopen` in Python-level tests. Persist only after a successful probe. Calendar/Drive merge the normalized absolute `credential_file` into their config. Gmail updates only the named account with `credential_file` and `auth: gmail-oauth`. Preserve all unrelated fields and old records.

- [ ] **Step 3: Make account-change behavior explicit**

  A first binding means there is no credential file, credential ID, or legacy OAuth credential state. A first Calendar/Drive binding needs no confirmation. Compare identity by stable Google `subject`, not the mutable account email. If the old subject differs or cannot be established, return `account-change-confirmation-required` unless that service was explicitly approved.

  Gmail additionally requires descriptor `account` to equal the nickname's configured `email`. A mismatch is never overridden by the approval flag; the user must explicitly update the email account record, then retry. A different prior bound `subject` still requires account-change approval.

- [ ] **Step 4: Enforce presence-based runtime precedence**

  Branch on key presence, not truthiness:

  ```python
  if "credential_file" in config:
      credential_file = require_nonempty_path(config["credential_file"])
      return refresh_access_token_from_file(credential_file, ...)
  if credential_id_is_configured(config):
      return refresh_access_token(...)
  return existing_legacy_loader(...)
  ```

  For email, apply the same rule to the named account record. Empty, whitespace, null, wrong-type, missing, malformed, scope-deficient, and refresh-failing file values must all fail without consulting ID or legacy loaders.

  An existing but unreadable or malformed service config is also terminal: the runtime cannot prove that no `credential_file` binding exists, so it must not fall back. In particular, remove Calendar's current warning-and-legacy-fallback branch for config read/JSON errors and raise the config error instead.

- [ ] **Step 5: Test the real binder and runtime contracts**

  For each service, cover: normalized path storage; scope rejection before writes; live probe before writes; exact success/error JSON; no write on probe failure; same-account rebind; cross-account confirmation; invalid-value no-fallback; unreadable/malformed service config with no legacy call; and legacy-only runtime behavior. Also bind Calendar/Drive to different files and two Gmail nicknames to different files. The coordinator tests include selected-but-denied Gmail with no nickname and assert denial only, with no missing-nickname incomplete entry.

- [ ] **Step 6: Synchronize and run service suites**

  Run:

  ```text
  python3 -m pytest -q -o pythonpath=src skills/g-calendar/_rtx/tests
  python3 -m pytest -q -o pythonpath=src skills/cloud-files/_rtx/tests
  python3 -m pytest -q -o pythonpath=src skills/email-client/_rtx/tests
  ```

---

## Task 4: Migrate installer and gateway routes, then certify end to end

**Files:**

- Modify: `skills/install-assistant-tools/_rtx/_google_onboarding.py`
- Modify: `skills/install-assistant-tools/_rtx/blueprints/rtx-google-onboarding.yaml`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_google_onboarding.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_install.py`
- Modify: `skills/g-calendar/blueprints/gateway.yaml`
- Modify through blueprint synchronization: `skills/g-calendar/SKILL.md`
- Modify: `skills/cloud-files/blueprints/gateway.yaml`
- Modify through blueprint synchronization: `skills/cloud-files/SKILL.md`
- Modify: `skills/email-client/blueprints/gateway.yaml`
- Modify through blueprint synchronization: `skills/email-client/SKILL.md`
- Modify: `skills/connect-google/_rtx/tests/test_service_delegation.py`
- Create: `skills/connect-google/_rtx/tests/test_credential_file_end_to_end.py`
- Modify through blueprint synchronization: affected root and `_rtx` module projections

- [ ] **Step 1: Route installer onboarding through the coordinator**

  Replace its authorize-then-ID-bind sequence with one `connect-google._rtx.interface.connect-services` dispatch. Pass services, Gmail nickname, and `home`; the live installer has no account-hint input, so do not invent one.

  Preserve the phase-entry-facing adapter contract: `OnboardingCapabilityResult` keeps `status`, `granted_services`, `denied_services`, `deferred_services`, `bound_services`, `failed_services`, and `detail`, replaces `credential_id` with `credential_file`, and may add `verified_services`. Map coordinator output into those existing status collections so `_phase_entry.py` requires no behavioral change. Update `test_install.py` fixtures that construct the dataclass to use `credential_file`. A selected Gmail service with no nickname maps to `deferred_services`; binder errors map to `failed_services`; `complete: true` maps to `status="completed"`, otherwise to the existing partial/skipped/failed status rules.

- [ ] **Step 2: Route every service gateway through connect-google**

  Calendar, Drive, and email gateway guidance requests `connect-google.interface.default` for setup/reauthorization and no longer tells the agent to pass an opaque ID to a service binder. Remove legacy ID binder dependencies from supported gateways. Keep legacy runtime reading documented only as migration compatibility, not as a setup option.

- [ ] **Step 3: Update delegation and authorization tests**

  Prove the installer and gateways expose only the file coordinator for new connections, the coordinator has access only to the three narrow file binders, and old ID authorization/binders are not reachable from supported external routes. Because implementation occurs in an isolated worktree, update the clean baseline test rather than touching the user's dirty copy.

- [ ] **Step 4: Add the end-to-end success and retry tests**

  Simulate one authorization for Calendar, Drive, and a named Gmail account. Require one descriptor, three exact path bindings, three service probes, and later token use through that path. Assert no raw secret appears in descriptor, outputs, or service configs.

  Then fail Drive's probe: require `complete: false`, preserve Drive's prior config, retain the file, and retry that same path without another consent call. Require final `complete: true`. Assert unselected services never change.

- [ ] **Step 5: Run the full focused Google surface**

  Run:

  ```text
  python3 -m pytest -q -o pythonpath=src tests/test_officina_google_credentials.py tests/test_officina_google_credential_files.py
  python3 -m pytest -q skills/connect-google/_rtx/tests
  python3 -m pytest -q skills/g-calendar/_rtx/tests
  python3 -m pytest -q skills/cloud-files/_rtx/tests
  python3 -m pytest -q skills/email-client/_rtx/tests
  python3 -m pytest -q skills/install-assistant-tools/_rtx/tests/test_google_onboarding.py skills/install-assistant-tools/_rtx/tests/test_install.py
  ```

  Run sibling `_rtx` suites in separate pytest processes because their package
  names intentionally repeat across skills and cannot be collected safely into
  one import namespace.

- [ ] **Step 6: Run graph and repository validation**

  Run:

  ```text
  python3 -m pytest -q -o pythonpath=src tests/test_interface_projection.py tests/test_officina_blueprint_authorization.py tests/test_node_standards.py tests/test_typed_blueprint_schemas.py
  ```

  Then run the canonical pre-commit command from the live `TESTING.md`. The concurrent pytest-runner work may have changed that command; read it in the isolated implementation worktree and do not import or revert changes from the user's dirty worktree.

- [ ] **Step 7: Audit all design requirements and scope**

  Record evidence for: distinct same-account files; no raw secrets; selected services bind and verify; unselected services unchanged; independent service/nickname paths; incomplete partial handoff; same-file retry; legacy runtime readability; strict no-fallback; explicit account changes; and declared public cross-module calls.

  Inspect `git status --short`, the exact feature diff, and `git diff --check`. Confirm the original dirty worktree is unchanged.

- [ ] **Step 8: Approval-gated commit checkpoint**

  Only if the user authorizes it, stage the exact feature files in the isolated worktree and create one scoped commit. Otherwise report the verified uncommitted file list and stop.

## Definition of Done

- Every supported authorization creates a distinct immutable descriptor file and no supported workflow creates or binds a credential ID.
- Selected Calendar, Drive, and named Gmail consumers store the exact descriptor path only after service-owned live verification.
- One coordinator result makes granted, bound, verified, incomplete, and retry state explicit.
- Retrying partial setup reuses the existing file and never repeats consent.
- A present file key never falls back to stale ID or legacy OAuth state.
- Legacy-only configurations still work until that service is successfully rebound.
- Installer and all service gateways use the same coordinator path.
- Cross-service access is limited to declared file-binder interfaces.
- Focused, legacy, graph, and repository validation pass without changing the user's dirty worktree.
