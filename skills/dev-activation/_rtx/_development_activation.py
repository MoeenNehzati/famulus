"""Activate one Famulus checkout in an isolated development environment."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Mapping, Sequence


class ActivationError(RuntimeError):
    pass

_REMOVED = (
    "AI", "FAMULUS_REPO_ROOT", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    "VIRTUAL_ENV_PROMPT", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "UV_PROJECT_ENVIRONMENT",
    "PIP_CONFIG_FILE", "PIP_REQUIRE_VIRTUALENV", "ASSISTANT_LOGS", "EMAIL_TRIAGE_STATE_DIR",
    "LIST_MANAGER_CLOUD_LOCK_DIR", "LLM_WAKEUP_HOME",
)
_XDG = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR")

def _checkout_root(checkout: Path) -> Path:
    path = Path(checkout)
    if not path.is_absolute() or not path.is_dir():
        raise ActivationError(f"checkout must be an existing directory: {path}")
    return path.resolve()

def _validated_checkout(checkout: Path) -> Path:
    root = _checkout_root(checkout)
    required = (
        root / "skills", root / ".claude-plugin" / "plugin.json",
        root / ".codex-plugin" / "plugin.json", root / ".mcp.json", root / "mcp_server.py",
        root / "skills" / "dev-activation" / "_rtx" / "_development_activation.py",
    )
    if not required[0].is_dir() or any(not path.is_file() for path in required[1:]):
        raise ActivationError("checkout plugin metadata is incomplete")
    local = root / ".famulus"
    if local.is_symlink():
        raise ActivationError("checkout .famulus symlink may resolve outside the checkout")
    return root

def _directories(root: Path, platform: str) -> tuple[Path, ...]:
    home = root / ".famulus" / "home"
    paths = [home, home / ".codex", home / ".claude"]
    if platform == "win32":
        paths += [home / "AppData" / "Roaming", home / "AppData" / "Local"]
    elif platform != "darwin":
        paths += [home / ".config", home / ".local" / "share", home / ".local" / "state"]
    return tuple(paths)

def _check_boundaries(root: Path, paths: tuple[Path, ...]) -> None:
    for path in paths:
        candidate = path
        while candidate != root:
            if candidate.is_symlink():
                raise ActivationError(f"activation root symlink may resolve outside the checkout: {candidate}")
            candidate = candidate.parent
        if path.exists() and not path.is_dir():
            raise ActivationError(f"activation root is not a directory: {path}")

def _environment(root: Path, environ: Mapping[str, str], platform: str) -> dict[str, str]:
    if platform not in {"linux", "darwin", "win32"}:
        raise ActivationError(f"unsupported platform: {platform!r}")
    home = root / ".famulus" / "home"
    result = dict(environ)
    normal_home = environ.get("USERPROFILE" if platform == "win32" else "HOME")
    configured_git = environ.get("GIT_CONFIG_GLOBAL")
    git_config = Path(configured_git) if configured_git else Path(normal_home) / ".gitconfig" if normal_home else None
    for name in (*_REMOVED, *_XDG):
        result.pop(name, None)
    result.update(HOME=str(home), CODEX_HOME=str(home / ".codex"), CLAUDE_CONFIG_DIR=str(home / ".claude"))
    if platform == "win32":
        result.update(USERPROFILE=str(home), APPDATA=str(home / "AppData" / "Roaming"), LOCALAPPDATA=str(home / "AppData" / "Local"))
    elif platform != "darwin":
        result.update(XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local" / "share"), XDG_STATE_HOME=str(home / ".local" / "state"))
    if git_config is not None and git_config.is_file():
        result["GIT_CONFIG_GLOBAL"] = str(git_config)
    else:
        result.pop("GIT_CONFIG_GLOBAL", None)
    return result

def _activate(checkout: Path, environ: Mapping[str, str], platform: str, create: bool) -> tuple[Path, dict[str, str]]:
    root = _validated_checkout(checkout)
    environment = _environment(root, environ, platform)
    paths = _directories(root, platform)
    _check_boundaries(root, paths)
    if create:
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
    elif missing := next((path for path in paths if not path.is_dir()), None):
        raise ActivationError(f"activation root is missing: {missing}")
    return root, environment

def create_activation(checkout: Path, *, environ: Mapping[str, str], platform: str) -> dict[str, str]:
    return _activate(checkout, environ, platform, True)[1]

def validate_activation(checkout: Path, *, environ: Mapping[str, str], platform: str) -> dict[str, str]:
    return _activate(checkout, environ, platform, False)[1]

def build_activation_environment(checkout: Path, *, environ: Mapping[str, str], platform: str) -> dict[str, str]:
    return _environment(_checkout_root(checkout), environ, platform)

def host_command(checkout: Path, host: str, arguments: list[str]) -> list[str]:
    root = _validated_checkout(checkout)
    commands = {"claude": ["claude", "--plugin-dir", str(root)], "codex": ["codex", "-C", str(root)]}
    try:
        return [*commands[host], *arguments]
    except KeyError as exc:
        raise ActivationError(f"unsupported development host: {host!r}") from exc

def _export_environment(activated: Mapping[str, str], inherited: Mapping[str, str], shell: str) -> str:
    changed = {name: value for name, value in activated.items() if inherited.get(name) != value}
    removed = sorted(name for name in inherited if name not in activated)
    if shell in {"sh", "bash", "zsh"}:
        return "\n".join([*(f"export {name}={shlex.quote(value)}" for name, value in sorted(changed.items())), *(f"unset {name}" for name in removed)]) + "\n"
    if shell == "cmd":
        if any("\r" in value or "\n" in value for value in changed.values()):
            raise ActivationError("environment values containing CR/LF cannot be exported")
        escaped = ((name, value.replace("^", "^^").replace("%", "%%")) for name, value in sorted(changed.items()))
        return "\r\n".join([*(f'set "{name}={value}"' for name, value in escaped), *(f'set "{name}="' for name in removed)]) + "\r\n"
    raise ActivationError(f"unsupported export shell: {shell!r}")

def _report(root: Path, platform: str) -> dict[str, object]:
    runtime = root / "skills" / "dev-activation" / "_rtx" / "_development_activation.py"
    prefix = ["python", str(runtime), "launch", "--checkout", str(root)]
    return {"checkout": str(root), "isolated_home": str(root / ".famulus" / "home"), "claude": [*prefix, "--host", "claude"], "codex": [*prefix, "--host", "codex"], "platform": platform}

def run_action(action: str, checkout: Path, *, environ: Mapping[str, str], platform: str) -> object:
    root, environment = _activate(checkout, environ, platform, action != "validate")
    return _report(root, platform) if action == "report" else environment

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev-activation")
    parser.add_argument("action", choices=("create", "validate", "report", "export", "exec", "launch"))
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--platform", default=sys.platform)
    parser.add_argument("--shell", choices=("sh", "bash", "zsh", "cmd"))
    parser.add_argument("--host", choices=("claude", "codex"))
    raw = list(sys.argv[1:] if argv is None else argv)
    separator = raw.index("--") if "--" in raw else len(raw)
    args = parser.parse_args(raw[:separator])
    command = raw[separator + 1:] if separator < len(raw) else []
    try:
        if args.action in {"create", "validate", "report"}:
            result = run_action(args.action, args.checkout, environ=os.environ, platform=args.platform)
            if args.action == "report":
                print(json.dumps(result, ensure_ascii=False))
            return 0
        environment = create_activation(args.checkout, environ=os.environ, platform=args.platform)
        if args.action == "export":
            if args.shell is None:
                raise ActivationError("export requires --shell")
            sys.stdout.write(_export_environment(environment, os.environ, args.shell))
            return 0
        if args.action == "launch":
            if args.host is None:
                raise ActivationError("launch requires --host")
            command = host_command(args.checkout, args.host, command)
        if not command:
            raise ActivationError(f"{args.action} requires a command after --")
        os.execvpe(command[0], command, environment)
    except (ActivationError, OSError) as exc:
        print(f"dev-activation: {exc}", file=sys.stderr)
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
