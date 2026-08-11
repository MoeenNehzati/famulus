"""Git snapshot and node-local commit-readiness checks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from typing import Iterable, Mapping, Sequence

from .atomic_files import (
    AtomicWriteError,
    atomic_replace_bytes,
    read_regular_file_bytes,
)
from .repository_paths import RepositoryPathError, repository_relative_posix


_REGULAR_FILE_MODES = {"100644", "100755"}
_MATERIALIZED_FILE_MODES = {*_REGULAR_FILE_MODES, "120000"}
_FULL_GIT_OBJECT_ID = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
BLUEPRINT_V4_MECHANICAL_REF = "refs/famulus/blueprint-v4-mechanical"
BLUEPRINT_V4_SOURCE_OVERLAY_REF = "refs/famulus/blueprint-v4-source-overlay"


class GitMaterializationError(RuntimeError):
    """Raised when one exact commit cannot be materialized safely."""


@dataclass(frozen=True)
class GitSnapshot:
    repo_root: Path
    commit: str


@dataclass(frozen=True)
class CommitReadiness:
    stamp_worthy: bool
    source: dict[str, object] | None
    reasons: tuple[str, ...]


def run_git(
    repo_root: Path,
    *args: str,
    check: bool = True,
    input_bytes: bytes | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run Git at ``repo_root`` without ambient routing, config, or hooks.

    ``timeout`` is forwarded to ``subprocess.run`` so latency-sensitive
    callers can bound even local Git inspection without duplicating this
    environment-isolation boundary.
    """

    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("GIT_"):
            environment.pop(name, None)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    command = [
        "git",
        "-c",
        "core.hooksPath=",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "core.fsmonitor=false",
        "-C",
        os.fspath(Path(repo_root).resolve()),
        *args,
    ]
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        input=input_bytes,
        env=environment,
        timeout=timeout,
    )


def _exact_git_commit(repo_root: Path, commit: str) -> str:
    if not isinstance(commit, str) or _FULL_GIT_OBJECT_ID.fullmatch(commit) is None:
        raise GitMaterializationError("Git operation requires a full commit ID")
    verified = run_git(
        repo_root,
        "rev-parse",
        "--verify",
        f"{commit}^{{commit}}",
        check=False,
    )
    if verified.returncode != 0:
        raise GitMaterializationError("Git commit is unavailable")
    try:
        resolved = verified.stdout.decode("ascii").strip()
    except UnicodeError as exc:
        raise GitMaterializationError("Git commit ID is invalid") from exc
    if resolved.lower() != commit.lower():
        raise GitMaterializationError("Git commit ID did not resolve exactly")
    return resolved


def blueprint_v4_mechanical_commit(repo_root: Path) -> str:
    """Return the immutable candidate mechanical baseline from its reserved ref."""

    root = Path(repo_root).resolve()
    result = run_git(
        root,
        "rev-parse",
        "--verify",
        f"{BLUEPRINT_V4_MECHANICAL_REF}^{{commit}}",
        check=False,
    )
    if result.returncode != 0:
        raise GitMaterializationError("blueprint v4 mechanical baseline is unavailable")
    try:
        commit = result.stdout.decode("ascii").strip()
    except UnicodeError as exc:
        raise GitMaterializationError(
            "blueprint v4 mechanical baseline is invalid"
        ) from exc
    return _exact_git_commit(root, commit)


def pin_blueprint_v4_mechanical_commit(repo_root: Path, commit: str) -> str:
    """Create the reserved candidate baseline ref once, without replacement."""

    root = Path(repo_root).resolve()
    resolved = _exact_git_commit(root, commit)
    existing = run_git(
        root,
        "show-ref",
        "--verify",
        "--quiet",
        BLUEPRINT_V4_MECHANICAL_REF,
        check=False,
    )
    if existing.returncode == 0:
        raise GitMaterializationError("blueprint v4 mechanical baseline is already pinned")
    if existing.returncode not in {0, 1}:
        raise GitMaterializationError("cannot inspect blueprint v4 mechanical baseline")
    created = run_git(
        root,
        "update-ref",
        "--create-reflog",
        BLUEPRINT_V4_MECHANICAL_REF,
        resolved,
        "",
        check=False,
    )
    if created.returncode != 0:
        raise GitMaterializationError("cannot pin blueprint v4 mechanical baseline")
    pinned = blueprint_v4_mechanical_commit(root)
    if pinned != resolved:
        raise GitMaterializationError("blueprint v4 mechanical baseline changed")
    return pinned


def blueprint_v4_source_overlay_commit(repo_root: Path) -> str:
    """Return the immutable authorized preconversion overlay commit."""

    root = Path(repo_root).resolve()
    result = run_git(
        root,
        "rev-parse",
        "--verify",
        f"{BLUEPRINT_V4_SOURCE_OVERLAY_REF}^{{commit}}",
        check=False,
    )
    if result.returncode != 0:
        raise GitMaterializationError("blueprint v4 source overlay is unavailable")
    return _exact_git_commit(root, result.stdout.decode("ascii").strip())


def pin_blueprint_v4_source_overlay_commit(repo_root: Path, commit: str) -> str:
    """Create the reserved source-overlay ref once, without replacement."""

    root = Path(repo_root).resolve()
    resolved = _exact_git_commit(root, commit)
    created = run_git(
        root,
        "update-ref",
        BLUEPRINT_V4_SOURCE_OVERLAY_REF,
        resolved,
        "",
        check=False,
    )
    if created.returncode != 0:
        raise GitMaterializationError("cannot pin blueprint v4 source overlay")
    pinned = blueprint_v4_source_overlay_commit(root)
    if pinned != resolved:
        raise GitMaterializationError("blueprint v4 source overlay changed")
    return pinned


def _tree_relative_path(raw: str) -> Path:
    if not raw or "\\" in raw or "\0" in raw:
        raise GitMaterializationError("Git tree contains an unsafe path")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} or ":" in part for part in relative.parts
    ):
        raise GitMaterializationError("Git tree contains an unsafe path")
    return Path(*relative.parts)


def _tree_symlink_is_confined(relative: Path, target: str) -> bool:
    if not target or "\\" in target or "\0" in target:
        return False
    link = PurePosixPath(target)
    if link.is_absolute() or any(":" in part for part in link.parts):
        return False
    resolved = list(relative.parent.parts)
    for part in link.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved:
                return False
            resolved.pop()
        else:
            resolved.append(part)
    return True


def _materialization_parent(root: Path, relative: Path) -> Path:
    current = root
    for component in relative.parts[:-1]:
        current = current / component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            metadata = current.lstat()
        except OSError as exc:
            raise GitMaterializationError(
                f"cannot create Git tree parent: {relative.as_posix()}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise GitMaterializationError(
                f"Git tree parent is unsafe: {relative.as_posix()}"
            )
    return current


def materialize_git_commit(
    repo_root: Path,
    commit: str,
    destination: Path,
    *,
    allow_non_atomic: bool = False,
) -> tuple[Path, ...]:
    """Materialize one full commit into an existing empty private directory.

    Paths, modes, and object IDs come from the exact commit tree; bytes come
    directly from its blobs, so export attributes and worktree filters cannot
    transform the result.
    """

    root = Path(repo_root).resolve()
    target_root = Path(destination)
    if target_root.is_symlink():
        raise GitMaterializationError("Git materialization destination is unsafe")
    try:
        target_root = target_root.resolve(strict=True)
        target_metadata = target_root.lstat()
    except OSError as exc:
        raise GitMaterializationError(
            "Git materialization destination must be an existing directory"
        ) from exc
    if (
        not stat.S_ISDIR(target_metadata.st_mode)
        or any(target_root.iterdir())
        or (
            os.name == "posix"
            and (
                target_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(target_metadata.st_mode) & 0o022
            )
        )
    ):
        raise GitMaterializationError(
            "Git materialization destination must be an empty private directory"
        )
    repository = run_git(root, "rev-parse", "--show-toplevel", check=False)
    if repository.returncode != 0:
        raise GitMaterializationError("Git repository is unavailable")
    try:
        discovered_root = Path(repository.stdout.decode("utf-8").strip()).resolve()
    except UnicodeError as exc:
        raise GitMaterializationError("Git repository root is invalid") from exc
    if discovered_root != root:
        raise GitMaterializationError("Git materialization requires the repository root")
    resolved_commit = _exact_git_commit(root, commit)
    tree = run_git(
        root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        resolved_commit,
        check=False,
    )
    if tree.returncode != 0:
        raise GitMaterializationError("Git commit tree is unavailable")

    records: list[tuple[Path, str, bytes | str, int]] = []
    seen: set[Path] = set()
    for record in tree.stdout.rstrip(b"\0").split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise GitMaterializationError("Git commit tree entry is invalid")
        try:
            mode, object_type, object_id = (
                field.decode("ascii") for field in fields
            )
        except UnicodeError as exc:
            raise GitMaterializationError("Git commit tree entry is invalid") from exc
        if object_type != "blob" or mode not in _MATERIALIZED_FILE_MODES:
            raise GitMaterializationError("Git commit tree entry type is unsupported")
        relative = _tree_relative_path(os.fsdecode(raw_path))
        if relative in seen:
            raise GitMaterializationError(
                f"Git tree contains duplicate path: {relative.as_posix()}"
            )
        seen.add(relative)
        blob = run_git(root, "cat-file", "blob", object_id, check=False)
        if blob.returncode != 0:
            raise GitMaterializationError(
                f"Git tree blob is unavailable: {relative.as_posix()}"
            )
        if mode == "120000":
            target = os.fsdecode(blob.stdout)
            if not _tree_symlink_is_confined(relative, target):
                raise GitMaterializationError(
                    f"Git tree symlink escapes destination: {relative.as_posix()}"
                )
            records.append((relative, "symlink", target, 0))
        else:
            file_mode = 0o755 if mode == "100755" else 0o644
            records.append((relative, "file", blob.stdout, file_mode))

    materialized: list[Path] = []
    try:
        for relative, kind, value, mode in records:
            parent = _materialization_parent(target_root, relative)
            target = parent / relative.name
            if target.exists() or target.is_symlink():
                raise GitMaterializationError(
                    f"Git tree path collides: {relative.as_posix()}"
                )
            if kind == "file":
                if not isinstance(value, bytes):
                    raise GitMaterializationError("Git tree file payload is invalid")
                atomic_replace_bytes(
                    target,
                    value,
                    allowed_root=target_root,
                    mode=mode,
                    allow_non_atomic=allow_non_atomic,
                )
            else:
                if not isinstance(value, str):
                    raise GitMaterializationError("Git tree symlink is invalid")
                os.symlink(value, target)
            materialized.append(relative)
    except (AtomicWriteError, OSError) as exc:
        raise GitMaterializationError("Git commit materialization failed") from exc
    return tuple(sorted(materialized, key=lambda path: path.as_posix()))


def _git(
    repo_root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return run_git(repo_root, *args, check=check)


def _git_ignored_entries(repo_root: Path) -> tuple[tuple[Path, bool], ...]:
    try:
        result = run_git(
            repo_root,
            "status",
            "--porcelain=v1",
            "-z",
            "--ignored=matching",
            "--untracked-files=all",
            check=False,
        )
    except OSError:
        return ()
    if result.returncode != 0:
        return ()
    entries: list[tuple[Path, bool]] = []
    for record in result.stdout.rstrip(b"\0").split(b"\0"):
        if not record.startswith(b"!! "):
            continue
        raw_path = record[3:]
        is_directory = raw_path.endswith(b"/")
        entries.append(
            (Path(os.fsdecode(raw_path[:-1] if is_directory else raw_path)), is_directory)
        )
    return tuple(sorted(entries, key=lambda entry: entry[0].as_posix()))


def git_ignored_paths(repo_root: Path) -> tuple[Path, ...]:
    """Return all repository-relative paths ignored by standard Git rules."""

    return tuple(path for path, _is_directory in _git_ignored_entries(repo_root))


def git_ignored_directories(repo_root: Path) -> tuple[Path, ...]:
    """Return repository-relative directories ignored by standard Git rules."""

    return tuple(
        path for path, is_directory in _git_ignored_entries(repo_root) if is_directory
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


def git_file_provenance_batch(
    repo_root: Path,
    paths: Iterable[Path],
) -> dict[Path, str]:
    """Classify repository files as tracked, ignored, or untracked in one batch."""

    root = Path(repo_root).resolve()
    normalized: dict[Path, str] = {}
    for path in paths:
        candidate = Path(path)
        try:
            relative_path = repository_relative_posix(candidate, root)
        except RepositoryPathError:
            raise ValueError(f"{path}: input is outside repository {root}")
        raw_path = candidate if candidate.is_absolute() else root / candidate
        normalized[Path(os.path.abspath(raw_path))] = relative_path
    ordered = tuple(
        sorted(normalized.items(), key=lambda item: item[1])
    )
    if not ordered:
        return {}

    try:
        tracked_result = run_git(
            root,
            "ls-files",
            "--cached",
            "-z",
            "--",
            *(_literal_pathspec(relative) for _path, relative in ordered),
            check=False,
        )
        if tracked_result.returncode != 0:
            detail = tracked_result.stderr.decode(
                "utf-8",
                errors="replace",
            ).strip()
            raise ValueError(
                f"{root}: Git tracked-file query failed: {detail}"
            )
        tracked_paths = {
            os.fsdecode(record)
            for record in tracked_result.stdout.split(b"\0")
            if record
        }
        remaining = tuple(
            relative
            for _path, relative in ordered
            if relative not in tracked_paths
        )
        ignored_paths: set[str] = set()
        if remaining:
            ignored_result = run_git(
                root,
                "check-ignore",
                "-z",
                "--stdin",
                check=False,
                input_bytes=b"".join(
                    os.fsencode(f"./{relative}") + b"\0" for relative in remaining
                ),
            )
            if ignored_result.returncode not in {0, 1}:
                detail = ignored_result.stderr.decode(
                    "utf-8",
                    errors="replace",
                ).strip()
                raise ValueError(
                    f"{root}: Git ignore query failed: {detail}"
                )
            ignored_paths = {
                decoded[2:] if decoded.startswith("./") else decoded
                for record in ignored_result.stdout.split(b"\0")
                if record
                for decoded in (os.fsdecode(record),)
            }
    except OSError as exc:
        raise ValueError(f"{root}: Git provenance is unavailable") from exc

    return {
        path: (
            "tracked"
            if relative in tracked_paths
            else "ignored"
            if relative in ignored_paths
            else "untracked"
        )
        for path, relative in ordered
    }


def git_file_provenance(repo_root: Path, path: Path) -> str:
    """Classify one repository file as tracked, ignored, or untracked."""

    return next(iter(git_file_provenance_batch(repo_root, (path,)).values()))


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


def _use_native_confined_read() -> bool:
    """Return whether tracked inputs use the shared native handle reader."""

    return os.name == "nt"


def _read_descriptor_safe_regular_file(
    repo_root: Path,
    relative_path: str,
    *,
    allow_non_atomic: bool = False,
) -> tuple[bytes | None, str | None, str | None]:
    """Read a regular input through no-follow descriptors, or fail closed."""

    if _use_native_confined_read():
        try:
            data = read_regular_file_bytes(
                repo_root / relative_path,
                allowed_root=repo_root,
                allow_non_atomic=allow_non_atomic,
            )
        except (AtomicWriteError, FileNotFoundError, OSError):
            return None, None, "unsafe-worktree-input"
        # Native Git worktrees do not expose the executable bit as a reliable
        # filesystem mode. The index-mode comparison below remains authoritative.
        return data, None, None

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
    *,
    allow_non_atomic: bool = False,
) -> CommitReadiness:
    """Determine whether exactly the supplied local inputs match captured HEAD."""

    if snapshot is None:
        return CommitReadiness(False, None, ("not-a-git-repository",))

    reasons: set[str] = set()
    relative_paths: set[str] = set()
    for path in input_paths:
        try:
            relative_path = repository_relative_posix(path, snapshot.repo_root)
        except RepositoryPathError:
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
            _read_descriptor_safe_regular_file(
                snapshot.repo_root,
                relative_path,
                allow_non_atomic=allow_non_atomic,
            )
        )
        if worktree_reason is not None:
            reasons.add(f"{worktree_reason}:{relative_path}")
            continue
        if worktree_bytes is None:
            reasons.add(f"unsafe-worktree-input:{relative_path}")
            continue
        if worktree_mode is not None and worktree_mode != commit_mode:
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
