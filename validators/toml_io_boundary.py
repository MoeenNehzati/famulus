"""Validate that production TOML filenames cross only the TOML IO boundary."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path


_CHECK_ROOTS = ["skills", "src", "script_dispatcher", "llmhooks"]
_SKIP_PARTS = {"tests", "validators", "__pycache__", ".git", ".claude-plugin", ".codex-plugin", "logs"}
_ALLOWED_REL = Path("src/officina/common/toml_io.py")


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
    return {repo_root / path for path in result.stdout.splitlines()}


def _iter_python_files(repo_root: Path):
    tracked = _tracked_files(repo_root)
    for root_name in _CHECK_ROOTS:
        root = repo_root / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if tracked is not None and path not in tracked:
                continue
            rel_path = path.relative_to(repo_root)
            if rel_path == _ALLOWED_REL:
                continue
            if any(part in _SKIP_PARTS for part in rel_path.parts):
                continue
            yield path


def _contains_toml_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return ".toml" in node.value
    if isinstance(node, ast.JoinedStr):
        return any(
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and ".toml" in value.value
            for value in node.values
        )
    return False


def _is_docstring(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    expr = parents.get(node)
    if isinstance(expr, ast.JoinedStr):
        expr = parents.get(expr)
    if not isinstance(expr, ast.Expr):
        return False
    owner = parents.get(expr)
    return isinstance(owner, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and owner.body and owner.body[0] is expr


def _is_toml_io_open_call(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "open"
        and isinstance(func.value, ast.Name)
        and func.value.id == "toml_io"
    )


def _is_direct_open_filename_arg(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    direct = parents[node] if isinstance(parents.get(node), ast.JoinedStr) else node
    parent = parents.get(direct)
    if not isinstance(parent, ast.Call) or not _is_toml_io_open_call(parent):
        return False
    if len(parent.args) >= 2 and parent.args[1] is direct:
        return True
    return any(
        keyword.arg in {"name", "filename"} and keyword.value is direct
        for keyword in parent.keywords
    )


def _open_filename_arg(call: ast.Call) -> ast.AST | None:
    if len(call.args) >= 2:
        return call.args[1]
    for keyword in call.keywords:
        if keyword.arg in {"name", "filename"}:
            return keyword.value
    return None


def _is_visible_toml_filename(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.endswith(".toml")
    if isinstance(node, ast.JoinedStr):
        return any(
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and ".toml" in value.value
            for value in node.values
        )
    return False


def _validate_file(path: Path, rel_path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{rel_path}:{exc.lineno}: failed to parse Python: {exc.msg}"]

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_toml_io_open_call(node):
            filename_arg = _open_filename_arg(node)
            if filename_arg is None:
                errors.append(
                    f"{rel_path}:{node.lineno}: toml_io.open(...) requires a visible "
                    "literal or f-string TOML filename argument"
                )
            elif not _is_visible_toml_filename(filename_arg):
                errors.append(
                    f"{rel_path}:{getattr(filename_arg, 'lineno', node.lineno)}: "
                    "toml_io.open(...) filename must be a literal or f-string ending in .toml"
                )
        if isinstance(node, ast.Constant) and isinstance(parents.get(node), ast.JoinedStr):
            continue
        if not _contains_toml_literal(node):
            continue
        if _is_docstring(node, parents):
            continue
        if _is_direct_open_filename_arg(node, parents):
            continue
        errors.append(
            f"{rel_path}:{getattr(node, 'lineno', 1)}: TOML filenames may only appear "
            "as the direct filename argument to toml_io.open(...)"
        )
    return errors


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for path in _iter_python_files(repo_root):
        errors.extend(_validate_file(path, path.relative_to(repo_root)))
    return errors
