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

import hashlib
import json
import os
import tempfile
from pathlib import Path

from officina.install.doctor import InstallManifestError, load_install_manifest

MANIFEST_VERSION = 2
SUPPORTED_MANIFEST_VERSIONS = (1, MANIFEST_VERSION)


def manifest_path(home: Path) -> Path:
    """Canonical manifest location for a given home directory."""
    return home / ".local" / "state" / "assistant-tools" / "install-manifest.json"


class Manifest:
    """Load/record/save install side effects. Dedupes on (kind, path)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.entries: list[dict] = []
        self.installation: dict[str, str] | None = None
        self.version = MANIFEST_VERSION
        if self.path.exists():
            data = load_install_manifest(self.path)
            version = data["version"]
            assert isinstance(version, int)
            self.version = version
            entries = data["entries"]
            assert isinstance(entries, list)
            self.entries = [dict(entry) for entry in entries]
            installation = data.get("installation")
            if isinstance(installation, dict):
                self.installation = {
                    str(key): str(value) for key, value in installation.items()
                }

    def bind_context(
        self,
        *,
        mode: str,
        installation_id: str,
        development_root: Path | None = None,
    ) -> None:
        """Bind this manifest to one selected installation before recording."""
        installation = {"mode": mode, "installation_id": installation_id}
        if development_root is not None:
            installation["development_root"] = str(
                Path(development_root).resolve(strict=False)
            )
        if self.installation is not None and self.installation != installation:
            if not self._rebase_moved_development_context(installation):
                raise ValueError("manifest belongs to a different installation context")
        self.installation = installation
        self.version = MANIFEST_VERSION
        self.save()

    @staticmethod
    def _rebase_value(value: object, *, old_root: Path, new_root: Path) -> object:
        if isinstance(value, str):
            candidate = Path(value)
            if not candidate.is_absolute():
                return value
            try:
                relative = candidate.relative_to(old_root)
            except ValueError:
                return value
            return str(new_root / relative)
        if isinstance(value, list):
            return [
                Manifest._rebase_value(item, old_root=old_root, new_root=new_root)
                for item in value
            ]
        if isinstance(value, dict):
            return {
                key: Manifest._rebase_value(item, old_root=old_root, new_root=new_root)
                for key, item in value.items()
            }
        return value

    def _rebase_moved_development_context(
        self, installation: dict[str, str]
    ) -> bool:
        prior = self.installation
        if (
            prior is None
            or prior.get("mode") != "development"
            or installation.get("mode") != "development"
            or prior.get("installation_id") != installation.get("installation_id")
        ):
            return False
        old_value = prior.get("development_root")
        new_value = installation.get("development_root")
        if not old_value or not new_value:
            return False
        old_root = Path(old_value)
        new_root = Path(new_value)
        if (
            not old_root.is_absolute()
            or not new_root.is_absolute()
            or old_root == new_root
            or old_root.exists()
        ):
            return False
        local_root = new_root / ".famulus"
        try:
            self.path.resolve(strict=False).relative_to(local_root.resolve(strict=False))
        except ValueError:
            return False
        try:
            recorded_id = (local_root / "install-id").read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if recorded_id != installation["installation_id"]:
            return False
        rebased = self._rebase_value(
            self.entries, old_root=old_root, new_root=new_root
        )
        assert isinstance(rebased, list)
        self.entries = rebased
        return True

    @staticmethod
    def _block_record_fields(path: Path, begin: str, end: str) -> dict[str, object]:
        try:
            lines = path.read_bytes().splitlines(keepends=True)
        except OSError:
            return {}
        begin_bytes = begin.encode("utf-8")
        end_bytes = end.encode("utf-8")
        selected: list[bytes] = []
        inside = False
        for index, line in enumerate(lines):
            if line.rstrip(b"\r\n") == begin_bytes:
                inside = True
            if inside:
                selected.append(line)
            if inside and line.rstrip(b"\r\n") == end_bytes:
                raw = b"".join(selected)
                result: dict[str, object] = {
                    "block_sha256": hashlib.sha256(raw).hexdigest()
                }
                start_index = index - len(selected) + 1
                if start_index > 0 and not lines[start_index - 1].strip():
                    separator = lines[start_index - 1]
                    result["separator_sha256"] = hashlib.sha256(separator).hexdigest()
                    result["separator_length"] = len(separator)
                return result
        return {}

    @staticmethod
    def tree_identity(root: Path) -> str:
        """Hash names, file bytes, and link targets for one owned tree."""
        digest = hashlib.sha256()
        for child in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            relative = child.relative_to(root).as_posix().encode("utf-8")
            digest.update(relative + b"\0")
            if child.is_symlink():
                digest.update(b"L\0" + os.readlink(child).encode("utf-8") + b"\0")
            elif child.is_file():
                digest.update(b"F\0" + child.read_bytes())
            elif child.is_dir():
                digest.update(b"D\0")
        return digest.hexdigest()

    def record(self, kind: str, *, path: str, **fields: object) -> None:
        artifact = Path(path)
        if kind in {"file", "launcher", "generated_config"} and "sha256" not in fields:
            try:
                if artifact.is_file() and not artifact.is_symlink():
                    fields["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            except OSError:
                pass
        if kind == "marker_block" and "block_sha256" not in fields:
            begin = fields.get("begin")
            end = fields.get("end")
            if isinstance(begin, str) and isinstance(end, str):
                fields.update(self._block_record_fields(artifact, begin, end))
        if kind == "json_hook_commands" and "commands_sha256" not in fields:
            commands = fields.get("commands")
            if isinstance(commands, list) and all(isinstance(item, str) for item in commands):
                raw = json.dumps(sorted(commands), separators=(",", ":")).encode("utf-8")
                fields["commands_sha256"] = hashlib.sha256(raw).hexdigest()
        if kind == "tree" and "tree_sha256" not in fields:
            try:
                if artifact.is_dir() and not artifact.is_symlink():
                    fields["tree_sha256"] = self.tree_identity(artifact)
            except OSError:
                pass
        if kind == "registry_env" and "values" not in fields and os.name == "nt":
            try:
                import winreg

                recorded_values: dict[str, object] = {}
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                    for name in fields.get("names", []):
                        if isinstance(name, str) and name.casefold() != "path":
                            try:
                                recorded_values[name] = winreg.QueryValueEx(key, name)[0]
                            except FileNotFoundError:
                                pass
                fields["values"] = recorded_values
            except OSError:
                pass
        if fields.get("preserve_if_modified") is True:
            fields.setdefault("purge_only", True)
        entry = {"kind": kind, "path": path, **fields}
        for i, existing in enumerate(self.entries):
            if existing.get("kind") == kind and existing.get("path") == path:
                self.entries[i] = entry
                break
        else:
            self.entries.append(entry)
        # Persist immediately: a mid-install crash must not lose the record
        # of side effects already applied (uninstall depends on it).
        self.save()

    def remove(self, entry: dict) -> None:
        self.entries = [e for e in self.entries if e is not entry]

    def forget(self, kind: str, *, path: str) -> None:
        """Drop a stale ownership record identified by kind and path."""
        remaining = [
            entry
            for entry in self.entries
            if not (entry.get("kind") == kind and entry.get("path") == path)
        ]
        if len(remaining) == len(self.entries):
            return
        self.entries = remaining
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.version = MANIFEST_VERSION if self.installation is not None else 1
        payload: dict[str, object] = {
            "version": self.version,
            "entries": self.entries,
        }
        if self.installation is not None:
            payload["installation"] = self.installation
        raw = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=self.path.name + ".tmp."
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if os.name == "posix":
                directory_descriptor = os.open(
                    self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
