#!/usr/bin/env python3
"""
scaffold.py — Install the universal dispatcher/invoke-skill launchers.

This is the Phase-1 floor: every skill's SKILL.md invokes scripts via a bare
`dispatcher --caller-skill ...` command, and recurring-tasks systemd/cron
jobs invoke `invoke-skill <name>`. Both need to exist and be on PATH
regardless of plugin vs dev-mode, and regardless of which agent launchers
(assistant/collab/coauthor/tw) the user wants. Run this first, always.

Also installs required third-party Python packages declared by blueprint
executable interfaces, not tied to any particular agent.

Does NOT set ASSISTANT_DEFAULT (see launchers.py) or AI (see dev_link.py) —
this subcommand only owns PATH.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from _install_launcher import LauncherInstallResult, platform_launcher_installer
from _state_record import Manifest, manifest_path
from _shell_block import ensure_rc_vars


def log(msg: str = "") -> None:
    print(msg, flush=True)


RUNTIME_DEPENDENCIES_MANIFEST = Path("references") / "blueprint" / "runtime_dependencies.json"
DEFAULT_PIP_INSTALL_TIMEOUT_SECONDS = 60


def required_python_packages(repo_root: Path) -> list[str]:
    packages: set[str] = set()
    manifest = repo_root / RUNTIME_DEPENDENCIES_MANIFEST
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        manifest_packages = data.get("all", {}).get("python", [])
        if isinstance(manifest_packages, list):
            packages.update(package for package in manifest_packages if isinstance(package, str) and package)
    return sorted(packages, key=str.lower)


def pip_install_timeout_seconds() -> int:
    raw = os.environ.get("FAMULUS_PIP_INSTALL_TIMEOUT_SECONDS", "")
    if not raw:
        return DEFAULT_PIP_INSTALL_TIMEOUT_SECONDS
    try:
        timeout = int(raw)
    except ValueError:
        return DEFAULT_PIP_INSTALL_TIMEOUT_SECONDS
    return max(1, timeout)


def install_python_packages(repo_root: Path, dry_run: bool) -> None:
    """Ensure required third-party Python packages are installed.

    officina.dispatcher itself (first-party) is deliberately NOT pip-installed
    here — it runs from the repo via the dispatcher launcher below.
    """
    log("\nInstalling required Python packages...")
    timeout = pip_install_timeout_seconds()
    for package in required_python_packages(repo_root):
        if dry_run:
            log(f"  (dry-run) Would install: {package}")
            continue
        log(f"  Installing: {package}")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package, "--quiet"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=timeout,
            )
            if result.returncode == 0:
                log(f"  OK: {package}")
            else:
                log(f"  WARN: failed to install {package}: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            log(f"  WARN: timed out installing {package} after {timeout}s")


def report_capabilities(results: list[LauncherInstallResult]) -> int:
    """Print scaffold capability status and return the aggregate exit status."""
    if not results:
        return 0

    log("")
    log("Scaffold capability report:")
    for result in results:
        if result.blocks_install():
            label = "FAILED"
        elif result.status == "would-install":
            label = "WOULD-INSTALL"
        elif result.status == "installed":
            label = "OK"
        else:
            label = result.status.upper()

        log(f"  {label}: {result.name}")
        if result.reason:
            log(f"    reason: {result.reason}")
        log(f"    affected workflows: {', '.join(result.workflows)}")

    if any(result.blocks_install() for result in results):
        log("")
        log("Scaffold failed: required capabilities were not installed.")
        return 1
    return 0


def ensure_path_windows(bin_dir: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Add bin_dir to the Windows user PATH via the registry (PATH only)."""
    if dry_run:
        log(f"  Would add to user PATH (registry): {bin_dir}")
        return

    import winreg

    REG_PATH = "Environment"
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, REG_PATH, 0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    ) as key:
        try:
            current_path, path_type = winreg.QueryValueEx(key, "PATH")
        except FileNotFoundError:
            current_path, path_type = "", winreg.REG_EXPAND_SZ

        bin_str = str(bin_dir)
        parts = [p for p in current_path.split(";") if p]
        if bin_str not in parts:
            new_path = ";".join([bin_str] + parts)
            winreg.SetValueEx(key, "PATH", 0, path_type, new_path)
            log(f"  Added to user PATH: {bin_dir}")
        else:
            log(f"  User PATH already contains: {bin_dir}")

    if manifest is not None:
        manifest.record("registry_env", path=str(bin_dir), names=["PATH"])


def run(
    *,
    repo_root: Path,
    home: Path | None = None,
    bin_dir: Path | None = None,
    shell_rc: Path | None = None,
    dry_run: bool = False,
    manifest: Manifest | None = None,
) -> int:
    home = home or Path.home()
    bin_dir = bin_dir or home / "Documents" / "_rtx" / "bin"

    if manifest is None and not dry_run:
        manifest = Manifest(manifest_path(home))
    if dry_run:
        manifest = None

    install_python_packages(repo_root, dry_run)
    launcher_installer = platform_launcher_installer()
    capability_results = [
        launcher_installer.install_dispatcher_launcher(repo_root, bin_dir, dry_run, manifest),
        launcher_installer.install_invoke_skill_launcher(bin_dir, dry_run, manifest),
    ]

    if sys.platform == "win32":
        ensure_path_windows(bin_dir, dry_run, manifest)
    else:
        if shell_rc is None:
            detected_shell = os.environ.get("SHELL", "")
            shell_rc = home / (".zshrc" if "zsh" in detected_shell else ".bashrc")
        ensure_rc_vars(
            shell_rc,
            {"PATH": f'export PATH="{bin_dir}:$PATH"'},
            dry_run,
            manifest,
            label="user",
        )

    if manifest is not None:
        manifest.save()

    status = report_capabilities(capability_results)

    log("")
    log("Scaffold complete." if status == 0 else "Scaffold incomplete.")
    log(f"  Bin dir: {bin_dir}")
    return status


class Interface(PythonArgvMachineInterface):
    prog = "install_scaffold.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-root", metavar="DIR", required=True, help="Path to the AI repo checkout")
    parser.add_argument("--home", metavar="DIR", help="Home directory (default: platform home)")
    parser.add_argument("--bin-dir", metavar="DIR", help="Bin dir for launchers (default: ~/Documents/scripts/bin)")
    parser.add_argument("--shell-rc", metavar="FILE", help="Shell rc file (auto-detected on Unix)")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without writing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run(
        repo_root=Path(args.repo_root),
        home=Path(args.home) if args.home else None,
        bin_dir=Path(args.bin_dir) if args.bin_dir else None,
        shell_rc=Path(args.shell_rc) if args.shell_rc else None,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
