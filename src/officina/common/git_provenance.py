"""Git snapshot and node-local commit-readiness checks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import subprocess
from typing import Mapping, Sequence


_REGULAR_FILE_MODES = {"100644", "100755"}


@dataclass(frozen=True)
class GitSnapshot:
    repo_root: Path
    commit: str


@dataclass(frozen=True)
class CommitReadiness:
    stamp_worthy: bool
    source: dict[str, object] | None
    reasons: tuple[str, ...]


def _git(
    repo_root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", os.fspath(repo_root), *args],
        check=check,
        capture_output=True,
    )


def _output_text(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stdout.decode("utf-8").strip()


def capture_git_snapshot(path: Path) -> GitSnapshot | None:
    """Capture the repository root and HEAD commit containing ``path``."""

    search_path = path if path.is_dir() else path.parent
    try:
        root = _git(search_path, "rev-parse", "--show-toplevel", check=False)
        if root.returncode != 0:
            return None
        repo_root = Path(_output_text(root)).resolve()
        head = _git(repo_root, "rev-parse", "HEAD", check=False)
    except OSError:
        return None
    if head.returncode != 0:
        return None
    return GitSnapshot(repo_root=repo_root, commit=_output_text(head))


def snapshot_head_matches(snapshot: GitSnapshot | None) -> bool:
    """Return whether the repository still points at the captured HEAD."""

    if snapshot is None:
        return False
    try:
        current = _git(snapshot.repo_root, "rev-parse", "HEAD", check=False)
    except OSError:
        return False
    return current.returncode == 0 and _output_text(current) == snapshot.commit


def _repository_relative_path(path: Path, repo_root: Path) -> str | None:
    raw_path = path if path.is_absolute() else repo_root / path
    normalized = Path(os.path.abspath(raw_path))
    try:
        return normalized.relative_to(repo_root).as_posix()
    except ValueError:
        return None


def _tree_entry(
    snapshot: GitSnapshot, relative_path: str
) -> tuple[str, str] | None:
    result = _git(
        snapshot.repo_root,
        "ls-tree",
        "-z",
        snapshot.commit,
        "--",
        _literal_pathspec(relative_path),
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    records = result.stdout.rstrip(b"\0").split(b"\0")
    if len(records) != 1:
        return None
    metadata, separator, returned_path = records[0].partition(b"\t")
    fields = metadata.split()
    if (
        not separator
        or returned_path != os.fsencode(relative_path)
        or len(fields) != 3
        or fields[1] != b"blob"
    ):
        return None
    return fields[0].decode("ascii"), fields[2].decode("ascii")


def _literal_pathspec(relative_path: str) -> str:
    return f":(literal){relative_path}"


def _index_entries(
    repo_root: Path, relative_path: str
) -> tuple[tuple[str, str, str], ...] | None:
    result = _git(
        repo_root,
        "ls-files",
        "--stage",
        "-z",
        "--",
        _literal_pathspec(relative_path),
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    records = result.stdout.rstrip(b"\0").split(b"\0")
    entries: list[tuple[str, str, str]] = []
    for record in records:
        metadata, separator, returned_path = record.partition(b"\t")
        fields = metadata.split()
        if (
            not separator
            or returned_path != os.fsencode(relative_path)
            or len(fields) != 3
        ):
            return None
        mode, object_id, stage = (field.decode("ascii") for field in fields)
        entries.append((mode, object_id, stage))
    return tuple(entries)


def _descriptor_safe_open_supported() -> bool:
    return (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
    )


def _read_descriptor_safe_regular_file(
    repo_root: Path, relative_path: str
) -> tuple[bytes | None, str | None, str | None]:
    """Read a regular input through no-follow descriptors, or fail closed."""

    if not _descriptor_safe_open_supported():
        return None, None, "descriptor-safe-open-unavailable"

    directory_fd = -1
    final_fd = -1
    file_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
    )
    directory_flags = file_flags | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(repo_root, directory_flags)
        if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
            return None, None, "unsafe-worktree-input"
        parts = Path(relative_path).parts
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                os.close(next_fd)
                return None, None, "unsafe-worktree-input"
            os.close(directory_fd)
            directory_fd = next_fd
        final_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        metadata = os.fstat(final_fd)
        if not stat.S_ISREG(metadata.st_mode):
            return None, None, "unsafe-worktree-input"
        chunks: list[bytes] = []
        while chunk := os.read(final_fd, 1024 * 1024):
            chunks.append(chunk)
        worktree_mode = "100755" if metadata.st_mode & stat.S_IXUSR else "100644"
        return b"".join(chunks), worktree_mode, None
    except OSError:
        return None, None, "unsafe-worktree-input"
    finally:
        if final_fd >= 0:
            os.close(final_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


def _commit_blob(repo_root: Path, object_id: str) -> bytes | None:
    result = _git(repo_root, "cat-file", "blob", object_id, check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def _readiness(reasons: set[str], source: dict[str, object]) -> CommitReadiness:
    ordered_reasons = tuple(sorted(reasons))
    return CommitReadiness(
        stamp_worthy=not ordered_reasons,
        source=source if not ordered_reasons else None,
        reasons=ordered_reasons,
    )


def check_commit_readiness(
    snapshot: GitSnapshot | None,
    input_paths: Sequence[Path],
    expected_hashes: Mapping[str, str],
) -> CommitReadiness:
    """Determine whether exactly the supplied local inputs match captured HEAD."""

    if snapshot is None:
        return CommitReadiness(False, None, ("not-a-git-repository",))

    reasons: set[str] = set()
    relative_paths: set[str] = set()
    for path in input_paths:
        relative_path = _repository_relative_path(path, snapshot.repo_root)
        if relative_path is None:
            reasons.add("input-outside-repository")
        else:
            relative_paths.add(relative_path)
    ordered_paths = sorted(relative_paths)

    for relative_path in ordered_paths:
        try:
            commit_entry = _tree_entry(snapshot, relative_path)
            index_entries = _index_entries(snapshot.repo_root, relative_path)
        except OSError:
            reasons.add(f"git-unavailable:{relative_path}")
            continue
        if commit_entry is None:
            reasons.add(f"not-tracked-at-commit:{relative_path}")
            continue
        commit_mode, commit_object_id = commit_entry
        if commit_mode not in _REGULAR_FILE_MODES:
            reasons.add(f"unsupported-commit-mode:{relative_path}")
            continue
        if not index_entries:
            reasons.add(f"missing-index-entry:{relative_path}")
            continue
        if any(stage != "0" for _mode, _object_id, stage in index_entries):
            reasons.add(f"nonzero-index-stage:{relative_path}")
            continue
        if len(index_entries) != 1:
            reasons.add(f"invalid-index-entry:{relative_path}")
            continue
        index_mode, index_object_id, _stage = index_entries[0]
        if index_mode not in _REGULAR_FILE_MODES:
            reasons.add(f"unsupported-index-mode:{relative_path}")
            continue
        if index_mode != commit_mode:
            reasons.add(f"index-mode-differs-from-commit:{relative_path}")
            continue
        if index_object_id != commit_object_id:
            reasons.add(f"index-differs-from-commit:{relative_path}")
            continue
        try:
            commit_bytes = _commit_blob(snapshot.repo_root, commit_object_id)
        except OSError:
            reasons.add(f"git-unavailable:{relative_path}")
            continue
        if commit_bytes is None:
            reasons.add(f"unreadable-commit-blob:{relative_path}")
            continue
        worktree_bytes, worktree_mode, worktree_reason = (
            _read_descriptor_safe_regular_file(snapshot.repo_root, relative_path)
        )
        if worktree_reason is not None:
            reasons.add(f"{worktree_reason}:{relative_path}")
            continue
        if worktree_bytes is None:
            reasons.add(f"unsafe-worktree-input:{relative_path}")
            continue
        if worktree_mode != commit_mode:
            reasons.add(f"worktree-mode-differs-from-commit:{relative_path}")
            continue
        if worktree_bytes != commit_bytes:
            reasons.add(f"worktree-differs-from-commit:{relative_path}")
            continue
        expected_hash = expected_hashes.get(relative_path)
        working_hash = "sha256:" + hashlib.sha256(worktree_bytes).hexdigest()
        if expected_hash is not None and working_hash != expected_hash:
            reasons.add(f"expected-hash-mismatch:{relative_path}")

    source = {
        "vcs": "git",
        "commit": snapshot.commit,
        "input_paths": ordered_paths,
    }
    return _readiness(reasons, source)
