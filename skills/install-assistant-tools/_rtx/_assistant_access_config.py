#!/usr/bin/env python3
"""Reconcile narrow Famulus state roots into assistant user configuration."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from officina.common import codex_toml, toml_io
from officina.common.atomic_files import (
    AtomicWriteError,
    atomic_compare_and_delete,
    atomic_compare_and_replace_bytes,
    exclusive_file_lock,
    read_regular_file_bytes,
)
from officina.install.assistant_access import resolve_assistant_access_roots
from officina.install.context import InstallationContext

if TYPE_CHECKING:
    if __package__:
        from ._state_record import Manifest
    else:
        from _state_record import Manifest


ACCESS_BEGIN = "# >>> famulus-access >>>"
ACCESS_END = "# <<< famulus-access <<<"


class AssistantAccessConfigError(ValueError):
    """Assistant configuration is malformed, ambiguous, or concurrently edited."""


@dataclass(frozen=True)
class _ClaudePlan:
    path: Path
    expected: bytes | None
    replacement: bytes
    mode: int
    ownership: dict[str, object]


_UNSUPPORTED_DIRECTORY_SYNC = {errno.EINVAL, errno.ENOTSUP, errno.EBADF}


def _sha256(raw: bytes | None) -> str | None:
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def _read_optional(path: Path) -> bytes | None:
    try:
        parent = path.parent.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise AssistantAccessConfigError(
            f"assistant configuration parent is not a real directory: {path.parent}"
        )
    try:
        return read_regular_file_bytes(path, allowed_root=path.parent)
    except FileNotFoundError:
        return None
    except (OSError, AtomicWriteError) as exc:
        raise AssistantAccessConfigError(f"cannot read assistant configuration {path}: {exc}") from exc


def _load_json_object(raw: bytes, *, path: Path, label: str) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise AssistantAccessConfigError(
                    f"duplicate JSON object key {key!r} in {label} configuration {path}"
                )
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except AssistantAccessConfigError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssistantAccessConfigError(f"cannot parse {label} configuration {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AssistantAccessConfigError(f"{label} configuration must be an object: {path}")
    return payload


def _transform_claude(raw: bytes | None, *, path: Path, required: list[str], prior: dict[str, object] | None) -> tuple[bytes, dict[str, object]]:
    payload: object = {} if raw is None else _load_json_object(raw, path=path, label="Claude")
    permissions = payload.get("permissions")
    created_permissions = permissions is None
    if permissions is None:
        permissions = {}
        payload["permissions"] = permissions
    if not isinstance(permissions, dict):
        raise AssistantAccessConfigError(f"Claude permissions must be an object: {path}")
    values = permissions.get("additionalDirectories")
    created_key = values is None
    if values is None:
        values = []
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise AssistantAccessConfigError(f"Claude permissions.additionalDirectories must contain only strings: {path}")
    foreign = list(values)
    prior_owned = prior.get("introduced", []) if prior else []
    rebased_from = prior.get("rebase_from_introduced", []) if prior else []
    if isinstance(rebased_from, list):
        prior_owned = [*rebased_from, *prior_owned] if isinstance(prior_owned, list) else rebased_from
    if isinstance(prior_owned, list) and all(isinstance(value, str) for value in prior_owned):
        for owned in reversed(prior_owned):
            for index in range(len(foreign) - 1, -1, -1):
                if foreign[index] == owned:
                    foreign.pop(index)
                    break
    introduced = [value for value in required if value not in set(foreign)]
    permissions["additionalDirectories"] = foreign + introduced
    result = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return result, {
        "introduced": introduced,
        "json_path": ["permissions", "additionalDirectories"],
        "created_permissions": created_permissions or bool(prior and prior.get("created_permissions")),
        "created_key": created_key or bool(prior and prior.get("created_key")),
    }


def _find_entry(manifest: "Manifest", kind: str, path: Path) -> dict[str, object] | None:
    return next((entry for entry in manifest.entries if entry.get("kind") == kind and entry.get("path") == str(path)), None)


def _sync_directory(path: Path) -> None:
    if os.name != "posix": return
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        if exc.errno in _UNSUPPORTED_DIRECTORY_SYNC: return
        raise
    try:
        try: os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED_DIRECTORY_SYNC: raise
    finally: os.close(descriptor)


def _atomic_write_preserving_mode(path: Path, replacement: bytes | None, *, expected: bytes | None, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if replacement is None:
            if expected is None:
                raise AssistantAccessConfigError(f"assistant removal has no predecessor: {path}")
            atomic_compare_and_delete(path, expected_previous_bytes=expected, expected_previous_mode=mode, allowed_root=path.parent)
        else:
            atomic_compare_and_replace_bytes(path, replacement, expected_previous_bytes=expected, expected_previous_mode=None if expected is None else mode, allowed_root=path.parent, mode=mode)
    except AtomicWriteError as exc:
        raise AssistantAccessConfigError(f"assistant configuration changed before confined publication: {path}: {exc}") from exc


def _replace_if_unchanged(path: Path, *, expected: bytes | None, replacement: bytes, mode: int) -> None:
    _atomic_write_preserving_mode(path, replacement, expected=expected, mode=mode)


def _codex_plan(required: list[str], context: InstallationContext, prior: dict[str, object] | None) -> toml_io.ManagedArrayPlan:
    try:
        return codex_toml.plan_access_roots(context.codex_home, required, prior=prior, begin=ACCESS_BEGIN, end=ACCESS_END)
    except toml_io.TomlManagedArrayError as exc:
        raise AssistantAccessConfigError(str(exc)) from exc


def _preflight_codex(manifest: "Manifest", context: InstallationContext, required: list[str]) -> toml_io.ManagedArrayPlan:
    path = codex_toml.config_path(context.codex_home)
    prior = _find_entry(manifest, "codex_access_array_block", path)
    try:
        state = codex_toml.config_state(context.codex_home)
    except toml_io.TomlManagedArrayError as exc:
        raise AssistantAccessConfigError(str(exc)) from exc
    plan = _codex_plan(required, context, prior)
    if prior and prior.get("transaction") == "pending":
        if plan.current_sha256 not in {prior.get("pre_sha256"), prior.get("post_sha256")}:
            raise AssistantAccessConfigError(f"pending assistant configuration has neither its pre-state nor intended post-state: {path}")
        expected_mode = (
            prior.get("pre_mode", None if prior.get("pre_sha256") is None else prior.get("file_mode"))
            if state.sha256 == prior.get("pre_sha256")
            else prior.get("post_mode", prior.get("file_mode"))
        )
        if state.mode != expected_mode:
            raise AssistantAccessConfigError(f"pending assistant configuration mode differs from its recorded state: {path}")
        if plan.replacement_sha256 != prior.get("post_sha256"):
            raise AssistantAccessConfigError(f"pending assistant configuration intent changed: {path}")
    return plan


def _codex_record(manifest: "Manifest", plan: toml_io.ManagedArrayPlan) -> dict[str, object]:
    path = plan.path
    prior = _find_entry(manifest, "codex_access_array_block", path)
    if prior and prior.get("transaction") == "pending":
        return {key: value for key, value in prior.items() if key not in {"kind", "path"}}
    record = {
        "introduced": list(plan.introduced), "begin": ACCESS_BEGIN, "end": ACCESS_END,
        "created_table": plan.created_table, "created_key": plan.created_key,
        "block_sha256": plan.block_sha256, "transaction": "pending",
        "created_file": plan.created_file, "pre_sha256": plan.current_sha256,
        "post_sha256": plan.replacement_sha256,
        "pre_mode": None if plan.current_sha256 is None else plan.mode,
        "post_mode": plan.mode,
        "file_mode": plan.mode,
    }
    return record


def _apply_codex(manifest: "Manifest", plan: toml_io.ManagedArrayPlan, record: dict[str, object]) -> None:
    path = plan.path
    if plan.current_sha256 != plan.replacement_sha256:
        try: codex_toml.apply_access_plan(plan)
        except toml_io.TomlManagedArrayError as exc: raise AssistantAccessConfigError(str(exc)) from exc
    committed = {**record, "transaction": "committed"}
    manifest.record("codex_access_array_block", path=str(path), **committed)


def _plan_claude(manifest: "Manifest", path: Path, required: list[str]) -> _ClaudePlan:
    prior = _find_entry(manifest, "json_array_values", path)
    current = _read_optional(path)
    if prior and prior.get("transaction") == "pending":
        identity = _sha256(current)
        if identity not in {prior.get("pre_sha256"), prior.get("post_sha256")}:
            raise AssistantAccessConfigError(f"pending assistant configuration has neither its pre-state nor intended post-state: {path}")
        current_mode = None if current is None else stat.S_IMODE(path.stat().st_mode)
        expected_mode = (
            prior.get("pre_mode", None if prior.get("pre_sha256") is None else prior.get("file_mode"))
            if identity == prior.get("pre_sha256")
            else prior.get("post_mode", prior.get("file_mode"))
        )
        if current_mode != expected_mode:
            raise AssistantAccessConfigError(f"pending assistant configuration mode differs from its recorded state: {path}")
    replacement, ownership = _transform_claude(current, path=path, required=required, prior=prior)
    if prior and prior.get("transaction") == "pending" and _sha256(replacement) != prior.get("post_sha256"):
        raise AssistantAccessConfigError(f"pending assistant configuration intent changed: {path}")
    mode = 0o600 if current is None else stat.S_IMODE(path.stat().st_mode)
    return _ClaudePlan(path, current, replacement, mode, ownership)


def _assert_claude_plan_current(plan: _ClaudePlan) -> None:
    current = _read_optional(plan.path)
    if current != plan.expected:
        raise AssistantAccessConfigError(f"assistant configuration changed after pair preflight: {plan.path}")
    if current is not None and stat.S_IMODE(plan.path.stat().st_mode) != plan.mode:
        raise AssistantAccessConfigError(f"assistant configuration mode changed after pair preflight: {plan.path}")


def _claude_record(manifest: "Manifest", plan: _ClaudePlan) -> dict[str, object]:
    prior = _find_entry(manifest, "json_array_values", plan.path)
    if prior and prior.get("transaction") == "pending":
        return {key: value for key, value in prior.items() if key not in {"kind", "path"}}
    created_file = plan.expected is None or bool(prior and prior.get("created_file"))
    current, replacement, ownership, path, mode = plan.expected, plan.replacement, plan.ownership, plan.path, plan.mode
    return {**ownership, "transaction": "pending", "created_file": created_file,
            "pre_sha256": _sha256(current), "post_sha256": _sha256(replacement),
            "pre_mode": None if current is None else mode, "post_mode": mode,
            "file_mode": mode}


def _apply_claude(manifest: "Manifest", plan: _ClaudePlan, record: dict[str, object]) -> None:
    current, replacement, path, mode = plan.expected, plan.replacement, plan.path, plan.mode
    if replacement != current: _replace_if_unchanged(path, expected=current, replacement=replacement, mode=mode)
    committed = {**record, "transaction": "committed"}
    manifest.record("json_array_values", path=str(path), **committed)


def _journal_pair(manifest: "Manifest", codex_path: Path, codex_record: dict[str, object], claude_path: Path, claude_record: dict[str, object]) -> None:
    replacements = {
        ("codex_access_array_block", str(codex_path)): {"kind": "codex_access_array_block", "path": str(codex_path), **codex_record},
        ("json_array_values", str(claude_path)): {"kind": "json_array_values", "path": str(claude_path), **claude_record},
    }
    retained = [entry for entry in manifest.entries if (entry.get("kind"), entry.get("path")) not in replacements]
    manifest.entries = [*retained, *replacements.values()]
    manifest.save()


def _validate_apply_entries(manifest: "Manifest", codex_path: Path, claude_path: Path) -> None:
    access = [entry for entry in manifest.entries if entry.get("kind") in {"codex_access_array_block", "json_array_values"}]
    if not access:
        return
    expected = {"codex_access_array_block": str(codex_path), "json_array_values": str(claude_path)}
    if any(len([entry for entry in access if entry.get("kind") == kind]) != 1 or next((entry.get("path") for entry in access if entry.get("kind") == kind), None) != path for kind, path in expected.items()):
        raise AssistantAccessConfigError("assistant-access manifest ownership is incomplete, duplicated, or targets another path")


def _require_manifest_binding(manifest: "Manifest", context: InstallationContext) -> None:
    expected = {"mode": context.mode, "installation_id": context.installation_id}
    if context.mode == "development":
        assert context.development_root is not None
        expected["development_root"] = str(context.development_root.resolve(strict=False))
    else:
        expected["codex_home"] = str(context.codex_home.resolve(strict=False))
        expected["claude_home"] = str(context.claude_home.resolve(strict=False))
        legacy = {"mode": "standard", "installation_id": "standard"}
        if manifest.installation == legacy:
            manifest.bind_context(
                mode="standard",
                installation_id="standard",
                codex_home=context.codex_home,
                claude_home=context.claude_home,
            )
    if manifest.installation != expected:
        raise AssistantAccessConfigError(
            "durable install manifest binding does not match the selected assistant context"
        )


def reconcile_assistant_access(context: InstallationContext, manifest: "Manifest") -> None:
    """Apply exact assistant access roots with durable, replayable ownership."""
    lock_root = context.paths.install_state_root
    lock_root.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(lock_root / "assistant-access.lock", allowed_root=lock_root):
        manifest.reload()
        _require_manifest_binding(manifest, context)
        required = [str(path) for path in resolve_assistant_access_roots(context)]
        codex_path = codex_toml.config_path(context.codex_home)
        claude_path = context.claude_home / "settings.json"
        _validate_apply_entries(manifest, codex_path, claude_path)
        claude_plan = _plan_claude(manifest, claude_path, required)
        codex_plan = _preflight_codex(manifest, context, required)
        _assert_claude_plan_current(claude_plan)
        codex_record = _codex_record(manifest, codex_plan)
        claude_record = _claude_record(manifest, claude_plan)
        _journal_pair(manifest, codex_plan.path, codex_record, claude_plan.path, claude_record)
        _apply_codex(manifest, codex_plan, codex_record)
        _apply_claude(manifest, claude_plan, claude_record)


__all__ = ["ACCESS_BEGIN", "ACCESS_END", "AssistantAccessConfigError", "reconcile_assistant_access"]
