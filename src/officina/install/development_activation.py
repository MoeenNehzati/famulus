"""Activate an isolated development installation across supported hosts."""
from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

from officina.common.famulus_paths import FamulusPaths
from officina.install.context import (
    DevelopmentBoundaryError,
    InstallationContext,
    build_development_environment,
    validate_development_boundaries,
)


class ActivationError(RuntimeError):
    """Development activation cannot safely proceed."""


_REMOVED_SELECTORS = ("AI", "FAMULUS_REPO_ROOT", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")
_SECURITY_WARNING = (
    "warning: Famulus development homes isolate state but are not a security sandbox; "
    "network, keychain, service, and filesystem effects may escape the checkout"
)


def _cmd_literal(value: str) -> str:
    return value.replace("^", "^^").replace("%", "%%")


def build_interactive_environment(
    context: InstallationContext, *, environ: Mapping[str, str], platform: str
) -> dict[str, str]:
    if context.mode != "development" or context.development_root is None:
        raise ActivationError("interactive development activation requires a development context")
    result = build_development_environment(
        context.development_root, environ=environ, platform=platform
    )
    if Path(result["CODEX_HOME"]) != context.codex_home or Path(
        result["CLAUDE_CONFIG_DIR"]
    ) != context.claude_home:
        raise ActivationError("development context assistant homes are not canonical")
    return result


def verify_managed_commands(
    context: InstallationContext,
    commands: Sequence[str],
    *,
    environ: Mapping[str, str],
) -> dict[str, Path]:
    """Require each managed command at exactly ``paths.user_bin``."""
    del environ  # Ambient PATH is intentionally irrelevant to this trust decision.
    resolved: dict[str, Path] = {}
    command_root = context.paths.user_bin.resolve(strict=False)
    for command in commands:
        if not command or Path(command).name != command:
            raise ActivationError(f"invalid managed command name: {command!r}")
        candidate = context.paths.user_bin / command
        if sys.platform == "win32" and not candidate.exists():
            candidate = candidate.with_suffix(".cmd")
        try:
            target = candidate.resolve(strict=True)
        except OSError as exc:
            raise ActivationError(
                f"managed command {command!r} is missing from {context.paths.user_bin}; refusing stable fallback"
            ) from exc
        if candidate.parent.resolve(strict=True) != command_root:
            raise ActivationError(f"managed command {command!r} is outside the development command directory")
        if not candidate.is_file() or (sys.platform != "win32" and not os.access(candidate, os.X_OK)):
            raise ActivationError(f"managed command {command!r} is not executable at {candidate}")
        resolved[command] = target
    return resolved


_STABLE_ENVIRONMENT_ROOTS = (
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "APPDATA",
    "LOCALAPPDATA",
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
)


def _context_payload(
    context: InstallationContext,
    managed_commands: Sequence[str],
    *,
    platform: str,
    home: Path,
    environ: Mapping[str, str],
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "mode": context.mode,
        "source_root": str(context.source_root),
        "development_root": str(context.development_root) if context.development_root else None,
        "installation_id": context.installation_id,
        "selected_home": str(context.selected_home),
        "codex_home": str(context.codex_home),
        "claude_home": str(context.claude_home),
        "paths": {name: str(value) for name, value in vars(context.paths).items()},
        "managed_commands": list(managed_commands),
        "stable_boundary": {
            "platform": platform,
            "home": str(home),
            "environ": {
                name: environ[name]
                for name in _STABLE_ENVIRONMENT_ROOTS
                if name in environ
            },
        },
    }


def _context_from_payload(payload: Mapping[str, object]) -> tuple[InstallationContext, tuple[str, ...]]:
    schema_version = payload.get("schema_version")
    if schema_version not in (1, 2) or payload.get("mode") != "development":
        raise ActivationError("unsupported development activation descriptor")
    raw_paths = payload.get("paths")
    raw_commands = payload.get("managed_commands")
    raw_boundary = payload.get("stable_boundary")
    if (
        not isinstance(raw_paths, dict)
        or not isinstance(raw_commands, list)
        or not isinstance(raw_boundary, dict)
    ):
        raise ActivationError("malformed development activation descriptor")
    try:
        raw_development_root = payload["development_root"]
        if not isinstance(raw_development_root, str):
            raise TypeError("invalid development root")
        development_root = Path(raw_development_root)
        if schema_version == 1:
            selected_home = development_root / ".famulus" / "home"
        else:
            raw_selected_home = payload["selected_home"]
            if not isinstance(raw_selected_home, str):
                raise TypeError("invalid selected home")
            selected_home = Path(raw_selected_home)
        paths = FamulusPaths(**{name: Path(value) for name, value in raw_paths.items()})
        context = InstallationContext(
            mode="development",
            source_root=Path(str(payload["source_root"])),
            development_root=development_root,
            paths=paths,
            selected_home=selected_home,
            codex_home=Path(str(payload["codex_home"])),
            claude_home=Path(str(payload["claude_home"])),
            installation_id=str(payload["installation_id"]),
        )
        boundary_platform = raw_boundary["platform"]
        boundary_home = raw_boundary["home"]
        boundary_environ = raw_boundary["environ"]
        if (
            not isinstance(boundary_platform, str)
            or not isinstance(boundary_home, str)
            or not isinstance(boundary_environ, dict)
            or not all(
                isinstance(name, str) and isinstance(value, str)
                for name, value in boundary_environ.items()
            )
        ):
            raise TypeError("invalid stable boundary")
    except (KeyError, TypeError, ValueError) as exc:
        raise ActivationError("malformed development activation descriptor") from exc
    commands = tuple(str(item) for item in raw_commands)
    validate_development_boundaries(
        context,
        operation="activate",
        platform=boundary_platform,
        home=Path(boundary_home),
        environ=boundary_environ,
    )
    return context, commands


def install_development_activation(
    context: InstallationContext,
    *,
    python_executable: Path,
    managed_commands: Sequence[str],
    platform: str,
    home: Path,
    environ: Mapping[str, str],
) -> Path:
    """Generate the stable activation bootstraps and immutable descriptor."""
    try:
        local_root = validate_development_boundaries(
            context,
            operation="install activation",
            platform=platform,
            home=home,
            environ=environ,
        )
    except DevelopmentBoundaryError as exc:
        raise ActivationError(str(exc)) from exc
    if not python_executable.is_absolute() or not python_executable.is_file():
        raise ActivationError("managed Python executable must be an existing absolute file")
    activation_bin = local_root / "bin"
    resolved_activation_bin = activation_bin.resolve(strict=False)
    if local_root not in resolved_activation_bin.parents:
        raise ActivationError(
            f"activation bin resolves outside .famulus: {activation_bin} -> {resolved_activation_bin}"
        )
    activation_bin.mkdir(parents=True, exist_ok=True)
    descriptor = local_root / "activation.json"
    payload = json.dumps(
        _context_payload(
            context,
            managed_commands,
            platform=platform,
            home=home,
            environ=environ,
        ),
        ensure_ascii=False,
        sort_keys=True,
    ) + "\n"
    descriptor.write_text(payload, encoding="utf-8")
    bootstrap = activation_bin / "famulus-env"
    bootstrap.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(str(python_executable))
        + " -m officina.install.development_activation --descriptor "
        + shlex.quote(str(descriptor))
        + ' "$@"\n',
        encoding="utf-8",
    )
    bootstrap.chmod(bootstrap.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    windows_bootstrap = activation_bin / "famulus-env.cmd"
    windows_bootstrap.write_text(
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\n"'
        + _cmd_literal(str(python_executable))
        + '" -m officina.install.development_activation --descriptor "'
        + _cmd_literal(str(descriptor))
        + '" %*\r\n',
        encoding="utf-8",
    )
    return bootstrap


def _export_script(context: InstallationContext, environ: Mapping[str, str], shell: str) -> str:
    platform = "win32" if shell == "cmd" else sys.platform
    activated = build_interactive_environment(context, environ=environ, platform=platform)
    changed = {
        name: value
        for name, value in activated.items()
        if environ.get(name) != value
    }
    removed = [name for name in _REMOVED_SELECTORS if name in environ]
    if shell in ("sh", "bash", "zsh"):
        lines = [f"export {name}={shlex.quote(value)}" for name, value in sorted(changed.items())]
        lines.extend(f"unset {name}" for name in removed)
        return "\n".join(lines) + "\n"
    if shell == "cmd":
        if any("\r" in value or "\n" in value for value in changed.values()):
            raise ActivationError("environment values containing CR/LF cannot be exported")
        lines = [
            f'set "{name}={_cmd_literal(value)}"'
            for name, value in sorted(changed.items())
        ]
        lines.extend(f'set "{name}="' for name in removed)
        return "\r\n".join(lines) + "\r\n"
    raise ActivationError(f"unsupported export shell: {shell!r}")


def _exec_command(command: list[str], env: Mapping[str, str], *, platform: str) -> None:
    """Replace this process, routing Windows batch commands through COMSPEC."""
    if platform == "win32" and Path(command[0]).suffix.lower() in (".cmd", ".bat"):
        comspec = env.get("COMSPEC")
        if not comspec or not Path(comspec).is_absolute():
            raise ActivationError("COMSPEC must name an absolute command processor on Windows")
        os.execve(
            comspec,
            [
                comspec,
                "/d",
                "/s",
                "/c",
                subprocess.list2cmdline(["call", *command]),
            ],
            dict(env),
        )
    os.execvpe(command[0], command, dict(env))


def _run_descriptor(descriptor: Path, argv: Sequence[str]) -> int:
    try:
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
        context, managed_commands = _context_from_payload(payload)
        managed = verify_managed_commands(context, managed_commands, environ=os.environ)
        if not argv:
            raise ActivationError("expected 'exec -- <argv>' or 'export --shell <shell>'")
        if argv[0] == "export":
            if len(argv) != 3 or argv[1] != "--shell":
                raise ActivationError("usage: famulus-env export --shell <sh|bash|zsh|cmd>")
            output = _export_script(context, os.environ, argv[2])
            sys.stdout.write(output)
            return 0
        if argv[0] == "exec":
            if len(argv) < 3 or argv[1] != "--":
                raise ActivationError("usage: famulus-env exec -- <argv>")
            command = list(argv[2:])
            if command[0] in managed:
                command[0] = str(managed[command[0]])
            env = build_interactive_environment(context, environ=os.environ, platform=sys.platform)
            print(_SECURITY_WARNING, file=sys.stderr)
            _exec_command(command, env, platform=sys.platform)
        raise ActivationError(f"unsupported famulus-env action: {argv[0]!r}")
    except (OSError, ValueError, json.JSONDecodeError, ActivationError) as exc:
        print(f"famulus-env: {exc}", file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[0] != "--descriptor":
        print("famulus-env: missing --descriptor <path>", file=sys.stderr)
        return 2
    descriptor = Path(args[1])
    if not descriptor.is_absolute():
        print("famulus-env: descriptor path must be absolute", file=sys.stderr)
        return 2
    return _run_descriptor(descriptor, args[2:])


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ActivationError",
    "build_interactive_environment",
    "install_development_activation",
    "main",
    "verify_managed_commands",
]
