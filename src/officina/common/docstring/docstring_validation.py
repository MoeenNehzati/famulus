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
    parse_graph_block,
    parse_ownership_reference,
    validate_edge_expression,
)
from .docstring_schema import DocstringSchema, load_docstring_schema

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


def _dot_segment_suffix_matches(ref: str, declared: str) -> bool:
    """Return true when ref exactly matches or is a dot-segment suffix."""
    needle = (ref or "").strip()
    candidate = (declared or "").strip()
    if not needle or not candidate:
        return False
    if needle == candidate:
        return True
    clean_needle = needle.lstrip(".")
    clean_candidate = candidate.lstrip(".")
    return clean_candidate == clean_needle or clean_candidate.endswith(f".{clean_needle}")


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
        raw = lines[index].strip()
        if raw.endswith(":") and raw[:-1] in section_names:
            issues.append(
                ParserIssue(
                    code="docstring.invalid-section-header",
                    message=(
                        f"Docstring section '{raw}' should be written as '{raw[:-1]}' "
                        "with a NumPy-style underline, not as a YAML key."
                    ),
                    section=raw[:-1],
                    severity="warning",
                )
            )
            continue
        header = _section_header(
            lines=lines,
            index=index,
            section_names=section_names,
        )
        if header is None:
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
    allow_legacy_flat: bool,
    require_why: bool,
) -> tuple[ParserIssue, ...]:
    """Collect malformed module dependency references."""
    issues: list[ParserIssue] = []
    from .docstring_parser import _parse_module_dependency_section

    _, invalid = _parse_module_dependency_section(
        lines,
        allow_implicit=allow_implicit,
        allow_legacy_flat=allow_legacy_flat,
        require_why=require_why,
    )
    for raw in invalid:
        issues.append(
            ParserIssue(
                code="docstring.invalid-module-dependency",
                message=(
                    f"Invalid module dependency {raw!r}; expected a YAML-like "
                    "tree leaf with 'why:' or legacy '<name> -> <why>' when "
                    "legacy flat syntax is enabled."
                ),
                section=section,
                severity="warning",
            )
        )
    return tuple(issues)


def _collect_invalid_dispatch_dependencies(
    lines: Iterable[str],
    *,
    section: str,
    allow_legacy_flat: bool,
    require_why: bool,
) -> tuple[ParserIssue, ...]:
    """Collect malformed dispatch dependency references."""
    issues: list[ParserIssue] = []
    from .docstring_parser import _parse_dispatch_dependency_section

    _, invalid = _parse_dispatch_dependency_section(
        lines,
        allow_legacy_flat=allow_legacy_flat,
        require_why=require_why,
    )
    for raw in invalid:
        issues.append(
            ParserIssue(
                code="docstring.invalid-module-dependency",
                message=(
                    f"Invalid dispatch dependency {raw!r}; expected a YAML-like "
                    "tree leaf with 'why:' or legacy '<id> -> <why>' when "
                    "legacy flat syntax is enabled."
                ),
                section=section,
                severity="warning",
            )
        )
    return tuple(issues)


def _collect_invalid_resources(
    lines: Iterable[str],
    *,
    section: str,
    require_why: bool,
) -> tuple[ParserIssue, ...]:
    """Collect malformed resource dependency declarations."""
    issues: list[ParserIssue] = []
    from .docstring_parser import _parse_resource_section

    _, invalid = _parse_resource_section(lines, require_why=require_why)
    for raw in invalid:
        issues.append(
            ParserIssue(
                code="docstring.invalid-resource",
                message=(
                    f"Invalid resource declaration {raw!r}; expected a YAML mapping "
                    "with kind, access, and why fields."
                ),
                section=section,
                severity="warning",
            )
        )
    return tuple(issues)


def _collect_invalid_dataflows(
    lines: Iterable[str],
    *,
    section: str,
    require_why: bool,
) -> tuple[ParserIssue, ...]:
    """Collect malformed dataflow declarations."""
    issues: list[ParserIssue] = []
    from .docstring_parser import _parse_dataflow_section

    _, invalid = _parse_dataflow_section(lines, require_why=require_why)
    for raw in invalid:
        issues.append(
            ParserIssue(
                code="docstring.invalid-dataflow",
                message=(
                    f"Invalid dataflow declaration {raw!r}; expected list entries "
                    "with from, to, kind, and why fields."
                ),
                section=section,
                severity="warning",
            )
        )
    return tuple(issues)


def _dependency_path_is_allowed(name: str, allowed_abs: tuple[str, ...]) -> bool:
    """Return True iff a logical dependency path is explicitly relative or allowed absolute."""
    raw = (name or "").strip()
    if not raw:
        return False
    if raw.startswith("."):
        return True
    first_segment = raw.split(".", 1)[0]
    return first_segment in set(allowed_abs)


def _collect_dependency_path_issues(
    values: Iterable[str],
    *,
    section: str,
    allowed_abs: tuple[str, ...],
) -> tuple[ParserIssue, ...]:
    """Collect dependency logical paths outside the repo-local portability policy."""
    issues: list[ParserIssue] = []
    allowed = tuple(root for root in allowed_abs if root)
    for value in values:
        raw = (value or "").strip()
        if not raw or _dependency_path_is_allowed(raw, allowed):
            continue
        issues.append(
            ParserIssue(
                code="docstring.absolute-dependency-not-allowed",
                message=(
                    f"Dependency path/id {raw!r} must start with '.' for a relative "
                    f"path or with one of the allowed absolute roots: "
                    f"{', '.join(allowed) or '<none>'}."
                ),
                section=section,
                severity="warning",
            )
        )
    return tuple(issues)


def _collect_invalid_dependency_marker_syntax(
    lines: Iterable[object],
    section: str,
    *,
    allowed_scopes: tuple[str, ...] | None = None,
) -> tuple[ParserIssue, ...]:
    """Compatibility hook; strict Pseudocode refs are parsed by the Lark step parser."""
    del lines, section, allowed_scopes
    return ()


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
        "CallsFromRepo",
        "InstantiationsFromRepo",
        "Dispatches",
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
    dispatches_section = normalized_declaration_sections[2]
    wraps_section = normalized_declaration_sections[3]
    noninferable_section = normalized_declaration_sections[4]

    for dependency in getattr(spec, "module_calls", ()):
        _add_declared(calls_section, getattr(dependency, "name", ""))
    for dependency in getattr(spec, "module_instantiates", ()):
        _add_declared(instantiates_section, getattr(dependency, "name", ""))
    for dependency in getattr(spec, "dispatches", ()):
        _add_declared(dispatches_section, getattr(dependency, "id", ""))
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
                    code="docstring.pseudocode-ref-unresolved",
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
                    code="docstring.pseudocode-ref-ambiguous",
                    message=(
                        f"Dependency reference {ref!r} is ambiguous across "
                        f"{', '.join(sorted(ref_sources))}; use the typed operation "
                        "syntax and the shortest unique dot-segment suffix."
                    ),
                    section=section,
                    severity="warning",
                )
            )

    for section_name, name in sorted(scoped_refs):
        candidates = [
            declared_name
            for source, declared_name in declared
            if source == section_name and _dot_segment_suffix_matches(name, declared_name)
        ]
        if not candidates:
            sources = declared_by_name.get(name, [])
            options = ", ".join(sources or ["<none>"])
            issues.append(
                ParserIssue(
                    code="docstring.pseudocode-ref-unresolved",
                    message=(
                        f"Dependency reference {section_name + ':' + name!r} does not "
                        f"match any declared dependency under {section_name!r}; "
                        f"known exact sources are {options}."
                    ),
                    section=section,
                    severity="warning",
                )
            )
            continue
        if len(candidates) > 1:
            issues.append(
                ParserIssue(
                    code="docstring.pseudocode-ref-ambiguous",
                    message=(
                        f"Dependency reference {section_name + ':' + name!r} matches "
                        f"multiple declared dependencies: {', '.join(sorted(candidates))}. "
                        "Use the shortest unique dot-segment suffix."
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
    control_kinds = {"if", "while", "for", "else"}
    block_kinds = {"if", "while", "for", "else"}
    valid_kinds = {
        "call",
        "dispatch",
        "instantiate",
        "set",
        "read",
        "write",
        "if",
        "else",
        "while",
        "for",
        "return",
        "raise",
        "continue",
        "break",
    }

    def _has_child(index: int) -> bool:
        return index + 1 < len(steps) and steps[index + 1].indent > steps[index].indent

    def _else_has_matching_if(index: int) -> bool:
        indent = steps[index].indent
        for previous in reversed(steps[:index]):
            if previous.indent > indent:
                continue
            if previous.indent < indent:
                return False
            return previous.kind == "if"
        return False

    def _inside_loop(index: int) -> bool:
        for previous_index in range(index - 1, -1, -1):
            previous = steps[previous_index]
            if previous.kind not in {"for", "while"}:
                continue
            if previous.indent >= steps[index].indent:
                continue
            if all(
                between.indent > previous.indent
                for between in steps[previous_index + 1 : index + 1]
            ):
                return True
        return False

    for index, step in enumerate(steps):
        if step.kind not in valid_kinds:
            issues.append(
                ParserIssue(
                    code="docstring.invalid-pseudocode",
                    message=(
                        f"Invalid strict pseudocode line {step.raw!r}; expected a "
                        "typed call, dispatch, constructor assignment, set/read/write, "
                        "if/else/while/for block, or terminal statement."
                    ),
                    section=section,
                    severity="warning",
                )
            )
            continue
        if step.kind in block_kinds and not _has_child(index):
            issues.append(
                ParserIssue(
                    code="docstring.pseudocode-control-empty",
                    message=f"Pseudocode control line {step.raw!r} has no indented body.",
                    section=section,
                    severity="warning",
                )
            )
        if step.kind == "else" and not _else_has_matching_if(index):
            issues.append(
                ParserIssue(
                    code="docstring.pseudocode-else-unmatched",
                    message="Pseudocode else block does not immediately follow a matching if block.",
                    section=section,
                    severity="warning",
                )
            )
        if step.kind in {"break", "continue"} and not _inside_loop(index):
            issues.append(
                ParserIssue(
                    code="docstring.pseudocode-loop-control-outside-loop",
                    message=f"Pseudocode {step.kind!r} is only valid inside for/while blocks.",
                    section=section,
                    severity="warning",
                )
            )

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


def _collect_forbidden_intent_phrases(
    intent_lines: list[str],
    *,
    forbidden_intent_phrases: tuple[str, ...],
) -> tuple[ParserIssue, ...]:
    """Collect warnings when intent contains prohibited placeholder phrases."""
    return _collect_forbidden_phrases(
        text=" ".join(line.strip() for line in intent_lines if line.strip()),
        forbidden_phrases=forbidden_intent_phrases,
        code="docstring.intent-forbidden",
        section="Intent",
        message_prefix="Intent",
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


def _collect_forbidden_dependency_why_phrases(
    spec,
    *,
    dependency_rules,
) -> tuple[ParserIssue, ...]:
    """Collect placeholder dependency rationale text across graphable sections."""
    forbidden = getattr(dependency_rules, "forbidden_why_phrases", ())
    if not forbidden:
        return ()

    issues: list[ParserIssue] = []
    entries: list[tuple[str, str, str]] = []
    for dependency in getattr(spec, "module_calls", ()):
        entries.append((dependency_rules.calls_section, dependency.name, dependency.why))
    for dependency in getattr(spec, "module_instantiates", ()):
        entries.append((dependency_rules.instantiates_section, dependency.name, dependency.why))
    for dependency in getattr(spec, "dispatches", ()):
        entries.append((dependency_rules.dispatches_section, dependency.id, dependency.why))
    for dependency in getattr(spec, "resources", ()):
        entries.append(("Resources", dependency.id, dependency.why))
    for dependency in getattr(spec, "dataflows", ()):
        entries.append(("Dataflow", f"{dependency.source}->{dependency.target}", dependency.why))

    for section_name, dependency_id, why in entries:
        for issue in _collect_forbidden_phrases(
            text=why,
            forbidden_phrases=forbidden,
            code="docstring.dependency-why-forbidden",
            section=section_name,
            message_prefix=f"Dependency rationale for {dependency_id!r}",
        ):
            issues.append(issue)
    return tuple(issues)


def _iter_dependency_why_entries(spec, *, dependency_rules) -> tuple[tuple[str, str, object], ...]:
    """Return parsed records that expose dependency rationale metadata."""
    entries: list[tuple[str, str, object]] = []
    for dependency in getattr(spec, "module_calls", ()):
        entries.append((dependency_rules.calls_section, dependency.name, dependency))
    for dependency in getattr(spec, "module_instantiates", ()):
        entries.append((dependency_rules.instantiates_section, dependency.name, dependency))
    for dependency in getattr(spec, "dispatches", ()):
        entries.append((dependency_rules.dispatches_section, dependency.id, dependency))
    for dependency in getattr(spec, "resources", ()):
        entries.append(("Resources", dependency.id, dependency))
    for dependency in getattr(spec, "dataflows", ()):
        entries.append(("Dataflow", f"{dependency.source}->{dependency.target}", dependency))
    return tuple(entries)


def _collect_dependency_why_action_issues(spec, *, dependency_rules) -> tuple[ParserIssue, ...]:
    """Collect dependency why action-key syntax diagnostics."""
    config = dependency_rules.dependency_why
    allowed = set(config.actions)
    issues: list[ParserIssue] = []
    for section_name, dependency_id, dependency in _iter_dependency_why_entries(
        spec,
        dependency_rules=dependency_rules,
    ):
        why = str(getattr(dependency, "why", "") or "").strip()
        action = str(getattr(dependency, "why_action", "") or "").strip()
        legacy = bool(getattr(dependency, "why_legacy_string", False))
        action_count = int(getattr(dependency, "why_action_count", 0) or 0)
        if legacy and not config.allow_legacy_string:
            issues.append(
                ParserIssue(
                    code="docstring.dependency-why-action",
                    message=(
                        f"Dependency rationale for {dependency_id!r} is a legacy string. "
                        "Use exactly one graphable action key, for example "
                        "why: {validates: \"Checks that candidate evidence matches reviewed input.\"}. "
                        f"Allowed keys: {', '.join(config.actions)}."
                    ),
                    section=section_name,
                    severity="warning",
                )
            )
            continue
        if legacy:
            continue
        if action_count != 1 or not action:
            issues.append(
                ParserIssue(
                    code="docstring.dependency-why-action",
                    message=(
                        f"Dependency rationale for {dependency_id!r} must use exactly one action key "
                        "under why so graph edges have a typed label."
                    ),
                    section=section_name,
                    severity="warning",
                )
            )
            continue
        if action not in allowed:
            issues.append(
                ParserIssue(
                    code="docstring.dependency-why-action",
                    message=(
                        f"Dependency rationale for {dependency_id!r} uses unknown action {action!r}. "
                        f"Allowed keys: {', '.join(config.actions)}."
                    ),
                    section=section_name,
                    severity="warning",
                )
            )
        if action == "misc" and len(why) < config.misc_min_chars:
            issues.append(
                ParserIssue(
                    code="docstring.dependency-why-action",
                    message=(
                        f"Dependency rationale for {dependency_id!r} uses misc with {len(why)} chars. "
                        f"Use a specific action key when possible, or provide at least {config.misc_min_chars} "
                        "chars explaining the concrete data/control contribution."
                    ),
                    section=section_name,
                    severity="warning",
                )
            )
    return tuple(issues)


def _collect_pseudocode_dataflow_issues(spec, *, dependency_rules) -> tuple[ParserIssue, ...]:
    """Collect mechanical pseudocode dataflow quality issues."""
    config = dependency_rules.pseudocode_quality
    forbidden = {name.strip() for name in config.forbidden_variables if name.strip()}
    if not forbidden and not config.require_assigned_dependency_output_use:
        return ()

    steps = tuple(getattr(spec, "pseudocode_steps", ()))
    dependency_kinds = {"call", "dispatch", "instantiate"}
    issues: list[ParserIssue] = []
    for index, step in enumerate(steps):
        if getattr(step, "kind", "") not in dependency_kinds:
            continue
        output = str(getattr(step, "output", "") or "").strip()
        args = str(getattr(step, "args", "") or "")
        arg_tokens = {
            token
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", args)
            if token in forbidden
        }
        if output in forbidden:
            issues.append(
                ParserIssue(
                    code="docstring.pseudocode-placeholder-variable",
                    message=(
                        f"Pseudocode assigns dependency output to placeholder variable {output!r}. "
                        "Use a semantic output variable so the data edge is graphable."
                    ),
                    section="Pseudocode",
                    severity="warning",
                )
            )
        if arg_tokens:
            issues.append(
                ParserIssue(
                    code="docstring.pseudocode-placeholder-argument",
                    message=(
                        "Pseudocode dependency call uses placeholder argument(s) "
                        f"{', '.join(sorted(arg_tokens))}. Use concrete input names from the algorithm."
                    ),
                    section="Pseudocode",
                    severity="warning",
                )
            )
        if not config.require_assigned_dependency_output_use or not output or output in forbidden:
            continue
        later_text = " ".join(str(getattr(later, "text", "") or "") for later in steps[index + 1 :])
        if not re.search(rf"\b{re.escape(output)}\b", later_text):
            issues.append(
                ParserIssue(
                    code="docstring.pseudocode-output-unused",
                    message=(
                        f"Assigned dependency output {output!r} is never used later in pseudocode. "
                        "Pass it, return it, raise it, write it, or use it in a condition so the data edge is graphable."
                    ),
                    section="Pseudocode",
                    severity="warning",
                )
            )
    return tuple(issues)


def _collect_pseudocode_resource_issues(spec) -> tuple[ParserIssue, ...]:
    """Collect unresolved read/write resource references from strict pseudocode."""
    declared = {
        str(getattr(resource, "id", "")).strip()
        for resource in getattr(spec, "resources", ())
        if str(getattr(resource, "id", "")).strip()
    }
    issues: list[ParserIssue] = []
    for step in getattr(spec, "pseudocode_steps", ()):
        if getattr(step, "kind", "") not in {"read", "write"}:
            continue
        resource_id = str(getattr(step, "resource_id", "")).strip()
        if resource_id in declared:
            continue
        issues.append(
            ParserIssue(
                code="docstring.pseudocode-resource-unresolved",
                message=(
                    f"Pseudocode {step.kind} references undeclared resource "
                    f"{resource_id!r}; declare it in Resources."
                ),
                section="Pseudocode",
                severity="warning",
            )
        )
    return tuple(issues)


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


def check_graph_docstring(
    docstring: str,
    schema_rules: DocstringSchema | None = None,
) -> tuple[ParserIssue, ...]:
    """Validate callable/class docstrings against callable-level format rules."""
    schema_rules = schema_rules or load_docstring_schema()
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
        _collect_forbidden_intent_phrases(
            spec.sections.get("Intent", []),
            forbidden_intent_phrases=schema_rules.callable.forbidden_intent_phrases,
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
            allow_legacy_flat=dependency_rules.allow_legacy_flat,
            require_why=dependency_rules.require_why,
        )
    )
    issues.extend(
        _collect_invalid_module_dependencies(
            spec.sections.get(dependency_rules.instantiates_section, []),
            section=dependency_rules.instantiates_section,
            allow_implicit=dependency_rules.allow_implicit,
            allow_legacy_flat=dependency_rules.allow_legacy_flat,
            require_why=dependency_rules.require_why,
        )
    )
    issues.extend(
        _collect_invalid_dispatch_dependencies(
            spec.sections.get(dependency_rules.dispatches_section, []),
            section=dependency_rules.dispatches_section,
            allow_legacy_flat=dependency_rules.allow_legacy_flat,
            require_why=dependency_rules.require_why,
        )
    )
    issues.extend(
        _collect_invalid_resources(
            spec.sections.get("Resources", []),
            section="Resources",
            require_why=dependency_rules.require_why,
        )
    )
    issues.extend(
        _collect_invalid_dataflows(
            spec.sections.get("Dataflow", []),
            section="Dataflow",
            require_why=dependency_rules.require_why,
        )
    )
    issues.extend(
        _collect_dependency_path_issues(
            (dependency.name for dependency in spec.module_calls),
            section=dependency_rules.calls_section,
            allowed_abs=dependency_rules.allowed_abs,
        )
    )
    issues.extend(
        _collect_dependency_path_issues(
            (dependency.name for dependency in spec.module_instantiates),
            section=dependency_rules.instantiates_section,
            allowed_abs=dependency_rules.allowed_abs,
        )
    )
    issues.extend(
        _collect_dependency_path_issues(
            (dependency.id for dependency in spec.dispatches),
            section=dependency_rules.dispatches_section,
            allowed_abs=dependency_rules.allowed_abs,
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
    issues.extend(_collect_pseudocode_resource_issues(spec))
    issues.extend(
        _collect_forbidden_pseudocode_phrases(
            spec.pseudocode_steps,
            forbidden_pseudocode_phrases=schema_rules.callable.forbidden_pseudocode_phrases,
        )
    )
    issues.extend(
        _collect_forbidden_dependency_why_phrases(
            spec,
            dependency_rules=dependency_rules,
        )
    )
    issues.extend(
        _collect_dependency_why_action_issues(
            spec,
            dependency_rules=dependency_rules,
        )
    )
    issues.extend(
        _collect_pseudocode_dataflow_issues(
            spec,
            dependency_rules=dependency_rules,
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
                    dependency_rules.dispatches_section,
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
                dependency_rules.dispatches_section,
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
