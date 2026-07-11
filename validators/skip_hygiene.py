"""Require explicit justification for test skips.

Skips are a coverage boundary, not ordinary test flow.  Any skip in the repo's
test tree must carry a nearby ``famulus-skip`` comment with a category, reason,
and alternate coverage statement so CI cannot silently lose platform coverage.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_CHECK_ROOTS = ["tests", "skills"]
_SKIP_PARTS = {"__pycache__", ".system"}
_ALLOWED_CATEGORIES = {
    "capability-unavailable",
    "empty-contract",
    "live-smoke-opt-in",
    "native-backend-unavailable",
    "platform-contract",
    "unsupported-platform",
}


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


def _iter_python_test_files(repo_root: Path):
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
            if root_name == "skills" and "tests" not in rel_path.parts:
                continue
            yield path, rel_path


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _is_skip_call(node: ast.Call) -> bool:
    name = _name(node.func)
    return name in {
        "pytest.skip",
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "unittest.skip",
        "unittest.skipIf",
    } or name.endswith(".skipTest")


def _skip_lines(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_skip_call(node):
            lines.append(node.lineno)
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            if _name(node.exc.func) in {"unittest.SkipTest", "pytest.SkipTest"}:
                lines.append(node.lineno)
    return sorted(set(lines))


def _marker_for(lines: list[str], lineno: int) -> str | None:
    start = max(0, lineno - 4)
    for raw in reversed(lines[start : lineno - 1]):
        stripped = raw.strip()
        if stripped.startswith("#") and "famulus-skip:" in stripped:
            return stripped.split("famulus-skip:", 1)[1].strip()
        if stripped and not stripped.startswith(("#", "@")):
            break
    return None


def _parse_marker(marker: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in marker.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def _validate_marker(marker: str) -> list[str]:
    fields = _parse_marker(marker)
    errors: list[str] = []
    missing = [name for name in ("category", "reason", "alternate") if not fields.get(name)]
    if missing:
        errors.append(f"missing field(s): {', '.join(missing)}")
    category = fields.get("category")
    if category and category not in _ALLOWED_CATEGORIES:
        allowed = ", ".join(sorted(_ALLOWED_CATEGORIES))
        errors.append(f"unknown category `{category}`; allowed: {allowed}")
    return errors


def _validate_file(path: Path, rel_path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{rel_path}:{exc.lineno}: failed to parse Python: {exc.msg}"]

    errors: list[str] = []
    for lineno in _skip_lines(tree):
        marker = _marker_for(lines, lineno)
        if marker is None:
            errors.append(
                f"{rel_path}:{lineno}: test skip must have a nearby "
                "`# famulus-skip: category=...; reason=...; alternate=...` comment"
            )
            continue
        for marker_error in _validate_marker(marker):
            errors.append(f"{rel_path}:{lineno}: invalid famulus-skip marker: {marker_error}")
    return errors


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for path, rel_path in _iter_python_test_files(repo_root):
        errors.extend(_validate_file(path, rel_path))
    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Skip hygiene violations found:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
