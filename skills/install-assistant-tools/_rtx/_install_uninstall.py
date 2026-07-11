#!/usr/bin/env python3
"""
uninstall.py — Reverse the side effects of install.py by replaying the
install manifest.

Manifest-based only: every install records its side effects in a manifest
under the home's state dir, and uninstall undoes exactly those entries.
If the manifest is missing (pre-manifest install, or deleted by hand),
uninstall refuses and asks for one idempotent re-run of the installer to
regenerate it — guessing at artifacts by pattern is how live generated
files were deleted in the past.

Best-effort within the replay: attempts every reversal, never aborts on
failure, and prints a final report of what was removed, skipped, left
behind, or FAILED (with the reason). Exits non-zero if anything failed.

Left alone unless --purge: OAuth credentials and service configs under
~/.config/cloud-files and ~/.config/g-calendar (their manifest entries are
kept for a future --purge run).

Never reversed (reported): local skills previously migrated into the repo's
skills tree, worker dirs (may contain data), installed Python dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _state_record import Manifest, manifest_path  # noqa: E402
from _shell_block import (  # noqa: E402
    BLOCK_BEGIN,
    BLOCK_END,
)

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
    path: Path, begin: str, end: str, label: str, report: Report, dry_run: bool
) -> None:
    """Remove the begin/end-delimited managed block from a text file."""
    if not path.exists():
        report.add("skipped", f"{label}: {path}", "file does not exist")
        return
    try:
        original = path.read_text(encoding="utf-8")
    except OSError as exc:
        report.add("FAILED", f"{label}: {path}", f"could not read: {exc}")
        return
    if begin not in original:
        report.add("skipped", f"{label}: {path}", "no managed block found")
        return
    if dry_run:
        print(f"Would strip managed block from {path}")
        report.add("removed", f"{label}: {path}", "(dry-run)")
        return

    lines = original.splitlines(keepends=True)
    filtered: list[str] = []
    inside = False
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped == begin:
            inside = True
            # the installer writes a blank separator line before the block;
            # drop it too so stripping restores the file exactly
            if filtered and not filtered[-1].strip():
                filtered.pop()
            continue
        if stripped == end:
            inside = False
            continue
        if not inside:
            filtered.append(line)
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".tmp.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(filtered)
        os.replace(tmp, path)
        report.add("removed", f"{label}: {path}", "managed block stripped")
    except OSError as exc:
        report.add("FAILED", f"{label}: {path}", f"could not write: {exc}")


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


# ── Steps ─────────────────────────────────────────────────────────────────────

def uninstall_git_hooks(repo_root: Path, report: Report, dry_run: bool) -> None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--get", "core.hooksPath"],
            capture_output=True, text=True, encoding="utf-8", errors="strict",
        )
    except OSError as exc:
        report.add("FAILED", "git core.hooksPath", f"git unavailable: {exc}")
        return
    if result.returncode != 0 or result.stdout.strip() != ".githooks":
        report.add("skipped", "git core.hooksPath", "not set to .githooks")
        return
    if dry_run:
        print(f"Would unset core.hooksPath in {repo_root}")
        report.add("removed", "git core.hooksPath", "(dry-run)")
        return
    unset = subprocess.run(
        ["git", "-C", str(repo_root), "config", "--unset", "core.hooksPath"],
        capture_output=True, text=True, encoding="utf-8", errors="strict",
    )
    if unset.returncode == 0:
        report.add("removed", "git core.hooksPath")
    else:
        report.add("FAILED", "git core.hooksPath", unset.stderr.strip())


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
    # If nothing but empty structure remains, the file is only a husk of our
    # managed entries — remove it entirely (whether emptied by this run or
    # already empty from an install with no registered hooks).
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


def remove_registry_env(entry: dict, report: Report, dry_run: bool) -> bool:
    if sys.platform != "win32":
        report.add("skipped", "windows registry env", "not on Windows")
        return True
    if dry_run:
        print("Would remove registry PATH entry and env vars")
        report.add("removed", "windows registry env", "(dry-run)")
        return True
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            bin_str = entry.get("path", "")
            try:
                current_path, path_type = winreg.QueryValueEx(key, "PATH")
                parts = [p for p in current_path.split(";") if p and p != bin_str]
                winreg.SetValueEx(key, "PATH", 0, path_type, ";".join(parts))
            except FileNotFoundError:
                pass
            for name in entry.get("names", []):
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        report.add("removed", "windows registry env", f"PATH entry + {entry.get('names')}")
        return True
    except OSError as exc:
        report.add("FAILED", "windows registry env", str(exc))
        return False


def replay_manifest(
    manifest: Manifest,
    report: Report,
    *,
    dry_run: bool,
    purge: bool,
    no_pip: bool,
    no_git_hooks: bool,
) -> None:
    """Undo every manifest entry; settled entries are dropped from the manifest."""
    for entry in list(manifest.entries):
        kind = entry.get("kind")
        path = entry.get("path", "")
        settled = True
        if kind == "symlink":
            settled = remove_manifest_symlink(entry, report, dry_run)
        elif kind == "marker_block":
            before = report.failed
            strip_marker_block(
                Path(path), entry.get("begin", BLOCK_BEGIN), entry.get("end", BLOCK_END),
                "managed block", report, dry_run,
            )
            # If stripping leaves the file blank, it existed only for our
            # block — remove the empty config file husk we created.
            stripped_file = Path(path)
            if (
                not dry_run
                and stripped_file.is_file()
                and not stripped_file.read_text(encoding="utf-8").strip()
            ):
                remove_file(stripped_file, "managed block file (emptied)", report, dry_run)
            settled = report.failed == before
        elif kind == "json_hook_commands":
            settled = remove_manifest_json_hooks(entry, report, dry_run)
        elif kind == "git_hooks_path":
            if no_git_hooks:
                report.add("skipped", "git core.hooksPath", "--no-git-hooks")
            else:
                before = report.failed
                uninstall_git_hooks(Path(path), report, dry_run)
                settled = report.failed == before
        elif kind == "file":
            before = report.failed
            remove_file(Path(path), "generated file", report, dry_run)
            settled = report.failed == before
        elif kind == "config_dir":
            if purge:
                before = report.failed
                remove_tree(Path(path), "config/credentials", report, dry_run)
                settled = report.failed == before
            else:
                if Path(path).exists():
                    report.add(
                        "left", f"config/credentials: {path}",
                        "user data; re-run with --purge to remove",
                    )
                settled = False  # keep in manifest for a future --purge run
        elif kind == "pip_editable":
            if no_pip:
                report.add("skipped", f"pip package {path}", "--no-pip")
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


# ── Entry point ───────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--home", metavar="DIR")
    parser.add_argument("--claude-home", metavar="DIR")
    parser.add_argument("--codex-home", metavar="DIR")
    parser.add_argument("--bin-dir", metavar="DIR")
    parser.add_argument("--shell-rc", metavar="FILE")
    parser.add_argument("--system-shell-rc", metavar="FILE", default="/etc/bash.bashrc")
    parser.add_argument("--no-system-shell-rc", action="store_true")
    parser.add_argument("--repo-root", metavar="DIR")
    parser.add_argument("--manifest", metavar="FILE",
        help="Install manifest to replay (default: <home>/.local/state/assistant-tools/install-manifest.json)")
    parser.add_argument("--no-pip", action="store_true",
        help="Do not uninstall the script_dispatcher pip package")
    parser.add_argument("--no-git-hooks", action="store_true",
        help="Do not unset git core.hooksPath")
    parser.add_argument("--purge", action="store_true",
        help="Also remove OAuth credentials and service configs")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    home = Path(args.home) if args.home else Path.home()
    repo_root = Path(args.repo_root) if args.repo_root else REPO_ROOT_DEFAULT
    claude_home = Path(args.claude_home or os.environ.get("CLAUDE_HOME") or home / ".claude")
    codex_home = Path(args.codex_home or os.environ.get("CODEX_HOME") or home / ".codex")
    bin_dir = Path(args.bin_dir) if args.bin_dir else home / "Documents" / "_rtx" / "bin"

    if args.shell_rc:
        shell_rc = Path(args.shell_rc)
    elif sys.platform != "win32":
        shell_rc = home / (".zshrc" if "zsh" in os.environ.get("SHELL", "") else ".bashrc")
    else:
        shell_rc = None

    report = Report()
    dry_run = args.dry_run

    manifest = Manifest(Path(args.manifest) if args.manifest else manifest_path(home))
    if manifest.entries:
        print(f"Replaying install manifest: {manifest.path}")
        replay_manifest(
            manifest, report,
            dry_run=dry_run, purge=args.purge,
            no_pip=args.no_pip, no_git_hooks=args.no_git_hooks,
        )
        report.add(
            "left", f"worker dirs: {repo_root / 'workers'}",
            "may contain session data; remove manually if unwanted",
        )
        report.print()
        sys.exit(1 if report.failed else 0)

    # No heuristic fallback: guessing at installed artifacts by pattern is
    # how live generated files got deleted in the past. The installer is
    # idempotent and always writes a manifest, so the fix is one re-run —
    # this also covers a manifest that was deleted by hand.
    print(
        f"error: no install manifest found at {manifest.path}.\n"
        "Uninstall is manifest-based. Re-run install-assistant-tools once to\n"
        "regenerate the manifest (the install is idempotent), then uninstall.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
