#!/usr/bin/env python3
"""Schema-backed docstring validation.

The validator layer reads normalized rules from ``docstring_schema`` and applies
them to parsed docstring text. Parser modules can stay permissive; validation
produces structured warnings and codes for CI and tooling.
"""

from __future__ import annotations

from typing import Iterable
from textwrap import dedent
from collections import Counter
import re

from .docstring_parser import (
    ParserIssue,
    _section_header,
    parse_pseudocode_dependency_ref,
    parse_graph_block,
    parse_ownership_reference,
    validate_edge_expression,
)
from .docstring_schema import load_docstring_schema

_DEPENDENCY_REFERENCE_MARKER_SCAN_RE = re.compile(
    r"(?<![A-Za-z0-9_])@(?:(?P<section>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*)?(?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)(?:\([^)]*\))?"
)


def _split_dependency_reference(value: str) -> tuple[str | None, str]:
    """Split ``section:name`` into ``(section, name)`` while preserving bare names."""
    raw = (value or "").strip()
    if not raw:
        return None, ""

    if ":" not in raw:
        return None, raw

    section, name = [part.strip() for part in raw.split(":", 1)]
    if not section or not name:
        return None, raw
    return section, name


def _collect_invalid_edges(lines: Iterable[str], section_name: str) -> tuple[ParserIssue, ...]:
    """Collect malformed edge expressions from a section."""
    issues: list[ParserIssue] = []
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        if validate_edge_expression(raw):
            continue
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


_VALID_SECTION_UNDERLINE_CHARS = {"-", "="}


def _looks_like_section_underline(raw: str) -> bool:
    """Return ``True`` for NumPy-style section underlines."""
    text = raw.strip()
    return bool(text) and len(set(text)) == 1 and text[0] in _VALID_SECTION_UNDERLINE_CHARS


def _collect_section_header_issues(lines: list[str], section_names: frozenset[str]) -> tuple[ParserIssue, ...]:
    """Collect unknown and duplicate section declarations from raw docstring lines."""
    issues: list[ParserIssue] = []
    seen_sections: list[str] = []

    for index in range(len(lines)):
        header = _section_header(
            lines=lines,
            index=index,
            section_names=section_names,
        )
        if header is None:
            raw = lines[index].strip()
            if (
                raw
                and not raw.startswith("-")
                and index + 1 < len(lines)
                and _looks_like_section_underline(lines[index + 1])
                and raw not in section_names
            ):
                issues.append(
                    ParserIssue(
                        code="docstring.invalid-section",
                        message=(
                            f"Unknown docstring section '{raw}' at line {index + 1}; "
                            "did you mean a standard section?"
                        ),
                        section=raw,
                        severity="warning",
                    )
                )
            continue
        seen_sections.append(header)

    for section_name, count in Counter(seen_sections).items():
        if count <= 1:
            continue
        issues.append(
            ParserIssue(
                code="docstring.duplicate-section",
                message=(
                    f"Docstring section '{section_name}' is declared {count} times; "
                    "keep one definition."
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


def _collect_invalid_dependency_marker_syntax(
    lines: Iterable[object],
    section: str,
    *,
    allowed_scopes: tuple[str, ...] | None = None,
) -> tuple[ParserIssue, ...]:
    """Collect malformed dependency reference markers in configured sections."""
    issues: list[ParserIssue] = []
    valid_scopes = {scope for scope in (allowed_scopes or ()) if scope}
    for line in lines:
        text = str(getattr(line, "text", line)).strip()
        if not text:
            continue
        for marker_match in _DEPENDENCY_REFERENCE_MARKER_SCAN_RE.finditer(text):
            marker = marker_match.group(0).rstrip(".,;:")
            if parse_pseudocode_dependency_ref(marker) is None:
                issues.append(
                    ParserIssue(
                        code="docstring.invalid-pseudocode-ref",
                        message=(
                            f"Invalid dependency reference marker in {text!r}; "
                            "use @<name>, @<section:name>, and optional @<name>(...)."
                        ),
                        section=section,
                        severity="warning",
                    )
                )
                continue

            parsed_scope, _ = parse_pseudocode_dependency_ref(marker)
            if parsed_scope is not None and parsed_scope not in valid_scopes:
                issues.append(
                    ParserIssue(
                        code="docstring.invalid-pseudocode-ref",
                        message=(
                            f"Dependency marker scope {parsed_scope!r} is not a known "
                            f"dependency declaration section. Known scopes: "
                            f"{', '.join(sorted(valid_scopes)) or '<none>'}."
                        ),
                        section=section,
                        severity="warning",
                    )
                )
    return tuple(issues)


def _dependency_reference_section_lines(
    section_names: tuple[str, ...],
    spec,
    pseudocode_section: str,
) -> dict[str, tuple[str, ...]]:
    """Collect raw lines for each dependency-reference section."""
    lines_by_section: dict[str, tuple[str, ...]] = {}

    for section_name in section_names:
        section_lines = getattr(spec, "sections", {}).get(section_name)
        if not section_lines:
            continue
        if section_name == pseudocode_section:
            if not getattr(spec, "pseudocode_steps", None):
                continue
            lines_by_section[section_name] = tuple(
                str(getattr(step, "text", "")) for step in spec.pseudocode_steps
            )
            continue
        lines_by_section[section_name] = tuple(section_lines)

    return lines_by_section


def _collect_pseudocode_dependency_entity_issues(
    spec,
    *,
    section: str,
    enforce_declared_coverage: bool,
    declaration_sections: tuple[str, ...] | None = None,
) -> tuple[ParserIssue, ...]:
    """Collect coverage and declaration consistency for explicit dependency refs."""
    issues: list[ParserIssue] = []

    declared: list[tuple[str, str]] = []
    seen_declared: set[tuple[str, str]] = set()
    declared_by_name: dict[str, list[str]] = {}
    scoped_refs: set[tuple[str, str]] = set()
    unscoped_refs: set[str] = set()

    def _add_declared(source: str, value: str | None) -> None:
        candidate = (value or "").strip()
        if not candidate:
            return
        key = (source, candidate)
        if key in seen_declared:
            return
        declared.append(key)
        seen_declared.add(key)
        declared_by_name.setdefault(candidate, [])
        if source not in declared_by_name[candidate]:
            declared_by_name[candidate].append(source)

    def _add_reference(ref: str | None) -> None:
        section_name, name = _split_dependency_reference(ref or "")
        if not name:
            return
        if section_name is None:
            unscoped_refs.add(name)
            return
        scoped_refs.add((section_name, name))

    normalized_declaration_sections: tuple[str, ...] = (
        "CallsFromModule",
        "InstantiationsFromModule",
        "Wraps",
        "NonInferableCalls",
    )
    if declaration_sections is not None:
        cleaned_sections = tuple(section.strip() for section in declaration_sections)
        if cleaned_sections:
            normalized_declaration_sections = tuple(
                section for section in cleaned_sections if section
            ) + normalized_declaration_sections[len(cleaned_sections) :]

    calls_section = normalized_declaration_sections[0]
    instantiates_section = normalized_declaration_sections[1]
    wraps_section = normalized_declaration_sections[2]
    noninferable_section = normalized_declaration_sections[3]

    for dependency in getattr(spec, "module_calls", ()):
        _add_declared(calls_section, getattr(dependency, "name", ""))
    for dependency in getattr(spec, "module_instantiates", ()):
        _add_declared(instantiates_section, getattr(dependency, "name", ""))
    for dependency in getattr(spec, "wraps", ()):
        _add_declared(wraps_section, getattr(dependency, "target", ""))
    for source, target in getattr(spec, "noninferable_calls", []):
        _add_declared(noninferable_section, source)
        _add_declared(noninferable_section, target)

    for ref in getattr(spec, "pseudocode_dependency_refs", ()):
        _add_reference(ref)

    if not scoped_refs and not unscoped_refs:
        return tuple(issues)

    for ref, ref_sources in (
        (ref, set([source for source in declared_by_name.get(ref, [])]))
        for ref in unscoped_refs
    ):
        if not ref_sources:
            issues.append(
                ParserIssue(
                    code="docstring.undeclared-pseudocode-dependency",
                    message=(
                        f"Dependency reference {ref!r} is not declared in "
                        f"{', '.join(normalized_declaration_sections)}."
                    ),
                    section=section,
                    severity="warning",
                )
            )
            continue
        if len(ref_sources) > 1:
            issues.append(
                ParserIssue(
                    code="docstring.ambiguous-pseudocode-dependency",
                    message=(
                        f"Dependency reference {ref!r} is ambiguous across "
                        f"{', '.join(sorted(ref_sources))}; add a section scope, e.g. "
                        f"@{calls_section}:{ref}."
                    ),
                    section=section,
                    severity="warning",
                )
            )

    for section_name, name in sorted(scoped_refs):
        sources = declared_by_name.get(name, [])
        if section_name not in sources:
            options = ", ".join(sources or ["<none>"])
            issues.append(
                ParserIssue(
                    code="docstring.undeclared-pseudocode-dependency",
                    message=(
                        f"Dependency reference {section_name + ':' + name!r} is not declared "
                        f"under {section_name!r}; known sources are {options}."
                    ),
                    section=section,
                    severity="warning",
                )
            )

    if not enforce_declared_coverage:
        return tuple(issues)

    for source, name in declared:
        coverage_sources = declared_by_name.get(name, [])
        has_scope_ref = (source, name) in scoped_refs
        has_unscoped_ref = name in unscoped_refs and len(coverage_sources) == 1
        if has_scope_ref or has_unscoped_ref:
            continue

        expected = (
            f"{source}:{name}"
            if len(coverage_sources) > 1
            else f"{name} (or scoped with {source}:)"
        )
        issues.append(
            ParserIssue(
                code="docstring.pseudocode-dependency-missing",
                message=(
                    f"Declared dependency '{name}' is not referenced in dependency "
                    "markers; "
                    f"add @{expected}."
                ),
                section=section,
                severity="warning",
            )
        )

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
    min_steps: int,
    max_steps: int,
    max_step_chars: int,
    max_total_chars: int,
) -> tuple[ParserIssue, ...]:
    """Collect malformed pseudocode entries from a parsed pseudocode section.

    The parser maps one of the configured keywords to a ``kind`` when a control
    keyword appears at the start of a line.
    """
    issues: list[ParserIssue] = []
    if min_steps < 1 and not steps:
        return ()
    if len(steps) < min_steps:
        issues.append(
            ParserIssue(
                code="docstring.pseudocode-step-min",
                message=(
                    f"Pseudocode has {len(steps)} steps, fewer than required minimum "
                    f"{min_steps}."
                ),
                section=section,
                severity="warning",
            )
        )
        if not steps:
            return tuple(issues)

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


def _collect_forbidden_phrases(
    text: str,
    *,
    forbidden_phrases: tuple[str, ...],
    code: str,
    section: str,
    message_prefix: str,
) -> tuple[ParserIssue, ...]:
    """Collect warnings for prohibited placeholder phrasing in free text."""
    if not text or not forbidden_phrases:
        return ()

    normalized_text = text.lower()
    matched: list[str] = []
    for phrase in forbidden_phrases:
        phrase_text = phrase.strip().lower()
        if not phrase_text:
            continue
        if phrase_text in normalized_text:
            matched.append(phrase)

    if not matched:
        return ()

    unique = ", ".join(sorted(set(matched)))
    return (
        ParserIssue(
            code=code,
            message=(
                f"{message_prefix} contains placeholder phrasing that is not allowed: "
                f"{unique}"
            ),
            section=section,
            severity="warning",
        ),
    )


def _collect_forbidden_summary_phrases(
    summary: str,
    *,
    forbidden_summary_phrases: tuple[str, ...],
) -> tuple[ParserIssue, ...]:
    """Collect warnings when the summary contains prohibited placeholder phrases."""
    return _collect_forbidden_phrases(
        text=summary,
        forbidden_phrases=forbidden_summary_phrases,
        code="docstring.summary-forbidden",
        section="summary",
        message_prefix="Summary",
    )


def _collect_forbidden_rationale_phrases(
    rationale: str,
    *,
    forbidden_rationale_phrases: tuple[str, ...],
) -> tuple[ParserIssue, ...]:
    """Collect warnings when rationale contains prohibited placeholder phrases."""
    return _collect_forbidden_phrases(
        text=rationale,
        forbidden_phrases=forbidden_rationale_phrases,
        code="docstring.rationale-forbidden",
        section="Rationale",
        message_prefix="Rationale",
    )


def _collect_forbidden_pseudocode_phrases(
    steps: list,
    *,
    forbidden_pseudocode_phrases: tuple[str, ...],
) -> tuple[ParserIssue, ...]:
    """Collect warnings when pseudocode text contains prohibited placeholder phrases."""
    joined = " ".join(step.text for step in steps if getattr(step, "text", ""))
    return _collect_forbidden_phrases(
        text=joined,
        forbidden_phrases=forbidden_pseudocode_phrases,
        code="docstring.pseudocode-forbidden",
        section="Pseudocode",
        message_prefix="Pseudocode",
    )


def _collect_text_length(
    text: str,
    *,
    code: str,
    section: str,
    min_chars: int,
    max_chars: int,
    label: str,
) -> tuple[ParserIssue, ...]:
    """Collect readable length checks for concise textual docstring fields."""
    if not text:
        return ()

    value = text.strip()
    if not value:
        return ()

    count = len(value)
    issues: list[ParserIssue] = []
    if min_chars > 0 and count < min_chars:
        issues.append(
            ParserIssue(
                code=code,
                message=(
                    f"{label} text has {count} chars, below minimum {min_chars}. "
                    f"Expand this section with concrete behavior."
                ),
                section=section,
                severity="warning",
            )
        )
    if max_chars > 0 and count > max_chars:
        issues.append(
            ParserIssue(
                code=code,
                message=(
                    f"{label} text has {count} chars, above maximum {max_chars}. "
                    "Trim to keep docs concise."
                ),
                section=section,
                severity="warning",
            )
        )

    return tuple(issues)


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

    dedented = dedent(docstring)
    lines = dedented.splitlines()
    section_names = frozenset(schema_rules.section_names())
    issues.extend(_collect_section_header_issues(lines, section_names=section_names))

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
    issues.extend(
        _collect_text_length(
            text=spec.summary,
            code="docstring.summary-length",
            section="summary",
            min_chars=schema_rules.callable.summary_min_chars,
            max_chars=schema_rules.callable.summary_max_chars,
            label="Summary",
        )
    )
    issues.extend(
        _collect_forbidden_rationale_phrases(
            spec.rationale or "",
            forbidden_rationale_phrases=schema_rules.callable.forbidden_rationale_phrases,
        )
    )
    issues.extend(
        _collect_text_length(
            text=spec.rationale or "",
            code="docstring.rationale-length",
            section="Rationale",
            min_chars=schema_rules.callable.rationale_min_chars,
            max_chars=schema_rules.callable.rationale_max_chars,
            label="Rationale",
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
            min_steps=schema_rules.callable.min_pseudocode_steps,
            max_steps=schema_rules.callable.pseudocode.max_steps,
            max_step_chars=schema_rules.callable.pseudocode.max_step_chars,
            max_total_chars=schema_rules.callable.pseudocode.max_total_chars,
        )
    )
    issues.extend(
        _collect_forbidden_pseudocode_phrases(
            spec.pseudocode_steps,
            forbidden_pseudocode_phrases=schema_rules.callable.forbidden_pseudocode_phrases,
        )
    )
    dependency_reference_sections = schema_rules.callable.dependency_reference_sections
    section_lines = _dependency_reference_section_lines(
        dependency_reference_sections,
        spec,
        pseudocode_section=schema_rules.callable.pseudocode.section,
    )
    for section_name in dependency_reference_sections:
        if section_name not in section_lines:
            continue
        issues.extend(
            _collect_invalid_dependency_marker_syntax(
                section_lines[section_name],
                section=section_name,
                allowed_scopes=(
                    dependency_rules.calls_section,
                    dependency_rules.instantiates_section,
                    "Wraps",
                    "NonInferableCalls",
                ),
            )
        )
    issues.extend(
        _collect_pseudocode_dependency_entity_issues(
            spec,
            section="DependencyRefs",
            enforce_declared_coverage=dependency_rules.enforce_declared_dependency_pseudocode_coverage,
            declaration_sections=(
                dependency_rules.calls_section,
                dependency_rules.instantiates_section,
                "Wraps",
                "NonInferableCalls",
            ),
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
