"""Require explicit encoding on subprocess text boundaries.

``subprocess`` text mode defaults to the host locale encoding when ``encoding``
is omitted. That is not a stable contract for repo runtime code: Windows code
pages, redirected stdio, and UTF-8 Unix locales can all disagree. Runtime code
that asks ``subprocess`` for text must spell out both the encoding and error
policy locally at the call site.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_CHECK_ROOTS = ["skills", "src", "validators", "llmhooks", "hooks"]
_SKIP_PARTS = {
    "tests",
    ".system",
    "__pycache__",
    ".git",
    ".claude-plugin",
    ".codex-plugin",
    "logs",
}
_SUBPROCESS_ATTRS = {"run", "Popen", "call", "check_call", "check_output"}


def _tracked_files(repo_root: Path) -> set[Path] | None:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return {
        repo_root / rel
        for rel in result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
        if rel
    }


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
            if any(part in _SKIP_PARTS for part in rel_path.parts):
                continue
            yield path, rel_path


def _is_subprocess_call(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
        and func.attr in _SUBPROCESS_ATTRS
    )


def _is_true_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _keyword_map(node: ast.Call) -> dict[str, ast.AST]:
    return {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}


def _uses_text_mode(keywords: dict[str, ast.AST]) -> bool:
    return (
        _is_true_constant(keywords.get("text", ast.Constant(False)))
        or _is_true_constant(keywords.get("universal_newlines", ast.Constant(False)))
        or "encoding" in keywords
        or "errors" in keywords
    )


def _validate_python(path: Path, rel_path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{rel_path}:{exc.lineno}: failed to parse Python: {exc.msg}"]

    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
            continue

        keywords = _keyword_map(node)
        if not _uses_text_mode(keywords):
            continue
        if "encoding" not in keywords or "errors" not in keywords:
            errors.append(
                f"{rel_path}:{node.lineno}: subprocess text mode must set "
                "both encoding and errors explicitly"
            )
    return errors


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for path, rel_path in _iter_python_files(repo_root):
        errors.extend(_validate_python(path, rel_path))
    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Subprocess text encoding violations found:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
