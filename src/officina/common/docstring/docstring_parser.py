#!/usr/bin/env python3
"""Docstring parser for graph-oriented documentation contracts.

Responsibilities:

1. Parse callable-level docstrings into ``FunctionSpec``.
2. Parse module-level ``GraphPipeline`` blocks into ``PipelineSpec``.
3. Parse ownership and wrapper declarations.
4. Parse docstrings from AST walk of a module.

This parser uses a strict Lark grammar for syntax; behavioral requirements and
cross-field constraints remain in :mod:`docstring_validation`.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Iterable

from .docstring_schema import (
    CallableDocstringSchema,
    DocstringSchema,
    ModuleDependencyConfig,
    ModuleDocstringSchema,
    ModuleOwnershipConfig,
    OwnershipConfig,
    PipelineDocstringSchema,
    resolve_docstring_schema_path,
)

try:
    from lark import Lark
except ImportError:  # pragma: no cover - optional dependency
    Lark = None


# Core callable sections that intentionally drive graph generation.
INFO_SECTIONS = {
    "Parameters",
    "Returns",
    "Raises",
    "Yields",
    "Warns",
    "Warnings",
    "Notes",
    "Examples",
    "See Also",
    "References",
}
OWNABLE_SECTION = "Ownable"

# Sections that are always accepted as documentation structure independent of policy
# additions. Pipeline and module headers are included so custom sections only need
# to be registered in the policy file to be recognized.
# Additional callable sections accepted across schema and migration variants.
STATIC_RECOGNIZED_SECTIONS = {
    *INFO_SECTIONS,
    "GraphPipeline",
    "Description",
    "Graph",
    "Phases",
    "PhaseMembers",
    OWNABLE_SECTION,
}

def _policy_sections() -> frozenset[str]:
    """Read policy-driven section names without hardcoded future assumptions."""
    rules = load_docstring_schema()
    return frozenset(
        rules.callable.section_names()
        + rules.module_dependencies.section_names()
        + rules.pipeline.section_names()
        + rules.module.section_names()
        + tuple(STATIC_RECOGNIZED_SECTIONS)
    )

EdgeList = list[tuple[str, str]]


_DOCSTRING_SYNTAX_FILES = (
    "docstring.standard.lark",
)
_PSEUDOCODE_CONTROL_KEYWORDS = (
    "if",
    "elif",
    "else",
    "while",
    "for",
    "for each",
    "loop",
    "try",
    "except",
    "finally",
    "with",
)
_WRAP_FIELDS = ("preprocess", "postprocess", "fixed_arguments")

_DOCSTRING_GRAMMAR_TEXT: str | None = None


def _resolve_docstring_syntax_path(path: str | Path | None = None) -> Path | None:
    """Resolve the syntax file used by Lark-backed docstring parsing."""
    schema_path = resolve_docstring_schema_path(path)
    if schema_path is None:
        return None

    start = schema_path.parent
    for base in (start, *start.parents):
        for filename in _DOCSTRING_SYNTAX_FILES:
            candidate = base / filename
            if candidate.exists():
                return candidate

    return None


def _load_docstring_grammar(path: str | Path | None = None) -> str | None:
    syntax_path = _resolve_docstring_syntax_path(path)
    if syntax_path is None:
        return None
    try:
        return syntax_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _build_parser(start_rule: str, grammar: str | None = None) -> Lark | None:
    """Create a strict parser object for one grammar, or return ``None``."""
    if Lark is None:
        return None

    _grammar = grammar if grammar is not None else _DOCSTRING_GRAMMAR_TEXT
    if _grammar is None:
        return None
    try:
        return Lark(_grammar, parser="lalr", start=start_rule)
    except Exception:
        return None


_DOCSTRING_GRAMMAR_TEXT = _load_docstring_grammar()
_EDGE_PARSER = _build_parser("edge")
_WRAP_PARSER = _build_parser("wraps")
_DEPENDENCY_PARSER = _build_parser("module_dependency")


def _ensure_lark_parser() -> None:
    """Require the strict parser runtime."""
    if Lark is None:
        raise RuntimeError(
            "Lark is required for docstring parsing and is not installed. "
            "Install with `pip install lark`."
        )


def _token_values(tree, token_type: str) -> list[str]:
    """Collect terminal values for a token type from a Lark parse tree."""
    if tree is None:
        return []
    from lark.lexer import Token

    return [
        str(node)
        for node in tree.scan_values(lambda value: isinstance(value, Token))
        if node.type == token_type
    ]


@dataclass
class PipelineSpec:
    """Parsed module-level pipeline metadata.

    Parameters
    ----------
    phases : list[str]
        Ordered list of pipeline phase names.
    phase_edges : list[tuple[str, str]]
        Directed phase-level edges.
    phase_members : dict[str, list[str]]
        Mapping from phase name to callable IDs.
    noninferable_calls : list[tuple[str, str]]
        Non-inferable callable edges.
    """

    phases: list[str] = field(default_factory=list)
    phase_edges: list[tuple[str, str]] = field(default_factory=list)
    phase_members: dict[str, list[str]] = field(default_factory=dict)
    noninferable_calls: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class WrapSpec:
    """Parsed wrapper relationship from a ``Wraps`` section.

    Parameters
    ----------
    target : str
        Callable this function wraps.
    preprocess : str
        Operations run before delegating to the wrapped callable.
    postprocess : str
        Operations run after delegation.
    fixed_arguments : str
        Fixed arguments this wrapper enforces while delegating.
    """

    target: str
    preprocess: str = ""
    postprocess: str = ""
    fixed_arguments: str = ""


@dataclass(frozen=True)
class ModuleDependencyRef:
    """Reference to a module dependency with optional implicitness."""

    name: str
    why: str = ""
    implicit: bool = False


@dataclass(frozen=True)
class PseudocodeStep:
    """Parsed unit from a callable ``Pseudocode`` section."""

    indent: int
    kind: str
    text: str
    raw: str




def _normalize_wrap_key(raw_key: str) -> str | None:
    """Normalize one wraps detail key to a supported field name."""
    normalized = raw_key.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"pre", "preprocess"}:
        return "preprocess"
    if normalized in {"post", "postprocess"}:
        return "postprocess"
    if normalized in {"fixed", "fixed_argument", "fixed_arguments", "fixed_args"}:
        return "fixed_arguments"
    return None


@dataclass
class FunctionSpec:
    """Parsed callable-level docstring metadata.

    Parameters
    ----------
    role : str | None
        Role hint extracted from ``Role`` text.
    phase : str | None
        Declared ``Phase`` for this callable.
    noninferable_calls : list[tuple[str, str]]
        Explicit ``NonInferableCalls`` edges.
    wraps : list[WrapSpec]
        Parsed ``Wraps`` metadata describing wrapped targets and rationale.
    owns : list[str]
        Declared ownership tags from the ``Owns`` section.
    module_calls : list[ModuleDependencyRef]
        Call targets referenced in ``CallsFromModule``.
    module_instantiates : list[ModuleDependencyRef]
        Module object constructions referenced in ``InstantiationsFromModule``.
    pseudocode_steps : list[PseudocodeStep]
        Parsed compact execution steps from the configurable pseudocode section.
    summary : str
        Compact free-text summary extracted from top-level lines.
    signature : str
        Rendered function signature for rendering payloads.
    sections : dict[str, list[str]]
        Parsed nonstandard sections retained for metadata consumers.
    """

    role: str | None = None
    phase: str | None = None
    noninferable_calls: list[tuple[str, str]] = field(default_factory=list)
    wraps: list[WrapSpec] = field(default_factory=list)
    owns: list[str] = field(default_factory=list)
    module_calls: list[ModuleDependencyRef] = field(default_factory=list)
    module_instantiates: list[ModuleDependencyRef] = field(default_factory=list)
    pseudocode_steps: list[PseudocodeStep] = field(default_factory=list)
    summary: str = ""
    signature: str = ""
    sections: dict[str, list[str]] = field(default_factory=dict)

    def module_call_names(self, *, include_implicit: bool = True) -> list[str]:
        """Return module-call dependency names as plain strings."""
        return [
            dependency.name
            for dependency in self.module_calls
            if include_implicit or not dependency.implicit
        ]

    def module_instantiates_names(self, *, include_implicit: bool = True) -> list[str]:
        """Return module-instantiation dependency names as plain strings."""
        return [
            dependency.name
            for dependency in self.module_instantiates
            if include_implicit or not dependency.implicit
        ]


@dataclass(frozen=True)
class ParserIssue:
    """Single parser validation issue."""

    code: str
    message: str
    section: str | None = None
    severity: str = "warning"


def _parse_with_lark(parser: "Lark | None", line: str):
    """Parse one docstring line with a Lark parser."""
    if not line.strip():
        return None
    _ensure_lark_parser()
    if parser is None:
        return None
    try:
        return parser.parse(line)
    except Exception:
        return None


def _parse_dependency_implicit_marker(
    marker_token: str | None,
) -> bool:
    """Return parsed implicit flag from a marker token."""
    if marker_token is None:
        return False
    return marker_token.strip().lower() == "implicit"


def _resolve_wrap_field_key(
    raw_key: str,
    *,
    allowed_fields: tuple[str, ...],
) -> str | None:
    """Normalize wrap key and check it is allowed by policy."""
    normalized = _normalize_wrap_key(raw_key)
    if normalized is None:
        return None
    return normalized if normalized in allowed_fields else None


def _extract_wrap_fields(tree, *, allowed_fields: tuple[str, ...]) -> dict[str, str] | None:
    """Extract wrap fields from one Lark parse tree."""
    from lark.lexer import Token

    entries: dict[str, str] = {}
    current_key: str | None = None
    seen: set[str] = set()

    for node in tree.scan_values(lambda value: isinstance(value, Token)):
        if node.type == "wrap_key":
            key = _resolve_wrap_field_key(str(node), allowed_fields=allowed_fields)
            if key is None or key in seen:
                return None
            seen.add(key)
            current_key = key
            continue
        if node.type == "wrap_value":
            if current_key is None:
                return None
            entries[current_key] = str(node).strip()
            current_key = None

    return entries


def _extract_dependency_parts(
    source: str,
    tree,
) -> tuple[str, str, str | None]:
    """Extract dependency name, rationale, and marker from a dependency tree."""
    from lark.lexer import Token

    name = ""
    why = ""
    marker: str | None = None

    for node in tree.scan_values(lambda value: isinstance(value, Token)):
        if node.type in {"name", "IDENT"}:
            name = str(node)
        elif node.type == "DEPS_REASON":
            why = str(node).strip()
        elif node.type == "IMPLICIT":
            marker = str(node)

    if marker is None and re.search(r"\[\s*implicit\s*\]", source, flags=re.IGNORECASE):
        marker = "implicit"

    return name, why, marker


_PSEUDOCODE_PREFIX_RE = re.compile(r"^(\([^)]+\)|\d+[.)]|[a-zA-Z][.)])\s+")


def parse_ownership_reference(
    raw: str,
) -> tuple[str | None, str] | None:
    """Parse one ``Owns`` entry into ``(module_hint, owner_id)``.

    Returns ``(None, owner)`` for local references and ``(module, owner)``
    when a module hint is present.
    """
    cleaned = _clean_item(raw)
    if not cleaned:
        return None

    if ":" in cleaned:
        module_hint, owner_id = [part.strip() for part in cleaned.split(":", 1)]
        if not owner_id:
            return None
        if not module_hint:
            return (None, owner_id)
        return (module_hint, owner_id)

    return (None, cleaned)


def _is_header_underline(line: str) -> bool:
    """Return ``True`` when a line matches NumPy-style underline syntax."""
    value = line.strip()
    return bool(value) and len(set(value)) == 1 and value[0] in {"-", "="}


def _section_header(
    lines: list[str],
    index: int,
    section_names: frozenset[str] | None = None,
) -> str | None:
    """Detect a valid section header line.

    Accepts both explicit header lines and header+underline styles.
    """
    if section_names is None:
        section_names = _policy_sections()
    text = lines[index].strip()
    if not text:
        return None
    next_index = index + 1
    has_underline = next_index < len(lines) and _is_header_underline(lines[next_index])
    if text in section_names and has_underline:
        return text
    if text in section_names:
        return text
    return None


def _clean_item(value: str) -> str:
    """Trim a bullet/list entry from docstring blocks."""
    return value.strip().lstrip("-").strip()


def _normalize_pseudocode_prefix(raw: str) -> str:
    """Strip supported list or numbered prefixes from one pseudocode line."""
    text = raw.strip()
    if not text:
        return text

    for token in ("- ", "+ ", "* ", "• "):
        if text.startswith(token):
            text = text[len(token):].strip()
            break

    if _PSEUDOCODE_PREFIX_RE.match(text):
        text = re.sub(_PSEUDOCODE_PREFIX_RE, "", text, count=1)

    return text


def _pseudocode_kind(text: str, control_keywords: tuple[str, ...]) -> str:
    """Infer pseudocode step kind from textual prefix."""
    lowered = text.lower().strip()
    if not lowered:
        return "step"
    if not control_keywords:
        return "step"
    for keyword in sorted(control_keywords, key=len, reverse=True):
        if not lowered.startswith(keyword):
            continue
        next_char = lowered[len(keyword) : len(keyword) + 1]
        if lowered == keyword or next_char in {"", " ", ":", "("}:
            return keyword.replace(" ", "_")
    return "step"


def _parse_pseudocode_section(
    lines: Iterable[str],
) -> list[PseudocodeStep]:
    """Parse compact pseudocode entries into typed metadata."""
    steps: list[PseudocodeStep] = []
    for raw in lines:
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        text = _normalize_pseudocode_prefix(raw)
        if not text:
            continue
        kind = _pseudocode_kind(text, _PSEUDOCODE_CONTROL_KEYWORDS)
        steps.append(
            PseudocodeStep(
                indent=indent,
                kind=kind,
                text=text,
                raw=raw.strip(),
            )
        )
    return steps


def _parse_edge(line: str) -> tuple[str, str] | None:
    """Parse ``source -> target`` edge syntax."""
    match = _parse_with_lark(_EDGE_PARSER, line)
    if match is None:
        return None
    names = _token_values(match, "name")
    if len(names) != 2:
        return None
    return names[0], names[1]


def _parse_wrap_entry(line: str) -> WrapSpec | None:
    """Parse one wraps directive line into structured metadata."""
    allowed_fields = _WRAP_FIELDS
    cleaned = _clean_item(line)
    if not cleaned:
        return None
    parse_tree = _parse_with_lark(_WRAP_PARSER, cleaned)
    if parse_tree is None:
        return None

    names = _token_values(parse_tree, "name")
    if not names:
        return None
    target = names[0]

    fields = _extract_wrap_fields(parse_tree, allowed_fields=allowed_fields)
    if fields is None:
        return None

    if not all(fields.get(name, "").strip() for name in _WRAP_FIELDS):
        return None

    return WrapSpec(
        target=target,
        preprocess=fields.get("preprocess", ""),
        postprocess=fields.get("postprocess", ""),
        fixed_arguments=fields.get("fixed_arguments", ""),
    )


def _parse_wraps(lines: Iterable[str]) -> tuple[list[WrapSpec], list[str]]:
    """Parse ``Wraps`` section lines into ``WrapSpec`` entries."""
    wraps: list[WrapSpec] = []
    invalid: list[str] = []
    for raw in lines:
        parsed = _parse_wrap_entry(raw)
        if parsed is None:
            raw_text = raw.strip()
            if raw_text:
                invalid.append(raw_text)
            continue
        wraps.append(parsed)
    return wraps, invalid


def _parse_module_dependency_ref(
    line: str,
    *,
    allow_implicit: bool = True,
) -> ModuleDependencyRef | None:
    """Parse a module dependency reference entry."""
    cleaned = _clean_item(line)
    if not cleaned:
        return None
    normalized = re.sub(r"\s*\[\s*implicit\s*\]\s*", "[implicit]", cleaned)
    normalized = re.sub(r"\s*->\s*", "->", normalized)
    parse_tree = _parse_with_lark(_DEPENDENCY_PARSER, normalized)
    if parse_tree is None:
        return None

    name, why, marker = _extract_dependency_parts(cleaned, parse_tree)
    if not name:
        return None

    if marker is not None:
        if not allow_implicit:
            return None
        is_implicit = _parse_dependency_implicit_marker(marker)
    else:
        is_implicit = False

    if not why:
        return None

    return ModuleDependencyRef(
        name=name,
        why=why,
        implicit=is_implicit,
    )


def parse_pipeline(docstring: str) -> PipelineSpec:
    """Parse the module-level ``GraphPipeline`` block.

    Parameters
    ----------
    docstring : str
    Module docstring.

    Returns
    -------
    PipelineSpec
        Parsed pipeline sections and relations.

    Notes
    -----
    Unknown lines are ignored so format changes can be introduced without
    immediately hard-failing parsing behavior.
    """
    schema_rules = load_docstring_schema()
    spec = PipelineSpec()
    lines = dedent(docstring).splitlines()
    section_names = frozenset(schema_rules.pipeline.section_names())
    edge_sections = frozenset(
        section
        for section in section_names
        if section.lower().endswith("edges")
        or section.lower().endswith("calls")
    )
    in_pipeline = False
    section = None
    phase = None

    for raw in lines:
        stripped = raw.rstrip()
        if not stripped or set(stripped) == {"-"}:
            continue
        text = stripped.strip()

        if text == "GraphPipeline":
            in_pipeline = True
            section = None
            phase = None
            continue

        if not in_pipeline:
            continue

        if text in section_names:
            section = text
            if section == "PhaseMembers":
                phase = None
            continue

        if text in {"Name", "Description", "Graph"}:
            if text == "Graph":
                in_pipeline = False
            section = None
            continue

        if section == "Phases":
            item = _clean_item(text)
            if item:
                spec.phases.append(item)
            continue

        if section in edge_sections and section not in {"NonInferableCalls"}:
            edge = _parse_edge(text)
            if edge is not None:
                spec.phase_edges.append(edge)
            continue

        if section == "PhaseMembers":
            if text.endswith(":"):
                phase = _clean_item(text[:-1])
                spec.phase_members.setdefault(phase, [])
                continue
            if phase is not None:
                member = _clean_item(text)
                if member:
                    spec.phase_members[phase].append(member)
            continue

        if section == "NonInferableCalls":
            edge = _parse_edge(text)
            if edge is not None:
                spec.noninferable_calls.append(edge)
            continue

    return spec


def parse_graph_block(docstring: str, *, section_names: frozenset[str] | None = None) -> FunctionSpec:
    """Parse callable-level graph metadata from a callable docstring.

    Unknown sections are preserved in ``sections`` so schema growth remains
    discoverable without parser edits.
    """
    spec = FunctionSpec()
    lines = dedent(docstring).splitlines()
    if not lines:
        return spec

    if section_names is None:
        section_names = _policy_sections()
    schema_rules = load_docstring_schema()
    pseudocode_rules = schema_rules.callable.pseudocode
    module_dependency_rules = schema_rules.module_dependencies

    section: str | None = None
    section_lines: list[str] = []
    sections: dict[str, list[str]] = {}
    summary_lines: list[str] = []
    skip_next = False

    for index, raw in enumerate(lines):
        if skip_next:
            skip_next = False
            continue

        section_name = _section_header(lines, index, section_names=section_names)
        if section_name is not None:
            if section is not None and section_lines:
                sections[section] = section_lines
            section = section_name
            section_lines = []
            if section is not None:
                sections.setdefault(section, [])
            if index + 1 < len(lines) and _is_header_underline(lines[index + 1]):
                skip_next = True
            continue

        if section is None:
            if raw.strip():
                summary_lines.append(raw.strip())
            continue

        section_lines.append(raw)

    if section is not None and section_lines:
        sections[section] = section_lines

    for section_name, lines_for_section in sections.items():
        cleaned = [line.rstrip() for line in lines_for_section if line.strip()]
        if cleaned:
            spec.sections[section_name] = cleaned

    if summary_lines:
        spec.summary = " ".join(line.strip() for line in summary_lines if line.strip())

    role_lines = spec.sections.get("Role", [])
    for line in role_lines:
        cleaned = _clean_item(line)
        if cleaned:
            spec.role = f"{spec.role} {cleaned}".strip() if spec.role else cleaned

    if "Phase" in spec.sections:
        for item in spec.sections["Phase"]:
            phase = _clean_item(item)
            if phase:
                spec.phase = phase
                break

    for edge_text in spec.sections.get("NonInferableCalls", []):
        edge = _parse_edge(edge_text)
        if edge is not None:
            spec.noninferable_calls.append(edge)

    spec.wraps, _ = _parse_wraps(spec.sections.get("Wraps", []))
    pseudocode_lines = []
    if pseudocode_rules.section:
        pseudocode_lines.extend(spec.sections.get(pseudocode_rules.section, []))
    spec.pseudocode_steps = _parse_pseudocode_section(
        pseudocode_lines,
    )
    for dependency in spec.sections.get(module_dependency_rules.calls_section, []):
        parsed = _parse_module_dependency_ref(
            dependency,
            allow_implicit=module_dependency_rules.allow_implicit,
        )
        if parsed is not None:
            spec.module_calls.append(parsed)
    for dependency in spec.sections.get(module_dependency_rules.instantiates_section, []):
        parsed = _parse_module_dependency_ref(
            dependency,
            allow_implicit=module_dependency_rules.allow_implicit,
        )
        if parsed is not None:
            spec.module_instantiates.append(parsed)

    for owner in spec.sections.get("Owns", []):
        cleaned = _clean_item(owner)
        if cleaned:
            spec.owns.append(cleaned)

    if not spec.role:
        spec.role = None

    return spec


def validate_edge_expression(edge_text: str) -> bool:
    """Return ``True`` when text matches ``source -> target`` syntax."""
    return _parse_edge(edge_text) is not None


def parse_ownable_registry(
    docstring: str,
    *,
    section: str = OWNABLE_SECTION,
) -> dict[str, str]:
    """Parse module-level ``Ownable`` entries from a module docstring.

    Returns
    -------
    dict[str, str]
        Mapping ``owner_id -> responsibility``.
    """
    spec = parse_graph_block(docstring)
    registry: dict[str, str] = {}
    for raw in spec.sections.get(section, []):
        cleaned = _clean_item(raw)
        if not cleaned:
            continue
        owner_id, sep, responsibility = cleaned.partition(":")
        owner_id = owner_id.strip()
        if not owner_id:
            continue
        registry[owner_id] = responsibility.strip() if sep else ""
    return registry


def validate_pipeline_docstring(docstring: str) -> tuple[ParserIssue, ...]:
    """Pipeline validation delegated to validator module."""
    from .docstring_validation import validate_pipeline_docstring as _validate_pipeline_docstring

    return _validate_pipeline_docstring(docstring)


def check_graph_docstring(docstring: str) -> tuple[ParserIssue, ...]:
    """Callable docstring checks delegated to validator module."""
    from .docstring_validation import check_graph_docstring as _check_graph_docstring

    return _check_graph_docstring(docstring)


def check_pipeline_docstring(docstring: str) -> tuple[ParserIssue, ...]:
    """Pipeline validation delegated to validator module."""
    from .docstring_validation import check_pipeline_docstring as _check_pipeline_docstring

    return _check_pipeline_docstring(docstring)


def check(docstring: str, kind: str = "callable") -> tuple[ParserIssue, ...]:
    """Generic validation entry point for docstrings.

    Parameters
    ----------
    kind : {'callable', 'pipeline'}
        Selects which validation mode to run.
    """
    from .docstring_validation import check as _check
    return _check(docstring, kind=kind)


def load_docstring_schema(path: str | Path | None = None) -> DocstringSchema:
    """Load and parse standard yaml policy."""
    from .docstring_schema import load_docstring_schema as _load_docstring_schema

    return _load_docstring_schema(path)


def _render_arg_default(expr: ast.expr | None) -> str | None:
    """Render an AST expression for display in signatures.

    Falls back to ``None`` when unparsing fails.
    """
    if expr is None:
        return None
    try:
        return ast.unparse(expr)
    except Exception:
        return None


def _format_arg(arg: ast.arg) -> str:
    """Render an AST argument name with optional annotation."""
    text = arg.arg
    if arg.annotation is not None:
        annotation = _render_arg_default(arg.annotation)
        if annotation is not None:
            text = f"{text}: {annotation}"
    return text


def _format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Render a human-readable function signature from AST metadata."""
    args = node.args
    parts: list[str] = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults = list(args.defaults)
    pad = max(0, len(positional) - len(defaults))
    for idx, arg in enumerate(positional):
        text = _format_arg(arg)
        default = defaults[idx - pad] if idx >= pad else None
        if default is not None:
            rendered_default = _render_arg_default(default) or "..."
            text = f"{text}={rendered_default}"
        parts.append(text)

    if args.vararg is not None:
        parts.append(f"*{_format_arg(args.vararg)}")

    for idx, arg in enumerate(args.kwonlyargs):
        text = _format_arg(arg)
        default = args.kw_defaults[idx]
        if default is not None:
            rendered_default = _render_arg_default(default) or "..."
            text = f"{text}={rendered_default}"
        parts.append(text)

    if args.kwarg is not None:
        parts.append(f"**{_format_arg(args.kwarg)}")

    signature = f"{node.name}(" + ", ".join(parts) + ")"
    if node.returns is not None:
        returns = _render_arg_default(node.returns)
        if returns is not None:
            signature += f" -> {returns}"
    return signature


def parse_function_graphs(tree: ast.AST) -> dict[str, FunctionSpec]:
    """Parse graph metadata for all callables in a module AST.

    Keys are callable names; methods use ``Class.method`` notation.
    """
    specs: dict[str, FunctionSpec] = {}

    def parse_callable(node: ast.AST, name: str, is_method: bool = False) -> None:
        doc = ast.get_docstring(node)
        if not doc:
            return
        spec = parse_graph_block(doc)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spec.signature = _format_signature(node)
        else:
            spec.signature = name if is_method else f"{name}()"
        specs[name] = spec

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parse_callable(node, node.name)
        elif isinstance(node, ast.ClassDef):
            parse_callable(node, node.name)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    parse_callable(child, f"{node.name}.{child.name}", is_method=True)

    return specs


__all__ = [
    "FunctionSpec",
    "PipelineSpec",
    "ParserIssue",
    "parse_function_graphs",
    "parse_graph_block",
    "parse_pipeline",
    "WrapSpec",
    "check",
    "check_graph_docstring",
    "check_pipeline_docstring",
    "load_docstring_schema",
    "resolve_docstring_schema_path",
    "OwnershipConfig",
    "ModuleDependencyRef",
    "ModuleOwnershipConfig",
    "DocstringSchema",
    "CallableDocstringSchema",
    "PipelineDocstringSchema",
    "ModuleDocstringSchema",
    "validate_edge_expression",
    "validate_pipeline_docstring",
    "parse_ownership_reference",
    "parse_ownable_registry",
]
