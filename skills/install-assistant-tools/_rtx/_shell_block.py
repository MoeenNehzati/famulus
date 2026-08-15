"""Managed shell-rc block writer shared by scaffold, launchers, and dev_link.

Each of those three subcommands owns exactly one variable in the managed
block (PATH, ASSISTANT_DEFAULT, AI respectively) but they share one physical
block in the rc file. ensure_rc_vars() merges by variable name so re-running
any one subcommand updates only its own line, leaving the others intact.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from officina.common.atomic_files import (
    atomic_publish_bytes,
    normalize_publication_mode,
    read_regular_file_bytes_bounded,
)

_MAX_RC_BYTES = 1024 * 1024

if __package__:
    from ._state_record import (
        InstallerMutationError,
        MutationRecorder,
        StateRecordError,
        snapshot_path_state,
    )
else:
    from _state_record import (
        InstallerMutationError,
        MutationRecorder,
        StateRecordError,
        snapshot_path_state,
    )

BLOCK_BEGIN = "# >>> assistant-tools >>>"
BLOCK_END = "# <<< assistant-tools <<<"

_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VAR_LINE_RE = re.compile(r"^export ([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


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


def _parse_block_vars(block_lines: list[str]) -> dict[str, str]:
    """Map var name -> full export line, in encounter order (dict preserves it).

    Intent
    ------
    Map var name -> full export line, in encounter order (dict preserves it). The boundary coordinates block_lines, parsed, line, match, and token through fullmatch, any, InstallerMutationError, group, list, and str with 1 guarded checks, 1 bounded iterations, and 1 typed refusals.

    Rationale
    ---------
    Because Map var name -> full export line, in encounter order (dict preserves it). Keep fullmatch, any, InstallerMutationError, group, list, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    parsed: dict[str, str] = {}
    for line in block_lines:
        match = _VAR_LINE_RE.fullmatch(line)
        if match is None or any(
            token in line for token in ("\r", "\n", "\x00", BLOCK_BEGIN, BLOCK_END)
        ):
            raise InstallerMutationError("managed rc block contains an invalid export")
        parsed[match.group(1)] = line
    return parsed


def _validate_updates(updates: dict[str, str]) -> None:
    """Require one canonical export line whose name matches each update key.

    Intent
    ------
    Require one canonical export line whose name matches each update key. The boundary coordinates updates, key, line, token, and match through items, isinstance, fullmatch, InstallerMutationError, any, and group with 3 guarded checks, 1 bounded iterations, and 3 typed refusals.

    Rationale
    ---------
    Because Require one canonical export line whose name matches each update key. Keep items, isinstance, fullmatch, InstallerMutationError, any, and group inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    for key, line in updates.items():
        if not isinstance(key, str) or _VAR_NAME_RE.fullmatch(key) is None:
            raise InstallerMutationError("rc update has an invalid variable name")
        if not isinstance(line, str) or any(
            token in line for token in ("\r", "\n", "\x00", BLOCK_BEGIN, BLOCK_END)
        ):
            raise InstallerMutationError("rc update must be exactly one export line")
        match = _VAR_LINE_RE.fullmatch(line)
        if match is None or match.group(1) != key:
            raise InstallerMutationError("rc update export name does not match its key")


def ensure_rc_vars(
    rc_file: Path,
    updates: dict[str, str],
    dry_run: bool,
    *,
    recorder: MutationRecorder | None,
    operation_key: str,
    label: str = "user",
) -> None:
    """Merge exact export assignments into the single managed shell block.

    Intent
    ------
    Preserve unmanaged text and unrelated managed variables while replacing each
    requested key with exactly one validated ``export NAME=...`` line.

    Rationale
    ---------
    A bounded UTF-8 read, closed marker grammar, durable intent, and exact atomic
    publication prevent duplicate blocks or partially recorded rc changes.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._parse_block_vars:
      why:
        computes: "Parses existing and rendered managed-block assignments by variable name."
    ._validate_updates:
      why:
        computes: "Rejects malformed, multiline, injected, or mismatched update lines before observation."
    .log:
      why:
        computes: "Reports the exact dry-run update without writing the rc file."
    officina.common.atomic_files.atomic_publish_bytes:
      why:
        computes: "Publishes the complete rendered rc bytes from deterministic build authority."

    InstantiationsFromRepo
    ----------------------
    ._parse_block_vars:
      why:
        constructs: "Builds the exact ordered variable mapping used to render one managed block."
    officina.common.atomic_files.normalize_publication_mode:
      why:
        constructs: "Builds the portable intended mode after special bits are removed."
    officina.common.atomic_files.read_regular_file_bytes_bounded:
      why:
        constructs: "Builds the bounded UTF-8 input snapshot from an eligible regular rc file."
    """
    if not dry_run and recorder is None:
        raise InstallerMutationError(
            "live installation requires a durable mutation recorder"
        )
    _validate_updates(updates)
    if dry_run:
        log(f"Would update {label} rc: {rc_file}")
        for line in updates.values():
            log(f"  {line}")
        return

    assert recorder is not None
    before = snapshot_path_state(rc_file)
    if before == {"kind": "absent"}:
        original_bytes = b""
        mode = normalize_publication_mode(0o600)
    elif before.get("kind") == "file":
        try:
            original_bytes = read_regular_file_bytes_bounded(
                rc_file,
                allowed_root=rc_file.parent,
                maximum_bytes=_MAX_RC_BYTES,
            )
            original = original_bytes.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise InstallerMutationError("cannot read bounded UTF-8 rc file") from exc
        if snapshot_path_state(rc_file) != before:
            raise InstallerMutationError("rc file changed during bounded read")
        selected_mode = before["mode"]
        if isinstance(selected_mode, bool) or not isinstance(selected_mode, int):
            raise InstallerMutationError("rc file observation has an invalid mode")
        mode = normalize_publication_mode(selected_mode & 0o777)
    else:
        raise InstallerMutationError("rc target is not an absent or regular file")
    try:
        original = original_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise InstallerMutationError("cannot read bounded UTF-8 rc file") from exc
    if "\r" in original:
        raise InstallerMutationError("managed rc input contains a carriage return")
    lines = original.splitlines(keepends=True)

    filtered: list[str] = []
    existing_block_lines: list[str] = []
    inside = False
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped == BLOCK_BEGIN:
            if inside:
                raise InstallerMutationError("managed rc block is nested")
            inside = True
            # Drop the blank separator line written before the block, so
            # repeated writes (from this or another rc_block.py caller
            # sharing the same managed block) don't accumulate blank lines.
            if filtered and not filtered[-1].strip():
                filtered.pop()
            continue
        if stripped == BLOCK_END:
            if not inside:
                raise InstallerMutationError("managed rc block end has no begin")
            inside = False
            continue
        if inside:
            existing_block_lines.append(stripped)
        else:
            filtered.append(line)
    if inside:
        raise InstallerMutationError("managed rc block is not closed")

    merged = _parse_block_vars(existing_block_lines)
    merged.update(updates)

    if _parse_block_vars(list(merged.values())) != merged:
        raise InstallerMutationError("rendered rc block failed exact reparse")

    new_block = f"\n{BLOCK_BEGIN}\n" + "".join(f"{line}\n" for line in merged.values()) + f"{BLOCK_END}\n"

    intended_bytes = ("".join(filtered) + new_block).encode("utf-8")
    intended = {
        "kind": "file",
        "mode": mode,
        "size": len(intended_bytes),
        "sha256": hashlib.sha256(intended_bytes).hexdigest(),
    }
    first_observation = True

    def observe() -> dict[str, object]:
        """Observe the rc target and reject a pre-recording state change.

        Intent
        ------
        Snapshot the live rc file and, on the initial recorder observation, prove
        it still matches the state used to render the intended bytes.

        Rationale
        ---------
        Recording a render derived from stale bytes would make replay target the
        wrong predecessor state.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        nonlocal first_observation
        actual = snapshot_path_state(rc_file)
        if first_observation and recorder.journal.pending_mutation is None and actual != before:
            raise StateRecordError("rc file changed before mutation recording")
        first_observation = False
        return actual

    recorder.mutate(
        operation_key=operation_key,
        kind="marker_block_replace",
        resource_kind="filesystem",
        resource_id=str(rc_file.absolute()),
        intended_after=intended,
        ownership_delta={
            "action": "upsert",
            "entry": {
                "kind": "marker_block",
                "path": str(rc_file),
                "begin": BLOCK_BEGIN,
                "end": BLOCK_END,
            },
        },
        observe=observe,
        apply=lambda pending: atomic_publish_bytes(
            rc_file,
            intended_bytes,
            allowed_root=rc_file.parent,
            mode=mode,
            build_id=pending.mutation_id,
            expected_before=pending.expected_before,
        ),
    )
