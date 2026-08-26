#!/usr/bin/env python3
"""Produce narrowly labeled assistant-access evidence for hosted CI runners."""
from __future__ import annotations

import argparse
import base64
import errno
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid
from pathlib import Path
from typing import Iterable

TEST_DIR = Path(__file__).resolve().parent
RTX_DIR = TEST_DIR.parent
REPO_ROOT = TEST_DIR.parents[3]
REPO_SRC = REPO_ROOT / "src"
for candidate in (RTX_DIR, REPO_SRC):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import _install_uninstall as install_uninstall  # noqa: E402
import _phase_entry as phase_entry  # noqa: E402
from _state_record import Manifest  # noqa: E402
from officina.install.assistant_access import resolve_assistant_access_roots  # noqa: E402
from officina.install.context import (  # noqa: E402
    InstallationContext,
    resolve_installation_context,
)

EVIDENCE_LABELS = {
    "config",
    "OS-write",
    "host-enforcement",
    "client-install-health",
}
EVIDENCE_STATUSES = {"passed", "failed"}
DENIAL_ERRNOS = {errno.EACCES, errno.EPERM, errno.EROFS}
CODEX_VERSION = "0.149.0"
CLAUDE_VERSION = "2.1.237"


class ProbeError(RuntimeError):
    """A probe failed without widening its evidence claim."""


def _platform_label(platform_name: str) -> str:
    return {"darwin": "macos", "win32": "windows"}.get(
        platform_name, platform_name
    )


def _qualifications() -> dict[str, str]:
    return {
        "claude_authenticated_access": "skipped",
        "codex_ide_app_enforcement": "unverified",
    }


def _atomic_json_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".tmp."
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def append_evidence(
    path: Path,
    *,
    platform_name: str,
    item: dict[str, object],
    qualification_updates: dict[str, str] | None = None,
) -> None:
    label = item.get("label")
    status = item.get("status")
    if label not in EVIDENCE_LABELS:
        raise ProbeError(f"unsupported evidence label: {label!r}")
    if status not in EVIDENCE_STATUSES:
        raise ProbeError(f"unsupported evidence status: {status!r}")
    if not isinstance(item.get("subject"), str):
        raise ProbeError("evidence subject must be a string")
    normalized_platform = _platform_label(platform_name)
    if path.exists():
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProbeError(f"cannot read evidence file {path}: {exc}") from exc
        if (
            not isinstance(report, dict)
            or report.get("schema_version") != 1
            or report.get("platform") != normalized_platform
            or not isinstance(report.get("evidence"), list)
            or not isinstance(report.get("qualifications"), dict)
        ):
            raise ProbeError(f"existing evidence file has the wrong schema: {path}")
    else:
        report = {
            "schema_version": 1,
            "platform": normalized_platform,
            "qualifications": _qualifications(),
            "evidence": [],
        }
    if qualification_updates:
        report["qualifications"].update(qualification_updates)
    report["evidence"].append(item)
    _atomic_json_write(path, report)


def _probe_environ(home: Path, codex_home: Path, claude_home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "CODEX_HOME": str(codex_home),
        "CLAUDE_CONFIG_DIR": str(claude_home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
    }


def _context(
    *, platform_name: str, source_root: Path, home: Path, codex_home: Path, claude_home: Path
) -> InstallationContext:
    environ = _probe_environ(home, codex_home, claude_home)
    return resolve_installation_context(
        mode="standard",
        source_root=source_root,
        development_root=None,
        platform=platform_name,
        home=home,
        environ=environ,
    )


def _independent_access_oracle(
    *, platform_name: str, home: Path
) -> tuple[Path, ...]:
    if platform_name == "darwin":
        base = home / "Library" / "Application Support" / "Famulus"
        config_root = base / "config"
        state_root = base / "state"
    elif platform_name == "win32":
        config_root = home / "AppData" / "Roaming" / "Famulus"
        state_root = home / "AppData" / "Local" / "Famulus" / "state"
    else:
        config_root = home / ".config" / "famulus"
        state_root = home / ".local" / "state" / "famulus"
    return tuple(
        path.resolve(strict=False)
        for path in (
            home / ".assistant-logs",
            config_root / "recurring-tasks",
            state_root / "recurring-tasks",
            state_root / "email-triage",
            state_root / "list-manager" / "locks",
            state_root / "list-manager" / "cache",
            home / ".local" / "share" / "llm-wakeup",
        )
    )


def _validated_control_root(
    control_root: Path,
    *,
    allowed_roots: tuple[Path, ...],
    source_root: Path,
    home: Path,
) -> Path:
    if not control_root.is_absolute():
        raise ProbeError("control root must be absolute")
    resolved = control_root.resolve(strict=False)
    excluded = (*allowed_roots, source_root.resolve(strict=False), home.resolve(strict=False))
    for allowed in excluded:
        canonical = allowed.resolve(strict=False)
        if (
            resolved == canonical
            or resolved in canonical.parents
            or canonical in resolved.parents
        ):
            raise ProbeError("control root overlaps the selected home, checkout, or policy")
    return resolved


def _file_snapshot(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"exists": False}
    if not path.is_file():
        raise ProbeError(f"recovery target is not a regular file: {path}")
    return {
        "exists": True,
        "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        "mode": path.stat().st_mode & 0o777,
    }


def _restore_file_snapshot(path: Path, snapshot: object) -> None:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("exists"), bool):
        raise ProbeError(f"invalid recovery snapshot for {path}")
    if not snapshot["exists"]:
        path.unlink(missing_ok=True)
        return
    encoded = snapshot.get("content_base64")
    mode = snapshot.get("mode")
    if not isinstance(encoded, str) or not isinstance(mode, int):
        raise ProbeError(f"incomplete recovery snapshot for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(encoded, validate=True))
    path.chmod(mode)


def _initialize_evidence(path: Path, *, platform_name: str) -> None:
    _atomic_json_write(
        path,
        {
            "schema_version": 1,
            "platform": _platform_label(platform_name),
            "qualifications": _qualifications(),
            "evidence": [],
        },
    )


def _state_context(state: dict[str, object]) -> InstallationContext:
    required = {
        "platform",
        "source_root",
        "home",
        "codex_home",
        "claude_home",
    }
    if any(not isinstance(state.get(name), str) for name in required):
        raise ProbeError("probe state is missing context paths")
    return _context(
        platform_name=str(state["platform"]),
        source_root=Path(str(state["source_root"])),
        home=Path(str(state["home"])),
        codex_home=Path(str(state["codex_home"])),
        claude_home=Path(str(state["claude_home"])),
    )


def _load_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"cannot read probe state {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ProbeError(f"probe state has the wrong schema: {path}")
    return payload


def _seed_configuration(context: InstallationContext) -> tuple[Path, Path, bytes]:
    foreign_codex = context.selected_home / "user-foreign" / "codex"
    foreign_claude = context.selected_home / "user-foreign" / "claude"
    codex = context.codex_home / "config.toml"
    claude = context.claude_home / "settings.json"
    local = context.claude_home / "settings.local.json"
    for path in (codex, claude, local):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise ProbeError(f"probe refuses to replace an existing fixture file: {path}")
    codex.write_text(
        'model = "gpt-5"\n'
        "[sandbox_workspace_write]\n"
        "network_access = false\n"
        f"writable_roots = [{json.dumps(str(foreign_codex))}]\n",
        encoding="utf-8",
    )
    claude.write_text(
        json.dumps(
            {
                "theme": "dark",
                "permissions": {
                    "deny": ["Read(.env)"],
                    "additionalDirectories": [str(foreign_claude)],
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    local_bytes = b'{"hooks":{"Notification":[]}}\n'
    local.write_bytes(local_bytes)
    return foreign_codex, foreign_claude, local_bytes


def prepare(
    *,
    platform_name: str,
    source_root: Path,
    home: Path,
    state_path: Path,
    evidence_path: Path,
    control_root: Path,
) -> None:
    home = home.resolve(strict=False)
    codex_home = (home / ".codex").resolve(strict=False)
    claude_home = (home / ".claude").resolve(strict=False)
    context = _context(
        platform_name=platform_name,
        source_root=source_root.resolve(strict=True),
        home=home,
        codex_home=codex_home,
        claude_home=claude_home,
    )
    allowed = _independent_access_oracle(platform_name=platform_name, home=home)
    control = _validated_control_root(
        control_root,
        allowed_roots=allowed,
        source_root=context.source_root,
        home=home,
    )
    codex_config = context.codex_home / "config.toml"
    claude_settings = context.claude_home / "settings.json"
    claude_local = context.claude_home / "settings.local.json"
    foreign_codex = context.selected_home / "user-foreign" / "codex"
    foreign_claude = context.selected_home / "user-foreign" / "claude"
    state = {
        "schema_version": 1,
        "platform": platform_name,
        "source_root": str(context.source_root),
        "home": str(context.selected_home),
        "codex_home": str(context.codex_home),
        "claude_home": str(context.claude_home),
        "phase": "baseline_recorded",
        "manifest": str(context.paths.install_state_root / "install-manifest.json"),
        "codex_config": str(codex_config),
        "claude_settings": str(claude_settings),
        "claude_local_settings": str(claude_local),
        "foreign_codex_root": str(foreign_codex),
        "foreign_claude_root": str(foreign_claude),
        "allowed_roots": [str(path) for path in allowed],
        "control_root": str(control),
        "baseline": {
            "manifest": _file_snapshot(
                context.paths.install_state_root / "install-manifest.json"
            ),
            "codex_config": _file_snapshot(codex_config),
            "claude_settings": _file_snapshot(claude_settings),
            "claude_local_settings": _file_snapshot(claude_local),
        },
    }
    _initialize_evidence(evidence_path, platform_name=platform_name)
    _atomic_json_write(state_path, state)
    try:
        if resolve_assistant_access_roots(context) != allowed:
            raise ProbeError("resolver disagrees with the independent canonical oracle")
        seeded_codex, seeded_claude, local_bytes = _seed_configuration(context)
        if seeded_codex != foreign_codex or seeded_claude != foreign_claude:
            raise ProbeError("seeded foreign roots differ from recovery state")
        state["phase"] = "seeded"
        _atomic_json_write(state_path, state)
        environ = dict(os.environ)
        environ.update(_probe_environ(home, codex_home, claude_home))
        choices = phase_entry.ApplyChoices(
            agents=(),
            default_backend="codex",
            home=home,
            shell_rc=home / ".bashrc",
        )
        state["phase"] = "apply_pending"
        _atomic_json_write(state_path, state)
        if phase_entry.apply(context=context, choices=choices, environ=environ) != 0:
            raise ProbeError("assistant access lifecycle apply failed")
        first = (codex_config.read_bytes(), claude_settings.read_bytes())
        if phase_entry.apply(context=context, choices=choices, environ=environ) != 0:
            raise ProbeError("assistant access lifecycle reapply failed")
        second = (codex_config.read_bytes(), claude_settings.read_bytes())
        if first != second:
            raise ProbeError("assistant access reapply was not byte-stable")
        manifest = Manifest(Path(str(state["manifest"])))
        access_entries = [
            entry
            for entry in manifest.entries
            if entry.get("kind") in {"codex_access_array_block", "json_array_values"}
        ]
        if len(access_entries) != 2 or any(
            entry.get("transaction") != "committed" for entry in access_entries
        ):
            raise ProbeError("assistant access reapply did not retain committed ownership")
        if claude_local.read_bytes() != local_bytes:
            raise ProbeError("Claude local settings changed during access installation")
        state["phase"] = "prepared"
        _atomic_json_write(state_path, state)
        append_evidence(
            evidence_path,
            platform_name=platform_name,
            item={
                "label": "config",
                "subject": "install and reapply",
                "status": "passed",
                "details": {
                    "policy_roots": state["allowed_roots"],
                    "reapply_byte_stable": True,
                    "foreign_settings_preserved": True,
                    "lifecycle_entrypoint": "_phase_entry.apply",
                },
            },
        )
    except Exception as exc:
        state["phase"] = "prepare_failed"
        state["prepare_error"] = str(exc)
        _atomic_json_write(state_path, state)
        _record_failed(
            evidence_path,
            platform_name=platform_name,
            label="config",
            subject="install and reapply",
            error=exc,
        )
        if isinstance(exc, ProbeError):
            raise
        raise ProbeError(str(exc)) from exc


def _policy_roots(state: dict[str, object]) -> tuple[list[str], list[str]]:
    codex_path = Path(str(state["codex_config"]))
    claude_path = Path(str(state["claude_settings"]))
    try:
        codex = tomllib.loads(codex_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ProbeError(f"invalid Codex host config: {exc}") from exc
    try:
        claude = json.loads(claude_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeError(f"invalid Claude host config: {exc}") from exc
    try:
        codex_roots = codex["sandbox_workspace_write"]["writable_roots"]
        claude_roots = claude["permissions"]["additionalDirectories"]
    except (KeyError, TypeError) as exc:
        raise ProbeError(f"assistant access policy is missing: {exc}") from exc
    if (
        not isinstance(codex_roots, list)
        or any(not isinstance(item, str) for item in codex_roots)
        or not isinstance(claude_roots, list)
        or any(not isinstance(item, str) for item in claude_roots)
    ):
        raise ProbeError("assistant access policy must contain only string roots")
    return codex_roots, claude_roots


def _create_canary(root: Path, token: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f".famulus-assistant-access-canary-{token}"
    with path.open("xb") as stream:
        stream.write(b"famulus assistant access probe\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def _cleanup_canaries(roots: Iterable[Path], token: str) -> None:
    canaries = [
        root / f".famulus-assistant-access-canary-{token}" for root in roots
    ]
    failures: list[str] = []
    for canary in reversed(canaries):
        try:
            if not canary.exists():
                continue
            canary.unlink(missing_ok=True)
        except OSError as exc:
            failures.append(f"{canary}: {exc}")
    remaining = [str(canary) for canary in canaries if canary.exists()]
    if failures or remaining:
        detail = "; ".join(failures)
        if remaining:
            detail += ("; " if detail else "") + "remaining: " + ", ".join(remaining)
        raise ProbeError("canary cleanup failed: " + detail)


def _cleanup_failure(
    roots: Iterable[Path], token: str, original: Exception
) -> tuple[ProbeError, bool]:
    try:
        _cleanup_canaries(roots, token)
    except ProbeError as cleanup_error:
        return ProbeError(f"{original}; {cleanup_error}"), False
    return ProbeError(str(original)), True


def config_os_write(
    *, state_path: Path, evidence_path: Path, canary_token: str | None = None
) -> None:
    state = _load_state(state_path)
    platform_name = str(state["platform"])
    allowed = [str(item) for item in state.get("allowed_roots", [])]
    expected_codex = [str(state["foreign_codex_root"]), *allowed]
    expected_claude = [str(state["foreign_claude_root"]), *allowed]
    control = str(state["control_root"])
    try:
        codex_roots, claude_roots = _policy_roots(state)
        if control in codex_roots or control in claude_roots:
            raise ProbeError("synthetic sibling control root is present in policy")
        if codex_roots != expected_codex:
            raise ProbeError("Codex policy does not exactly preserve foreign plus canonical roots")
        if claude_roots != expected_claude:
            raise ProbeError("Claude policy does not exactly preserve foreign plus canonical roots")
    except ProbeError as exc:
        append_evidence(
            evidence_path,
            platform_name=platform_name,
            item={
                "label": "config",
                "subject": "resolved assistant policy",
                "status": "failed",
                "detail": str(exc),
            },
        )
        raise
    append_evidence(
        evidence_path,
        platform_name=platform_name,
        item={
            "label": "config",
            "subject": "resolved assistant policy",
            "status": "passed",
            "details": {
                "canonical_roots": allowed,
                "control_root": control,
                "control_absent": True,
            },
        },
    )
    token = canary_token or uuid.uuid4().hex
    root_paths = [Path(root) for root in allowed]
    try:
        for root in root_paths:
            _create_canary(root, token)
    except OSError as exc:
        error, cleaned = _cleanup_failure(root_paths, token, exc)
        append_evidence(
            evidence_path,
            platform_name=platform_name,
            item={
                "label": "OS-write",
                "subject": "canonical root canaries",
                "status": "failed",
                "detail": str(error),
                "details": {"canaries_cleaned": cleaned},
            },
        )
        raise ProbeError(f"OS write failed for an allowed root: {error}") from exc
    try:
        _cleanup_canaries(root_paths, token)
    except ProbeError as exc:
        _record_failed(
            evidence_path,
            platform_name=platform_name,
            label="OS-write",
            subject="canonical root canaries",
            error=exc,
        )
        raise
    append_evidence(
        evidence_path,
        platform_name=platform_name,
        item={
            "label": "OS-write",
            "subject": "canonical root canaries",
            "status": "passed",
            "details": {
                "roots": allowed,
                "control_attempted": False,
                "canaries_cleaned": True,
            },
        },
    )


def host_enforcement(
    *, state_path: Path, evidence_path: Path, canary_token: str | None = None
) -> None:
    state = _load_state(state_path)
    platform_name = str(state["platform"])
    allowed = Path(str(state["allowed_roots"][0]))
    control = Path(str(state["control_root"]))
    token = canary_token or uuid.uuid4().hex
    roots = [allowed, control]
    try:
        _create_canary(allowed, token)
        try:
            _create_canary(control, token)
        except OSError as exc:
            if exc.errno not in DENIAL_ERRNOS and not isinstance(exc, PermissionError):
                raise ProbeError(f"control write failed for a non-policy reason: {exc}") from exc
        else:
            raise ProbeError("control write unexpectedly succeeded")
    except (OSError, ProbeError) as exc:
        error, cleaned = _cleanup_failure(roots, token, exc)
        append_evidence(
            evidence_path,
            platform_name=platform_name,
            item={
                "label": "host-enforcement",
                "subject": "Codex sandbox allowed/control canaries",
                "status": "failed",
                "detail": str(error),
                "details": {"canaries_cleaned": cleaned},
            },
        )
        raise error from exc
    try:
        _cleanup_canaries(roots, token)
    except ProbeError as exc:
        _record_failed(
            evidence_path,
            platform_name=platform_name,
            label="host-enforcement",
            subject="Codex sandbox allowed/control canaries",
            error=exc,
        )
        raise
    append_evidence(
        evidence_path,
        platform_name=platform_name,
        item={
            "label": "host-enforcement",
            "subject": "Codex sandbox allowed/control canaries",
            "status": "passed",
            "details": {
                "allowed_write": "succeeded",
                "control_write": "denied",
                "canaries_cleaned": True,
            },
        },
    )


def _restore(*, state_path: Path, evidence_path: Path) -> None:
    state = _load_state(state_path)
    platform_name = str(state["platform"])
    context = _state_context(state)
    phase = state.get("phase")
    manifest_path = Path(str(state["manifest"]))
    uninstall_error: ProbeError | None = None
    manifest_has_entries = False
    if manifest_path.exists():
        manifest_has_entries = bool(Manifest(manifest_path).entries)
    if manifest_has_entries:
        environ = dict(os.environ)
        environ.update(
            _probe_environ(
                context.selected_home, context.codex_home, context.claude_home
            )
        )
        report = install_uninstall.uninstall_context(
            context=context,
            platform=platform_name,
            home=context.selected_home,
            environ=environ,
            purge=True,
            dry_run=False,
            no_pip=False,
            no_git_hooks=False,
        )
        if report.failed:
            uninstall_error = ProbeError(
                "assistant access lifecycle uninstall failed: " + repr(report.items)
            )
    elif phase == "prepared":
        uninstall_error = ProbeError("prepared fixture has no install manifest")

    if phase == "prepare_failed":
        baseline = state.get("baseline")
        if not isinstance(baseline, dict):
            raise ProbeError("failed prepare state has no recovery baseline")
        for key in (
            "manifest",
            "codex_config",
            "claude_settings",
            "claude_local_settings",
        ):
            _restore_file_snapshot(Path(str(state[key])), baseline.get(key))
    elif uninstall_error is None:
        codex_roots, claude_roots = _policy_roots(state)
        expected_codex = [str(state["foreign_codex_root"])]
        expected_claude = [str(state["foreign_claude_root"])]
        local = Path(str(state["claude_local_settings"]))
        if codex_roots != expected_codex or claude_roots != expected_claude:
            raise ProbeError("uninstall did not restore foreign assistant policy")
        if local.read_bytes() != b'{"hooks":{"Notification":[]}}\n':
            raise ProbeError("uninstall changed Claude local settings")
        if manifest_path.exists():
            raise ProbeError("assistant access manifest remained after lifecycle uninstall")

    if uninstall_error is not None:
        raise uninstall_error
    state["phase"] = "restored"
    _atomic_json_write(state_path, state)
    append_evidence(
        evidence_path,
        platform_name=platform_name,
        item={
            "label": "config",
            "subject": "uninstall restoration",
            "status": "passed",
            "details": {
                "foreign_settings_preserved": True,
                "owned_roots_removed": True,
                "lifecycle_entrypoint": "_install_uninstall.uninstall_context",
                "failed_prepare_recovered": phase == "prepare_failed",
            },
        },
    )


def restore(*, state_path: Path, evidence_path: Path) -> None:
    state = _load_state(state_path)
    platform_name = str(state["platform"])
    try:
        _restore(state_path=state_path, evidence_path=evidence_path)
    except (OSError, ProbeError, ValueError) as exc:
        _record_failed(
            evidence_path,
            platform_name=platform_name,
            label="config",
            subject="uninstall restoration",
            error=exc,
        )
        raise


def _record_failed(
    evidence_path: Path,
    *,
    platform_name: str,
    label: str,
    subject: str,
    error: Exception | str,
) -> None:
    append_evidence(
        evidence_path,
        platform_name=platform_name,
        item={
            "label": label,
            "subject": subject,
            "status": "failed",
            "detail": str(error),
        },
    )


def client_install_health(
    *, client: str, state_path: Path, evidence_path: Path
) -> None:
    state = _load_state(state_path)
    platform_name = str(state["platform"])
    try:
        executable = shutil.which(client)
        if executable is None:
            raise ProbeError(f"{client} executable is not on PATH")
        expected = CLAUDE_VERSION if client == "claude" else CODEX_VERSION
        version = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        combined = (version.stdout + "\n" + version.stderr).strip()
        pattern = (
            r"(?P<version>\d+\.\d+\.\d+) \(Claude Code\)"
            if client == "claude"
            else r"codex-cli (?P<version>\d+\.\d+\.\d+)"
        )
        version_matches = [
            match.group("version")
            for line in (version.stdout + "\n" + version.stderr).splitlines()
            if (match := re.fullmatch(pattern, line.strip())) is not None
        ]
        parsed = version_matches[0] if len(version_matches) == 1 else None
        if version.returncode != 0 or parsed != expected:
            raise ProbeError(
                f"{client} version is not pinned to {expected}: {combined}"
            )
        details: dict[str, object] = {"version": parsed}
        if client == "claude":
            doctor = subprocess.run(
                [executable, "doctor"],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            if doctor.returncode != 0:
                raise ProbeError(
                    "claude doctor failed: "
                    + (doctor.stdout + doctor.stderr).strip()
                )
            details["doctor"] = "passed"
            details["authentication"] = "not tested"
    except (OSError, ProbeError, subprocess.SubprocessError) as exc:
        _record_failed(
            evidence_path,
            platform_name=platform_name,
            label="client-install-health",
            subject=client,
            error=exc,
        )
        raise ProbeError(str(exc)) from exc
    append_evidence(
        evidence_path,
        platform_name=platform_name,
        item={
            "label": "client-install-health",
            "subject": client,
            "status": "passed",
            "details": details,
        },
    )


def _claude_write_command(executable: str, target: Path) -> tuple[list[str], str]:
    prompt = (
        "Use the Write tool exactly once for the target below. Write exactly the text "
        "`famulus assistant access probe` followed by one newline. If host policy denies "
        "the write, do not use another tool or path and report the denial.\n"
        f"TARGET={target}\n"
    )
    return (
        [
            executable,
            "--print",
            "--no-session-persistence",
            "--permission-mode",
            "acceptEdits",
            "--tools",
            "Write",
            "--output-format",
            "stream-json",
            "--verbose",
            prompt,
        ],
        prompt,
    )


def _claude_tool_denied(stdout: str, *, target: Path) -> bool:
    tool_ids: set[str] = set()
    results: dict[str, bool] = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProbeError("Claude stream-json output is malformed") from exc
        if not isinstance(event, dict):
            continue
        message = event.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if not isinstance(block, dict):
                continue
            if (
                block.get("type") == "tool_use"
                and block.get("name") == "Write"
                and isinstance(block.get("id"), str)
                and isinstance(block.get("input"), dict)
                and block["input"].get("file_path") == str(target)
            ):
                tool_ids.add(block["id"])
            if (
                block.get("type") == "tool_result"
                and isinstance(block.get("tool_use_id"), str)
                and isinstance(block.get("is_error"), bool)
            ):
                results[block["tool_use_id"]] = block["is_error"]
    matched = [results[tool_id] for tool_id in tool_ids if tool_id in results]
    if len(matched) != 1:
        raise ProbeError("Claude did not emit one structured Write tool result")
    return matched[0]


def claude_authenticated(*, state_path: Path, evidence_path: Path) -> None:
    state = _load_state(state_path)
    platform_name = str(state["platform"])
    token = uuid.uuid4().hex
    allowed = Path(str(state["allowed_roots"][0]))
    control = Path(str(state["control_root"]))
    allowed_canary = allowed / f".famulus-assistant-access-canary-{token}"
    control_canary = control / f".famulus-assistant-access-canary-{token}"
    roots = [allowed, control]
    environ = os.environ.copy()
    environ.update(
        _probe_environ(
            Path(str(state["home"])),
            Path(str(state["codex_home"])),
            Path(str(state["claude_home"])),
        )
    )
    try:
        if not (
            environ.get("ANTHROPIC_API_KEY")
            or environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        ):
            raise ProbeError("authenticated Claude probe requires an explicit CI credential")
        executable = shutil.which("claude")
        if executable is None:
            raise ProbeError("claude executable is not on PATH")
        allowed_command, _ = _claude_write_command(executable, allowed_canary)
        allowed_result = subprocess.run(
            allowed_command,
            cwd=str(state["source_root"]),
            env=environ,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        if (
            allowed_result.returncode != 0
            or not allowed_canary.is_file()
            or allowed_canary.read_bytes() != b"famulus assistant access probe\n"
            or _claude_tool_denied(allowed_result.stdout, target=allowed_canary)
        ):
            raise ProbeError("authenticated Claude did not write the allowed canary")
        allowed_canary.unlink()

        control_command, _ = _claude_write_command(executable, control_canary)
        control_result = subprocess.run(
            control_command,
            cwd=str(state["source_root"]),
            env=environ,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        if control_canary.exists():
            raise ProbeError("authenticated Claude wrote the synthetic sibling control")
        if control_result.returncode != 0:
            raise ProbeError("authenticated Claude control invocation failed")
        if not _claude_tool_denied(control_result.stdout, target=control_canary):
            raise ProbeError("authenticated Claude did not emit a structured control denial")
    except (OSError, ProbeError, subprocess.SubprocessError) as exc:
        error, cleaned = _cleanup_failure(roots, token, exc)
        _record_failed(
            evidence_path,
            platform_name=platform_name,
            label="host-enforcement",
            subject="Claude authenticated access",
            error=error,
        )
        if not cleaned:
            raise error from exc
        raise ProbeError(str(exc)) from exc
    try:
        _cleanup_canaries(roots, token)
    except ProbeError as exc:
        _record_failed(
            evidence_path,
            platform_name=platform_name,
            label="host-enforcement",
            subject="Claude authenticated access",
            error=exc,
        )
        raise
    append_evidence(
        evidence_path,
        platform_name=platform_name,
        qualification_updates={"claude_authenticated_access": "run"},
        item={
            "label": "host-enforcement",
            "subject": "Claude authenticated access",
            "status": "passed",
            "details": {
                "allowed_write": "succeeded",
                "control_write": "denied",
                "canaries_cleaned": True,
            },
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--platform", choices=("linux", "darwin", "win32"), required=True)
    prepare_parser.add_argument("--source-root", type=Path, required=True)
    prepare_parser.add_argument("--home", type=Path, required=True)
    prepare_parser.add_argument("--state", type=Path, required=True)
    prepare_parser.add_argument("--evidence", type=Path, required=True)
    prepare_parser.add_argument("--control-root", type=Path, required=True)
    for name in (
        "config-os-write",
        "host-enforcement",
        "restore",
        "claude-authenticated",
    ):
        selected = subparsers.add_parser(name)
        selected.add_argument("--state", type=Path, required=True)
        selected.add_argument("--evidence", type=Path, required=True)
    client = subparsers.add_parser("client-install-health")
    client.add_argument("--client", choices=("claude", "codex"), required=True)
    client.add_argument("--state", type=Path, required=True)
    client.add_argument("--evidence", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            prepare(
                platform_name=args.platform,
                source_root=args.source_root,
                home=args.home,
                state_path=args.state,
                evidence_path=args.evidence,
                control_root=args.control_root,
            )
        elif args.command == "config-os-write":
            config_os_write(state_path=args.state, evidence_path=args.evidence)
        elif args.command == "host-enforcement":
            host_enforcement(state_path=args.state, evidence_path=args.evidence)
        elif args.command == "restore":
            restore(state_path=args.state, evidence_path=args.evidence)
        elif args.command == "claude-authenticated":
            claude_authenticated(state_path=args.state, evidence_path=args.evidence)
        elif args.command == "client-install-health":
            client_install_health(
                client=args.client,
                state_path=args.state,
                evidence_path=args.evidence,
            )
        else:
            raise ProbeError(f"unsupported probe command: {args.command}")
    except (OSError, ProbeError, ValueError) as exc:
        print(f"assistant access probe failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
