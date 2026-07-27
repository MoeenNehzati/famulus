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
import re
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.common.blueprint_graph import (  # noqa: E402
    BlueprintGraphError,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.common.blueprint_inventory import BlueprintInventoryError  # noqa: E402
from officina.common.repository_paths import repository_relative_path  # noqa: E402
from validators.skill_runtime_files import _registered_child_artifact  # noqa: E402


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
_RAW_GIT_CATEGORIES = {
    "ambient-config",
    "hooks",
    "object-format",
    "index-stages",
    "validator-isolation",
    "run-git-contract",
}
_RAW_GIT_ANNOTATION = re.compile(
    r"^\s*# famulus-raw-git: category=([^;]+); reason=(.+?)\s*$"
)
_COMPOSITE_PYTHON_TARGET = re.compile(
    r"(?<![A-Za-z0-9_./-])"
    r"_rtx/[A-Za-z0-9_./-]+\.py:[A-Za-z_][A-Za-z0-9_]*"
)
_PYTHON_RUNNER = "officina.runtime.python_machine_interface_runner"
_LEGACY_COMPOSITE_FUNCTION = (
    Path("src/officina/common/interface_injection_migration.py"),
    "_legacy_gateway",
)
REQUIRES_BLUEPRINT_GRAPH = True
BLUEPRINT_GRAPH_OPTIONAL = True


def _is_excluded(rel_path: Path) -> bool:
    if any(part in _SKIP_PARTS for part in rel_path.parts):
        return True
    return False


def _iter_skill_files(
    repo_root: Path,
    graph: RepositoryBlueprintGraph | None,
):
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return
    for path in skills_root.rglob("*"):
        if not path.is_file():
            continue
        rel_path = path.relative_to(repo_root)
        if _is_excluded(rel_path):
            continue
        if _registered_child_artifact(path, graph):
            continue
        yield path


def _iter_ordinary_test_files(repo_root: Path):
    tests_root = repo_root / "tests"
    if tests_root.is_dir():
        yield from sorted(tests_root.rglob("*.py"))
    skills_root = repo_root / "skills"
    if skills_root.is_dir():
        for tests_dir in sorted(
            (
                *skills_root.glob("*/tests"),
                *skills_root.glob("*/_rtx/tests"),
            )
        ):
            yield from sorted(tests_dir.rglob("*.py"))


def _iter_live_python_files(repo_root: Path):
    roots = (
        repo_root / "src",
        repo_root / "validators",
        repo_root / "scripts",
        repo_root / "docs_tooling",
        repo_root / "skills",
    )
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            rel_path = path.relative_to(repo_root)
            if "tests" in rel_path.parts or path in seen:
                continue
            seen.add(path)
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
        owner_relative = repository_relative_path(node.module_root, repo_root)
        is_skill_node = (
            len(owner_relative.parts) >= 2
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
                        tokens = [*command, *args_prefix]
                        errors.extend(
                            _command_violations(
                                tokens,
                                context,
                            )
                        )
                        if (
                            _PYTHON_RUNNER in tokens
                            and any(
                                _COMPOSITE_PYTHON_TARGET.search(token)
                                for token in tokens
                            )
                        ):
                            errors.append(
                                f"{context}: composite runner permission "
                                "target is not allowed"
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


def _literal_command_tokens(node: ast.AST) -> list[str] | None:
    tokens = _literal_string_tokens(node)
    if tokens is not None:
        return tokens
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split()
    return None


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


def _is_direct_run_git_call(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Name)
        and func.id == "run_git"
    ) or (
        isinstance(func, ast.Attribute)
        and func.attr == "run_git"
    )


def _raw_git_kind(node: ast.Call) -> str | None:
    if _is_direct_run_git_call(node):
        return "direct run_git"
    if not _is_subprocess_call(node) or not node.args:
        return None
    tokens = _literal_command_tokens(node.args[0])
    if tokens and Path(tokens[0]).name.lower() in {"git", "git.exe"}:
        return "raw Git"
    return None


def _nearest_statement(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.stmt | None:
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, ast.stmt):
            return current
        current = parents.get(current)
    return None


def _validate_raw_git_test(path: Path, rel_path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return []
    lines = source.splitlines()
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    errors: list[str] = []
    checked_statements: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = _raw_git_kind(node)
        if kind is None:
            continue
        statement = _nearest_statement(node, parents)
        if statement is None or id(statement) in checked_statements:
            continue
        checked_statements.add(id(statement))
        annotation_line = statement.lineno - 2
        annotation = (
            _RAW_GIT_ANNOTATION.fullmatch(lines[annotation_line])
            if 0 <= annotation_line < len(lines)
            else None
        )
        if annotation is None:
            errors.append(
                f"{rel_path}:{node.lineno}: {kind} call requires an immediately "
                "preceding famulus-raw-git annotation"
            )
            continue
        category = annotation.group(1).strip()
        reason = annotation.group(2).strip()
        if category not in _RAW_GIT_CATEGORIES:
            errors.append(
                f"{rel_path}:{node.lineno}: unknown famulus-raw-git category "
                f"`{category}`"
            )
        elif not reason:
            errors.append(
                f"{rel_path}:{node.lineno}: famulus-raw-git reason must not be empty"
            )
    return errors


def _validate_composite_python_targets(
    path: Path,
    rel_path: Path,
) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return []
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    errors: list[str] = []
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Constant)
            or not isinstance(node.value, str)
            or _COMPOSITE_PYTHON_TARGET.search(node.value) is None
        ):
            continue
        if rel_path == _LEGACY_COMPOSITE_FUNCTION[0]:
            current = parents.get(node)
            while current is not None:
                if (
                    isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and current.name == _LEGACY_COMPOSITE_FUNCTION[1]
                ):
                    break
                current = parents.get(current)
            else:
                current = None
            if current is not None:
                continue
        errors.append(
            f"{rel_path}:{node.lineno}: composite Python process target is not "
            "allowed; carry gateway path and process entry separately"
        )
    return errors


def _iter_nested_lists(value: object):
    if isinstance(value, list):
        yield value
        for item in value:
            yield from _iter_nested_lists(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_nested_lists(item)


def _validate_runner_permission_documents(repo_root: Path) -> list[str]:
    errors: list[str] = []
    paths = [
        *sorted((repo_root / "skills").glob("*/blueprint.yaml")),
        *sorted((repo_root / "skills").glob("*/.pooled-blueprint-review.yaml")),
    ]
    for path in paths:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        for tokens in _iter_nested_lists(document):
            if (
                _PYTHON_RUNNER not in tokens
                or not all(isinstance(token, str) for token in tokens)
            ):
                continue
            composite = next(
                (
                    token
                    for token in tokens
                    if _COMPOSITE_PYTHON_TARGET.search(token)
                ),
                None,
            )
            if composite is not None:
                rel_path = path.relative_to(repo_root)
                errors.append(
                    f"{rel_path}: composite runner permission target "
                    f"`{composite}` is not allowed"
                )
    return errors


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


def _validate(
    repo_root: Path,
    repository_graph: RepositoryBlueprintGraph | None,
) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if repository_graph is None and skills_root.is_dir() and any(
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
    elif repository_graph is not None:
        errors.extend(_validate_v4_blueprints(repository_graph, repo_root))
    for path in _iter_skill_files(repo_root, repository_graph):
        rel_path = path.relative_to(repo_root)
        if path.suffix in FORBIDDEN_SUFFIXES and _is_runtime_script(rel_path):
            errors.append(f"{rel_path}: shell scripts are not allowed in shared skills")
            continue
        if path.name == "blueprint.yaml":
            continue
        if path.suffix == PYTHON_SUFFIX:
            errors.extend(_validate_python(path, rel_path))
    for path in _iter_ordinary_test_files(repo_root):
        errors.extend(
            _validate_raw_git_test(
                path,
                path.relative_to(repo_root),
            )
        )
    for path in _iter_live_python_files(repo_root):
        errors.extend(
            _validate_composite_python_targets(
                path,
                path.relative_to(repo_root),
            )
        )
    errors.extend(_validate_runner_permission_documents(repo_root))
    return errors


def validate_with_graph(
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
) -> list[str]:
    return _validate(repo_root, graph)


def validate(repo_root: Path) -> list[str]:
    return _validate(repo_root, None)


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
