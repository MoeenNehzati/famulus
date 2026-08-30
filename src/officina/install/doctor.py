"""Read-only diagnosis for one explicitly selected Famulus installation."""
from __future__ import annotations

import json
import re
import shlex
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from officina.common import codex_toml
from officina.install.context import InstallationContext
from officina.install.assistant_access import (
    AssistantAccessBoundaryError,
    resolve_assistant_access_roots,
)
from officina.install.managed_runtime import (
    ManagedRuntimeError,
    deployed_resolver_trusted_roots,
)
from officina.install.runtime_pointer import (
    RuntimePointerError,
    load_current_pointer,
    load_installed_context_record,
)


class InstallManifestError(ValueError):
    pass


_DEVELOPMENT_ID = re.compile(r"dev-[0-9a-f]{32}\Z")


def _load_json_strict(text: str, *, label: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key {key!r} in {label}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=reject_duplicates)


def load_install_manifest(path: Path) -> dict[str, object]:
    try:
        payload = _load_json_strict(
            path.read_text(encoding="utf-8"), label="install manifest"
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise InstallManifestError(f"cannot read install manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise InstallManifestError("install manifest must be an object")
    version = payload.get("version")
    if version not in {1, 2} or isinstance(version, bool):
        raise InstallManifestError(f"unsupported install manifest version: {version!r}")
    expected_keys = {"version", "entries"}
    if version == 2:
        expected_keys.add("installation")
    if set(payload) != expected_keys:
        raise InstallManifestError("install manifest has unexpected fields")
    entries = payload.get("entries")
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("kind"), str)
        or not entry.get("kind")
        or not isinstance(entry.get("path"), str)
        or not entry.get("path")
        for entry in entries
    ):
        raise InstallManifestError("install manifest entries must have kind and path strings")
    if version == 2:
        installation = payload.get("installation")
        if not isinstance(installation, dict):
            raise InstallManifestError("schema-2 install manifest binding must be an object")
        mode = installation.get("mode")
        installation_id = installation.get("installation_id")
        if mode == "standard":
            keys = set(installation)
            valid = installation_id == "standard" and keys in (
                {"mode", "installation_id"},
                {"mode", "installation_id", "codex_home", "claude_home"},
            )
            if valid and "codex_home" in installation:
                valid = all(
                    isinstance(installation.get(name), str)
                    and bool(installation.get(name))
                    and Path(str(installation[name])).is_absolute()
                    for name in ("codex_home", "claude_home")
                )
        elif mode == "development":
            root = installation.get("development_root")
            valid = (
                set(installation) == {"mode", "installation_id", "development_root"}
                and isinstance(installation_id, str)
                and _DEVELOPMENT_ID.fullmatch(installation_id) is not None
                and isinstance(root, str)
                and bool(root)
                and Path(root).is_absolute()
            )
        else:
            valid = False
        if not valid:
            raise InstallManifestError("install manifest installation binding is invalid")
    return payload


@dataclass(frozen=True)
class DiagnosticCheck:
    id: str
    status: str
    summary: str
    recovery: str = ""


@dataclass(frozen=True)
class DiagnosticReport:
    schema_version: int
    mode: str
    installation_id: str
    status: str
    checks: tuple[DiagnosticCheck, ...]

    @classmethod
    def healthy_for(cls, context: InstallationContext) -> "DiagnosticReport":
        return cls(1, context.mode, context.installation_id, "healthy", ())


_REQUIRED_COMMANDS = (
    "dispatcher",
    "invoke-skill",
    "llm-wakeup",
    "lw",
    "background_run",
)
def _apply_command(context: InstallationContext) -> str:
    command = [
        "dispatcher",
        "--caller-skill",
        "install-assistant-tools",
        "install-assistant-tools._rtx.interface.scripts-install",
    ]
    if context.mode == "development":
        command.extend(("--dev-mode", "--repo-path", str(context.development_root)))
    else:
        command.append("--no-dev-mode")
    command.extend(("--non-interactive", "--yes"))
    return shlex.join(command)


def _recurring_remove_context_command() -> str:
    return shlex.join(
        [
            "dispatcher",
            "--caller-skill",
            "recurring-tasks",
            "recurring-tasks._rtx.interface.scripts-remove-context",
        ]
    )


def _check_pointer(context: InstallationContext) -> DiagnosticCheck:
    if not context.paths.current_pointer.exists():
        return DiagnosticCheck(
            "pointer", "error", "No active managed-runtime pointer.", _apply_command(context)
        )
    try:
        pointer = load_current_pointer(
            runtime_root=context.paths.runtime_root,
            trusted_interpreter_roots=deployed_resolver_trusted_roots(
                runtime_root=context.paths.runtime_root
            ),
        )
    except (ManagedRuntimeError, RuntimePointerError) as exc:
        return DiagnosticCheck(
            "pointer",
            "error",
            f"The runtime pointer is malformed or internally inconsistent: {exc}",
            _apply_command(context),
        )
    if pointer.installation_context is None or pointer.launcher_resources is None:
        return DiagnosticCheck(
            "pointer", "error", "The active release is not a schema-3 installation.", _apply_command(context)
        )
    try:
        installed = load_installed_context_record(pointer.installation_context)
    except RuntimePointerError as exc:
        return DiagnosticCheck(
            "pointer", "error", f"The installed context record is malformed: {exc}", _apply_command(context)
        )
    selected_identity = (
        context.mode,
        context.installation_id,
        context.source_root.resolve(strict=False),
        context.development_root.resolve(strict=False)
        if context.development_root is not None
        else None,
    )
    installed_identity = (
        installed.mode,
        installed.installation_id,
        installed.source_root,
        installed.development_root,
    )
    home_fields_match = all(
        Path(getattr(context, name)).resolve(strict=False) == getattr(installed, name)
        for name in vars(installed)
        if name.endswith("_home")
    )
    if installed_identity != selected_identity or not home_fields_match:
        return DiagnosticCheck(
            "pointer",
            "error",
            "The active release belongs to a different installation context.",
            _apply_command(context),
        )
    return DiagnosticCheck(
        "pointer",
        "ok",
        f"Active release: {pointer.release_id}; launcher resources: {pointer.launcher_resources}",
    )


def _check_source(context: InstallationContext) -> DiagnosticCheck:
    if not context.source_root.is_dir():
        return DiagnosticCheck(
            "source",
            "error",
            f"The selected source is missing: {context.source_root}",
            f"restore source at {context.source_root}, then run {_apply_command(context)}",
        )
    return DiagnosticCheck("source", "ok", f"Source: {context.source_root}")


def _check_commands(
    context: InstallationContext, *, environ: Mapping[str, str], platform: str
) -> DiagnosticCheck:
    path_value = environ.get("PATH", "")
    stale: list[str] = []
    commands = _REQUIRED_COMMANDS
    native_batch_commands = platform.startswith("win")
    if context.mode == "development" and not native_batch_commands:
        commands += ("milestone", "agent-timeline")
    suffix = ".bat" if native_batch_commands else ""
    for command in commands:
        expected = context.paths.user_bin / f"{command}{suffix}"
        resolved = shutil.which(command, path=path_value)
        if resolved is None or Path(resolved).resolve(strict=False) != expected.resolve(strict=False):
            stale.append(f"{command} (expected {expected}, found {resolved or 'absent'})")
    if stale:
        return DiagnosticCheck(
            "commands", "error", "Missing or stale command origins: " + "; ".join(stale), _apply_command(context)
        )
    return DiagnosticCheck("commands", "ok", f"Required commands resolve from {context.paths.user_bin}")


def _check_manifest(context: InstallationContext) -> DiagnosticCheck:
    path = context.paths.install_state_root / "install-manifest.json"
    try:
        payload = load_install_manifest(path)
    except InstallManifestError as exc:
        return DiagnosticCheck(
            "manifest", "error", f"Install manifest is absent or malformed: {exc}", _apply_command(context)
        )
    expected = {"mode": context.mode, "installation_id": context.installation_id}
    if context.development_root is not None:
        expected["development_root"] = str(context.development_root.resolve(strict=False))
    else:
        expected["codex_home"] = str(context.codex_home.resolve(strict=False))
        expected["claude_home"] = str(context.claude_home.resolve(strict=False))
    if payload.get("version") != 2 or payload.get("installation") != expected:
        return DiagnosticCheck(
            "manifest", "error", "Install manifest does not match the selected installation context.", _apply_command(context)
        )
    entries = payload["entries"]
    assert isinstance(entries, list)
    return DiagnosticCheck("manifest", "ok", f"Manifest entries: {len(entries)}")


def _access_error(context: InstallationContext, detail: str) -> DiagnosticCheck:
    return DiagnosticCheck(
        "assistant-access",
        "error",
        f"Assistant access configuration is incomplete or unproven: {detail}",
        _apply_command(context),
    )


def _check_assistant_access(context: InstallationContext) -> DiagnosticCheck:
    manifest_path = context.paths.install_state_root / "install-manifest.json"
    try:
        manifest = load_install_manifest(manifest_path)
        required = [str(path) for path in resolve_assistant_access_roots(context)]
    except (InstallManifestError, AssistantAccessBoundaryError) as exc:
        return _access_error(context, str(exc))
    entries = manifest["entries"]
    assert isinstance(entries, list)
    codex_path = codex_toml.config_path(context.codex_home)
    claude_path = context.claude_home / "settings.json"
    selected = {
        "codex_access_array_block": [
            entry for entry in entries if entry.get("kind") == "codex_access_array_block"
        ],
        "json_array_values": [
            entry for entry in entries if entry.get("kind") == "json_array_values"
        ],
    }
    expected_paths = {
        "codex_access_array_block": str(codex_path),
        "json_array_values": str(claude_path),
    }
    for kind, candidates in selected.items():
        if len(candidates) != 1 or candidates[0].get("path") != expected_paths[kind]:
            return _access_error(
                context, f"{kind} must own exactly {expected_paths[kind]}"
            )
        if (
            candidates[0].get("transaction") != "committed"
            or candidates[0].get("uninstall_transaction") is not None
        ):
            return _access_error(context, f"{kind} has a pending operation")
        introduced = candidates[0].get("introduced")
        if (
            not isinstance(introduced, list)
            or any(not isinstance(value, str) for value in introduced)
            or not set(introduced).issubset(required)
        ):
            return _access_error(context, f"{kind} ownership values are malformed")
    codex_entry = selected["codex_access_array_block"][0]
    claude_entry = selected["json_array_values"][0]
    begin = codex_entry.get("begin")
    end = codex_entry.get("end")
    identity = codex_entry.get("block_sha256")
    if not isinstance(begin, str) or not isinstance(end, str) or not isinstance(identity, str):
        return _access_error(context, "Codex marker ownership is malformed")
    try:
        inspection = codex_toml.inspect_access_roots(
            context.codex_home, begin=begin, end=end
        )
    except (OSError, codex_toml.CodexTomlError) as exc:
        return _access_error(context, f"cannot inspect {codex_path}: {exc}")
    if any(value not in inspection.roots for value in required):
        return _access_error(context, f"{codex_path} lacks required writable_roots")
    if not inspection.marker_within_array or inspection.block_sha256 != identity:
        return _access_error(context, "Codex owned marker block was modified")
    if list(inspection.marker_values) != codex_entry["introduced"]:
        return _access_error(context, "Codex marker values do not match ownership")
    try:
        claude_payload = _load_json_strict(
            claude_path.read_text(encoding="utf-8"), label=str(claude_path)
        )
    except (OSError, UnicodeError, ValueError) as exc:
        return _access_error(context, f"cannot read {claude_path}: {exc}")
    permissions = claude_payload.get("permissions") if isinstance(claude_payload, dict) else None
    claude_roots = (
        permissions.get("additionalDirectories") if isinstance(permissions, dict) else None
    )
    if (
        not isinstance(claude_roots, list)
        or any(not isinstance(value, str) for value in claude_roots)
        or any(value not in claude_roots for value in required)
        or any(claude_roots.count(value) != 1 for value in claude_entry["introduced"])
    ):
        return _access_error(
            context, f"{claude_path} lacks required or uniquely owned additionalDirectories"
        )
    return DiagnosticCheck(
        "assistant-access",
        "ok",
        f"Codex access: {codex_path}; Claude access: {claude_path}; "
        f"warning: {context.paths.recurring_config_root} grants scheduled-command authority.",
    )


def _check_recurring(
    context: InstallationContext, *, environ: Mapping[str, str], platform: str
) -> DiagnosticCheck:
    from officina.recurring.runtime import (
        RecurringPrerequisiteError,
        RecurringRuntimeError,
        load_managed_schedule,
        resolve_managed_schedule_authority,
    )

    descriptor_path = context.paths.recurring_config_root / "schedule-descriptor.json"
    summary_path = context.paths.recurring_state_root / "registrations.json"
    if not descriptor_path.exists() and not summary_path.exists():
        return DiagnosticCheck("recurring", "ok", "No recurring registrations are recorded.")
    recovery = _recurring_remove_context_command()
    descriptor_absent = not descriptor_path.exists()
    try:
        schedule = (
            resolve_managed_schedule_authority(
                runtime_root=context.paths.runtime_root,
                environ=environ,
                platform=platform,
            )
            if descriptor_absent
            else load_managed_schedule(
                runtime_root=context.paths.runtime_root,
                descriptor_path=descriptor_path,
                environ=environ,
                platform=platform,
            )
        )
    except RecurringPrerequisiteError as exc:
        return DiagnosticCheck(
            "recurring",
            "error",
            f"Recurring scheduling prerequisite is unavailable: {exc}",
            _apply_command(context),
        )
    except RecurringRuntimeError as exc:
        return DiagnosticCheck(
            "recurring",
            "error",
            f"Recurring descriptor does not match the active authority: {exc}",
            recovery,
        )
    if descriptor_absent:
        return DiagnosticCheck(
            "recurring",
            "error",
            "Recurring registrations exist but the canonical descriptor is absent.",
            recovery,
        )
    if not summary_path.exists():
        return DiagnosticCheck(
            "recurring",
            "ok",
            "Recurring descriptor: schema 1, "
            f"backend {schedule.default_backend}; registrations: 0",
        )
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        registrations = payload.get("registrations")
    except (OSError, UnicodeError, ValueError, AttributeError) as exc:
        return DiagnosticCheck(
            "recurring",
            "error",
            f"Recurring registration summary is malformed: {exc}",
            recovery,
        )
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "installation_id", "registrations"}
        or payload.get("schema_version") != 1
        or payload.get("installation_id") != context.installation_id
        or not isinstance(registrations, list)
        or any(not isinstance(name, str) or not name for name in registrations)
    ):
        return DiagnosticCheck(
            "recurring",
            "error",
            "Recurring registration summary does not match the selected installation context.",
            recovery,
        )
    if registrations:
        return DiagnosticCheck(
            "recurring",
            "ok",
            "Recurring descriptor: schema 1, "
            f"backend {schedule.default_backend}; registrations: {len(registrations)}",
        )
    return DiagnosticCheck(
        "recurring",
        "ok",
        "Recurring descriptor: schema 1, "
        f"backend {schedule.default_backend}; registrations: 0",
    )


def diagnose_installation(
    *, context: InstallationContext, environ: Mapping[str, str], platform: str
) -> DiagnosticReport:
    """Inspect one explicit context without writing or inferring from cwd."""
    checks = (
        _check_pointer(context),
        _check_source(context),
        _check_commands(context, environ=environ, platform=platform),
        _check_manifest(context),
        _check_assistant_access(context),
        _check_recurring(context, environ=environ, platform=platform),
    )
    status = "healthy" if all(check.status == "ok" for check in checks) else "unhealthy"
    return DiagnosticReport(1, context.mode, context.installation_id, status, checks)


def render_diagnostic_text(report: DiagnosticReport) -> str:
    lines = [
        f"Famulus installation: {report.status}",
        f"Mode: {report.mode}",
        f"Installation ID: {report.installation_id}",
    ]
    for check in report.checks:
        lines.append(f"[{check.status.upper()}] {check.id}: {check.summary}")
        if check.recovery:
            lines.append(f"  Recovery: {check.recovery}")
    return "\n".join(lines) + "\n"


def render_diagnostic_json(report: DiagnosticReport) -> str:
    return json.dumps(asdict(report), indent=2) + "\n"


__all__ = [
    "DiagnosticCheck",
    "DiagnosticReport",
    "InstallManifestError",
    "diagnose_installation",
    "load_install_manifest",
    "render_diagnostic_json",
    "render_diagnostic_text",
]
