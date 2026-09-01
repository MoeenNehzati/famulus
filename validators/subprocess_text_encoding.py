"""Require explicit encoding on subprocess text boundaries.

``subprocess`` text mode defaults to the host locale encoding when ``encoding``
is omitted. That is not a stable contract for repo runtime code: Windows code
pages, redirected stdio, and UTF-8 Unix locales can all disagree. Runtime code
that asks ``subprocess`` for text must spell out both the encoding and error
policy locally at the call site.
"""
from __future__ import annotations

import ast
import sys
import unicodedata
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.common.python_source_cache import PythonSourceCache

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


def _iter_python_files(repo_root: Path):
    """Yield governed Python files with repository-relative paths.

    Intent
    ------
    Discover each Python source subject to the encoding policy.

    Rationale
    ---------
    Centralized traversal gives every downstream check the same exclusions.

    Pseudocode
    ----------
    - for root in configured roots:
      - if root exists:
        - for source in Python files:
          - if source is governed:
            - return source and repository-relative path

    Wraps
    -----
    - none
    """
    for root_name in _CHECK_ROOTS:
        root = repo_root / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            rel_path = path.relative_to(repo_root)
            if any(part in _SKIP_PARTS for part in rel_path.parts):
                continue
            yield path, rel_path


def _is_subprocess_call(node: ast.Call) -> bool:
    """Return whether a call directly targets a governed subprocess API.

    Intent
    ------
    Recognize the subprocess calls whose text boundaries this validator owns.

    Rationale
    ---------
    Restricting the AST shape avoids treating unrelated methods as subprocess.

    Pseudocode
    ----------
    - set function = call target expression
    - return whether function is a governed attribute on ``subprocess``

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


def _is_true_constant(node: ast.AST) -> bool:
    """Return whether an AST node is the literal Boolean ``True``.

    Intent
    ------
    Recognize statically explicit opt-in to subprocess text mode.

    Rationale
    ---------
    Dynamic expressions cannot prove that text mode is enabled.

    Pseudocode
    ----------
    - return whether the node is a constant whose value is exactly true

    Wraps
    -----
    - none
    """
    return isinstance(node, ast.Constant) and node.value is True


def _keyword_map(node: ast.Call) -> dict[str, ast.AST]:
    """Map statically named call keywords to their AST values.

    Intent
    ------
    Expose subprocess keyword arguments for policy checks.

    Rationale
    ---------
    Expanded keyword mappings lack names that can be verified statically.

    Pseudocode
    ----------
    - set keyword_map = each explicitly named keyword value
    - return the name-to-value mapping

    Wraps
    -----
    - none
    """
    return {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}


def _uses_text_mode(keywords: dict[str, ast.AST]) -> bool:
    """Return whether subprocess keywords establish text handling.

    Intent
    ------
    Identify calls where the explicit encoding policy applies.

    Rationale
    ---------
    Either a true text flag or an encoding-policy keyword creates text mode.

    Pseudocode
    ----------
    - set text_flags = literal-true status for both text flags
    - set policy_keywords = presence of encoding or errors
    - return whether text flags or policy keywords establish text mode

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_true_constant:
      why:
        computes: "Distinguishes explicit true flags from dynamic expressions."
    """
    return (
        _is_true_constant(keywords.get("text", ast.Constant(False)))
        or _is_true_constant(keywords.get("universal_newlines", ast.Constant(False)))
        or "encoding" in keywords
        or "errors" in keywords
    )


def _validate_python(
    path: Path,
    rel_path: Path,
    source_cache: PythonSourceCache,
) -> list[str]:
    """Validate subprocess text boundaries in one Python source file.

    Intent
    ------
    Report parse failures and incomplete text-encoding policies for one file.

    Rationale
    ---------
    File-local validation preserves precise paths and source line diagnostics.

    Pseudocode
    ----------
    - set parsed_source = cached source parse
    - if parsing fails:
      - return parse diagnostic
    - for call in syntax tree:
      - if call is a governed text-mode subprocess call missing policy:
        - set errors = errors plus encoding diagnostic
    - return errors

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_subprocess_call:
      why:
        computes: "Limits validation to governed subprocess APIs."
    ._uses_text_mode:
      why:
        computes: "Determines whether the call creates a text boundary."

    InstantiationsFromRepo
    ----------------------
    ._keyword_map:
      why:
        constructs: "Builds the keyword lookup used by the policy checks."
    """
    display_path = rel_path.as_posix()
    try:
        source, tree = source_cache.read_parse(path)
    except SyntaxError as exc:
        return [f"{display_path}:{exc.lineno}: failed to parse Python: {exc.msg}"]

    if "subprocess" not in unicodedata.normalize("NFKC", source):
        return []

    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
            continue

        keywords = _keyword_map(node)
        if not _uses_text_mode(keywords):
            continue
        if "encoding" not in keywords or "errors" not in keywords:
            errors.append(
                f"{display_path}:{node.lineno}: subprocess text mode must set "
                "both encoding and errors explicitly"
            )
    return errors


def _validate(repo_root: Path, source_cache: PythonSourceCache) -> list[str]:
    """Validate every governed Python file with one shared source cache.

    Intent
    ------
    Aggregate repository-wide subprocess text-encoding diagnostics.

    Rationale
    ---------
    Reusing the injected cache avoids reparsing files across AST validators.

    Pseudocode
    ----------
    - set errors = empty diagnostics
    - for source in governed Python files:
      - set errors = errors plus source diagnostics from shared cache
    - return the accumulated diagnostics

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_python_files:
      why:
        computes: "Supplies the governed file set and diagnostic paths."

    InstantiationsFromRepo
    ----------------------
    ._validate_python:
      why:
        constructs: "Produces the diagnostics accumulated for each file."
    """
    errors: list[str] = []
    for path, rel_path in _iter_python_files(repo_root):
        errors.extend(_validate_python(path, rel_path, source_cache))
    return errors


def validate(repo_root: Path) -> list[str]:
    """Run subprocess text-encoding validation with a private source cache.

    Intent
    ------
    Preserve the public standalone validator interface.

    Rationale
    ---------
    Non-pytest callers need the same validation without fixture injection.

    Pseudocode
    ----------
    - set source_cache = repository Python source cache
    - return repository findings from the shared implementation

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.python_source_cache.PythonSourceCache:
      why:
        computes: "Provides standalone source reads and AST parsing."

    InstantiationsFromRepo
    ----------------------
    ._validate:
      why:
        constructs: "Produces repository-wide encoding diagnostics."
    """
    return _validate(repo_root, PythonSourceCache(repo_root))


def test_subprocess_text_encoding(
    repo_root: Path,
    python_source_cache: PythonSourceCache,
) -> list[str]:
    """Expose subprocess text-encoding validation as a pytest item.

    Intent
    ------
    Reuse pytest's repository-wide parsed-source cache.

    Rationale
    ---------
    Fixture injection prevents repeated parsing without changing findings.

    Pseudocode
    ----------
    - return repository findings from the shared implementation and cache

    Wraps
    -----
    - ._validate -> preprocess: forwards fixture values; postprocess: returns findings unchanged; fixed_arguments: none

    """
    return _validate(repo_root, python_source_cache)


def main() -> int:
    """Run validation from the command line and print any violations.

    Intent
    ------
    Provide a process exit contract for direct validator execution.

    Rationale
    ---------
    Repository tooling needs a conventional zero-or-one command result.

    Pseudocode
    ----------
    - set findings = repository subprocess text-encoding validation
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
        constructs: "Produces the findings rendered by the CLI."
    """
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
