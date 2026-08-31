"""Validate portable ``strftime`` use in shared runtime files.

Python's ``strftime`` delegates platform-specific format modifiers to the
host C library. GNU/POSIX-style no-padding modifiers such as ``%-d`` fail on
Windows, while Windows-style ``%#d`` is not portable in the other direction.

This validator intentionally checks only literal ``strftime`` format strings
for those non-portable padding modifiers. Shared project-owned date/time IO
formats should live in ``officina.common.dates``; that convention belongs to the
applicable Python node-standard closure rather than being enforced here.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.common.python_source_cache import PythonSourceCache

_CHECK_ROOTS = ["skills", "src"]
_SKIP_PARTS = {"tests", "validators", "__pycache__", ".git", ".claude-plugin", ".codex-plugin", "logs"}
_NON_PORTABLE_STRFTIME = re.compile(r"(?<!%)%[-_#0][A-Za-z]")


def _iter_files(repo_root: Path):
    """Yield repository files eligible for portable-date validation.

    Intent
    ------
    Traverse the configured source roots while excluding test and tooling trees.

    Rationale
    ---------
    The validator should inspect shared runtime content only.

    Pseudocode
    ----------
    - for root in configured roots:
      - for path in root traversal order:
        - if path is eligible:
          - return path

    Wraps
    -----
    - none
    """
    for root_name in _CHECK_ROOTS:
        root = repo_root / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel_path = path.relative_to(repo_root)
            if any(part in _SKIP_PARTS for part in rel_path.parts):
                continue
            yield path


def _literal_strftime_format(node: ast.Call) -> str | None:
    """Return the literal format from a supported ``strftime`` call.

    Intent
    ------
    Recognize direct and attribute ``strftime`` calls with literal formats.

    Rationale
    ---------
    Static validation can safely inspect only format strings present in the source.

    Pseudocode
    ----------
    - set is_strftime = whether call target is named ``strftime``
    - if call is unsupported or has no arguments:
      - return none
    - if first argument is a string literal:
      - return literal value
    - return none

    Wraps
    -----
    - none
    """
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


def _validate_python(
    path: Path,
    rel_path: Path,
    source_cache: PythonSourceCache,
) -> list[str]:
    """Validate literal ``strftime`` directives in one Python file.

    Intent
    ------
    Report non-portable padding modifiers with their source locations.

    Rationale
    ---------
    Python date formatting should behave consistently across supported platforms.

    Pseudocode
    ----------
    - set parsed_source = cached source and syntax tree for path
    - if Python syntax is invalid:
      - return parse finding
    - for call in syntax tree:
      - set format = literal ``strftime`` format
      - for directive in non_portable_directives:
        - set findings = findings plus formatted directive finding
    - return findings

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._literal_strftime_format:
      why:
        constructs: "Extracts a statically checkable format from each call."
    """
    try:
        _source, tree = source_cache.read_parse(path)
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


def _validate(repo_root: Path, source_cache: PythonSourceCache) -> list[str]:
    """Validate portable date formatting with a prepared source cache.

    Intent
    ------
    Apply Python date checks across all eligible repository files.

    Rationale
    ---------
    Accepting the cache lets a repository check suite reuse parsed Python sources.

    Pseudocode
    ----------
    - for path in eligible repository files:
      - set rel_path = path relative to repository root
      - if path is Python:
        - set findings = findings plus Python date findings
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_files:
      why:
        computes: "Provides the eligible shared runtime files."

    InstantiationsFromRepo
    ----------------------
    ._validate_python:
      why:
        constructs: "Builds portable-date findings for each Python file."
    """
    errors: list[str] = []
    for path in _iter_files(repo_root):
        rel_path = path.relative_to(repo_root)
        if path.suffix == ".py":
            errors.extend(_validate_python(path, rel_path, source_cache))
    return errors


def validate(repo_root: Path) -> list[str]:
    """Validate portable date formatting from the standalone API.

    Intent
    ------
    Preserve the validator's public one-argument entry point.

    Rationale
    ---------
    Standalone callers need validation without preparing shared suite state.

    Pseudocode
    ----------
    - set source_cache = repository Python source cache
    - return portable-date findings with standalone preparation

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
        constructs: "Builds findings with the standalone source cache."
    """
    return _validate(repo_root, PythonSourceCache(repo_root))


def test_portable_dates(
    repo_root: Path,
    python_source_cache: PythonSourceCache,
) -> list[str]:
    """Run portable-date validation as a repository pytest item.

    Intent
    ------
    Connect the validator to pytest's shared repository fixtures.

    Rationale
    ---------
    Suite execution should reuse the prepared Python source cache.

    Pseudocode
    ----------
    - return portable-date findings from shared fixtures

    Wraps
    -----
    - ._validate -> preprocess: forwards shared fixtures; postprocess: returns findings unchanged; fixed_arguments: none
    """
    return _validate(repo_root, python_source_cache)


def main() -> int:
    """Run standalone validation and return a process exit status.

    Intent
    ------
    Provide a command-line entry point for portable-date validation.

    Rationale
    ---------
    Direct execution must print actionable findings and signal failure.

    Pseudocode
    ----------
    - set findings = repository portable-date validation
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
        constructs: "Builds the findings printed by the command-line boundary."
    """
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
