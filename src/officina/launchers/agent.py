"""Manage durable backend selection and launch selected-plugin agents."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Protocol

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from officina.common.atomic_files import atomic_replace_bytes
from officina.common.famulus_paths import resolve_famulus_paths
import officina.common.toml_io as toml_io

Backend = Literal["claude", "codex"]
_BACKENDS = frozenset({"claude", "codex"})
_AGENTS = frozenset({"assistant", "collab", "coauthor", "background_run"})
_AGENT_OVERRIDE = {
    "collab": "COLLAB_DEFAULT",
    "coauthor": "COAUTHOR_DEFAULT",
    "background_run": "BACKGROUND_RUN_DEFAULT",
}
_CODEX_PROFILE_OVERRIDE_AGENTS = frozenset({"background_run"})
_MANAGED_STATE_OVERRIDES = frozenset(
    {
        "ASSISTANT_LOGS",
        "EMAIL_TRIAGE_STATE_DIR",
        "LIST_MANAGER_CLOUD_LOCK_DIR",
        "LLM_WAKEUP_HOME",
    }
)


class LauncherConfigurationError(ValueError):
    """The durable launcher selection is missing, malformed, or unsupported."""


class ManifestRecorder(Protocol):
    def record(self, kind: str, *, path: str, **fields: object) -> None: ...


@dataclass(frozen=True)
class LauncherConfiguration:
    default_backend: Backend
    identity: str


def _configuration_bytes(default_backend: Backend) -> bytes:
    return (
        json.dumps(
            {"schema_version": 1, "default_backend": default_backend},
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _decode_configuration(raw: bytes) -> LauncherConfiguration:
    try:
        payload = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise LauncherConfigurationError(f"cannot read launchers.json: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "default_backend"}
        or payload.get("schema_version") != 1
        or payload.get("default_backend") not in _BACKENDS
    ):
        raise LauncherConfigurationError(
            "launchers.json must have exact schema "
            "{schema_version: 1, default_backend: claude|codex}"
        )
    return LauncherConfiguration(
        default_backend=payload["default_backend"],
        identity=hashlib.sha256(raw).hexdigest(),
    )


def load_launcher_configuration(*, config_root: Path) -> LauncherConfiguration:
    path = config_root / "launchers.json"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LauncherConfigurationError(f"cannot read launchers.json: {exc}") from exc
    return _decode_configuration(raw)


def ensure_launcher_configuration(
    *,
    config_root: Path,
    default_backend: Backend | None,
    manifest: ManifestRecorder | None = None,
) -> LauncherConfiguration:
    """Create, preserve, or explicitly replace the durable backend selection."""
    if default_backend is not None and default_backend not in _BACKENDS:
        raise LauncherConfigurationError(f"unsupported launcher backend: {default_backend!r}")
    path = config_root / "launchers.json"
    if path.exists() or path.is_symlink():
        current = load_launcher_configuration(config_root=config_root)
        if default_backend is None or current.default_backend == default_backend:
            return current
    elif default_backend is None:
        default_backend = "claude"
    assert default_backend is not None
    raw = _configuration_bytes(default_backend)
    config_root.mkdir(parents=True, exist_ok=True)
    atomic_replace_bytes(path, raw, allowed_root=config_root, mode=0o600)
    configured = _decode_configuration(raw)
    if manifest is not None:
        manifest.record(
            "file",
            path=str(path),
            sha256=configured.identity,
            preserve_if_modified=True,
        )
    return configured


def select_backend(
    *,
    agent: str,
    configuration: LauncherConfiguration,
    environ: Mapping[str, str],
) -> Backend:
    """Apply process-local overrides without changing durable configuration."""
    candidates = (_AGENT_OVERRIDE.get(agent), "ASSISTANT_DEFAULT")
    for name in candidates:
        if name and name in environ:
            value = environ[name]
            if value not in _BACKENDS:
                raise LauncherConfigurationError(
                    f"{name} must be 'claude' or 'codex', got {value!r}"
                )
            return value  # type: ignore[return-value]
    return configuration.default_backend


def _parse_agent_markdown(resources: Path, agent: str) -> tuple[str, str]:
    text = (resources / "agents" / f"{agent}.md").read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        return "", text.strip()
    description = ""
    for line in parts[1].splitlines():
        if line.strip().startswith("description:"):
            description = line.split(":", 1)[1].strip()
            break
    return description, parts[2].strip()


def _flatten_toml(table: dict[str, object], prefix: str = "") -> list[tuple[str, object]]:
    values: list[tuple[str, object]] = []
    for key, value in table.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            values.extend(_flatten_toml(value, prefix=f"{dotted}."))
        else:
            values.append((dotted, value))
    return values


def _codex_profile_overrides(resources: Path, agent: str) -> list[str]:
    if agent not in _CODEX_PROFILE_OVERRIDE_AGENTS:
        return []
    try:
        with toml_io.open(resources / "profiles", f"{agent}.config.toml") as stream:
            data = tomllib.loads(stream.read())
    except (OSError, ValueError):
        return []
    argv: list[str] = []
    for key, value in _flatten_toml(data):
        if key == "model_instructions_file":
            continue
        rendered = "true" if value is True else "false" if value is False else str(value)
        argv.extend(("-c", f"{key}={rendered}"))
    return argv


def build_agent_command(
    *, agent: str, backend: Backend, resources: Path, claude_home: Path, args: list[str]
) -> list[str]:
    if backend == "claude":
        description, prompt = _parse_agent_markdown(resources, agent)
        settings = resources / "profiles" / f"{agent}_claude_setting.json"
        if not settings.is_file():
            settings = claude_home / settings.name
        return [
            "claude",
            "--agent",
            agent,
            "--agents",
            json.dumps({agent: {"description": description, "prompt": prompt}}),
            "--settings",
            str(settings),
            *args,
        ]
    instructions = resources / "agents" / f"{agent}.md"
    return [
        "codex",
        "-c",
        f"model_instructions_file={instructions}",
        *_codex_profile_overrides(resources, agent),
        "--profile",
        agent,
        *args,
    ]


def _launch_command(command: list[str]) -> int:
    if sys.platform == "win32":
        executable = shutil.which(command[0])
        if executable is None:
            raise LauncherConfigurationError(f"{command[0]!r} not found on PATH")
        return subprocess.run([executable, *command[1:]], check=False).returncode
    os.execvp(command[0], command)
    return 1


def _launch_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """Drop mutable state selectors before launching an assistant."""
    return {name: value for name, value in environ.items() if name not in _MANAGED_STATE_OVERRIDES}


def _agent_args(argv: list[str]) -> tuple[str, bool, Backend | None, list[str]]:
    agent = argv[0]
    remaining = list(argv[1:])
    local = False
    backend: Backend | None = None
    while remaining:
        flag = remaining[0]
        if flag in {"-l", "--local"}:
            local = True
        elif flag == "--claude":
            backend = "claude"
        elif flag == "--codex":
            backend = "codex"
        else:
            break
        remaining.pop(0)
    return agent, local, backend, remaining


def _selected_plugin_main(plugin_root: Path, agent: str, remaining: list[str]) -> int:
    expected = plugin_root / "src" / "officina" / "launchers" / "agent.py"
    if not plugin_root.is_absolute() or Path(__file__).resolve() != expected.resolve():
        raise LauncherConfigurationError("selected plugin root does not own this launcher entry")
    environ = _launch_environment(os.environ)
    paths = resolve_famulus_paths(platform=sys.platform, home=Path.home(), environ=environ)
    selected_agent, local, explicit_backend, args = _agent_args([agent, *remaining])
    configured = load_launcher_configuration(config_root=paths.config_root)
    backend = explicit_backend or select_backend(
        agent=selected_agent, configuration=configured, environ=environ
    )
    os.environ["FAMULUS_LAUNCHER_RESOURCES"] = str(plugin_root)
    if not local:
        worker = paths.worker_root / selected_agent
        worker.mkdir(parents=True, exist_ok=True)
        os.chdir(worker)
    return _launch_command(build_agent_command(
        agent=selected_agent,
        backend=backend,
        resources=plugin_root,
        claude_home=Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude")),
        args=args,
    ))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0].startswith("-"):
        raise LauncherConfigurationError("selected plugin root and agent are required")
    return _selected_plugin_main(Path(argv[0]), argv[1], argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "LauncherConfiguration",
    "LauncherConfigurationError",
    "build_agent_command",
    "ensure_launcher_configuration",
    "load_launcher_configuration",
    "main",
    "select_backend",
]
