"""Managed shell-rc block writer shared by scaffold, launchers, and dev_link.

Each of those three subcommands owns exactly one variable in the managed
block (PATH, ASSISTANT_DEFAULT, AI respectively) but they share one physical
block in the rc file. ensure_rc_vars() merges by variable name so re-running
any one subcommand updates only its own line, leaving the others intact.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

BLOCK_BEGIN = "# >>> assistant-tools >>>"
BLOCK_END = "# <<< assistant-tools <<<"

_VAR_LINE_RE = re.compile(r"^export\s+([A-Za-z_][A-Za-z0-9_]*)=")


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _parse_block_vars(block_lines: list[str]) -> dict[str, str]:
    """Map var name -> full export line, in encounter order (dict preserves it)."""
    parsed: dict[str, str] = {}
    for line in block_lines:
        match = _VAR_LINE_RE.match(line)
        if match:
            parsed[match.group(1)] = line
    return parsed


def ensure_rc_vars(
    rc_file: Path,
    updates: dict[str, str],
    dry_run: bool,
    manifest=None,
    label: str = "user",
) -> None:
    """Merge `updates` (var name -> full `export NAME=...` line) into the
    managed block in rc_file, preserving any other vars already there.
    """
    if dry_run:
        log(f"Would update {label} rc: {rc_file}")
        for line in updates.values():
            log(f"  {line}")
        return

    rc_file.parent.mkdir(parents=True, exist_ok=True)
    rc_file.touch(exist_ok=True)

    original = rc_file.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)

    filtered: list[str] = []
    existing_block_lines: list[str] = []
    inside = False
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped == BLOCK_BEGIN:
            inside = True
            continue
        if stripped == BLOCK_END:
            inside = False
            continue
        if inside:
            existing_block_lines.append(stripped)
        else:
            filtered.append(line)

    merged = _parse_block_vars(existing_block_lines)
    merged.update(updates)

    new_block = f"\n{BLOCK_BEGIN}\n" + "".join(f"{line}\n" for line in merged.values()) + f"{BLOCK_END}\n"

    fd, tmp_path = tempfile.mkstemp(dir=rc_file.parent, prefix=rc_file.name + ".tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(filtered)
            f.write(new_block)
        os.replace(tmp_path, rc_file)
        if manifest is not None:
            manifest.record("marker_block", path=str(rc_file), begin=BLOCK_BEGIN, end=BLOCK_END)
    except Exception:
        os.unlink(tmp_path)
        raise
