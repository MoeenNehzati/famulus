#!/usr/bin/env python3
"""
scaffold.py — Install the universal dispatcher/invoke-skill launchers.

This is the Phase-1 floor: every skill's SKILL.md invokes scripts via a bare
`dispatcher --caller-skill ...` command, and recurring-tasks systemd/cron
jobs invoke `invoke-skill <name>`. Both need to exist and be on PATH
regardless of plugin vs dev-mode, and regardless of which agent launchers
(assistant/collab/coauthor/tw) the user wants. Run this first, always.

Required third-party Python packages declared by blueprint executable
interfaces are provisioned into the managed-runtime release venv by
officina.install.managed_runtime.build_candidate_release, which
_phase_entry.py calls before this scaffold step runs at all -- scaffold no
longer installs packages into the ambient Python itself (see
warn_if_managed_release_missing for the lightweight, non-blocking sanity
check that a managed release exists).

Does NOT set ASSISTANT_DEFAULT (see launchers.py) or AI (see dev_link.py) —
this subcommand only owns PATH.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if not __package__ and str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from officina.runtime.python_machine_interface import PythonArgvMachineInterface
from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.managed_runtime import declared_python_packages

if __package__:
    from ._install_launcher import LauncherInstallResult, platform_launcher_installer
else:
    from _install_launcher import LauncherInstallResult, platform_launcher_installer
if __package__:
    from ._state_record import Manifest, manifest_path
else:
    from _state_record import Manifest, manifest_path
if __package__:
    from ._shell_block import ensure_rc_vars
else:
    from _shell_block import ensure_rc_vars
if __package__:
    from ._fs_links import default_bin_dir
else:
    from _fs_links import default_bin_dir


def log(msg: str = "") -> None:
    print(msg, flush=True)


RUNTIME_DEPENDENCIES_MANIFEST = Path("references") / "blueprint" / "runtime_dependencies.json"


def _platform_name() -> str | None:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return None


# uv's real per-OS release-asset target-triple suffixes and archive formats
# (confirmed against the real 0.11.29 GitHub release asset list). This table
# -- and the machine-architecture aliasing below -- is the one place that
# translates _platform_name()'s generic vocabulary into uv's concrete
# release-asset naming; officina.install.uv_bootstrap itself stays
# platform-name-free and only ever sees the already-resolved triple/archive
# pair this function returns.
_UV_TRIPLE_OS_SUFFIX = {
    "macos": "apple-darwin",
    "linux": "unknown-linux-gnu",
    "windows": "pc-windows-msvc",
}

# Normalizes platform.machine()'s real reported values (which differ by
# platform: e.g. macOS Apple Silicon reports "arm64", Linux reports
# "aarch64", Windows reports "AMD64") to uv's release-asset arch tokens.
_UV_MACHINE_ALIASES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
}

_UV_WINDOWS_ARCHIVE_EXTENSION = ".zip"
_UV_POSIX_ARCHIVE_EXTENSION = ".tar.gz"


class UvReleaseTargetError(ValueError):
    """Raised when a platform/machine combination has no known uv release asset."""


def uv_release_target(*, platform_name: str, machine: str) -> tuple[str, str]:
    """Resolve the real uv release-asset (target-triple, archive-extension)
    pair for ``platform_name`` (this module's own vocabulary: macos/linux/
    windows) and ``machine`` (platform.machine()'s real reported value).

    Raises UvReleaseTargetError for any unsupported platform or machine
    architecture.
    """
    suffix = _UV_TRIPLE_OS_SUFFIX.get(platform_name)
    if suffix is None:
        raise UvReleaseTargetError(
            f"unsupported platform for uv bootstrap: {platform_name!r} "
            f"(supported: {sorted(_UV_TRIPLE_OS_SUFFIX)})"
        )
    arch = _UV_MACHINE_ALIASES.get(machine.casefold())
    if arch is None:
        raise UvReleaseTargetError(
            f"unsupported machine architecture for uv bootstrap: {machine!r} "
            f"(supported: {sorted(set(_UV_MACHINE_ALIASES.values()))})"
        )
    triple = f"{arch}-{suffix}"
    extension = (
        _UV_WINDOWS_ARCHIVE_EXTENSION
        if platform_name == "windows"
        else _UV_POSIX_ARCHIVE_EXTENSION
    )
    return triple, extension


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


def warn_if_managed_release_missing(*, home: Path) -> None:
    """Log an advisory (non-blocking) note if no managed-runtime release is
    active yet.

    Scaffold no longer installs third-party Python packages itself: they are
    provisioned into the managed release venv by
    ``officina.install.managed_runtime.build_candidate_release``, which
    ``_phase_entry.py`` calls before ``scaffold.run`` runs at all. This is a
    lightweight sanity check for that call-order assumption, not a second
    install — scaffold proceeds regardless, since it is also called directly
    (bypassing `_phase_entry.py`) by targeted-repair invocations and tests.
    """
    try:
        current_pointer = resolve_famulus_paths(platform=sys.platform, home=home).current_pointer
    except Exception:
        return
    if not current_pointer.exists():
        log(
            "  NOTE: no managed-runtime release is active yet "
            f"({current_pointer} not found). dispatcher/invoke-skill will "
            "not work until officina.install.managed_runtime.build_candidate_release "
            "has run (see _phase_entry.py)."
        )


def _import_certificate_records():
    """Import point for ``officina.common.certificate_records``, isolated so
    ``install_certificate_signing_material`` can be tested against a missing
    ``cryptography`` dependency without actually uninstalling it (see that
    function's ``ModuleNotFoundError`` handling below).
    """
    from officina.common.certificate_records import (
        certificate_public_key_root,
        provision_certificate_signing_material,
    )

    return certificate_public_key_root, provision_certificate_signing_material


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
        certificate_public_key_root, provision_certificate_signing_material = (
            _import_certificate_records()
        )
    except ModuleNotFoundError as exc:
        # certificate_records.py imports `cryptography` at module level, and
        # this runs in-process against the installer's own ambient
        # interpreter (not the managed-runtime venv that build_candidate_
        # release provisions packages into -- see the module docstring).
        # On a fresh machine whose ambient Python never had `cryptography`
        # installed, fail with a clear, actionable message instead of a
        # confusing raw traceback. Deliberately does NOT pip-install into
        # the ambient interpreter: that ambient-mutation anti-pattern was
        # removed elsewhere in this same effort (see module docstring).
        return LauncherInstallResult(
            name="certificate-signing-material",
            required=True,
            status="failed",
            workflows=workflows,
            reason=(
                f"missing module {exc.name!r}: cryptography is not installed in the "
                "interpreter running this installer -- install it with "
                "`pip install cryptography`, or run the installer with a Python "
                "environment that already has it"
            ),
        )
    try:
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

    if not dry_run:
        warn_if_managed_release_missing(home=home)

    declared_packages = required_python_packages(repo_root)
    launcher_installer = platform_launcher_installer()
    capability_results = [
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
