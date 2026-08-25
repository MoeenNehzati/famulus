#!/usr/bin/env python3
"""Synchronize committed plugin versions from staged ``pyproject.toml``."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib


_VERSION_RE = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
)
_JSON_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')
_MANIFESTS = (
    Path(".claude-plugin/plugin.json"),
    Path(".codex-plugin/plugin.json"),
)


class SynchronizationError(RuntimeError):
    """The staged release-version inputs cannot be synchronized safely."""


def _git(
    root: Path | None,
    *arguments: str,
    input_bytes: bytes | None = None,
) -> bytes:
    command = ["git"]
    if root is not None:
        command.extend(("-C", str(root)))
    command.extend(arguments)
    completed = subprocess.run(
        command,
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise SynchronizationError(detail or f"Git command failed: {' '.join(command)}")
    return completed.stdout


def _repository_root() -> Path:
    output = _git(None, "rev-parse", "--show-toplevel")
    return Path(output.decode("utf-8", errors="strict").strip()).resolve()


def _index_bytes(root: Path, path: Path) -> bytes:
    return _git(root, "show", f":{path.as_posix()}")


def _index_mode(root: Path, path: Path) -> str:
    output = _git(root, "ls-files", "--stage", "--", path.as_posix())
    lines = output.decode("utf-8", errors="strict").splitlines()
    if len(lines) != 1:
        raise SynchronizationError(f"{path}: expected exactly one staged index entry")
    metadata, recorded_path = lines[0].split("\t", 1)
    mode, _object_id, stage = metadata.split()
    if recorded_path != path.as_posix() or stage != "0":
        raise SynchronizationError(f"{path}: expected an ordinary stage-0 index entry")
    return mode


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SynchronizationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _manifest_output(data: bytes, path: Path, version: str) -> bytes:
    try:
        text = data.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SynchronizationError(f"{path}: malformed UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SynchronizationError(f"{path}: top-level JSON value must be an object")
    current = payload.get("version")
    if not isinstance(current, str):
        raise SynchronizationError(f"{path}: top-level version must be a string")
    if current == version:
        return data
    depth = 0
    position = 0
    while position < len(text):
        character = text[position]
        if character == '"':
            token = _JSON_STRING_RE.match(text, position)
            if token is None:
                raise SynchronizationError(f"{path}: malformed JSON string")
            after_key = token.end()
            while after_key < len(text) and text[after_key].isspace():
                after_key += 1
            if (
                depth == 1
                and json.loads(token.group()) == "version"
                and after_key < len(text)
                and text[after_key] == ":"
            ):
                value_start = after_key + 1
                while value_start < len(text) and text[value_start].isspace():
                    value_start += 1
                value = _JSON_STRING_RE.match(text, value_start)
                if value is None:
                    raise SynchronizationError(
                        f"{path}: top-level version must be a string"
                    )
                replacement = json.dumps(version, ensure_ascii=True)
                return (text[:value_start] + replacement + text[value.end() :]).encode(
                    "utf-8"
                )
            position = token.end()
            continue
        if character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
        position += 1
    raise SynchronizationError(f"{path}: top-level version field not found")


def _temporary_index_active(root: Path) -> bool:
    index_environment = os.environ.get("GIT_INDEX_FILE")
    if not index_environment:
        return False
    active = Path(index_environment)
    if not active.is_absolute():
        active = root / active
    git_directory = Path(
        _git(root, "rev-parse", "--absolute-git-dir")
        .decode("utf-8", errors="strict")
        .strip()
    )
    return active.resolve() != (git_directory / "index").resolve()


def _canonical_version(staged_toml: bytes) -> str:
    try:
        configuration = tomllib.loads(staged_toml.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SynchronizationError(f"pyproject.toml: malformed UTF-8 TOML: {exc}") from exc
    project = configuration.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise SynchronizationError(
            "pyproject.toml: [project].version must be canonical MAJOR.MINOR.PATCH"
        )
    return version


def _atomic_replace(path: Path, data: bytes) -> None:
    mode = path.stat().st_mode & 0o777
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def synchronize() -> str:
    root = _repository_root()
    version = _canonical_version(_index_bytes(root, Path("pyproject.toml")))
    prepared: list[tuple[Path, bytes, bytes, str, bytes, bytes]] = []
    for relative in _MANIFESTS:
        staged = _index_bytes(root, relative)
        working_path = root / relative
        try:
            working = working_path.read_bytes()
        except OSError as exc:
            raise SynchronizationError(f"{relative}: cannot read working file: {exc}") from exc
        mode = _index_mode(root, relative)
        prepared.append(
            (
                working_path,
                working,
                _manifest_output(working, relative, version),
                mode,
                staged,
                _manifest_output(staged, relative, version),
            )
        )

    if _temporary_index_active(root) and any(
        staged != replacement
        for _path, _original, _working, _mode, staged, replacement in prepared
    ):
        raise SynchronizationError(
            "partial-path commits cannot synchronize a version change; "
            "stage pyproject.toml and commit normally"
        )

    changed_working: list[tuple[Path, bytes]] = []
    try:
        for working_path, original, replacement, _mode, _staged, _output in prepared:
            if replacement != original:
                _atomic_replace(working_path, replacement)
                changed_working.append((working_path, original))
        index_records: list[bytes] = []
        for working_path, _original, _replacement, mode, _staged, output in prepared:
            object_id = _git(root, "hash-object", "-w", "--stdin", input_bytes=output)
            relative = working_path.relative_to(root).as_posix()
            index_records.append(
                mode.encode()
                + b" "
                + object_id.strip()
                + b"\t"
                + relative.encode()
                + b"\0"
            )
        _git(root, "update-index", "-z", "--index-info", input_bytes=b"".join(index_records))
    except BaseException as cause:
        rollback_errors: list[str] = []
        for working_path, original in reversed(changed_working):
            try:
                _atomic_replace(working_path, original)
            except OSError as rollback_error:
                rollback_errors.append(f"{working_path}: {rollback_error}")
        if rollback_errors:
            raise SynchronizationError(
                "synchronization failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            ) from cause
        raise
    return version


def main() -> int:
    try:
        version = synchronize()
    except (OSError, SynchronizationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Synchronized release version {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
