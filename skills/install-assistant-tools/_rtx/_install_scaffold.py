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
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from officina.runtime.python_machine_interface import PythonArgvMachineInterface
from officina.install.managed_runtime import declared_python_packages

from _install_launcher import LauncherInstallResult, platform_launcher_installer
from _state_record import Manifest, manifest_path
from _shell_block import ensure_rc_vars
from _fs_links import default_bin_dir


def log(msg: str = "") -> None:
    print(msg, flush=True)


RUNTIME_DEPENDENCIES_MANIFEST = Path("references") / "blueprint" / "runtime_dependencies.json"
DEFAULT_PIP_INSTALL_TIMEOUT_SECONDS = 60


def _platform_name() -> str | None:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return None


def _declares_package(spec: str, name: str) -> bool:
    """Return whether install spec ``spec`` (e.g. "cryptography>=44.0.1") is
    for the bare package ``name`` (case-insensitive)."""
    bare = re.split(r"(==|>=|<=|!=|~=|>|<)", spec, maxsplit=1)[0]
    return bare.strip().casefold() == name.casefold()


def required_python_packages(repo_root: Path) -> list[str]:
    """Return the declared python-package install specs for this platform,
    sourced from the shared officina.install.managed_runtime manifest reader.
    """
    manifest = repo_root / RUNTIME_DEPENDENCIES_MANIFEST
    platform_name = _platform_name()
    if not manifest.exists() or platform_name is None:
        return []
    return sorted(declared_python_packages(manifest, platform=platform_name), key=str.lower)


def pip_install_timeout_seconds() -> int:
    raw = os.environ.get("FAMULUS_PIP_INSTALL_TIMEOUT_SECONDS", "")
    if not raw:
        return DEFAULT_PIP_INSTALL_TIMEOUT_SECONDS
    try:
        timeout = int(raw)
    except ValueError:
        return DEFAULT_PIP_INSTALL_TIMEOUT_SECONDS
    return max(1, timeout)


def install_python_packages(repo_root: Path, dry_run: bool) -> LauncherInstallResult:
    """Ensure required third-party Python packages are installed, in one
    atomic batch call rather than a per-package best-effort loop.

    officina.dispatcher itself (first-party) is deliberately NOT pip-installed
    here — it runs from the repo via the dispatcher launcher below.

    Unlike the previous per-package WARN-and-continue loop, a failed batch
    install is reported as a failed, blocking capability rather than being
    silently swallowed.
    """
    workflows = ("skill python-package dependencies",)
    packages = required_python_packages(repo_root)
    log("\nInstalling required Python packages...")
    if dry_run:
        for package in packages:
            log(f"  (dry-run) Would install: {package}")
        return LauncherInstallResult(
            name="python-packages",
            required=True,
            status="would-install",
            workflows=workflows,
        )
    if not packages:
        return LauncherInstallResult(
            name="python-packages",
            required=True,
            status="installed",
            workflows=workflows,
        )
    log(f"  Installing {len(packages)} declared package(s) in one batch: {', '.join(packages)}")
    timeout = pip_install_timeout_seconds()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *packages, "--quiet"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        reason = f"timed out installing batch after {timeout}s"
        log(f"  FAILED: {reason}")
        return LauncherInstallResult(
            name="python-packages",
            required=True,
            status="failed",
            workflows=workflows,
            reason=reason,
        )
    if result.returncode != 0:
        reason = result.stderr.strip()
        log(f"  FAILED: batch package install failed: {reason}")
        return LauncherInstallResult(
            name="python-packages",
            required=True,
            status="failed",
            workflows=workflows,
            reason=reason,
        )
    log("  OK: installed all declared packages")
    return LauncherInstallResult(
        name="python-packages",
        required=True,
        status="installed",
        workflows=workflows,
    )


def install_certificate_signing_material(
    repo_root: Path,
    dry_run: bool,
) -> LauncherInstallResult:
    """Provision the existing certifier key lifecycle as one required capability."""

    workflows = ("v4 certification", "certificate verification")
    if dry_run:
        log("  (dry-run) Would provision certificate signing material")
        return LauncherInstallResult(
            name="certificate-signing-material",
            required=True,
            status="would-install",
            workflows=workflows,
        )
    try:
        from officina.common.certificate_records import (
            certificate_public_key_root,
            provision_certificate_signing_material,
        )

        provision_certificate_signing_material(repo_root)
        path = certificate_public_key_root(repo_root)
    except Exception as exc:
        return LauncherInstallResult(
            name="certificate-signing-material",
            required=True,
            status="failed",
            workflows=workflows,
            reason=str(exc),
        )
    return LauncherInstallResult(
        name="certificate-signing-material",
        required=True,
        status="installed",
        workflows=workflows,
        path=path,
    )


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
    bin_dir = bin_dir or default_bin_dir(home=home)

    if manifest is None and not dry_run:
        manifest = Manifest(manifest_path(home))
    if dry_run:
        manifest = None

    declared_packages = required_python_packages(repo_root)
    launcher_installer = platform_launcher_installer()
    capability_results = [
        install_python_packages(repo_root, dry_run),
        launcher_installer.install_dispatcher_launcher(repo_root, bin_dir, dry_run, manifest, home=home),
        launcher_installer.install_invoke_skill_launcher(bin_dir, dry_run, manifest),
    ]
    if any(_declares_package(package, "cryptography") for package in declared_packages):
        capability_results.append(
            install_certificate_signing_material(repo_root, dry_run)
        )

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
    parser.add_argument("--bin-dir", metavar="DIR", help="Bin dir for launchers (default: platform user-bin dir from officina.common.famulus_paths, e.g. ~/.local/bin on Linux/macOS)")
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
