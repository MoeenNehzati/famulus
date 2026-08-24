"""Canonical Google client discovery, secret-store client-secret storage,
and per-account credential registry shared across connect-google's service
consumers (cloud-files, online-calendar, email-client).

The installed Google Desktop OAuth client JSON keeps the shape Google's
Cloud Console actually exports (a top-level ``installed`` object). This
module never writes the client's ``client_secret`` to disk: it stores the
secret through ``officina.credentials.secret_store`` and replaces it on disk with
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
import urllib.request
import uuid
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SERVICE_SCOPES: dict[str, frozenset[str]] = {
    "drive": frozenset({"https://www.googleapis.com/auth/drive"}),
    "calendar": frozenset({"https://www.googleapis.com/auth/calendar"}),
    "gmail": frozenset({"https://mail.google.com/"}),
}
IDENTITY_SCOPES = frozenset({"openid", "email"})
GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_HTTP_TIMEOUT_S = 30.0
GOOGLE_HTTP_MAX_BODY_BYTES = 64 * 1024


class GoogleCredentialError(RuntimeError):
    """Raised for invalid service/scope requests and credential failures."""


class GoogleCredentialPublicationUncertain(GoogleCredentialError):
    """Raised when registry state cannot be classified after a write error."""


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
    client_secret_ref: str
    token_uri: str
    granted_scopes: frozenset[str]
    refresh_secret_ref: str
    publication_warnings: tuple[str, ...] = ()


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

    return (
        resolve_famulus_paths(
            platform=platform, home=Path(home), environ=os.environ
        ).config_root
        / "connect-google"
        / "client.json"
    )


def _credentials_registry_path(*, home: Path, platform: str) -> Path:
    from officina.common.famulus_paths import resolve_famulus_paths

    return (
        resolve_famulus_paths(
            platform=platform, home=Path(home), environ=os.environ
        ).config_root
        / "connect-google"
        / "credentials.json"
    )


def _credential_files_dir(*, home: Path, platform: str) -> Path:
    """Return the directory containing immutable per-authorization descriptors."""
    from officina.common.famulus_paths import resolve_famulus_paths

    return (
        resolve_famulus_paths(
            platform=platform, home=Path(home), environ=os.environ
        ).config_root
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
    client_secret_ref: str | None = None,
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
    from . import secret_store as secret_store_module

    for label, value in (
        ("subject", subject),
        ("account", account),
        ("client_id", client_id),
        ("token_uri", token_uri),
        ("refresh_token", refresh_token),
    ):
        if not isinstance(value, str) or not value:
            raise GoogleCredentialError(f"{label} must be a non-empty string")

    resolved_client_secret_ref = (
        client_secret_ref
        if client_secret_ref is not None
        else f"oauth-client:{client_id}:client-secret"
    )
    if not isinstance(resolved_client_secret_ref, str) or not resolved_client_secret_ref:
        raise GoogleCredentialError("client_secret_ref must be a non-empty string")

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
        "client_secret_ref": resolved_client_secret_ref,
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
        client_secret_ref=resolved_client_secret_ref,
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

    expected_refresh_ref = f"credential-file:{resolved.stem}:refresh-token"
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

    from . import secret_store as secret_store_module

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
    from . import secret_store as secret_store_module
    from officina.credentials.oauth import OAuthJsonError, write_oauth_json

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


_GENERATED_REFRESH_REF = re.compile(r"^google-refresh:[0-9a-f]{32}$")


def _refresh_secret_ref(credential_id: str, record: Mapping[str, object]) -> str:
    value = record.get("refresh_secret_ref")
    if value is None:
        return f"{credential_id}:refresh-token"
    if not isinstance(value, str) or not value:
        raise GoogleCredentialError("credential has invalid refresh_secret_ref")
    return value


def _client_secret_ref(client_id: str, record: Mapping[str, object]) -> str:
    value = record.get("client_secret_ref")
    if value is None:
        return f"oauth-client:{client_id}:client-secret"
    if not isinstance(value, str) or not value:
        raise GoogleCredentialError("credential has invalid client_secret_ref")
    return value


def _optional_refresh_secret_ref(
    credential_id: str, record: Mapping[str, object]
) -> str | None:
    try:
        return _refresh_secret_ref(credential_id, record)
    except GoogleCredentialError:
        return None


def _load_registry(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 2, "credentials": {}}
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoogleCredentialError(f"cannot read Google credential registry at {path}") from exc
    if not isinstance(registry, dict) or not isinstance(registry.get("credentials"), dict):
        raise GoogleCredentialError("Google credential registry has invalid structure")
    return registry


def _safe_clear(backend, key: str) -> bool:
    try:
        return bool(backend.clear("connect-google", key))
    except Exception:
        return False


def store_google_credential(
    *,
    subject: str,
    account: str,
    client_id: str,
    client_secret_ref: str | None = None,
    token_uri: str,
    granted_scopes: frozenset[str],
    refresh_token: str,
    home: Path,
    platform: str,
    secret_backend=None,
    registry_writer: Callable | None = None,
    refresh_ref_factory: Callable[[], str] | None = None,
) -> GoogleCredentialRef:
    from . import secret_store as secret_store_module
    from officina.credentials.oauth import write_oauth_json

    backend = secret_backend or secret_store_module
    credential_id = f"google:{subject}"
    resolved_client_secret_ref = (
        f"oauth-client:{client_id}:client-secret"
        if client_secret_ref is None
        else client_secret_ref
    )
    if not resolved_client_secret_ref:
        raise GoogleCredentialError("client-secret reference must be non-empty")
    refresh_secret_ref = (
        refresh_ref_factory() if refresh_ref_factory is not None else f"google-refresh:{uuid.uuid4().hex}"
    )
    if _GENERATED_REFRESH_REF.fullmatch(refresh_secret_ref) is None:
        raise GoogleCredentialError("generated refresh-secret reference has invalid format")
    backend.store("connect-google", refresh_secret_ref, refresh_token)
    writer = registry_writer or write_oauth_json

    registry_path = _credentials_registry_path(home=home, platform=platform)
    with _registry_file_lock(registry_path):
        try:
            registry = _load_registry(registry_path)
        except Exception:
            _safe_clear(backend, refresh_secret_ref)
            raise
        _test_race_delay(subject)
        previous = registry["credentials"].get(credential_id)
        new_record = {
            "subject": subject,
            "account": account,
            "client_id": client_id,
            "client_secret_ref": resolved_client_secret_ref,
            "granted_scopes": sorted(granted_scopes),
            "refresh_secret_ref": refresh_secret_ref,
        }
        published = json.loads(json.dumps(registry))
        published["schema_version"] = 2
        published["credentials"][credential_id] = new_record
        warnings: list[str] = []
        try:
            writer(registry_path, published)
        except Exception as exc:
            try:
                visible = _load_registry(registry_path)["credentials"].get(credential_id)
            except Exception as read_exc:
                raise GoogleCredentialPublicationUncertain(
                    "Google credential publication state is uncertain"
                ) from read_exc
            if visible == new_record:
                warnings.append("registry_durability_warning")
            elif visible == previous:
                _safe_clear(backend, refresh_secret_ref)
                raise GoogleCredentialError("Google credential publication failed") from exc
            else:
                raise GoogleCredentialPublicationUncertain(
                    "Google credential publication state is uncertain"
                ) from exc
        else:
            if isinstance(previous, dict):
                previous_ref = _optional_refresh_secret_ref(credential_id, previous)
                candidate_is_safe = (
                    previous_ref is not None
                    and (
                        _GENERATED_REFRESH_REF.fullmatch(previous_ref) is not None
                        or previous_ref == f"{credential_id}:refresh-token"
                    )
                )
                still_referenced = any(
                    _optional_refresh_secret_ref(other_id, other_record) == previous_ref
                    for other_id, other_record in published["credentials"].items()
                    if other_id != credential_id and isinstance(other_record, dict)
                )
                if candidate_is_safe and not still_referenced and previous_ref != refresh_secret_ref:
                    try:
                        backend.clear("connect-google", previous_ref)
                    except Exception:
                        warnings.append("secret_cleanup_warning")
    return GoogleCredentialRef(
        credential_id=credential_id,
        subject=subject,
        account=account,
        client_id=client_id,
        client_secret_ref=resolved_client_secret_ref,
        token_uri=GOOGLE_TOKEN_URL,
        granted_scopes=frozenset(granted_scopes),
        refresh_secret_ref=refresh_secret_ref,
        publication_warnings=tuple(warnings),
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
    string_fields = ("subject", "account", "client_id")
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
        client_secret_ref=_client_secret_ref(record["client_id"], record),
        token_uri=GOOGLE_TOKEN_URL,
        granted_scopes=frozenset(granted_scopes),
        refresh_secret_ref=_refresh_secret_ref(credential_id, record),
    )


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Reject every redirect so secret-bearing requests stay on pinned origins."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GoogleCredentialError("Google endpoint redirect refused")


def _default_urlopen(request, *, timeout: float):
    return urllib.request.build_opener(_RejectRedirects()).open(request, timeout=timeout)


def _read_bounded_json(response) -> dict:
    data = response.read(GOOGLE_HTTP_MAX_BODY_BYTES + 1)
    if len(data) > GOOGLE_HTTP_MAX_BODY_BYTES:
        raise GoogleCredentialError("Google endpoint response exceeded 65536 bytes")
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoogleCredentialError("Google endpoint returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise GoogleCredentialError("Google endpoint returned a non-object response")
    return payload


def _open_google_json(
    request,
    *,
    urlopen: Callable | None = None,
    timeout_s: float = GOOGLE_HTTP_TIMEOUT_S,
) -> dict:
    """Open one pinned Google request with redirect, size, and JSON guards."""
    opener = urlopen or _default_urlopen
    try:
        with opener(request, timeout=timeout_s) as response:
            return _read_bounded_json(response)
    except GoogleCredentialError:
        raise
    except urllib.error.HTTPError as exc:
        try:
            exc.read(GOOGLE_HTTP_MAX_BODY_BYTES + 1)
        except Exception:
            pass
        raise GoogleCredentialError(f"Google endpoint returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GoogleCredentialError("Google endpoint request failed") from exc


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
    ref = load_credential(credential_id, home=home, platform=platform)
    missing = set(required_scopes) - ref.granted_scopes
    if missing:
        raise GoogleCredentialError(f"credential {credential_id} lacks required scopes: {missing}")

    from . import secret_store as secret_store_module

    client_secret = secret_store_module.require(
        "connect-google", ref.client_secret_ref, backend=secret_backend
    )
    refresh_token = secret_store_module.require(
        "connect-google", ref.refresh_secret_ref, backend=secret_backend
    )
    return _exchange_refresh_token(ref=ref, client_secret=client_secret, refresh_token=refresh_token, urlopen=urlopen)


def exchange_authorization_code(
    *,
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    token_uri: str,
    urlopen: Callable | None = None,
    client_secret_ref: str | None = None,
    secret_backend=None,
) -> dict:
    """Exchange a PKCE authorization code for tokens using the stored client secret.

    The client secret never leaves this module: callers (connect-google's
    authorize-services source) pass only ``client_id``/``token_uri`` read from
    the redacted canonical client file, and this function resolves the exact
    supplied secret reference. ``token_uri`` remains accepted for legacy
    callers but is never trusted as the exchange endpoint.
    """
    import urllib.parse
    import urllib.request

    from . import secret_store as secret_store_module

    secret_ref = client_secret_ref or f"oauth-client:{client_id}:client-secret"
    client_secret = secret_store_module.require(
        "connect-google", secret_ref, backend=secret_backend
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
    request = urllib.request.Request(GOOGLE_TOKEN_URL, data=data, method="POST")
    return _open_google_json(request, urlopen=urlopen)


def _exchange_refresh_token(
    *,
    ref: GoogleCredentialRef | GoogleCredentialFile,
    client_secret: str,
    refresh_token: str,
    urlopen: Callable | None,
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
    request = urllib.request.Request(GOOGLE_TOKEN_URL, data=data, method="POST")
    payload = _open_google_json(request, urlopen=urlopen)
    return payload["access_token"]


__all__ = [
    "GoogleCredentialError",
    "GoogleCredentialFile",
    "GoogleCredentialPublicationUncertain",
    "GoogleCredentialRef",
    "GOOGLE_AUTHORIZATION_URL",
    "GOOGLE_TOKEN_URL",
    "GOOGLE_USERINFO_URL",
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
