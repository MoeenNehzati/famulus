"""Require Python dispatch() calls to identify the owning skill correctly."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from officina.blueprints.graph import RepositoryBlueprintGraph
from officina.common.python_source_cache import PythonSourceCache
from officina.runtime.python_machine_interface import (
    analyze_dispatch_call_declarations,
)

REQUIRES_BLUEPRINT_GRAPH = True
BLUEPRINT_GRAPH_OPTIONAL = True


def _python_files(skill_dir: Path) -> list[Path]:
    """Collect non-test Python files from a skill's runtime directories.

    Intent
    ------
    Limit fallback dispatch validation to executable Python sources under ``_rtx``
    and ``bin`` while excluding their test subtrees.

    Rationale
    ---------
    The fallback scan must match the validator's historical source boundary when
    no version-5 ownership graph is available.

    Pseudocode
    ----------
    - set paths = empty list
    - for subdir in supported runtime subdirectories:
      - if subdir is missing:
        - continue
      - set paths = paths plus Python files outside the tests subtree
    - return collected paths

    Wraps
    -----
    - none
    """
    paths: list[Path] = []
    for subdir in ("_rtx", "bin"):
        root = skill_dir / subdir
        if not root.is_dir():
            continue
        tests_root = root / "tests"
        paths.extend(
            path
            for path in root.rglob("*.py")
            if path.is_file() and not path.is_relative_to(tests_root)
        )
    return paths


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Map simple module-level names to their literal string values.

    Intent
    ------
    Resolve the constant form accepted for dispatch caller declarations.

    Rationale
    ---------
    Dispatch callers may use either a string literal or a directly assigned
    module-level string constant without requiring general Python evaluation.

    Pseudocode
    ----------
    - set constants = literal strings from simple module-level name assignments
    - return the name-to-string mapping

    Wraps
    -----
    - none
    """
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            constants[target.id] = value.value
    return constants


def _dispatch_aliases(tree: ast.AST) -> tuple[set[str], set[str], list[int]]:
    """Discover supported dispatch imports and removed legacy import lines.

    Intent
    ------
    Identify names that can denote ``dispatch`` calls and locations importing the
    removed ``famulus.dispatcher`` module.

    Rationale
    ---------
    Call recognition must respect import aliases while reporting legacy imports at
    their source lines.

    Pseudocode
    ----------
    - set direct_aliases = direct dispatch imports from supported modules
    - set module_aliases = supported dispatcher module imports
    - set legacy_lines = source lines importing the legacy dispatcher
    - return all three collections

    Wraps
    -----
    - none
    """
    direct: set[str] = set()
    modules: set[str] = set()
    legacy_famulus_lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "script_dispatcher",
            "officina.dispatcher",
        }:
            for alias in node.names:
                if alias.name == "dispatch":
                    direct.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "famulus.dispatcher":
            legacy_famulus_lines.append(getattr(node, "lineno", 0))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"script_dispatcher", "officina.dispatcher"}:
                    modules.add(alias.asname or alias.name)
                elif alias.name == "famulus.dispatcher":
                    legacy_famulus_lines.append(getattr(node, "lineno", 0))
    return direct, modules, legacy_famulus_lines


def _is_dispatch_call(node: ast.Call, direct_aliases: set[str], module_aliases: set[str]) -> bool:
    """Return whether a call targets one of the discovered dispatch aliases.

    Intent
    ------
    Recognize direct and module-qualified dispatch calls without matching unrelated
    functions or attributes.

    Rationale
    ---------
    Caller-skill validation applies only to calls reached through supported
    dispatcher imports.

    Pseudocode
    ----------
    - if callee is a name:
      - return whether callee is in direct aliases
    - if callee is a dispatch attribute on a name:
      - return whether its base is in module aliases
    - return false

    Wraps
    -----
    - none
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in direct_aliases
    if isinstance(func, ast.Attribute) and func.attr == "dispatch" and isinstance(func.value, ast.Name):
        return func.value.id in module_aliases
    return False


def _resolve_string(expr: ast.AST, constants: dict[str, str]) -> str | None:
    """Resolve an accepted dispatch argument expression to a string.

    Intent
    ------
    Support literal strings and references to known module-level string constants.

    Rationale
    ---------
    The validator deliberately rejects dynamic expressions so caller ownership is
    mechanically verifiable.

    Pseudocode
    ----------
    - return a literal string value directly
    - if expression is a name:
      - return constants[expression]
    - return none for every other expression

    Wraps
    -----
    - none
    """
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.Name):
        return constants.get(expr.id)
    return None


def _deepest_module_id(
    graph: RepositoryBlueprintGraph,
    path: Path,
    fallback: str,
) -> str:
    """Select the deepest graph module that owns a source path.

    Intent
    ------
    Determine the expected caller module for fallback scans when nested modules
    contain the source file.

    Rationale
    ---------
    Nested module ownership is more specific than the enclosing skill identifier.

    Pseudocode
    ----------
    - set matches = module nodes whose roots contain the source path
    - return the fallback identifier if there are no matches
    - set owner = match with the deepest root and stable identifier tie-break
    - return owner module identifier

    Wraps
    -----
    - none
    """
    matches = [
        node
        for node in graph.nodes.values()
        if node.node_type == "module"
        and path.is_relative_to(node.module_root)
    ]
    if not matches:
        return fallback
    return max(
        matches,
        key=lambda node: (
            len(node.module_root.parts),
            node.node_id,
        ),
    ).node_id


def _validate(
    repo_root: Path,
    graph: RepositoryBlueprintGraph | None,
    source_cache: PythonSourceCache,
) -> list[str]:
    """Validate dispatch caller declarations across the selected Python sources.

    Intent
    ------
    Report legacy imports, malformed ``DispatchCall`` declarations, and dispatch
    calls whose caller identity is missing, dynamic, or inconsistent with ownership.

    Rationale
    ---------
    A shared implementation lets compatibility entry points and pytest reuse the
    same validation semantics while optionally sharing graph and AST preparation.

    Pseudocode
    ----------
    - set candidates = discovered skill runtime sources and deepest module owners
    - set parsed_candidates = source_cache.read_parse for each candidate
    - set errors = legacy import and invalid DispatchCall findings
    - set errors = errors plus invalid dispatch caller-skill findings
    - return findings in scan order

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._python_files:
      why:
        computes: "Discovers fallback runtime Python candidates for each skill."
    ._deepest_module_id:
      why:
        computes: "Selects expected nested-module ownership during fallback discovery."
    .officina.runtime.python_machine_interface.analyze_dispatch_call_declarations:
      why:
        computes: "Enumerates declared DispatchCall ownership metadata in each module."

    InstantiationsFromRepo
    ----------------------
    ._module_string_constants:
      why:
        constructs: "Builds the literal constant table used to resolve caller arguments."
    ._dispatch_aliases:
      why:
        constructs: "Builds dispatch alias and legacy-import collections for each module."
    ._is_dispatch_call:
      why:
        constructs: "Produces the decision to subject an AST call to caller-skill validation."
    ._resolve_string:
      why:
        constructs: "Produces the resolved caller identity used for ownership comparison."
    """
    errors: list[str] = []
    candidates: list[tuple[Path, str]] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors
    for blueprint_path in sorted(skills_root.glob("*/blueprint.yaml")):
        skill_name = blueprint_path.parent.name
        for path in _python_files(blueprint_path.parent):
            expected_module_id = (
                _deepest_module_id(graph, path, skill_name)
                if graph is not None
                else skill_name
            )
            candidates.append((path, expected_module_id))

    for path, expected_module_id in candidates:
        rel = path.relative_to(repo_root).as_posix()
        try:
            _source, tree = source_cache.read_parse(path)
        except (SyntaxError, UnicodeDecodeError):
            continue

        constants = _module_string_constants(tree)
        (
            direct_aliases,
            module_aliases,
            legacy_famulus_lines,
        ) = _dispatch_aliases(tree)
        for lineno in legacy_famulus_lines:
            errors.append(
                f"{rel}:{lineno}: import officina.dispatcher instead of removed famulus.dispatcher"
            )
        for declaration in analyze_dispatch_call_declarations(tree):
            if declaration.caller_module_id is None:
                errors.append(
                    f"{rel}:{declaration.lineno}: DispatchCall() must include caller_module_id "
                    "as a literal or module-level string constant"
                )
            elif declaration.caller_module_id != expected_module_id:
                errors.append(
                    f"{rel}:{declaration.lineno}: caller_module_id resolves to "
                    f"`{declaration.caller_module_id}`, expected `{expected_module_id}`"
                )

        if not direct_aliases and not module_aliases:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            is_dispatch_call = _is_dispatch_call(node, direct_aliases, module_aliases)
            if not is_dispatch_call:
                continue

            caller_expr = None
            for keyword in node.keywords:
                if keyword.arg == "caller_skill":
                    caller_expr = keyword.value
                    break

            lineno = getattr(node, "lineno", 0)
            if caller_expr is None:
                errors.append(f"{rel}:{lineno}: dispatch() call must include caller_skill")
                continue

            resolved = _resolve_string(caller_expr, constants)
            if resolved is None:
                errors.append(
                    f"{rel}:{lineno}: caller_skill must be a string literal or module-level string constant "
                    f"resolving to `{expected_module_id}`"
                )
                continue

            if resolved != expected_module_id:
                errors.append(
                    f"{rel}:{lineno}: caller_skill resolves to `{resolved}`, "
                    f"expected `{expected_module_id}`"
                )

    return errors


def validate_with_graph(
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
) -> list[str]:
    """Validate dispatch ownership with a caller-supplied blueprint graph.

    Intent
    ------
    Preserve the graph-aware compatibility entry point for direct validator callers.

    Rationale
    ---------
    Direct callers can supply graph ownership while this wrapper creates the
    per-invocation Python source cache expected by the shared implementation.

    Pseudocode
    ----------
    - set source_cache = PythonSourceCache(repo_root)
    - set findings = _validate(repo_root, graph, source_cache)
    - return findings

    Wraps
    -----
    - ._validate -> preprocess: create a repository-scoped source cache and retain the supplied graph; postprocess: return findings unchanged; fixed_arguments: none

    CallsFromRepo
    -------------
    .officina.common.python_source_cache.PythonSourceCache:
      why:
        computes: "Creates the compatibility call's repository-scoped source cache."

    """
    return _validate(repo_root, graph, PythonSourceCache(repo_root))


def validate(repo_root: Path) -> list[str]:
    """Validate dispatch ownership through the graph-free compatibility path.

    Intent
    ------
    Preserve standalone validation for callers that provide only a repository root.

    Rationale
    ---------
    The public compatibility API must retain fallback discovery while pytest may
    inject shared prepared state through a separate item function.

    Pseudocode
    ----------
    - set source_cache = PythonSourceCache(repo_root)
    - set findings = _validate(repo_root, none, source_cache)
    - return findings

    Wraps
    -----
    - ._validate -> preprocess: create a repository-scoped source cache; postprocess: return findings unchanged; fixed_arguments: graph=none

    CallsFromRepo
    -------------
    .officina.common.python_source_cache.PythonSourceCache:
      why:
        computes: "Creates the standalone call's repository-scoped source cache."

    """
    return _validate(repo_root, None, PythonSourceCache(repo_root))


def test_dispatch_caller_module(
    repo_root: Path,
    graph: RepositoryBlueprintGraph | None,
    python_source_cache: PythonSourceCache,
) -> list[str]:
    """Run dispatch ownership validation with pytest-shared prepared state.

    Intent
    ------
    Expose a pytest item that consumes the repository graph and Python source cache
    fixtures prepared for the validator phase.

    Rationale
    ---------
    Fixture injection avoids rebuilding shared repository data while delegating all
    validation decisions to the same implementation as compatibility callers.

    Pseudocode
    ----------
    - set findings = _validate(repo_root, graph, python_source_cache)
    - return findings

    Wraps
    -----
    - ._validate -> preprocess: pass through pytest-shared graph and source cache state; postprocess: return findings unchanged; fixed_arguments: none

    """
    return _validate(repo_root, graph, python_source_cache)


def main() -> int:
    """Run standalone dispatch caller validation and return a process status.

    Intent
    ------
    Provide a command-line entry point that prints every ownership finding.

    Rationale
    ---------
    The validator remains directly executable outside the consolidated pytest runner.

    Pseudocode
    ----------
    - set repo_root = repository containing this module
    - set errors = validate(repo_root)
    - if errors exist:
      - return 1
    - return failure when findings exist and success otherwise

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .validate:
      why:
        constructs: "Produces findings that local command-line logic renders and converts into exit status."
    """
    errors = validate(Path(__file__).resolve().parents[2])
    if errors:
        print("error: invalid dispatch caller_skill usage.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
