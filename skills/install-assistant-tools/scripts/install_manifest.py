#!/usr/bin/env python3
"""Install manifest: a home-scoped record of every install side effect.

install.py / setup_symlinks.py / setup_tools.py record what they change here;
uninstall.py replays the manifest in reverse. This makes uninstall exact even
when the installing tree is gone (e.g. an old plugin-cache version dir).

Schema (JSON):
    {"version": 1, "entries": [{"kind": ..., "path": ..., ...}, ...]}

Entry kinds:
    symlink            {path, target}
    marker_block       {path, begin, end}
    json_hook_commands {path, commands: [str]}
    git_hooks_path     {path: repo_root}
    file               {path}
    config_dir         {path, purge_only: true}
    pip_editable       {path: package name}
    registry_env       {path: bin_dir, names: [env var names]}
"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST_VERSION = 1


def manifest_path(home: Path) -> Path:
    """Canonical manifest location for a given home directory."""
    return home / ".local" / "state" / "assistant-tools" / "install-manifest.json"


class Manifest:
    """Load/record/save install side effects. Dedupes on (kind, path)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.entries: list[dict] = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                entries = data.get("entries", [])
                if isinstance(entries, list):
                    self.entries = [e for e in entries if isinstance(e, dict)]
            except (OSError, json.JSONDecodeError):
                self.entries = []

    def record(self, kind: str, *, path: str, **fields: object) -> None:
        entry = {"kind": kind, "path": path, **fields}
        for i, existing in enumerate(self.entries):
            if existing.get("kind") == kind and existing.get("path") == path:
                self.entries[i] = entry
                return
        self.entries.append(entry)

    def remove(self, entry: dict) -> None:
        self.entries = [e for e in self.entries if e is not entry]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": MANIFEST_VERSION, "entries": self.entries}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
