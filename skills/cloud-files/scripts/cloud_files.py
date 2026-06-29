#!/usr/bin/env python3
from __future__ import annotations

import json
import mimetypes
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
CONFIG_DIR_NAME = ".config/cloud-files"
CONFIG_FILE_NAME = "config.json"
CREDENTIALS_FILE_NAME = "credentials.json"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API_ROOT = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_ROOT = "https://www.googleapis.com/upload/drive/v3/files"


@dataclass(frozen=True)
class CloudFilesConfig:
    remote_llm_root: str
    timeout_seconds: int
    credentials_path: Path


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


def resolve_base_id(config: CloudFilesConfig, *, use_llm_root: bool) -> str:
    if not use_llm_root:
        return "root"
    llm_root = config.remote_llm_root.rstrip("/")
    return resolve_folder_path(config, "root", llm_root, create=True)


def read_text(config: CloudFilesConfig, relpath: str, *, use_llm_root: bool) -> str:
    base_id = resolve_base_id(config, use_llm_root=use_llm_root)
    info = resolve_file(config, base_id, relpath)
    if info.get("mimeType") == FOLDER_MIME_TYPE:
        raise CloudFilesError(f"path is a folder: {relpath}; use list instead")
    body = drive_request(
        config,
        "GET",
        f"{DRIVE_API_ROOT}/{urllib.parse.quote(str(info['id']))}",
        query={"alt": "media", "supportsAllDrives": "true"},
        expect_json=False,
    )
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


def write_text(
    config: CloudFilesConfig,
    relpath: str,
    text: str,
    *,
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

    content = text.encode("utf-8")
    mime_type = mimetypes.guess_type(filename)[0] or "text/plain; charset=utf-8"

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
            "usage: cloud_files.py {read|list|write|delete|read-remote|list-remote} ...",
            file=sys.stderr,
        )
        return 2

    command = args.pop(0)
    try:
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
            "usage: cloud_files.py {read|list|write|delete|read-remote|list-remote} ...",
            file=sys.stderr,
        )
        return 2
    except (CloudFilesError, FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
