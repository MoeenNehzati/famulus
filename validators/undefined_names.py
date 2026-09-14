"""Validate that shared runtime files reference no undefined names.

An undefined name is a runtime ``NameError`` waiting on a branch. When the
branch is one only production takes, no test observes it: ``_run_record.py``
called ``sys.platform`` without importing ``sys`` on a path reachable only for
the email-triage job with ``EMAIL_TRIAGE_STATE_DIR`` unset -- exactly the
scheduler's configuration -- and every scheduled run died after completing its
work for four days before anyone noticed.

Detection is delegated to pyflakes rather than reimplemented. Correct
undefined-name analysis requires full scope handling (comprehensions, class
bodies, ``global``/``nonlocal``, star imports, ``__all__``), and a hand-rolled
AST walk that gets any of that wrong produces false positives, which is how a
validator earns a blanket suppression and then deletion.

Only undefined-name findings are reported. Unused imports and similar style
findings are deliberately out of scope: this validator exists to catch code
that cannot run, not code that is untidy.
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

from pyflakes import checker as pyflakes_checker
from pyflakes import messages as pyflakes_messages

_CHECK_ROOTS = ["skills", "src", "validators"]
_SKIP_PARTS = {"__pycache__", ".git", ".claude-plugin", ".codex-plugin", "logs", "_build"}

# UndefinedLocal is "local variable referenced before assignment"; UndefinedName
# is the plain unresolved reference that bit us. UndefinedExport covers a name
# in __all__ that does not exist. All three are runtime failures, not style.
_UNDEFINED_MESSAGES = (
    pyflakes_messages.UndefinedName,
    pyflakes_messages.UndefinedLocal,
    pyflakes_messages.UndefinedExport,
)


def _iter_files(repo_root: Path, validation_paths: tuple[str, ...] | None = None):
    """Yield repository Python files eligible for undefined-name validation.

    Intent
    ------
    Traverse the configured source roots while excluding tooling trees.

    Rationale
    ---------
    Test trees are included deliberately: a test referencing an undefined name
    fails loudly on its own, but a fixture or helper that does so can be
    skipped silently, and the cost of checking them is negligible.

    Pseudocode
    ----------
    - for root in configured roots:
      - for path in root traversal order:
        - if path is an eligible Python file:
          - return path

    Wraps
    -----
    - none
    """
    for root_name in _CHECK_ROOTS:
        root = repo_root / root_name
        if validation_paths is None and not root.exists():
            continue
        candidates = (
            sorted(root.rglob("*.py")) if validation_paths is None else
            (repo_root / rel for rel in validation_paths
             if Path(rel).is_relative_to(root_name) and Path(rel).match("*.py"))
        )
        for path in candidates:
            if validation_paths is None and not path.is_file():
                continue
            rel_path = path.relative_to(repo_root)
            if any(part in _SKIP_PARTS for part in rel_path.parts):
                continue
            yield path


def _validate_python(
    path: Path,
    rel_path: Path,
    source_cache: PythonSourceCache,
    *,
    strict: bool = False,
) -> list[str]:
    """Return undefined-name findings for one Python file.

    Intent
    ------
    Run pyflakes' scope analysis over a single module and keep only the
    undefined-name findings.

    Rationale
    ---------
    Full-repository syntax checks own parse failures in unscoped runs.
    Scoped runs must propagate read and parse failures instead of passing silently.

    Pseudocode
    ----------
    - set parsed_source = cached source and syntax tree for path
    - if reading or parsing fails:
      - raise when strict, otherwise return no findings
    - set messages = pyflakes scope findings for the syntax tree
    - for message in messages:
      - if message is an undefined-name finding:
        - set findings = findings plus formatted location finding
    - return findings

    Wraps
    -----
    - none
    """
    try:
        _source, tree = source_cache.read_parse(path)
    except (SyntaxError, OSError, UnicodeDecodeError):
        if strict:
            raise
        return []
    checker = pyflakes_checker.Checker(tree, filename=str(path))
    findings = []
    for message in checker.messages:
        if isinstance(message, _UNDEFINED_MESSAGES):
            detail = message.message % message.message_args
            findings.append(f"{rel_path}:{message.lineno}: {detail}")
    return findings


def validate(repo_root: Path, validation_paths: tuple[str, ...] | None = None) -> list[str]:
    """Return undefined-name findings across the repository.

    Intent
    ------
    Provide the repository-check boundary with actionable findings.

    Rationale
    ---------
    The repository checks collect validators through this signature.

    Pseudocode
    ----------
    - for path in eligible repository files:
      - set rel_path = path relative to repository root
      - set findings = findings plus undefined-name findings for path
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_files:
      why:
        computes: "Provides the eligible repository Python files."

    InstantiationsFromRepo
    ----------------------
    ._validate_python:
      why:
        constructs: "Builds the per-file findings aggregated here."
    .officina.common.python_source_cache.PythonSourceCache:
      why:
        constructs: "Shares parsed sources across the repository check suite."
    """
    source_cache = PythonSourceCache(repo_root)
    findings: list[str] = []
    for path in _iter_files(repo_root, validation_paths):
        rel_path = path.relative_to(repo_root)
        findings.extend(_validate_python(
            path, rel_path, source_cache, strict=validation_paths is not None,
        ))
    return findings


def test_undefined_names(
    repo_root: Path,
    validation_paths: tuple[str, ...] | None,
) -> list[str]:
    """Validate selected whole files with a private Pyflakes source cache.

    Intent
    ------
    Restrict findings to the selected files while retaining the repository root.

    Rationale
    ---------
    Pyflakes mutates its tree, so it must not receive the shared suite AST cache.

    Pseudocode
    ----------
    - return undefined-name findings for the selected whole files

    Wraps
    -----
    - .validate -> preprocess: forwards repository and selected paths; postprocess: returns findings unchanged; fixed_arguments: none
    """
    return validate(repo_root, validation_paths)


def main() -> int:
    """Run standalone validation and return a process exit status.

    Intent
    ------
    Provide a command-line entry point for undefined-name validation.

    Rationale
    ---------
    Direct execution must print actionable findings and signal failure.

    Pseudocode
    ----------
    - set findings = repository undefined-name validation
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
        print("Undefined name violations found:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
