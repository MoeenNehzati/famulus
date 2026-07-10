"""Validate portable ``strftime`` use in shared runtime files.

Python's ``strftime`` delegates platform-specific format modifiers to the
host C library. GNU/POSIX-style no-padding modifiers such as ``%-d`` fail on
Windows, while Windows-style ``%#d`` is not portable in the other direction.

This validator intentionally checks only literal ``strftime`` format strings
for those non-portable padding modifiers. Shared project-owned date/time IO
formats should live in ``officina.common.dates``; that convention is documented
in ``references/skill-guidelines.md`` rather than enforced here.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

_CHECK_ROOTS = ["skills", "src", "script_dispatcher"]
_SKIP_PARTS = {"tests", "validators", "__pycache__", ".git", ".claude-plugin", ".codex-plugin", "logs"}
_NON_PORTABLE_STRFTIME = re.compile(r"(?<!%)%[-_#0][A-Za-z]")


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
    return {repo_root / path for path in result.stdout.splitlines()}


def _iter_files(repo_root: Path):
    tracked = _tracked_files(repo_root)
    for root_name in _CHECK_ROOTS:
        root = repo_root / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if tracked is not None and path not in tracked:
                continue
            rel_path = path.relative_to(repo_root)
            if any(part in _SKIP_PARTS for part in rel_path.parts):
                continue
            yield path


def _literal_strftime_format(node: ast.Call) -> str | None:
    func = node.func
    is_strftime = (
        isinstance(func, ast.Attribute)
        and func.attr == "strftime"
    ) or (
        isinstance(func, ast.Name)
        and func.id == "strftime"
    )
    if not is_strftime or not node.args:
        return None
    first_arg = node.args[0]
    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
        return first_arg.value
    return None


def _validate_python(path: Path, rel_path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{rel_path}:{exc.lineno}: failed to parse Python: {exc.msg}"]

    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fmt = _literal_strftime_format(node)
        if fmt is None:
            continue
        for match in _NON_PORTABLE_STRFTIME.finditer(fmt):
            errors.append(
                f"{rel_path}:{node.lineno}: non-portable strftime directive `{match.group(0)}`; "
                "use explicit Python date formatting"
            )
    return errors


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for path in _iter_files(repo_root):
        rel_path = path.relative_to(repo_root)
        if path.suffix == ".py":
            errors.extend(_validate_python(path, rel_path))
    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Portable date violations found:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
