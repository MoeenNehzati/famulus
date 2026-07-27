"""Shared confined writes and exact Git evidence for migration candidates."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Iterable

from .atomic_files import AtomicWriteError, atomic_replace_bytes
from .git_provenance import run_git


class MigrationCandidateError(ValueError):
    """Raised when candidate materialization cannot remain exact and confined."""


@dataclass(frozen=True)
class CutoverChange:
    status: str
    path: Path
    source_path: Path | None = None


def _require_relative_path(relative: Path, context: str) -> None:
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise MigrationCandidateError(
            f"unsafe {context} path: {relative.as_posix()}"
        )


def _ensure_real_parent(root: Path, relative: Path, *, context: str) -> Path:
    _require_relative_path(relative, context)
    current = root
    for part in relative.parent.parts:
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o755)
        if current.is_symlink() or not current.is_dir():
            raise MigrationCandidateError(
                f"unsafe {context} parent: {relative.as_posix()}"
            )
    return root / relative


def atomic_candidate_write(
    candidate_root: Path,
    relative: Path,
    content: bytes,
    *,
    context: str,
) -> None:
    target = _ensure_real_parent(candidate_root, relative, context=context)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise MigrationCandidateError(
            f"unsafe {context} target: {relative.as_posix()}"
        )
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
    try:
        atomic_replace_bytes(
            target,
            content,
            allowed_root=candidate_root,
            mode=mode,
        )
    except AtomicWriteError as exc:
        raise MigrationCandidateError(
            f"cannot atomically write {context}: "
            f"{relative.as_posix()}: {exc}"
        ) from exc


def candidate_commit(
    candidate_root: Path,
    message: str,
    paths: Iterable[Path],
    *,
    commit_timestamp: str | None = None,
) -> str:
    selected = tuple(sorted(set(paths)))
    if not selected:
        raise MigrationCandidateError(
            "candidate commit requires produced paths"
        )
    expanded: set[Path] = set()
    for path in selected:
        tracked = run_git(
            candidate_root,
            "ls-files",
            "-z",
            "--",
            path.as_posix(),
            check=False,
        )
        if tracked.returncode != 0:
            raise MigrationCandidateError(
                f"cannot resolve produced path: {path.as_posix()}"
            )
        expanded.update(
            Path(os.fsdecode(raw))
            for raw in tracked.stdout.rstrip(b"\0").split(b"\0")
            if raw
        )
        current = candidate_root / path
        if current.is_dir() and not current.is_symlink():
            expanded.update(
                entry.relative_to(candidate_root)
                for entry in current.rglob("*")
                if entry.is_file() or entry.is_symlink()
            )
        elif current.exists() or current.is_symlink():
            expanded.add(path)
    run_git(
        candidate_root,
        "update-index",
        "--add",
        "--remove",
        "-z",
        "--stdin",
        input_bytes=b"\0".join(
            os.fsencode(path.as_posix()) for path in sorted(expanded)
        )
        + b"\0",
    )
    if commit_timestamp is None:
        run_git(candidate_root, "commit", "-qm", message)
        return (
            run_git(candidate_root, "rev-parse", "HEAD")
            .stdout.decode("utf-8")
            .strip()
        )
    if re.fullmatch(r"[0-9]+ [+-][0-9]{4}", commit_timestamp) is None:
        raise MigrationCandidateError(
            "deterministic candidate timestamp is invalid"
        )
    name = (
        run_git(candidate_root, "config", "--get", "user.name")
        .stdout.decode("utf-8")
        .strip()
    )
    email = (
        run_git(candidate_root, "config", "--get", "user.email")
        .stdout.decode("utf-8")
        .strip()
    )
    if (
        not name
        or not email
        or any(character in name for character in "\r\n<>")
        or any(character in email for character in "\r\n<>")
    ):
        raise MigrationCandidateError(
            "deterministic candidate identity is invalid"
        )
    tree = (
        run_git(candidate_root, "write-tree")
        .stdout.decode("ascii")
        .strip()
    )
    parent = (
        run_git(candidate_root, "rev-parse", "HEAD")
        .stdout.decode("ascii")
        .strip()
    )
    identity = f"{name} <{email}> {commit_timestamp}"
    commit_bytes = (
        f"tree {tree}\n"
        f"parent {parent}\n"
        f"author {identity}\n"
        f"committer {identity}\n"
        "\n"
        f"{message}\n"
    ).encode("utf-8")
    commit = (
        run_git(
            candidate_root,
            "hash-object",
            "-t",
            "commit",
            "-w",
            "--stdin",
            input_bytes=commit_bytes,
        )
        .stdout.decode("ascii")
        .strip()
    )
    run_git(candidate_root, "update-ref", "HEAD", commit, parent)
    return commit


def deterministic_candidate_commit(
    candidate_root: Path,
    message: str,
    paths: Iterable[Path],
    *,
    commit_timestamp: str,
) -> str:
    return candidate_commit(
        candidate_root,
        message,
        paths,
        commit_timestamp=commit_timestamp,
    )


def candidate_cutover_manifest(
    candidate_root: Path,
    source_commit: str,
    candidate_commit_id: str,
) -> tuple[CutoverChange, ...]:
    result = run_git(
        candidate_root,
        "diff",
        "--name-status",
        "--no-renames",
        "-z",
        source_commit,
        candidate_commit_id,
    )
    fields = result.stdout.rstrip(b"\0").split(b"\0") if result.stdout else []
    changes: list[CutoverChange] = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii")
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                raise MigrationCandidateError(
                    "invalid Git cutover manifest"
                )
            source = Path(os.fsdecode(fields[index]))
            path = Path(os.fsdecode(fields[index + 1]))
            index += 2
            changes.append(CutoverChange(status, path, source))
        else:
            if index >= len(fields):
                raise MigrationCandidateError(
                    "invalid Git cutover manifest"
                )
            changes.append(
                CutoverChange(
                    status,
                    Path(os.fsdecode(fields[index])),
                )
            )
            index += 1
    return tuple(changes)
