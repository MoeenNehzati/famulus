#!/usr/bin/env python3
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
    remote_llm_root: str
    timeout_seconds: int
    credentials_path: Path


@dataclass(frozen=True)
class RemoteEntry:
    path: str
    id: str
    is_dir: bool


class CloudFilesError(RuntimeError):
    pass


def normalize_llm_root(root: str) -> str:
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
    return any(char in path for char in GLOB_CHARS)


def parse_llm_spec(
    spec: str,
    *,
    allow_empty: bool = False,
    allow_glob: bool = False,
) -> tuple[str, bool]:
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
    base = home or Path.home()
    return base / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def default_credentials_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / CONFIG_DIR_NAME / CREDENTIALS_FILE_NAME


def load_config(home: Path | None = None) -> CloudFilesConfig:
    config_path = default_config_path(home)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CloudFilesError(f"missing config file: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise CloudFilesError(f"invalid JSON in config file: {config_path}") from exc

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

    return CloudFilesConfig(
        remote_llm_root=remote_llm_root,
        timeout_seconds=timeout_seconds,
        credentials_path=credentials_path,
    )


def load_credentials(config: CloudFilesConfig) -> dict[str, str]:
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


def get_access_token(config: CloudFilesConfig) -> str:
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
    return value.replace("\\", "\\\\").replace("'", "\\'")


def list_children(
    config: CloudFilesConfig,
    parent_id: str,
    *,
    name: str | None = None,
    mime_type: str | None = None,
) -> list[dict[str, Any]]:
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
    if not use_llm_root:
        return "root"
    llm_root = config.remote_llm_root.rstrip("/")
    return resolve_folder_path(config, "root", llm_root, create=True)


def download_bytes(config: CloudFilesConfig, relpath: str, *, use_llm_root: bool) -> bytes:
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
    body = download_bytes(config, relpath, use_llm_root=use_llm_root)
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CloudFilesError(f"file is not valid UTF-8 text: {relpath}") from exc


def list_entries(config: CloudFilesConfig, relpath: str, *, use_llm_root: bool) -> list[str]:
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
    try:
        return entrypoint(args, use_llm_root=use_llm_root)
    except (CloudFilesError, FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def write_entrypoint(args: Sequence[str], *, use_llm_root: bool) -> int:
    config = load_config()
    path = validate_relpath(args[0]) if args else validate_relpath("")
    write_text(config, path, sys.stdin.read(), use_llm_root=use_llm_root)
    return 0


def delete_entrypoint(args: Sequence[str], *, use_llm_root: bool) -> int:
    config = load_config()
    path = validate_relpath(args[0]) if args else validate_relpath("")
    delete_file(config, path, use_llm_root=use_llm_root)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
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
