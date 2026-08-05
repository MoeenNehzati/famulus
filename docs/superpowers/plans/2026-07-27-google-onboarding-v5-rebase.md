# Shared Google Onboarding (v5 Rebase) Implementation Plan

> **Version-6 note:** The onboarding requirements remain historical planning
> context, but all v5 facade and `-rtx` blueprint instructions are obsolete.
> Current exports use dotted child IDs and explicit namespace routes; see
> `docs/skill-blueprints.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix feedback item 6 from `docs/plans/osx_feedback_fix/README.md` — one canonical Google client discovery, one combined OAuth grant covering Drive/Calendar/Gmail, and the client secret stored in the OS secret store instead of plaintext — superseding `docs/plans/osx_feedback_fix/03-google-onboarding.md`.

**Architecture:** A new `google_credentials.py` source on the existing `officina.common` module owns the canonical client file (now secret-free — the client secret moves to `secret_store`), a credential registry, and scoped token refresh. `connect-google` gets one new `authorize-services` interface that runs a single loopback OAuth flow across the union of requested service scopes and returns an opaque `credential_id`. `cloud-files`, `g-calendar`, and `email-client` each gain a `use-google-credential`-style interface that stores only that `credential_id`, with the existing independent-OAuth path kept as an explicit, un-migrated legacy branch. The installer's `_phase_entry.py` calls this as a self-contained, script-owned onboarding step after core install completes — not through a not-yet-built `InstallSelections` type.

**Tech Stack:** Python 3.11+, pytest, `keyring`-backed `secret_store`, Google OAuth 2.0/OpenID Connect, JSON Schema (blueprint schema v5).

**Hard dependency:** Task 0 requires `common.source.famulus-paths` from `docs/superpowers/plans/2026-07-27-osx-installer-runtime-v5-rebase.md` Task 1. Check `src/officina/common/blueprint.yaml` for `common.source.famulus-paths` before starting — if that plan has already landed, Task 0 is a verification no-op.

---

## Task 0: Confirm `famulus_paths` prerequisite

**Files:** none new — verification only.

- [ ] **Step 1: Check whether `FamulusPaths` already landed**

Run: `grep -n "common.source.famulus-paths" src/officina/common/blueprint.yaml`
If present: run `python3 -m pytest -q tests/test_officina_famulus_paths.py -v` and expect PASS, then proceed to Task 1.
If absent: stop and land `docs/superpowers/plans/2026-07-27-osx-installer-runtime-v5-rebase.md` Task 1 first (or execute it inline as a prerequisite slice) — every later task in this plan imports `officina.common.famulus_paths.resolve_famulus_paths`.

---

## Task 1: `officina.common.google_credentials` — canonical client file, secret-store client secret, credential registry

**Files:**
- Create: `src/officina/common/google_credentials.py`
- Create: `src/officina/common/blueprints/google-credentials.yaml`
- Modify (via `skill-maker`): `src/officina/common/blueprint.yaml` (add `common.source.google-credentials`; export `common.interface.google-credentials`, `access.allow_all_modules: false`, `allowed_callers: [connect-google, connect-google._rtx]`)
- Modify: `skills/connect-google/_rtx/_client_config.py` (`canonical_client_path` currently hardcodes `Path.home()/".config"/"connect-google"/"client.json"` at lines 30-31; `install_client()` at lines 136-159 currently writes plaintext `client_secret` via `write_oauth_json`)
- Modify (via `skill-maker`): `skills/connect-google/_rtx/blueprints/rtx-client-config.yaml` (`direct_io` paths at lines 38, 190 currently reference the old `$HOME/.config/connect-google/client.json`; `uses_interfaces` at lines 312-314 currently only lists `common.interface.oauth-json`)
- Modify: `skills/connect-google/_rtx/tests/test_client_config.py`
- Create: `tests/test_officina_google_credentials.py`

- [ ] **Step 1: Write failing tests for scope/service helpers**

```python
import pytest

from officina.common.google_credentials import (
    GoogleCredentialError,
    normalize_services,
    scope_union_for_services,
    SERVICE_SCOPES,
    IDENTITY_SCOPES,
)


def test_normalize_services_preserves_order_and_dedupes():
    assert normalize_services(["drive", "calendar", "drive"]) == ("drive", "calendar")


def test_normalize_services_rejects_empty():
    with pytest.raises(GoogleCredentialError):
        normalize_services([])


def test_normalize_services_rejects_unknown_service():
    with pytest.raises(GoogleCredentialError):
        normalize_services(["dropbox"])


def test_scope_union_for_services_includes_identity_scopes():
    scopes = scope_union_for_services(["drive"])
    assert scopes == SERVICE_SCOPES["drive"] | IDENTITY_SCOPES
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q tests/test_officina_google_credentials.py -v`
Expected: FAIL — `officina.common.google_credentials` doesn't exist yet.

- [ ] **Step 3: Implement scope/service helpers and data shapes**

```python
"""Canonical Google client discovery, secret-store client-secret storage,
and per-account credential registry shared across connect-google's service
consumers (cloud-files, g-calendar, email-client)."""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

SERVICE_SCOPES: dict[str, frozenset[str]] = {
    "drive": frozenset({"https://www.googleapis.com/auth/drive"}),
    "calendar": frozenset({"https://www.googleapis.com/auth/calendar"}),
    "gmail": frozenset({"https://mail.google.com/"}),
}
IDENTITY_SCOPES = frozenset({"openid", "email"})


class GoogleCredentialError(RuntimeError):
    pass


def normalize_services(services: Sequence[str]) -> tuple[str, ...]:
    if not services:
        raise GoogleCredentialError("at least one service must be requested")
    seen: dict[str, None] = {}
    for service in services:
        if service not in SERVICE_SCOPES:
            raise GoogleCredentialError(f"unknown service: {service!r}")
        seen.setdefault(service, None)
    return tuple(seen)


def scope_union_for_services(services: Sequence[str]) -> frozenset[str]:
    normalized = normalize_services(services)
    union: frozenset[str] = frozenset(IDENTITY_SCOPES)
    for service in normalized:
        union = union | SERVICE_SCOPES[service]
    return union


@dataclass(frozen=True)
class GoogleCredentialRef:
    credential_id: str
    subject: str
    account: str
    client_id: str
    token_uri: str
    granted_scopes: frozenset[str]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q tests/test_officina_google_credentials.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write failing tests for `install_client` (secret-store migration)**

```python
class FakeSecretBackend:
    def __init__(self):
        self.stored: list[tuple[str, str, str]] = []

    def store(self, namespace: str, key: str, value: str) -> None:
        self.stored.append((namespace, key, value))


def test_install_client_stores_secret_and_strips_it_from_disk(tmp_path):
    from officina.common.google_credentials import install_client
    source = tmp_path / "downloaded-client.json"
    source.write_text(json.dumps({
        "client_id": "abc.apps.googleusercontent.com",
        "client_secret": "sekret-value",
        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }))
    backend = FakeSecretBackend()
    install_client(source, home=tmp_path, platform="linux", replace=False, secret_backend=backend)

    assert backend.stored == [("connect-google", "oauth-client:abc.apps.googleusercontent.com:client-secret", "sekret-value")]

    from officina.common.famulus_paths import resolve_famulus_paths
    installed_path = resolve_famulus_paths(platform="linux", home=tmp_path).config_root / "connect-google" / "client.json"
    installed = json.loads(installed_path.read_text())
    assert "client_secret" not in installed
    assert installed["client_secret_ref"] == "oauth-client:abc.apps.googleusercontent.com:client-secret"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python3 -m pytest -q tests/test_officina_google_credentials.py -k install_client -v`
Expected: FAIL — `install_client` doesn't exist in the module yet.

- [ ] **Step 7: Implement `install_client`, `client_status`, credential storage/refresh**

```python
def _client_path(*, home: Path, platform: str) -> Path:
    from officina.common.famulus_paths import resolve_famulus_paths
    return resolve_famulus_paths(platform=platform, home=home).config_root / "connect-google" / "client.json"


def _credentials_registry_path(*, home: Path, platform: str) -> Path:
    from officina.common.famulus_paths import resolve_famulus_paths
    return resolve_famulus_paths(platform=platform, home=home).config_root / "connect-google" / "credentials.json"


def install_client(source: Path, *, home: Path, platform: str, replace: bool, secret_backend=None) -> dict:
    from officina.common import secret_store as secret_store_module
    backend = secret_backend or secret_store_module
    payload = json.loads(source.read_text())
    client_secret = payload.pop("client_secret", None)
    if client_secret is None:
        raise GoogleCredentialError("source client JSON has no client_secret")
    client_id = payload["client_id"]
    secret_ref = f"oauth-client:{client_id}:client-secret"
    backend.store("connect-google", secret_ref, client_secret)
    payload["client_secret_ref"] = secret_ref
    payload["schema_version"] = 1

    dest = _client_path(home=home, platform=platform)
    if dest.exists() and not replace:
        raise GoogleCredentialError(f"client already installed at {dest}; pass replace=True to overwrite")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2))
    os.replace(tmp_path, dest)
    return payload


def client_status(*, home: Path, platform: str) -> dict:
    path = _client_path(home=home, platform=platform)
    if not path.exists():
        return {"installed": False}
    payload = json.loads(path.read_text())
    return {"installed": True, "client_id": payload.get("client_id")}


def store_google_credential(*, subject: str, account: str, client_id: str, token_uri: str,
                             granted_scopes: frozenset[str], refresh_token: str,
                             home: Path, platform: str, secret_backend=None) -> GoogleCredentialRef:
    from officina.common import secret_store as secret_store_module
    backend = secret_backend or secret_store_module
    credential_id = f"google:{subject}"
    backend.store("connect-google", f"{credential_id}:refresh-token", refresh_token)

    registry_path = _credentials_registry_path(home=home, platform=platform)
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {"schema_version": 1, "credentials": {}}
    registry["credentials"][credential_id] = {
        "subject": subject,
        "account": account,
        "client_id": client_id,
        "token_uri": token_uri,
        "granted_scopes": sorted(granted_scopes),
    }
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = registry_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(registry, indent=2))
    os.replace(tmp_path, registry_path)
    return GoogleCredentialRef(credential_id=credential_id, subject=subject, account=account,
                                client_id=client_id, token_uri=token_uri, granted_scopes=frozenset(granted_scopes))


def load_credential(credential_id: str, *, home: Path, platform: str) -> GoogleCredentialRef:
    registry_path = _credentials_registry_path(home=home, platform=platform)
    if not registry_path.exists():
        raise GoogleCredentialError(f"no credential registry at {registry_path}")
    registry = json.loads(registry_path.read_text())
    record = registry["credentials"].get(credential_id)
    if record is None:
        raise GoogleCredentialError(f"unknown credential_id: {credential_id}")
    return GoogleCredentialRef(
        credential_id=credential_id, subject=record["subject"], account=record["account"],
        client_id=record["client_id"], token_uri=record["token_uri"],
        granted_scopes=frozenset(record["granted_scopes"]),
    )


def refresh_access_token(credential_id: str, *, required_scopes: Collection[str],
                          home: Path | None = None, platform: str | None = None,
                          urlopen: Callable = None) -> str:
    import sys
    import urllib.request
    urlopen = urlopen or urllib.request.urlopen
    ref = load_credential(credential_id, home=home or Path.home(), platform=platform or sys.platform)
    if not set(required_scopes) <= ref.granted_scopes:
        raise GoogleCredentialError(
            f"credential {credential_id} lacks required scopes: {set(required_scopes) - ref.granted_scopes}"
        )
    from officina.common import secret_store as secret_store_module
    client_secret = secret_store_module.require("connect-google", f"oauth-client:{ref.client_id}:client-secret")
    refresh_token = secret_store_module.require("connect-google", f"{credential_id}:refresh-token")
    # Token exchange via urlopen against ref.token_uri, posting client_id/client_secret/refresh_token/grant_type=refresh_token.
    # Implementation detail deferred to Step 7b below (kept separate so the scope-check-before-network-call
    # ordering, which is the behavior under test, is unambiguous).
    return _exchange_refresh_token(ref=ref, client_secret=client_secret, refresh_token=refresh_token, urlopen=urlopen)


def _exchange_refresh_token(*, ref: GoogleCredentialRef, client_secret: str, refresh_token: str, urlopen: Callable) -> str:
    import urllib.parse
    import urllib.request
    data = urllib.parse.urlencode({
        "client_id": ref.client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    request = urllib.request.Request(ref.token_uri, data=data, method="POST")
    with urlopen(request) as response:
        payload = json.loads(response.read())
    return payload["access_token"]
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python3 -m pytest -q tests/test_officina_google_credentials.py -v`
Expected: PASS

- [ ] **Step 9: Write and pass the scope-check-before-network test**

```python
def test_refresh_access_token_checks_scopes_before_network_call(tmp_path):
    from officina.common.google_credentials import (
        GoogleCredentialError, refresh_access_token, store_google_credential,
    )
    store_google_credential(
        subject="sub1", account="user@example.com", client_id="abc", token_uri="https://oauth2.googleapis.com/token",
        granted_scopes=frozenset({"openid", "email", "https://www.googleapis.com/auth/drive"}),
        refresh_token="rt", home=tmp_path, platform="linux", secret_backend=FakeSecretBackend(),
    )

    def fail_if_called(*a, **k):
        raise AssertionError("urlopen should not be called when required scopes are missing")

    with pytest.raises(GoogleCredentialError):
        refresh_access_token("google:sub1", required_scopes={"https://mail.google.com/"},
                              home=tmp_path, platform="linux", urlopen=fail_if_called)
```

Run: `python3 -m pytest -q tests/test_officina_google_credentials.py -v`
Expected: PASS, all tests green.

- [ ] **Step 10: Migrate `_client_config.py` to delegate to `google_credentials`**

Replace `canonical_client_path`'s hardcoded `Path.home()/".config"/"connect-google"/"client.json"` with a call into `google_credentials._client_path`-equivalent (expose it, or call `client_status`/`install_client` directly rather than duplicating path logic). Replace `install_client()`'s direct `write_oauth_json` call with `google_credentials.install_client(...)`.

- [ ] **Step 11: Run connect-google tests, verify no plaintext secret regression**

Run: `python3 -m pytest -q skills/connect-google/_rtx/tests/test_client_config.py -v`
Expected: PASS; add/confirm an assertion that the installed file never contains `client_secret`.

- [ ] **Step 12: Blueprint updates through `skill-maker`**

Register `common.source.google-credentials` on `src/officina/common/blueprint.yaml`; update `skills/connect-google/_rtx/blueprints/rtx-client-config.yaml`'s `direct_io` paths and `uses_interfaces` to add `common.interface.google-credentials`.

- [ ] **Step 13: Commit**

```bash
git add src/officina/common/google_credentials.py src/officina/common/blueprints/google-credentials.yaml src/officina/common/blueprint.yaml skills/connect-google/_rtx/_client_config.py skills/connect-google/_rtx/blueprints/rtx-client-config.yaml skills/connect-google/_rtx/tests/test_client_config.py tests/test_officina_google_credentials.py
git commit -m "feat(officina.common): canonical Google client discovery, client secret in secret store not plaintext"
```

---

## Task 2: Combined-authorization interface (`authorize-services`)

**Files:**
- Create: `skills/connect-google/_rtx/_authorize_services.py`
- Create: `skills/connect-google/_rtx/blueprints/rtx-authorize-services.yaml`
- Modify (via `skill-maker`): `skills/connect-google/_rtx/blueprint.yaml` (register source), `skills/connect-google/blueprint.yaml` (add facade export `connect-google._rtx.interface.authorize-services`, mirroring the existing `connect-google._rtx.interface.install-client` facade at lines 40-47)
- Modify: `skills/connect-google/instructions/connect-services.md` (real v5 path — not `llm_interfaces/`, which no longer exists)
- Create: `skills/connect-google/_rtx/tests/test_authorize_services.py`
- Modify: `skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py`, `test_service_delegation.py`

- [ ] **Step 1: Write failing tests for the scope/URL construction and result shape**

```python
def test_authorize_services_builds_correct_scope_union(monkeypatch):
    captured = {}
    def fake_open_browser(url):
        captured["url"] = url
    result = authorize_services(
        ["drive", "calendar", "gmail"], home=tmp_path, account_hint=None,
        open_browser=fake_open_browser, urlopen=fake_urlopen_returning_valid_token_and_userinfo,
    )
    assert "code_challenge_method=S256" in captured["url"]
    assert "access_type=offline" in captured["url"]


def test_authorize_services_rejects_unknown_service():
    with pytest.raises(GoogleCredentialError):
        authorize_services(["dropbox"], home=tmp_path, account_hint=None)


def test_authorize_services_result_payload_shape(monkeypatch):
    result = authorize_services(["drive"], home=tmp_path, account_hint=None,
                                 open_browser=lambda url: None, urlopen=fake_urlopen_returning_valid_token_and_userinfo)
    payload = result.as_payload()
    assert payload["schema_version"] == 1
    assert set(payload) == {"schema_version", "account", "credential_id", "requested_services", "granted_services", "denied_services"}


def test_authorize_services_partial_grant_still_stores_granted_subset(monkeypatch):
    result = authorize_services(["drive", "gmail"], home=tmp_path, account_hint=None,
                                 open_browser=lambda url: None, urlopen=fake_urlopen_granting_only_drive_scope)
    assert result.granted_services == ("drive",)
    assert result.denied_services == ("gmail",)
    # credential IS stored for the granted subset:
    from officina.common.google_credentials import load_credential
    ref = load_credential(result.credential_id, home=tmp_path, platform="linux")
    assert "https://www.googleapis.com/auth/drive" in ref.granted_scopes
    assert "https://mail.google.com/" not in ref.granted_scopes


def test_authorize_services_state_mismatch_stores_no_credential(monkeypatch, tmp_path):
    with pytest.raises(GoogleCredentialError):
        authorize_services(["drive"], home=tmp_path, account_hint=None,
                            open_browser=lambda url: None, urlopen=fake_urlopen_with_mismatched_state)
    registry_path = tmp_path_config_root(tmp_path) / "connect-google" / "credentials.json"
    assert not registry_path.exists()


def test_authorize_services_never_calls_real_network(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("no real network call permitted in tests")
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr("webbrowser.open", forbidden)
```

(These fakes — `fake_urlopen_returning_valid_token_and_userinfo`, `fake_urlopen_granting_only_drive_scope`, `fake_urlopen_with_mismatched_state`, `tmp_path_config_root` — must be written as real local test helpers producing well-formed fake HTTP responses shaped like Google's token/UserInfo endpoints; build them alongside these tests, matching whatever the real loopback-server implementation in Step 3 expects to receive.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/connect-google/_rtx/tests/test_authorize_services.py -v`
Expected: FAIL — `_authorize_services.py` doesn't exist yet.

- [ ] **Step 3: Implement `_authorize_services.py`**

Implement `authorize_services(services, *, home, account_hint, open_browser=webbrowser.open, urlopen=urllib.request.urlopen)`:
1. `normalize_services(services)`, `scope_union_for_services(services)`.
2. Load the canonical client via `google_credentials.client_status`/read the client file directly for `client_id`/`auth_uri`/`token_uri`/`client_secret_ref`.
3. Generate PKCE `code_verifier`/`code_challenge` and a random `state`.
4. Start a loopback HTTP server on an ephemeral port, build the authorization URL with `scope`, `access_type=offline`, `prompt=consent`, `state`, `code_challenge`, `code_challenge_method=S256`, call `open_browser(url)`.
5. Wait for exactly one callback; verify `state` matches (raise `GoogleCredentialError` and stop — no credential written — on mismatch).
6. Exchange the authorization code for tokens via `urlopen` POST to `token_uri` (using `client_secret` from `secret_store.require`).
7. Fetch OpenID UserInfo; verify `email_verified is True`; if `account_hint` was given and doesn't match `email`, raise `GoogleCredentialError` without storing.
8. Classify granted services from the actual returned scope set (partial grants are not rolled back — call `google_credentials.store_google_credential` with only the granted subset).
9. Return `AuthorizationResult(account=..., credential_id=..., requested_services=normalize_services(services), granted_services=..., denied_services=...)`.

```python
@dataclass(frozen=True)
class AuthorizationResult:
    account: str
    credential_id: str
    requested_services: tuple[str, ...]
    granted_services: tuple[str, ...]
    denied_services: tuple[str, ...]

    def as_payload(self) -> dict:
        return {
            "schema_version": 1,
            "account": self.account,
            "credential_id": self.credential_id,
            "requested_services": list(self.requested_services),
            "granted_services": list(self.granted_services),
            "denied_services": list(self.denied_services),
        }
```

Print only `json.dumps(result.as_payload())` on stdout when invoked as a CLI entry point (`__main__`), so credentials/tokens never appear in dispatcher/log capture.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/connect-google/_rtx/tests/test_authorize_services.py -v`
Expected: PASS, all cases (unknown service, duplicate service, state mismatch, partial grant, account-hint mismatch, no real network).

- [ ] **Step 5: Update `instructions/connect-services.md`**

Rewrite the "Service-owned handoff" section to describe: call `connect-google._rtx.interface.authorize-services` once for the selected services, then have each service call its own `use-google-credential` interface with the returned `credential_id` — not independent per-service OAuth.

- [ ] **Step 6: Run routing/delegation tests**

Run: `python3 -m pytest -q skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py skills/connect-google/_rtx/tests/test_service_delegation.py -v`
Expected: PASS after updating any assertions that referenced the old three-independent-OAuth wording.

- [ ] **Step 7: Blueprint updates through `skill-maker`**

Register `connect-google._rtx.source.rtx-authorize-services`; export `connect-google._rtx.interface.authorize-services`; add facade `connect-google._rtx.interface.authorize-services` on the parent module.

- [ ] **Step 8: Commit**

```bash
git add skills/connect-google/_rtx/_authorize_services.py skills/connect-google/_rtx/blueprints/rtx-authorize-services.yaml skills/connect-google/_rtx/blueprint.yaml skills/connect-google/blueprint.yaml skills/connect-google/instructions/connect-services.md skills/connect-google/_rtx/tests/
git commit -m "feat(connect-google): one combined OAuth grant across Drive/Calendar/Gmail via authorize-services"
```

---

## Task 3: Service consumers — `use-google-credential` for cloud-files, g-calendar, email-client

**Files:**
- Modify: `skills/cloud-files/_rtx/_ensure_oauth.py`, `skills/cloud-files/_rtx/_drive_gateway.py`
- Modify (via `skill-maker`): `skills/cloud-files/_rtx/blueprint.yaml`, `skills/cloud-files/blueprint.yaml`
- Modify: `skills/cloud-files/tests/test_cloud_files.py`, `test_cloud_files_ensure_oauth.py`
- Modify: `skills/g-calendar/_rtx/_ensure_oauth.py`, `skills/g-calendar/_rtx/_gcal_client.py`
- Modify (via `skill-maker`): `skills/g-calendar/_rtx/blueprint.yaml`, `skills/g-calendar/blueprint.yaml`
- Modify: `skills/g-calendar/tests/test_gcal.py` and the real `_rtx/tests/` oauth test file (confirm exact filename before editing)
- Modify: `skills/email-client/_rtx/_email_accounts.py`, `skills/email-client/_rtx/_oauth_tokens.py`
- Modify (via `skill-maker`): `skills/email-client/_rtx/blueprint.yaml`, `skills/email-client/blueprint.yaml`
- Modify: `skills/email-client/tests/test_accounts.py`, `test_oauth_tokens.py`
- Modify (via `skill-maker`): `src/officina/common/blueprint.yaml` (widen `common.interface.google-credentials.allowed_callers` to add `cloud-files._rtx`, `g-calendar._rtx`, `email-client._rtx`)

- [ ] **Step 1: Write failing tests for one service (cloud-files) first**

```python
def test_use_google_credential_stores_only_credential_id(tmp_path, fake_registry_with_drive_scope):
    use_google_credential(credential_id="google:sub1", home=tmp_path, platform="linux")
    config = json.loads((tmp_path / "config.json").read_text())  # match real config path
    assert config["credential_id"] == "google:sub1"
    assert "client_secret" not in config
    assert "refresh_token" not in config


def test_use_google_credential_rejects_insufficient_scope(tmp_path, fake_registry_missing_drive_scope):
    with pytest.raises(GoogleCredentialError):
        use_google_credential(credential_id="google:sub1", home=tmp_path, platform="linux")


def test_token_refresh_uses_shared_credential_when_present(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "officina.common.google_credentials.refresh_access_token",
        lambda cred_id, **k: calls.append((cred_id, k)) or "fake-access-token",
    )
    token = get_access_token(config={"credential_id": "google:sub1"}, home=tmp_path, platform="linux")
    assert token == "fake-access-token"
    assert calls[0][0] == "google:sub1"


def test_token_refresh_falls_back_to_legacy_path_without_credential_id(monkeypatch, tmp_path):
    # Existing legacy secret_store-based flow must be untouched when credential_id is absent.
    token = get_access_token(config={"legacy_field": "..."}, home=tmp_path, platform="linux")
    # Assert against whatever the CURRENT legacy behavior already does (read it before writing this test).
```

(Match real function names in `_ensure_oauth.py`/`_drive_gateway.py` — read both files first; this task adds to existing code, it does not replace the legacy OAuth path.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/cloud-files/tests/test_cloud_files_ensure_oauth.py -k use_google_credential -v`
Expected: FAIL — `use_google_credential` doesn't exist yet.

- [ ] **Step 3: Implement `use_google_credential` for cloud-files**

```python
def use_google_credential(*, credential_id: str, home: Path, platform: str) -> None:
    from officina.common.google_credentials import load_credential, SERVICE_SCOPES, GoogleCredentialError
    ref = load_credential(credential_id, home=home, platform=platform)
    if not SERVICE_SCOPES["drive"] <= ref.granted_scopes:
        raise GoogleCredentialError(f"credential {credential_id} lacks Drive scope")
    # Write only credential_id into cloud-files' own config file, reusing whatever
    # atomic-write helper _ensure_oauth.py/_drive_gateway.py already uses.
    ...
```

Add a `get_access_token(config, *, home, platform)` branch: if `config.get("credential_id")`, call `google_credentials.refresh_access_token(credential_id, required_scopes=SERVICE_SCOPES["drive"], home=home, platform=platform)`; else fall back to the existing legacy per-service flow unchanged.

- [ ] **Step 4: Run tests to verify they pass, then repeat Steps 1-3 for g-calendar and email-client**

Run: `python3 -m pytest -q skills/cloud-files/tests/test_cloud_files_ensure_oauth.py -v`
Expected: PASS.

Repeat the same pattern (write failing tests → implement `use_google_credential`/`accounts_use_google_credential` → pass) for:
- `skills/g-calendar/_rtx/_ensure_oauth.py` + `_gcal_client.py` (required scope: `SERVICE_SCOPES["calendar"]`)
- `skills/email-client/_rtx/_email_accounts.py` + `_oauth_tokens.py` (required scope: `SERVICE_SCOPES["gmail"]`)

Run after each: `python3 -m pytest -q skills/g-calendar/tests/ -v` then `python3 -m pytest -q skills/email-client/tests/ -v`.
Expected: PASS for each, no regressions in existing legacy-path tests.

- [ ] **Step 5: Regression-test the legacy path is untouched**

Run the full existing test suites for all three services and confirm zero failures — a legacy Drive/Calendar credential file or Gmail keyring entry with no `credential_id` must be read exactly as before.

Run: `python3 -m pytest -q skills/cloud-files/tests/ skills/g-calendar/tests/ skills/email-client/tests/ -v`
Expected: PASS, full suites, zero regressions.

- [ ] **Step 6: Blueprint updates through `skill-maker`**

Add `cloud-files._rtx.interface.use-google-credential`, `g-calendar._rtx.interface.use-google-credential`, `email-client._rtx.interface.accounts-use-google-credential` (plus matching facades on each parent module blueprint). Widen `common.interface.google-credentials`'s `allowed_callers` in `src/officina/common/blueprint.yaml` to include all three `-rtx` modules.

- [ ] **Step 7: Commit**

```bash
git add skills/cloud-files/_rtx/_ensure_oauth.py skills/cloud-files/_rtx/_drive_gateway.py skills/cloud-files/_rtx/blueprint.yaml skills/cloud-files/blueprint.yaml skills/cloud-files/tests/ skills/g-calendar/_rtx/_ensure_oauth.py skills/g-calendar/_rtx/_gcal_client.py skills/g-calendar/_rtx/blueprint.yaml skills/g-calendar/blueprint.yaml skills/g-calendar/tests/ skills/email-client/_rtx/_email_accounts.py skills/email-client/_rtx/_oauth_tokens.py skills/email-client/_rtx/blueprint.yaml skills/email-client/blueprint.yaml skills/email-client/tests/ src/officina/common/blueprint.yaml
git commit -m "feat(google-services): consume shared credential_id, keep legacy per-service OAuth as untouched fallback"
```

---

## Task 4: Installer orchestration (self-contained, wired after core install completes)

**Files:**
- Create: `skills/install-assistant-tools/_rtx/_google_onboarding.py`
- Create: `skills/install-assistant-tools/_rtx/blueprints/rtx-google-onboarding.yaml`
- Modify (via `skill-maker`): `skills/install-assistant-tools/_rtx/blueprint.yaml` (register source)
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py` (call after the point the installer-runtime rebase's Task 7 marks core install complete — after `build_candidate_release` + scaffold succeed)
- Create: `skills/install-assistant-tools/tests/test_google_onboarding.py`
- Modify: `skills/install-assistant-tools/tests/test_install.py`

- [ ] **Step 1: Write failing tests**

```python
def test_run_google_onboarding_calls_dispatcher_in_order(fake_dispatcher):
    run_google_onboarding(
        ["drive", "calendar", "gmail"], dispatcher_path=fake_dispatcher.path,
        home=tmp_home, stdin_isatty=False,
    )
    assert fake_dispatcher.calls[0][0] == "connect-google.machine.client-status"
    assert fake_dispatcher.calls[-1][0].startswith("connect-google.machine.authorize-services") or \
           any(c[0].startswith("connect-google.machine.authorize-services") for c in fake_dispatcher.calls)


def test_run_google_onboarding_non_interactive_with_no_services_does_not_block(fake_dispatcher):
    result = run_google_onboarding(None, dispatcher_path=fake_dispatcher.path, home=tmp_home, stdin_isatty=False)
    assert result.status in {"skipped", "needs_selection"}
    assert fake_dispatcher.calls == []


def test_run_google_onboarding_output_never_contains_secrets(fake_dispatcher_with_leaky_stdout):
    result = run_google_onboarding(["drive"], dispatcher_path=fake_dispatcher_with_leaky_stdout.path, home=tmp_home, stdin_isatty=False)
    dumped = str(result)
    assert "client_secret" not in dumped
    assert "refresh_token" not in dumped


def test_phase_entry_calls_google_onboarding_only_after_core_install_succeeds(monkeypatch, tmp_home):
    calls = []
    monkeypatch.setattr("_install_scaffold... build_candidate_release", lambda **k: calls.append("build") or fake_release)
    monkeypatch.setattr("_google_onboarding.run_google_onboarding", lambda *a, **k: calls.append("google"))
    run_install(home=tmp_home, mode="plugin")
    assert calls.index("build") < calls.index("google")


def test_phase_entry_skips_google_onboarding_on_core_install_failure(monkeypatch, tmp_home):
    def fail(**k):
        raise ManagedRuntimeError("simulated")
    monkeypatch.setattr("... build_candidate_release", fail)
    called = []
    monkeypatch.setattr("_google_onboarding.run_google_onboarding", lambda *a, **k: called.append(True))
    run_install(home=tmp_home, mode="plugin")
    assert called == []
```

(Match real `_phase_entry.py`/`_install_scaffold.py` function names from the installer-runtime rebase plan's Task 7 wiring.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_google_onboarding.py -v`
Expected: FAIL — `_google_onboarding.py` doesn't exist yet.

- [ ] **Step 3: Implement `_google_onboarding.py`**

```python
"""Self-contained, script-owned Google onboarding step run after core
install completes. Does not depend on any InstallSelections type — that
wizard contract was never built; service selection here is either passed
explicitly (non-interactive) or prompted (interactive), matching the same
stdin_isatty pattern already used in cloud-files/_rtx/_ensure_oauth.py."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OnboardingCapabilityResult:
    status: str  # "completed" | "partial" | "skipped" | "needs_selection" | "failed"
    credential_id: str | None = None
    granted_services: tuple[str, ...] = ()
    denied_services: tuple[str, ...] = ()


def run_google_onboarding(services, *, dispatcher_path: Path, home: Path,
                           gmail_nickname: str | None = None, dry_run: bool = False,
                           stdin_isatty: bool | None = None) -> OnboardingCapabilityResult:
    if not services:
        if stdin_isatty:
            services = _prompt_for_services()
            if not services:
                return OnboardingCapabilityResult(status="skipped")
        else:
            return OnboardingCapabilityResult(status="needs_selection")

    if dry_run:
        return OnboardingCapabilityResult(status="skipped")

    status = _dispatch(dispatcher_path, "connect-google.machine.client-status", home=home)
    if not status.get("installed"):
        return OnboardingCapabilityResult(status="failed")

    auth_result = _dispatch(
        dispatcher_path,
        "connect-google.machine.authorize-services",
        *[arg for service in services for arg in ("--service", service)],
        home=home,
    )
    credential_id = auth_result["credential_id"]
    granted = tuple(auth_result["granted_services"])
    for service in granted:
        _dispatch(dispatcher_path, f"{_service_module(service)}.machine.use-google-credential",
                  "--credential-id", credential_id, home=home)

    denied = tuple(auth_result["denied_services"])
    return OnboardingCapabilityResult(
        status="completed" if not denied else "partial",
        credential_id=credential_id, granted_services=granted, denied_services=denied,
    )


def _service_module(service: str) -> str:
    return {"drive": "cloud-files", "calendar": "g-calendar", "gmail": "email-client"}[service]


def _dispatch(dispatcher_path: Path, interface: str, *args: str, home: Path) -> dict:
    result = subprocess.run(
        [str(dispatcher_path), "--caller-skill", "install-assistant-tools", interface, *args],
        capture_output=True, text=True, cwd=str(home),
    )
    if result.returncode != 0:
        raise RuntimeError(f"{interface} failed: exit {result.returncode}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def _prompt_for_services() -> tuple[str, ...]:
    raise NotImplementedError("interactive prompting implemented alongside the installer wizard")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_google_onboarding.py -v`
Expected: PASS.

- [ ] **Step 5: Wire into `_phase_entry.py`**

Add the call to `run_google_onboarding(...)` immediately after the point Task 7 of the installer-runtime rebase plan marks core installation complete (after `build_candidate_release` + scaffold both succeed). On `partial`/`failed` status, do not fail the overall install — report it in the final install summary per the umbrella's "explicit optional failures produce exit 3, preserve core install" constraint.

- [ ] **Step 6: Run integration test**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_install.py skills/install-assistant-tools/tests/test_google_onboarding.py -v`
Expected: PASS.

- [ ] **Step 7: Blueprint updates through `skill-maker`**

Register `install-assistant-tools._rtx.source.rtx-google-onboarding`; add `uses_interfaces: [{interface: common.interface.famulus-paths, version: 1}]` (the dispatcher calls to `connect-google.machine.*` are cross-process, not direct Python imports, so they don't need a `uses_interfaces` declaration — only the direct `famulus_paths` import does).

- [ ] **Step 8: Commit**

```bash
git add skills/install-assistant-tools/_rtx/_google_onboarding.py skills/install-assistant-tools/_rtx/blueprints/rtx-google-onboarding.yaml skills/install-assistant-tools/_rtx/blueprint.yaml skills/install-assistant-tools/_rtx/_phase_entry.py skills/install-assistant-tools/tests/
git commit -m "feat(install-assistant-tools): self-contained Google onboarding step after core install completes"
```

---

## Task 5: Integration gate

**Files:** none new — regenerate contracts and run the full focused slice.

- [ ] **Step 1: Regenerate blueprint contracts**

Run `skill-maker`'s sync for every touched module (`officina.common`, `connect-google`, `cloud-files`, `g-calendar`, `email-client`, `install-assistant-tools`); regenerate each `SKILL.md` and `references/blueprint/runtime_dependencies.json`.

- [ ] **Step 2: Run the full focused suite**

Run:
```
python3 -m pytest -q \
  tests/test_officina_famulus_paths.py \
  tests/test_officina_google_credentials.py \
  skills/connect-google/_rtx/tests \
  skills/cloud-files/tests \
  skills/g-calendar/tests \
  skills/email-client/tests \
  skills/install-assistant-tools/tests/test_install.py skills/install-assistant-tools/tests/test_google_onboarding.py
```
Expected: all PASS.

- [ ] **Step 3: Grep for secret leakage across the whole run**

Run the suite with `-s` and capture output; grep the captured transcript for `client_secret`, `refresh_token`, `access_token` substrings.
Expected: zero matches.

- [ ] **Step 4: Validate blueprints**

Run the repo's blueprint validation entrypoint across all six touched modules.
Expected: zero orphaned exports, zero dangling `uses_interfaces`.

- [ ] **Step 5: Commit**

```bash
git add <regenerated SKILL.md files and runtime_dependencies.json>
git commit -m "chore: sync blueprints after Google onboarding v5 rebase"
```

---

## Dependency order summary

```
Task 0 (famulus_paths verified/landed)
  -> Task 1 (google_credentials + secret-store client secret)
       -> Task 2 (authorize-services combined grant)
            -> Task 3 (cloud-files, g-calendar, email-client consumers)
                 -> Task 4 (installer orchestration)
                      -> Task 5 (integration gate)
```

## Explicitly out of scope

- Migrating or deleting existing per-service refresh tokens for already-connected accounts — the legacy path stays as an explicit, untouched fallback (matches the umbrella's own out-of-scope line).
- Building the `InstallSelections` typed wizard contract — Task 4's onboarding step is deliberately self-contained and script-owned instead, since that wizard was never built by the (also-frozen) installer-runtime subplan.
