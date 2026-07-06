#!/usr/bin/env python3
"""
uninstall.py — Reverse the side effects of install.py.

Best-effort: attempts every reversal, never aborts on failure, and prints a
final report of what was removed, skipped, left behind, or FAILED (with the
reason). Exits non-zero if anything failed.

Reverses:
  - Claude/Codex config-dir symlinks into the repo (skills, references,
    agents, CLAUDE.md/AGENTS.md, profile links)
  - bin symlinks (assistant, collab, coauthor, tmux-workspace, tw, .bat)
  - managed shell-rc blocks (user and system)
  - managed Codex hooks block in config.toml
  - managed Claude hook entries in settings.local.json
  - git core.hooksPath registration
  - recurring-tasks env.sh and the systemd AI-agent environment file
  - editable script_dispatcher pip install

Left alone unless --purge: OAuth credentials and service configs under
~/.config/cloud-files and ~/.config/g-calendar.

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

from install_manifest import Manifest, manifest_path  # noqa: E402
from setup_tools import (  # noqa: E402
    BAT_WRAPPERS,
    BIN_SCRIPTS,
    BLOCK_BEGIN,
    BLOCK_END,
    GOOGLE_OAUTH_SERVICE_ORDER,
    HOOKS_BLOCK_BEGIN,
    HOOKS_BLOCK_END,
)

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[3]

CLAUDE_LINK_NAMES = ["skills", "references", "agents", "CLAUDE.md"]
CODEX_LINK_NAMES = ["skills", "references", "agents", "AGENTS.md"]


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

def points_into(link: Path, root: Path) -> bool:
    """True if symlink `link` resolves to a path inside `root`."""
    try:
        return link.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def remove_repo_link(link: Path, repo_root: Path, report: Report, dry_run: bool) -> None:
    """Remove `link` if it is a symlink into the repo; otherwise leave and note."""
    if not link.is_symlink():
        if link.exists():
            report.add("skipped", str(link), "exists but is not a symlink")
        return
    if not points_into(link, repo_root):
        report.add("skipped", str(link), "symlink does not point into repo")
        return
    if dry_run:
        print(f"Would remove symlink {link}")
        report.add("removed", str(link), "(dry-run)")
        return
    try:
        link.unlink()
        report.add("removed", str(link))
    except OSError as exc:
        report.add("FAILED", str(link), f"could not unlink: {exc}")


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

def uninstall_home_links(
    claude_home: Path, codex_home: Path, repo_root: Path, report: Report, dry_run: bool
) -> None:
    for name in CLAUDE_LINK_NAMES:
        remove_repo_link(claude_home / name, repo_root, report, dry_run)
    for name in CODEX_LINK_NAMES:
        remove_repo_link(codex_home / name, repo_root, report, dry_run)
    # profile links (both homes) and Claude settings profile links.
    # .config.toml profiles are COPIES since the installer stopped
    # symlinking them (the tool writes machine-local state back into the
    # file); legacy installs may still have symlinks. Handle both: a
    # non-symlink file is treated as an installed copy iff a profile of
    # the same name exists in the repo.
    for home in (claude_home, codex_home):
        for pattern in ("*.config.toml", "*_claude_setting.json"):
            for entry in sorted(home.glob(pattern)):
                if entry.is_symlink():
                    remove_repo_link(entry, repo_root, report, dry_run)
                elif (repo_root / "profiles" / entry.name).exists():
                    remove_file(entry, "profile copy", report, dry_run)
                else:
                    report.add("skipped", str(entry), "no matching repo profile; not ours")


def uninstall_bin_links(bin_dir: Path, repo_root: Path, report: Report, dry_run: bool) -> None:
    for name in BIN_SCRIPTS + BAT_WRAPPERS + ["tw"]:
        remove_repo_link(bin_dir / name, repo_root, report, dry_run)
    # dispatcher is a GENERATED launcher, not a symlink (first-party code
    # runs from the repo; see setup_tools.install_dispatcher_launcher).
    # Identify it by its generation marker before removing.
    launcher = bin_dir / "dispatcher"
    if launcher.is_file() and not launcher.is_symlink():
        try:
            generated = "Generated by install-assistant-tools" in launcher.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            generated = False
        if generated:
            remove_file(launcher, "dispatcher launcher", report, dry_run)
        else:
            report.add("skipped", str(launcher), "not our generated launcher")


def uninstall_claude_hooks(claude_home: Path, repo_root: Path, report: Report, dry_run: bool) -> None:
    settings_file = claude_home / "settings.local.json"
    if not settings_file.exists():
        report.add("skipped", f"claude hooks: {settings_file}", "does not exist")
        return
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.add("FAILED", f"claude hooks: {settings_file}", f"could not parse: {exc}")
        return

    # Managed commands: registry bindings when importable, plus legacy command.
    commands: set[str] = {f'python3 "{repo_root / "hooks" / "inject_dispatcher_context.py"}"'}
    try:
        from setup_tools import _hook_commands_to_replace
        commands |= _hook_commands_to_replace(repo_root, "claude")
    except Exception as exc:  # registry import may fail on partial installs
        report.add("skipped", "claude hook registry", f"using legacy command only: {exc}")

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        report.add("skipped", f"claude hooks: {settings_file}", "no hooks section")
        return

    changed = False
    for event_name in list(hooks.keys()):
        entries = hooks.get(event_name)
        if not isinstance(entries, list):
            continue
        kept = [
            entry for entry in entries
            if not any(
                isinstance(hook, dict) and hook.get("command", "") in commands
                for hook in entry.get("hooks", [])
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
        remove_file(settings_file, "claude settings (emptied)", report, dry_run)
        return
    if not changed:
        report.add("skipped", f"claude hooks: {settings_file}", "no managed entries found")
        return
    if dry_run:
        print(f"Would remove managed hook entries from {settings_file}")
        report.add("removed", f"claude hooks: {settings_file}", "(dry-run)")
        return
    try:
        fd, tmp = tempfile.mkstemp(dir=settings_file.parent, prefix=settings_file.name + ".tmp.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        os.replace(tmp, settings_file)
        report.add("removed", f"claude hooks: {settings_file}", "managed entries removed")
    except OSError as exc:
        report.add("FAILED", f"claude hooks: {settings_file}", f"could not write: {exc}")


def uninstall_git_hooks(repo_root: Path, report: Report, dry_run: bool) -> None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--get", "core.hooksPath"],
            capture_output=True, text=True,
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
        capture_output=True, text=True,
    )
    if unset.returncode == 0:
        report.add("removed", "git core.hooksPath")
    else:
        report.add("FAILED", "git core.hooksPath", unset.stderr.strip())


def uninstall_systemd_env(home: Path, report: Report, dry_run: bool) -> None:
    if sys.platform == "win32":
        return
    remove_file(
        home / ".config" / "environment.d" / "20-ai-agent.conf",
        "ai-agent env", report, dry_run,
    )
    # Only touch the live systemd session for the real $HOME (mirror of install).
    if dry_run or home.expanduser().resolve() != Path.home().resolve():
        return
    if shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", "--user", "unset-environment", "AI_AGENT_COMMAND_TEMPLATE"],
            check=False, capture_output=True,
        )
        report.add("removed", "systemd AI_AGENT_COMMAND_TEMPLATE", "best-effort unset")


def uninstall_pip_package(report: Report, dry_run: bool) -> None:
    if dry_run:
        print("Would pip uninstall script_dispatcher")
        report.add("removed", "pip package script_dispatcher", "(dry-run)")
        return
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", "script_dispatcher"],
        capture_output=True, text=True,
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


def purge_service_configs(home: Path, purge: bool, report: Report, dry_run: bool) -> None:
    for svc in GOOGLE_OAUTH_SERVICE_ORDER:
        config_dir = home / ".config" / svc
        if purge:
            remove_tree(config_dir, f"{svc} config/credentials", report, dry_run)
        elif config_dir.exists():
            report.add(
                "left", f"{svc} config/credentials: {config_dir}",
                "user data; re-run with --purge to remove",
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
    if str(Path(actual_target)) != str(Path(recorded_target)):
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
            # block — remove the husk (e.g. codex config.toml we created).
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
    bin_dir = Path(args.bin_dir) if args.bin_dir else home / "Documents" / "scripts" / "bin"

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

    print("No install manifest found — falling back to heuristic uninstall.")
    uninstall_home_links(claude_home, codex_home, repo_root, report, dry_run)
    uninstall_bin_links(bin_dir, repo_root, report, dry_run)
    if shell_rc is not None:
        strip_marker_block(shell_rc, BLOCK_BEGIN, BLOCK_END, "shell rc", report, dry_run)
    if not args.no_system_shell_rc and sys.platform != "win32":
        strip_marker_block(
            Path(args.system_shell_rc), BLOCK_BEGIN, BLOCK_END, "system rc", report, dry_run
        )
    strip_marker_block(
        codex_home / "config.toml", HOOKS_BLOCK_BEGIN, HOOKS_BLOCK_END,
        "codex hooks", report, dry_run,
    )
    # If stripping the managed block leaves the codex config empty, the file
    # existed only for our block — remove the husk.
    codex_config = codex_home / "config.toml"
    if (
        not dry_run
        and codex_config.is_file()
        and not codex_config.read_text(encoding="utf-8").strip()
    ):
        remove_file(codex_config, "codex config (emptied)", report, dry_run)
    uninstall_claude_hooks(claude_home, repo_root, report, dry_run)
    if not args.no_git_hooks:
        uninstall_git_hooks(repo_root, report, dry_run)
    # recurring-tasks env.sh is generated by the installer inside the repo
    remove_file(
        repo_root / "skills" / "recurring-tasks" / "scripts" / "env.sh",
        "recurring-tasks env.sh", report, dry_run,
    )
    uninstall_systemd_env(home, report, dry_run)
    if not args.no_pip:
        uninstall_pip_package(report, dry_run)
    purge_service_configs(home, args.purge, report, dry_run)

    report.add(
        "left", f"worker dirs: {repo_root / 'workers'}",
        "may contain session data; remove manually if unwanted",
    )

    report.print()
    sys.exit(1 if report.failed else 0)


if __name__ == "__main__":
    main()
