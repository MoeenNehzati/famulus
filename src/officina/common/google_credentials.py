"""Canonical Google client discovery, secret-store client-secret storage,
and per-account credential registry shared across connect-google's service
consumers (cloud-files, g-calendar, email-client).

The installed Google Desktop OAuth client JSON keeps the shape Google's
Cloud Console actually exports (a top-level ``installed`` object). This
module never writes the client's ``client_secret`` to disk: it stores the
secret through ``officina.common.secret_store`` and replaces it on disk with
a ``client_secret_ref`` pointing at the stored secret.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import time
import urllib.error
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SERVICE_SCOPES: dict[str, frozenset[str]] = {
    "drive": frozenset({"https://www.googleapis.com/auth/drive"}),
    "calendar": frozenset({"https://www.googleapis.com/auth/calendar"}),
    "gmail": frozenset({"https://mail.google.com/"}),
}
IDENTITY_SCOPES = frozenset({"openid", "email"})


class GoogleCredentialError(RuntimeError):
    """Raised for invalid service/scope requests and credential failures."""


def normalize_services(services: Sequence[str]) -> tuple[str, ...]:
    """Validate and dedupe a requested service list, preserving first-seen order."""
    if not services:
        raise GoogleCredentialError("at least one service must be requested")
    seen: dict[str, None] = {}
    for service in services:
        if service not in SERVICE_SCOPES:
            raise GoogleCredentialError(f"unknown service: {service!r}")
        seen.setdefault(service, None)
    return tuple(seen)


def scope_union_for_services(services: Sequence[str]) -> frozenset[str]:
    """Return the OAuth scope union for the requested services plus identity scopes."""
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


@dataclass(frozen=True)
class GoogleCredentialFile:
    """One immutable authorization descriptor backed by secret-store references.

    ``path`` is the normalized absolute descriptor path used directly by service
    configurations. The descriptor contains no raw OAuth secret or access token;
    ``client_secret_ref`` and ``refresh_token_ref`` are resolved only when an
    access token is refreshed.
    """

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


_CREDENTIAL_FILE_FIELDS = frozenset(
    {
        "schema_version",
        "created_at",
        "subject",
        "account",
        "client_id",
        "token_uri",
        "granted_services",
        "granted_scopes",
        "client_secret_ref",
        "refresh_token_ref",
    }
)
_CREDENTIAL_FILE_ID_RE = re.compile(r"[0-9a-f]{8}")
_CREDENTIAL_FILE_STEM_RE = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)-(?P<unique_id>[0-9a-f]{8})"
)


def canonical_client_path(*, home: Path, platform: str) -> Path:
    """Return the single canonical Google Desktop OAuth client path."""
    from officina.common.famulus_paths import resolve_famulus_paths

    return resolve_famulus_paths(platform=platform, home=Path(home)).config_root / "connect-google" / "client.json"


def _credentials_registry_path(*, home: Path, platform: str) -> Path:
    from officina.common.famulus_paths import resolve_famulus_paths

    return resolve_famulus_paths(platform=platform, home=Path(home)).config_root / "connect-google" / "credentials.json"


def _credential_files_dir(*, home: Path, platform: str) -> Path:
    """Return the directory containing immutable per-authorization descriptors."""
    from officina.common.famulus_paths import resolve_famulus_paths

    return (
        resolve_famulus_paths(platform=platform, home=Path(home)).config_root
        / "connect-google"
        / "credentials"
    )


def _validated_granted_services(services: Sequence[str]) -> tuple[str, ...]:
    """Validate a possibly empty, already-deduplicated granted-service list."""
    result: list[str] = []
    for service in services:
        if not isinstance(service, str) or service not in SERVICE_SCOPES:
            raise GoogleCredentialError(f"unknown granted service: {service!r}")
        if service in result:
            raise GoogleCredentialError(f"duplicate granted service: {service!r}")
        result.append(service)
    return tuple(result)


def create_credential_file(
    *,
    subject: str,
    account: str,
    client_id: str,
    token_uri: str,
    granted_services: Sequence[str],
    granted_scopes: frozenset[str],
    refresh_token: str,
    home: Path,
    platform: str,
    now: datetime | None = None,
    unique_id: str | None = None,
    secret_backend=None,
) -> GoogleCredentialFile:
    """Create one non-overwriting credential descriptor and store its refresh token.

    The full timestamped file stem is also the refresh-token secret identity. A
    candidate path is exclusively created before the secret store is touched, so
    even a forced filename collision cannot replace an older descriptor's token.
    """
    from officina.common import secret_store as secret_store_module

    for label, value in (
        ("subject", subject),
        ("account", account),
        ("client_id", client_id),
        ("token_uri", token_uri),
        ("refresh_token", refresh_token),
    ):
        if not isinstance(value, str) or not value:
            raise GoogleCredentialError(f"{label} must be a non-empty string")

    services = _validated_granted_services(granted_services)
    if not isinstance(granted_scopes, frozenset) or not all(
        isinstance(scope, str) and scope for scope in granted_scopes
    ):
        raise GoogleCredentialError("granted_scopes must be a frozenset of non-empty strings")
    for service in services:
        if not SERVICE_SCOPES[service] <= granted_scopes:
            raise GoogleCredentialError(f"granted service {service!r} lacks its required scopes")

    timestamp = now or datetime.now(UTC)
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise GoogleCredentialError("now must be a timezone-aware datetime")
    timestamp = timestamp.astimezone(UTC).replace(microsecond=0)
    created_at = timestamp.isoformat().replace("+00:00", "Z")
    timestamp_slug = timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")

    artifact_id = unique_id or secrets.token_hex(4)
    if not isinstance(artifact_id, str) or _CREDENTIAL_FILE_ID_RE.fullmatch(artifact_id) is None:
        raise GoogleCredentialError("unique_id must be exactly eight lowercase hexadecimal characters")

    stem = f"{timestamp_slug}-{artifact_id}"
    client_secret_ref = f"oauth-client:{client_id}:client-secret"
    refresh_token_ref = f"credential-file:{stem}:refresh-token"
    payload = {
        "schema_version": 1,
        "created_at": created_at,
        "subject": subject,
        "account": account,
        "client_id": client_id,
        "token_uri": token_uri,
        "granted_services": list(services),
        "granted_scopes": sorted(granted_scopes),
        "client_secret_ref": client_secret_ref,
        "refresh_token_ref": refresh_token_ref,
    }

    directory = _credential_files_dir(home=home, platform=platform)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise GoogleCredentialError(f"credential file already exists: {path}") from exc
    except OSError as exc:
        raise GoogleCredentialError(f"could not create credential file {path}: {exc}") from exc

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        secret_store_module.store(
            "connect-google",
            refresh_token_ref,
            refresh_token,
            backend=secret_backend,
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return GoogleCredentialFile(
        path=path.resolve(strict=True),
        created_at=created_at,
        subject=subject,
        account=account,
        client_id=client_id,
        token_uri=token_uri,
        granted_services=services,
        granted_scopes=granted_scopes,
        client_secret_ref=client_secret_ref,
        refresh_token_ref=refresh_token_ref,
    )


def load_credential_file(path: Path) -> GoogleCredentialFile:
    """Load and fully validate one generated credential descriptor."""
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise GoogleCredentialError(f"credential file must not be a symbolic link: {candidate}")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise GoogleCredentialError(f"credential file does not exist: {candidate}") from exc
    except OSError as exc:
        raise GoogleCredentialError(f"could not resolve credential file {candidate}: {exc}") from exc
    if not resolved.is_file():
        raise GoogleCredentialError(f"credential path is not a regular file: {resolved}")

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GoogleCredentialError(f"invalid credential JSON at {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise GoogleCredentialError("credential descriptor must be a JSON object")
    if set(payload) != _CREDENTIAL_FILE_FIELDS:
        raise GoogleCredentialError(
            f"credential descriptor fields differ from schema: {sorted(set(payload) ^ _CREDENTIAL_FILE_FIELDS)}"
        )
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise GoogleCredentialError("unsupported credential schema_version")

    for field in (
        "created_at",
        "subject",
        "account",
        "client_id",
        "token_uri",
        "client_secret_ref",
        "refresh_token_ref",
    ):
        if not isinstance(payload[field], str) or not payload[field]:
            raise GoogleCredentialError(f"credential field {field} must be a non-empty string")

    stem_match = _CREDENTIAL_FILE_STEM_RE.fullmatch(resolved.stem)
    if stem_match is None:
        raise GoogleCredentialError("credential filename is not canonical")
    expected_created_at = (
        datetime.strptime(stem_match.group("timestamp"), "%Y-%m-%dT%H-%M-%SZ")
        .replace(tzinfo=UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )
    if payload["created_at"] != expected_created_at:
        raise GoogleCredentialError("credential created_at does not match its filename")

    raw_services = payload["granted_services"]
    if not isinstance(raw_services, list):
        raise GoogleCredentialError("credential granted_services must be a list")
    services = _validated_granted_services(raw_services)
    raw_scopes = payload["granted_scopes"]
    if not isinstance(raw_scopes, list) or not all(
        isinstance(scope, str) and scope for scope in raw_scopes
    ):
        raise GoogleCredentialError("credential granted_scopes must be a list of non-empty strings")
    if raw_scopes != sorted(set(raw_scopes)):
        raise GoogleCredentialError("credential granted_scopes must be sorted and unique")
    scopes = frozenset(raw_scopes)
    for service in services:
        if not SERVICE_SCOPES[service] <= scopes:
            raise GoogleCredentialError(f"credential grants {service!r} without its required scopes")

    expected_client_ref = f"oauth-client:{payload['client_id']}:client-secret"
    expected_refresh_ref = f"credential-file:{resolved.stem}:refresh-token"
    if payload["client_secret_ref"] != expected_client_ref:
        raise GoogleCredentialError("credential client_secret_ref does not match client_id")
    if payload["refresh_token_ref"] != expected_refresh_ref:
        raise GoogleCredentialError("credential refresh_token_ref does not match its filename")

    return GoogleCredentialFile(
        path=resolved,
        created_at=payload["created_at"],
        subject=payload["subject"],
        account=payload["account"],
        client_id=payload["client_id"],
        token_uri=payload["token_uri"],
        granted_services=services,
        granted_scopes=scopes,
        client_secret_ref=payload["client_secret_ref"],
        refresh_token_ref=payload["refresh_token_ref"],
    )


def refresh_access_token_from_file(
    path: Path,
    *,
    required_scopes: Collection[str],
    urlopen: Callable | None = None,
    secret_backend=None,
) -> str:
    """Refresh an access token through one descriptor's recorded secret refs."""
    import urllib.request

    from officina.common import secret_store as secret_store_module

    ref = load_credential_file(path)
    missing = set(required_scopes) - ref.granted_scopes
    if missing:
        raise GoogleCredentialError(f"credential file {ref.path} lacks required scopes: {missing}")
    client_secret = secret_store_module.require(
        "connect-google", ref.client_secret_ref, backend=secret_backend
    )
    refresh_token = secret_store_module.require(
        "connect-google", ref.refresh_token_ref, backend=secret_backend
    )
    return _exchange_refresh_token(
        ref=ref,
        client_secret=client_secret,
        refresh_token=refresh_token,
        urlopen=urlopen or urllib.request.urlopen,
    )


def install_client(payload: dict, *, home: Path, platform: str, replace: bool, secret_backend=None) -> dict:
    """Store ``payload``'s ``installed.client_secret`` in the secret store and
    write the remaining client JSON (with ``client_secret`` replaced by
    ``client_secret_ref``) to the canonical path.

    ``payload`` must already be a validated Google Desktop OAuth client JSON
    (a dict with a top-level ``installed`` object). It is never mutated in
    place; a deep copy is written to disk.
    """
    from officina.common import secret_store as secret_store_module
    from officina.common.oauth_json import OAuthJsonError, write_oauth_json

    backend = secret_backend or secret_store_module
    payload = json.loads(json.dumps(payload))
    installed = payload.get("installed")
    if not isinstance(installed, dict):
        raise GoogleCredentialError("client JSON must contain an installed object")

    client_secret = installed.pop("client_secret", None)
    if not client_secret:
        raise GoogleCredentialError("source client JSON has no client_secret")
    client_id = installed.get("client_id")
    if not client_id:
        raise GoogleCredentialError("source client JSON has no client_id")

    secret_ref = f"oauth-client:{client_id}:client-secret"
    installed["client_secret_ref"] = secret_ref

    dest = canonical_client_path(home=home, platform=platform)
    existed = dest.exists() or dest.is_symlink()
    if existed:
        current = None
        if dest.exists():
            try:
                current = json.loads(dest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                current = None
        if current == payload:
            # The redacted on-disk JSON matching `payload` is not proof
            # nothing changed: `client_secret_ref` is derived solely from
            # `client_id`, so reinstalling the same client_id with a
            # rotated client_secret produces byte-identical redacted JSON.
            # Consult the secret store itself (not just the disk file) to
            # decide whether the *live* secret actually changed.
            stored_secret = backend.lookup("connect-google", secret_ref)
            if stored_secret != client_secret:
                # Either a genuine rotation (stored_secret is a different,
                # non-None value) or the secret store has no record of it
                # despite the file existing (stored_secret is None -- e.g. a
                # fresh backend/state reset). Either way, store now so the
                # invariant "an installed client file implies its secret is
                # in the secret store" always holds.
                backend.store("connect-google", secret_ref, client_secret)
            return {"status": "unchanged", "path": str(dest), "payload": payload}
        if not replace:
            raise GoogleCredentialError(
                f"a different or invalid canonical client already exists at {dest}; pass replace=True to overwrite"
            )

    # Only store the secret once every rejection path above has had its chance to
    # raise; storing earlier would leave an orphaned secret-store entry behind on
    # every rejected reinstall attempt (e.g. re-running install-client with a
    # stale file and no replace=True), and nothing ever cleans that up.
    backend.store("connect-google", secret_ref, client_secret)
    try:
        write_oauth_json(dest, payload)
    except OAuthJsonError as exc:
        raise GoogleCredentialError(str(exc)) from exc
    status = "replaced" if existed else "installed"
    return {"status": status, "path": str(dest), "payload": payload}


def client_status(*, home: Path, platform: str) -> dict:
    path = canonical_client_path(home=home, platform=platform)
    if not path.exists():
        return {"installed": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"installed": False, "path": str(path)}
    installed = payload.get("installed") if isinstance(payload, dict) else None
    client_id = installed.get("client_id") if isinstance(installed, dict) else None
    return {"installed": True, "path": str(path), "client_id": client_id}


_LOCK_TIMEOUT_S = 30.0
_LOCK_POLL_INTERVAL_S = 0.05


def _lock_timeout_s() -> float:
    """Bounded-wait deadline for _registry_file_lock() acquisition, mirroring
    list-manager's _yaml_store.py::_lock_timeout_s(). A HUNG-but-alive writer
    must not make every later call stall forever; 30s is meant to catch a
    genuinely stuck process, not add friction to normal fast operations.

    OFFICINA_GOOGLE_CREDENTIALS_TEST_LOCK_TIMEOUT_S overrides it for tests.
    """
    override = os.environ.get("OFFICINA_GOOGLE_CREDENTIALS_TEST_LOCK_TIMEOUT_S")
    return float(override) if override else _LOCK_TIMEOUT_S


@contextlib.contextmanager
def _registry_file_lock(path: Path):
    """Cross-platform exclusive advisory lock over `path`'s read-modify-write
    sequence, mirroring skills/list-manager/_rtx/_yaml_store.py's file_lock()
    (see that module for the full cross-platform design rationale).

    Two concurrent store_google_credential() calls (e.g. onboarding two
    Google accounts back-to-back via connect-google's authorize-services)
    must not both read the registry before either writes: the second's
    os.replace() would silently overwrite the registry with a copy missing
    the first's newly-stored credential entry -- even though that
    credential's refresh token was already durably written to the secret
    store, leaving an orphaned secret and an unresolvable credential_id for
    the lost account. This lock, held for the full load -> mutate -> save
    sequence, closes that race by genuinely serializing racing callers
    rather than merely narrowing the window.

    A `<path>.lock` sidecar (rather than locking `path` itself) is used
    because `path` is replaced, not written in place, by
    store_google_credential -- locking a path across a replace is
    unreliable. Advisory only: it serializes cooperating callers that go
    through this same function; it does not prevent a process that ignores
    locking entirely from writing the file.
    """
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        deadline = time.monotonic() + _lock_timeout_s()
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise GoogleCredentialError(
                            f"could not acquire lock on {lock_path} after {_lock_timeout_s():.0f}s -- "
                            "another process may be stuck; if none is actually running, delete the "
                            "stale lock file and retry."
                        )
                    time.sleep(_LOCK_POLL_INTERVAL_S)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise GoogleCredentialError(
                            f"could not acquire lock on {lock_path} after {_lock_timeout_s():.0f}s -- "
                            "another process may be stuck; if none is actually running, delete the "
                            "stale lock file and retry."
                        )
                    time.sleep(_LOCK_POLL_INTERVAL_S)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _test_race_delay(subject: str) -> None:
    """Test-only hook (no-op by default): tests monkeypatch this to
    deterministically hold one writer inside the locked critical section
    (after its read, before its write) while a second writer attempts to
    enter, proving the lock -- not timing luck -- is what serializes them.
    See test_store_google_credential_concurrent_writes_dont_lose_entries."""
    return None


def store_google_credential(
    *,
    subject: str,
    account: str,
    client_id: str,
    token_uri: str,
    granted_scopes: frozenset[str],
    refresh_token: str,
    home: Path,
    platform: str,
    secret_backend=None,
) -> GoogleCredentialRef:
    from officina.common import secret_store as secret_store_module

    backend = secret_backend or secret_store_module
    credential_id = f"google:{subject}"
    backend.store("connect-google", f"{credential_id}:refresh-token", refresh_token)

    registry_path = _credentials_registry_path(home=home, platform=platform)
    with _registry_file_lock(registry_path):
        registry = (
            json.loads(registry_path.read_text(encoding="utf-8"))
            if registry_path.exists()
            else {"schema_version": 1, "credentials": {}}
        )
        _test_race_delay(subject)
        registry["credentials"][credential_id] = {
            "subject": subject,
            "account": account,
            "client_id": client_id,
            "token_uri": token_uri,
            "granted_scopes": sorted(granted_scopes),
        }
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = registry_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
        os.replace(tmp_path, registry_path)
    return GoogleCredentialRef(
        credential_id=credential_id,
        subject=subject,
        account=account,
        client_id=client_id,
        token_uri=token_uri,
        granted_scopes=frozenset(granted_scopes),
    )


def load_credential(credential_id: str, *, home: Path, platform: str) -> GoogleCredentialRef:
    registry_path = _credentials_registry_path(home=home, platform=platform)
    if not registry_path.exists():
        raise GoogleCredentialError(f"no credential registry at {registry_path}")
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GoogleCredentialError(
            f"invalid credential registry at {registry_path}: {exc}"
        ) from exc
    if not isinstance(registry, dict) or not isinstance(
        registry.get("credentials"), dict
    ):
        raise GoogleCredentialError(
            f"invalid credential registry at {registry_path}: expected credentials object"
        )
    record = registry["credentials"].get(credential_id)
    if record is None:
        raise GoogleCredentialError(f"unknown credential_id: {credential_id}")
    if not isinstance(record, dict):
        raise GoogleCredentialError(
            f"invalid credential registry at {registry_path}: {credential_id} must be an object"
        )
    string_fields = ("subject", "account", "client_id", "token_uri")
    if any(
        not isinstance(record.get(field), str) or not record[field]
        for field in string_fields
    ):
        raise GoogleCredentialError(
            f"invalid credential registry at {registry_path}: {credential_id} has invalid fields"
        )
    granted_scopes = record.get("granted_scopes")
    if not isinstance(granted_scopes, list) or not all(
        isinstance(scope, str) and scope for scope in granted_scopes
    ):
        raise GoogleCredentialError(
            f"invalid credential registry at {registry_path}: {credential_id} has invalid granted_scopes"
        )
    return GoogleCredentialRef(
        credential_id=credential_id,
        subject=record["subject"],
        account=record["account"],
        client_id=record["client_id"],
        token_uri=record["token_uri"],
        granted_scopes=frozenset(granted_scopes),
    )


def refresh_access_token(
    credential_id: str,
    *,
    required_scopes: Collection[str],
    home: Path,
    platform: str,
    urlopen: Callable | None = None,
    secret_backend=None,
) -> str:
    """Exchange a stored refresh token for a fresh access token.

    Required scopes are checked against the credential's granted scopes
    *before* any network call is made, so a caller requesting a scope the
    user never granted fails locally without touching the network.
    """
    import urllib.request

    urlopen = urlopen or urllib.request.urlopen
    ref = load_credential(credential_id, home=home, platform=platform)
    missing = set(required_scopes) - ref.granted_scopes
    if missing:
        raise GoogleCredentialError(f"credential {credential_id} lacks required scopes: {missing}")

    from officina.common import secret_store as secret_store_module

    client_secret = secret_store_module.require(
        "connect-google", f"oauth-client:{ref.client_id}:client-secret", backend=secret_backend
    )
    refresh_token = secret_store_module.require(
        "connect-google", f"{credential_id}:refresh-token", backend=secret_backend
    )
    return _exchange_refresh_token(ref=ref, client_secret=client_secret, refresh_token=refresh_token, urlopen=urlopen)


def exchange_authorization_code(
    *,
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    token_uri: str,
    urlopen: Callable,
    secret_backend=None,
) -> dict:
    """Exchange a PKCE authorization code for tokens using the stored client secret.

    The client secret never leaves this module: callers (connect-google's
    authorize-services source) pass only ``client_id``/``token_uri`` read from
    the public canonical client file, and this function looks the secret up
    from the host secret store itself, keyed by ``client_id``.
    """
    import urllib.parse
    import urllib.request

    from officina.common import secret_store as secret_store_module

    client_secret = secret_store_module.require(
        "connect-google", f"oauth-client:{client_id}:client-secret", backend=secret_backend
    )
    data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
    ).encode()
    request = urllib.request.Request(token_uri, data=data, method="POST")
    try:
        with urlopen(request) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GoogleCredentialError(f"OAuth token endpoint returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GoogleCredentialError(f"OAuth token endpoint failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise GoogleCredentialError("token endpoint returned a non-object response")
    return payload


def _exchange_refresh_token(
    *,
    ref: GoogleCredentialRef | GoogleCredentialFile,
    client_secret: str,
    refresh_token: str,
    urlopen: Callable,
) -> str:
    import urllib.parse
    import urllib.request

    data = urllib.parse.urlencode(
        {
            "client_id": ref.client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode()
    request = urllib.request.Request(ref.token_uri, data=data, method="POST")
    try:
        with urlopen(request) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GoogleCredentialError(f"OAuth token endpoint returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GoogleCredentialError(f"OAuth token endpoint failed: {exc}") from exc
    return payload["access_token"]


__all__ = [
    "GoogleCredentialError",
    "GoogleCredentialFile",
    "GoogleCredentialRef",
    "IDENTITY_SCOPES",
    "SERVICE_SCOPES",
    "canonical_client_path",
    "client_status",
    "create_credential_file",
    "exchange_authorization_code",
    "install_client",
    "load_credential",
    "load_credential_file",
    "normalize_services",
    "refresh_access_token",
    "refresh_access_token_from_file",
    "scope_union_for_services",
]
