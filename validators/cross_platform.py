"""Validate that shared skills avoid platform-specific runtime surfaces.

This validator enforces the Python-first portability policy for shared skills.
It currently checks:

- no tracked shell-script entrypoints under non-excluded skills
- no blueprint commands or suggested bash permissions that invoke shell scripts
  or obvious Unix/macOS/Windows-specific commands
- no obvious Python runtime shell usage such as ``shell=True``, ``os.system``,
  or ``subprocess`` calls with literal platform-specific commands

Skills can opt out by setting ``cross_platform: false`` in their blueprint when
platform-specific behavior is an intentional part of the skill contract.
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

_SKIP_PARTS = {"tests", "validators", "__pycache__", ".git", ".claude-plugin", ".codex-plugin", "logs"}
_SUBPROCESS_ATTRS = {"run", "Popen", "call", "check_call", "check_output"}


def _tracked_files(repo_root: Path) -> set[Path] | None:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return {repo_root / p for p in result.stdout.splitlines()}


def _is_excluded(rel_path: Path) -> bool:
    if any(part in _SKIP_PARTS for part in rel_path.parts):
        return True
    return False


def _skill_root_for(rel_path: Path) -> Path | None:
    parts = rel_path.parts
    if len(parts) >= 2 and parts[0] == "skills":
        return Path(parts[0]) / parts[1]
    return None


def _load_blueprint(repo_root: Path, skill_root: Path) -> dict | None:
    path = repo_root / skill_root / "blueprint.yaml"
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    return raw if isinstance(raw, dict) else None


def _is_cross_platform_enabled(repo_root: Path, skill_root: Path, cache: dict[Path, bool]) -> bool:
    if skill_root in cache:
        return cache[skill_root]
    blueprint = _load_blueprint(repo_root, skill_root)
    enabled = True
    if blueprint is not None:
        enabled = blueprint.get("cross_platform", True) is not False
    cache[skill_root] = enabled
    return enabled


def _iter_skill_files(repo_root: Path):
    tracked = _tracked_files(repo_root)
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return
    enabled_cache: dict[Path, bool] = {}
    for path in skills_root.rglob("*"):
        if not path.is_file():
            continue
        if tracked is not None and path not in tracked:
            continue
        rel_path = path.relative_to(repo_root)
        if _is_excluded(rel_path):
            continue
        skill_root = _skill_root_for(rel_path)
        if skill_root is not None and not _is_cross_platform_enabled(repo_root, skill_root, enabled_cache):
            continue
        yield path


def _is_runtime_script(rel_path: Path) -> bool:
    parts = rel_path.parts
    return len(parts) >= 4 and parts[0] == "skills" and parts[2] == "scripts"


def _command_violations(tokens: list[str], context: str) -> list[str]:
    errors: list[str] = []
    lowered = [token.strip() for token in tokens if isinstance(token, str)]
    for token in lowered:
        leaf = Path(token).name
        if any(token.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
            errors.append(f"{context}: shell script token `{token}` is not allowed")
    if lowered:
        command = Path(lowered[0]).name
        if command in FORBIDDEN_COMMANDS:
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

    script_interfaces = raw.get("script_interfaces") or {}
    if isinstance(script_interfaces, dict):
        for interface_name, spec in script_interfaces.items():
            if not isinstance(spec, dict):
                continue
            command = spec.get("command")
            if isinstance(command, list) and all(isinstance(token, str) for token in command):
                for error in _command_violations(command, f"{rel_path}: script_interfaces.{interface_name}.command"):
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
        for error in _command_violations(tokens, f"{rel_path}:{node.lineno}"):
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
