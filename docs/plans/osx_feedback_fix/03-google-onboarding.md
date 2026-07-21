# Shared Google Onboarding Implementation Plan

> **Deferred pending version-4 adoption and rebase — do not execute.** The
> umbrella package is frozen. Its proposed artifacts are not authorized by the
> unified migration and require fresh functional-predecessor dispositions when
> rebased.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` or, with explicit delegation approval, `superpowers:subagent-driven-development`.

**Goal:** Authorize a selected same-account subset of Drive, Calendar, and Gmail once, store one shared credential safely, and let each service retain configuration and verification ownership.

**Architecture:** `connect-google` owns sanitized canonical Desktop-client metadata, a single client-secret reference, the combined OAuth/OpenID transaction, per-account refresh tokens, and grant metadata. Services store only an opaque credential reference. Installer orchestration calls declared interfaces after core installation.

**Tech Stack:** Python 3.11 stdlib, Google OAuth 2.0/OpenID Connect, `officina.common.secret_store`, dispatcher, pytest.

## Global constraints

- Inherit program-wide constraints and sequencing from the [umbrella](README.md). This subplan owns Google identity, grant, rollback, and secret contracts; it consumes installer paths/results from [installer Tasks 1-3](01-installer-runtime.md).
- One authorization covers only selected services using the same verified Google `sub`.
- Tokens/client secrets appear only in the host secret store and transient request memory. Canonical JSON files contain references and non-secret metadata only.
- Existing per-service credentials remain readable until explicit reauthorization; they are not silently copied or deleted.
- All skill changes use `skill-maker`; installer-to-service calls use dispatcher only.
- Tests open no browser and contact no live Google endpoint.

## Source feedback owned here

Item 6 plus the repeated three-service authorization problem discovered during onboarding.

Authoritative provider references: [Google OpenID Connect](https://developers.google.com/identity/openid-connect/reference), [OAuth web-server authorization](https://developers.google.com/identity/protocols/oauth2/web-server), and [granular permissions](https://developers.google.com/identity/protocols/oauth2/resources/granular-permissions).

---

### Task 1: Unify canonical client discovery and add the shared credential model

**Files:**
- Modify through `skill-maker`: `skills/cloud-files/blueprint.yaml`
- Modify: `skills/cloud-files/_rtx/_drive_gateway.py`
- Modify: `skills/cloud-files/_rtx/_ensure_oauth.py`
- Modify: `skills/cloud-files/tests/test_cloud_files.py`
- Modify: `skills/cloud-files/tests/test_cloud_files_ensure_oauth.py`
- Create: `src/officina/common/google_credentials.py`
- Create: `tests/test_officina_google_credentials.py`
- Regenerate: `skills/cloud-files/SKILL.md`
- Regenerate: `references/blueprint/runtime_dependencies.json`

**Interfaces:**
- Produces: `GoogleCredentialRef` and `refresh_access_token(credential_id: str, *, required_scopes: Collection[str], home: Path | None = None) -> str` in `officina.common.google_credentials`.
- Consumes: canonical client path `<famulus-config-root>/connect-google/client.json` (`~/.config/famulus/connect-google/client.json` by default on POSIX and `%LOCALAPPDATA%\Famulus\config\connect-google\client.json` on Windows).
- Changes: `connect-google.machine.install-client` validates an input Desktop client JSON, stores its client secret once under `oauth-client:<client-id>:client-secret`, and atomically writes sanitized canonical metadata with `client_secret_ref`.
- Preserves: existing per-service credential files remain readable for backward compatibility; shared authorization is the default for new setup and reauthorization, and old tokens are not copied or deleted automatically.

- [ ] **Step 1: Route Drive's legacy guidance to the canonical client**

Change `client_setup_lines` and `run` to use:

```python
client_json = famulus_paths(home, platform).config_root / "connect-google" / "client.json"
```

When it exists, route through the declared service-owned setup binding with `--from-json <canonical path>`. Do not copy secrets into a service-local client file. Add a dry-run regression proving the canonical path is selected.

- [ ] **Step 2: Write the failing shared-credential model tests**

In `tests/test_officina_google_credentials.py`, define the exact service-to-scope policy:

```python
SERVICE_SCOPES = {
    "drive": frozenset({"https://www.googleapis.com/auth/drive"}),
    "calendar": frozenset({"https://www.googleapis.com/auth/calendar"}),
    "gmail": frozenset({"https://mail.google.com/"}),
}
IDENTITY_SCOPES = frozenset({"openid", "email"})
```

Assert that service normalization rejects an empty list, unknown names, and duplicates; preserves the first requested order; and returns the union of the selected service scopes plus `IDENTITY_SCOPES`. Assert that a credential registry record contains only `credential_id`, Google subject, account email, client ID, token URI, and granted scopes. It must reject `client_secret`, `refresh_token`, and `access_token` fields.

Use a fake `SecretBackend` to prove that `install-client` stores the client secret exactly once and the canonical file contains only client ID, redirect/token/auth URIs, and `client_secret_ref`. `store_google_credential` stores only the account refresh token; its registry record references the shared client record and contains neither secret. Assert `refresh_access_token` refuses a service call when any required scope is absent before contacting the token endpoint.

Seed a legacy plaintext `~/.config/connect-google/client.json`. On successful migration, require one secret-store write followed by an atomic sanitized write at the platform Famulus config path; remove the old file only after the new client passes `client-status` and only when it is a regular installer-owned file. Inject failure before and after the secret write: before success, preserve the original bytes; after success but before replacement, the legacy file must still be readable and a retry must be idempotent. A malformed input never changes the canonical file or secret store.

Run: `python3 -m pytest -q tests/test_officina_google_credentials.py`

Expected: collection fails because `officina.common.google_credentials` does not exist.

- [ ] **Step 3: Implement the shared credential and refresh boundary**

Create `src/officina/common/google_credentials.py` with immutable `GoogleCredentialRef` and `AuthorizationResult` dataclasses, the exact scope constants above, service normalization, scope-union calculation, registry loading/writing, secret-store persistence, and token refresh. Use credential IDs of the form `google:<subject>`, where `subject` comes from Google's verified identity response; do not derive secret-store keys from an installer-provided email hint.

Store the non-secret registry at `<famulus-config-root>/connect-google/credentials.json`. On POSIX, create/replace it and sanitized `client.json` with mode `0600`; on Windows, use atomic current-user files and the user-profile ACL rather than claiming POSIX mode semantics. Store secrets through `officina.common.secret_store` under namespace `connect-google`: the client secret uses `oauth-client:<client-id>:client-secret`, while the per-account refresh token uses `<credential-id>:refresh-token`. Registry records reference the shared client ID/secret key and never duplicate the client secret per account. Write a new registry record only after identity lookup succeeds, every selected service is classified from the returned scope set, and the needed refresh token is stored successfully. Preserve the previous credential and registry record on a failed replacement transaction.

`refresh_access_token` loads the registry record, verifies the requested scopes are a subset of the recorded grant, retrieves both secrets, performs the refresh exchange, and returns only the access token. It never returns or logs the client secret or refresh token.

- [ ] **Step 4: Run the credential/discovery slice and commit after review**

Run: `python3 -m pytest -q tests/test_officina_google_credentials.py skills/cloud-files/tests/test_cloud_files.py skills/cloud-files/tests/test_cloud_files_ensure_oauth.py`

Expected: all pass, the canonical client is used, failure injection restores prior registry/secrets after every transaction stage, and no token appears in output. Commit only Task 1 files with message `feat: add shared Google credential storage`.

---

### Task 2: Add the combined authorization machine interface

**Files:**
- Modify through `skill-maker`: `skills/connect-google/blueprint.yaml`
- Modify through `skill-maker`: `skills/connect-google/llm_interfaces/connect-services.md`
- Create: `skills/connect-google/_rtx/_authorize_services.py`
- Create: `skills/connect-google/tests/test_authorize_services.py`
- Modify: `skills/connect-google/tests/test_service_delegation.py`
- Modify: `skills/connect-google/tests/test_connect_google_llm_routing.py`
- Regenerate: `skills/connect-google/SKILL.md`

**Interfaces:**
- Produces: `connect-google.machine.authorize-services --service <drive|calendar|gmail> [--service ...] [--account-hint <email>] [--home <dir>] [--no-open-browser]`.
- Produces: `authorize_services(services: Sequence[str], *, home: Path, account_hint: str | None, open_browser: bool) -> AuthorizationResult`.
- Produces: `AuthorizationResult.as_payload() -> dict[str, object]` with `schema_version`, `account`, `credential_id`, `requested_services`, `granted_services`, and `denied_services`.

- [ ] **Step 1: Write the failing combined-authorization machine-interface tests**

In `skills/connect-google/tests/test_authorize_services.py`, invoke the interface with repeated service flags:

```text
--service drive --service calendar --service gmail --home <tmp-home> --no-open-browser
```

Assert one authorization URL is constructed with the union of Drive, Calendar, Gmail, `openid`, and `email` scopes; `access_type=offline`; PKCE; state; and granular-consent support. Assert there is exactly one loopback callback and one authorization-code exchange. Use fake token and identity responses; no live browser or Google request is permitted in tests.

Require one JSON object on stdout:

```json
{
  "schema_version": 1,
  "account": "user@example.com",
  "credential_id": "google:verified-google-subject",
  "requested_services": ["drive", "calendar", "gmail"],
  "granted_services": ["drive", "calendar", "gmail"],
  "denied_services": []
}
```

Add cases for unknown and duplicate `--service` flags, callback state mismatch, timeout, token exchange failure, identity mismatch with `--account-hint`, missing refresh token, and a granular partial grant that returns the granted services and lists the omitted service under `denied_services`. A valid partial grant is committed for its granted scopes and services; it is not rolled back merely because another requested scope was denied. Assert every actual failure preserves a prior credential and contains no authorization URL, code, token, client secret, or raw provider response.

Run: `python3 -m pytest -q skills/connect-google/tests/test_authorize_services.py`

Expected: collection or routing fails because the interface is not declared or implemented.

- [ ] **Step 2: Add `connect-google.machine.authorize-services` through `skill-maker`**

Implement one loopback OAuth flow in `_authorize_services.py`. Use repeated `--service` flags rather than comma-separated input so dispatcher can validate every value. Load the canonical Desktop-client metadata from the shared Famulus config path and its client secret through `client_secret_ref`; request the selected scope union once; verify callback state; exchange the code once; fetch verified Google identity; compare `--account-hint` only after Google identifies the account; classify each service from the actually granted scope set; and persist through `google_credentials` only after the transaction validates.

The machine interface prints only `AuthorizationResult.as_payload()` as JSON. It never invokes Drive, Calendar, or Gmail interfaces. Change the `connect-google` contract and `llm_interfaces/connect-services.md` so it owns shared authorization and credential storage, while service skills retain configuration, API use, smoke verification, and account-specific settings. Replace the existing prohibition against receiving service tokens with an explicit prohibition against emitting, copying, or logging them. Update the LLM routing tests so selected services produce one authorization request followed by service-owned configuration, rather than three service-owned OAuth exchanges.

For later additions, invoke with `include_granted_scopes=true`, request only the missing service scopes plus `openid email`, and union only scopes actually reported by Google with the prior recorded grant. Fetch the OpenID UserInfo endpoint and require nonempty `sub`, nonempty `email`, and `email_verified is True`. If the response omits a refresh token, reuse the prior token only when the verified `sub` and client ID exactly match the existing credential and that prior token exists; otherwise fail and execute rollback. Never substitute a token based only on an email/account hint.

- [ ] **Step 3: Run authorization tests and commit after review**

Run: `python3 -m pytest -q skills/connect-google/tests/test_authorize_services.py skills/connect-google/tests/test_service_delegation.py skills/connect-google/tests/test_connect_google_llm_routing.py`

Expected: all pass with one simulated callback/exchange and no network/browser access. Commit only Task 2 files with message `feat: authorize selected Google services once`.

---

### Task 3: Make Drive, Calendar, and Gmail consume shared credential references

**Files:**
- Modify through `skill-maker`: `skills/cloud-files/blueprint.yaml`
- Modify: `skills/cloud-files/_rtx/_drive_gateway.py`
- Modify: `skills/cloud-files/_rtx/_ensure_oauth.py`
- Modify: `skills/cloud-files/tests/test_cloud_files.py`
- Modify: `skills/cloud-files/tests/test_cloud_files_ensure_oauth.py`
- Modify through `skill-maker`: `skills/g-calendar/blueprint.yaml`
- Modify: `skills/g-calendar/_rtx/_gcal_client.py`
- Modify: `skills/g-calendar/_rtx/_ensure_oauth.py`
- Modify: `skills/g-calendar/tests/test_gcal.py`
- Modify: `skills/g-calendar/tests/test_g_calendar_ensure_oauth.py`
- Modify through `skill-maker`: `skills/email-client/blueprint.yaml`
- Modify: `skills/email-client/_rtx/_email_accounts.py`
- Modify: `skills/email-client/_rtx/_oauth_tokens.py`
- Modify: `skills/email-client/tests/test_accounts.py`
- Modify: `skills/email-client/tests/test_oauth_tokens.py`
- Regenerate: `skills/cloud-files/SKILL.md`
- Regenerate: `skills/g-calendar/SKILL.md`
- Regenerate: `skills/email-client/SKILL.md`

**Interfaces:**
- Produces: `cloud-files.machine.use-google-credential`, `g-calendar.machine.use-google-credential`, and `email-client.machine.accounts-use-google-credential`, each accepting `--credential-id`.
- Changes: new service configuration writes use `<famulus-config-root>/{cloud-files,g-calendar,email-client}` on every platform. Existing `~/.config/<service>` files remain a read-only legacy source until explicitly migrated.

- [ ] **Step 1: Write failing service-consumer tests**

Add tests proving each service configuration stores only `credential_id` and calls `google_credentials.refresh_access_token` with its own required scope:

```python
assert cloud_config["credential_id"] == "google:verified-google-subject"
assert calendar_config["credential_id"] == "google:verified-google-subject"
assert email_account["oauth"]["credential_id"] == "google:verified-google-subject"
```

Drive must require `SERVICE_SCOPES["drive"]`, Calendar must require `SERVICE_SCOPES["calendar"]`, and Gmail IMAP/SMTP must require `SERVICE_SCOPES["gmail"]`. Tests must fail if a service reads another service's legacy refresh-token file, copies the shared refresh token into its own configuration, or accepts a credential whose recorded scopes do not cover the service.

Parameterize config-path tests for Linux, macOS, and Windows using `famulus_paths`; no implementation may synthesize `home / ".config"` on Windows. For each legacy service config, validate and copy only non-secret service settings plus the new credential reference into the Famulus config root, then smoke the service. Preserve the old file by default and never copy its refresh/client secrets into the new file; explicit purge/cleanup is owned by manifest v2 and secret-store migration policy.

Keep explicit backward-compatibility cases proving existing Drive and Calendar credential JSON and existing Gmail keyring entries remain usable until the user reauthorizes. New shared configuration takes precedence only when a `credential_id` is present; do not silently migrate or delete legacy secrets.

Run: `python3 -m pytest -q skills/cloud-files/tests/test_cloud_files.py skills/g-calendar/tests/test_gcal.py skills/email-client/tests/test_accounts.py skills/email-client/tests/test_oauth_tokens.py`

Expected: failures because the services do not accept or consume shared credential references.

- [ ] **Step 2: Add the three service-owned configuration interfaces**

Use `skill-maker` to declare:

```text
cloud-files.machine.use-google-credential --credential-id <id> [--home <dir>]
g-calendar.machine.use-google-credential --credential-id <id> [--home <dir>]
email-client.machine.accounts-use-google-credential --nickname <nick> --email <addr> --credential-id <id>
```

Each interface validates the credential reference and its required recorded scope before changing service configuration. Drive preserves `remote_llm_root` and timeout settings. Calendar writes only its credential reference. Gmail creates or updates the selected nickname with Gmail OAuth mode, email address, and credential reference while preserving unrelated account fields. Each interface then performs its service-owned smoke check; on failure, it restores the previous service configuration and reports a safe retry route.

Change normal service token refresh to call `google_credentials.refresh_access_token` when `credential_id` is configured. Keep the existing legacy reader as an explicit compatibility branch. Update `ensure-oauth` guidance to prefer `connect-google.machine.authorize-services` plus the service's `use-google-credential` interface instead of starting an independent browser grant. A smoke failure restores only that service's prior non-secret configuration; it does not delete the shared grant.

- [ ] **Step 3: Run service-adapter tests and commit after review**

Run: `python3 -m pytest -q skills/cloud-files/tests/test_cloud_files.py skills/g-calendar/tests/test_gcal.py skills/email-client/tests/test_accounts.py skills/email-client/tests/test_oauth_tokens.py`

Expected: all pass for shared references and legacy compatibility. Commit only Task 3 files with message `feat: consume shared Google credentials`.

---

### Task 4: Orchestrate selected Google setup from the installer

**Files:**
- Modify through `skill-maker`: `skills/install-assistant-tools/blueprint.yaml`
- Modify through `skill-maker`: `skills/install-assistant-tools/SKILL.md`
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Create: `skills/install-assistant-tools/_rtx/_google_onboarding.py`
- Modify: `skills/install-assistant-tools/tests/test_install.py`
- Create: `skills/install-assistant-tools/tests/test_google_onboarding.py`
- Modify: `docs/installation.md`

**Interfaces:**
- Consumes: validated `InstallSelections.google_services` and `InstallSelections.gmail_nickname` from the installer subplan.
- Produces: `run_google_onboarding(services: Sequence[str], *, dispatcher_path: Path, home: Path, gmail_nickname: str | None = None, dry_run: bool = False) -> OnboardingCapabilityResult`.

- [ ] **Step 1: Write the failing optional installer-onboarding tests**

In `test_google_onboarding.py`, give the installer an absolute dispatcher path, a fake dispatcher process, and an `InstallSelections` whose `google_services` contains all three services. Assert installation performs no additional selection prompt and invokes:

```text
<absolute-dispatcher> --caller-skill install-assistant-tools \
  connect-google.machine.authorize-services \
  --service drive --service calendar --service gmail
```

Before authorization, assert the installer calls `connect-google.machine.client-status`. If missing, the script asks for a local Desktop-client JSON path (or accepts the non-interactive flag) and invokes `connect-google.machine.install-client --from-json <path>`; the LLM never reads or transcribes that file. After parsing the authorization result, assert the installer invokes only the granted service-owned configuration interfaces. For Gmail, it uses the already collected `gmail_nickname` and defaults the address from the verified `account` field without another prompt. Assert all service configuration invocations receive the same `credential_id` and none receives a token.

Reuse the installer-runtime subplan's interactive/non-interactive parity tests for selection. Add missing/invalid client, skip, partial-grant, browser denial, malformed JSON, dispatcher failure, and service smoke-failure cases. A partial grant configures only granted services, records the valid credential, and makes the installer report optional status `partial` with exit `3`; it does not call this an incomplete credential replacement. In every actual failure case the core installation remains complete, the result identifies Google onboarding as failed/blocked, and output includes the exact safe dispatcher retry command without secrets.

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_install.py skills/install-assistant-tools/tests/test_google_onboarding.py`

Expected: failures because installation currently defers all remote setup and exposes no Google-service selection.

- [ ] **Step 2: Implement post-install selection and dispatcher-only orchestration**

Create `_google_onboarding.py` as installer orchestration, not an OAuth implementation. It accepts the installed absolute dispatcher path, selected service names, and optional Gmail nickname; invokes only declared dispatcher interfaces; validates the authorization JSON schema; and hands the opaque credential ID to service-owned configuration interfaces. It must not import or execute another skill's private runtime.

Call it from `_phase_entry.py` only after core installation is recorded complete, passing the already validated selections. Empty selection skips onboarding. The script, not the LLM, owns any client-path prompt and validates the path before dispatcher invocation. Non-interactive mode requires an explicit client path when status is missing and never reads stdin. Render the umbrella `InstallResult`: requested partial/blocked/failed Google setup yields exit `3`; core failure yields exit `1`; explicit skip remains exit `0`. Register the sanitized client metadata, client-secret reference, and account credential references in manifest v2; default uninstall preserves them and `--purge` removes them only through their owning interfaces/secret-store API when unreferenced.

Update `docs/installation.md` to distinguish the one-time shared client, one combined authorization per Google account, service-specific configuration, partial grants, later incremental additions, and the separate-account case. State that one authorization cannot cover services intentionally configured for different Google accounts.

- [ ] **Step 3: Run installer-onboarding tests and commit after review**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_install.py skills/install-assistant-tools/tests/test_google_onboarding.py`

Expected: all pass, requested optional failure yields exit `3`, and no second prompt set occurs. Commit only Task 4 files with message `feat: script Google onboarding during installation`.

---

### Task 5: Run the shared-Google integration gate

**Files:**
- Regenerate: all changed `SKILL.md` contract blocks
- Regenerate: `references/blueprint/runtime_dependencies.json`
- Modify: `docs/installation.md`

**Interfaces:**
- Consumes: Tasks 1-4 and the dispatcher error contract.

- [ ] **Step 1: Regenerate contracts and run the focused integration slice**

Regenerate all five changed skill contracts and the runtime dependency artifact through `skill-maker`; do not edit generated SKILL blocks directly.

Run:

```text
python3 -m pytest -q \
  tests/test_officina_google_credentials.py \
  skills/connect-google/tests \
  skills/cloud-files/tests/test_cloud_files.py \
  skills/cloud-files/tests/test_cloud_files_ensure_oauth.py \
  skills/g-calendar/tests/test_gcal.py \
  skills/g-calendar/tests/test_g_calendar_ensure_oauth.py \
  skills/email-client/tests/test_accounts.py \
  skills/email-client/tests/test_oauth_tokens.py \
  skills/install-assistant-tools/tests/test_install.py \
  skills/install-assistant-tools/tests/test_google_onboarding.py \
  tests/validate_blueprints.py
```

Expected: all pass without opening a browser, contacting Google, copying a refresh token into a service store, or invoking a private skill runtime directly.

- [ ] **Step 2: Commit generated integration artifacts after review**

Stage only regenerated dependency/contract artifacts and integration documentation not already included in Tasks 1-4. Commit with message `docs: finalize shared Google onboarding contracts`.

---
