#!/usr/bin/env python3
"""Provide bounded Google Drive transport and local/remote CLI operations."""

from __future__ import annotations

import glob
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence

from officina.common.configured_schema import ConfiguredSchemaError, load_configuration

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
CONFIG_DIR_NAME = ".config/cloud-files"
CONFIG_FILE_NAME = "config.json"
CREDENTIALS_FILE_NAME = "credentials.json"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API_ROOT = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_ROOT = "https://www.googleapis.com/upload/drive/v3/files"
LLM_PREFIX = "llm:"
GLOB_CHARS = "*?[]"


@dataclass(frozen=True)
class CloudFilesConfig:
    """Immutable paths, timeout, and credential selection for Drive access."""

    remote_llm_root: str
    timeout_seconds: int
    credentials_path: Path
    credential_id: str | None = None
    home: Path | None = None


@dataclass(frozen=True)
class RemoteEntry:
    """Immutable Drive entry identity, relative path, and directory marker."""

    path: str
    id: str
    is_dir: bool


class CloudFilesError(RuntimeError):
    """Report cloud-files configuration, transport, and path contract failures."""

    pass


def normalize_llm_root(root: str) -> str:
    """Normalize a configured Drive root to a safe relative directory prefix.

    Intent
    ------
    Remove redundant separators and dot segments while rejecting absolute paths,
    backslashes, and parent traversal.

    Rationale
    ---------
    Every LLM-scoped operation must remain beneath the configured Drive root, and a
    canonical trailing slash prevents callers from interpreting the root as a file.

    Pseudocode
    ----------
    - set raw_root = trimmed root
    - if raw_root is empty:
      - return empty path
    - set segments = safe nonempty slash segments or raise ValueError
    - return segments joined with a trailing slash

    Wraps
    -----
    - none
    """
    raw = root.strip()
    if not raw:
        return ""
    if raw.startswith("/") or "\\" in raw:
        raise ValueError(f"invalid remote_llm_root: {root}")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError(f"invalid remote_llm_root: {root}")
        parts.append(part)
    if not parts:
        return ""
    return "/".join(parts) + "/"


def validate_relpath(path: str, *, allow_empty: bool = False) -> str:
    """Validate and canonicalize a non-glob Drive-relative path.

    Intent
    ------
    Collapse harmless separators and dot segments without permitting an absolute,
    backslash-delimited, parent-traversing, or disallowed empty path.

    Rationale
    ---------
    A single strict path boundary keeps Drive lookups inside their selected base and
    gives downstream resolution code one stable slash-separated representation.

    Pseudocode
    ----------
    - if path is empty and empty paths are disallowed:
      - raise ValueError(path required)
    - set segments = safe nonempty slash segments or raise ValueError
    - return joined segments

    Wraps
    -----
    - none
    """
    if not path:
        if allow_empty:
            return ""
        raise ValueError("path required")
    if path.startswith("/") or "\\" in path:
        raise ValueError(f"invalid path: {path}")
    parts: list[str] = []
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError(f"invalid path: {path}")
        parts.append(part)
    if not parts and not allow_empty:
        raise ValueError("path required")
    return "/".join(parts)


def normalize_relpath_pattern(path: str, *, allow_empty: bool = False) -> str:
    """Normalize a Drive-relative glob pattern without erasing pattern syntax.

    Intent
    ------
    Trim and join pattern segments while rejecting absolute paths, backslashes, dot
    segments, parent traversal, and an empty pattern when it is not allowed.

    Rationale
    ---------
    Glob characters must survive normalization, but navigation segments must not let
    recursive matching escape the selected Drive namespace.

    Pseudocode
    ----------
    - set raw_pattern = trimmed pattern
    - if raw_pattern is empty and empty patterns are disallowed:
      - raise ValueError(path required)
    - set segments = nonempty slash segments without dot navigation
    - return joined segments with glob syntax preserved

    Wraps
    -----
    - none
    """
    raw = path.strip()
    if not raw:
        if allow_empty:
            return ""
        raise ValueError("path required")
    if raw.startswith("/") or "\\" in raw:
        raise ValueError(f"invalid path: {path}")
    parts: list[str] = []
    for part in raw.split("/"):
        if part == "":
            continue
        if part in {".", ".."}:
            raise ValueError(f"invalid path: {path}")
        parts.append(part)
    if not parts:
        if allow_empty:
            return ""
        raise ValueError("path required")
    return "/".join(parts)


def has_glob_magic(path: str) -> bool:
    """Return whether a path contains any supported glob metacharacter.

    Intent
    ------
    Distinguish literal paths from patterns before choosing local or remote expansion.

    Rationale
    ---------
    Centralizing the character test keeps copy, list, and remove commands consistent
    about when recursive enumeration is required.

    Pseudocode
    ----------
    - return whether any supported glob character occurs in path

    Wraps
    -----
    - none
    """
    return any(char in path for char in GLOB_CHARS)


def parse_llm_spec(
    spec: str,
    *,
    allow_empty: bool = False,
    allow_glob: bool = False,
) -> tuple[str, bool]:
    """Parse an ``llm:`` operand into a normalized path and directory hint.

    Intent
    ------
    Require the remote prefix, preserve a trailing-slash hint, and select literal or
    glob-aware normalization according to the caller's mode.

    Rationale
    ---------
    Copy, list, and remove share one remote operand grammar, including the distinction
    between a destination file and a directory-shaped destination.

    Pseudocode
    ----------
    - if spec lacks the remote prefix:
      - raise ValueError(remote prefix required)
    - set raw_path = spec without the remote prefix
    - if glob syntax is allowed:
      - normalized_path = .normalize_relpath_pattern(raw_path)
    - else:
      - normalized_path = .validate_relpath(raw_path)
    - return normalized_path and trailing-slash hint

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .normalize_relpath_pattern:
      why:
        transforms: "Produces the normalized path product when the operand may contain glob syntax."
    .validate_relpath:
      why:
        transforms: "Produces the normalized path product for literal remote operands."
    """
    if not spec.startswith(LLM_PREFIX):
        raise ValueError(f"remote path must start with {LLM_PREFIX}")
    raw = spec[len(LLM_PREFIX):]
    dir_hint = bool(raw) and raw.endswith("/")
    normalized = (
        normalize_relpath_pattern(raw, allow_empty=allow_empty)
        if allow_glob
        else validate_relpath(raw, allow_empty=allow_empty)
    )
    return normalized, dir_hint


def default_config_path(home: Path | None = None) -> Path:
    """Build the cloud-files configuration path beneath a selected home directory.

    Intent
    ------
    Use the supplied home when present and otherwise resolve the current user's home.

    Rationale
    ---------
    Tests and setup flows need deterministic home substitution without duplicating the
    service-specific configuration location.

    Pseudocode
    ----------
    - return supplied or current home joined with the service config location

    Wraps
    -----
    - none
    """
    base = home or Path.home()
    return base / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def default_credentials_path(home: Path | None = None) -> Path:
    """Build the legacy OAuth credential path beneath a selected home directory.

    Intent
    ------
    Resolve the service-owned credentials filename from an explicit or current home.

    Rationale
    ---------
    Keeping this fallback path in one helper lets configuration loading remain
    overrideable while preserving the established per-service OAuth layout.

    Pseudocode
    ----------
    - return supplied or current home joined with the legacy credentials location

    Wraps
    -----
    - none
    """
    base = home or Path.home()
    return base / CONFIG_DIR_NAME / CREDENTIALS_FILE_NAME


def load_config(home: Path | None = None) -> CloudFilesConfig:
    """Load, validate, and materialize cloud-files runtime configuration.

    Intent
    ------
    Resolve defaults and overrides for the Drive root, timeout, credential file,
    shared credential identifier, and effective home.

    Rationale
    ---------
    Converting schema, file, and root-validation failures to ``CloudFilesError`` gives
    every CLI entrypoint one stable service-level failure contract.

    Pseudocode
    ----------
    - config_path = .default_config_path(home)
    - payload = officina.common.configured_schema.load_configuration(config_path)
    - normalized_root = .normalize_llm_root(payload remote root)
    - if no credentials-path override is configured:
      - credentials_path = .default_credentials_path(home)
    - if loading or root validation fails:
      - raise .CloudFilesError(configuration failure)
    - return .CloudFilesConfig(normalized_root, timeout, credentials_path, credential id, home)

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .default_config_path:
      why:
        constructs: "Builds the configuration path consumed by the schema-backed loader."
    officina.common.configured_schema.load_configuration:
      why:
        constructs: "Builds the validated configuration mapping used to materialize runtime settings."
    .normalize_llm_root:
      why:
        transforms: "Produces the canonical Drive-root prefix stored in the runtime configuration."
    .default_credentials_path:
      why:
        constructs: "Builds the legacy credentials fallback when configuration does not override it."
    .CloudFilesConfig:
      why:
        constructs: "Builds the immutable configuration product returned to transport callers."
    .CloudFilesError:
      why:
        raises: "Translates missing, schema-invalid, or unsafe configuration into the service error contract."
    """
    config_path = default_config_path(home)
    try:
        payload = load_configuration(config_path)
    except FileNotFoundError as exc:
        raise CloudFilesError(f"missing config file: {config_path}") from exc
    except ConfiguredSchemaError as exc:
        raise CloudFilesError(f"invalid configuration in {config_path}: {exc}") from exc

    raw_llm_root = str(payload.get("remote_llm_root", "assistant/"))
    try:
        remote_llm_root = normalize_llm_root(raw_llm_root)
    except ValueError as exc:
        raise CloudFilesError(str(exc)) from exc

    timeout_seconds = int(payload.get("timeout_seconds", 45))
    credentials_value = str(payload.get("credentials_path", "")).strip()
    credentials_path = (
        Path(credentials_value).expanduser()
        if credentials_value
        else default_credentials_path(home)
    )
    credential_id_value = payload.get("credential_id")
    credential_id = str(credential_id_value).strip() if credential_id_value else None

    return CloudFilesConfig(
        remote_llm_root=remote_llm_root,
        timeout_seconds=timeout_seconds,
        credentials_path=credentials_path,
        credential_id=credential_id or None,
        home=home or Path.home(),
    )


def load_credentials(config: CloudFilesConfig) -> dict[str, str]:
    """Read and validate the legacy OAuth client and refresh-token fields.

    Intent
    ------
    Translate a missing file or JSON syntax error, require three nonblank fields from a
    decoded mapping, and return their stripped string values.

    Rationale
    ---------
    Token refresh must not send blank secrets. Valid non-mapping JSON still raises
    ``AttributeError`` at ``payload.get``, and read/decode failures other than the two
    caught cases retain their native exceptions.

    Pseudocode
    ----------
    - set payload = JSON decoded from the credentials file
    - if the file is missing or JSON syntax is invalid:
      - raise .CloudFilesError(credentials file failure)
    - set missing_fields = blank required keys from payload
    - if missing_fields is nonempty:
      - raise .CloudFilesError(missing_fields)
    - return stripped required credential fields

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CloudFilesError:
      why:
        raises: "Carries bounded credentials-file and required-field failures to CLI error handling."
    """
    try:
        payload = json.loads(config.credentials_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CloudFilesError(
            f"missing credentials file: {config.credentials_path}; "
            "expected JSON with client_id, client_secret, and refresh_token"
        ) from exc
    except json.JSONDecodeError as exc:
        raise CloudFilesError(
            f"invalid JSON in credentials file: {config.credentials_path}"
        ) from exc

    required = ["client_id", "client_secret", "refresh_token"]
    missing = [key for key in required if not str(payload.get(key, "")).strip()]
    if missing:
        raise CloudFilesError(
            f"credentials file is missing required field(s): {', '.join(missing)}"
        )
    return {key: str(payload[key]).strip() for key in required}


def get_access_token(config: CloudFilesConfig, *, platform: str = sys.platform) -> str:
    """Obtain a Drive access token from shared or legacy OAuth credentials.

    Intent
    ------
    Prefer the configured shared credential registry; otherwise exchange the legacy
    refresh token at Google's token endpoint and require a nonempty access token.

    Rationale
    ---------
    One token boundary preserves the legacy fallback while enforcing Drive scope for
    shared credentials and translating network failures into service-level errors.

    Pseudocode
    ----------
    - if shared credential id is configured:
      - return officina.common.google_credentials.refresh_access_token(credential id)
    - credentials = .load_credentials(config)
    - set token_response = refresh-token HTTP exchange using credentials
    - if the exchange fails or access token is empty:
      - raise .CloudFilesError(token failure)
    - return access token from token_response

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.common.google_credentials.refresh_access_token:
      why:
        constructs: "Returns a Drive-scoped access token from the selected shared credential."
    .load_credentials:
      why:
        constructs: "Builds the validated legacy credential mapping used in the token exchange."
    .CloudFilesError:
      why:
        raises: "Carries token endpoint, network, and missing-token failures to the command boundary."
    """
    if config.credential_id:
        from officina.common.google_credentials import SERVICE_SCOPES, refresh_access_token

        return refresh_access_token(
            config.credential_id,
            required_scopes=SERVICE_SCOPES["drive"],
            home=config.home or Path.home(),
            platform=platform,
        )

    creds = load_credentials(config)
    data = urllib.parse.urlencode(
        {
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CloudFilesError(f"token refresh failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise CloudFilesError(f"token refresh failed: {exc.reason}") from exc

    token = str(payload.get("access_token", "")).strip()
    if not token:
        raise CloudFilesError("token refresh succeeded but no access_token was returned")
    return token


def drive_request(
    config: CloudFilesConfig,
    method: str,
    url: str,
    *,
    query: dict[str, Any] | None = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    expect_json: bool = True,
) -> Any:
    """Execute one authenticated Google Drive request and decode its response.

    Intent
    ------
    Add authentication and optional query/header data, perform the request with the
    configured timeout, and return bytes, an empty mapping, or decoded JSON.

    Rationale
    ---------
    Centralizing HTTP construction gives Drive operations consistent authentication and
    failure translation. Optional query values pass through from callers, which choose
    whether a request includes shared-drive flags.

    Pseudocode
    ----------
    - token = .get_access_token(config)
    - set request_url = URL plus encoded query
    - set request_headers = bearer token plus supplied headers
    - set response_body = HTTP response bytes using request_url and request_headers
    - if the request fails:
      - raise .CloudFilesError(Drive transport failure)
    - if raw bytes are requested:
      - return response_body
    - return decoded JSON or empty mapping from response_body

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .get_access_token:
      why:
        constructs: "Provides the bearer token carried into the outgoing Drive request headers."
    .CloudFilesError:
      why:
        raises: "Carries Drive HTTP and transport failures without exposing raw URL exceptions."
    """
    token = get_access_token(config)
    if query:
        encoded = urllib.parse.urlencode(query, doseq=True)
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{encoded}"

    request_headers = {"Authorization": f"Bearer {token}"}
    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CloudFilesError(f"Drive API error: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise CloudFilesError(f"Drive API request failed: {exc.reason}") from exc

    if not expect_json:
        return body
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def escape_query_value(value: str) -> str:
    """Escape backslashes and apostrophes for a Drive query string literal.

    Intent
    ------
    Protect caller-supplied identifiers before interpolating them into Drive ``q`` clauses.

    Rationale
    ---------
    Drive list filters use quoted literals rather than parameter binding, so their two
    significant escape characters must be handled consistently at interpolation sites.

    Pseudocode
    ----------
    - set escaped = value with escaped backslashes
    - return escaped with escaped apostrophes

    Wraps
    -----
    - none
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def list_children(
    config: CloudFilesConfig,
    parent_id: str,
    *,
    name: str | None = None,
    mime_type: str | None = None,
) -> list[dict[str, Any]]:
    """Return up to the first 200 matching untrashed Drive children.

    Intent
    ------
    Build one parent-scoped Drive query, request identity metadata with ``pageSize=200``,
    and return that response's file records as a list.

    Rationale
    ---------
    Folder traversal and file resolution share safely escaped filters and shared-drive
    options. This helper neither requests ``nextPageToken`` nor follows later pages.

    Pseudocode
    ----------
    - set conditions = untrashed children of escaped parent id
    - if name is supplied:
      - set conditions = conditions plus escaped name filter
    - if MIME type is supplied:
      - set conditions = conditions plus escaped MIME filter
    - response = .drive_request(config, GET, Drive files endpoint, conditions, page size 200)
    - return first-page file records from response

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .escape_query_value:
      why:
        transforms: "Produces safe parent, name, and MIME literals for the Drive query expression."
    .drive_request:
      why:
        constructs: "Builds the decoded Drive listing response from which child records are returned."
    """
    conditions = [
        "trashed = false",
        f"'{escape_query_value(parent_id)}' in parents",
    ]
    if name is not None:
        conditions.append(f"name = '{escape_query_value(name)}'")
    if mime_type is not None:
        conditions.append(f"mimeType = '{escape_query_value(mime_type)}'")
    payload = drive_request(
        config,
        "GET",
        DRIVE_API_ROOT,
        query={
            "q": " and ".join(conditions),
            "fields": "files(id,name,mimeType)",
            "pageSize": 200,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        },
    )
    return list(payload.get("files", []))


def create_folder(config: CloudFilesConfig, parent_id: str, name: str) -> str:
    """Create one Drive folder beneath a parent and return its identifier.

    Intent
    ------
    Submit folder metadata to Drive and reject a successful response that lacks an id.

    Rationale
    ---------
    Recursive path creation can advance safely only when every newly created segment
    yields the stable identifier required for the next lookup.

    Pseudocode
    ----------
    - set metadata = folder name, MIME type, and parent id
    - response = .drive_request(config, POST, Drive files endpoint, metadata)
    - set folder_id = trimmed id from response
    - if folder_id is empty:
      - raise .CloudFilesError(folder creation failed)
    - return folder_id

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .drive_request:
      why:
        constructs: "Builds the decoded folder-creation response carrying the new Drive id."
    .CloudFilesError:
      why:
        raises: "Reports a creation response that cannot support subsequent path traversal."
    """
    payload = json.dumps(
        {
            "name": name,
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [parent_id],
        }
    ).encode("utf-8")
    response = drive_request(
        config,
        "POST",
        DRIVE_API_ROOT,
        query={"supportsAllDrives": "true", "fields": "id"},
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    folder_id = str(response.get("id", "")).strip()
    if not folder_id:
        raise CloudFilesError(f"failed to create folder '{name}'")
    return folder_id


def split_relpath(path: str) -> list[str]:
    """Split a normalized relative path into nonempty segments.

    Intent
    ------
    Represent the empty path as no segments and ignore redundant separators defensively.

    Rationale
    ---------
    Traversal helpers operate segment by segment, while callers retain slash-separated
    paths at their public boundary.

    Pseudocode
    ----------
    - if path is empty:
      - return empty segment list
    - return nonempty slash-delimited segments

    Wraps
    -----
    - none
    """
    if not path:
        return []
    return [part for part in path.split("/") if part]


def resolve_folder_path(
    config: CloudFilesConfig,
    base_id: str,
    relpath: str,
    *,
    create: bool,
) -> str:
    """Resolve a relative folder path, optionally creating absent segments.

    Intent
    ------
    Walk from a known base id, requiring each segment to resolve uniquely and creating
    missing folders only when requested.

    Rationale
    ---------
    Drive permits duplicate sibling names, so path traversal must reject ambiguity
    instead of choosing an arbitrary folder and crossing the intended namespace.

    Pseudocode
    ----------
    - segments = .split_relpath(relpath)
    - set current_id = base_id
    - for segment in segments:
      - matches = .list_children(config, current_id, segment, folder MIME)
      - if matches are empty:
        - if creation is allowed:
          - current_id = .create_folder(config, current_id, segment)
          - continue
        - raise FileNotFoundError(relpath)
      - if multiple matches exist:
        - raise .CloudFilesError(ambiguous folder segment)
      - set current_id = unique match id
    - return current_id

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .split_relpath:
      why:
        constructs: "Builds the ordered path-segment product carried through traversal."
    .list_children:
      why:
        constructs: "Builds each candidate folder set used to advance or reject traversal."
    .create_folder:
      why:
        constructs: "Returns the id carried into traversal when an allowed segment is absent."
    .CloudFilesError:
      why:
        raises: "Reports duplicate sibling folders that make a path segment ambiguous."
    """
    current_id = base_id
    for segment in split_relpath(relpath):
        matches = list_children(config, current_id, name=segment, mime_type=FOLDER_MIME_TYPE)
        if not matches:
            if create:
                current_id = create_folder(config, current_id, segment)
                continue
            raise FileNotFoundError(relpath)
        if len(matches) > 1:
            raise CloudFilesError(
                f"ambiguous folder path segment '{segment}' under '{relpath}'"
            )
        current_id = str(matches[0]["id"])
    return current_id


def resolve_file(config: CloudFilesConfig, base_id: str, relpath: str) -> dict[str, Any]:
    """Resolve one unique Drive record at a relative file path.

    Intent
    ------
    Normalize the path, resolve its parent without creation, and require exactly one
    child record with the final segment's name.

    Rationale
    ---------
    Delete operations need the original Drive metadata while retaining the same
    missing and duplicate-name protections used by other resolvers.

    Pseudocode
    ----------
    - normalized_path = .validate_relpath(relpath)
    - segments = .split_relpath(normalized_path)
    - parent_id = .resolve_folder_path(config, base_id, parent segments)
    - matches = .list_children(config, parent_id, final segment)
    - if matches are empty:
      - raise FileNotFoundError(normalized_path)
    - if multiple matches exist:
      - raise .CloudFilesError(ambiguous file path)
    - return unique record from matches

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .validate_relpath:
      why:
        transforms: "Produces the safe canonical path used for lookup and error reporting."
    .split_relpath:
      why:
        constructs: "Builds the parent and leaf segments used by Drive resolution."
    .resolve_folder_path:
      why:
        constructs: "Returns the parent folder id consumed by the final child query."
    .list_children:
      why:
        constructs: "Builds the candidate record list from which the unique file is selected."
    .CloudFilesError:
      why:
        raises: "Reports duplicate final-name records that prevent deterministic resolution."
    """
    normalized = validate_relpath(relpath)
    parts = split_relpath(normalized)
    parent_path = "/".join(parts[:-1])
    parent_id = resolve_folder_path(config, base_id, parent_path, create=False)
    matches = list_children(config, parent_id, name=parts[-1])
    if not matches:
        raise FileNotFoundError(normalized)
    if len(matches) > 1:
        raise CloudFilesError(f"ambiguous file path: {normalized}")
    return matches[0]


def resolve_entry(config: CloudFilesConfig, base_id: str, relpath: str) -> RemoteEntry:
    """Resolve one unique Drive path into a typed remote entry.

    Intent
    ------
    Locate the named child beneath its parent and retain only canonical path, id, and
    folder status for later copy and listing decisions.

    Rationale
    ---------
    A small immutable entry avoids passing provider response mappings through recursive
    traversal and local-target logic while preserving ambiguity checks.

    Pseudocode
    ----------
    - normalized_path = .validate_relpath(relpath)
    - segments = .split_relpath(normalized_path)
    - parent_id = .resolve_folder_path(config, base_id, parent segments)
    - matches = .list_children(config, parent_id, final segment)
    - if matches are empty:
      - raise FileNotFoundError(normalized_path)
    - if multiple matches exist:
      - raise .CloudFilesError(ambiguous entry path)
    - return .RemoteEntry(normalized_path, unique id from matches, folder marker)

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .validate_relpath:
      why:
        transforms: "Produces the canonical relative path stored in the returned entry."
    .split_relpath:
      why:
        constructs: "Builds parent and leaf segments for entry lookup."
    .resolve_folder_path:
      why:
        constructs: "Returns the parent folder id used to query the final segment."
    .list_children:
      why:
        constructs: "Builds the candidate records inspected for a unique entry."
    .RemoteEntry:
      why:
        constructs: "Builds the typed entry product consumed by listing and transfer logic."
    .CloudFilesError:
      why:
        raises: "Reports duplicate final-name records that make the entry ambiguous."
    """
    normalized = validate_relpath(relpath)
    parts = split_relpath(normalized)
    parent_path = "/".join(parts[:-1])
    parent_id = resolve_folder_path(config, base_id, parent_path, create=False)
    matches = list_children(config, parent_id, name=parts[-1])
    if not matches:
        raise FileNotFoundError(normalized)
    if len(matches) > 1:
        raise CloudFilesError(f"ambiguous file path: {normalized}")
    match = matches[0]
    return RemoteEntry(
        path=normalized,
        id=str(match["id"]),
        is_dir=match.get("mimeType") == FOLDER_MIME_TYPE,
    )


def resolve_base_id(config: CloudFilesConfig, *, use_llm_root: bool) -> str:
    """Select Drive root or resolve the configured LLM-root folder id.

    Intent
    ------
    Return Google's root id for broad operations and create or resolve the configured
    scoped path for routine LLM storage.

    Rationale
    ---------
    One explicit switch keeps broader reads separate from preapproved scoped operations
    without duplicating path-resolution behavior.

    Pseudocode
    ----------
    - if broad Drive scope is requested:
      - return Drive root id
    - return .resolve_folder_path(config, Drive root id, configured LLM root)

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .resolve_folder_path:
      why:
        constructs: "Returns the scoped base folder id carried into subsequent Drive operations."
    """
    if not use_llm_root:
        return "root"
    llm_root = config.remote_llm_root.rstrip("/")
    return resolve_folder_path(config, "root", llm_root, create=True)


def download_bytes(config: CloudFilesConfig, relpath: str, *, use_llm_root: bool) -> bytes:
    """Download the raw bytes of one resolved non-folder Drive entry.

    Intent
    ------
    Resolve the selected base and path, reject folders, and request media bytes for the
    entry id.

    Rationale
    ---------
    Byte transport supports both text reads and binary copies while keeping folder
    misuse and provider URL construction behind one boundary.

    Pseudocode
    ----------
    - base_id = .resolve_base_id(config)
    - entry = .resolve_entry(config, base_id, relpath)
    - if entry is a directory:
      - raise .CloudFilesError(folder cannot be downloaded)
    - return .drive_request(config, GET, entry media URL)

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .resolve_base_id:
      why:
        constructs: "Returns the base folder id used to resolve the requested path."
    .resolve_entry:
      why:
        constructs: "Builds the typed Drive entry whose id and kind control the download."
    .drive_request:
      why:
        constructs: "Returns the raw media bytes carried to the read or copy caller."
    .CloudFilesError:
      why:
        raises: "Rejects attempts to download a folder through the file-media endpoint."
    """
    base_id = resolve_base_id(config, use_llm_root=use_llm_root)
    entry = resolve_entry(config, base_id, relpath)
    if entry.is_dir:
        raise CloudFilesError(f"path is a folder: {relpath}; use list instead")
    return drive_request(
        config,
        "GET",
        f"{DRIVE_API_ROOT}/{urllib.parse.quote(entry.id)}",
        query={"alt": "media", "supportsAllDrives": "true"},
        expect_json=False,
    )


def read_text(config: CloudFilesConfig, relpath: str, *, use_llm_root: bool) -> str:
    """Download one Drive file and decode it as strict UTF-8 text.

    Intent
    ------
    Reuse byte download semantics and translate invalid UTF-8 into a service failure.

    Rationale
    ---------
    The public read interfaces promise plain text, whereas copy operations must remain
    byte-preserving; this boundary makes that distinction explicit.

    Pseudocode
    ----------
    - body = .download_bytes(config, relpath)
    - if body is not valid UTF-8:
      - raise .CloudFilesError(nontext file)
    - return UTF-8 text decoded from body

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .download_bytes:
      why:
        constructs: "Returns the raw file content transformed into the public text result."
    .CloudFilesError:
      why:
        raises: "Translates a Unicode decoding failure into the cloud-files error contract."
    """
    body = download_bytes(config, relpath, use_llm_root=use_llm_root)
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CloudFilesError(f"file is not valid UTF-8 text: {relpath}") from exc


def list_entries(config: CloudFilesConfig, relpath: str, *, use_llm_root: bool) -> list[str]:
    """List sorted names from a Drive folder's first returned child page.

    Intent
    ------
    Resolve the selected base and optional folder, append slash markers to returned
    child folders, and sort names from the single ``list_children`` response.

    Rationale
    ---------
    CLI and delegated list operations get stable human-readable output for the fetched
    page without exposing provider ids or recursively traversing the tree.

    Pseudocode
    ----------
    - normalized_path = .validate_relpath(relpath)
    - base_id = .resolve_base_id(config)
    - if normalized_path is empty:
      - set folder_id = base_id
    - else:
      - folder_id = .resolve_folder_path(config, base_id, normalized_path)
    - children = .list_children(config, folder_id)
    - set names = child names with folder markers from children
    - return sorted names

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .validate_relpath:
      why:
        transforms: "Produces the canonical optional folder path used for resolution."
    .resolve_base_id:
      why:
        constructs: "Returns the root id from which the listing path is interpreted."
    .resolve_folder_path:
      why:
        constructs: "Returns the target folder id when a nonempty relative path is supplied."
    .list_children:
      why:
        constructs: "Builds the child metadata collection transformed into display names."
    """
    normalized = validate_relpath(relpath, allow_empty=True)
    base_id = resolve_base_id(config, use_llm_root=use_llm_root)
    folder_id = (
        resolve_folder_path(config, base_id, normalized, create=False)
        if normalized
        else base_id
    )
    children = list_children(config, folder_id)
    names: list[str] = []
    for child in children:
        name = str(child["name"])
        if child.get("mimeType") == FOLDER_MIME_TYPE:
            name += "/"
        names.append(name)
    return sorted(names)


def multipart_body(
    metadata: dict[str, Any], content: bytes, content_type: str
) -> tuple[bytes, str]:
    """Encode Drive metadata and file bytes as a multipart upload body.

    Intent
    ------
    Generate a unique boundary and assemble JSON metadata plus binary content with the
    line endings and closing delimiter required by multipart upload.

    Rationale
    ---------
    Keeping byte framing separate from upload decisions prevents create and update
    branches from drifting in their wire format.

    Pseudocode
    ----------
    - set boundary = unique multipart delimiter
    - set parts = encoded metadata, content bytes, and closing delimiter using boundary
    - return joined parts and boundary

    Wraps
    -----
    - none
    """
    boundary = f"===============cloudfiles-{uuid.uuid4().hex}=="
    parts = [
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode("utf-8"),
        json.dumps(metadata).encode("utf-8"),
        b"\r\n",
        f"--{boundary}\r\nContent-Type: {content_type}\r\n\r\n".encode("utf-8"),
        content,
        b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    return b"".join(parts), boundary


def upload_bytes(
    config: CloudFilesConfig,
    relpath: str,
    content: bytes,
    *,
    source_name: str | None = None,
    use_llm_root: bool = True,
) -> None:
    """Create a Drive file or patch one sole same-name Drive entry with supplied bytes.

    Intent
    ------
    Resolve and create parent folders, reject duplicate target names, infer content
    type, PATCH a sole same-name entry without checking its MIME kind, or POST when no
    such entry exists.

    Rationale
    ---------
    Both text writes and local-file uploads need identical path safety and existing-id
    selection. The implementation does not prove that a sole target is a file before
    PATCHing it, so the documentation preserves that edge case.

    Pseudocode
    ----------
    - base_id = .resolve_base_id(config)
    - normalized = .validate_relpath(relpath)
    - parts = .split_relpath(normalized)
    - parent = .resolve_folder_path(config, base_id, parent path from parts)
    - matches = .list_children(config, parent, filename)
    - if multiple matches exist:
      - raise .CloudFilesError(ambiguous path)
    - if one match exists:
      - set patch_metadata = filename
      - patch_body = .multipart_body(patch_metadata, content, MIME type)
      - @.drive_request(config, PATCH, match id, patch_body)
    - else:
      - set create_metadata = filename and parent
      - create_body = .multipart_body(create_metadata, content, MIME type)
      - @.drive_request(config, POST, parent, create_body)
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .drive_request:
      why:
        writes: "Sends the selected create or replace multipart request to Google Drive."

    InstantiationsFromRepo
    ----------------------
    .resolve_base_id:
      why:
        constructs: "Returns the base folder id used to interpret the destination path."
    .validate_relpath:
      why:
        transforms: "Produces the canonical safe destination path."
    .split_relpath:
      why:
        constructs: "Builds parent and filename segments for upload resolution."
    .resolve_folder_path:
      why:
        constructs: "Returns the existing or newly created parent folder id."
    .list_children:
      why:
        constructs: "Builds the same-name record set that selects create versus update."
    .multipart_body:
      why:
        constructs: "Builds the encoded request body and boundary consumed by Drive upload."
    .CloudFilesError:
      why:
        raises: "Reports duplicate target records that make overwrite selection ambiguous."
    """
    base_id = resolve_base_id(config, use_llm_root=use_llm_root)
    normalized = validate_relpath(relpath)
    parts = split_relpath(normalized)
    parent_path = "/".join(parts[:-1])
    parent_id = resolve_folder_path(config, base_id, parent_path, create=True)
    filename = parts[-1]
    existing = list_children(config, parent_id, name=filename)
    if len(existing) > 1:
        raise CloudFilesError(f"ambiguous file path: {normalized}")

    mime_type = mimetypes.guess_type(source_name or filename)[0] or "application/octet-stream"

    if existing:
        metadata = {"name": filename}
        body, boundary = multipart_body(metadata, content, mime_type)
        drive_request(
            config,
            "PATCH",
            f"{DRIVE_UPLOAD_ROOT}/{urllib.parse.quote(str(existing[0]['id']))}",
            query={
                "uploadType": "multipart",
                "supportsAllDrives": "true",
                "fields": "id",
            },
            data=body,
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        )
        return

    metadata = {"name": filename, "parents": [parent_id]}
    body, boundary = multipart_body(metadata, content, mime_type)
    drive_request(
        config,
        "POST",
        DRIVE_UPLOAD_ROOT,
        query={
            "uploadType": "multipart",
            "supportsAllDrives": "true",
            "fields": "id",
        },
        data=body,
        headers={"Content-Type": f"multipart/related; boundary={boundary}"},
    )


def write_text(
    config: CloudFilesConfig,
    relpath: str,
    text: str,
    *,
    use_llm_root: bool = True,
) -> None:
    """Encode text as UTF-8 and upload it to one Drive-relative path.

    Intent
    ------
    Preserve the destination basename as the MIME-type hint and delegate byte upload.

    Rationale
    ---------
    Plain-text interfaces should share the binary upload implementation while making
    their encoding choice explicit and consistent.

    Pseudocode
    ----------
    - set encoded_text = UTF-8 bytes from text
    - set upload_effect = delegate encoded_text and destination basename to the wrapped upload
    - return

    Wraps
    -----
    - .upload_bytes -> preprocess: encode text and derive basename; postprocess: none; fixed_arguments: none
    """
    upload_bytes(
        config,
        relpath,
        text.encode("utf-8"),
        source_name=Path(relpath).name,
        use_llm_root=use_llm_root,
    )


def delete_file(
    config: CloudFilesConfig,
    relpath: str,
    *,
    use_llm_root: bool = True,
) -> None:
    """Delete one resolved Drive file while refusing folder deletion.

    Intent
    ------
    Resolve the selected base and target metadata, reject folders, and issue the Drive
    delete request for the unique file id.

    Rationale
    ---------
    The MIME check rejects a record reported as a folder before one DELETE request
    targets the uniquely resolved id; this helper performs no recursive deletion.

    Pseudocode
    ----------
    - base_id = .resolve_base_id(config)
    - record = .resolve_file(config, base_id, relpath)
    - if record has folder MIME type:
      - raise .CloudFilesError(folder deletion refused)
    - @.drive_request(config, DELETE, record id)
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .drive_request:
      why:
        writes: "Deletes the uniquely resolved file from Google Drive."

    InstantiationsFromRepo
    ----------------------
    .resolve_base_id:
      why:
        constructs: "Returns the base folder id used to interpret the deletion path."
    .resolve_file:
      why:
        constructs: "Builds the target metadata whose id and MIME type gate deletion."
    .CloudFilesError:
      why:
        raises: "Rejects folder deletion through the file-only interface."
    """
    base_id = resolve_base_id(config, use_llm_root=use_llm_root)
    info = resolve_file(config, base_id, relpath)
    if info.get("mimeType") == FOLDER_MIME_TYPE:
        raise CloudFilesError(f"path is a folder: {relpath}")
    drive_request(
        config,
        "DELETE",
        f"{DRIVE_API_ROOT}/{urllib.parse.quote(str(info['id']))}",
        query={"supportsAllDrives": "true"},
    )


def walk_remote_entries(
    config: CloudFilesConfig,
    parent_id: str,
    *,
    prefix: str = "",
) -> list[RemoteEntry]:
    """Recursively enumerate descendants reachable through first-page child results.

    Intent
    ------
    Convert each returned child record to ``RemoteEntry`` and depth-first extend the
    result through each returned folder.

    Rationale
    ---------
    Remote glob matching needs recursive paths, but each folder lookup fetches at most
    one 200-item page. The resulting index can omit children from later pages.

    Pseudocode
    ----------
    - children = .list_children(config, parent_id)
    - set entries = empty entry list
    - for child in children:
      - entry = .RemoteEntry(prefixed child path, child id, folder marker)
      - set entries = entries plus entry
      - if entry is a directory:
        - descendants = .walk_remote_entries(config, entry id, entry path prefix)
        - set entries = entries plus descendants
    - return entries

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .list_children:
      why:
        constructs: "Builds each child collection carried through recursive enumeration."
    .RemoteEntry:
      why:
        constructs: "Builds the typed path records returned to remote matchers."
    .walk_remote_entries:
      why:
        constructs: "Builds descendant entry products that are merged into the current traversal."
    """
    entries: list[RemoteEntry] = []
    for child in list_children(config, parent_id):
        name = str(child["name"])
        path = f"{prefix}{name}"
        entry = RemoteEntry(
            path=path,
            id=str(child["id"]),
            is_dir=child.get("mimeType") == FOLDER_MIME_TYPE,
        )
        entries.append(entry)
        if entry.is_dir:
            entries.extend(
                walk_remote_entries(config, entry.id, prefix=f"{entry.path}/")
            )
    return entries


def match_remote_entries(
    config: CloudFilesConfig,
    base_id: str,
    pattern: str,
    *,
    include_dirs: bool,
) -> list[RemoteEntry]:
    """Match a remote glob against entries returned by first-page traversal.

    Intent
    ------
    Normalize and validate the pattern, filter fetched entries by directory policy and
    full relative path match, require one result, and return path-sorted entries.

    Rationale
    ---------
    Anchoring candidate and pattern with a leading slash avoids basename-only matching,
    but matching cannot recover entries omitted by ``list_children`` pagination.

    Pseudocode
    ----------
    - normalized_pattern = .normalize_relpath_pattern(pattern)
    - if normalized_pattern is empty or lacks glob syntax:
      - raise ValueError(glob pattern required)
    - remote_entries = .walk_remote_entries(config, base_id)
    - set matches = allowed remote_entries matching normalized_pattern
    - if matches is empty:
      - raise FileNotFoundError(normalized_pattern)
    - return matches sorted by path

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .has_glob_magic:
      why:
        computes: "Determines whether the normalized operand satisfies this glob-only boundary."

    InstantiationsFromRepo
    ----------------------
    .normalize_relpath_pattern:
      why:
        transforms: "Produces the safe canonical glob used for matching and errors."
    .walk_remote_entries:
      why:
        constructs: "Builds the recursive entry product carried through matching."
    """
    normalized = normalize_relpath_pattern(pattern, allow_empty=True)
    if not normalized or not has_glob_magic(normalized):
        raise ValueError("glob pattern required")
    matches = [
        entry
        for entry in walk_remote_entries(config, base_id)
        if (include_dirs or not entry.is_dir)
        and PurePosixPath(f"/{entry.path}").match(f"/{normalized}")
    ]
    if not matches:
        raise FileNotFoundError(normalized)
    return sorted(matches, key=lambda entry: entry.path)


def expand_local_sources(args: Sequence[str]) -> list[Path]:
    """Expand local copy operands into existing non-directory paths.

    Intent
    ------
    Apply recursive globbing only to pattern operands, reject missing matches and paths,
    and refuse directory copies.

    Rationale
    ---------
    Upload copy semantics support multiple files but deliberately exclude recursive
    local directory transfer, so validation must occur before any Drive writes begin.

    Pseudocode
    ----------
    - set sources = empty local path list
    - for operand in source operands:
      - if operand has glob magic:
        - set matches = recursive local glob matches
      - else:
        - set matches = literal operand path
      - if matches are empty or a path is missing:
        - raise FileNotFoundError(source operand)
      - if a matched path is a directory:
        - raise .CloudFilesError(directory copy unsupported)
      - set sources = sources plus matches
    - return sources

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .has_glob_magic:
      why:
        computes: "Selects recursive local glob expansion versus literal-path handling."

    InstantiationsFromRepo
    ----------------------
    .CloudFilesError:
      why:
        raises: "Reports unsupported local directory sources before upload effects occur."
    """
    sources: list[Path] = []
    for raw in args:
        matches = (
            [Path(match) for match in glob.glob(raw, recursive=True)]
            if has_glob_magic(raw)
            else [Path(raw)]
        )
        if not matches:
            raise FileNotFoundError(raw)
        for path in matches:
            if not path.exists():
                raise FileNotFoundError(str(path))
            if path.is_dir():
                raise CloudFilesError(f"directory copy is not supported: {path}")
            sources.append(path)
    return sources


def expand_remote_sources(
    config: CloudFilesConfig,
    source_specs: Sequence[str],
    *,
    use_llm_root: bool,
) -> list[RemoteEntry]:
    """Expand remote copy or removal operands into unique sorted file entries.

    Intent
    ------
    Resolve one base, expand glob specs over recursively fetched entries, resolve literal
    specs directly, reject folders, deduplicate by path, and sort the result.

    Rationale
    ---------
    Copy and remove process the same deterministic returned file set when overlapping
    operands name a file more than once; glob expansion inherits first-page omissions.

    Pseudocode
    ----------
    - base_id = .resolve_base_id(config)
    - set sources_by_path = empty mapping
    - for spec in source_specs:
      - parsed_spec = .parse_llm_spec(spec)
      - if parsed_spec has glob magic:
        - matches = .match_remote_entries(config, base_id, parsed_spec)
        - set sources_by_path = sources_by_path updated with matches
        - continue
      - entry = .resolve_entry(config, base_id, parsed_spec)
      - if entry is a directory:
        - raise .CloudFilesError(folder source refused)
      - set sources_by_path = sources_by_path updated with entry
    - return entries from sources_by_path in sorted path order

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .has_glob_magic:
      why:
        computes: "Selects recursive matching versus literal entry resolution for each operand."

    InstantiationsFromRepo
    ----------------------
    .resolve_base_id:
      why:
        constructs: "Returns the base folder id shared by all source resolutions."
    .parse_llm_spec:
      why:
        transforms: "Produces each normalized remote source pattern."
    .match_remote_entries:
      why:
        constructs: "Builds matched entry products carried into the deduplicated source mapping."
    .resolve_entry:
      why:
        constructs: "Builds the typed entry contributed by a literal operand."
    .CloudFilesError:
      why:
        raises: "Rejects remote folder sources from file-only copy and removal operations."
    """
    base_id = resolve_base_id(config, use_llm_root=use_llm_root)
    sources: dict[str, RemoteEntry] = {}
    for spec in source_specs:
        pattern, _dir_hint = parse_llm_spec(spec, allow_glob=True)
        if has_glob_magic(pattern):
            for entry in match_remote_entries(
                config,
                base_id,
                pattern,
                include_dirs=False,
            ):
                sources[entry.path] = entry
            continue
        entry = resolve_entry(config, base_id, pattern)
        if entry.is_dir:
            raise CloudFilesError(f"path is a folder: {pattern}")
        sources[entry.path] = entry
    return [sources[path] for path in sorted(sources)]


def resolve_local_target(
    raw_dest: str,
    *,
    source_name: str,
    multiple_sources: bool,
) -> Path:
    """Resolve a download destination to a file path without creating directories.

    Intent
    ------
    Treat multiple sources, a trailing separator, or an existing directory as directory
    mode; otherwise require the destination's parent to exist.

    Rationale
    ---------
    Copy must not silently create or reinterpret local directories, and multi-source
    downloads need a distinct destination file for every remote basename.

    Pseudocode
    ----------
    - set destination = local path from raw destination
    - if multiple sources, directory hint, or destination is a directory:
      - if destination is not an existing directory:
        - raise .CloudFilesError(invalid destination directory)
      - return destination joined with source_name
    - if destination parent is missing:
      - raise .CloudFilesError(missing destination parent)
    - return destination

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CloudFilesError:
      why:
        raises: "Reports absent or non-directory local destinations before a download writes bytes."
    """
    dest = Path(raw_dest)
    dir_hint = raw_dest.endswith((os.sep, "/"))
    if multiple_sources or dir_hint or dest.is_dir():
        if not dest.exists():
            raise CloudFilesError(f"destination directory does not exist: {dest}")
        if not dest.is_dir():
            raise CloudFilesError(f"destination is not a directory: {dest}")
        return dest / source_name
    if not dest.parent.exists():
        raise CloudFilesError(f"destination directory does not exist: {dest.parent}")
    return dest


def resolve_remote_target(
    config: CloudFilesConfig,
    raw_dest_spec: str,
    *,
    source_name: str,
    multiple_sources: bool,
    use_llm_root: bool,
) -> str:
    """Resolve an ``llm:`` upload destination to its final file path.

    Intent
    ------
    Combine source basename with directory-shaped destinations, honor an existing remote
    folder, and reject directory mode when the destination is an existing file.

    Rationale
    ---------
    Remote copy should mirror familiar file-copy destination rules without creating an
    ambiguous interpretation when Drive already contains a conflicting entry.

    Pseudocode
    ----------
    - destination_path = .parse_llm_spec(raw destination)
    - base_id = .resolve_base_id(config)
    - set directory_mode = multiple sources or trailing slash hint
    - set existing = no remote entry
    - if destination_path exists:
      - existing = .resolve_entry(config, base_id, destination_path)
      - set directory_mode = directory_mode or existing is a directory
    - if existing is a file and directory_mode:
      - raise .CloudFilesError(destination is not a directory)
    - if directory_mode:
      - return destination_path joined with source_name
    - return destination_path

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .parse_llm_spec:
      why:
        transforms: "Produces the normalized destination path and trailing-slash hint."
    .resolve_base_id:
      why:
        constructs: "Returns the base folder id used to inspect an existing destination."
    .resolve_entry:
      why:
        constructs: "Builds existing destination metadata that refines file-versus-directory mode."
    .CloudFilesError:
      why:
        raises: "Reports an existing remote file that cannot serve as a directory destination."
    """
    dest_relpath, dir_hint = parse_llm_spec(raw_dest_spec, allow_empty=True)
    base_id = resolve_base_id(config, use_llm_root=use_llm_root)
    use_as_dir = multiple_sources or dir_hint
    existing: RemoteEntry | None = None
    if dest_relpath:
        try:
            existing = resolve_entry(config, base_id, dest_relpath)
        except FileNotFoundError:
            existing = None
    if existing is not None and existing.is_dir:
        use_as_dir = True
    if existing is not None and not existing.is_dir and use_as_dir:
        raise CloudFilesError(f"destination is not a directory: {raw_dest_spec}")
    if use_as_dir:
        return f"{dest_relpath}/{source_name}" if dest_relpath else source_name
    return dest_relpath


def read_entrypoint(args: Sequence[str], *, use_llm_root: bool) -> int:
    """Run text read or immediate-folder listing for one selected Drive scope.

    Intent
    ------
    Load configuration, interpret an optional ``--list`` mode, print list entries one per
    line, or write file text without adding output characters.

    Rationale
    ---------
    Read and broader-read wrappers share transport behavior but choose their scope
    explicitly, while exact stdout handling preserves stored text verbatim.

    Pseudocode
    ----------
    - config = .load_config()
    - if list mode is selected:
      - list_path = .validate_relpath(optional list path)
      - entries = .list_entries(config, list_path)
      - set stdout_effect = print entries
      - return success
    - read_path = .validate_relpath(required read path)
    - text = .read_text(config, read_path)
    - set stdout_effect = write text exactly
    - return success

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .load_config:
      why:
        constructs: "Builds the runtime configuration used by the selected operation."
    .validate_relpath:
      why:
        transforms: "Produces the canonical list or read path used by the selected operation."
    .list_entries:
      why:
        constructs: "Builds ordered child-name products carried through list-mode output."
    .read_text:
      why:
        constructs: "Builds the exact text product carried to standard output."
    """
    config = load_config()
    argv = list(args)
    if argv and argv[0] == "--list":
        path = validate_relpath(argv[1], allow_empty=True) if len(argv) > 1 else ""
        for entry in list_entries(config, path, use_llm_root=use_llm_root):
            print(entry)
        return 0

    path = validate_relpath(argv[0]) if argv else validate_relpath("")
    sys.stdout.write(read_text(config, path, use_llm_root=use_llm_root))
    return 0


def cp_entrypoint(args: Sequence[str], *, use_llm_root: bool) -> int:
    """Copy files across the local/Drive boundary in exactly one direction.

    Intent
    ------
    Require sources and a destination, forbid remote-to-remote or local-to-local copy,
    expand the source side, resolve targets, and transfer bytes for each returned source.

    Rationale
    ---------
    Direction validation before effects and deterministic expansion prevent partial
    transfers caused by malformed mixed source sets.

    Pseudocode
    ----------
    - if operands are incomplete or remote sides are invalid:
      - raise ValueError(copy contract)
    - config = .load_config()
    - if destination is remote:
      - local_sources = .expand_local_sources(source_operands)
      - for source in local_sources:
        - remote_target = .resolve_remote_target(config, destination, source name)
        - @.upload_bytes(config, remote_target, source bytes)
      - return success
    - remote_sources = .expand_remote_sources(config, source_operands)
    - for source in remote_sources:
      - local_target = .resolve_local_target(destination, source name)
      - downloaded_bytes = .download_bytes(config, source path)
      - set local_write_effect = local_target receives downloaded_bytes
    - return success

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .upload_bytes:
      why:
        writes: "Transfers each validated local source to its resolved remote target."

    InstantiationsFromRepo
    ----------------------
    .load_config:
      why:
        constructs: "Builds runtime settings shared by all transfers in the command."
    .expand_local_sources:
      why:
        constructs: "Builds the validated local source set before remote writes begin."
    .resolve_remote_target:
      why:
        constructs: "Returns each final remote file path used by upload."
    .expand_remote_sources:
      why:
        constructs: "Builds the unique sorted remote source set before local writes begin."
    .resolve_local_target:
      why:
        constructs: "Returns each final local path used by the download branch."
    .download_bytes:
      why:
        constructs: "Builds remote byte products carried into local file writes."
    """
    if len(args) < 2:
        raise ValueError("usage: cp_llm.py <source>... <destination>")
    config = load_config()
    source_args = list(args[:-1])
    raw_dest = args[-1]
    dest_is_remote = raw_dest.startswith(LLM_PREFIX)
    source_are_remote = [source.startswith(LLM_PREFIX) for source in source_args]

    if dest_is_remote:
        if any(source_are_remote):
            raise ValueError("cp_llm.py requires exactly one remote side")
        local_sources = expand_local_sources(source_args)
        multiple_sources = len(local_sources) > 1
        for source in local_sources:
            remote_target = resolve_remote_target(
                config,
                raw_dest,
                source_name=source.name,
                multiple_sources=multiple_sources,
                use_llm_root=use_llm_root,
            )
            upload_bytes(
                config,
                remote_target,
                source.read_bytes(),
                source_name=source.name,
                use_llm_root=use_llm_root,
            )
        return 0

    if not all(source_are_remote):
        raise ValueError("cp_llm.py requires exactly one remote side")

    remote_sources = expand_remote_sources(config, source_args, use_llm_root=use_llm_root)
    multiple_sources = len(remote_sources) > 1
    for source in remote_sources:
        local_target = resolve_local_target(
            raw_dest,
            source_name=Path(source.path).name,
            multiple_sources=multiple_sources,
        )
        local_target.write_bytes(
            download_bytes(config, source.path, use_llm_root=use_llm_root)
        )
    return 0


def ls_entrypoint(args: Sequence[str], *, use_llm_root: bool) -> int:
    """Print literal or globbed remote paths for the selected Drive scope.

    Intent
    ------
    Default to the scoped root, list empty and folder operands from their first child
    page, expand globs over recursively fetched entries, and print literal files directly.

    Rationale
    ---------
    A single command boundary keeps display conventions stable across returned root,
    folder, file, and wildcard entries; it does not add pagination to its helpers.

    Pseudocode
    ----------
    - config = .load_config()
    - base_id = .resolve_base_id(config)
    - for spec in listing_operands:
      - parsed_spec = .parse_llm_spec(spec)
      - if parsed_spec is root:
        - entries = .list_entries(config, root)
        - set output_effect = print entries
        - continue
      - if parsed_spec has glob magic:
        - entries = .match_remote_entries(config, base_id, parsed_spec)
        - set output_effect = print entries
        - continue
      - entry = .resolve_entry(config, base_id, parsed_spec)
      - if entry is a directory:
        - entries = .list_entries(config, parsed_spec)
      - set output_effect = print entry or entries

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .has_glob_magic:
      why:
        computes: "Selects recursive wildcard expansion versus literal entry handling."

    InstantiationsFromRepo
    ----------------------
    .load_config:
      why:
        constructs: "Builds runtime settings used by every listing operand."
    .resolve_base_id:
      why:
        constructs: "Returns the base folder id for literal and glob resolution."
    .parse_llm_spec:
      why:
        transforms: "Produces each normalized listing operand."
    .list_entries:
      why:
        constructs: "Builds immediate child-name products carried through root and folder output."
    .match_remote_entries:
      why:
        constructs: "Builds matched entry products carried through glob output."
    .resolve_entry:
      why:
        constructs: "Builds literal entry metadata used to choose file or folder output."
    """
    config = load_config()
    specs = list(args) or [LLM_PREFIX]
    base_id = resolve_base_id(config, use_llm_root=use_llm_root)
    for spec in specs:
        pattern, _dir_hint = parse_llm_spec(spec, allow_empty=True, allow_glob=True)
        if not pattern:
            for entry in list_entries(config, "", use_llm_root=use_llm_root):
                print(entry)
            continue
        if has_glob_magic(pattern):
            for entry in match_remote_entries(
                config,
                base_id,
                pattern,
                include_dirs=True,
            ):
                print(f"{entry.path}/" if entry.is_dir else entry.path)
            continue
        entry = resolve_entry(config, base_id, pattern)
        if entry.is_dir:
            for child in list_entries(config, pattern, use_llm_root=use_llm_root):
                print(child)
            continue
        print(entry.path)
    return 0


def rm_entrypoint(args: Sequence[str], *, use_llm_root: bool) -> int:
    """Delete files selected by literal or globbed remote operands.

    Intent
    ------
    Require at least one operand, expand the returned remote source set, and delete each
    resolved file in deterministic order.

    Rationale
    ---------
    Reusing source expansion gives remove the same folder rejection, deduplication, and
    no-match behavior as downloads before the first destructive request.

    Pseudocode
    ----------
    - if source operands are empty:
      - raise ValueError(remove usage)
    - config = .load_config()
    - entries = .expand_remote_sources(config, source operands)
    - for entry in entries:
      - @.delete_file(config, entry path)
    - return success

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .delete_file:
      why:
        writes: "Deletes each fully resolved file selected by the command operands."

    InstantiationsFromRepo
    ----------------------
    .load_config:
      why:
        constructs: "Builds runtime settings used for expansion and deletion."
    .expand_remote_sources:
      why:
        constructs: "Builds the validated, deduplicated, and sorted file deletion set."
    """
    if not args:
        raise ValueError("usage: rm_llm.py <pattern>...")
    config = load_config()
    entries = expand_remote_sources(config, args, use_llm_root=use_llm_root)
    for entry in entries:
        delete_file(config, entry.path, use_llm_root=use_llm_root)
    return 0


def run_entrypoint(
    entrypoint: Callable[..., int],
    args: Sequence[str],
    *,
    use_llm_root: bool,
) -> int:
    """Convert expected entrypoint failures into stderr text and status one.

    Intent
    ------
    Invoke a selected operation with its explicit Drive scope and catch only the domain,
    missing-path, and input errors exposed by command implementations.

    Rationale
    ---------
    Thin script wrappers need trace-free expected failures while unexpected programming
    errors must still propagate for diagnosis.

    Pseudocode
    ----------
    - set delegated_status = entrypoint invoked with operands and Drive scope
    - if an expected domain or path exception occurs:
      - set stderr_effect = print exception message
      - return failure status
    - return delegated_status

    Wraps
    -----
    - none
    """
    try:
        return entrypoint(args, use_llm_root=use_llm_root)
    except (CloudFilesError, FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def write_entrypoint(args: Sequence[str], *, use_llm_root: bool) -> int:
    """Read stdin and write it as one Drive text file.

    Intent
    ------
    Load configuration, require and normalize the first path operand, consume all stdin,
    and delegate the scoped text write.

    Rationale
    ---------
    Dispatcher write interfaces provide content on stdin, so this boundary preserves
    exact text while sharing transport and path validation with other commands.

    Pseudocode
    ----------
    - config = .load_config()
    - destination_path = .validate_relpath(required first operand)
    - set input_text = all standard input
    - @.write_text(config, destination_path, input_text)
    - return success

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .write_text:
      why:
        writes: "Transfers the stdin text to the validated Drive-relative path."

    InstantiationsFromRepo
    ----------------------
    .load_config:
      why:
        constructs: "Builds runtime settings used by the text write."
    .validate_relpath:
      why:
        transforms: "Produces the canonical required destination path."
    """
    config = load_config()
    path = validate_relpath(args[0]) if args else validate_relpath("")
    write_text(config, path, sys.stdin.read(), use_llm_root=use_llm_root)
    return 0


def delete_entrypoint(args: Sequence[str], *, use_llm_root: bool) -> int:
    """Delete one Drive file named by the first command argument.

    Intent
    ------
    Load configuration, require and normalize a path operand, and delegate scoped
    file-only deletion.

    Rationale
    ---------
    Registered delete interfaces need a narrow single-path boundary distinct from the
    glob-capable remove command.

    Pseudocode
    ----------
    - config = .load_config()
    - target_path = .validate_relpath(required first operand)
    - @.delete_file(config, target_path)
    - return success

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .delete_file:
      why:
        writes: "Deletes the validated path within the explicitly selected Drive scope."

    InstantiationsFromRepo
    ----------------------
    .load_config:
      why:
        constructs: "Builds runtime settings used by the deletion request."
    .validate_relpath:
      why:
        transforms: "Produces the canonical required deletion path."
    """
    config = load_config()
    path = validate_relpath(args[0]) if args else validate_relpath("")
    delete_file(config, path, use_llm_root=use_llm_root)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch the legacy aggregate CLI and normalize expected command failures.

    Intent
    ------
    Parse the command token, select scoped or broad read semantics, forward remaining
    arguments to the matching entrypoint, and emit usage for missing or unknown commands.

    Rationale
    ---------
    Although dedicated wrappers own registered interfaces, this compatibility surface
    preserves the original command contract and its status codes for direct callers.

    Pseudocode
    ----------
    - set command_operands = supplied argv or process arguments
    - if command_operands is empty:
      - return usage status
    - set command = first command_operands item
    - if command is cp:
      - return .cp_entrypoint(remaining operands)
    - if command is ls:
      - return .ls_entrypoint(remaining operands)
    - if command is rm:
      - return .rm_entrypoint(remaining operands)
    - if command is read or list:
      - return .read_entrypoint(adapted scoped operands)
    - if command is write or delete:
      - return selected .write_entrypoint(remaining operands) or .delete_entrypoint(remaining operands)
    - return broad read status, usage status, or expected failure status

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .cp_entrypoint:
      why:
        constructs: "Returns the copy command status forwarded by the aggregate CLI."
    .ls_entrypoint:
      why:
        constructs: "Returns the listing command status forwarded by the aggregate CLI."
    .rm_entrypoint:
      why:
        constructs: "Returns the removal command status forwarded by the aggregate CLI."
    .read_entrypoint:
      why:
        constructs: "Returns scoped or broad read/list status after command-specific argument adaptation."
    .write_entrypoint:
      why:
        constructs: "Returns the text-write status forwarded by the aggregate CLI."
    .delete_entrypoint:
      why:
        constructs: "Returns the single-file deletion status forwarded by the aggregate CLI."
    """
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print(
            "usage: cloud_files.py {cp|ls|rm|read|list|write|delete|read-remote|list-remote} ...",
            file=sys.stderr,
        )
        return 2

    command = args.pop(0)
    try:
        if command == "cp":
            return cp_entrypoint(args, use_llm_root=True)
        if command == "ls":
            return ls_entrypoint(args, use_llm_root=True)
        if command == "rm":
            return rm_entrypoint(args, use_llm_root=True)
        if command in {"read", "list"}:
            read_args = ["--list", *args] if command == "list" else args
            return read_entrypoint(read_args, use_llm_root=True)
        if command == "write":
            return write_entrypoint(args, use_llm_root=True)
        if command == "delete":
            return delete_entrypoint(args, use_llm_root=True)
        if command in {"read-remote", "list-remote"}:
            read_args = ["--list", *args] if command == "list-remote" else args
            return read_entrypoint(read_args, use_llm_root=False)
        print(
            "usage: cloud_files.py {cp|ls|rm|read|list|write|delete|read-remote|list-remote} ...",
            file=sys.stderr,
        )
        return 2
    except (CloudFilesError, FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
