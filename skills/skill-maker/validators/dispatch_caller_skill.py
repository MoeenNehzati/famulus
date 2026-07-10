"""Require Python dispatch() calls to identify the owning skill correctly."""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def _python_files(skill_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for subdir in ("_rtx", "bin"):
        root = skill_dir / subdir
        if not root.is_dir():
            continue
        paths.extend(path for path in root.rglob("*.py") if path.is_file())
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


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors

    for blueprint_path in sorted(skills_root.glob("*/blueprint.yaml")):
        skill_name = blueprint_path.parent.name
        skill_dir = blueprint_path.parent
        for path in _python_files(skill_dir):
            rel = path.relative_to(repo_root)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue

            constants = _module_string_constants(tree)
            direct_aliases, module_aliases, legacy_famulus_lines = _dispatch_aliases(tree)
            for lineno in legacy_famulus_lines:
                errors.append(
                    f"{rel}:{lineno}: import officina.dispatcher instead of removed famulus.dispatcher"
                )
            if not direct_aliases and not module_aliases:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not _is_dispatch_call(node, direct_aliases, module_aliases):
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
                        f"resolving to `{skill_name}`"
                    )
                    continue

                if resolved != skill_name:
                    errors.append(
                        f"{rel}:{lineno}: caller_skill resolves to `{resolved}`, expected `{skill_name}`"
                    )

    return errors


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[3])
    if errors:
        print("error: invalid dispatch caller_skill usage.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
