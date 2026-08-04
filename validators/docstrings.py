"""Enforce canonical Officina docstrings on eligible staged Python modules."""
from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath
from typing import Sequence

from officina.validators.docstring_validator import (
    DocstringValidationIssue,
    validate_module_docstrings,
)


def _eligible_python_path(repo_root: Path, relative_path: str) -> Path | None:
    """Resolve one safe staged Python path beneath the validator mirror.

    Intent
    ------
    Restrict canonical docstring checks to regular Python files represented by
    safe repository-relative paths.

    Rationale
    ---------
    The runner validates staged-path transport before dispatch, but this adapter
    retains its own narrow filesystem boundary so direct callers cannot make it
    read an absolute path, parent traversal, or symlink. A safe Python path that
    is unexpectedly missing or unreadable remains a fail-closed target error.

    Pseudocode
    ----------
    - set logical_path = relative_path parsed as POSIX path parts
    - if logical_path is unsafe or does not end in .py:
      - return none
    - set candidate = repo_root plus logical_path
    - if candidate is a symlink or escapes repo_root:
      - return none
    - if candidate is missing or unreadable:
      - raise filesystem access error
    - return candidate

    Wraps
    -----
    - none
    """

    logical = PurePosixPath(relative_path)
    if (
        logical.is_absolute()
        or not logical.parts
        or ".." in logical.parts
        or logical.suffix != ".py"
    ):
        return None
    root = repo_root.resolve()
    candidate = root.joinpath(*logical.parts)
    if candidate.is_symlink():
        return None
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _issue_sort_key(issue: DocstringValidationIssue) -> tuple[object, ...]:
    """Build the deterministic ordering key for one checker finding.

    Intent
    ------
    Order canonical checker findings without changing their content.

    Rationale
    ---------
    Stable validator output keeps repeated pre-commit runs comparable even when
    a checker builds findings from sets or optional source locations.

    Pseudocode
    ----------
    - set line_key = numbered line or missing-line sentinel
    - set node_key = node id or empty string
    - return line_key node_key code severity and message

    Wraps
    -----
    - none
    """

    line_key = (1, 0) if issue.line is None else (0, issue.line)
    return (
        line_key,
        issue.node_id or "",
        issue.code,
        issue.severity,
        issue.message,
    )


def _format_issue(
    repo_root: Path,
    issue: DocstringValidationIssue,
) -> str:
    """Render one structured checker finding for the root validator runner.

    Intent
    ------
    Convert canonical docstring diagnostics into stable repository-relative
    messages understood by the root validator result protocol.

    Rationale
    ---------
    The checker record remains the semantic authority; this adapter adds only
    location and field formatting needed by the shared pre-commit renderer.

    Pseudocode
    ----------
    - set relative_path = issue path relative to repo_root
    - set location = relative_path plus optional source line
    - set subject = severity code and optional node id
    - return location subject and issue message

    Wraps
    -----
    - none
    """

    try:
        relative_path = issue.path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        relative_path = issue.path
    location = relative_path.as_posix()
    if issue.line is not None:
        location = f"{location}:{issue.line}"
    subject = f"[{issue.severity}] {issue.code}"
    if issue.node_id:
        subject += f" ({issue.node_id})"
    return f"{location}: {subject}: {issue.message}"


def validate_staged(
    repo_root: Path,
    staged_paths: Sequence[str],
) -> list[str]:
    """Validate eligible staged Python modules with the canonical checker.

    Intent
    ------
    Apply the existing parser-backed and AST-backed Officina docstring policy to
    Python files changed in the captured Git index.

    Rationale
    ---------
    A thin staged adapter makes canonical docstring enforcement part of the root
    pre-commit gate without duplicating policy or scanning unchanged modules.

    Pseudocode
    ----------
    - set ordered_paths = staged_paths sorted lexically
    - set collected_findings = empty finding list
    - for relative_path in ordered_paths:
      - set module_path = safe regular Python path for relative_path
      - set access_finding = bounded error when Python source is missing or unreadable
      - set canonical_issues = canonical docstring findings or bounded parse finding
      - set collected_findings = collected_findings plus formatted canonical_issues
    - return collected_findings

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.validators.docstring_validator.validate_module_docstrings:
      why:
        constructs: "Builds the canonical syntax and behavioral findings for each eligible staged Python module."
    ._eligible_python_path:
      why:
        constructs: "Builds the bounded staged-mirror path supplied to the canonical module checker."
    ._format_issue:
      why:
        constructs: "Builds each stable root-validator message from a canonical structured finding."
    """

    errors: list[str] = []
    for relative_path in sorted(staged_paths):
        try:
            module_path = _eligible_python_path(repo_root, relative_path)
        except OSError:
            errors.append(
                f"{relative_path}: staged Python source is missing or unreadable"
            )
            continue
        if module_path is None:
            continue
        try:
            source = module_path.read_text(encoding="utf-8")
        except OSError:
            errors.append(
                f"{relative_path}: staged Python source is missing or unreadable"
            )
            continue
        except UnicodeError as exc:
            errors.append(
                f"{relative_path}: cannot decode Python source as UTF-8: {exc}"
            )
            continue
        try:
            ast.parse(source, filename=str(module_path))
        except SyntaxError as exc:
            location = relative_path
            if exc.lineno is not None:
                location = f"{location}:{exc.lineno}"
            errors.append(f"{location}: cannot parse Python: {exc.msg}")
            continue
        issues = validate_module_docstrings(module_path)
        errors.extend(
            _format_issue(repo_root, issue)
            for issue in sorted(issues, key=_issue_sort_key)
        )
    return errors


def validate(repo_root: Path) -> list[str]:
    """Fail closed when invoked without staged-path-aware runner support.

    Intent
    ------
    Prevent an older root validator runner from silently skipping docstring
    enforcement.

    Rationale
    ---------
    The adapter cannot infer staged paths safely from the isolated child mirror,
    so compatibility must be explicit failure rather than a full scan or no-op.

    Pseudocode
    ----------
    - set missing_protocol_error = staged-path-aware validator runner requirement
    - return missing_protocol_error

    Wraps
    -----
    - none
    """

    del repo_root
    return ["docstrings: staged-path-aware validator runner is required"]
