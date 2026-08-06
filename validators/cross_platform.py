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
from officina.common.python_source_cache import PythonSourceCache  # noqa: E402
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
    """Return whether a repository-relative path belongs to a skipped tree.

    Intent
    ------
    Exclude files outside the validator's live shared surfaces.

    Rationale
    ---------
    Tests, metadata, caches, and validator code are governed separately.

    Pseudocode
    ----------
    - return whether any path component is configured for exclusion

    Wraps
    -----
    - none
    """

    if any(part in _SKIP_PARTS for part in rel_path.parts):
        return True
    return False


def _iter_skill_files(
    repo_root: Path,
    graph: RepositoryBlueprintGraph | None,
):
    """Yield non-excluded, unregistered files below the skills directory.

    Intent
    ------
    Discover skill files subject to runtime portability checks.

    Rationale
    ---------
    Registered child artifacts and excluded trees have separate ownership.

    Pseudocode
    ----------
    - for path in skill tree:
      - if path is a governed file:
        - return path

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_excluded:
      why:
        computes: "Recognizes exclusions while this function filters file type and child ownership."
    """

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
    """Yield Python tests governed by the raw-Git annotation policy.

    Intent
    ------
    Discover ordinary repository and skill test modules.

    Rationale
    ---------
    Raw Git calls in tests require explicit policy annotations.

    Pseudocode
    ----------
    - for path in repository and skill test roots:
      - return Python test path

    Wraps
    -----
    - none
    """

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
    """Yield each non-test Python source file in the validator's live roots.

    Intent
    ------
    Discover live Python sources for composite-target validation.

    Rationale
    ---------
    Runtime process targets must keep path and entry point separate.

    Pseudocode
    ----------
    - for path in configured live roots:
      - if path is a unique non-test Python file:
        - return path

    Wraps
    -----
    - none
    """

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
    """Return whether a path is inside a skill's private runtime tree.

    Intent
    ------
    Identify executable files governed by the shell-script prohibition.

    Rationale
    ---------
    Non-runtime documents can legitimately share otherwise forbidden suffixes.

    Pseudocode
    ----------
    - return whether path components identify a skill runtime directory

    Wraps
    -----
    - none
    """

    parts = rel_path.parts
    return len(parts) >= 4 and parts[0] == "skills" and parts[2] == "_rtx"


def _allowed_platform_commands(rel_path: Path) -> set[str]:
    """Return platform commands allowed for a platform-specific adapter path.

    Intent
    ------
    Derive narrow command exceptions from explicit adapter identity.

    Rationale
    ---------
    Platform adapters may invoke their named platform's native commands.

    Pseudocode
    ----------
    - return all commands for an explicit cross-platform adapter
    - set allowed_commands = commands for platforms named by the filename
    - return allowed_commands

    Wraps
    -----
    - none
    """

    if rel_path in _CROSS_PLATFORM_ADAPTER_FILES:
        return set().union(*_PLATFORM_COMMAND_ALLOWLIST.values())
    name_lower = rel_path.name.lower()
    allowed: set[str] = set()
    for platform, aliases in _PLATFORM_COMMAND_ALIASES.items():
        if any(alias in name_lower for alias in aliases):
            allowed.update(_PLATFORM_COMMAND_ALLOWLIST[platform])
    return allowed


def _command_violations(tokens: list[str], context: str, allowed_commands: set[str] | None = None) -> list[str]:
    """Return portability findings for one literal command token sequence.

    Intent
    ------
    Detect shell scripts and disallowed platform-specific executables.

    Rationale
    ---------
    Central token checking keeps blueprint and Python diagnostics consistent.

    Pseudocode
    ----------
    - for token in normalized tokens:
      - if token has a forbidden script suffix:
        - set findings = findings plus shell-script finding
    - if leading command is forbidden and not allowed:
      - set findings = findings plus command finding
    - return findings

    Wraps
    -----
    - none
    """

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
    """Validate command portability declarations in a loaded blueprint graph.

    Intent
    ------
    Check skill permissions and universal binary dependencies for portability.

    Rationale
    ---------
    The prepared graph is the canonical source of validated declarations.

    Pseudocode
    ----------
    - for node in blueprint graph:
      - if node is a skill module:
        - set findings = permission command and composite-target findings
      - if node is a universal behavioral source:
        - set findings = binary portability findings
    - return findings

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .officina.common.repository_paths.repository_relative_path:
      why:
        constructs: "Builds stable repository-relative diagnostic paths."
    ._command_violations:
      why:
        constructs: "Builds portability findings for declaration commands."
    """

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
    """Return literal string elements from a list or tuple AST node.

    Intent
    ------
    Extract statically inspectable command argument sequences.

    Rationale
    ---------
    Dynamic command expressions cannot be classified mechanically here.

    Pseudocode
    ----------
    - return none unless node is a list or tuple
    - return strings when every element is a string constant

    Wraps
    -----
    - none
    """

    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    tokens: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            tokens.append(elt.value)
    return tokens


def _literal_command_tokens(node: ast.AST) -> list[str] | None:
    """Return command tokens from a literal sequence or whitespace-split string.

    Intent
    ------
    Normalize statically inspectable process commands into tokens.

    Rationale
    ---------
    Raw Git recognition supports both sequence and string subprocess forms.

    Pseudocode
    ----------
    - return literal sequence tokens when available
    - if node is a literal string:
      - return whitespace-split tokens
    - return none

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._literal_string_tokens:
      why:
        constructs: "Builds tokens from literal sequence nodes."
    """

    tokens = _literal_string_tokens(node)
    if tokens is not None:
        return tokens
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split()
    return None


def _is_true_constant(node: ast.AST) -> bool:
    """Return whether an AST node is the literal boolean value true.

    Intent
    ------
    Recognize statically explicit shell activation.

    Rationale
    ---------
    Only literal true proves that subprocess shell mode is enabled.

    Pseudocode
    ----------
    - return whether node is a constant whose value is true

    Wraps
    -----
    - none
    """

    return isinstance(node, ast.Constant) and node.value is True


def _is_subprocess_call(node: ast.Call) -> bool:
    """Return whether a call directly invokes a monitored subprocess function.

    Intent
    ------
    Recognize subprocess calls governed by portability checks.

    Rationale
    ---------
    Restrict matching to explicit functions on the subprocess module.

    Pseudocode
    ----------
    - return whether the call target is a monitored subprocess attribute

    Wraps
    -----
    - none
    """

    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
        and func.attr in _SUBPROCESS_ATTRS
    )


def _is_direct_run_git_call(node: ast.Call) -> bool:
    """Return whether a call targets a function or method named ``run_git``.

    Intent
    ------
    Recognize direct repository Git helper calls.

    Rationale
    ---------
    Both imported functions and object methods require policy annotations.

    Pseudocode
    ----------
    - return whether call target is a name or attribute named run_git

    Wraps
    -----
    - none
    """

    func = node.func
    return (
        isinstance(func, ast.Name)
        and func.id == "run_git"
    ) or (
        isinstance(func, ast.Attribute)
        and func.attr == "run_git"
    )


def _raw_git_kind(node: ast.Call) -> str | None:
    """Classify a direct Git execution call, or return ``None``.

    Intent
    ------
    Recognize repository helpers and literal subprocess Git commands.

    Rationale
    ---------
    Annotation enforcement applies only to mechanically identifiable raw Git calls.

    Pseudocode
    ----------
    - if node directly calls run_git:
      - return direct-helper classification
    - if node is a subprocess call with literal Git tokens:
      - return raw-Git classification
    - return none

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_direct_run_git_call:
      why:
        computes: "Recognizes calls to the repository Git helper."
    ._is_subprocess_call:
      why:
        computes: "Restricts literal command inspection to subprocess calls."

    InstantiationsFromRepo
    ----------------------
    ._literal_command_tokens:
      why:
        constructs: "Builds statically inspectable tokens for subprocess classification."
    """

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
    """Return the closest enclosing statement for an AST node.

    Intent
    ------
    Locate the statement whose preceding line may carry a raw-Git annotation.

    Rationale
    ---------
    Calls can be nested inside expressions while annotations govern statements.

    Pseudocode
    ----------
    - while current node exists:
      - if current node is a statement:
        - return current node
      - set current_node = parent node
    - return none

    Wraps
    -----
    - none
    """

    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, ast.stmt):
            return current
        current = parents.get(current)
    return None


def _validate_raw_git_test(
    path: Path,
    rel_path: Path,
    source_cache: PythonSourceCache,
) -> list[str]:
    """Validate required annotations on raw Git calls in one test file.

    Intent
    ------
    Report missing, unknown-category, or empty-reason raw-Git annotations.

    Rationale
    ---------
    Ordinary tests may bypass Git abstractions only with an explicit policy reason.

    Pseudocode
    ----------
    - set parsed_source = cached source text for file
    - set syntax_tree = cached syntax tree for file
    - set parent_map = syntax-tree parent relationships
    - for call in syntax tree:
      - set git_kind = classified call kind
      - set statement = nearest enclosing statement
      - if statement annotation is invalid:
        - set findings = findings plus annotation finding
    - return findings

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._raw_git_kind:
      why:
        constructs: "Builds the Git-call classification for each candidate call."
    ._nearest_statement:
      why:
        constructs: "Builds the statement boundary used to locate its annotation."
    """

    try:
        source, tree = source_cache.read_parse(path)
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
    source_cache: PythonSourceCache,
) -> list[str]:
    """Reject composite runtime-path and function targets in one Python file.

    Intent
    ------
    Find literal Python runner targets that combine a path and entry point.

    Rationale
    ---------
    Runtime paths and callable entries must remain separate except in one migration shim.

    Pseudocode
    ----------
    - set syntax_tree = cached syntax tree for file
    - set parent_map = syntax-tree parent relationships
    - for node in syntax_tree:
      - if literal is a forbidden composite target outside the legacy function:
        - set findings = findings plus composite-target finding
    - return findings

    Wraps
    -----
    - none
    """

    try:
        _source, tree = source_cache.read_parse(path)
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
    """Yield every list nested within a list-or-dictionary document tree.

    Intent
    ------
    Traverse permission documents without depending on their nesting shape.

    Rationale
    ---------
    Runner command token lists may appear at several declaration depths.

    Pseudocode
    ----------
    - if value is a list:
      - return value
      - for child in value:
        - set nested_lists = recursive traversal for child
    - if value is a dictionary:
      - for child in dictionary values:
        - set nested_lists = recursive traversal for child

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_nested_lists:
      why:
        computes: "Recursively traverses child containers while this function owns type dispatch and yielding."
    """

    if isinstance(value, list):
        yield value
        for item in value:
            yield from _iter_nested_lists(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_nested_lists(item)


def _validate_runner_permission_documents(repo_root: Path) -> list[str]:
    """Reject composite Python targets in skill permission documents.

    Intent
    ------
    Scan blueprint and pooled-review command lists for combined runner targets.

    Rationale
    ---------
    Permission documents are not all represented by the prepared blueprint graph.

    Pseudocode
    ----------
    - for document in skill permission documents:
      - set document_tree = parsed YAML
      - for tokens in nested document lists:
        - if tokens contain a composite Python runner target:
          - set findings = findings plus composite-target finding
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_nested_lists:
      why:
        computes: "Traverses candidate token lists while this function parses documents and reports matches."
    """

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
    """Return whether a call directly invokes ``os.system``.

    Intent
    ------
    Recognize an explicitly non-portable process-execution surface.

    Rationale
    ---------
    Attribute matching avoids classifying unrelated functions named system.

    Pseudocode
    ----------
    - return whether call target is the system attribute on os

    Wraps
    -----
    - none
    """

    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
        and func.attr == "system"
    )


def _validate_python(
    path: Path,
    rel_path: Path,
    source_cache: PythonSourceCache,
) -> list[str]:
    """Validate portable process execution in one skill Python file.

    Intent
    ------
    Report parse failures, shell execution, and literal non-portable commands.

    Rationale
    ---------
    Static AST inspection catches explicit process behavior without executing skill code.

    Pseudocode
    ----------
    - set allowed_commands = path-specific platform exceptions
    - set syntax_tree = cached syntax tree for file
    - for call in syntax tree:
      - if call invokes os.system or literal shell mode:
        - set findings = findings plus shell finding
      - if call is a subprocess call with literal tokens:
        - set findings = findings plus command findings
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_os_system:
      why:
        computes: "Recognizes direct os.system calls."
    ._is_subprocess_call:
      why:
        computes: "Recognizes monitored subprocess calls."
    ._is_true_constant:
      why:
        computes: "Recognizes explicit shell activation."
    ._command_violations:
      why:
        computes: "Returns portability findings for literal command tokens."

    InstantiationsFromRepo
    ----------------------
    ._allowed_platform_commands:
      why:
        constructs: "Builds path-specific command exceptions."
    ._literal_string_tokens:
      why:
        constructs: "Builds literal subprocess token sequences."
    """

    errors: list[str] = []
    allowed_commands = _allowed_platform_commands(rel_path)
    try:
        _source, tree = source_cache.read_parse(path)
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
    source_cache: PythonSourceCache,
) -> list[str]:
    """Run all cross-platform checks using supplied shared preparation.

    Intent
    ------
    Aggregate declaration, skill-file, test, live-source, and permission findings.

    Rationale
    ---------
    One orchestration boundary preserves finding order while accepting reusable preparation.

    Pseudocode
    ----------
    - if repository graph is absent and blueprints exist:
      - set repository_graph = loaded blueprint graph
    - set findings = blueprint declaration findings
    - for path in governed skill files:
      - set findings = findings plus runtime-file findings
    - for path in ordinary tests:
      - set findings = findings plus raw-Git findings
    - for path in live Python files:
      - set findings = findings plus composite-target findings
    - set findings = findings plus permission-document findings
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_skill_files:
      why:
        computes: "Provides governed skill files in scan order."
    ._is_runtime_script:
      why:
        computes: "Recognizes forbidden runtime script locations."
    ._iter_ordinary_test_files:
      why:
        computes: "Provides tests subject to raw-Git annotation checks."
    ._iter_live_python_files:
      why:
        computes: "Provides live sources subject to composite-target checks."

    InstantiationsFromRepo
    ----------------------
    .officina.common.blueprint_graph.load_repository_blueprint_graph:
      why:
        constructs: "Builds standalone blueprint preparation when no graph is supplied."
    ._validate_v4_blueprints:
      why:
        constructs: "Builds declaration portability findings."
    ._validate_python:
      why:
        constructs: "Builds per-skill Python portability findings."
    ._validate_raw_git_test:
      why:
        constructs: "Builds per-test raw-Git annotation findings."
    ._validate_composite_python_targets:
      why:
        constructs: "Builds per-source composite-target findings."
    ._validate_runner_permission_documents:
      why:
        constructs: "Builds permission-document composite-target findings."
    """

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
            errors.extend(_validate_python(path, rel_path, source_cache))
    for path in _iter_ordinary_test_files(repo_root):
        errors.extend(
            _validate_raw_git_test(
                path,
                path.relative_to(repo_root),
                source_cache,
            )
        )
    for path in _iter_live_python_files(repo_root):
        errors.extend(
            _validate_composite_python_targets(
                path,
                path.relative_to(repo_root),
                source_cache,
            )
        )
    errors.extend(_validate_runner_permission_documents(repo_root))
    return errors


def validate_with_graph(
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
) -> list[str]:
    """Validate a repository while reusing a caller-provided blueprint graph.

    Intent
    ------
    Supply direct callers with graph reuse and validator-owned source preparation.

    Rationale
    ---------
    Some callers share the expensive graph but do not own a Python source cache.

    Pseudocode
    ----------
    - set source_cache = repository Python source cache
    - return cross-platform findings with graph and source cache

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .officina.common.python_source_cache.PythonSourceCache:
      why:
        computes: "Provides validator-scoped source and syntax-tree preparation."

    InstantiationsFromRepo
    ----------------------
    ._validate:
      why:
        constructs: "Builds findings from the supplied graph and new source cache."
    """

    return _validate(repo_root, graph, PythonSourceCache(repo_root))


def validate(repo_root: Path) -> list[str]:
    """Validate a repository with validator-owned preparation.

    Intent
    ------
    Preserve the standalone validator entry point.

    Rationale
    ---------
    Direct callers cannot rely on pytest-managed graph or source fixtures.

    Pseudocode
    ----------
    - set source_cache = repository Python source cache
    - return cross-platform findings with standalone preparation

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .officina.common.python_source_cache.PythonSourceCache:
      why:
        computes: "Provides validator-scoped source and syntax-tree preparation."

    InstantiationsFromRepo
    ----------------------
    ._validate:
      why:
        constructs: "Builds findings with standalone graph and source preparation."
    """

    return _validate(repo_root, None, PythonSourceCache(repo_root))


def test_cross_platform(
    repo_root: Path,
    graph: RepositoryBlueprintGraph | None,
    python_source_cache: PythonSourceCache,
) -> list[str]:
    """Run cross-platform checks as a pytest item with shared fixtures.

    Intent
    ------
    Expose cross-platform validation to repository-check pytest collection.

    Rationale
    ---------
    Suite fixtures eliminate repeated graph loading and Python parsing.

    Pseudocode
    ----------
    - return cross-platform findings from shared fixtures

    Wraps
    -----
    - ._validate -> preprocess: forwards shared fixtures; postprocess: returns findings unchanged; fixed_arguments: none
    """

    return _validate(repo_root, graph, python_source_cache)


def main() -> int:
    """Run the validator for this repository and return a process exit code.

    Intent
    ------
    Print cross-platform findings for standalone command-line use.

    Rationale
    ---------
    Maintainers need a conventional script boundary outside pytest.

    Pseudocode
    ----------
    - set findings = repository cross-platform validation
    - if findings exist:
      - return failure status
    - return success status

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .validate:
      why:
        constructs: "Builds findings printed by the command-line boundary."
    """

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
