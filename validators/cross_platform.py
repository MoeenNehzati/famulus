"""Validate that shared skills avoid platform-specific runtime surfaces.

This validator enforces the Python-first portability policy for shared skills.
It currently checks:

- no tracked shell-script entrypoints under non-excluded skills
- no blueprint commands or suggested bash permissions that invoke shell scripts
  or obvious Unix/macOS/Windows-specific commands
- no obvious Python runtime shell usage such as ``shell=True``, ``os.system``,
  or ``subprocess`` calls with literal platform-specific commands

Blueprint-level portability is interface-scoped. Machine interfaces whose
``platform_support`` enables Linux, macOS, and Windows are checked for portable
invocation metadata. Platform-specific machine interfaces are allowed to name
platform-specific scheduler and host tools in their own dependency metadata.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import yaml


FORBIDDEN_SUFFIXES = {".sh", ".bash", ".bat", ".cmd", ".ps1"}
FORBIDDEN_COMMANDS = {
    "bash",
    "sh",
    "grep",
    "sed",
    "awk",
    "find",
    "chmod",
    "cp",
    "mv",
    "rm",
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "robocopy",
    "xcopy",
    "clip",
    "osascript",
    "open",
    "pbcopy",
    "pbpaste",
    "launchctl",
    "defaults",
}
PYTHON_SUFFIX = ".py"
_PLATFORM_COMMAND_ALIASES = {
    "linux": {"linux"},
    "macos": {"osx", "macos", "darwin"},
    "windows": {"windows", "win32"},
}
_PLATFORM_COMMAND_ALLOWLIST = {
    "linux": {"systemctl", "journalctl", "notify-send"},
    "macos": {"launchctl", "osascript", "open", "pbcopy", "pbpaste", "defaults"},
    "windows": {"cmd", "cmd.exe", "powershell", "powershell.exe", "robocopy", "xcopy", "clip", "schtasks"},
}
_CROSS_PLATFORM_ADAPTER_FILES = {
    Path("skills/recurring-tasks/_rtx/_assistant_desktop_notify.py"),
}

_SKIP_PARTS = {"tests", "validators", "__pycache__", ".git", ".claude-plugin", ".codex-plugin", "logs"}
_SUBPROCESS_ATTRS = {"run", "Popen", "call", "check_call", "check_output"}


def _tracked_files(repo_root: Path) -> set[Path] | None:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return {repo_root / p for p in result.stdout.splitlines()}


def _is_excluded(rel_path: Path) -> bool:
    if any(part in _SKIP_PARTS for part in rel_path.parts):
        return True
    return False


def _iter_skill_files(repo_root: Path):
    tracked = _tracked_files(repo_root)
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return
    for path in skills_root.rglob("*"):
        if not path.is_file():
            continue
        if tracked is not None and path not in tracked:
            continue
        rel_path = path.relative_to(repo_root)
        if _is_excluded(rel_path):
            continue
        yield path


def _is_runtime_script(rel_path: Path) -> bool:
    parts = rel_path.parts
    return len(parts) >= 4 and parts[0] == "skills" and parts[2] == "_rtx"


def _allowed_platform_commands(rel_path: Path) -> set[str]:
    if rel_path in _CROSS_PLATFORM_ADAPTER_FILES:
        return set().union(*_PLATFORM_COMMAND_ALLOWLIST.values())
    name_lower = rel_path.name.lower()
    allowed: set[str] = set()
    for platform, aliases in _PLATFORM_COMMAND_ALIASES.items():
        if any(alias in name_lower for alias in aliases):
            allowed.update(_PLATFORM_COMMAND_ALLOWLIST[platform])
    return allowed


def _command_violations(tokens: list[str], context: str, allowed_commands: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    allowed_commands = allowed_commands or set()
    lowered = [token.strip() for token in tokens if isinstance(token, str)]
    for token in lowered:
        leaf = Path(token).name
        if any(token.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
            errors.append(f"{context}: shell script token `{token}` is not allowed")
    if lowered:
        command = Path(lowered[0]).name
        if command in FORBIDDEN_COMMANDS and command not in allowed_commands:
            errors.append(f"{context}: command `{command}` is not cross-platform")
    return errors


def _validate_blueprint(path: Path, rel_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return [f"{rel_path}: failed to parse YAML: {exc}"]
    if not isinstance(raw, dict):
        return errors

    interfaces = raw.get("interfaces") or {}
    if isinstance(interfaces, dict):
        machine_interfaces = interfaces.get("machine") or {}
        if isinstance(machine_interfaces, dict):
            for interface_name, spec in machine_interfaces.items():
                if not isinstance(spec, dict):
                    continue
                if not _supports_all_platforms(spec):
                    continue
                invocation = spec.get("invocation") or {}
                if not isinstance(invocation, dict):
                    continue
                command = invocation.get("argv")
                if isinstance(command, list) and all(isinstance(token, str) for token in command):
                    context = f"{rel_path}: interfaces.machine.{interface_name}.invocation.argv"
                    for error in _command_violations(command, context):
                        errors.append(error)

    suggested = raw.get("suggested_permissions") or {}
    if isinstance(suggested, dict):
        bash_entries = suggested.get("bash") or []
        if isinstance(bash_entries, list):
            for index, entry in enumerate(bash_entries):
                if not isinstance(entry, dict):
                    continue
                command = entry.get("command")
                if isinstance(command, list) and all(isinstance(token, str) for token in command):
                    for error in _command_violations(command, f"{rel_path}: suggested_permissions.bash[{index}].command"):
                        errors.append(error)

    return errors


def _supports_all_platforms(spec: dict) -> bool:
    platforms = spec.get("platform_support")
    if not isinstance(platforms, dict):
        return True
    return all(platforms.get(platform) is True for platform in ("linux", "macos", "windows"))


def _literal_string_tokens(node: ast.AST) -> list[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    tokens: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            tokens.append(elt.value)
    return tokens


def _is_true_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_subprocess_call(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
        and func.attr in _SUBPROCESS_ATTRS
    )


def _is_os_system(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
        and func.attr == "system"
    )


def _validate_python(path: Path, rel_path: Path) -> list[str]:
    errors: list[str] = []
    allowed_commands = _allowed_platform_commands(rel_path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{rel_path}:{exc.lineno}: failed to parse Python: {exc.msg}"]

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if _is_os_system(node):
            errors.append(f"{rel_path}:{node.lineno}: os.system is not cross-platform")
            continue

        if not _is_subprocess_call(node):
            continue

        for kw in node.keywords:
            if kw.arg == "shell" and _is_true_constant(kw.value):
                errors.append(f"{rel_path}:{node.lineno}: shell=True is not allowed")

        if not node.args:
            continue
        tokens = _literal_string_tokens(node.args[0])
        if not tokens:
            continue
        for error in _command_violations(tokens, f"{rel_path}:{node.lineno}", allowed_commands):
            errors.append(error)

    return errors


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for path in _iter_skill_files(repo_root):
        rel_path = path.relative_to(repo_root)
        if path.suffix in FORBIDDEN_SUFFIXES and _is_runtime_script(rel_path):
            errors.append(f"{rel_path}: shell scripts are not allowed in shared skills")
            continue
        if path.name == "blueprint.yaml":
            errors.extend(_validate_blueprint(path, rel_path))
            continue
        if path.suffix == PYTHON_SUFFIX:
            errors.extend(_validate_python(path, rel_path))
    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Cross-platform violations found:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
