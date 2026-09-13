from __future__ import annotations

import ast
from pathlib import Path

from officina.common import command_files


def test_command_file_primitive_has_no_managed_runtime_or_host_policy() -> None:
    """Resolver or host policy here would make neutral command installation host-owned."""
    tree = ast.parse(Path(command_files.__file__).read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imports.add(node.module)
            imports.update(alias.name for alias in node.names)

    forbidden_modules = {"current", "runtime_resolver", "launch"}
    assert not {
        part
        for item in imports
        for part in item.split(".")
        if part in forbidden_modules
    }

    def string_literal(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = string_literal(node.left)
            right = string_literal(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    literals = {value for node in ast.walk(tree) if (value := string_literal(node)) is not None}
    assert not any(
        marker in literal
        for marker in ("current.json", "runtime_resolver", "launch.py", "python3", "Windows", "win32")
        for literal in literals
    )
    assert "py" not in literals
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "which"
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        and node.attr == "platform"
        for node in ast.walk(tree)
    )
