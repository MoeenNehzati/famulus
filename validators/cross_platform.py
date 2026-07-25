"""Validate that shared skills avoid platform-specific runtime surfaces.

This validator enforces the Python-first portability policy for shared skills.
It currently checks:

- no tracked shell-script entrypoints under non-excluded skills
- no blueprint commands or suggested bash permissions that invoke shell scripts
  or obvious Unix/macOS/Windows-specific commands
- no obvious Python runtime shell usage such as ``shell=True``, ``os.system``,
  or ``subprocess`` calls with literal platform-specific commands

Blueprint-level portability is source-scoped. Behavioral sources whose runtime
dependencies apply to Linux, macOS, and Windows must use portable commands.
Module-level suggested permissions are checked as well.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.common.blueprint_graph import (  # noqa: E402
    BlueprintGraphError,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
    repository_relative_path,
)
from officina.common.blueprint_inventory import BlueprintInventoryError  # noqa: E402


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


def _validate_v4_blueprints(
    graph: RepositoryBlueprintGraph,
    repo_root: Path,
) -> list[str]:
    errors: list[str] = []
    repo_root = repo_root.resolve()
    for node in graph.nodes.values():
        rel_path = repository_relative_path(node.blueprint_path, repo_root)
        owner_relative = repository_relative_path(node.skill_root, repo_root)
        is_skill_node = (
            len(owner_relative.parts) == 2
            and owner_relative.parts[0] == "skills"
        )
        if (
            node.node_type == "module"
            and is_skill_node
        ):
            authority = node.declaration.get("authority")
            suggested = (
                authority.get("suggested_permissions")
                if isinstance(authority, dict)
                else None
            )
            bash_entries = (
                suggested.get("bash")
                if isinstance(suggested, dict)
                else None
            )
            if isinstance(bash_entries, list):
                for index, entry in enumerate(bash_entries):
                    if not isinstance(entry, dict):
                        continue
                    command = entry.get("command")
                    args_prefix = entry.get("args_prefix", [])
                    if (
                        isinstance(command, list)
                        and all(isinstance(token, str) for token in command)
                        and isinstance(args_prefix, list)
                        and all(
                            isinstance(token, str)
                            for token in args_prefix
                        )
                    ):
                        context = (
                            f"{rel_path}: authority.suggested_permissions."
                            f"bash[{index}]"
                        )
                        errors.extend(
                            _command_violations(
                                [*command, *args_prefix],
                                context,
                            )
                        )
            continue
        if (
            node.node_type != "behavioral_source"
            or not is_skill_node
        ):
            continue
        dependencies = node.declaration.get("runtime_dependencies", [])
        if not isinstance(dependencies, list):
            continue
        for index, dependency in enumerate(dependencies):
            if not isinstance(dependency, dict) or dependency.get("kind") != "binary":
                continue
            name = dependency.get("name")
            platforms = dependency.get("platforms")
            if not isinstance(name, str) or not isinstance(platforms, dict):
                continue
            if not all(
                platforms.get(platform) is True
                for platform in ("linux", "macos", "windows")
            ):
                continue
            context = f"{rel_path}: runtime_dependencies[{index}].name"
            errors.extend(_command_violations([name], context))
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
    skills_root = repo_root / "skills"
    if skills_root.is_dir() and any(
        skills_root.glob("*/blueprint.yaml")
    ):
        schema_root = repo_root / "references" / "blueprint"
        try:
            repository_graph = load_repository_blueprint_graph(
                repo_root,
                schema_root=(
                    schema_root
                    if (schema_root / "module.schema.json").is_file()
                    else None
                ),
            )
        except (
            BlueprintGraphError,
            BlueprintInventoryError,
            OSError,
            UnicodeError,
        ) as exc:
            errors.append(str(exc))
        else:
            errors.extend(
                _validate_v4_blueprints(repository_graph, repo_root)
            )
    for path in _iter_skill_files(repo_root):
        rel_path = path.relative_to(repo_root)
        if path.suffix in FORBIDDEN_SUFFIXES and _is_runtime_script(rel_path):
            errors.append(f"{rel_path}: shell scripts are not allowed in shared skills")
            continue
        if path.name == "blueprint.yaml":
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
