#!/usr/bin/env python3
"""Schema-backed docstring validation.

The validator layer reads normalized rules from ``docstring_schema`` and applies
them to parsed docstring text. Parser modules can stay permissive; validation
produces structured warnings and codes for CI and tooling.
"""

from __future__ import annotations

from typing import Iterable
from textwrap import dedent

from .docstring_parser import (
    ParserIssue,
    parse_graph_block,
    parse_ownership_reference,
    validate_edge_expression,
)
from .docstring_schema import load_docstring_schema


def _collect_invalid_edges(lines: Iterable[str], section_name: str) -> tuple[ParserIssue, ...]:
    """Collect malformed edge expressions from a section."""
    issues: list[ParserIssue] = []
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        if "->" in raw and not validate_edge_expression(raw):
            issues.append(
                ParserIssue(
                    code="docstring.invalid-edge",
                    message=(
                        f"Invalid edge {raw!r}; expected "
                        "syntax 'source -> target'."
                    ),
                    section=section_name,
                    severity="warning",
                )
            )
    return tuple(issues)


def _collect_invalid_wraps(lines: Iterable[str]) -> tuple[ParserIssue, ...]:
    """Collect malformed ``Wraps`` entries.

    Parse logic is intentionally delegated to ``docstring_parser`` so this module
    remains focused on classification and issue emission.
    """
    issues: list[ParserIssue] = []
    from .docstring_parser import _parse_wrap_entry

    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        if _parse_wrap_entry(raw) is None:
            issues.append(
                ParserIssue(
                    code="docstring.invalid-wraps",
                    message=(
                        f"Invalid wraps entry {raw!r}; expected "
                        "'<target> -> preprocess: <text>; postprocess: <text>; "
                        "fixed_arguments: <text>'."
                    ),
                    section="Wraps",
                    severity="warning",
                )
            )
    return tuple(issues)


def _collect_invalid_module_dependencies(
    lines: Iterable[str],
    *,
    section: str,
    allow_implicit: bool,
) -> tuple[ParserIssue, ...]:
    """Collect malformed module dependency references."""
    issues: list[ParserIssue] = []
    from .docstring_parser import _parse_module_dependency_ref

    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        parsed = _parse_module_dependency_ref(
            raw,
            allow_implicit=allow_implicit,
        )
        if parsed is None:
            issues.append(
                ParserIssue(
                    code="docstring.invalid-module-dependency",
                    message=(
                        f"Invalid module dependency {raw!r}; expected "
                        "'<name>(<args>) [implicit] -> <why>' or "
                        "'<name> [implicit] -> <why>'."
                    ),
                    section=section,
                    severity="warning",
                )
            )
            continue
    return tuple(issues)


def _collect_invalid_ownership(
    lines: Iterable[str],
    *,
    section: str = "Owns",
) -> tuple[ParserIssue, ...]:
    """Collect malformed ownership declarations from ``Owns`` or similar sections."""
    issues: list[ParserIssue] = []
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        if parse_ownership_reference(raw) is None:
            issues.append(
                ParserIssue(
                    code="docstring.invalid-owns",
                    message=(
                        f"Invalid ownership entry {raw!r}; expected "
                        "'<module>: <owner>' or '<owner>'."
                    ),
                    section=section,
                    severity="warning",
                )
            )
    return tuple(issues)


def _collect_invalid_pseudocode(
    steps: list,
    *,
    section: str,
    max_steps: int,
    max_step_chars: int,
    max_total_chars: int,
) -> tuple[ParserIssue, ...]:
    """Collect malformed pseudocode entries from a parsed pseudocode section.

    The parser maps one of the configured keywords to a ``kind`` when a control
    keyword appears at the start of a line.
    """
    issues: list[ParserIssue] = []
    if not steps:
        return ()

    requires_clause = {"if", "elif", "for", "for_each", "while", "with"}
    block_controls = {
        "if",
        "elif",
        "else",
        "for",
        "for_each",
        "while",
        "try",
        "except",
        "finally",
        "loop",
        "with",
    }

    require_control_suffix = True
    require_control_colon = True
    enforce_indent_blocks = True
    if enforce_indent_blocks:
        open_blocks: list[dict[str, object]] = []
        for index, step in enumerate(steps):
            text = step.text.strip()
            if not text:
                issues.append(
                    ParserIssue(
                        code="docstring.invalid-pseudocode",
                        message="Pseudocode entry has no parseable text.",
                        section=section,
                        severity="warning",
                    )
                )
                continue
            kind = step.kind
            indent = step.indent
            keyword = "for each" if kind == "for_each" else kind
            suffix = text[len(keyword) :].strip() if text.startswith(keyword) else ""
            colon_position = suffix.find(":")
            has_inline_body = (
                colon_position != -1 and bool(suffix[colon_position + 1 :].strip())
            )

            while open_blocks and indent < open_blocks[-1]["indent"]:
                block = open_blocks.pop()
                if block["has_body"] is False:
                    issues.append(
                        ParserIssue(
                            code="docstring.pseudocode-indent",
                            message=(
                                f"Control block '{block['kind']}' at step {block['index']} "
                                "has no indented body."
                            ),
                            section=section,
                            severity="warning",
                        )
                    )
            # Mark parent control blocks as having body once we move inside any deeper
            # indentation level.
            for block in open_blocks:
                if indent > int(block["indent"]):
                    block["has_body"] = True

            if kind in {"elif", "else"}:
                matching = next(
                    (
                        entry
                        for entry in reversed(open_blocks)
                        if int(entry["indent"]) == indent
                        and str(entry["kind"]) in {"if", "elif"}
                    ),
                    None,
                )
                if matching is None:
                    issues.append(
                        ParserIssue(
                            code="docstring.pseudocode-flow",
                            message=(
                                f"'{keyword}' at step {index + 1} is missing a matching "
                                "'if' at the same indentation level."
                            ),
                            section=section,
                            severity="warning",
                        )
                    )

            if kind in {"except", "finally"}:
                matching = next(
                    (
                        entry
                        for entry in reversed(open_blocks)
                        if int(entry["indent"]) == indent
                        and str(entry["kind"]) in {"try", "except"}
                    ),
                    None,
                )
                if matching is None:
                    issues.append(
                        ParserIssue(
                            code="docstring.pseudocode-flow",
                            message=(
                                f"'{keyword}' at step {index + 1} should follow a "
                                "'try'/'except' at the same indentation level."
                            ),
                            section=section,
                            severity="warning",
                        )
                    )

            if kind in block_controls and require_control_colon and not ":" in suffix:
                issues.append(
                    ParserIssue(
                        code="docstring.pseudocode-colon",
                        message=(
                            f"Control line '{keyword}' at step {index + 1} should use ':' "
                            "syntax."
                        ),
                        section=section,
                        severity="warning",
                    )
                )

            if require_control_suffix and kind in requires_clause:
                predicate = suffix if colon_position == -1 else suffix[:colon_position].strip()
                if not predicate:
                    issues.append(
                        ParserIssue(
                            code="docstring.invalid-pseudocode",
                            message="Pseudocode control line is missing its control predicate.",
                            section=section,
                            severity="warning",
                        )
                    )

            if kind in block_controls and not has_inline_body:
                open_blocks.append(
                    {
                        "kind": kind,
                        "indent": indent,
                        "index": index + 1,
                        "has_body": False,
                    }
                )

        while open_blocks:
            block = open_blocks.pop()
            if block["has_body"] is False:
                issues.append(
                    ParserIssue(
                        code="docstring.pseudocode-indent",
                        message=(
                            f"Control block '{block['kind']}' at step {block['index']} "
                            "has no indented body."
                        ),
                        section=section,
                        severity="warning",
                    )
                )

    for step in steps:
        if require_control_suffix and step.kind in requires_clause:
            keyword = "for each" if step.kind == "for_each" else step.kind
            if not step.text[len(keyword) :].strip():
                issues.append(
                    ParserIssue(
                        code="docstring.invalid-pseudocode",
                        message="Pseudocode control line is missing its control predicate.",
                        section=section,
                        severity="warning",
                    )
                )

    if max_steps > 0 and len(steps) > max_steps:
        issues.append(
            ParserIssue(
                code="docstring.pseudocode-step-cap",
                message=f"Pseudocode has {len(steps)} steps, exceeding cap {max_steps}.",
                section=section,
                severity="warning",
            )
        )

    if max_step_chars > 0:
        for step in steps:
            if len(step.text) > max_step_chars:
                issues.append(
                    ParserIssue(
                        code="docstring.pseudocode-step-length",
                        message=(
                            f"Pseudocode step text has {len(step.text)} chars, "
                            f"exceeding cap {max_step_chars}."
                        ),
                        section=section,
                        severity="warning",
                    )
                )

    if max_total_chars > 0:
        total_chars = sum(len(step.text) for step in steps)
        if total_chars > max_total_chars:
            issues.append(
                ParserIssue(
                    code="docstring.pseudocode-total-length",
                    message=(
                        f"Pseudocode total text has {total_chars} chars, "
                        f"exceeding cap {max_total_chars}."
                    ),
                    section=section,
                    severity="warning",
                )
            )

    return tuple(issues)


def _collect_forbidden_summary_phrases(
    summary: str,
    *,
    forbidden_summary_phrases: tuple[str, ...],
) -> tuple[ParserIssue, ...]:
    """Collect warnings when the summary contains prohibited placeholder phrases."""
    if not summary or not forbidden_summary_phrases:
        return ()

    normalized_summary = summary.lower()
    matched: list[str] = []
    for phrase in forbidden_summary_phrases:
        phrase_text = phrase.strip().lower()
        if not phrase_text:
            continue
        if phrase_text in normalized_summary:
            matched.append(phrase)

    if not matched:
        return ()

    unique = ", ".join(sorted(set(matched)))
    return (
        ParserIssue(
            code="docstring.summary-forbidden",
            message=f"Summary contains placeholder phrasing that is not allowed: {unique}",
            section="summary",
            severity="warning",
        ),
    )


def validate_pipeline_docstring(docstring: str) -> tuple[ParserIssue, ...]:
    """Validate ``GraphPipeline`` syntax and required pipeline sections.

    This runs a light parser/lexer over the raw section text and collects
    structural issues (`PhaseEdges` / `NonInferableCalls`) as parse warnings.
    """
    schema_rules = load_docstring_schema()
    issues: list[ParserIssue] = []
    pipeline_sections = frozenset(schema_rules.pipeline.section_names())
    edge_sections = frozenset(
        section
        for section in pipeline_sections
        if section.lower().endswith("edges")
        or section.lower().endswith("calls")
    )
    lines = dedent(docstring).splitlines()
    in_pipeline = False
    section = None
    phase = None
    section_buffer: list[str] = []
    seen_sections: set[str] = set()
    has_graph_pipeline = "GraphPipeline" in lines

    for raw in lines:
        text = raw.rstrip().strip()
        if not text or set(text) == {"-"}:
            continue
        if text == "GraphPipeline":
            in_pipeline = True
            section = None
            phase = None
            section_buffer = []
            seen_sections.add("GraphPipeline")
            continue
        if not in_pipeline:
            continue
        if text in pipeline_sections:
            if section in edge_sections:
                issues.extend(_collect_invalid_edges(section_buffer, section))
            section_buffer = []
            section = text
            seen_sections.add(text)
            if section == "PhaseMembers":
                phase = None
            continue
        if text in {"Name", "Description", "Graph"}:
            if section in edge_sections:
                issues.extend(_collect_invalid_edges(section_buffer, section))
            section_buffer = []
            section = None
            if text == "Graph":
                in_pipeline = False
            continue
        if text.endswith(":") and section == "PhaseMembers":
            if section in edge_sections:
                issues.extend(_collect_invalid_edges(section_buffer, section))
            section_buffer = []
            phase = text[:-1].strip()
            continue
        if section in edge_sections:
            section_buffer.append(raw)
        if text and phase is not None and not section and text != "Phases":
            continue

    if section in edge_sections:
        issues.extend(_collect_invalid_edges(section_buffer, section))

    if not has_graph_pipeline:
        if schema_rules.pipeline.required:
            issues.append(
                ParserIssue(
                    code="docstring.missing-graphpipeline",
                    message="Docstring is missing required GraphPipeline block.",
                    section="GraphPipeline",
                    severity="warning",
                )
            )
        return tuple(issues)

    if schema_rules.pipeline.required and schema_rules.strict:
        required_sections = set(schema_rules.pipeline.required_sections)
        required_sections.discard("GraphPipeline")
        for section_name in required_sections:
            if section_name not in seen_sections:
                issues.append(
                    ParserIssue(
                        code="docstring.section-missing",
                        message=f"GraphPipeline block is missing required section '{section_name}'.",
                        section=section_name,
                        severity="warning",
                    )
                )

    return tuple(issues)


def check_graph_docstring(docstring: str) -> tuple[ParserIssue, ...]:
    """Validate callable/class docstrings against callable-level format rules."""
    schema_rules = load_docstring_schema()
    issues: list[ParserIssue] = []

    if not docstring or not docstring.strip():
        issues.append(
            ParserIssue(
                code="docstring.empty",
                message="Docstring is empty.",
                severity="warning",
            )
        )
        return tuple(issues)

    spec = parse_graph_block(docstring)
    if schema_rules.callable.required_summary and not spec.summary:
        issues.append(
            ParserIssue(
                code="docstring.summary-missing",
                message="Docstring is missing a short summary line.",
                severity="warning",
            )
        )
    issues.extend(
        _collect_forbidden_summary_phrases(
            spec.summary,
            forbidden_summary_phrases=schema_rules.callable.forbidden_summary_phrases,
        )
    )

    if schema_rules.strict:
        for section_name in schema_rules.callable.required_sections:
            if section_name not in spec.sections:
                issues.append(
                    ParserIssue(
                        code="docstring.section-missing",
                        message=f"Docstring is missing required section '{section_name}'.",
                        section=section_name,
                        severity="warning",
                    )
                )

    issues.extend(_collect_invalid_edges(spec.sections.get("NonInferableCalls", []), "NonInferableCalls"))
    issues.extend(_collect_invalid_wraps(spec.sections.get("Wraps", [])))
    dependency_rules = schema_rules.module_dependencies
    issues.extend(
        _collect_invalid_module_dependencies(
            spec.sections.get(dependency_rules.calls_section, []),
            section=dependency_rules.calls_section,
            allow_implicit=dependency_rules.allow_implicit,
        )
    )
    issues.extend(
        _collect_invalid_module_dependencies(
            spec.sections.get(dependency_rules.instantiates_section, []),
            section=dependency_rules.instantiates_section,
            allow_implicit=dependency_rules.allow_implicit,
        )
    )
    ownership_rules = schema_rules.callable.ownership
    if ownership_rules.section_required and ownership_rules.section not in spec.sections:
        issues.append(
            ParserIssue(
                code="docstring.section-missing",
                message=f"Docstring is missing required section '{ownership_rules.section}'.",
                section=ownership_rules.section,
                severity="warning",
            )
        )

    issues.extend(
        _collect_invalid_ownership(
            spec.sections.get(ownership_rules.section, []),
            section=ownership_rules.section,
        )
    )
    issues.extend(
        _collect_invalid_pseudocode(
            spec.pseudocode_steps,
            section=schema_rules.callable.pseudocode.section,
            max_steps=schema_rules.callable.pseudocode.max_steps,
            max_step_chars=schema_rules.callable.pseudocode.max_step_chars,
            max_total_chars=schema_rules.callable.pseudocode.max_total_chars,
        )
    )
    if not ownership_rules.allows_multiple and len(spec.owns) > 1:
        issues.append(
            ParserIssue(
                code="docstring.owns-too-many",
                message=f"Docstring has too many entries in {ownership_rules.section}.",
                section=ownership_rules.section,
                severity="warning",
            )
        )
    return tuple(issues)


def check_pipeline_docstring(docstring: str) -> tuple[ParserIssue, ...]:
    """Validate the module-level pipeline block and optional module contract."""
    schema_rules = load_docstring_schema()
    issues: list[ParserIssue] = []

    if not docstring or not docstring.strip():
        issues.append(
            ParserIssue(
                code="docstring.empty",
                message="Module docstring is empty.",
                severity="warning",
            )
        )
        return tuple(issues)

    if schema_rules.module.required:
        module_spec = parse_graph_block(docstring)
        if schema_rules.module.required_summary and not module_spec.summary:
            issues.append(
                ParserIssue(
                    code="docstring.summary-missing",
                    message="Module docstring is missing a short summary line.",
                    severity="warning",
                )
            )
        if schema_rules.strict:
            for section_name in schema_rules.module.required_sections:
                if section_name not in module_spec.sections:
                    issues.append(
                        ParserIssue(
                            code="docstring.section-missing",
                            message=f"Module docstring is missing required section '{section_name}'.",
                            section=section_name,
                            severity="warning",
                        )
                    )

    if "GraphPipeline" not in docstring:
        if schema_rules.pipeline.required:
            return (
                ParserIssue(
                    code="docstring.missing-graphpipeline",
                    message="Docstring is missing required GraphPipeline block.",
                    section="GraphPipeline",
                    severity="warning",
                ),
            )
        return tuple(issues)

    issues.extend(validate_pipeline_docstring(docstring))
    return tuple(issues)


def check(docstring: str, kind: str = "callable") -> tuple[ParserIssue, ...]:
    """Generic validation entry point.

    Parameters
    ----------
    kind : {'callable', 'pipeline', 'function', 'method', 'class', 'module'}
        Validation mode selector. Unknown values raise ``ValueError``.
    """
    normalized_kind = kind.lower().strip()
    if normalized_kind in {"callable", "function", "method", "class", "callables"}:
        return check_graph_docstring(docstring)
    if normalized_kind in {"pipeline", "module"}:
        return check_pipeline_docstring(docstring)
    raise ValueError(f"Unknown docstring validation kind: {kind!r}")


__all__ = [
    "check",
    "check_graph_docstring",
    "check_pipeline_docstring",
    "validate_pipeline_docstring",
]
