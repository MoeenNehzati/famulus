"""Require Python dispatch() calls to identify the owning skill correctly."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from officina.common.blueprint_graph import RepositoryBlueprintGraph
from officina.runtime.python_machine_interface import (
    analyze_dispatch_call_declarations,
)

REQUIRES_BLUEPRINT_GRAPH = True
BLUEPRINT_GRAPH_OPTIONAL = True


def _python_files(skill_dir: Path) -> list[Path]:
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
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in direct_aliases
    if isinstance(func, ast.Attribute) and func.attr == "dispatch" and isinstance(func.value, ast.Name):
        return func.value.id in module_aliases
    return False


def _resolve_string(expr: ast.AST, constants: dict[str, str]) -> str | None:
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
) -> list[str]:
    errors: list[str] = []
    candidates: list[tuple[Path, str]] = []
    if graph is not None and graph.schema_version == 5:
        for path, owner_id in sorted(graph.direct_file_owners.items()):
            if path.suffix != ".py" or not path.is_file():
                continue
            rel = path.relative_to(repo_root)
            if "tests" in rel.parts:
                continue
            candidates.append(
                (path, graph.source_modules.get(owner_id, owner_id))
            )
    else:
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
        rel = path.relative_to(repo_root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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
            if graph is not None and graph.schema_version == 5 and declaration.legacy_v4:
                errors.append(
                    f"{rel}:{declaration.lineno}: DispatchCall() must use "
                    "caller_module_id and target_module_id in v5 runtime code"
                )
                continue
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
    return _validate(repo_root, graph)


def validate(repo_root: Path) -> list[str]:
    return _validate(repo_root, None)


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[2])
    if errors:
        print("error: invalid dispatch caller_skill usage.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
