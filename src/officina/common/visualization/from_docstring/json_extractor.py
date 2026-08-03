#!/usr/bin/env python3
"""Docstring-aware graph JSON extraction for shared visualizers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from ..base_visualizer import GraphBuildError, GraphBuildValidationError
from ..base_extractor import BaseJsonExtractor
from ...docstring import PipelineSpec, FunctionSpec
from .io import default_out_dir, gather_modules, gather_modules_in_directory
from .payload_builder import to_dependency_json
from .parser import (
    collect_defined_callables,
    infer_call_edges,
    parse_docstring_module,
    parse_module,
)

DEFAULT_FORMATS = ("dot", "svg", "png", "html")
_MAX_VALIDATION_ISSUES_IN_ERROR = 30

if TYPE_CHECKING:  # pragma: no cover
    from ....validators import DocstringValidationIssue


def _normalize_class_nodes(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, (list, tuple, set)):
        return tuple(str(item) for item in sorted(raw))
    if isinstance(raw, str):
        return (raw.strip(),) if raw.strip() else ()
    return ()


def _format_validation_issue(issue: "DocstringValidationIssue") -> str:
    location = issue.path.as_posix()
    if issue.line is not None:
        location = f"{location}:{issue.line}"
    node = f" [{issue.node_id}]" if issue.node_id else ""
    return f"{location}{node} {issue.code}: {issue.message}"


def _raise_if_docstrings_invalid(module_path: Path) -> None:
    from ....validators import validate_module_docstrings

    issues = validate_module_docstrings(module_path, check_group="all")
    if not issues:
        return

    shown = issues[:_MAX_VALIDATION_ISSUES_IN_ERROR]
    lines = [
        f"docstring validation failed for {module_path.as_posix()} ({len(issues)} issue(s)); graph extraction aborted.",
        *[f"- {_format_validation_issue(issue)}" for issue in shown],
    ]
    remaining = len(issues) - len(shown)
    if remaining > 0:
        lines.append(f"- ... {remaining} more issue(s)")
    raise GraphBuildValidationError("\n".join(lines))


def extract_docstring_dependency_json(
    module_path: str | Path,
    infer_local_edges: bool = False,
    *,
    class_nodes: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Parse a module and return a graph payload directly."""
    resolved_module_path = Path(module_path).resolve()
    _raise_if_docstrings_invalid(resolved_module_path)
    return to_dependency_json(
        module_path=resolved_module_path,
        infer_local_edges=infer_local_edges,
        class_nodes=class_nodes,
    )


class DocstringJsonExtractor(BaseJsonExtractor):
    """Extractor that maps a source to docstring-derived dependency payloads."""

    @staticmethod
    def extract_from_path(
        module_path: Path,
        *,
        class_nodes: tuple[str, ...] = (),
        infer_local_edges: bool = False,
    ) -> dict[str, Any]:
        """Extract payload from an already-resolved path."""
        return extract_docstring_dependency_json(
            module_path,
            infer_local_edges=infer_local_edges,
            class_nodes=class_nodes,
        )

    def extract(self, source) -> dict[str, Any]:
        """Extract a normalized dependency payload from one source."""
        resolved = Path(source.resolved_path).resolve()
        if not resolved.is_file():
            raise GraphBuildError(
                f"docstring extraction requires an existing .py file: {resolved}"
            )
        class_nodes = _normalize_class_nodes(getattr(source, "options", {}).get("class_nodes"))
        infer_local_edges = bool(getattr(source, "options", {}).get("infer_local_edges", False))
        return extract_docstring_dependency_json(
            resolved,
            infer_local_edges=infer_local_edges,
            class_nodes=class_nodes,
        )


__all__ = [
    "DEFAULT_FORMATS",
    "DocstringJsonExtractor",
    "collect_defined_callables",
    "default_out_dir",
    "parse_docstring_module",
    "parse_module",
    "infer_call_edges",
    "to_dependency_json",
    "extract_docstring_dependency_json",
    "gather_modules",
    "gather_modules_in_directory",
]
