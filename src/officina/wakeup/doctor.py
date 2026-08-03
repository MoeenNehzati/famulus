"""Read-only capability diagnostics for wakeup deployments."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from .linux_osx_windows import scheduler_capability
from .locking import locked_file
from .providers import all_providers
from .store import data_dir


@dataclass(frozen=True)
class Diagnostic:
    """One named capability result rendered by the doctor command."""

    name: str
    ok: bool
    detail: str


def collect_diagnostics() -> list[Diagnostic]:
    """Inspect provider, queue, locking, and scheduler capabilities read-only."""

    results: list[Diagnostic] = []
    for adapter in all_providers():
        executable = adapter.executable_override() or shutil.which(adapter.name)
        root = adapter.transcript_root()
        results.append(
            Diagnostic(
                f"provider:{adapter.name}",
                bool(executable) and root.is_dir(),
                f"executable={executable or 'missing'} transcripts={root}",
            )
        )
    root = data_dir()
    writable_parent = root if root.exists() else root.parent
    results.append(
        Diagnostic("queue", os.access(writable_parent, os.W_OK), str(root))
    )
    try:
        with locked_file(root / "doctor.lock", blocking=False):
            pass
        lock_result = Diagnostic("locking", True, "available")
    except OSError as error:
        lock_result = Diagnostic("locking", False, str(error))
    results.append(lock_result)
    scheduler_ok, scheduler_detail = scheduler_capability()
    results.append(Diagnostic("scheduler", scheduler_ok, scheduler_detail))
    return results


def render_diagnostics() -> int:
    """Print diagnostics and return nonzero when a required capability is absent."""

    diagnostics = collect_diagnostics()
    for item in diagnostics:
        print(f"{'OK' if item.ok else 'MISSING'} {item.name}: {item.detail}")
    return 0 if all(item.ok for item in diagnostics) else 1
