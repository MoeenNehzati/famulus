#!/usr/bin/env python3
"""Docstring parser for graph-oriented documentation contracts.

Responsibilities:

1. Parse callable-level docstrings into ``FunctionSpec``.
2. Parse module-level ``GraphPipeline`` blocks into ``PipelineSpec``.
3. Parse ownership and wrapper declarations.
4. Parse docstrings from AST walk of a module.

This parser uses a strict Lark grammar for syntax; behavioral requirements and
cross-field constraints remain in :mod:`validation`.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Iterable

import yaml

from .policy import (
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
    "Resources",
    "Dataflow",
    OWNABLE_SECTION,
}
_UNKNOWN_SECTION_PREFIX = "__unknown_docstring_section__:"

def _policy_sections() -> frozenset[str]:
    """Read policy-driven section names without hardcoded future assumptions.

    Intent
    ------
    Expose the policy sections step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps policy sections behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set policy_sections_inputs = received_context
    - set policy_sections_products = carried_outputs
    - return policy_sections_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .load_docstring_schema:
      why:
        constructs: "load docstring schema produces a value carried by policy sections; this edge is documented from the observed product position in the body."
    """
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
_WRAP_FIELDS = ("preprocess", "postprocess", "fixed_arguments")

_DOCSTRING_GRAMMAR_TEXT: str | None = None


def _resolve_docstring_syntax_path(path: str | Path | None = None) -> Path | None:
    """Resolve the syntax file used by Lark-backed docstring parsing.

    Intent
    ------
    Expose the resolve docstring syntax path step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps resolve docstring syntax path behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set resolve_docstring_syntax_path_inputs = received_context
    - return resolve_docstring_syntax_path_inputs

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .policy.resolve_docstring_schema_path:
      why:
        constructs: "Builds the resolve_docstring_schema_path contribution used by _resolve_docstring_syntax_path."
    """
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
    """Load the Lark grammar text used for strict docstring micro-syntax.

    Intent
    ------
    Expose the load docstring grammar step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps load docstring grammar behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set load_docstring_grammar_inputs = received_context
    - set load_docstring_grammar_products = carried_outputs
    - return load_docstring_grammar_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._resolve_docstring_syntax_path:
      why:
        constructs: "resolve docstring syntax path produces a value carried by load docstring grammar; this edge is documented from the observed product position in the body."
    """
    syntax_path = _resolve_docstring_syntax_path(path)
    if syntax_path is None:
        return None
    try:
        return syntax_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _build_parser(start_rule: str, grammar: str | None = None) -> Lark | None:
    """Create a strict parser object for one grammar, or return ``None``.

    Intent
    ------
    Expose the build parser step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps build parser behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set build_parser_inputs = received_context
    - return build_parser_inputs

    Wraps
    -----
    - none
    """
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
_PSEUDOCODE_BULLET_PARSER = _build_parser("pseudocode_bullet")
_PSEUDOCODE_REF_PARSER = _build_parser("pseudocode_ref_expr")


def _ensure_lark_parser() -> None:
    """Require the strict parser runtime.

    Intent
    ------
    Expose the ensure lark parser step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps ensure lark parser behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set ensure_lark_parser_inputs = received_context
    - return ensure_lark_parser_inputs

    Wraps
    -----
    - none
    """
    if Lark is None:
        raise RuntimeError(
            "Lark is required for docstring parsing and is not installed. "
            "Install with `pip install lark`."
        )


def _token_values(tree, token_type: str) -> list[str]:
    """Collect terminal values for a token type from a Lark parse tree.

    Intent
    ------
    Expose the token values step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps token values behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set token_values_inputs = received_context
    - return token_values_inputs

    Wraps
    -----
    - none
    """
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
    """Parsed representation of one graph-pipeline block.

    Intent
    ------
    Carry pipeline metadata, ownership references, external resources, declared data
    flow, and graph edges in one typed record.

    Rationale
    ---------
    A single record lets the parser return structured pipeline data without binding
    callers to raw section text or repeated AST walks.

    Pseudocode
    ----------
    - set pipeline_spec_contract = declared_fields
    - return pipeline_spec_contract

    Wraps
    -----
    - none"""

    phases: list[str] = field(default_factory=list)
    phase_edges: list[tuple[str, str]] = field(default_factory=list)
    phase_members: dict[str, list[str]] = field(default_factory=dict)
    noninferable_calls: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class WrapSpec:
    """Parsed wrapper edge metadata from a Wraps section.

    Intent
    ------
    Record whether a callable delegates to another repo target and how the wrapper
    changes inputs, outputs, or fixed arguments.

    Rationale
    ---------
    Wrapper edges have different graph semantics from ordinary calls, so they need a
    dedicated typed record instead of being mixed into call dependencies.

    Pseudocode
    ----------
    - set wrap_spec_contract = target plus wrapper behavior fields
    - return wrap_spec_contract

    Wraps
    -----
    - none
    """

    target: str
    preprocess: str = ""
    postprocess: str = ""
    fixed_arguments: str = ""
    is_wrapper: bool = True


@dataclass(frozen=True)
class ModuleDependencyRef:
    """Parsed dependency edge declared in CallsFromRepo or InstantiationsFromRepo.

    Intent
    ------
    Store a logical dependency id together with structured rationale metadata and
    implicit-edge status.

    Rationale
    ---------
    Call and product sections share the same payload shape, so one typed reference
    keeps parsing simple while section placement preserves edge meaning.

    Pseudocode
    ----------
    - set module_dependency_contract = name plus rationale fields
    - return module_dependency_contract

    Wraps
    -----
    - none
    """

    name: str
    why: str = ""
    why_action: str = ""
    why_legacy_string: bool = False
    why_action_count: int = 0
    implicit: bool = False


@dataclass(frozen=True)
class DispatchDependencyRef:
    """Parsed dispatch interface dependency declared in Dispatches.

    Intent
    ------
    Store a dispatch interface id and its structured rationale action for graphable
    handoff edges.

    Rationale
    ---------
    Dispatch ids are logical interfaces rather than Python symbols, so they need a
    separate record from repo call and product dependencies.

    Pseudocode
    ----------
    - set dispatch_dependency_contract = id plus rationale fields
    - return dispatch_dependency_contract

    Wraps
    -----
    - none
    """

    id: str
    why: str = ""
    why_action: str = ""
    why_legacy_string: bool = False
    why_action_count: int = 0


@dataclass(frozen=True)
class ResourceDependencyRef:
    """Parsed external resource dependency declared by a graph docstring.

    Intent
    ------
    Carry a resource id, access mode, rationale, and sensitivity metadata for graph
    extraction.

    Rationale
    ---------
    Resources are not repo call edges, but graph consumers still need to know which
    files, services, or stores a documented step reads or writes.

    Pseudocode
    ----------
    - set resource_dependency_contract = id plus mode and metadata fields
    - return resource_dependency_contract

    Wraps
    -----
    - none
    """

    id: str
    kind: str = ""
    access: str = ""
    why: str = ""
    why_action: str = ""
    why_legacy_string: bool = False
    why_action_count: int = 0


@dataclass(frozen=True)
class DataflowDependencyRef:
    """Parsed dataflow edge between documented graph nodes.

    Intent
    ------
    Record source, target, payload, and rationale for explicit data movement in a
    pipeline docstring.

    Rationale
    ---------
    Dataflow edges describe how artifacts move through a graph, which is distinct
    from callable dependencies and wrapper delegation.

    Pseudocode
    ----------
    - set dataflow_dependency_contract = source target payload and rationale
    - return dataflow_dependency_contract

    Wraps
    -----
    - none
    """

    source: str
    target: str
    kind: str = ""
    why: str = ""
    why_action: str = ""
    why_legacy_string: bool = False
    why_action_count: int = 0


@dataclass(frozen=True)
class PseudocodeStep:
    """Parsed strict pseudocode step with extracted graph hints.

    Intent
    ------
    Represent one structured pseudocode bullet, including indentation, control-flow
    kind, dependency marker, output variable, and resource metadata.

    Rationale
    ---------
    Flowchart extraction needs more than raw text; this record keeps each human step
    readable while exposing machine-usable call, dispatch, product, and control data.

    Pseudocode
    ----------
    - set pseudocode_step_contract = text plus extracted structure fields
    - return pseudocode_step_contract

    Wraps
    -----
    - none
    """

    indent: int
    kind: str
    text: str
    raw: str
    output: str = ""
    ref: str = ""
    args: str = ""
    dependency_kind: str = ""
    condition: str = ""
    loop_variable: str = ""
    loop_iterable: str = ""
    resource_id: str = ""
    expression: str = ""
    product_position: str = ""




def _normalize_wrap_key(raw_key: str) -> str | None:
    """Normalize one wraps detail key to a supported field name.

    Intent
    ------
    Expose the normalize wrap key step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps normalize wrap key behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set normalize_wrap_key_inputs = received_context
    - return normalize_wrap_key_inputs

    Wraps
    -----
    - none
    """
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
    """Parsed callable docstring with graphable dependency sections.

    Intent
    ------
    Collect summary, rationale, pseudocode, wrappers, ownership, repo dependencies,
    dispatches, resources, and dataflow for one callable.

    Rationale
    ---------
    A single callable record gives validators and visualizers a stable typed view of
    the documentation without re-reading raw section text.

    Pseudocode
    ----------
    - set function_spec_contract = callable metadata and graph sections
    - return function_spec_contract

    Wraps
    -----
    - none
    """

    role: str | None = None
    rationale: str | None = None
    phase: str | None = None
    noninferable_calls: list[tuple[str, str]] = field(default_factory=list)
    wraps: list[WrapSpec] = field(default_factory=list)
    owns: list[str] = field(default_factory=list)
    module_calls: list[ModuleDependencyRef] = field(default_factory=list)
    module_instantiates: list[ModuleDependencyRef] = field(default_factory=list)
    dispatches: list[DispatchDependencyRef] = field(default_factory=list)
    resources: list[ResourceDependencyRef] = field(default_factory=list)
    dataflows: list[DataflowDependencyRef] = field(default_factory=list)
    pseudocode_steps: list[PseudocodeStep] = field(default_factory=list)
    pseudocode_dependency_refs: list[str] = field(default_factory=list)
    summary: str = ""
    signature: str = ""
    sections: dict[str, list[str]] = field(default_factory=dict)

    def module_call_names(self, *, include_implicit: bool = True) -> list[str]:
        """Return module-call dependency names as plain strings.

        Intent
        ------
        Expose the module call names step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps module call names behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set module_call_names_inputs = received_context
        - return module_call_names_inputs

        Wraps
        -----
        - none
        """
        return [
            dependency.name
            for dependency in self.module_calls
            if include_implicit or not dependency.implicit
        ]

    def module_instantiates_names(self, *, include_implicit: bool = True) -> list[str]:
        """Return module-instantiation dependency names as plain strings.

        Intent
        ------
        Expose the module instantiates names step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps module instantiates names behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set module_instantiates_names_inputs = received_context
        - return module_instantiates_names_inputs

        Wraps
        -----
        - none
        """
        return [
            dependency.name
            for dependency in self.module_instantiates
            if include_implicit or not dependency.implicit
        ]

    def dispatch_ids(self) -> list[str]:
        """Return dispatch dependency ids as plain strings.

        Intent
        ------
        Expose the dispatch ids step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps dispatch ids behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set dispatch_ids_inputs = received_context
        - return dispatch_ids_inputs

        Wraps
        -----
        - none
        """
        return [dependency.id for dependency in self.dispatches]


@dataclass(frozen=True)
class ParserIssue:
    """Parser-level docstring diagnostic emitted before AST behavior checks.

    Intent
    ------
    Carry a stable code, message, section, and severity for one syntax or local-format
    problem found inside a docstring block.

    Rationale
    ---------
    Parser diagnostics are reused by standalone checks and module validation, so they
    need a compact typed shape independent of AST node metadata.

    Pseudocode
    ----------
    - set parser_issue_contract = code message section and severity
    - return parser_issue_contract

    Wraps
    -----
    - none
    """

    code: str
    message: str
    section: str | None = None
    severity: str = "warning"


def _parse_with_lark(parser: "Lark | None", line: str):
    """Parse one docstring line with a Lark parser.

    Intent
    ------
    Expose the parse with lark step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse with lark behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_with_lark_inputs = received_context
    - set parse_with_lark_effects = local_decisions
    - return parse_with_lark_effects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._ensure_lark_parser:
      why:
        computes: "ensure lark parser supplies repo-local behavior used by parse with lark; this edge is documented from an observed call in the body."
    """
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
    """Return parsed implicit flag from a marker token.

    Intent
    ------
    Expose the parse dependency implicit marker step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse dependency implicit marker behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_dependency_implicit_marker_inputs = received_context
    - return parse_dependency_implicit_marker_inputs

    Wraps
    -----
    - none
    """
    if marker_token is None:
        return False
    return marker_token.strip().lower() == "implicit"


def _resolve_wrap_field_key(
    raw_key: str,
    *,
    allowed_fields: tuple[str, ...],
) -> str | None:
    """Normalize wrap key and check it is allowed by policy.

    Intent
    ------
    Expose the resolve wrap field key step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps resolve wrap field key behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set resolve_wrap_field_key_inputs = received_context
    - set resolve_wrap_field_key_products = carried_outputs
    - return resolve_wrap_field_key_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._normalize_wrap_key:
      why:
        constructs: "normalize wrap key produces a value carried by resolve wrap field key; this edge is documented from the observed product position in the body."
    """
    normalized = _normalize_wrap_key(raw_key)
    if normalized is None:
        return None
    return normalized if normalized in allowed_fields else None


def _extract_wrap_fields(tree, *, allowed_fields: tuple[str, ...]) -> dict[str, str] | None:
    """Extract wrap fields from one Lark parse tree.

    Intent
    ------
    Expose the extract wrap fields step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps extract wrap fields behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set extract_wrap_fields_inputs = received_context
    - set extract_wrap_fields_products = carried_outputs
    - return extract_wrap_fields_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._resolve_wrap_field_key:
      why:
        constructs: "resolve wrap field key produces a value carried by extract wrap fields; this edge is documented from the observed product position in the body."
    """
    from lark.lexer import Token

    entries: dict[str, str] = {}
    current_key: str | None = None
    seen: set[str] = set()

    for node in tree.scan_values(lambda value: isinstance(value, Token)):
        if node.type == "WRAP_KEY":
            key = _resolve_wrap_field_key(str(node), allowed_fields=allowed_fields)
            if key is None or key in seen:
                return None
            seen.add(key)
            current_key = key
            continue
        if node.type == "WRAP_VALUE":
            if current_key is None:
                return None
            entries[current_key] = str(node).strip()
            current_key = None

    return entries


def _extract_dependency_parts(
    source: str,
    tree,
) -> tuple[str, str, str | None]:
    """Extract dependency name, rationale, and marker from a dependency tree.

    Intent
    ------
    Expose the extract dependency parts step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps extract dependency parts behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set extract_dependency_parts_inputs = received_context
    - return extract_dependency_parts_inputs

    Wraps
    -----
    - none
    """
    from lark.lexer import Token

    name = ""
    why = ""
    marker: str | None = None

    for node in tree.scan_values(lambda value: isinstance(value, Token)):
        if node.type in {"name", "IDENT"}:
            name = str(node)
        elif node.type in {"DEPS_RATIONALE", "DEPS_REASON"}:
            why = str(node).strip()
        elif node.type == "IMPLICIT":
            marker = str(node)

    if marker is None and re.search(r"\[\s*implicit\s*\]", source, flags=re.IGNORECASE):
        marker = "implicit"

    return name, why, marker


_LEGACY_DEPENDENCY_RE = re.compile(
    r"^(?P<name>\.?[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*)"
    r"(?:\([^)]*\))?"
    r"\s*(?P<implicit>\[\s*implicit\s*\])?"
    r"\s*->\s*(?P<why>.+?)\s*$"
)


def parse_pseudocode_dependency_ref(
    raw_ref: str,
) -> tuple[str | None, str] | None:
    """Parse one dependency marker from docstring text. Supported forms: - ``@name(...)`` for ``CallsFromRepo`` - ``#name(...)`` for ``Dispatches`` - ``name(...)`` for ``InstantiationsFromRepo``.

    Intent
    ------
    Expose the parse pseudocode dependency ref step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse pseudocode dependency ref behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_pseudocode_dependency_ref_inputs = received_context
    - set parse_pseudocode_dependency_ref_products = carried_outputs
    - return parse_pseudocode_dependency_ref_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._parse_with_lark:
      why:
        constructs: "parse with lark produces a value carried by parse pseudocode dependency ref; this edge is documented from the observed product position in the body."
    ._token_values:
      why:
        constructs: "token values produces a value carried by parse pseudocode dependency ref; this edge is documented from the observed product position in the body."
    """
    if not raw_ref:
        return None
    parse_tree = _parse_with_lark(_PSEUDOCODE_REF_PARSER, raw_ref.strip())
    if parse_tree is None:
        return None
    refs = _token_values(parse_tree, "PSEUDO_REF")
    if not refs:
        return None
    if any(getattr(node, "data", "") == "pseudocode_call_ref" for node in parse_tree.iter_subtrees()):
        return ("CallsFromRepo", refs[0])
    if any(getattr(node, "data", "") == "pseudocode_dispatch_ref" for node in parse_tree.iter_subtrees()):
        return ("Dispatches", refs[0])
    if any(getattr(node, "data", "") == "pseudocode_product_ref" for node in parse_tree.iter_subtrees()):
        return ("InstantiationsFromRepo", refs[0])
    return None


def _normalize_pseudocode_dependency_ref(raw_ref: str) -> str:
    """Return canonical dependency ref key used by checker-side consistency checks.

    Intent
    ------
    Expose the normalize pseudocode dependency ref step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps normalize pseudocode dependency ref behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set normalize_pseudocode_dependency_ref_inputs = received_context
    - set normalize_pseudocode_dependency_ref_products = carried_outputs
    - return normalize_pseudocode_dependency_ref_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .parse_pseudocode_dependency_ref:
      why:
        constructs: "parse pseudocode dependency ref produces a value carried by normalize pseudocode dependency ref; this edge is documented from the observed product position in the body."
    """
    parsed = parse_pseudocode_dependency_ref(raw_ref)
    if parsed is None:
        return raw_ref.strip()
    section, name = parsed
    return f"{section}:{name}" if section else name


def normalize_pseudocode_dependency_ref(raw_ref: str) -> str:
    """Normalize a pseudocode dependency marker for public parser and checker callers.

    Intent
    ------
    Expose one canonical dependency-reference normalizer so syntax parsing,
    validation, and graph extraction compare the same compact marker values.

    Rationale
    ---------
    The public wrapper preserves a stable import surface while the private helper
    keeps the actual marker parsing logic local to this module.

    Pseudocode
    ----------
    - set normalized_ref = delegated marker normalization
    - return normalized_ref

    Wraps
    -----
    _normalize_pseudocode_dependency_ref -> preprocess: accepts raw marker text; postprocess: returns the canonical marker unchanged; fixed_arguments: none

    InstantiationsFromRepo
    ----------------------
    """
    return _normalize_pseudocode_dependency_ref(raw_ref)


def extract_pseudocode_dependency_refs_from_text(text: str) -> tuple[str, ...]:
    """Extract canonical references from a section text line.

    Intent
    ------
    Expose the extract pseudocode dependency refs from text step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps extract pseudocode dependency refs from text behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set extract_pseudocode_dependency_refs_from_text_inputs = received_context
    - set extract_pseudocode_dependency_refs_from_text_products = carried_outputs
    - return extract_pseudocode_dependency_refs_from_text_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._parse_strict_pseudocode_bullet:
      why:
        constructs: "parse strict pseudocode bullet produces a value carried by extract pseudocode dependency refs from text; this edge is documented from the observed product position in the body."
    .parse_pseudocode_dependency_ref:
      why:
        constructs: "parse pseudocode dependency ref produces a value carried by extract pseudocode dependency refs from text; this edge is documented from the observed product position in the body."
    """
    raw = (text or "").strip()
    if not raw:
        return ()
    if raw.startswith("- "):
        step = _parse_strict_pseudocode_bullet(raw)
        section_for_kind = {
            "call": "CallsFromRepo",
            "instantiate": "InstantiationsFromRepo",
            "dispatch": "Dispatches",
        }
        section = section_for_kind.get(step.dependency_kind)
        if section and step.ref:
            return (f"{section}:{step.ref}",)
        return ()
    parsed = parse_pseudocode_dependency_ref(raw)
    if parsed is None:
        return ()
    section, name = parsed
    return (f"{section}:{name}",)


def extract_pseudocode_dependency_refs_from_lines(
    lines: Iterable[object],
) -> tuple[str, ...]:
    """Extract canonical dependency references from several section lines.

    Intent
    ------
    Expose the extract pseudocode dependency refs from lines step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps extract pseudocode dependency refs from lines behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set extract_pseudocode_dependency_refs_from_lines_inputs = received_context
    - set extract_pseudocode_dependency_refs_from_lines_effects = local_decisions
    - return extract_pseudocode_dependency_refs_from_lines_effects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .extract_pseudocode_dependency_refs_from_text:
      why:
        parses: "extract pseudocode dependency refs from text supplies repo-local behavior used by extract pseudocode dependency refs from lines; this edge is documented from an observed call in the body."
    """
    refs: list[str] = []
    seen: set[str] = set()

    for raw in lines:
        for ref in extract_pseudocode_dependency_refs_from_text(str(raw or "")):
            if ref in seen:
                continue
            seen.add(ref)
            refs.append(ref)
    return tuple(refs)


def parse_ownership_reference(
    raw: str,
) -> tuple[str | None, str] | None:
    """Parse one ``Owns`` entry into ``(module_hint, owner_id)``. Returns ``(None, owner)`` for local references and ``(module, owner)`` when a module hint is present.

    Intent
    ------
    Expose the parse ownership reference step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse ownership reference behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_ownership_reference_inputs = received_context
    - set parse_ownership_reference_products = carried_outputs
    - return parse_ownership_reference_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._clean_item:
      why:
        constructs: "clean item produces a value carried by parse ownership reference; this edge is documented from the observed product position in the body."
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
    """Return ``True`` when a line matches NumPy-style underline syntax.

    Intent
    ------
    Expose the is header underline step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps is header underline behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set is_header_underline_inputs = received_context
    - return is_header_underline_inputs

    Wraps
    -----
    - none
    """
    value = line.strip()
    return bool(value) and len(set(value)) == 1 and value[0] in {"-", "="}


def _section_header(
    lines: list[str],
    index: int,
    section_names: frozenset[str] | None = None,
) -> str | None:
    """Detect a valid section header line. Accepts both explicit header lines and header+underline styles.

    Intent
    ------
    Expose the section header step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps section header behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set section_header_inputs = received_context
    - set section_header_products = carried_outputs
    - return section_header_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_header_underline:
      why:
        computes: "is header underline supplies repo-local behavior used by section header; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    ._policy_sections:
      why:
        constructs: "policy sections produces a value carried by section header; this edge is documented from the observed product position in the body."
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
    """Trim a bullet/list entry from docstring blocks.

    Intent
    ------
    Expose the clean item step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps clean item behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set clean_item_inputs = received_context
    - return clean_item_inputs

    Wraps
    -----
    - none
    """
    return value.strip().lstrip("-").strip()


def _parse_strict_pseudocode_bullet(raw: str) -> PseudocodeStep:
    """Parse one strict pseudocode bullet with Lark and project it to metadata.

    Intent
    ------
    Expose the parse strict pseudocode bullet step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse strict pseudocode bullet behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_strict_pseudocode_bullet_inputs = received_context
    - set parse_strict_pseudocode_bullet_products = carried_outputs
    - return parse_strict_pseudocode_bullet_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .PseudocodeStep:
      why:
        constructs: "PseudocodeStep produces a value carried by parse strict pseudocode bullet; this edge is documented from the observed product position in the body."
    ._parse_with_lark:
      why:
        constructs: "parse with lark produces a value carried by parse strict pseudocode bullet; this edge is documented from the observed product position in the body."
    ._token_values:
      why:
        constructs: "token values produces a value carried by parse strict pseudocode bullet; this edge is documented from the observed product position in the body."
    """
    cleaned_raw = raw.rstrip()
    parse_tree = _parse_with_lark(_PSEUDOCODE_BULLET_PARSER, cleaned_raw)
    if parse_tree is None:
        return PseudocodeStep(
            indent=0,
            kind="invalid",
            text=raw.strip(),
            raw=raw.strip(),
        )

    indent_values = _token_values(parse_tree, "PSEUDO_INDENT")
    indent = len(indent_values[0]) // 2 if indent_values else 0
    stripped = cleaned_raw.strip()
    body = stripped[2:].strip() if stripped.startswith("- ") else stripped
    variables = _token_values(parse_tree, "PSEUDO_VAR")
    refs = _token_values(parse_tree, "PSEUDO_REF")
    args_values = _token_values(parse_tree, "PSEUDO_ARGS")
    args = args_values[0][1:-1] if args_values else ""
    expressions = _token_values(parse_tree, "PSEUDO_EXPR")
    resources = _token_values(parse_tree, "RESOURCE_ID")
    subtree_names = {str(getattr(node, "data", "")) for node in parse_tree.iter_subtrees()}

    if "pseudocode_call" in subtree_names:
        return PseudocodeStep(
            indent=indent,
            kind="call",
            text=body,
            raw=stripped,
            output=variables[0] if variables else "",
            ref=refs[0] if refs else "",
            args=args,
            dependency_kind="call",
        )

    if "pseudocode_dispatch" in subtree_names:
        return PseudocodeStep(
            indent=indent,
            kind="dispatch",
            text=body,
            raw=stripped,
            output=variables[0] if variables else "",
            ref=refs[0] if refs else "",
            args=args,
            dependency_kind="dispatch",
        )

    if "pseudocode_instantiate" in subtree_names:
        product_position = "assign"
        if body.startswith("return "):
            product_position = "return"
        elif body.startswith("raise "):
            product_position = "raise"
        elif body.startswith("yield "):
            product_position = "yield"
        return PseudocodeStep(
            indent=indent,
            kind="instantiate",
            text=body,
            raw=stripped,
            output=variables[0] if variables else "",
            ref=refs[0] if refs else "",
            args=args,
            dependency_kind="instantiate",
            product_position=product_position,
        )

    if "pseudocode_set" in subtree_names:
        return PseudocodeStep(
            indent=indent,
            kind="set",
            text=body,
            raw=stripped,
            output=variables[0] if variables else "",
            expression=expressions[0].strip() if expressions else "",
        )

    if "pseudocode_resource" in subtree_names:
        kind = "read" if body.startswith("read ") else "write"
        return PseudocodeStep(
            indent=indent,
            kind=kind,
            text=body,
            raw=stripped,
            resource_id=resources[0] if resources else "",
        )

    if "pseudocode_control" in subtree_names:
        if body == "else:":
            return PseudocodeStep(indent=indent, kind="else", text=body, raw=stripped)
        if body.startswith("if "):
            return PseudocodeStep(
                indent=indent,
                kind="if",
                text=body,
                raw=stripped,
                condition=body[len("if ") : -1].strip(),
            )
        if body.startswith("while "):
            return PseudocodeStep(
                indent=indent,
                kind="while",
                text=body,
                raw=stripped,
                condition=body[len("while ") : -1].strip(),
            )
        if body.startswith("for "):
            loop_text = body[len("for ") : -1].strip()
            loop_variable, _, loop_iterable = loop_text.partition(" in ")
            return PseudocodeStep(
                indent=indent,
                kind="for",
                text=body,
                raw=stripped,
                loop_variable=loop_variable.strip(),
                loop_iterable=loop_iterable.strip(),
            )

    if "pseudocode_terminal" in subtree_names:
        if body in {"continue", "break"}:
            return PseudocodeStep(indent=indent, kind=body, text=body, raw=stripped)
        if body.startswith("return"):
            return PseudocodeStep(
                indent=indent,
                kind="return",
                text=body,
                raw=stripped,
                expression=body[len("return") :].strip(),
            )
        if body.startswith("raise "):
            return PseudocodeStep(
                indent=indent,
                kind="raise",
                text=body,
                raw=stripped,
                expression=body[len("raise ") :].strip(),
            )

    return PseudocodeStep(indent=indent, kind="invalid", text=body, raw=stripped)


def _parse_pseudocode_section(
    lines: Iterable[str],
) -> list[PseudocodeStep]:
    """Parse compact pseudocode entries into typed metadata.

    Intent
    ------
    Expose the parse pseudocode section step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse pseudocode section behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_pseudocode_section_inputs = received_context
    - set parse_pseudocode_section_products = carried_outputs
    - return parse_pseudocode_section_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._parse_strict_pseudocode_bullet:
      why:
        constructs: "parse strict pseudocode bullet produces a value carried by parse pseudocode section; this edge is documented from the observed product position in the body."
    """
    steps: list[PseudocodeStep] = []
    for raw in lines:
        if not raw.strip():
            continue
        steps.append(_parse_strict_pseudocode_bullet(raw))
    return steps


def _extract_pseudocode_dependency_refs(
    steps: Iterable[PseudocodeStep],
    *,
    calls_section: str = "CallsFromRepo",
    instantiates_section: str = "InstantiationsFromRepo",
    dispatches_section: str = "Dispatches",
) -> list[str]:
    """Collect typed dependency references from strict pseudocode steps.

    Intent
    ------
    Expose the extract pseudocode dependency refs step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps extract pseudocode dependency refs behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set extract_pseudocode_dependency_refs_inputs = received_context
    - return extract_pseudocode_dependency_refs_inputs

    Wraps
    -----
    - none
    """
    refs: list[str] = []
    seen: set[str] = set()
    section_for_kind = {
        "call": calls_section,
        "instantiate": instantiates_section,
        "dispatch": dispatches_section,
    }
    for step in steps:
        if not isinstance(step, PseudocodeStep):
            continue
        dependency_kind = step.dependency_kind
        if not dependency_kind or not step.ref:
            continue
        section = section_for_kind.get(dependency_kind)
        if section is None:
            continue
        ref = f"{section}:{step.ref}"
        if ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
    return refs


def _parse_edge(line: str) -> tuple[str, str] | None:
    """Parse ``source -> target`` edge syntax.

    Intent
    ------
    Expose the parse edge step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse edge behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_edge_inputs = received_context
    - set parse_edge_products = carried_outputs
    - return parse_edge_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._parse_with_lark:
      why:
        constructs: "parse with lark produces a value carried by parse edge; this edge is documented from the observed product position in the body."
    ._token_values:
      why:
        constructs: "token values produces a value carried by parse edge; this edge is documented from the observed product position in the body."
    """
    match = _parse_with_lark(_EDGE_PARSER, line)
    if match is None:
        return None
    names = _token_values(match, "name")
    if not names:
        names = _token_values(match, "IDENT")
    if len(names) != 2:
        return None
    return names[0], names[1]


def _parse_wrap_entry(line: str) -> WrapSpec | None:
    """Parse one Wraps section line into a structured wrapper declaration.

    Intent
    ------
    Accept the standard wrapper notation and return a typed record that exposes the
    delegated target plus preprocessing, postprocessing, and fixed-argument notes.

    Rationale
    ---------
    Keeping wrapper parsing here lets syntax validation, graph extraction, and
    behavioral wrapper checks share one interpretation of wrapper metadata.

    Pseudocode
    ----------
    - cleaned_line = ._clean_item(line)
    - if cleaned_line is empty:
      - return
    - parsed_tree = ._parse_with_lark(wrapper_parser, cleaned_line)
    - target_names = ._token_values(parsed_tree, name_token)
    - fields = ._extract_wrap_fields(parsed_tree)
    - wrap_spec = WrapSpec(target_names, fields)
    - return wrap_spec

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._token_values:
      why:
        parses: "Checks for the optional none marker before normal target extraction."

    InstantiationsFromRepo
    ----------------------
    ._clean_item:
      why:
        transforms: "Builds normalized wrapper text from bullet syntax and surrounding whitespace."
    ._parse_with_lark:
      why:
        constructs: "Builds the wrapper parse tree used for target and field extraction."
    ._token_values:
      why:
        constructs: "Builds target-name token lists from the parsed wrapper tree."
    ._extract_wrap_fields:
      why:
        constructs: "Builds canonical wrapper key-value fields from the parse tree."
    .WrapSpec:
      why:
        constructs: "Builds the typed wrapper record returned to graph and validation callers."
    """
    allowed_fields = _WRAP_FIELDS
    cleaned = _clean_item(line)
    if not cleaned:
        return None
    if cleaned.lower() in {"none", "no", "not a wrapper", "not-wrapper", "not wrapper", "n/a", "na"}:
        return WrapSpec(
            target="",
            preprocess="",
            postprocess="",
            fixed_arguments="",
            is_wrapper=False,
        )
    parse_tree = _parse_with_lark(_WRAP_PARSER, cleaned)
    if parse_tree is None:
        return None

    if _token_values(parse_tree, "WRAP_NONE"):
        return WrapSpec(
            target="",
            preprocess="",
            postprocess="",
            fixed_arguments="",
            is_wrapper=False,
        )

    names = _token_values(parse_tree, "name")
    if not names:
        names = _token_values(parse_tree, "IDENT")
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
    """Parse ``Wraps`` section lines into ``WrapSpec`` entries.

    Intent
    ------
    Expose the parse wraps step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse wraps behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_wraps_inputs = received_context
    - set parse_wraps_products = carried_outputs
    - return parse_wraps_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._parse_wrap_entry:
      why:
        constructs: "parse wrap entry produces a value carried by parse wraps; this edge is documented from the observed product position in the body."
    """
    wraps: list[WrapSpec] = []
    invalid: list[str] = []
    for raw in lines:
        parsed = _parse_wrap_entry(raw)
        if parsed is None:
            raw_text = raw.strip()
            if raw_text:
                invalid.append(raw_text)
            continue
        if not parsed.is_wrapper:
            continue
        wraps.append(parsed)
    return wraps, invalid


def _parse_module_dependency_ref(
    line: str,
    *,
    allow_implicit: bool = True,
    require_why: bool = True,
) -> ModuleDependencyRef | None:
    """Parse a module dependency reference entry.

    Intent
    ------
    Expose the parse module dependency ref step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse module dependency ref behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_module_dependency_ref_inputs = received_context
    - set parse_module_dependency_ref_products = carried_outputs
    - return parse_module_dependency_ref_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .ModuleDependencyRef:
      why:
        constructs: "ModuleDependencyRef produces a value carried by parse module dependency ref; this edge is documented from the observed product position in the body."
    ._clean_item:
      why:
        constructs: "clean item produces a value carried by parse module dependency ref; this edge is documented from the observed product position in the body."
    ._extract_dependency_parts:
      why:
        constructs: "extract dependency parts produces a value carried by parse module dependency ref; this edge is documented from the observed product position in the body."
    ._parse_dependency_implicit_marker:
      why:
        constructs: "parse dependency implicit marker produces a value carried by parse module dependency ref; this edge is documented from the observed product position in the body."
    ._parse_with_lark:
      why:
        constructs: "parse with lark produces a value carried by parse module dependency ref; this edge is documented from the observed product position in the body."
    """
    cleaned = _clean_item(line)
    if not cleaned:
        return None
    normalized = re.sub(r"\s*\[\s*implicit\s*\]\s*", "[implicit]", cleaned)
    normalized = re.sub(r"\s*->\s*", "->", normalized)
    parse_tree = _parse_with_lark(_DEPENDENCY_PARSER, normalized)
    if parse_tree is None:
        match = _LEGACY_DEPENDENCY_RE.fullmatch(cleaned)
        if match is None:
            return None
        name = match.group("name").strip()
        why = (match.group("why") or "").strip()
        marker = match.group("implicit")
    else:
        name, why, marker = _extract_dependency_parts(cleaned, parse_tree)
    if not name:
        return None

    if marker is not None:
        if not allow_implicit:
            return None
        is_implicit = _parse_dependency_implicit_marker(marker)
    else:
        is_implicit = False

    if require_why and not why:
        return None

    return ModuleDependencyRef(
        name=name,
        why=why,
        why_legacy_string=True,
        implicit=is_implicit,
    )


def _parse_dependency_implicit_from_name(
    name: str,
    *,
    allow_implicit: bool,
) -> tuple[str, bool] | None:
    """Extract ``[implicit]`` markers from tree dependency keys.

    Intent
    ------
    Expose the parse dependency implicit from name step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse dependency implicit from name behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_dependency_implicit_from_name_inputs = received_context
    - return parse_dependency_implicit_from_name_inputs

    Wraps
    -----
    - none
    """
    marker = re.search(r"\[\s*implicit\s*\]", name, flags=re.IGNORECASE)
    if marker is None:
        return name.strip(), False
    if not allow_implicit:
        return None
    cleaned = re.sub(r"\s*\[\s*implicit\s*\]\s*", "", name, flags=re.IGNORECASE)
    return cleaned.strip(), True


def _join_dependency_path(prefix: str, key: str) -> str:
    """Join a dependency tree prefix and child key into a dotted logical path.

    Intent
    ------
    Expose the join dependency path step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps join dependency path behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set join_dependency_path_inputs = received_context
    - return join_dependency_path_inputs

    Wraps
    -----
    - none
    """
    parent = prefix.strip()
    child = key.strip()
    if not parent:
        return child
    if not child:
        return parent
    if child.startswith("."):
        return f"{parent}{child}"
    return f"{parent}.{child}"


def _parse_dependency_why_value(value: object) -> tuple[str, str, bool, int]:
    """Parse dependency why text plus compact action metadata.

    Intent
    ------
    Expose the parse dependency why value step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse dependency why value behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_dependency_why_value_inputs = received_context
    - return parse_dependency_why_value_inputs

    Wraps
    -----
    - none
    """
    if isinstance(value, str):
        return value.strip(), "", True, 0
    if isinstance(value, Mapping):
        items = [(str(key).strip(), item) for key, item in value.items() if str(key).strip()]
        if len(items) != 1:
            joined = " ".join(str(item or "").strip() for _, item in items if str(item or "").strip())
            return joined, "", False, len(items)
        action, explanation = items[0]
        return str(explanation or "").strip(), action, False, 1
    return str(value or "").strip(), "", False, 0


def _flatten_dependency_tree(
    value: object,
    *,
    prefix: str = "",
    require_why: bool,
) -> tuple[list[tuple[str, str, str, bool, int]], list[str]]:
    """Flatten a YAML-like dependency tree into ``(path, why)`` pairs.

    Intent
    ------
    Expose the flatten dependency tree step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps flatten dependency tree behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set flatten_dependency_tree_inputs = received_context
    - set flatten_dependency_tree_products = carried_outputs
    - return flatten_dependency_tree_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._flatten_dependency_tree:
      why:
        constructs: "flatten dependency tree produces a value carried by flatten dependency tree; this edge is documented from the observed product position in the body."
    ._join_dependency_path:
      why:
        constructs: "join dependency path produces a value carried by flatten dependency tree; this edge is documented from the observed product position in the body."
    ._parse_dependency_why_value:
      why:
        constructs: "parse dependency why value produces a value carried by flatten dependency tree; this edge is documented from the observed product position in the body."
    """
    entries: list[tuple[str, str, str, bool, int]] = []
    invalid: list[str] = []

    if not isinstance(value, Mapping):
        return entries, invalid

    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key:
            invalid.append(str(raw_key))
            continue
        path = _join_dependency_path(prefix, key)

        if isinstance(raw_value, Mapping) and "why" in raw_value:
            why, why_action, why_legacy, why_action_count = _parse_dependency_why_value(
                raw_value.get("why")
            )
            if require_why and not why:
                invalid.append(path)
                continue
            entries.append((path, why, why_action, why_legacy, why_action_count))
            child_map = {
                child_key: child_value
                for child_key, child_value in raw_value.items()
                if child_key != "why" and child_key != "implicit"
            }
            child_entries, child_invalid = _flatten_dependency_tree(
                child_map,
                prefix=path,
                require_why=require_why,
            )
            entries.extend(child_entries)
            invalid.extend(child_invalid)
            continue

        if isinstance(raw_value, Mapping):
            child_entries, child_invalid = _flatten_dependency_tree(
                raw_value,
                prefix=path,
                require_why=require_why,
            )
            entries.extend(child_entries)
            invalid.extend(child_invalid)
            continue

        if isinstance(raw_value, str):
            why = raw_value.strip()
            if require_why and not why:
                invalid.append(path)
                continue
            entries.append((path, why, "", True, 0))
            continue

        invalid.append(path)

    return entries, invalid


def _parse_dependency_section_tree(
    lines: Iterable[str],
    *,
    allow_implicit: bool,
    require_why: bool,
) -> tuple[list[ModuleDependencyRef], list[str]]:
    """Parse tree-shaped call/instantiation dependency declarations.

    Intent
    ------
    Expose the parse dependency section tree step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse dependency section tree behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_dependency_section_tree_inputs = received_context
    - set parse_dependency_section_tree_products = carried_outputs
    - return parse_dependency_section_tree_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .ModuleDependencyRef:
      why:
        constructs: "ModuleDependencyRef produces a value carried by parse dependency section tree; this edge is documented from the observed product position in the body."
    ._flatten_dependency_tree:
      why:
        constructs: "flatten dependency tree produces a value carried by parse dependency section tree; this edge is documented from the observed product position in the body."
    ._parse_dependency_implicit_from_name:
      why:
        constructs: "parse dependency implicit from name produces a value carried by parse dependency section tree; this edge is documented from the observed product position in the body."
    ._parse_dependency_why_value:
      why:
        constructs: "parse dependency why value produces a value carried by parse dependency section tree; this edge is documented from the observed product position in the body."
    """
    raw_lines = [line.rstrip() for line in lines if line.strip()]
    if not raw_lines:
        return [], []

    loaded = None
    invalid: list[str] = []
    try:
        loaded = yaml.safe_load("\n".join(raw_lines))
    except yaml.YAMLError:
        invalid.extend(line.strip() for line in raw_lines)

    entries: list[ModuleDependencyRef] = []
    if isinstance(loaded, Mapping):
        flattened, tree_invalid = _flatten_dependency_tree(
            loaded,
            require_why=require_why,
        )
        invalid.extend(tree_invalid)
        for name, why, why_action, why_legacy, why_action_count in flattened:
            parsed_name = _parse_dependency_implicit_from_name(
                name,
                allow_implicit=allow_implicit,
            )
            if parsed_name is None:
                invalid.append(name)
                continue
            clean_name, implicit = parsed_name
            entries.append(
                ModuleDependencyRef(
                    name=clean_name,
                    why=why,
                    why_action=why_action,
                    why_legacy_string=why_legacy,
                    why_action_count=why_action_count,
                    implicit=implicit,
                )
            )
        return entries, invalid

    if isinstance(loaded, list):
        for item in loaded:
            if isinstance(item, Mapping):
                name = str(item.get("name") or item.get("id") or "").strip()
                why, why_action, why_legacy, why_action_count = _parse_dependency_why_value(
                    item.get("why")
                )
                if not name or (require_why and not why):
                    invalid.append(str(item))
                    continue
                parsed_name = _parse_dependency_implicit_from_name(
                    name,
                    allow_implicit=allow_implicit,
                )
                if parsed_name is None:
                    invalid.append(name)
                    continue
                clean_name, implicit = parsed_name
                entries.append(
                    ModuleDependencyRef(
                        name=clean_name,
                        why=why,
                        why_action=why_action,
                        why_legacy_string=why_legacy,
                        why_action_count=why_action_count,
                        implicit=implicit,
                    )
                )
                continue
            invalid.append(str(item))
        return entries, invalid

    if loaded is not None:
        invalid.extend(line.strip() for line in raw_lines)

    return entries, invalid


def _parse_dispatch_section_tree(
    lines: Iterable[str],
    *,
    require_why: bool,
) -> tuple[list[DispatchDependencyRef], list[str]]:
    """Parse tree-shaped dispatch dependency declarations.

    Intent
    ------
    Expose the parse dispatch section tree step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse dispatch section tree behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_dispatch_section_tree_inputs = received_context
    - set parse_dispatch_section_tree_products = carried_outputs
    - return parse_dispatch_section_tree_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .DispatchDependencyRef:
      why:
        constructs: "DispatchDependencyRef produces a value carried by parse dispatch section tree; this edge is documented from the observed product position in the body."
    ._flatten_dependency_tree:
      why:
        constructs: "flatten dependency tree produces a value carried by parse dispatch section tree; this edge is documented from the observed product position in the body."
    ._parse_dependency_why_value:
      why:
        constructs: "parse dependency why value produces a value carried by parse dispatch section tree; this edge is documented from the observed product position in the body."
    """
    raw_lines = [line.rstrip() for line in lines if line.strip()]
    if not raw_lines:
        return [], []

    try:
        loaded = yaml.safe_load("\n".join(raw_lines))
    except yaml.YAMLError:
        return [], [line.strip() for line in raw_lines]

    entries: list[DispatchDependencyRef] = []
    invalid: list[str] = []
    if isinstance(loaded, Mapping):
        flattened, tree_invalid = _flatten_dependency_tree(
            loaded,
            require_why=require_why,
        )
        invalid.extend(tree_invalid)
        entries.extend(
            DispatchDependencyRef(
                id=path,
                why=why,
                why_action=why_action,
                why_legacy_string=why_legacy,
                why_action_count=why_action_count,
            )
            for path, why, why_action, why_legacy, why_action_count in flattened
        )
        return entries, invalid

    if isinstance(loaded, list):
        for item in loaded:
            if not isinstance(item, Mapping):
                invalid.append(str(item))
                continue
            dispatch_id = str(item.get("id") or "").strip()
            why, why_action, why_legacy, why_action_count = _parse_dependency_why_value(
                item.get("why")
            )
            if not dispatch_id or (require_why and not why):
                invalid.append(str(item))
                continue
            entries.append(
                DispatchDependencyRef(
                    id=dispatch_id,
                    why=why,
                    why_action=why_action,
                    why_legacy_string=why_legacy,
                    why_action_count=why_action_count,
                )
            )
        return entries, invalid

    return [], [line.strip() for line in raw_lines]


def _parse_module_dependency_section(
    lines: Iterable[str],
    *,
    allow_implicit: bool,
    require_why: bool,
) -> tuple[list[ModuleDependencyRef], list[str]]:
    """Parse a module dependency section using structured tree syntax.

    Intent
    ------
    Expose the parse module dependency section step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse module dependency section behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_module_dependency_section_inputs = received_context
    - set parse_module_dependency_section_products = carried_outputs
    - return parse_module_dependency_section_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._parse_dependency_section_tree:
      why:
        constructs: "parse dependency section tree produces a value carried by parse module dependency section; this edge is documented from the observed product position in the body."
    """
    raw_lines = [line.rstrip() for line in lines if line.strip()]
    tree_entries, tree_invalid = _parse_dependency_section_tree(
        raw_lines,
        allow_implicit=allow_implicit,
        require_why=require_why,
    )
    return tree_entries, tree_invalid


def _parse_dispatch_dependency_section(
    lines: Iterable[str],
    *,
    require_why: bool,
) -> tuple[list[DispatchDependencyRef], list[str]]:
    """Parse a dispatch dependency section using structured tree syntax.

    Intent
    ------
    Expose the parse dispatch dependency section step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse dispatch dependency section behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_dispatch_dependency_section_inputs = received_context
    - set parse_dispatch_dependency_section_products = carried_outputs
    - return parse_dispatch_dependency_section_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .DispatchDependencyRef:
      why:
        constructs: "DispatchDependencyRef produces a value carried by parse dispatch dependency section; this edge is documented from the observed product position in the body."
    ._parse_dispatch_section_tree:
      why:
        constructs: "parse dispatch section tree produces a value carried by parse dispatch dependency section; this edge is documented from the observed product position in the body."
    """
    raw_lines = [line.rstrip() for line in lines if line.strip()]
    tree_entries, tree_invalid = _parse_dispatch_section_tree(
        raw_lines,
        require_why=require_why,
    )
    return tree_entries, tree_invalid


def _parse_resource_section(
    lines: Iterable[str],
    *,
    require_why: bool,
) -> tuple[list[ResourceDependencyRef], list[str]]:
    """Parse compact resource dependency declarations.

    Intent
    ------
    Expose the parse resource section step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse resource section behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_resource_section_inputs = received_context
    - set parse_resource_section_products = carried_outputs
    - return parse_resource_section_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .ResourceDependencyRef:
      why:
        constructs: "ResourceDependencyRef produces a value carried by parse resource section; this edge is documented from the observed product position in the body."
    ._parse_dependency_why_value:
      why:
        constructs: "parse dependency why value produces a value carried by parse resource section; this edge is documented from the observed product position in the body."
    """
    raw_lines = [line.rstrip() for line in lines if line.strip()]
    if not raw_lines:
        return [], []

    try:
        loaded = yaml.safe_load("\n".join(raw_lines))
    except yaml.YAMLError:
        return [], [line.strip() for line in raw_lines]

    entries: list[ResourceDependencyRef] = []
    invalid: list[str] = []

    def _entry(resource_id: str, value: object) -> None:
        """_entry supports docstring syntax parsing and typed IR construction as a documented callable boundary.

        Intent
        ------
        Expose the entry step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps entry behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set entry_inputs = received_context
        - set entry_products = carried_outputs
        - return entry_products

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .ResourceDependencyRef:
          why:
            constructs: "ResourceDependencyRef produces a value carried by entry; this edge is documented from the observed product position in the body."
        ._parse_dependency_why_value:
          why:
            constructs: "parse dependency why value produces a value carried by entry; this edge is documented from the observed product position in the body."
        """
        if not isinstance(value, Mapping):
            invalid.append(resource_id)
            return
        why, why_action, why_legacy, why_action_count = _parse_dependency_why_value(
            value.get("why")
        )
        if not resource_id or (require_why and not why):
            invalid.append(resource_id)
            return
        entries.append(
            ResourceDependencyRef(
                id=resource_id,
                kind=str(value.get("kind") or "").strip(),
                access=str(value.get("access") or "").strip(),
                why=why,
                why_action=why_action,
                why_legacy_string=why_legacy,
                why_action_count=why_action_count,
            )
        )

    if isinstance(loaded, Mapping):
        for key, value in loaded.items():
            _entry(str(key).strip(), value)
        return entries, invalid

    if isinstance(loaded, list):
        for item in loaded:
            if not isinstance(item, Mapping):
                invalid.append(str(item))
                continue
            resource_id = str(item.get("id") or "").strip()
            _entry(resource_id, item)
        return entries, invalid

    return [], [line.strip() for line in raw_lines]


def _parse_dataflow_section(
    lines: Iterable[str],
    *,
    require_why: bool,
) -> tuple[list[DataflowDependencyRef], list[str]]:
    """Parse compact dataflow edge declarations.

    Intent
    ------
    Expose the parse dataflow section step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse dataflow section behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_dataflow_section_inputs = received_context
    - set parse_dataflow_section_products = carried_outputs
    - return parse_dataflow_section_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .DataflowDependencyRef:
      why:
        constructs: "DataflowDependencyRef produces a value carried by parse dataflow section; this edge is documented from the observed product position in the body."
    ._parse_dependency_why_value:
      why:
        constructs: "parse dependency why value produces a value carried by parse dataflow section; this edge is documented from the observed product position in the body."
    """
    raw_lines = [line.rstrip() for line in lines if line.strip()]
    if not raw_lines:
        return [], []

    try:
        loaded = yaml.safe_load("\n".join(raw_lines))
    except yaml.YAMLError:
        return [], [line.strip() for line in raw_lines]

    entries: list[DataflowDependencyRef] = []
    invalid: list[str] = []
    if not isinstance(loaded, list):
        return [], [line.strip() for line in raw_lines]

    for item in loaded:
        if not isinstance(item, Mapping):
            invalid.append(str(item))
            continue
        source = str(item.get("from") or item.get("source") or "").strip()
        target = str(item.get("to") or item.get("target") or "").strip()
        why, why_action, why_legacy, why_action_count = _parse_dependency_why_value(
            item.get("why")
        )
        if not source or not target or (require_why and not why):
            invalid.append(str(item))
            continue
        entries.append(
            DataflowDependencyRef(
                source=source,
                target=target,
                kind=str(item.get("kind") or "").strip(),
                why=why,
                why_action=why_action,
                why_legacy_string=why_legacy,
                why_action_count=why_action_count,
            )
        )
    return entries, invalid


def parse_pipeline(docstring: str) -> PipelineSpec:
    """Parse the module-level ``GraphPipeline`` block.

    Intent
    ------
    Expose the parse pipeline step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse pipeline behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_pipeline_inputs = received_context
    - set parse_pipeline_products = carried_outputs
    - return parse_pipeline_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .PipelineSpec:
      why:
        constructs: "PipelineSpec produces a value carried by parse pipeline; this edge is documented from the observed product position in the body."
    ._clean_item:
      why:
        constructs: "clean item produces a value carried by parse pipeline; this edge is documented from the observed product position in the body."
    ._parse_edge:
      why:
        constructs: "parse edge produces a value carried by parse pipeline; this edge is documented from the observed product position in the body."
    .load_docstring_schema:
      why:
        constructs: "load docstring schema produces a value carried by parse pipeline; this edge is documented from the observed product position in the body."
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
    """Parse callable-level graph metadata from a callable docstring. Unknown sections are preserved in ``sections`` so schema growth remains discoverable without parser edits.

    Intent
    ------
    Expose the parse graph block step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse graph block behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_graph_block_inputs = received_context
    - set parse_graph_block_products = carried_outputs
    - return parse_graph_block_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_header_underline:
      why:
        computes: "is header underline supplies repo-local behavior used by parse graph block; this edge is documented from an observed call in the body."
    ._parse_dataflow_section:
      why:
        computes: "parse dataflow section supplies repo-local behavior used by parse graph block; this edge is documented from an observed call in the body."
    ._parse_dispatch_dependency_section:
      why:
        computes: "parse dispatch dependency section supplies repo-local behavior used by parse graph block; this edge is documented from an observed call in the body."
    ._parse_module_dependency_section:
      why:
        computes: "parse module dependency section supplies repo-local behavior used by parse graph block; this edge is documented from an observed call in the body."
    ._parse_resource_section:
      why:
        computes: "parse resource section supplies repo-local behavior used by parse graph block; this edge is documented from an observed call in the body."
    .extract_pseudocode_dependency_refs_from_lines:
      why:
        parses: "extract pseudocode dependency refs from lines supplies repo-local behavior used by parse graph block; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    .FunctionSpec:
      why:
        constructs: "FunctionSpec produces a value carried by parse graph block; this edge is documented from the observed product position in the body."
    ._clean_item:
      why:
        constructs: "clean item produces a value carried by parse graph block; this edge is documented from the observed product position in the body."
    ._extract_pseudocode_dependency_refs:
      why:
        constructs: "extract pseudocode dependency refs produces a value carried by parse graph block; this edge is documented from the observed product position in the body."
    ._parse_edge:
      why:
        constructs: "parse edge produces a value carried by parse graph block; this edge is documented from the observed product position in the body."
    ._parse_pseudocode_section:
      why:
        constructs: "parse pseudocode section produces a value carried by parse graph block; this edge is documented from the observed product position in the body."
    ._parse_wraps:
      why:
        constructs: "parse wraps produces a value carried by parse graph block; this edge is documented from the observed product position in the body."
    ._policy_sections:
      why:
        constructs: "policy sections produces a value carried by parse graph block; this edge is documented from the observed product position in the body."
    ._section_header:
      why:
        constructs: "section header produces a value carried by parse graph block; this edge is documented from the observed product position in the body."
    .load_docstring_schema:
      why:
        constructs: "load docstring schema produces a value carried by parse graph block; this edge is documented from the observed product position in the body."
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
    dependency_reference_sections = schema_rules.callable.dependency_reference_sections

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
            if (
                section is not None
                and not section.startswith(_UNKNOWN_SECTION_PREFIX)
                and section_lines
            ):
                sections[section] = section_lines
            section = section_name
            section_lines = []
            if section is not None:
                sections.setdefault(section, [])
            if index + 1 < len(lines) and _is_header_underline(lines[index + 1]):
                skip_next = True
            continue

        if (
            raw.strip()
            and index + 1 < len(lines)
            and _is_header_underline(lines[index + 1])
        ):
            if (
                section is not None
                and not section.startswith(_UNKNOWN_SECTION_PREFIX)
                and section_lines
            ):
                sections[section] = section_lines
            section = f"{_UNKNOWN_SECTION_PREFIX}{raw.strip()}"
            section_lines = []
            skip_next = True
            continue

        if section is None:
            if raw.strip():
                summary_lines.append(raw.strip())
            continue

        section_lines.append(raw)

    if (
        section is not None
        and not section.startswith(_UNKNOWN_SECTION_PREFIX)
        and section_lines
    ):
        sections[section] = section_lines

    for section_name, lines_for_section in sections.items():
        cleaned = [line.rstrip() for line in lines_for_section if line.strip()]
        if cleaned:
            spec.sections[section_name] = cleaned

    if summary_lines:
        spec.summary = " ".join(line.strip() for line in summary_lines if line.strip())

    intent_lines = spec.sections.get("Intent", [])
    role_lines = spec.sections.get("Role", [])
    if intent_lines:
        role_lines = intent_lines + role_lines
    for line in role_lines:
        cleaned = _clean_item(line)
        if cleaned:
            spec.role = f"{spec.role} {cleaned}".strip() if spec.role else cleaned

    rationale_lines = spec.sections.get("Rationale", [])
    for line in rationale_lines:
        cleaned = _clean_item(line)
        if cleaned:
            spec.rationale = f"{spec.rationale} {cleaned}".strip() if spec.rationale else cleaned

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
    spec.pseudocode_dependency_refs = _extract_pseudocode_dependency_refs(
        spec.pseudocode_steps,
        calls_section=module_dependency_rules.calls_section,
        instantiates_section=module_dependency_rules.instantiates_section,
        dispatches_section=module_dependency_rules.dispatches_section,
    )
    for section_name in dependency_reference_sections:
        if section_name == pseudocode_rules.section:
            continue
        for ref in extract_pseudocode_dependency_refs_from_lines(
            spec.sections.get(section_name, ())
        ):
            if ref not in spec.pseudocode_dependency_refs:
                spec.pseudocode_dependency_refs.append(ref)
    spec.module_calls.extend(
        _parse_module_dependency_section(
            spec.sections.get(module_dependency_rules.calls_section, []),
            allow_implicit=module_dependency_rules.allow_implicit,
            require_why=module_dependency_rules.require_why,
        )[0]
    )
    spec.module_instantiates.extend(
        _parse_module_dependency_section(
            spec.sections.get(module_dependency_rules.instantiates_section, []),
            allow_implicit=module_dependency_rules.allow_implicit,
            require_why=module_dependency_rules.require_why,
        )[0]
    )
    spec.dispatches.extend(
        _parse_dispatch_dependency_section(
            spec.sections.get(module_dependency_rules.dispatches_section, []),
            require_why=module_dependency_rules.require_why,
        )[0]
    )
    spec.resources.extend(
        _parse_resource_section(
            spec.sections.get("Resources", []),
            require_why=module_dependency_rules.require_why,
        )[0]
    )
    spec.dataflows.extend(
        _parse_dataflow_section(
            spec.sections.get("Dataflow", []),
            require_why=module_dependency_rules.require_why,
        )[0]
    )

    for owner in spec.sections.get("Owns", []):
        cleaned = _clean_item(owner)
        if cleaned:
            spec.owns.append(cleaned)

    if not spec.role:
        spec.role = None

    return spec


def validate_edge_expression(edge_text: str) -> bool:
    """Return ``True`` when text matches ``source -> target`` syntax.

    Intent
    ------
    Expose the validate edge expression step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps validate edge expression behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set validate_edge_expression_inputs = received_context
    - set validate_edge_expression_effects = local_decisions
    - return validate_edge_expression_effects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._parse_edge:
      why:
        computes: "parse edge supplies repo-local behavior used by validate edge expression; this edge is documented from an observed call in the body."
    """
    return _parse_edge(edge_text) is not None


def parse_ownable_registry(
    docstring: str,
    *,
    section: str = OWNABLE_SECTION,
) -> dict[str, str]:
    """Parse module-level ``Ownable`` entries from a module docstring.

    Intent
    ------
    Expose the parse ownable registry step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse ownable registry behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_ownable_registry_inputs = received_context
    - set parse_ownable_registry_products = carried_outputs
    - return parse_ownable_registry_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._clean_item:
      why:
        constructs: "clean item produces a value carried by parse ownable registry; this edge is documented from the observed product position in the body."
    .parse_graph_block:
      why:
        constructs: "parse graph block produces a value carried by parse ownable registry; this edge is documented from the observed product position in the body."
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
    """Pipeline validation delegated to validator module.

    Intent
    ------
    Expose the validate pipeline docstring step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps validate pipeline docstring behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set validate_pipeline_docstring_inputs = received_context
    - return validate_pipeline_docstring_inputs

    Wraps
    -----
    - none
    """
    from .validation import validate_pipeline_docstring as _validate_pipeline_docstring

    return _validate_pipeline_docstring(docstring)


def check_graph_docstring(docstring: str, schema_rules: DocstringSchema | None = None) -> tuple[ParserIssue, ...]:
    """Callable docstring checks delegated to validator module.

    Intent
    ------
    Expose the check graph docstring step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps check graph docstring behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set check_graph_docstring_inputs = received_context
    - return check_graph_docstring_inputs

    Wraps
    -----
    - none
    """
    from .validation import check_graph_docstring as _check_graph_docstring

    return _check_graph_docstring(docstring, schema_rules=schema_rules)


def check_pipeline_docstring(docstring: str) -> tuple[ParserIssue, ...]:
    """Pipeline validation delegated to validator module.

    Intent
    ------
    Expose the check pipeline docstring step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps check pipeline docstring behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set check_pipeline_docstring_inputs = received_context
    - return check_pipeline_docstring_inputs

    Wraps
    -----
    - none
    """
    from .validation import check_pipeline_docstring as _check_pipeline_docstring

    return _check_pipeline_docstring(docstring)


def check(docstring: str, kind: str = "callable") -> tuple[ParserIssue, ...]:
    """Generic validation entry point for docstrings.

    Intent
    ------
    Expose the check step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps check behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set check_inputs = received_context
    - return check_inputs

    Wraps
    -----
    - none
    """
    from .validation import check as _check
    return _check(docstring, kind=kind)


def load_docstring_schema(path: str | Path | None = None) -> DocstringSchema:
    """Load the active docstring policy through the parser compatibility surface.

    Intent
    ------
    Delegate legacy parser imports to the policy module without duplicating policy
    loading behavior.

    Rationale
    ---------
    Older callers still import this symbol from the parser module, while new code
    keeps configuration and profile materialization in policy.

    Pseudocode
    ----------
    - set schema_rules = delegated policy loader output
    - return schema_rules

    Wraps
    -----
    - none
    """
    from .policy import load_docstring_schema as _load_docstring_schema

    return _load_docstring_schema(path)


def _render_arg_default(expr: ast.expr | None) -> str | None:
    """Render an AST expression for display in signatures. Falls back to ``None`` when unparsing fails.

    Intent
    ------
    Expose the render arg default step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps render arg default behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set render_arg_default_inputs = received_context
    - return render_arg_default_inputs

    Wraps
    -----
    - none
    """
    if expr is None:
        return None
    try:
        return ast.unparse(expr)
    except Exception:
        return None


def _format_arg(arg: ast.arg) -> str:
    """Render an AST argument name with optional annotation.

    Intent
    ------
    Expose the format arg step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps format arg behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set format_arg_inputs = received_context
    - set format_arg_products = carried_outputs
    - return format_arg_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._render_arg_default:
      why:
        constructs: "render arg default produces a value carried by format arg; this edge is documented from the observed product position in the body."
    """
    text = arg.arg
    if arg.annotation is not None:
        annotation = _render_arg_default(arg.annotation)
        if annotation is not None:
            text = f"{text}: {annotation}"
    return text


def _format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Render an AST function signature into compact display text.

    Intent
    ------
    Convert Python argument metadata into a stable signature string for extracted
    function specs.

    Rationale
    ---------
    Docstring graph consumers need readable callable signatures, but they should not
    need to know how Python AST argument nodes are laid out.

    Pseudocode
    ----------
    - rendered_params = ._format_arg(function_parameters)
    - rendered_defaults = ._render_arg_default(default_nodes)
    - set signature_text = combine rendered_params and rendered_defaults
    - return signature_text

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._format_arg:
      why:
        computes: "Renders parameter text while the signature builder iterates over AST arguments."
    ._render_arg_default:
      why:
        computes: "Renders default-value text while aligning defaults with parameters."

    InstantiationsFromRepo
    ----------------------
    ._format_arg:
      why:
        constructs: "Builds each rendered parameter fragment used in the final signature string."
    ._render_arg_default:
      why:
        constructs: "Builds readable default-value fragments for parameters that define defaults."
    """
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
    """Parse graph metadata for all callables in a module AST. Keys are callable names; methods use ``Class.method`` notation.

    Intent
    ------
    Expose the parse function graphs step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse function graphs behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_function_graphs_inputs = received_context
    - set parse_function_graphs_products = carried_outputs
    - return parse_function_graphs_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._format_signature:
      why:
        constructs: "format signature produces a value carried by parse function graphs; this edge is documented from the observed product position in the body."
    .parse_graph_block:
      why:
        constructs: "parse graph block produces a value carried by parse function graphs; this edge is documented from the observed product position in the body."
    """
    specs: dict[str, FunctionSpec] = {}

    def parse_callable(node: ast.AST, name: str, is_method: bool = False) -> None:
        """parse_callable supports docstring syntax parsing and typed IR construction as a documented callable boundary.

        Intent
        ------
        Expose the parse callable step in docstring syntax parsing and typed IR construction so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps parse callable behavior separate inside docstring syntax parsing and typed IR construction; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set parse_callable_inputs = received_context
        - set parse_callable_products = carried_outputs
        - return parse_callable_products

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        ._format_signature:
          why:
            constructs: "format signature produces a value carried by parse callable; this edge is documented from the observed product position in the body."
        .parse_graph_block:
          why:
            constructs: "parse graph block produces a value carried by parse callable; this edge is documented from the observed product position in the body."
        """
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
    "DispatchDependencyRef",
    "ResourceDependencyRef",
    "DataflowDependencyRef",
    "ModuleOwnershipConfig",
    "DocstringSchema",
    "CallableDocstringSchema",
    "PipelineDocstringSchema",
    "ModuleDocstringSchema",
    "validate_edge_expression",
    "validate_pipeline_docstring",
    "parse_ownership_reference",
    "parse_ownable_registry",
    "parse_pseudocode_dependency_ref",
    "normalize_pseudocode_dependency_ref",
    "extract_pseudocode_dependency_refs_from_text",
    "extract_pseudocode_dependency_refs_from_lines",
]
