"""Require explicit justification for test skips.

Skips are a coverage boundary, not ordinary test flow.  Any skip in the repo's
test tree must carry a nearby ``famulus-skip`` comment with a category, reason,
and alternate coverage statement so CI cannot silently lose platform coverage.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.common.python_source_cache import PythonSourceCache

_CHECK_ROOTS = ["tests", "skills", "src/officina/wakeup/tests"]
_SKIP_PARTS = {"__pycache__", ".system"}
_ALLOWED_CATEGORIES = {
    "capability-unavailable",
    "empty-contract",
    "live-smoke-opt-in",
    "native-backend-unavailable",
    "platform-contract",
    "unsupported-platform",
}

# Conservative gate invariant: every skip form recognized by _is_skip_call or
# _skip_lines' raised-exception branch must contain at least one of these source
# tokens.  Extend this evidence whenever adding a recognized form.
_SKIP_TOKENS = ("skip", "SkipTest")


def _iter_python_test_files(repo_root: Path):
    """Yield eligible Python test paths with repository-relative identities.

    Intent
    ------
    Discover Python tests covered by skip-hygiene validation.

    Rationale
    ---------
    Repository and skill tests share one skip-justification policy.

    Pseudocode
    ----------
    - for root in configured roots:
      - for path in Python files under root:
        - if path is an eligible test:
          - return path and relative path

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
            if root_name == "skills" and "tests" not in rel_path.parts:
                continue
            yield path, rel_path


def _name(node: ast.AST) -> str:
    """Return the dotted name represented by an AST expression.

    Intent
    ------
    Normalize name and attribute nodes for skip-call matching.

    Rationale
    ---------
    Skip APIs may be referenced through dotted module or instance attributes.

    Pseudocode
    ----------
    - if node is a name:
      - return identifier
    - if node is an attribute:
      - set prefix = dotted name of attribute owner
      - return joined prefix and attribute
    - return empty string

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._name:
      why:
        constructs: "Builds the dotted prefix for an attribute expression."
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _is_skip_call(node: ast.Call) -> bool:
    """Return whether an AST call invokes a recognized skip API.

    Intent
    ------
    Identify pytest, unittest, and test-case skip calls.

    Rationale
    ---------
    Every supported form must receive the same justification check.

    Pseudocode
    ----------
    - set name = dotted call target name
    - return whether name is a recognized skip target

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._name:
      why:
        constructs: "Builds the normalized call target for membership checks."
    """
    name = _name(node.func)
    return name in {
        "pytest.skip",
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "unittest.skip",
        "unittest.skipIf",
    } or name.endswith(".skipTest")


def _skip_lines(tree: ast.AST) -> list[int]:
    """Return sorted unique line numbers containing test skips.

    Intent
    ------
    Locate recognized skip calls and explicit raised skip exceptions.

    Rationale
    ---------
    Marker lookup needs a stable source line for every coverage boundary.

    Pseudocode
    ----------
    - for node in syntax tree:
      - if node is a recognized skip call:
        - set lines = lines plus node line
      - else:
        - if node raises a recognized skip exception:
          - set lines = lines plus node line
    - return sorted unique lines

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_skip_call:
      why:
        computes: "Recognizes ordinary skip call forms."
    ._name:
      why:
        computes: "Normalizes raised exception targets."
    """
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_skip_call(node):
            lines.append(node.lineno)
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            if _name(node.exc.func) in {"unittest.SkipTest", "pytest.SkipTest"}:
                lines.append(node.lineno)
    return sorted(set(lines))


def _marker_for(lines: list[str], lineno: int) -> str | None:
    """Return the nearby skip marker associated with a source line.

    Intent
    ------
    Find a ``famulus-skip`` comment immediately preceding a skip.

    Rationale
    ---------
    Justification must remain visibly adjacent to the skipped coverage.

    Pseudocode
    ----------
    - for line in preceding marker window in reverse order:
      - if line contains a skip marker comment:
        - return marker payload
      - if line is substantive code:
        - return none
    - return none

    Wraps
    -----
    - none
    """
    start = max(0, lineno - 4)
    for raw in reversed(lines[start : lineno - 1]):
        stripped = raw.strip()
        if stripped.startswith("#") and "famulus-skip:" in stripped:
            return stripped.split("famulus-skip:", 1)[1].strip()
        if stripped and not stripped.startswith(("#", "@")):
            break
    return None


def _parse_marker(marker: str) -> dict[str, str]:
    """Parse semicolon-delimited skip-marker fields.

    Intent
    ------
    Convert marker assignments into normalized key-value pairs.

    Rationale
    ---------
    Structured fields make skip justification mechanically enforceable.

    Pseudocode
    ----------
    - for part in semicolon-delimited marker parts:
      - if part contains an assignment:
        - set fields = fields plus stripped key and value
    - return fields

    Wraps
    -----
    - none
    """
    fields: dict[str, str] = {}
    for part in marker.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def _validate_marker(marker: str) -> list[str]:
    """Return structural errors in one skip-marker payload.

    Intent
    ------
    Require complete fields and a recognized skip category.

    Rationale
    ---------
    Skip comments must state why coverage is absent and where it remains.

    Pseudocode
    ----------
    - set fields = parsed marker fields
    - if required fields are missing:
      - set findings = findings plus missing-field error
    - if category is unknown:
      - set findings = findings plus category error
    - return findings

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._parse_marker:
      why:
        constructs: "Builds normalized fields for policy checks."
    """
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


def _validate_file(
    path: Path,
    rel_path: Path,
    source_cache: PythonSourceCache,
) -> list[str]:
    """Validate skip justification in one Python test file.

    Intent
    ------
    Report missing or malformed markers for every detected skip.

    Rationale
    ---------
    File-level validation preserves exact source locations and parse failures.

    Pseudocode
    ----------
    - set parsed_source = cached source and syntax tree for path
    - if Python syntax is invalid:
      - return parse finding
    - for line_number in detected skip lines:
      - set marker = nearby marker payload
      - if marker is absent:
        - set findings = findings plus missing-marker error
      - else:
        - set findings = findings plus marker validation errors
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._skip_lines:
      why:
        computes: "Locates skip boundaries in the parsed syntax tree."
    ._validate_marker:
      why:
        computes: "Returns structural errors for a discovered marker."

    InstantiationsFromRepo
    ----------------------
    ._marker_for:
      why:
        constructs: "Builds the justification payload adjacent to each skip."
    """
    display_path = rel_path.as_posix()
    try:
        source, tree = source_cache.read_parse(path)
    except SyntaxError as exc:
        return [f"{display_path}:{exc.lineno}: failed to parse Python: {exc.msg}"]
    if not any(token in source for token in _SKIP_TOKENS):
        return []
    lines = source.splitlines()

    errors: list[str] = []
    for lineno in _skip_lines(tree):
        marker = _marker_for(lines, lineno)
        if marker is None:
            errors.append(
                f"{display_path}:{lineno}: test skip must have a nearby "
                "`# famulus-skip: category=...; reason=...; alternate=...` comment"
            )
            continue
        for marker_error in _validate_marker(marker):
            errors.append(
                f"{display_path}:{lineno}: invalid famulus-skip marker: {marker_error}"
            )
    return errors


def _validate(repo_root: Path, source_cache: PythonSourceCache) -> list[str]:
    """Validate skip hygiene with a prepared Python source cache.

    Intent
    ------
    Aggregate skip-marker findings across repository tests.

    Rationale
    ---------
    Accepting shared preparation avoids reparsing files across validators.

    Pseudocode
    ----------
    - for test_file in eligible Python tests:
      - set findings = findings plus file findings
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_python_test_files:
      why:
        computes: "Provides eligible tests and relative identities."

    InstantiationsFromRepo
    ----------------------
    ._validate_file:
      why:
        constructs: "Builds skip-hygiene findings for each file."
    """
    errors: list[str] = []
    for path, rel_path in _iter_python_test_files(repo_root):
        errors.extend(_validate_file(path, rel_path, source_cache))
    return errors


def validate(repo_root: Path) -> list[str]:
    """Validate skip hygiene through the standalone API.

    Intent
    ------
    Preserve direct one-argument validator use.

    Rationale
    ---------
    Standalone callers lack pytest's shared Python source cache.

    Pseudocode
    ----------
    - set source_cache = repository Python source cache
    - return skip-hygiene findings with standalone preparation

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


def test_skip_hygiene(
    repo_root: Path,
    python_source_cache: PythonSourceCache,
) -> list[str]:
    """Run skip-hygiene validation as a pytest item.

    Intent
    ------
    Connect validation to pytest's shared repository fixtures.

    Rationale
    ---------
    Suite execution should reuse the prepared Python source cache.

    Pseudocode
    ----------
    - return skip-hygiene findings from shared fixtures

    Wraps
    -----
    - ._validate -> preprocess: forwards shared fixtures; postprocess: returns findings unchanged; fixed_arguments: none
    """
    return _validate(repo_root, python_source_cache)


def main() -> int:
    """Run standalone skip-hygiene validation and print findings.

    Intent
    ------
    Provide the validator's command-line compatibility entry point.

    Rationale
    ---------
    Direct execution requires actionable output and a conventional exit status.

    Pseudocode
    ----------
    - set findings = repository skip-hygiene validation
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
        print("Skip hygiene violations found:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
