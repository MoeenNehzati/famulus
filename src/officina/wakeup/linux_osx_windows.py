"""Host scheduler capability discovery for Linux, macOS, and Windows."""
from __future__ import annotations

import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from officina.common.atomic_files import atomic_replace_bytes
from officina.common.command_files import (
    CommandBundleSpec,
    CommandFileInstaller,
    CommandFileSpec,
    restore_command_file,
    snapshot_command_file,
)
from . import WakeupError

_OWNER = "famulus-llm-wakeup-owner.json"


def scheduler_capability() -> tuple[bool, str]:
    """Report the native scheduler command expected on the current host."""

    if sys.platform.startswith("linux"):
        command = "systemctl"
    elif sys.platform == "darwin":
        command = "launchctl"
    elif sys.platform == "win32":
        command = "schtasks"
    else:
        return False, f"unsupported platform: {sys.platform}"
    path = shutil.which(command)
    return (path is not None, path or f"{command} not found")


def _command_content(python: Path, plugin_root: Path, *, windows: bool) -> str:
    source = plugin_root / "src"
    if windows:
        return f'@echo off\r\nset "PYTHONPATH={source}"\r\n"{python}" -m officina.wakeup.cli %*\r\n'
    return (
        "#!/bin/sh\n"
        f"PYTHONPATH={shlex.quote(str(source))} "
        f"exec {shlex.quote(str(python))} "
        '-m officina.wakeup.cli "$@"\n'
    )


def _install_commands(
    python: Path,
    plugin_root: Path,
    bin_dir: Path,
    platform: str,
) -> None:
    suffix = ".bat" if platform == "win32" else ""
    content = _command_content(python, plugin_root, windows=platform == "win32")
    bundle = CommandBundleSpec(
        name="llm-wakeup",
        workflows=("guarded LLM session wakeups",),
        files=[
            CommandFileSpec(
                destination=bin_dir / f"{name}{suffix}",
                mode="generate",
                content=content,
                executable=platform != "win32",
            )
            for name in ("llm-wakeup", "lw")
        ],
    )
    CommandFileInstaller().install_bundle(bundle, dry_run=False, manifest=None)


def _paths(native_root: Path, platform: str) -> tuple[Path, ...]:
    if platform.startswith("linux"):
        return (
            native_root / "famulus-llm-wakeup.service",
            native_root / "famulus-llm-wakeup.timer",
        )
    if platform == "darwin":
        return (native_root / "com.famulus.llm-wakeup.plist",)
    return (native_root / "famulus-llm-wakeup-due.cmd",)


def _render_native(
    python: Path,
    plugin_root: Path,
    native_root: Path,
    platform: str,
) -> dict[Path, bytes]:
    source = plugin_root / "src"
    targets = _paths(native_root, platform)
    if platform.startswith("linux"):
        resources = Path(__file__).with_name("resources")
        executable = f'"{python}" -m officina.wakeup.cli'
        service = (
            (resources / "llm-wakeup.service.in")
            .read_text(encoding="utf-8")
            .replace("@LLM_WAKEUP_EXECUTABLE@", executable)
            .replace("@LLM_WAKEUP_SOURCE@", str(source))
        )
        timer = (resources / "llm-wakeup.timer").read_bytes()
        return {targets[0]: service.encode(), targets[1]: timer}
    if platform == "darwin":
        payload = {
            "Label": "com.famulus.llm-wakeup",
            "ProgramArguments": [
                str(python),
                "-m",
                "officina.wakeup.cli",
                "run-due",
            ],
            "EnvironmentVariables": {"PYTHONPATH": str(source)},
            "StartInterval": 600,
            "RunAtLoad": True,
        }
        return {targets[0]: plistlib.dumps(payload, sort_keys=True)}
    command = (
        f'@echo off\r\nset "PYTHONPATH={source}"\r\n'
        f'"{python}" -m officina.wakeup.cli run-due\r\n'
    )
    return {targets[0]: command.encode()}


def _run_native(
    native_root: Path,
    platform: str,
    *,
    remove: bool,
    run: Callable[..., object],
) -> None:
    targets = _paths(native_root, platform)
    if platform.startswith("linux"):
        if remove:
            run(
                ["systemctl", "--user", "disable", "--now", "famulus-llm-wakeup.timer"],
                check=False,
            )
        run(["systemctl", "--user", "daemon-reload"], check=True)
        if not remove:
            run(
                ["systemctl", "--user", "enable", "--now", "famulus-llm-wakeup.timer"],
                check=True,
            )
    elif platform == "darwin":
        domain = f"gui/{os.getuid()}"
        run(["launchctl", "bootout", domain, "com.famulus.llm-wakeup"], check=False)
        if not remove:
            run(["launchctl", "bootstrap", domain, str(targets[0])], check=True)
    else:
        name = "Famulus-LLMWakeup-Due"
        if remove:
            run(["schtasks", "/Delete", "/F", "/TN", name], check=False)
        else:
            run(
                [
                    "schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "10",
                    "/TN", name, "/TR", str(targets[0]),
                ],
                check=True,
            )


def setup_integration(
    *,
    python: Path,
    plugin_root: Path,
    bin_dir: Path,
    native_root: Path,
    platform: str | None = None,
    run: Callable[..., object] = subprocess.run,
) -> None:
    platform = sys.platform if platform is None else platform
    python, plugin_root = python.resolve(), plugin_root.resolve()
    if not python.is_file() or not python.is_absolute() or not plugin_root.is_dir():
        raise WakeupError("canonical Python and plugin root must be existing absolute paths")
    native = _render_native(python, plugin_root, native_root, platform)
    suffix = ".bat" if platform == "win32" else ""
    command_paths = tuple(bin_dir / f"{name}{suffix}" for name in ("llm-wakeup", "lw"))
    owner = native_root / _OWNER
    snapshots = {
        path: snapshot_command_file(path)
        for path in (*native, owner, *command_paths)
    }
    try:
        native_root.mkdir(parents=True, exist_ok=True)
        for path, raw in native.items():
            atomic_replace_bytes(path, raw, allowed_root=native_root, mode=0o600)
        _run_native(native_root, platform, remove=False, run=run)
        _install_commands(python, plugin_root, bin_dir, platform)
        owner_raw = (
            json.dumps({"python": str(python), "plugin_root": str(plugin_root)}) + "\n"
        ).encode()
        atomic_replace_bytes(
            owner,
            owner_raw,
            allowed_root=native_root,
            mode=0o600,
        )
    except Exception:
        try:
            _run_native(native_root, platform, remove=True, run=run)
        except Exception:
            pass
        for path, snapshot in snapshots.items():
            restore_command_file(path, snapshot)
        owner_snapshot = snapshots[owner]
        if owner_snapshot.content is not None or owner_snapshot.symlink_target is not None:
            _run_native(native_root, platform, remove=False, run=run)
        raise


def teardown_integration(
    *,
    native_root: Path,
    bin_dir: Path,
    platform: str | None = None,
    run: Callable[..., object] = subprocess.run,
) -> None:
    platform = sys.platform if platform is None else platform
    owner = native_root / _OWNER
    if not owner.is_file():
        return
    _run_native(native_root, platform, remove=True, run=run)
    suffix = ".bat" if platform == "win32" else ""
    commands = tuple(bin_dir / f"{name}{suffix}" for name in ("llm-wakeup", "lw"))
    for path in (*_paths(native_root, platform), owner, *commands):
        path.unlink(missing_ok=True)
