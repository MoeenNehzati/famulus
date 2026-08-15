"""Shared symlink/copy helpers used by scaffold, dev_link, and launchers.

Extracted from setup_symlinks.py / setup_tools.py, which each had their own
near-identical copy of make_link (and setup_tools.py additionally had
make_copy). One copy avoids the two drifting apart.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
import stat

REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if not __package__ and str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from officina.common.famulus_paths import resolve_famulus_paths
from officina.common.atomic_files import (
    atomic_publish_bytes,
    atomic_publish_symlink,
    normalize_publication_mode,
    read_regular_file_bytes_bounded,
)

_MAX_INSTALL_FILE_BYTES = 1024 * 1024

if __package__:
    from ._state_record import (
        InstallerMutationError,
        MutationRecorder,
        snapshot_path_state,
    )
else:
    from _state_record import (
        InstallerMutationError,
        MutationRecorder,
        snapshot_path_state,
    )


def log(msg: str = "") -> None:
    """Print one installer status line immediately.

    Intent
    ------
    Emit the caller-supplied status text and flush the stream before returning.

    Rationale
    ---------
    Immediate flushing preserves useful progress ordering during installer failures.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    print(msg, flush=True)


def default_bin_dir(*, home: Path) -> Path:
    """Platform-correct default launcher bin dir.

    Intent
    ------
    Platform-correct default launcher bin dir. The boundary coordinates home through resolve_famulus_paths, Path, sys, and home with one closed state transition.

    Rationale
    ---------
    Because Platform-correct default launcher bin dir. Keep resolve_famulus_paths, Path, sys, and home inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - set host_path_policy = received_context
    - return host_path_policy

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.famulus_paths.resolve_famulus_paths:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Platform-correct default launcher bin dir."
    """
    return resolve_famulus_paths(platform=sys.platform, home=home).user_bin


def make_link(
    src: Path,
    dst: Path,
    dry_run: bool,
    *,
    recorder: MutationRecorder | None,
    operation_key: str,
) -> None:
    """Publish one exact owned symlink through the durable recorder.

    Intent
    ------
    Publish one exact owned symlink through the durable recorder. The boundary coordinates src, dst, dry_run, recorder, and operation_key through InstallerMutationError, lstat, S_ISLNK, S_ISREG, S_ISDIR, and S_IFMT with 3 guarded checks, 1 cleanup or failure regions, and 3 typed refusals.

    Rationale
    ---------
    Because Publish one exact owned symlink through the durable recorder. Keep InstallerMutationError, lstat, S_ISLNK, S_ISREG, S_ISDIR, and S_IFMT inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .log:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Publish one exact owned symlink through the durable recorder."
    officina.common.atomic_files.atomic_publish_symlink:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Publish one exact owned symlink through the durable recorder."
    """

    if not dry_run and recorder is None:
        raise InstallerMutationError(
            "live installation requires a durable mutation recorder"
        )
    try:
        source = src.lstat()
    except FileNotFoundError as exc:
        raise InstallerMutationError(f"required link source is absent: {src}") from exc
    if stat.S_ISLNK(source.st_mode) or not (
        stat.S_ISREG(source.st_mode) or stat.S_ISDIR(source.st_mode)
    ):
        raise InstallerMutationError(f"required link source is ineligible: {src}")
    source_identity = (source.st_dev, source.st_ino, stat.S_IFMT(source.st_mode))
    if dry_run:
        log(f"  Would link: {dst} -> {src}")
        return
    assert recorder is not None
    intended = {"kind": "symlink", "target": str(src)}

    def publish(pending) -> None:
        """Revalidate the link source identity immediately before publication.

        Intent
        ------
        Reopen the source without following links, compare its type and identity
        with the pre-recording snapshot, then invoke exact symlink publication.

        Rationale
        ---------
        A source replaced after durable intent must not become the target of the
        recorded link mutation.

        Pseudocode
        ----------
        - set revalidated_source_identity = local_decisions
        - return

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        officina.common.atomic_files.atomic_publish_symlink:
          why:
            computes: "Publishes the lexical link only after source revalidation succeeds."
        """
        try:
            current = src.lstat()
        except FileNotFoundError as exc:
            raise InstallerMutationError("required link source changed") from exc
        current_identity = (
            current.st_dev,
            current.st_ino,
            stat.S_IFMT(current.st_mode),
        )
        if current_identity != source_identity or stat.S_ISLNK(current.st_mode):
            raise InstallerMutationError("required link source changed")
        atomic_publish_symlink(
            dst,
            str(src),
            allowed_root=dst.parent,
            build_id=pending.mutation_id,
            expected_before=pending.expected_before,
        )

    recorder.mutate(
        operation_key=operation_key,
        kind="symlink_replace",
        resource_kind="filesystem",
        resource_id=str(dst.absolute()),
        intended_after=intended,
        ownership_delta={
            "action": "upsert",
            "entry": {"kind": "symlink", "path": str(dst), "target": str(src)},
        },
        observe=lambda: snapshot_path_state(dst),
        apply=publish,
    )
    log(f"  Linked: {dst} -> {src}")


def make_copy(
    src: Path,
    dst: Path,
    dry_run: bool,
    *,
    recorder: MutationRecorder | None,
    operation_key: str,
) -> None:
    """Copy src to dst instead of symlinking.

    Intent
    ------
    Copy src to dst instead of symlinking. The boundary coordinates src, dst, dry_run, recorder, and operation_key through InstallerMutationError, log, snapshot_path_state, get, read_regular_file_bytes_bounded, and isinstance with 6 guarded checks, and 4 typed refusals.

    Rationale
    ---------
    Because Copy src to dst instead of symlinking. Keep InstallerMutationError, log, snapshot_path_state, get, read_regular_file_bytes_bounded, and isinstance inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .log:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Copy src to dst instead of symlinking."
    officina.common.atomic_files.atomic_publish_bytes:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Copy src to dst instead of symlinking."

    InstantiationsFromRepo
    ----------------------
    officina.common.atomic_files.read_regular_file_bytes_bounded:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Copy src to dst instead of symlinking."
    """
    if not dry_run and recorder is None:
        raise InstallerMutationError(
            "live installation requires a durable mutation recorder"
        )
    if dry_run:
        log(f"  Would copy: {src} -> {dst}")
        return
    assert recorder is not None
    source_before = snapshot_path_state(src)
    if source_before.get("kind") != "file":
        raise InstallerMutationError(f"required copy source is not regular: {src}")
    data = read_regular_file_bytes_bounded(
        src,
        allowed_root=src.parent,
        maximum_bytes=_MAX_INSTALL_FILE_BYTES,
    )
    if snapshot_path_state(src) != source_before:
        raise InstallerMutationError(f"required copy source changed: {src}")
    mode = source_before.get("mode")
    if isinstance(mode, bool) or not isinstance(mode, int):
        raise InstallerMutationError(f"required copy source mode is invalid: {src}")
    mode = normalize_publication_mode(mode & 0o777)
    destination_before = snapshot_path_state(dst)
    if destination_before.get("kind") == "file":
        log(f"  SKIP (exists, keeping machine-local state): {dst}")
        return
    intended = {
        "kind": "file",
        "mode": mode,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    first_observation = True

    def observe() -> dict[str, object]:
        """Observe the copy destination and reject a pre-recording state change.

        Intent
        ------
        Snapshot the destination and, on the first recorder observation, prove it
        still matches the state checked before constructing durable intent.

        Rationale
        ---------
        Recording a copy against a stale predecessor could overwrite an
        unrecorded destination change.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        nonlocal first_observation
        actual = snapshot_path_state(dst)
        if (
            first_observation
            and recorder.journal.pending_mutation is None
            and actual != destination_before
        ):
            raise InstallerMutationError("copy destination changed before recording")
        first_observation = False
        return actual

    recorder.mutate(
        operation_key=operation_key,
        kind="file_create_or_legacy_replace",
        resource_kind="filesystem",
        resource_id=str(dst.absolute()),
        intended_after=intended,
        ownership_delta={
            "action": "upsert",
            "entry": {"kind": "file", "path": str(dst)},
        },
        observe=observe,
        apply=lambda pending: atomic_publish_bytes(
            dst,
            data,
            allowed_root=dst.parent,
            mode=mode,
            build_id=pending.mutation_id,
            expected_before=pending.expected_before,
        ),
    )
    log(f"  Copied: {src} -> {dst}")
