#!/usr/bin/env python3
"""
uninstall.py — Reverse the side effects of install.py by replaying the
install manifest.

Manifest-based only: every install records its side effects in a manifest
under the home's state dir, and uninstall undoes exactly those entries.
If the manifest is missing (pre-manifest install, or deleted by hand),
uninstall refuses — guessing at artifacts by pattern is how live generated
files were deleted in the past.

Re-running the installer does NOT fully regenerate a lost manifest, so a
manifest-less installation cannot be reversed by this tool alone. Ownership
of the assistant access roots lives only in the manifest, and an install
step that correctly skips an already-correct artifact also skips recording
it. Concretely, after a re-run against an already-installed system:
  - Codex refuses outright ("managed TOML marker has no matching
    ownership"), so no access entry is journaled at all;
  - Claude's permissions.additionalDirectories re-records with an empty
    `introduced`, which means "own nothing", not "own what is there";
  - launchers.json, Windows-copied launcher helpers, and the per-agent
    profile configs are skipped as already present, so they too lose their
    ownership record.
A later uninstall then exits 0 and reports success while silently leaving
every one of those behind, with no report line naming them. Revoke the
access roots by hand before trusting such an uninstall.

Best-effort within the replay: attempts every reversal, never aborts on
failure, and prints a final report of what was removed, skipped, left
behind, or FAILED (with the reason). Exits non-zero if anything failed.

Never removed, with or without --purge: OAuth credentials and service
configs under ~/.config/cloud-files and ~/.config/g-calendar. Their
manifest entries are never settled either, so the manifest itself always
survives and every run reports it as holding unresolved entries.

Never reversed, and NOT named in the report: local skills previously
migrated into the repo's skills tree, the repo-local Git exclude lines
written alongside them, worker dirs (may contain data), installed Python
dependencies, created directories, and a development checkout's
.famulus/install-id (deliberately kept so a reinstall keeps its
scheduler-visible identity).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping

REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from officina.install.context import (
    InstallationContext,
    resolve_installation_context,
    validate_development_boundaries,
)
from officina.recurring.native import inspect_registration_namespace
from officina.recurring.runtime import native_registration_root
from officina.common import codex_toml, toml_io

if not __package__:
    sys.path.insert(0, str(Path(__file__).parent))

if __package__:
    from ._state_record import InstallManifestError, Manifest
else:
    from _state_record import InstallManifestError, Manifest  # noqa: E402
if __package__:
    from ._assistant_access_config import (
        ACCESS_BEGIN,
        ACCESS_END,
        AssistantAccessConfigError,
        _atomic_write_preserving_mode,
        _load_json_object,
        _read_optional,
    )
else:
    from _assistant_access_config import (  # noqa: E402
        ACCESS_BEGIN,
        ACCESS_END,
        AssistantAccessConfigError,
        _atomic_write_preserving_mode,
        _load_json_object,
        _read_optional,
    )
if __package__:
    from ._shell_block import BLOCK_BEGIN, BLOCK_END
else:
    from _shell_block import (  # noqa: E402
    BLOCK_BEGIN,
    BLOCK_END,
)
if __package__:
    from ._fs_links import default_bin_dir
else:
    from _fs_links import default_bin_dir  # noqa: E402

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[3]


# ── Reporting ─────────────────────────────────────────────────────────────────

class Report:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []  # (status, action, detail)

    def add(self, status: str, action: str, detail: str = "") -> None:
        self.items.append((status, action, detail))

    @property
    def failed(self) -> bool:
        return any(status == "FAILED" for status, _, _ in self.items)

    def print(self) -> None:
        print()
        print("Uninstall report:")
        order = {"removed": 0, "skipped": 1, "left": 2, "FAILED": 3}
        for status, action, detail in sorted(
            self.items, key=lambda i: order.get(i[0], 9)
        ):
            line = f"  [{status}] {action}"
            if detail:
                line += f" — {detail}"
            print(line)
        counts: dict[str, int] = {}
        for status, _, _ in self.items:
            counts[status] = counts.get(status, 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        print(f"  Summary: {summary}")
        if self.failed:
            print("  Some steps FAILED — see above for manual follow-up.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def strip_marker_block(
    path: Path,
    begin: str,
    end: str,
    label: str,
    report: Report,
    dry_run: bool,
    expected_identity: str | None = None,
    separator_identity: str | None = None,
    separator_length: int | None = None,
) -> bool:
    """Remove the begin/end-delimited managed block from a text file."""
    if not path.exists():
        report.add("skipped", f"{label}: {path}", "file does not exist")
        return True
    try:
        original = path.read_bytes()
    except OSError as exc:
        report.add("FAILED", f"{label}: {path}", f"could not read: {exc}")
        return False
    begin_bytes = begin.encode("utf-8")
    end_bytes = end.encode("utf-8")
    lines = original.splitlines(keepends=True)
    spans: list[tuple[int, int, str]] = []
    active_start: int | None = None
    for index, line in enumerate(lines):
        marker = line.rstrip(b"\r\n")
        if active_start is None and marker == begin_bytes:
            active_start = index
        elif active_start is not None and marker == end_bytes:
            raw = b"".join(lines[active_start : index + 1])
            spans.append((active_start, index, hashlib.sha256(raw).hexdigest()))
            active_start = None
    if not spans:
        if active_start is not None:
            report.add("FAILED", f"{label}: {path}", "managed block is incomplete; preserved")
            return False
        report.add("skipped", f"{label}: {path}", "no managed block found")
        return True
    if isinstance(expected_identity, str):
        matches = [span for span in spans if span[2] == expected_identity]
        if len(matches) != 1:
            detail = "managed block was modified; preserved" if not matches else "multiple identity-matching blocks are ambiguous; preserved"
            report.add("skipped", f"{label}: {path}", detail)
            return not matches
        start_index, end_index, _identity = matches[0]
    else:
        start_index, end_index, _identity = spans[0]
    if dry_run:
        print(f"Would strip managed block from {path}")
        report.add("removed", f"{label}: {path}", "(dry-run)")
        return True

    remove_start = sum(len(line) for line in lines[:start_index])
    remove_end = sum(len(line) for line in lines[: end_index + 1])
    if (
        start_index > 0
        and isinstance(separator_identity, str)
        and isinstance(separator_length, int)
        and not isinstance(separator_length, bool)
    ):
        separator = lines[start_index - 1]
        if (
            len(separator) == separator_length
            and hashlib.sha256(separator).hexdigest() == separator_identity
        ):
            remove_start -= len(separator)
    filtered = original[:remove_start] + original[remove_end:]
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".tmp.")
        with os.fdopen(fd, "wb") as f:
            f.write(filtered)
        os.replace(tmp, path)
        report.add("removed", f"{label}: {path}", "managed block stripped")
        return True
    except OSError as exc:
        report.add("FAILED", f"{label}: {path}", f"could not write: {exc}")
        return False


def remove_file(path: Path, label: str, report: Report, dry_run: bool) -> None:
    if not path.exists():
        report.add("skipped", f"{label}: {path}", "does not exist")
        return
    if dry_run:
        print(f"Would remove {path}")
        report.add("removed", f"{label}: {path}", "(dry-run)")
        return
    try:
        path.unlink()
        report.add("removed", f"{label}: {path}")
    except OSError as exc:
        report.add("FAILED", f"{label}: {path}", f"could not remove: {exc}")


def remove_tree(path: Path, label: str, report: Report, dry_run: bool) -> None:
    if not path.exists():
        report.add("skipped", f"{label}: {path}", "does not exist")
        return
    if dry_run:
        print(f"Would remove directory {path}")
        report.add("removed", f"{label}: {path}", "(dry-run)")
        return
    try:
        shutil.rmtree(path)
        report.add("removed", f"{label}: {path}")
    except OSError as exc:
        report.add("FAILED", f"{label}: {path}", f"could not remove: {exc}")


def remove_manifest_file(entry: dict, report: Report, dry_run: bool) -> bool:
    """Remove an owned file unless its recorded content identity changed."""
    path = Path(entry["path"])
    expected = entry.get("sha256")
    if isinstance(expected, str):
        if not path.exists():
            report.add("skipped", f"generated file: {path}", "does not exist")
            return True
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            report.add("FAILED", f"generated file: {path}", f"could not read: {exc}")
            return False
        if actual != expected:
            report.add(
                "skipped",
                f"generated file: {path}",
                "modified since install; preserved",
            )
            return True
    before = report.failed
    remove_file(path, "generated file", report, dry_run)
    return report.failed == before


def remove_manifest_tree(entry: dict, report: Report, dry_run: bool) -> bool:
    path = Path(entry["path"])
    if not path.exists():
        report.add("skipped", f"generated tree: {path}", "does not exist")
        return True
    expected = entry.get("tree_sha256")
    try:
        actual = Manifest.tree_identity(path)
    except OSError as exc:
        report.add("FAILED", f"generated tree: {path}", f"could not inspect: {exc}")
        return False
    if not isinstance(expected, str) or actual != expected:
        report.add("skipped", f"generated tree: {path}", "modified since install; preserved")
        return True
    before = report.failed
    remove_tree(path, "generated tree", report, dry_run)
    return report.failed == before


# ── Steps ─────────────────────────────────────────────────────────────────────

def remove_manifest_git_hooks(entry: dict, report: Report, dry_run: bool) -> bool:
    repo_root = Path(entry["path"])
    installed_value = entry.get("installed_value", ".githooks")
    prior_value = entry.get("prior_value")
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--get", "core.hooksPath"],
            capture_output=True, text=True, encoding="utf-8", errors="strict",
        )
    except OSError as exc:
        report.add("FAILED", "git core.hooksPath", f"git unavailable: {exc}")
        return False
    if result.returncode != 0 or result.stdout.strip() != installed_value:
        report.add("skipped", "git core.hooksPath", "modified since install; preserved")
        return True
    if dry_run:
        verb = "restore" if isinstance(prior_value, str) else "unset"
        print(f"Would {verb} core.hooksPath in {repo_root}")
        report.add("removed", "git core.hooksPath", "(dry-run)")
        return True
    arguments = ["git", "-C", str(repo_root), "config"]
    if isinstance(prior_value, str):
        arguments.extend(("core.hooksPath", prior_value))
    else:
        arguments.extend(("--unset", "core.hooksPath"))
    changed = subprocess.run(
        arguments,
        capture_output=True, text=True, encoding="utf-8", errors="strict",
    )
    if changed.returncode == 0:
        detail = f"restored {prior_value}" if isinstance(prior_value, str) else "unset"
        report.add("removed", "git core.hooksPath", detail)
        return True
    else:
        report.add("FAILED", "git core.hooksPath", changed.stderr.strip())
        return False


def uninstall_pip_package(report: Report, dry_run: bool) -> None:
    if dry_run:
        print("Would pip uninstall script_dispatcher")
        report.add("removed", "pip package script_dispatcher", "(dry-run)")
        return
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", "script_dispatcher"],
        capture_output=True, text=True, encoding="utf-8", errors="strict",
    )
    if result.returncode == 0:
        report.add("removed", "pip package script_dispatcher")
    elif "not installed" in (result.stdout + result.stderr).lower():
        report.add("skipped", "pip package script_dispatcher", "not installed")
    else:
        report.add("FAILED", "pip package script_dispatcher", result.stderr.strip()[:200])
    report.add(
        "left", "other pip dependencies",
        "shared packages are not uninstalled; remove manually if unwanted",
    )


# ── Manifest replay ───────────────────────────────────────────────────────────

def remove_manifest_symlink(entry: dict, report: Report, dry_run: bool) -> bool:
    """Remove a recorded symlink if it still points where install left it.

    Returns True when the entry is settled (removed or safely skipped),
    False on failure (entry stays in the manifest).
    """
    link = Path(entry["path"])
    if not link.is_symlink():
        report.add("skipped", str(link), "no longer a symlink")
        return True
    recorded_target = entry.get("target", "")
    try:
        actual_target = os.readlink(link)
    except OSError as exc:
        report.add("FAILED", str(link), f"could not readlink: {exc}")
        return False
    # Normalize before comparing: on Windows os.readlink() returns
    # \\?\-prefixed extended paths, which would never string-match the
    # recorded target and wrongly preserve every installed symlink.
    def _norm(p: str) -> str:
        text = str(p)
        if text.startswith("\\\\?\\"):
            text = text[4:]
        return os.path.normcase(os.path.normpath(text))

    if _norm(actual_target) != _norm(recorded_target):
        report.add("skipped", str(link), "re-pointed since install; preserved")
        return True
    if dry_run:
        print(f"Would remove symlink {link}")
        report.add("removed", str(link), "(dry-run)")
        return True
    try:
        link.unlink()
        report.add("removed", str(link))
        return True
    except OSError as exc:
        report.add("FAILED", str(link), f"could not unlink: {exc}")
        return False


def remove_manifest_json_hooks(entry: dict, report: Report, dry_run: bool) -> bool:
    settings_file = Path(entry["path"])
    commands = set(entry.get("commands", []))
    if not settings_file.exists():
        report.add("skipped", f"claude hooks: {settings_file}", "does not exist")
        return True
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.add("FAILED", f"claude hooks: {settings_file}", f"could not parse: {exc}")
        return False
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        report.add("skipped", f"claude hooks: {settings_file}", "no hooks section")
        return True
    changed = False
    for event_name in list(hooks.keys()):
        entries = hooks.get(event_name)
        if not isinstance(entries, list):
            continue
        kept = [
            e for e in entries
            if not any(
                isinstance(h, dict) and h.get("command", "") in commands
                for h in e.get("hooks", [])
            )
        ]
        if len(kept) != len(entries):
            changed = True
            if kept:
                hooks[event_name] = kept
            else:
                hooks.pop(event_name)
    if not hooks:
        settings.pop("hooks", None)
    if not settings or settings == {"hooks": {}}:
        before = report.failed
        remove_file(settings_file, "claude settings (emptied)", report, dry_run)
        return report.failed == before
    if not changed:
        report.add("skipped", f"claude hooks: {settings_file}", "no managed entries found")
        return True
    if dry_run:
        print(f"Would remove managed hook entries from {settings_file}")
        report.add("removed", f"claude hooks: {settings_file}", "(dry-run)")
        return True
    try:
        fd, tmp = tempfile.mkstemp(dir=settings_file.parent, prefix=settings_file.name + ".tmp.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        os.replace(tmp, settings_file)
        report.add("removed", f"claude hooks: {settings_file}", "managed entries removed")
        return True
    except OSError as exc:
        report.add("FAILED", f"claude hooks: {settings_file}", f"could not write: {exc}")
        return False


def _content_identity(raw: bytes | None) -> str | None:
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def _identity_mode(mode: int | None) -> int | None:
    return None if os.name == "nt" else mode


def _read_optional_bytes(path: Path) -> bytes | None:
    return _read_optional(path)


def _pending_replay_state(
    entry: dict, raw: bytes | None, *, path: Path, label: str, report: Report
) -> str:
    if entry.get("transaction") != "pending":
        return "post"
    identity = _content_identity(raw)
    current_mode = _identity_mode(_mode(path))
    pre_mode = entry.get("pre_mode", None if entry.get("pre_sha256") is None else entry.get("file_mode"))
    post_mode = entry.get("post_mode", entry.get("file_mode"))
    if identity == entry.get("pre_sha256") and current_mode == pre_mode:
        report.add("skipped", label, "pending write never applied; pre-state preserved")
        return "pre"
    if identity == entry.get("post_sha256") and current_mode == post_mode:
        return "post"
    report.add(
        "FAILED",
        label,
        "pending write has neither its recorded pre-state nor intended post-state; preserved",
    )
    return "unknown"


def _mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None


def _uninstall_replay_state(
    entry: dict, raw: bytes | None, *, path: Path, label: str, report: Report
) -> str:
    if entry.get("uninstall_transaction") != "pending":
        return "new"
    identity = _content_identity(raw)
    current_mode = _identity_mode(_mode(path))
    if (
        identity == entry.get("uninstall_post_sha256")
        and current_mode == entry.get("uninstall_post_mode")
    ):
        report.add("removed", label, "completed uninstall intent recovered")
        return "post"
    if (
        identity == entry.get("uninstall_pre_sha256")
        and current_mode == entry.get("uninstall_pre_mode")
    ):
        return "pre"
    report.add(
        "FAILED",
        label,
        "pending uninstall has neither its recorded pre-state nor intended post-state; preserved",
    )
    return "unknown"


def _persist_uninstall_intent(
    manifest: Manifest,
    entry: dict,
    *,
    path: Path,
    original: bytes,
    replacement: bytes | None,
) -> int:
    pre_mode = _mode(path)
    assert pre_mode is not None
    identity_pre_mode = _identity_mode(pre_mode)
    post_mode = None if replacement is None else identity_pre_mode
    intended = {
        "uninstall_transaction": "pending",
        "uninstall_pre_sha256": _content_identity(original),
        "uninstall_post_sha256": _content_identity(replacement),
        "uninstall_pre_mode": identity_pre_mode,
        "uninstall_post_mode": post_mode,
    }
    if entry.get("uninstall_transaction") == "pending":
        if any(entry.get(key) != value for key, value in intended.items()):
            raise AssistantAccessConfigError(
                f"pending uninstall intent changed for assistant configuration: {path}"
            )
        return pre_mode
    entry.update(intended)
    manifest.save()
    return pre_mode


def remove_codex_access_array_block(
    manifest: Manifest, entry: dict, report: Report, dry_run: bool,
    prepared: dict[int, object] | None = None,
) -> bool:
    path = Path(entry["path"])
    label = f"Codex assistant access: {path}"
    try:
        state = codex_toml.config_state(path.parent)
    except (OSError, toml_io.TomlManagedArrayError) as exc:
        report.add("FAILED", label, f"could not read: {exc}")
        return False
    if entry.get("transaction") == "pending":
        pre_mode = entry.get("pre_mode", None if entry.get("pre_sha256") is None else entry.get("file_mode"))
        post_mode = entry.get("post_mode", entry.get("file_mode"))
        if state.sha256 == entry.get("pre_sha256") and _identity_mode(state.mode) == pre_mode:
            report.add("skipped", label, "pending write never applied; pre-state preserved")
            return True
        if state.sha256 != entry.get("post_sha256") or _identity_mode(state.mode) != post_mode:
            report.add("FAILED", label, "pending write has neither its recorded pre-state nor intended post-state; preserved")
            return False
    if entry.get("uninstall_transaction") == "pending":
        if state.sha256 == entry.get("uninstall_post_sha256") and _identity_mode(state.mode) == entry.get("uninstall_post_mode"):
            report.add("removed", label, "completed uninstall intent recovered")
            return True
        if state.sha256 != entry.get("uninstall_pre_sha256") or _identity_mode(state.mode) != entry.get("uninstall_pre_mode"):
            report.add("FAILED", label, "pending uninstall has neither its recorded pre-state nor intended post-state; preserved")
            return False
    if state.sha256 is None:
        report.add("skipped", label, "file does not exist")
        return True
    plan = prepared.get(id(entry)) if prepared is not None else None
    if not isinstance(plan, toml_io.ManagedArrayPlan):
        try:
            plan = codex_toml.plan_access_removal(path.parent, ownership=entry)
        except toml_io.TomlManagedArrayError as exc:
            report.add("FAILED", label, f"managed block location is unproven; preserved: {exc}")
            return False
    if prepared is not None:
        prepared[id(entry)] = plan
    if dry_run:
        report.add("removed", label, "managed roots would be removed (dry-run)")
        return True
    try:
        intended = {
            "uninstall_transaction": "pending",
            "uninstall_pre_sha256": plan.current_sha256,
            "uninstall_post_sha256": plan.replacement_sha256,
            "uninstall_pre_mode": _identity_mode(plan.mode),
            "uninstall_post_mode": None if plan.replacement_sha256 is None else _identity_mode(plan.mode),
        }
        if entry.get("uninstall_transaction") == "pending":
            if any(entry.get(key) != value for key, value in intended.items()):
                raise AssistantAccessConfigError(f"pending uninstall intent changed for assistant configuration: {path}")
        else:
            entry.update(intended)
            manifest.save()
        codex_toml.apply_access_plan(plan)
        report.add("removed", label, "managed roots removed")
        return True
    except (OSError, AssistantAccessConfigError, toml_io.TomlManagedArrayError) as exc:
        report.add("FAILED", label, f"could not write: {exc}")
        return False


def remove_json_array_values(
    manifest: Manifest, entry: dict, report: Report, dry_run: bool,
    prepared: dict[int, object] | None = None,
) -> bool:
    path = Path(entry["path"])
    label = f"Claude assistant access: {path}"
    frozen = prepared.get(id(entry)) if prepared is not None else None
    if isinstance(frozen, tuple) and len(frozen) == 4:
        _frozen_path, original, replacement, mode = frozen
        if dry_run:
            report.add("removed", label, "managed values would be removed (dry-run)")
            return True
        try:
            _persist_uninstall_intent(manifest, entry, path=path, original=original, replacement=replacement)
            _atomic_write_preserving_mode(path, replacement, expected=original, mode=mode)
            report.add("removed", label, "managed values removed")
            return True
        except (OSError, AssistantAccessConfigError) as exc:
            report.add("FAILED", label, f"could not write: {exc}")
            return False
    try:
        original = _read_optional_bytes(path)
    except (OSError, AssistantAccessConfigError) as exc:
        report.add("FAILED", label, f"could not read: {exc}")
        return False
    state = _pending_replay_state(entry, original, path=path, label=label, report=report)
    if state == "pre":
        return True
    if state == "unknown":
        return False
    uninstall_state = _uninstall_replay_state(
        entry, original, path=path, label=label, report=report
    )
    if uninstall_state == "post":
        return True
    if uninstall_state == "unknown":
        return False
    if original is None:
        report.add("skipped", label, "file does not exist")
        return True
    try:
        payload = _load_json_object(original, path=path, label="Claude")
    except AssistantAccessConfigError as exc:
        report.add("FAILED", label, f"could not parse: {exc}")
        return False
    permissions = payload.get("permissions") if isinstance(payload, dict) else None
    values = permissions.get("additionalDirectories") if isinstance(permissions, dict) else None
    introduced = entry.get("introduced", [])
    if (
        not isinstance(payload, dict)
        or not isinstance(values, list)
        or any(not isinstance(value, str) for value in values)
        or not isinstance(introduced, list)
        or any(not isinstance(value, str) for value in introduced)
    ):
        report.add("skipped", label, "owned JSON array structure was modified; preserved")
        return False
    if any(values.count(value) != 1 for value in introduced):
        report.add("skipped", label, "managed values were modified; preserved")
        return False
    owned = set(introduced)
    permissions["additionalDirectories"] = [value for value in values if value not in owned]
    if entry.get("created_key") and not permissions["additionalDirectories"]:
        permissions.pop("additionalDirectories")
    if entry.get("created_permissions") and not permissions:
        payload.pop("permissions")
    filtered = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    replacement = None if entry.get("created_file") and not payload else filtered
    mode = _mode(path)
    assert mode is not None
    if prepared is not None:
        prepared[id(entry)] = (path, original, replacement, mode)
    if dry_run:
        report.add("removed", label, "managed values would be removed (dry-run)")
        return True
    try:
        mode = _persist_uninstall_intent(
            manifest, entry, path=path, original=original, replacement=replacement
        )
        _atomic_write_preserving_mode(
            path, replacement, expected=original, mode=mode
        )
        report.add("removed", label, "managed values removed")
        return True
    except (OSError, AssistantAccessConfigError) as exc:
        report.add("FAILED", label, f"could not write: {exc}")
        return False


def _assistant_access_targets(context: InstallationContext) -> tuple[Path, Path]:
    return (
        codex_toml.config_path(context.codex_home),
        context.claude_home / "settings.json",
    )


def _assistant_access_preflight(
    manifest: Manifest,
    report: Report,
    context: InstallationContext | None,
) -> tuple[bool, dict[int, object]]:
    prepared: dict[int, object] = {}
    access_entries = [
        entry
        for entry in manifest.entries
        if entry.get("kind") in {"codex_access_array_block", "json_array_values"}
    ]
    if not access_entries:
        return True, prepared
    if context is None:
        report.add(
            "FAILED",
            "assistant access preflight",
            "installation context is required to prove assistant-access ownership",
        )
        return False, prepared
    codex_path, claude_path = _assistant_access_targets(context)
    expected = {
        "codex_access_array_block": str(codex_path),
        "json_array_values": str(claude_path),
    }
    for kind, path in expected.items():
        entries = [entry for entry in access_entries if entry.get("kind") == kind]
        if len(entries) != 1 or entries[0].get("path") != path:
            report.add(
                "FAILED",
                "assistant access preflight",
                f"expected exactly one {kind} entry for {path}",
            )
            return False, prepared
    scratch = Report()
    replayable = True
    for entry in manifest.entries:
        kind = entry.get("kind")
        if kind == "codex_access_array_block":
            replayable = (
                remove_codex_access_array_block(manifest, entry, scratch, True, prepared)
                and replayable
            )
        elif kind == "json_array_values":
            replayable = remove_json_array_values(manifest, entry, scratch, True, prepared) and replayable
    if replayable:
        return True, prepared
    for status, action, detail in scratch.items:
        if status != "removed":
            report.add(status, action, detail)
    report.add(
        "FAILED",
        "assistant access preflight",
        "all assistant-access entries must be fully replayable before either target changes",
    )
    return False, prepared


def _prepared_access_current(prepared: dict[int, object], report: Report) -> bool:
    for value in prepared.values():
        if isinstance(value, toml_io.ManagedArrayPlan):
            try:
                state = codex_toml.config_state(value.path.parent)
            except (OSError, toml_io.TomlManagedArrayError) as exc:
                report.add("FAILED", "assistant access preflight", str(exc))
                return False
            if state.sha256 != value.current_sha256 or _identity_mode(state.mode) != _identity_mode(value.mode):
                report.add("FAILED", "assistant access preflight", "Codex target changed after frozen preflight")
                return False
        elif isinstance(value, tuple) and len(value) == 4:
            path, expected, _replacement, mode = value
            try:
                current = _read_optional_bytes(path)
            except (OSError, AssistantAccessConfigError) as exc:
                report.add("FAILED", "assistant access preflight", str(exc))
                return False
            if current != expected or (
                current is not None
                and _identity_mode(_mode(path)) != _identity_mode(mode)
            ):
                report.add("FAILED", "assistant access preflight", "Claude target changed after frozen preflight")
                return False
    return True


def _normalized_windows_component(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value))


def _ordered_subsequence(required: list[str], available: list[str]) -> bool:
    position = 0
    for value in available:
        if position < len(required) and value == required[position]:
            position += 1
    return position == len(required)


def _path_after_owned_registry_removal(
    entry: dict, *, current_path: str, current_type: int
) -> str | None:
    path_value = entry.get("path_value")
    prior_path = entry.get("prior_path")
    prior_identity = entry.get("prior_path_sha256")
    installed_identity = entry.get("installed_path_sha256")
    recorded_type = entry.get("value_type")
    if (
        not isinstance(path_value, str)
        or path_value != entry.get("path")
        or not isinstance(prior_path, str)
        or not isinstance(prior_identity, str)
        or not isinstance(installed_identity, str)
        or not isinstance(recorded_type, int)
        or isinstance(recorded_type, bool)
        or current_type != recorded_type
    ):
        return None
    if hashlib.sha256(prior_path.encode("utf-8")).hexdigest() != prior_identity:
        return None
    prior_parts = [part for part in prior_path.split(";") if part]
    installed_path = ";".join([path_value, *prior_parts])
    if hashlib.sha256(installed_path.encode("utf-8")).hexdigest() != installed_identity:
        return None

    normalized_owned = _normalized_windows_component(path_value)
    raw_parts = current_path.split(";")
    normalized_prior = [_normalized_windows_component(part) for part in prior_parts]
    for index, part in enumerate(raw_parts):
        if not part or _normalized_windows_component(part) != normalized_owned:
            continue
        kept = raw_parts[:index] + raw_parts[index + 1 :]
        normalized_kept = [
            _normalized_windows_component(value) for value in kept if value
        ]
        if _ordered_subsequence(normalized_prior, normalized_kept):
            return ";".join(kept)
    return None


def remove_registry_env(entry: dict, report: Report, dry_run: bool) -> bool:
    if sys.platform != "win32":
        report.add("skipped", "windows registry env", "not on Windows")
        return True
    if entry.get("path_inserted") is not True:
        report.add(
            "skipped",
            "windows registry env",
            "no recorded installer-owned PATH insertion; preserved",
        )
        return True
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            try:
                current_path, path_type = winreg.QueryValueEx(key, "PATH")
                transaction_state = entry.get("transaction_state", "committed")
                if transaction_state == "pending":
                    prior_path = entry.get("prior_path")
                    prior_identity = entry.get("prior_path_sha256")
                    installed_identity = entry.get("installed_path_sha256")
                    current_identity = hashlib.sha256(
                        current_path.encode("utf-8")
                    ).hexdigest()
                    identities_valid = (
                        isinstance(prior_path, str)
                        and isinstance(prior_identity, str)
                        and isinstance(installed_identity, str)
                        and hashlib.sha256(prior_path.encode("utf-8")).hexdigest()
                        == prior_identity
                        and entry.get("value_type") == path_type
                    )
                    if identities_valid and current_identity == prior_identity:
                        report.add(
                            "skipped",
                            "windows registry PATH",
                            "pending insertion was not applied; registry already matches prior value",
                        )
                        return True
                    if not (identities_valid and current_identity == installed_identity):
                        report.add(
                            "left",
                            "windows registry PATH pending intent",
                            "current PATH is neither the exact prior nor intended value; preserved for explicit retry",
                        )
                        return False
                restored_path = _path_after_owned_registry_removal(
                    entry, current_path=current_path, current_type=path_type
                )
                if restored_path is None:
                    report.add(
                        "skipped",
                        "windows registry PATH",
                        "recorded/current PATH identity does not prove a safe owned removal; preserved",
                    )
                elif dry_run:
                    print("Would remove installer-owned registry PATH component")
                    report.add("removed", "windows registry PATH", "(dry-run)")
                else:
                    winreg.SetValueEx(key, "PATH", 0, path_type, restored_path)
                    report.add("removed", "windows registry PATH")
            except FileNotFoundError:
                pass
            for name in entry.get("names", []):
                if str(name).casefold() == "path":
                    continue
                try:
                    current_value, _current_type = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                expected_values = entry.get("values", {})
                if isinstance(expected_values, dict) and name in expected_values:
                    if current_value != expected_values[name]:
                        report.add(
                            "skipped",
                            f"windows registry env {name}",
                            "modified since install; preserved",
                        )
                        continue
                else:
                    report.add(
                        "skipped",
                        f"windows registry env {name}",
                        "no recorded value identity; preserved",
                    )
                    continue
                winreg.DeleteValue(key, name)
        return True
    except OSError as exc:
        report.add("FAILED", "windows registry env", str(exc))
        return False


def _replay_manifest_unlocked(
    manifest: Manifest,
    report: Report,
    *,
    dry_run: bool,
    purge: bool,
    no_pip: bool,
    no_git_hooks: bool,
    context: InstallationContext | None = None,
) -> None:
    """Undo every manifest entry; settled entries are dropped from the manifest."""
    assistant_access_ready, prepared_access = _assistant_access_preflight(manifest, report, context)
    if assistant_access_ready and not _prepared_access_current(prepared_access, report):
        assistant_access_ready = False
    access_kinds = {"codex_access_array_block", "json_array_values"}
    entries = list(manifest.entries)
    access_entries = [entry for entry in entries if entry.get("kind") in access_kinds]
    if assistant_access_ready:
        access_results: list[bool] = []
        for entry in access_entries:
            if entry.get("kind") == "codex_access_array_block":
                access_results.append(
                    remove_codex_access_array_block(
                        manifest, entry, report, dry_run, prepared_access
                    )
                )
            else:
                access_results.append(
                    remove_json_array_values(
                        manifest, entry, report, dry_run, prepared_access
                    )
                )
        if access_results and all(access_results) and not dry_run:
            for entry in access_entries:
                manifest.remove(entry)
            manifest.save()
    for entry in entries:
        kind = entry.get("kind")
        path = entry.get("path", "")
        settled = True
        artifact_path = Path(path)
        if kind in access_kinds:
            continue
        if context is not None and (
            artifact_path == context.paths.recurring_config_root
            or context.paths.recurring_config_root in artifact_path.parents
            or artifact_path == context.paths.recurring_state_root
            or context.paths.recurring_state_root in artifact_path.parents
        ):
            report.add("left", f"recurring-owned state: {path}", "preserved by installer ownership boundary")
            settled = False
        elif entry.get("purge_only") and not purge:
            report.add("left", f"purge-only artifact: {path}", "re-run with --purge to remove unchanged installer state")
            settled = False
        elif kind == "symlink":
            settled = remove_manifest_symlink(entry, report, dry_run)
        elif kind == "marker_block":
            settled = strip_marker_block(
                Path(path), entry.get("begin", BLOCK_BEGIN), entry.get("end", BLOCK_END),
                "managed block", report, dry_run,
                entry.get("block_sha256"),
                entry.get("separator_sha256"),
                entry.get("separator_length"),
            )
            # If stripping leaves the file blank, it existed only for our
            # block — remove the empty config file husk we created.
            stripped_file = Path(path)
            if (
                not dry_run
                and stripped_file.is_file()
                and not stripped_file.read_bytes().strip()
            ):
                remove_file(stripped_file, "managed block file (emptied)", report, dry_run)
        elif kind == "json_hook_commands":
            settled = remove_manifest_json_hooks(entry, report, dry_run)
        elif kind == "git_hooks_path":
            if no_git_hooks:
                report.add("skipped", "git core.hooksPath", "--no-git-hooks")
                settled = False
            else:
                settled = remove_manifest_git_hooks(entry, report, dry_run)
        elif kind in {"file", "launcher", "generated_config"}:
            settled = remove_manifest_file(entry, report, dry_run)
        elif kind == "tree":
            settled = remove_manifest_tree(entry, report, dry_run)
        elif kind == "config_dir":
            if Path(path).exists():
                report.add(
                    "left", f"config/credentials: {path}",
                    "mutable user data and credentials are never recursively deleted",
                )
            settled = False
        elif kind == "pip_editable":
            if no_pip:
                report.add("skipped", f"pip package {path}", "--no-pip")
                settled = False
            else:
                before = report.failed
                uninstall_pip_package(report, dry_run)
                settled = report.failed == before
        elif kind == "registry_env":
            settled = remove_registry_env(entry, report, dry_run)
        else:
            report.add("skipped", f"unknown manifest entry kind: {kind}", str(path))
        if settled and not dry_run:
            manifest.remove(entry)
            manifest.save()

    if dry_run:
        return
    if manifest.entries:
        manifest.save()
        report.add(
            "left", f"manifest: {manifest.path}",
            f"{len(manifest.entries)} unresolved entry(s) kept for a future run",
        )
    else:
        manifest.delete()


def replay_manifest(
    manifest: Manifest,
    report: Report,
    *,
    dry_run: bool,
    purge: bool,
    no_pip: bool,
    no_git_hooks: bool,
    context: InstallationContext | None = None,
) -> None:
    if context is None:
        _replay_manifest_unlocked(manifest, report, dry_run=dry_run, purge=purge, no_pip=no_pip, no_git_hooks=no_git_hooks, context=context)
        return
    if dry_run:
        manifest.reload()
        _replay_manifest_unlocked(manifest, report, dry_run=True, purge=purge, no_pip=no_pip, no_git_hooks=no_git_hooks, context=context)
        return
    lock_root = context.paths.install_state_root
    lock_root.mkdir(parents=True, exist_ok=True)
    from officina.common.atomic_files import exclusive_file_lock
    with exclusive_file_lock(lock_root / "assistant-access.lock", allowed_root=lock_root):
        manifest.reload()
        _replay_manifest_unlocked(manifest, report, dry_run=dry_run, purge=purge, no_pip=no_pip, no_git_hooks=no_git_hooks, context=context)


def _manifest_matches_context(manifest: Manifest, context: InstallationContext) -> bool:
    expected = {"mode": context.mode, "installation_id": context.installation_id}
    if context.development_root is not None:
        expected["development_root"] = str(context.development_root.resolve(strict=False))
    elif manifest.installation and (
        "codex_home" in manifest.installation or "claude_home" in manifest.installation
    ):
        expected["codex_home"] = str(context.codex_home.resolve(strict=False))
        expected["claude_home"] = str(context.claude_home.resolve(strict=False))
    return manifest.installation == expected


def _recurring_preflight(
    context: InstallationContext, report: Report, *, platform: str
) -> bool:
    status = inspect_registration_namespace(
        installation_id=context.installation_id,
        state_root=context.paths.recurring_state_root,
        native_registration_root=native_registration_root(context, platform),
        platform=platform,
    )
    if not status.certain:
        report.add(
            "FAILED",
            f"recurring namespace certainty for {context.installation_id}",
            (status.detail or "native scheduler inventory unavailable")
            + "; no installer artifacts were changed",
        )
        return False
    if status.registrations_present:
        report.add(
            "FAILED",
            f"recurring registrations for {context.installation_id}",
            "run recurring-tasks remove-context for this installation ID before uninstall or purge",
        )
        return False
    return True


def _development_entry_is_contained(entry: dict, context: InstallationContext) -> bool:
    if context.development_root is None:
        return True
    if entry.get("kind") == "git_hooks_path":
        return Path(entry.get("path", "")).resolve(strict=False) == context.development_root
    local_root = (context.development_root / ".famulus").resolve(strict=False)
    raw_path = Path(entry.get("path", ""))
    if entry.get("kind") == "symlink":
        path = raw_path.parent.resolve(strict=False) / raw_path.name
    else:
        path = raw_path.resolve(strict=False)
    return path == local_root or local_root in path.parents


def uninstall_context(
    *,
    context: InstallationContext,
    platform: str,
    home: Path,
    environ: Mapping[str, str],
    purge: bool,
    dry_run: bool,
    no_pip: bool,
    no_git_hooks: bool,
) -> Report:
    """Replay only the manifest bound to the explicitly selected context."""
    report = Report()
    if context.mode == "development":
        try:
            validate_development_boundaries(
                context,
                operation="uninstall",
                platform=platform,
                home=home,
                environ=environ,
            )
        except (OSError, ValueError) as exc:
            report.add("FAILED", "development containment preflight", str(exc))
            return report
    try:
        manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    except InstallManifestError as exc:
        report.add("FAILED", "install manifest preflight", str(exc))
        return report
    if not manifest.entries:
        report.add("FAILED", f"manifest: {manifest.path}", "no install manifest found; run apply for this exact context first")
        return report
    if not _manifest_matches_context(manifest, context):
        report.add("FAILED", f"manifest: {manifest.path}", "manifest belongs to a different or legacy unbound installation context")
        return report
    if not _recurring_preflight(context, report, platform=platform):
        return report
    if context.mode == "development" and any(
        not _development_entry_is_contained(entry, context) for entry in manifest.entries
    ):
        report.add("FAILED", f"manifest: {manifest.path}", "manifest contains an artifact outside the selected checkout .famulus boundary")
        return report
    if context.mode == "development" and context.development_root is not None:
        install_id_path = (context.development_root / ".famulus" / "install-id").resolve(
            strict=False
        )
        manifest.entries = [
            entry
            for entry in manifest.entries
            if Path(entry.get("path", "")).resolve(strict=False) != install_id_path
        ]
    replay_manifest(
        manifest,
        report,
        dry_run=dry_run,
        purge=purge,
        no_pip=no_pip,
        no_git_hooks=no_git_hooks,
        context=context,
    )
    return report


# ── Entry point ───────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mode", choices=("standard", "development"), required=True)
    parser.add_argument("--checkout", metavar="ABSOLUTE_PATH")
    parser.add_argument("--home", metavar="DIR")
    parser.add_argument("--claude-home", metavar="DIR")
    parser.add_argument("--codex-home", metavar="DIR")
    parser.add_argument("--bin-dir", metavar="DIR")
    parser.add_argument("--shell-rc", metavar="FILE")
    parser.add_argument("--system-shell-rc", metavar="FILE", default="/etc/bash.bashrc")
    parser.add_argument("--no-system-shell-rc", action="store_true")
    parser.add_argument("--repo-root", metavar="DIR")
    parser.add_argument("--no-pip", action="store_true",
        help="Do not uninstall the script_dispatcher pip package")
    parser.add_argument("--no-git-hooks", action="store_true",
        help="Do not unset git core.hooksPath")
    parser.add_argument("--purge", action="store_true",
        help="Also remove unchanged installer-owned immutable artifacts (managed "
             "runtime, launcher profiles). Credential and service config dirs are "
             "never removed, with or without this flag.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.mode == "standard" and args.checkout is not None:
        parser.error("--checkout is valid only with --mode development")
    if args.mode == "development" and args.checkout is None:
        parser.error("--mode development requires --checkout")
    return args


def main() -> None:
    args = parse_args()
    home = Path(args.home).expanduser().resolve() if args.home else Path.home().resolve()
    selected_environ: dict[str, str] = {}
    if sys.platform == "win32":
        selected_environ = {
            "APPDATA": str(home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
        }
    repo_root = Path(args.repo_root) if args.repo_root else REPO_ROOT_DEFAULT
    if args.mode == "development":
        checkout = Path(args.checkout)
        if not checkout.is_absolute():
            raise SystemExit("--checkout must be an absolute path")
        identifier_path = checkout / ".famulus" / "install-id"
        try:
            installation_id = identifier_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SystemExit(f"development context is absent: {identifier_path}: {exc}") from exc
        context = resolve_installation_context(
            mode="development",
            source_root=checkout,
            development_root=checkout,
            platform=sys.platform,
            home=home,
            environ=selected_environ,
            installation_id=installation_id,
        )
    else:
        context = resolve_installation_context(
            mode="standard",
            source_root=repo_root.resolve(),
            development_root=None,
            platform=sys.platform,
            home=home,
            environ=selected_environ,
        )
    report = uninstall_context(
        context=context,
        platform=sys.platform,
        home=home,
        environ=selected_environ,
        purge=args.purge,
        dry_run=args.dry_run,
        no_pip=args.no_pip,
        no_git_hooks=args.no_git_hooks,
    )
    report.add(
        "left", f"worker dirs: {context.paths.worker_root}",
        "mutable session data is never recursively deleted by installer removal",
    )
    report.print()
    sys.exit(1 if report.failed else 0)


if __name__ == "__main__":
    main()
