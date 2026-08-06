#!/usr/bin/env python3
"""Validation helpers for docstring consistency and parser-level metadata quality."""

from __future__ import annotations

import ast
import builtins
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping

from ..common.docstring.docstring_parser import (
    ParserIssue,
    check_graph_docstring,
    check_pipeline_docstring,
    parse_graph_block,
    parse_ownership_reference,
    parse_ownable_registry,
)
from ..common.docstring.docstring_policy import (
    ModuleDependencyConfig,
    DocstringSchema,
    OwnershipConfig,
    apply_docstring_profiles,
    load_docstring_check_categories,
    load_docstring_schema,
)
from ..common.test_discovery import is_test_module as _is_repo_test_module

_IGNORED_CALL_BASES = frozenset({
    "self",
    "cls",
    "super",
    "type(self)",
    "globals",
    "locals",
    "vars",
    "object",
})

_BUILTIN_SYMBOLS = frozenset(dir(builtins))
_BUILTIN_EXCEPTION_NAMES = frozenset(
    name
    for name in dir(builtins)
    if isinstance(getattr(builtins, name), type)
    and issubclass(getattr(builtins, name), BaseException)
)
_COMPACT_SECTION_WAIVERS = frozenset({"Intent", "Rationale", "Pseudocode", "Wraps"})
_PSEUDOCODE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)(?![A-Za-z0-9_])"
)
_PSEUDOCODE_SKIP_TOKENS = {
    "and",
    "as",
    "else",
    "for",
    "if",
    "in",
    "is",
    "not",
    "or",
    "return",
    "then",
    "try",
    "with",
    "while",
    "with",
}
_DISPATCH_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_.:-])(?:skills\.)?[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.interface\.[A-Za-z0-9_-]+(?![A-Za-z0-9_.:-])"
)


def _relative_dependency_tail(name: str) -> str:
    """Return the logical tail for a leading-dot relative dependency.

    Intent
    ------
    Expose the relative dependency tail step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps relative dependency tail behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set relative_dependency_tail_inputs = received_context
    - return relative_dependency_tail_inputs

    Wraps
    -----
    - none
    """
    raw = (name or "").strip()
    if not raw.startswith("."):
        return ""
    return raw.lstrip(".")


def _dependency_name_variants(
    name: str,
    import_aliases: Mapping[str, str],
) -> tuple[str, ...]:
    """Build candidate forms for matching dependency mentions in pseudocode.

    Intent
    ------
    Expose the dependency name variants step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps dependency name variants behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set dependency_name_variants_inputs = received_context
    - set dependency_name_variants_products = carried_outputs
    - return dependency_name_variants_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._normalize_dependency_name:
      why:
        constructs: "normalize dependency name produces a value carried by dependency name variants; this edge is documented from the observed product position in the body."
    ._relative_dependency_tail:
      why:
        constructs: "relative dependency tail produces a value carried by dependency name variants; this edge is documented from the observed product position in the body."
    """
    if not name:
        return ()

    stripped = name.strip()
    if not stripped:
        return ()

    variants: set[str] = {stripped}
    relative = _relative_dependency_tail(stripped)
    if relative:
        variants.add(relative)
        variants.add(relative.rsplit(".", 1)[-1])
    normalized = _normalize_dependency_name(relative or stripped, import_aliases)
    if normalized:
        variants.add(normalized)
        if "." in normalized:
            variants.add(normalized.split(".", 1)[0])
            variants.add(normalized.rsplit(".", 1)[-1])

    for alias, resolved in import_aliases.items():
        if not alias or not resolved:
            continue
        if normalized == resolved:
            variants.add(alias)
            continue
        if normalized.startswith(f"{resolved}."):
            suffix = normalized[len(resolved) + 1 :]
            variants.add(f"{alias}.{suffix}")
        if stripped == alias:
            variants.add(resolved)

    return tuple(sorted(variants))


def _dependency_is_resolved(
    name: str,
    import_aliases: Mapping[str, str],
    defined_symbols: set[str],
) -> bool:
    """Best-effort resolution check for documented dependency names.

    Intent
    ------
    Accept local symbols, import aliases, relative repo paths, and alias targets as
    valid declared dependency roots.

    Rationale
    ---------
    Docstrings use logical dependency paths, so resolution must recognize both the
    name visible in source and the repo-relative path stored in the import map.

    Pseudocode
    ----------
    - if dependency_name is empty:
      - return false
    - if dependency_name is relative import path:
      - return true
    - candidate_name = ._normalize_dependency_name(dependency_name)
    - if candidate_name matches import or local symbols:
      - return true
    - return false

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._dependency_matches:
      why:
        computes: "Compares a declared relative import path with stored import-alias targets."

    InstantiationsFromRepo
    ----------------------
    ._normalize_dependency_name:
      why:
        constructs: "Builds alias-normalized dependency names for resolution checks."
    ._relative_dependency_tail:
      why:
        constructs: "Builds local tails from relative dependency paths before symbol lookup."
    """
    if not name:
        return False

    stripped = name.strip()
    if not stripped:
        return False

    relative = _relative_dependency_tail(stripped)
    if relative:
        if stripped.startswith(".."):
            return True
        if stripped in set(import_aliases.values()):
            return True
        relative_head = relative.split(".", 1)[0]
        relative_tail = relative.rsplit(".", 1)[-1]
        return (
            relative in defined_symbols
            or relative_head in defined_symbols
            or relative_tail in defined_symbols
            or relative_head in import_aliases
            or any(
                _dependency_matches(stripped, imported, {})
                for imported in import_aliases.values()
            )
        )

    if stripped in defined_symbols:
        return True

    head = stripped.split(".", 1)[0]
    if head in import_aliases or stripped in import_aliases:
        return True

    normalized = _normalize_dependency_name(stripped, import_aliases)
    if normalized in defined_symbols:
        return True
    normalized_head = normalized.split(".", 1)[0]
    if normalized_head in import_aliases or normalized in import_aliases.values():
        return True

    return any(
        value == normalized or normalized.startswith(f"{value}.")
        for value in import_aliases.values()
    )


def _collect_pseudocode_tokens(steps: Iterable[object]) -> set[str]:
    """Collect identifier-like tokens from pseudocode lines for behavior checks.

    Intent
    ------
    Expose the collect pseudocode tokens step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps collect pseudocode tokens behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set collect_pseudocode_tokens_inputs = received_context
    - return collect_pseudocode_tokens_inputs

    Wraps
    -----
    - none
    """
    tokens: set[str] = set()
    for step in steps:
        raw = getattr(step, "text", "")
        if not isinstance(raw, str):
            continue
        for match in _PSEUDOCODE_TOKEN_RE.finditer(raw):
            token = match.group(1).strip()
            if not token:
                continue
            if token.lower() in _PSEUDOCODE_SKIP_TOKENS:
                continue
            tokens.add(token)
    return tokens


def _collect_explicit_dependency_refs(parsed: object) -> set[str]:
    """Collect canonical dependency markers from parser output.

    Intent
    ------
    Expose the collect explicit dependency refs step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps collect explicit dependency refs behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set collect_explicit_dependency_refs_inputs = received_context
    - return collect_explicit_dependency_refs_inputs

    Wraps
    -----
    - none
    """
    refs: set[str] = set()
    for raw in getattr(parsed, "pseudocode_dependency_refs", ()):
        value = (str(raw) if raw is not None else "").strip()
        if value:
            refs.add(value)
    return refs


def _name_matches_observed(
    name: str,
    observed: set[str],
    import_aliases: Mapping[str, str],
) -> bool:
    """Match a name against a set of observed calls using import-aware equivalence.

    Intent
    ------
    Expose the name matches observed step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps name matches observed behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set name_matches_observed_inputs = received_context
    - set name_matches_observed_effects = local_decisions
    - return name_matches_observed_effects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._dependency_matches:
      why:
        computes: "dependency matches supplies repo-local behavior used by name matches observed; this edge is documented from an observed call in the body."
    """
    for candidate in observed:
        if _dependency_matches(name, candidate, import_aliases):
            return True
    return False


def _name_mentioned_in_pseudocode(
    name: str,
    pseudocode_refs: set[str] | list[str],
    import_aliases: Mapping[str, str],
    *,
    scope: str | None = None,
) -> bool:
    """Check whether pseudocode names a dependency through any accepted shorthand.

    Intent
    ------
    Resolve compact pseudocode markers against observed dependency names, imported
    aliases, and optional section scope.

    Rationale
    ---------
    Authors should be able to write concise markers such as `@load_policy(...)`
    while the validator still proves the marker corresponds to a declared repo edge.

    Pseudocode
    ----------
    - for dependency_ref in pseudocode_refs:
      - explicit_ref = ._split_explicit_ref(dependency_ref)
      - candidate_names = @._dependency_name_variants(explicit_ref)
      - if candidate_names mention name:
        - return true
    - return false

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._dependency_name_variants:
      why:
        computes: "Expands scoped and compact dependency name forms before matching pseudocode markers."

    InstantiationsFromRepo
    ----------------------
    ._split_explicit_ref:
      why:
        constructs: "Builds the optional section/name pair used to filter scoped pseudocode markers."

    ._dependency_name_variants:
      why:
        constructs: "Builds the dependency_name_variants contribution used by _name_mentioned_in_pseudocode."
    """
    references = set(pseudocode_refs)
    if not references:
        return False

    candidate_variants = set(_dependency_name_variants(name, import_aliases))
    for ref in references:
        if ":" in ref:
            ref_scope, ref_name = _split_explicit_ref(ref)
            if scope is not None and ref_scope is not None and ref_scope != scope:
                continue
            if not ref_name:
                continue
            if scope is not None and ref_scope is not None and ref_scope == scope:
                if ref_name in candidate_variants:
                    return True
            if scope is None:
                if ref_name in candidate_variants:
                    return True
            continue

        if ref in candidate_variants:
            return True

    for variant in _dependency_name_variants(name, import_aliases):
        if variant in references:
            return True
    return False


def _split_explicit_ref(ref: str) -> tuple[str | None, str]:
    """Split ``section:name`` to scope + identifier if present.

    Intent
    ------
    Expose the split explicit ref step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps split explicit ref behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set split_explicit_ref_inputs = received_context
    - return split_explicit_ref_inputs

    Wraps
    -----
    - none
    """
    raw = (ref or "").strip()
    if ":" not in raw:
        return None, raw
    section, name = [part.strip() for part in raw.split(":", 1)]
    if not section or not name:
        return None, raw
    return section, name


@dataclass(frozen=True)
class DocstringValidationIssue:
    """Structured finding emitted by module docstring validation.

    Intent
    ------
    Carry a stable check code, source path, node id, line number, severity, and human
    message for one docstring quality issue.

    Rationale
    ---------
    Validation results need a simple immutable record so CLI output, tests, and
    editor integrations can consume the same diagnostic shape.

    Pseudocode
    ----------
    - set issue_contract = declared diagnostic fields
    - return issue_contract

    Wraps
    -----
    - none"""

    path: Path
    code: str
    message: str
    line: int | None = None
    node_id: str | None = None
    severity: str = "warning"


_FALLBACK_SYNTAX_CHECK_CODES: frozenset[str] = frozenset(
    {
        "docstring.formatting",
        "docstring.empty",
        "docstring.invalid-owns",
        "docstring.owns-too-many",
        "docstring.invalid-edge",
        "docstring.invalid-section",
        "docstring.invalid-section-header",
        "docstring.duplicate-section",
        "docstring.invalid-wraps",
        "docstring.invalid-module-dependency",
        "docstring.invalid-resource",
        "docstring.invalid-dataflow",
        "docstring.absolute-dependency-not-allowed",
        "docstring.invalid-pseudocode",
        "docstring.pseudocode-control-empty",
        "docstring.pseudocode-else-unmatched",
        "docstring.pseudocode-loop-control-outside-loop",
        "docstring.pseudocode-ref-unresolved",
        "docstring.pseudocode-ref-ambiguous",
        "docstring.pseudocode-resource-unresolved",
        "docstring.pseudocode-placeholder-variable",
        "docstring.pseudocode-placeholder-argument",
        "docstring.pseudocode-output-unused",
        "docstring.pseudocode-step-min",
        "docstring.pseudocode-step-length",
        "docstring.pseudocode-step-cap",
        "docstring.pseudocode-total-length",
        "docstring.section-missing",
        "docstring.summary-missing",
        "docstring.summary-forbidden",
        "docstring.summary-length",
        "docstring.intent-forbidden",
        "docstring.rationale-forbidden",
        "docstring.rationale-length",
        "docstring.pseudocode-forbidden",
        "docstring.dependency-why-forbidden",
        "docstring.dependency-why-action",
        "docstring.instantiation-why-action",
        "docstring.instantiation-product-unshown",
        "docstring.dependency-section-overlap",
        "docstring.repeated-template",
        "docstring.missing-graphpipeline",
    }
)

_FALLBACK_BEHAVIORAL_CHECK_CODES: frozenset[str] = frozenset(
    {
        "docstring.invalid-wraps",
        "docstring.missing",
        "docstring.module-missing",
        "docstring.unknown-owner",
        "docstring.crossfile-owner-disabled",
        "docstring.module-dependency-not-observed",
        "docstring.module-dependency-undocumented",
        "docstring.module-dependency-unresolved",
        "docstring.dispatch-not-observed",
        "docstring.dispatch-undocumented",
        "docstring.dispatch-unresolved",
        "docstring.absolute-dependency-not-allowed",
        "docstring.repo-dependency-not-repo",
        "docstring.wraps-missing-thin-wrapper",
        "docstring.single-repo-call-review",
        "docstring.instantiation-why-action",
        "docstring.instantiation-product-unshown",
        "docstring.dependency-section-overlap",
        "docstring.wraps-incomplete",
        "docstring.wraps-unresolved-target",
        "docstring.pseudocode-dependency-missing",
        "docstring.pseudocode-dependency-undocumented",
        "docstring.owning-single",
    }
)


def _resolve_check_group_codes(check_group: str) -> tuple[str, ...]:
    """Resolve checker scope from policy if present, else use fallback buckets.

    Intent
    ------
    Expose the resolve check group codes step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps resolve check group codes behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set resolve_check_group_codes_inputs = received_context
    - return resolve_check_group_codes_inputs

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ..common.docstring.docstring_policy.load_docstring_check_categories:
      why:
        constructs: "Builds the catalog of syntax and behavioral check codes used to expand check groups."
    """
    categories = load_docstring_check_categories()
    if categories:
        group = check_group.strip().lower()
        if group in categories:
            return categories[group]
    if check_group == "syntax":
        return _FALLBACK_SYNTAX_CHECK_CODES
    if check_group == "behavioral":
        return _FALLBACK_BEHAVIORAL_CHECK_CODES
    return tuple(sorted(_FALLBACK_SYNTAX_CHECK_CODES | _FALLBACK_BEHAVIORAL_CHECK_CODES))


class _BaseDocstringChecker:
    """Base class for grouping validation checks.

    Intent
    ------
    Expose the BaseDocstringChecker step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps BaseDocstringChecker behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set BaseDocstringChecker_contract = declared_fields
    - return BaseDocstringChecker_contract

    Wraps
    -----
    - none
    """

    check_codes: frozenset[str] = frozenset()

    def __init__(self, check_codes: Iterable[str] | None = None) -> None:
        """Initialize a checker with the set of enabled docstring check codes.

        Intent
        ------
        Normalize optional check-code configuration into a frozen set used by all checker
        methods.

        Rationale
        ---------
        A shared base class keeps syntax and behavioral filtering consistent without
        reimplementing enabled-code handling in each checker.

        Pseudocode
        ----------
        - if check_codes is empty:
          - return
        - set enabled_check_codes = frozen configured check codes
        - return

        Wraps
        -----
        - none
        """
        if check_codes is None:
            self.check_codes = frozenset()
        else:
            self.check_codes = frozenset(code for code in check_codes if code)

    def _issue_in_scope(self, issue: DocstringValidationIssue) -> bool:
        """Return whether one diagnostic code is enabled for this checker.

        Intent
        ------
        Apply checker-local code filtering before a syntax or behavioral issue is emitted.

        Rationale
        ---------
        The validator supports syntax-only, behavioral-only, and all-check modes; this
        method keeps that filtering centralized for every checker subclass.

        Pseudocode
        ----------
        - if checker has no scoped code list:
          - return true
        - if issue code is in scoped code list:
          - return true
        - return false

        Wraps
        -----
        - none
        """
        return issue.code in self.check_codes

    def _filter_scoped(self, issues: Iterable[DocstringValidationIssue]) -> tuple[DocstringValidationIssue, ...]:
        """Return only diagnostics whose check codes are enabled for this checker.

        Intent
        ------
        Expose the filter scoped step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps filter scoped behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set filter_scoped_inputs = received_context
        - return filter_scoped_inputs

        Wraps
        -----
        - none
        """
        return tuple(issue for issue in issues if self._issue_in_scope(issue))


class SyntaxDocstringChecker(_BaseDocstringChecker):
    """Parser-backed checker for docstring structure and local syntax.

    Intent
    ------
    Run section, pseudocode, dependency-tree, rationale-action, text-quality, and
    wrapper-line checks that do not require inspecting function bodies.

    Rationale
    ---------
    Separating syntax checks from AST behavior checks lets tests and lightweight
    profiles validate format without requiring source-code dependency inference.

    Pseudocode
    ----------
    - set syntax_checker_contract = parser policy and enabled syntax check codes
    - return syntax_checker_contract

    Wraps
    -----
    - none
    """

    def __init__(self, check_codes: Iterable[str] | None = None) -> None:
        """Initialize checker-specific policy and enabled check-code state.

        Intent
        ------
        Expose the init step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps init behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set init_inputs = received_context
        - set init_effects = local_decisions
        - return init_effects

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._resolve_check_group_codes:
          why:
            computes: "resolve check group codes supplies repo-local behavior used by init; this edge is documented from an observed call in the body."
        """
        super().__init__(check_codes or _resolve_check_group_codes("syntax"))

    def validate_node(
        self,
        path: Path,
        node_id: str,
        node_type: str,
        node: ast.AST,
        schema_rules: DocstringSchema,
        ownership_aliases: list[str],
        ownership_index: Mapping[str, dict[str, str]],
        allow_cross_file_ownership: bool,
        import_aliases: Mapping[str, str],
        defined_symbols: set[str],
        shadowed_names: set[str],
    ) -> tuple[DocstringValidationIssue, ...]:
        """Validate one parsed callable docstring against this checker family.

        Intent
        ------
        Expose the validate node step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps validate node behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set validate_node_inputs = received_context
        - set validate_node_effects = local_decisions
        - return validate_node_effects

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._validate_node_docstring:
          why:
            computes: "validate node docstring supplies repo-local behavior used by validate node; this edge is documented from an observed call in the body."
        """
        return self._filter_scoped(
            _validate_node_docstring(
                path=path,
                node_id=node_id,
                node_type=node_type,
                node=node,
                schema_rules=schema_rules,
                ownership_aliases=ownership_aliases,
                ownership_index=ownership_index,
                allow_cross_file_ownership=allow_cross_file_ownership,
                import_aliases=import_aliases,
                defined_symbols=defined_symbols,
                shadowed_names=shadowed_names,
            )
        )


class BehavioralDocstringChecker(_BaseDocstringChecker):
    """AST-backed checker for documented behavior against source code.

    Intent
    ------
    Compare parsed docstring claims with observed calls, products, dispatches,
    wrappers, ownership declarations, and repeated prose patterns.

    Rationale
    ---------
    Graphable documentation is only useful when it tracks the implementation; this
    checker enforces that link using mechanical source analysis.

    Pseudocode
    ----------
    - set behavioral_checker_contract = AST policy and enabled behavior check codes
    - return behavioral_checker_contract

    Wraps
    -----
    - none
    """

    def __init__(self, check_codes: Iterable[str] | None = None) -> None:
        """Initialize checker-specific policy and enabled check-code state.

        Intent
        ------
        Expose the init step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps init behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set init_inputs = received_context
        - set init_effects = local_decisions
        - return init_effects

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._resolve_check_group_codes:
          why:
            computes: "resolve check group codes supplies repo-local behavior used by init; this edge is documented from an observed call in the body."
        """
        super().__init__(check_codes or _resolve_check_group_codes("behavioral"))

    def validate_node(
        self,
        path: Path,
        node_id: str,
        node_type: str,
        node: ast.AST,
        schema_rules: DocstringSchema,
        ownership_aliases: list[str],
        ownership_index: Mapping[str, dict[str, str]],
        allow_cross_file_ownership: bool,
        import_aliases: Mapping[str, str],
        defined_symbols: set[str],
        shadowed_names: set[str],
    ) -> tuple[DocstringValidationIssue, ...]:
        """Validate one parsed callable docstring against this checker family.

        Intent
        ------
        Expose the validate node step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps validate node behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set validate_node_inputs = received_context
        - set validate_node_effects = local_decisions
        - return validate_node_effects

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._validate_node_docstring:
          why:
            computes: "validate node docstring supplies repo-local behavior used by validate node; this edge is documented from an observed call in the body."
        """
        return self._filter_scoped(
            _validate_node_docstring(
                path=path,
                node_id=node_id,
                node_type=node_type,
                node= node,
                schema_rules=schema_rules,
                ownership_aliases=ownership_aliases,
                ownership_index=ownership_index,
                allow_cross_file_ownership=allow_cross_file_ownership,
                import_aliases=import_aliases,
                defined_symbols=defined_symbols,
                shadowed_names=shadowed_names,
            )
        )


def _should_require_docstring(node: ast.AST) -> bool:
    """All callables/classes require docstrings by default.

    Intent
    ------
    Expose the should require docstring step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps should require docstring behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set should_require_docstring_inputs = received_context
    - return should_require_docstring_inputs

    Wraps
    -----
    - none
    """
    del node
    return True


def _is_resolved_dataclasses_field(
    call: ast.Call,
    import_aliases: Mapping[str, str],
) -> bool:
    """Return whether a call target resolves to stdlib ``dataclasses.field``.

    Intent
    ------
    Permit the one active expression intrinsic to passive dataclass field declarations.

    Rationale
    ---------
    Name spelling alone is spoofable, so compact treatment must use the same import
    resolution that distinguishes stdlib decorators from project-local callables.

    Pseudocode
    ----------
    - target = _flatten_attribute_name(call)
    - if target is missing:
      - return false
    - return @_resolves_visible_import(target) for dataclasses.field

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._resolves_visible_import:
      why:
        computes: "Requires the field-call target to resolve through a visible exact stdlib import."

    InstantiationsFromRepo
    ----------------------
    ._flatten_attribute_name:
      why:
        constructs: "Builds the dotted field-call spelling used for alias resolution."
    """
    target = _flatten_attribute_name(call.func)
    return bool(
        target
        and _resolves_visible_import(
            target,
            "dataclasses.field",
            import_aliases,
        )
    )


def _resolves_visible_import(
    name: str,
    expected: str,
    import_aliases: Mapping[str, str],
) -> bool:
    """Return whether a visible import resolves a name to one exact symbol.

    Intent
    ------
    Reject qualified spellings that merely resemble trusted stdlib markers.

    Rationale
    ---------
    Normalization leaves unknown roots unchanged, so equality without an imported root
    would accept unimported or locally shadowed ``dataclasses`` objects.

    Pseudocode
    ----------
    - set root = first name segment
    - if root is not a visible import:
      - return false
    - return @_normalize_dependency_name(name) equals expected symbol

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._normalize_dependency_name:
      why:
        computes: "Resolves the visibly imported name before exact-symbol comparison."
    """
    root = name.split(".", 1)[0]
    if root not in import_aliases:
        return False
    return _normalize_dependency_name(name, import_aliases) == expected


def _is_passive_dataclass_field(
    statement: ast.stmt,
    import_aliases: Mapping[str, str],
) -> bool:
    """Return whether one class statement is an allowed annotated instance field.

    Intent
    ------
    Admit annotations, non-call defaults, and resolved ``dataclasses.field`` defaults.

    Rationale
    ---------
    Methods, descriptors, nested declarations, and arbitrary calls carry behavior that
    requires the full docstring profile rather than a summary-only structural marker.

    Pseudocode
    ----------
    - if statement is not a plain-name annotated assignment:
      - return false
    - if no default:
      - return true
    - set calls = call nodes in default expression
    - if no calls:
      - return true
    - return _is_resolved_dataclasses_field(sole_outer_call)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_resolved_dataclasses_field:
      why:
        computes: "Checks the only call-valued default permitted by compact dataclass policy."
    """
    if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
        return False
    if statement.value is None:
        return True
    calls = tuple(node for node in ast.walk(statement.value) if isinstance(node, ast.Call))
    if not calls:
        return True
    return (
        len(calls) == 1
        and calls[0] is statement.value
        and _is_resolved_dataclasses_field(calls[0], import_aliases)
    )


def _compact_structural_kind(
    node: ast.AST,
    import_aliases: Mapping[str, str],
    defined_symbols: set[str],
    shadowed_names: set[str],
    enabled_kinds: tuple[str, ...],
) -> str | None:
    """Classify only the two policy-approved passive structural declarations.

    Intent
    ------
    Select summary-only treatment for exact stdlib dataclasses and builtin exception
    markers while rejecting every executable or spoofable near miss.

    Rationale
    ---------
    Compact docstrings remove behavioral explanation, so false positives are more costly
    than leaving a structural class on the full profile.

    Pseudocode
    ----------
    - if node is not a class:
      - return no kind
    - body = _statements_without_docstring(class_body)
    - if stdlib dataclass kind is enabled and statement-ordered fields qualify:
      - return stdlib dataclass kind
    - if unambiguous builtin exception base and passive body qualify:
      - return builtin exception kind
    - return no kind

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_passive_dataclass_field:
      why:
        computes: "Checks every dataclass body statement against the passive-field boundary."
    ._resolves_visible_import:
      why:
        computes: "Requires the decorator target to resolve through a visible exact stdlib import."

    InstantiationsFromRepo
    ----------------------
    ._flatten_attribute_name:
      why:
        constructs: "Builds the decorator spelling used for stdlib alias resolution."
    ._statements_without_docstring:
      why:
        constructs: "Builds the structural class body after removing its summary expression."
    """
    if not isinstance(node, ast.ClassDef):
        return None
    body = _statements_without_docstring(node.body)

    if "stdlib_dataclass" in enabled_kinds and len(node.decorator_list) == 1:
        decorator_node = node.decorator_list[0]
        decorator_target = (
            decorator_node.func if isinstance(decorator_node, ast.Call) else decorator_node
        )
        decorator = _flatten_attribute_name(decorator_target)
        if (
            decorator
            and _resolves_visible_import(
                decorator,
                "dataclasses.dataclass",
                import_aliases,
            )
            and not node.bases
            and not node.keywords
        ):
            field_aliases = dict(import_aliases)
            for statement in body:
                if not _is_passive_dataclass_field(statement, field_aliases):
                    break
                if isinstance(statement, ast.AnnAssign) and isinstance(
                    statement.target,
                    ast.Name,
                ):
                    field_aliases.pop(statement.target.id, None)
            else:
                return "stdlib_dataclass"

    if (
        "builtin_exception" in enabled_kinds
        and not node.decorator_list
        and len(node.bases) == 1
        and not node.keywords
        and isinstance(node.bases[0], ast.Name)
        and node.bases[0].id in _BUILTIN_EXCEPTION_NAMES
        and node.bases[0].id not in import_aliases
        and node.bases[0].id not in defined_symbols
        and node.bases[0].id not in shadowed_names
        and "*" not in import_aliases
        and all(
            isinstance(statement, ast.Pass)
            or (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and statement.value.value is Ellipsis
            )
            for statement in body
        )
    ):
        return "builtin_exception"

    return None


def _schema_for_node(
    node: ast.AST,
    schema_rules: DocstringSchema,
    import_aliases: Mapping[str, str],
    defined_symbols: set[str],
    shadowed_names: set[str],
) -> DocstringSchema:
    """Return node policy with only approved compact section waivers applied.

    Intent
    ------
    Preserve the full callable contract unless AST classification proves a configured
    passive structural kind.

    Rationale
    ---------
    Deriving a temporary policy keeps parser enforcement unchanged and prevents compact
    exemptions from leaking to neighboring declarations.

    Pseudocode
    ----------
    - if @_compact_structural_kind(node) is missing:
      - return original policy
    - set required_sections = required sections minus compact waivers
    - set pseudocode_minimum = zero
    - return derived node policy

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._compact_structural_kind:
      why:
        computes: "Checks the conservative structural classification controlling section waivers."
    """
    if _compact_structural_kind(
        node,
        import_aliases,
        defined_symbols,
        shadowed_names,
        schema_rules.callable.compact_structural_kinds,
    ) is None:
        return schema_rules
    callable_rules = replace(
        schema_rules.callable,
        required_sections=tuple(
            section
            for section in schema_rules.callable.required_sections
            if section not in _COMPACT_SECTION_WAIVERS
        ),
        optional_sections=tuple(
            dict.fromkeys(
                (*schema_rules.callable.optional_sections, *_COMPACT_SECTION_WAIVERS)
            )
        ),
        min_pseudocode_steps=0,
        pseudocode=replace(schema_rules.callable.pseudocode, min_steps=0),
    )
    return replace(schema_rules, callable=callable_rules)


def _iter_defined_callables(
    tree: ast.AST,
    include_nested: bool = True,
    include_private: bool = True,
) -> Iterable[tuple[ast.AST, str, str]]:
    """Yield callable/class nodes with fully qualified IDs.

    Intent
    ------
    Expose the iter defined callables step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps iter defined callables behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set iter_defined_callables_inputs = received_context
    - return iter_defined_callables_inputs

    Wraps
    -----
    - none
    """

    def walk(parent_nodes: list[str], body: list[ast.stmt]) -> Iterable[tuple[ast.AST, str, str]]:
        """Recursively yield callable and class nodes with stable dotted ids.

        Intent
        ------
        Expose the walk step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps walk behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set walk_inputs = received_context
        - return walk_inputs

        Wraps
        -----
        - none
        """
        for node in body:
            if isinstance(node, ast.ClassDef):
                node_id = ".".join([*parent_nodes, node.name])
                is_private = not include_private and node.name.startswith("_")
                if not is_private:
                    yield node, node_id, "class"
                if include_nested:
                    yield from walk([*parent_nodes, node.name], node.body)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node_id = ".".join([*parent_nodes, node.name])
                is_private = not include_private and node.name.startswith("_")
                if not is_private:
                    yield node, node_id, "method" if parent_nodes else "function"
                if include_nested:
                    yield from walk([*parent_nodes, node.name], node.body)

    yield from walk([], tree.body)


def _collect_import_aliases(
    tree: ast.AST,
) -> tuple[dict[str, str], bool]:
    """Collect import aliases used to recognize repo-local dependency calls.

    Intent
    ------
    Map imported names and aliases to their logical module paths, preserving leading
    dots for relative imports.

    Rationale
    ---------
    Behavioral dependency checks rely on this map to distinguish repo imports from
    stdlib or third-party calls without forcing every docstring to use physical paths.

    Pseudocode
    ----------
    - for import_statement in module_imports:
      - set alias_target = imported module path
      - if import_statement is relative:
        - set alias_target = leading dots plus alias_target
      - set aliases = aliases plus imported name mapping
    - return aliases

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_lexical_scope_nodes:
      why:
        computes: "Streams module-scope imports while excluding nested declarations."

    InstantiationsFromRepo
    ----------------------
    ._scope_import_aliases:
      why:
        constructs: "Builds the module alias map with top-level shadowing applied."
    """
    aliases = _scope_import_aliases(tree.body, inherited={})
    has_wildcard_import = any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "*" for alias in node.names)
        for node in _iter_lexical_scope_nodes(tree.body)
    )
    return aliases, has_wildcard_import


def _iter_lexical_scope_nodes(body: Iterable[ast.stmt]) -> Iterable[ast.AST]:
    """Yield nodes in one lexical scope without entering nested callables or classes.

    Intent
    ------
    Keep import and dependency analysis aligned with Python lexical visibility.

    Rationale
    ---------
    ``ast.walk`` crosses nested definitions and makes sibling or child bindings appear
    available to parents, which invents dependency edges that source execution cannot use.

    Pseudocode
    ----------
    - set stack = scope statements
    - while stack has nodes:
      - set current = next stack node
      - if current opens a nested scope:
        - continue
      - set stack = stack plus child nodes
    - return scope node stream

    Wraps
    -----
    - none
    """
    stack: list[ast.AST] = list(reversed(tuple(body)))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(
            current,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            continue
        stack.extend(reversed(tuple(ast.iter_child_nodes(current))))


def _bound_target_names(target: ast.AST) -> set[str]:
    """Return plain names bound by one assignment-like target.

    Intent
    ------
    Identify bindings that shadow inherited import aliases in a lexical scope.

    Rationale
    ---------
    Tuple unpacking and starred targets bind several names, so alias invalidation must
    traverse target structure without treating attribute or subscription writes as binds.

    Pseudocode
    ----------
    - if target is a name:
      - return its identifier
    - if target unpacks values:
      - return names from each child target
    - return no names

    Wraps
    -----
    - none
    """
    names: set[str] = set()
    stack: list[ast.AST] = [target]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Name):
            names.add(current.id)
        elif isinstance(current, ast.Starred):
            stack.append(current.value)
        elif isinstance(current, (ast.Tuple, ast.List)):
            stack.extend(current.elts)
    return names


def _scope_binding_names(node: ast.AST) -> set[str]:
    """Return non-import names bound by one node in its containing scope.

    Intent
    ------
    Keep alias invalidation and compact-class shadow tracking on one binding model.

    Rationale
    ---------
    Assignments are not the only lexical binders: deletes and structural-pattern
    captures also make imported or builtin spellings unsafe to trust.

    Pseudocode
    ----------
    - if node declares a callable or class:
      - return its name
    - if node binds assignment-like targets:
      - return ._bound_target_names(targets)
    - if node captures a structural-pattern name:
      - return captured names
    - return empty names

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._bound_target_names:
      why:
        computes: "Collects names bound or deleted through assignment-shaped targets."

    InstantiationsFromRepo
    ----------------------
    ._bound_target_names:
      why:
        constructs: "Builds target-name sets returned for direct assignment-shaped binders."
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {
            name
            for target in node.targets
            for name in _bound_target_names(target)
        }
    if isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        return _bound_target_names(node.target)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return _bound_target_names(node.target)
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return {
            name
            for item in node.items
            if item.optional_vars is not None
            for name in _bound_target_names(item.optional_vars)
        }
    if isinstance(node, ast.ExceptHandler) and node.name:
        return {node.name}
    if isinstance(node, ast.Delete):
        return {
            name
            for target in node.targets
            for name in _bound_target_names(target)
        }
    if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
        return {node.name}
    if isinstance(node, ast.MatchMapping) and node.rest:
        return {node.rest}
    return set()


def _scope_import_aliases(
    body: Iterable[ast.stmt],
    inherited: Mapping[str, str],
    *,
    arguments: ast.arguments | None = None,
) -> dict[str, str]:
    """Merge imports for one scope while removing aliases shadowed by local bindings.

    Intent
    ------
    Resolve module and enclosing-function imports exactly where a callable may use them.

    Rationale
    ---------
    Python function bindings are scope-wide: current imports replace inherited aliases,
    while parameters, assignments, and declarations make inherited aliases unavailable.

    Pseudocode
    ----------
    - set aliases = inherited aliases
    - set current_imports = imports in current scope
    - set shadowed_names = non-import bindings and parameters
    - set aliases = aliases plus current imports minus shadowed names
    - return effective aliases

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_lexical_scope_nodes:
      why:
        computes: "Streams only nodes belonging to the current lexical scope."

    InstantiationsFromRepo
    ----------------------
    ._scope_binding_names:
      why:
        constructs: "Builds every non-import binder that invalidates an inherited alias."
    """
    aliases = dict(inherited)
    local_imports: dict[str, str] = {}
    shadowed: set[str] = set()
    if arguments is not None:
        shadowed.update(argument.arg for argument in arguments.posonlyargs)
        shadowed.update(argument.arg for argument in arguments.args)
        shadowed.update(argument.arg for argument in arguments.kwonlyargs)
        if arguments.vararg is not None:
            shadowed.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            shadowed.add(arguments.kwarg.arg)

    for current in _iter_lexical_scope_nodes(body):
        if isinstance(current, ast.Import):
            for alias in current.names:
                key = (alias.asname or alias.name).split(".")[0]
                local_imports[key] = alias.name
            continue
        if isinstance(current, ast.ImportFrom):
            module_name = current.module or ""
            if not module_name and current.level <= 0:
                continue
            if current.level > 0:
                module_name = f"{'.' * current.level}{module_name}"
            for alias in current.names:
                if alias.name == "*":
                    continue
                local_imports[alias.asname or alias.name] = f"{module_name}.{alias.name}"
            continue
        shadowed.update(_scope_binding_names(current))

    aliases.update(local_imports)
    for name in shadowed:
        aliases.pop(name, None)
    return aliases


def _scope_nonimport_bound_names(
    body: Iterable[ast.stmt],
    *,
    arguments: ast.arguments | None = None,
) -> set[str]:
    """Collect non-import bindings that shadow enclosing or builtin names.

    Intent
    ------
    Preserve lexical non-import bindings for conservative structural classification.

    Rationale
    ---------
    Import maps alone cannot distinguish a builtin exception marker from a parameter or
    assignment, deletion, or pattern capture with the same spelling in an enclosing scope.

    Pseudocode
    ----------
    - set bound_names = parameter names
    - for node in current lexical scope:
      - set bound_names = bound_names plus ._scope_binding_names(node)
    - return bound names

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_lexical_scope_nodes:
      why:
        computes: "Streams current-scope bindings without entering child declarations."

    InstantiationsFromRepo
    ----------------------
    ._scope_binding_names:
      why:
        constructs: "Builds declarations, targets, deletes, and pattern captures in the scope."
    """
    bound: set[str] = set()
    if arguments is not None:
        bound.update(argument.arg for argument in arguments.posonlyargs)
        bound.update(argument.arg for argument in arguments.args)
        bound.update(argument.arg for argument in arguments.kwonlyargs)
        if arguments.vararg is not None:
            bound.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            bound.add(arguments.kwarg.arg)

    for current in _iter_lexical_scope_nodes(body):
        if isinstance(current, (ast.Import, ast.ImportFrom)):
            continue
        bound.update(_scope_binding_names(current))
    return bound


def _collect_callable_import_aliases(
    tree: ast.Module,
    module_aliases: Mapping[str, str],
) -> tuple[dict[ast.AST, dict[str, str]], dict[ast.AST, set[str]]]:
    """Map each callable or class to imports visible in its lexical scope.

    Intent
    ------
    Supply node-specific resolution maps for nested dependency and decorator checks.

    Rationale
    ---------
    Functions inherit enclosing-function imports, but sibling and class-body imports are
    not lexical parents; retaining that distinction prevents both missed and invented edges.

    Pseudocode
    ----------
    - set aliases_by_node = empty mapping
    - set shadows_by_node = empty mapping
    - for declaration in module declarations:
      - if declaration is a function:
        - set function_aliases = lexical aliases including function scope
      - else:
        - set class_aliases = lexical parent aliases
    - return node alias and non-import shadow maps

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._scope_nonimport_bound_names:
      why:
        constructs: "Builds lexical shadow sets carried beside each callable alias map."

    """
    aliases_by_node: dict[ast.AST, dict[str, str]] = {}
    shadows_by_node: dict[ast.AST, set[str]] = {}
    module_shadows = _scope_nonimport_bound_names(tree.body)

    def walk(
        body: Iterable[ast.stmt],
        inherited: Mapping[str, str],
        inherited_shadows: set[str],
        *,
        class_namespace: bool = False,
    ) -> None:
        """Populate callable alias maps without treating class scope as lexical.

        Intent
        ------
        Recurse through nested declarations with the correct enclosing-function aliases.

        Rationale
        ---------
        Methods may close over an enclosing function but never inherit class locals, while
        nested declaration headers execute after earlier class statements. Recursion must
        carry lexical and statement-ordered execution contexts separately.

        Pseudocode
        ----------
        - for declaration in body:
          - if declaration is a function:
            - aliases = _scope_import_aliases(function_body)
            - set shadows = inherited shadows plus @_scope_nonimport_bound_names(function_body)
          - else:
            - if declaration is a class:
              - set aliases = current execution aliases
          - if walking a class namespace:
            - set execution_aliases = remaining aliases after statement bindings

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        ._scope_import_aliases:
          why:
            constructs: "Builds aliases visible in each function before descending further."
        ._scope_nonimport_bound_names:
          why:
            constructs: "Builds statement binding sets used after class-body evaluation."

        CallsFromRepo
        -------------
        ._scope_nonimport_bound_names:
          why:
            computes: "Builds lexical shadow sets for nested compact-class classification."
        """
        execution_aliases = dict(inherited)
        execution_shadows = set(inherited_shadows)
        for current in body:
            declaration_aliases = execution_aliases if class_namespace else inherited
            declaration_shadows = (
                execution_shadows if class_namespace else inherited_shadows
            )
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                effective = _scope_import_aliases(
                    current.body,
                    inherited,
                    arguments=current.args,
                )
                local_imports = _scope_import_aliases(
                    current.body,
                    {},
                    arguments=current.args,
                )
                effective_shadows = (
                    inherited_shadows - set(local_imports)
                ) | _scope_nonimport_bound_names(
                    current.body,
                    arguments=current.args,
                )
                aliases_by_node[current] = effective
                shadows_by_node[current] = effective_shadows
                walk(current.body, effective, effective_shadows)
            elif isinstance(current, ast.ClassDef):
                aliases_by_node[current] = dict(declaration_aliases)
                shadows_by_node[current] = set(declaration_shadows)
                walk(
                    current.body,
                    inherited,
                    inherited_shadows,
                    class_namespace=True,
                )
            if class_namespace:
                statement_bindings = _scope_nonimport_bound_names((current,))
                statement_bindings.update(
                    _scope_import_aliases((current,), inherited={})
                )
                for name in statement_bindings:
                    execution_aliases.pop(name, None)
                execution_shadows.update(statement_bindings)

    walk(tree.body, module_aliases, module_shadows)
    return aliases_by_node, shadows_by_node


def _collect_defined_symbols(tree: ast.AST) -> set[str]:
    """Collect top-level callables, classes, and shadowed exception markers.

    Intent
    ------
    Expose the collect defined symbols step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps collect defined symbols behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set collect_defined_symbols_inputs = received_context
    - return collect_defined_symbols_inputs

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_lexical_scope_nodes:
      why:
        computes: "Streams module-scope bindings without nested-scope leakage."

    ._bound_target_names:
      why:
        computes: "Computes assignment-target names used to detect shadowed exception markers."
    """
    symbols: set[str] = set()
    for node in _iter_lexical_scope_nodes(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                symbols.update(_bound_target_names(target) & _BUILTIN_EXCEPTION_NAMES)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            symbols.update(_bound_target_names(node.target) & _BUILTIN_EXCEPTION_NAMES)
    return symbols


def _flatten_attribute_name(node: ast.AST) -> str | None:
    """Convert a call target expression into a dotted symbol name.

    Intent
    ------
    Expose the flatten attribute name step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps flatten attribute name behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set flatten_attribute_name_inputs = received_context
    - set flatten_attribute_name_products = carried_outputs
    - return flatten_attribute_name_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._flatten_attribute_name:
      why:
        constructs: "flatten attribute name produces a value carried by flatten attribute name; this edge is documented from the observed product position in the body."
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _flatten_attribute_name(node.value)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"
    return None


def _normalize_dependency_name(
    name: str,
    import_aliases: Mapping[str, str],
) -> str:
    """Resolve a dotted dependency name through import alias mappings.

    Intent
    ------
    Expose the normalize dependency name step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps normalize dependency name behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set normalize_dependency_name_inputs = received_context
    - set normalize_dependency_name_products = carried_outputs
    - return normalize_dependency_name_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._relative_dependency_tail:
      why:
        constructs: "relative dependency tail produces a value carried by normalize dependency name; this edge is documented from the observed product position in the body."
    """
    relative = _relative_dependency_tail(name)
    if relative:
        name = relative
    parts = name.split(".")
    if not parts:
        return name
    head = parts[0]
    resolved = import_aliases.get(head, head)
    if resolved == head:
        return name
    if len(parts) == 1:
        return resolved
    return f"{resolved}.{'.'.join(parts[1:])}"


def _dependency_matches(
    declared: str,
    observed: str,
    import_aliases: Mapping[str, str],
) -> bool:
    """Return ``True`` when declared and observed identifiers refer to the same symbol.

    Intent
    ------
    Expose the dependency matches step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps dependency matches behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set dependency_matches_inputs = received_context
    - set dependency_matches_products = carried_outputs
    - return dependency_matches_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._dependency_name_variants:
      why:
        constructs: "dependency name variants produces a value carried by dependency matches; this edge is documented from the observed product position in the body."
    """
    if declared == observed:
        return True
    declared_variants = set(_dependency_name_variants(declared, import_aliases))
    observed_variants = set(_dependency_name_variants(observed, import_aliases))
    return bool(declared_variants & observed_variants)


def _declared_dependency_targets_match(
    left: str,
    right: str,
    import_aliases: Mapping[str, str],
) -> bool:
    """Return ``True`` when two documented dependency leaves name the same target.

    Intent
    ------
    Expose the declared dependency targets match step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps declared dependency targets match behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set declared_dependency_targets_match_inputs = received_context
    - set declared_dependency_targets_match_effects = local_decisions
    - return declared_dependency_targets_match_effects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._normalize_dependency_name:
      why:
        computes: "normalize dependency name supplies repo-local behavior used by declared dependency targets match; this edge is documented from an observed call in the body."
    """
    return _normalize_dependency_name(left, import_aliases) == _normalize_dependency_name(
        right,
        import_aliases,
    )


def _ast_parent_map(root: ast.AST) -> dict[ast.AST, ast.AST]:
    """Return child-to-parent AST links for result-position checks.

    Intent
    ------
    Expose the ast parent map step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps ast parent map behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set ast_parent_map_inputs = received_context
    - return ast_parent_map_inputs

    Wraps
    -----
    - none
    """
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(root):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _is_wrapped_call_node(node: ast.AST, call: ast.Call) -> bool:
    """Return true when ``node`` is the call or a simple await of the call.

    Intent
    ------
    Expose the is wrapped call node step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps is wrapped call node behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set is_wrapped_call_node_inputs = received_context
    - return is_wrapped_call_node_inputs

    Wraps
    -----
    - none
    """
    if node is call:
        return True
    return isinstance(node, ast.Await) and node.value is call


def _call_result_is_product_position(
    call: ast.Call,
    parents: Mapping[ast.AST, ast.AST],
    *,
    import_aliases: Mapping[str, str],
    defined_symbols: set[str],
    allowed_abs: tuple[str, ...],
) -> bool:
    """Return true when a call result is carried as a semantic product.

    Intent
    ------
    Expose the call result is product position step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps call result is product position behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set call_result_is_product_position_inputs = received_context
    - set call_result_is_product_position_effects = local_decisions
    - return call_result_is_product_position_effects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._flatten_attribute_name:
      why:
        computes: "flatten attribute name supplies repo-local behavior used by call result is product position; this edge is documented from an observed call in the body."
    ._is_wrapped_call_node:
      why:
        computes: "is wrapped call node supplies repo-local behavior used by call result is product position; this edge is documented from an observed call in the body."
    ._observed_call_is_repo_dependency:
      why:
        computes: "Checks whether an outer positional or keyword consumer is repo-local."

    InstantiationsFromRepo
    ----------------------
    ._normalize_dependency_name:
      why:
        constructs: "Builds normalized outer-consumer names before repo-local classification."
    """
    parent = parents.get(call)
    value_node: ast.AST = call
    if isinstance(parent, ast.Await):
        value_node = parent
        parent = parents.get(parent)

    while True:
        if isinstance(parent, (ast.Tuple, ast.List, ast.Set, ast.Dict, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            value_node = parent
            parent = parents.get(parent)
            continue
        if isinstance(parent, ast.Call) and value_node in tuple(parent.args):
            wrapper = _flatten_attribute_name(parent.func) or ""
            wrapper_tail = wrapper.rsplit(".", 1)[-1]
            if wrapper_tail in {"tuple", "list", "set", "frozenset", "dict"}:
                value_node = parent
                parent = parents.get(parent)
                continue
            normalized_wrapper = _normalize_dependency_name(wrapper, import_aliases)
            if _observed_call_is_repo_dependency(
                normalized_wrapper,
                import_aliases=import_aliases,
                defined_symbols=defined_symbols,
                allowed_abs=allowed_abs,
            ):
                return True
        if isinstance(parent, ast.keyword) and parent.value is value_node:
            call_parent = parents.get(parent)
            if isinstance(call_parent, ast.Call):
                constructor = _flatten_attribute_name(call_parent.func) or ""
                normalized_constructor = _normalize_dependency_name(
                    constructor,
                    import_aliases,
                )
                if _observed_call_is_repo_dependency(
                    normalized_constructor,
                    import_aliases=import_aliases,
                    defined_symbols=defined_symbols,
                    allowed_abs=allowed_abs,
                ):
                    return True
        break

    if isinstance(parent, ast.Return) and (
        parent.value is value_node or _is_wrapped_call_node(parent.value, call)
    ):
        return True
    if isinstance(parent, ast.Raise) and (
        parent.exc is value_node or _is_wrapped_call_node(parent.exc, call)
    ):
        return True
    if isinstance(parent, ast.Yield) and (
        parent.value is value_node or _is_wrapped_call_node(parent.value, call)
    ):
        return True
    if isinstance(parent, ast.Assign) and (
        parent.value is value_node or _is_wrapped_call_node(parent.value, call)
    ):
        return True
    if isinstance(parent, ast.AnnAssign) and (
        parent.value is value_node or _is_wrapped_call_node(parent.value, call)
    ):
        return True
    if isinstance(parent, ast.NamedExpr) and (
        parent.value is value_node or _is_wrapped_call_node(parent.value, call)
    ):
        return True

    if isinstance(parent, ast.Call) and value_node in tuple(parent.args):
        collector = _flatten_attribute_name(parent.func) or ""
        collector_tail = collector.rsplit(".", 1)[-1]
        return collector_tail in {"append", "extend", "insert", "add", "update", "setdefault"}

    if isinstance(parent, ast.keyword) and parent.value is value_node:
        call_parent = parents.get(parent)
        if isinstance(call_parent, ast.Call):
            collector = _flatten_attribute_name(call_parent.func) or ""
            collector_tail = collector.rsplit(".", 1)[-1]
            return collector_tail in {"append", "extend", "insert", "add", "update", "setdefault"}

    return False


def _collect_dependency_targets(
    node: ast.AST,
    defined_symbols: set[str],
    import_aliases: Mapping[str, str],
    dependency_rules: ModuleDependencyConfig,
) -> tuple[set[str], set[str]]:
    """Collect observed module calls and instantiations from one callable node.

    Intent
    ------
    Expose the collect dependency targets step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps collect dependency targets behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set collect_dependency_targets_inputs = received_context
    - set collect_dependency_targets_products = carried_outputs
    - return collect_dependency_targets_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._call_result_is_product_position:
      why:
        computes: "call result is product position supplies repo-local behavior used by collect dependency targets; this edge is documented from an observed call in the body."
    ._observed_call_is_repo_dependency:
      why:
        computes: "observed call is repo dependency supplies repo-local behavior used by collect dependency targets; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    ._ast_parent_map:
      why:
        constructs: "ast parent map produces a value carried by collect dependency targets; this edge is documented from the observed product position in the body."
    ._flatten_attribute_name:
      why:
        constructs: "flatten attribute name produces a value carried by collect dependency targets; this edge is documented from the observed product position in the body."
    ._normalize_dependency_name:
      why:
        constructs: "normalize dependency name produces a value carried by collect dependency targets; this edge is documented from the observed product position in the body."
    ._iter_lexical_scope_nodes:
      why:
        constructs: "Builds the node stream for the callable body without nested-scope absorption."
    """
    calls: set[str] = set()
    instantiations: set[str] = set()
    has_calls = bool(dependency_rules.calls_section)
    has_instantiations = bool(dependency_rules.instantiates_section)
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        walk_nodes: Iterable[ast.AST] = _iter_lexical_scope_nodes(node.body)
    else:
        walk_nodes = ast.walk(node)
    parents = _ast_parent_map(node)

    for statement in walk_nodes:
            if not isinstance(statement, ast.Call):
                continue

            target_name = _flatten_attribute_name(statement.func)
            if target_name is None:
                continue

            target_name = target_name.strip()
            if not target_name:
                continue

            base = target_name.split(".", 1)[0]
            is_local_defined = base in defined_symbols
            if (
                not base
                or base in _IGNORED_CALL_BASES
                or base in _BUILTIN_SYMBOLS
            ):
                continue

            if dependency_rules.ignore_non_external and base not in import_aliases and not is_local_defined:
                continue

            normalized = _normalize_dependency_name(target_name, import_aliases)
            if not _observed_call_is_repo_dependency(
                normalized,
                import_aliases=import_aliases,
                defined_symbols=defined_symbols,
                allowed_abs=dependency_rules.allowed_abs,
            ):
                continue
            if _call_result_is_product_position(
                statement,
                parents,
                import_aliases=import_aliases,
                defined_symbols=defined_symbols,
                allowed_abs=dependency_rules.allowed_abs,
            ):
                if has_instantiations:
                    instantiations.add(normalized)
                elif has_calls:
                    calls.add(normalized)
            elif has_calls:
                calls.add(normalized)

    return calls, instantiations


def _collect_repo_call_targets(
    node: ast.AST,
    defined_symbols: set[str],
    import_aliases: Mapping[str, str],
    dependency_rules: ModuleDependencyConfig,
) -> set[str]:
    """Collect all repo-local call targets before operation/product classification.

    Intent
    ------
    Expose the collect repo call targets step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps collect repo call targets behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set collect_repo_call_targets_inputs = received_context
    - set collect_repo_call_targets_products = carried_outputs
    - return collect_repo_call_targets_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._observed_call_is_repo_dependency:
      why:
        computes: "observed call is repo dependency supplies repo-local behavior used by collect repo call targets; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    ._normalize_dependency_name:
      why:
        constructs: "normalize dependency name produces a value carried by collect repo call targets; this edge is documented from the observed product position in the body."
    ._flatten_attribute_name:
      why:
        constructs: "Builds dotted call targets for repo-local resolution."
    ._iter_lexical_scope_nodes:
      why:
        constructs: "Builds the callable node stream without entering nested scopes."
    """
    targets: set[str] = set()
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        walk_nodes = _iter_lexical_scope_nodes(node.body)
    else:
        walk_nodes = ast.walk(node)
    for current in walk_nodes:
        if not isinstance(current, ast.Call):
            continue
        target_name = _flatten_attribute_name(current.func)
        if target_name:
            normalized = _normalize_dependency_name(target_name, import_aliases)
            if _observed_call_is_repo_dependency(
                normalized,
                import_aliases=import_aliases,
                defined_symbols=defined_symbols,
                allowed_abs=dependency_rules.allowed_abs,
            ):
                targets.add(normalized)
    return targets


def _dispatch_id_variants(dispatch_id: str) -> set[str]:
    """Return equivalent logical spellings for a dispatch interface id.

    Intent
    ------
    Expose the dispatch id variants step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps dispatch id variants behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set dispatch_id_variants_inputs = received_context
    - return dispatch_id_variants_inputs

    Wraps
    -----
    - none
    """
    raw = (dispatch_id or "").strip()
    if not raw:
        return set()
    variants = {raw}
    if raw.startswith("skills."):
        variants.add(raw[len("skills.") :])
    else:
        variants.add(f"skills.{raw}")
    return variants


def _dispatch_ids_match(left: str, right: str) -> bool:
    """Return true when two dispatch ids are equivalent logical spellings.

    Intent
    ------
    Expose the dispatch ids match step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps dispatch ids match behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set dispatch_ids_match_inputs = received_context
    - set dispatch_ids_match_effects = local_decisions
    - return dispatch_ids_match_effects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._dispatch_id_variants:
      why:
        computes: "dispatch id variants supplies repo-local behavior used by dispatch ids match; this edge is documented from an observed call in the body."
    """
    return bool(_dispatch_id_variants(left) & _dispatch_id_variants(right))


def _collect_known_dispatch_ids(repo_root: Path | None = None) -> set[str]:
    """Collect public interface ids advertised by skill contracts.

    Intent
    ------
    Expose the collect known dispatch ids step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps collect known dispatch ids behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set collect_known_dispatch_ids_inputs = received_context
    - set collect_known_dispatch_ids_products = carried_outputs
    - return collect_known_dispatch_ids_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._dispatch_id_variants:
      why:
        constructs: "dispatch id variants produces a value carried by collect known dispatch ids; this edge is documented from the observed product position in the body."
    """
    root = repo_root or Path.cwd()
    skills_root = root / "skills"
    known: set[str] = set()
    if not skills_root.exists():
        return known
    for skill_doc in skills_root.glob("*/SKILL.md"):
        try:
            text = skill_doc.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _DISPATCH_ID_RE.finditer(text):
            known.update(_dispatch_id_variants(match.group(0)))
    return known


def _collect_observed_dispatch_ids(node: ast.AST) -> set[str]:
    """Collect dispatcher interface ids from literal dispatcher call arguments.

    Intent
    ------
    Expose the collect observed dispatch ids step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps collect observed dispatch ids behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set collect_observed_dispatch_ids_inputs = received_context
    - return collect_observed_dispatch_ids_inputs

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._iter_lexical_scope_nodes:
      why:
        constructs: "Builds the callable node stream without nested-scope dispatch leakage."
    """
    observed: set[str] = set()
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        walk_nodes: Iterable[ast.AST] = _iter_lexical_scope_nodes(node.body)
    else:
        walk_nodes = ast.walk(node)
    for statement in walk_nodes:
        if not isinstance(statement, ast.Call):
            continue
        literals = [
            child.value
            for child in ast.walk(statement)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        ]
        if not any(value == "dispatcher" or value == "--caller-skill" for value in literals):
            continue
        for value in literals:
            for match in _DISPATCH_ID_RE.finditer(value):
                observed.add(match.group(0))
    return observed


def _emit_missing_dependency_issues(
    path: Path,
    node_id: str,
    line_no: int,
    declared: str,
    observed: set[str],
    import_aliases: Mapping[str, str],
    *,
    section: str,
) -> Iterable[DocstringValidationIssue]:
    """Emit a warning when a declaration is not seen in observed calls.

    Intent
    ------
    Expose the emit missing dependency issues step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps emit missing dependency issues behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set emit_missing_dependency_issues_inputs = received_context
    - set emit_missing_dependency_issues_products = carried_outputs
    - return emit_missing_dependency_issues_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._dependency_matches:
      why:
        computes: "dependency matches supplies repo-local behavior used by emit missing dependency issues; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    .DocstringValidationIssue:
      why:
        constructs: "DocstringValidationIssue produces a value carried by emit missing dependency issues; this edge is documented from the observed product position in the body."
    """
    for observed_name in observed:
        if _dependency_matches(declared, observed_name, import_aliases):
            return
    yield DocstringValidationIssue(
        path=path,
        code="docstring.module-dependency-not-observed",
        message=(
            f"Declared dependency '{declared}' in {section} is not observed in that "
            "dependency category. Move it to the section named by the corresponding "
            "undocumented-dependency warning, mark it implicit only when it is "
            "noninferable, or remove it."
        ),
        line=line_no,
        node_id=node_id,
    )


def _emit_undocumented_dependency_issues(
    path: Path,
    node_id: str,
    line_no: int,
    observed: str,
    documented: set[str],
    import_aliases: Mapping[str, str],
    *,
    section: str,
) -> Iterable[DocstringValidationIssue]:
    """Emit a warning when observed dependency is not documented.

    Intent
    ------
    Expose the emit undocumented dependency issues step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps emit undocumented dependency issues behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set emit_undocumented_dependency_issues_inputs = received_context
    - set emit_undocumented_dependency_issues_products = carried_outputs
    - return emit_undocumented_dependency_issues_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._dependency_matches:
      why:
        computes: "dependency matches supplies repo-local behavior used by emit undocumented dependency issues; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    .DocstringValidationIssue:
      why:
        constructs: "DocstringValidationIssue produces a value carried by emit undocumented dependency issues; this edge is documented from the observed product position in the body."
    """
    for documented_name in documented:
        if _dependency_matches(observed, documented_name, import_aliases):
            return
    yield DocstringValidationIssue(
        path=path,
        code="docstring.module-dependency-undocumented",
        message=(
            f"Observed dependency '{observed}' is not listed in {section}. "
            f"Document it under {section} with a leading-dot relative logical path "
            "for same-repo symbols, or omit it only if it is not repo-local."
        ),
        line=line_no,
        node_id=node_id,
    )


def _dependency_path_is_allowed(name: str, allowed_abs: tuple[str, ...]) -> bool:
    """Return True iff a dependency path is explicitly relative or allowed absolute.

    Intent
    ------
    Expose the dependency path is allowed step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps dependency path is allowed behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set dependency_path_is_allowed_inputs = received_context
    - return dependency_path_is_allowed_inputs

    Wraps
    -----
    - none
    """
    raw = (name or "").strip()
    if not raw:
        return False
    if raw.startswith("."):
        return True
    return raw.split(".", 1)[0] in set(allowed_abs)


def _dependency_resolves_to_repo(
    name: str,
    import_aliases: Mapping[str, str],
    allowed_abs: tuple[str, ...],
) -> bool:
    """Return True when a declared repo dependency denotes repo-local code.

    Intent
    ------
    Expose the dependency resolves to repo step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps dependency resolves to repo behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set dependency_resolves_to_repo_inputs = received_context
    - set dependency_resolves_to_repo_products = carried_outputs
    - return dependency_resolves_to_repo_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._normalize_dependency_name:
      why:
        constructs: "normalize dependency name produces a value carried by dependency resolves to repo; this edge is documented from the observed product position in the body."
    """
    raw = (name or "").strip()
    if not raw:
        return False
    if raw.startswith("."):
        return True
    allowed_roots = set(allowed_abs)
    if raw.split(".", 1)[0] in allowed_roots:
        return True
    normalized = _normalize_dependency_name(raw, import_aliases)
    return bool(normalized and normalized.split(".", 1)[0] in allowed_roots)


def _observed_call_is_repo_dependency(
    name: str,
    import_aliases: Mapping[str, str],
    defined_symbols: set[str],
    allowed_abs: tuple[str, ...],
) -> bool:
    """Return true when an observed call target is repo-local.

    Intent
    ------
    Expose the observed call is repo dependency step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps observed call is repo dependency behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set observed_call_is_repo_dependency_inputs = received_context
    - set observed_call_is_repo_dependency_products = carried_outputs
    - return observed_call_is_repo_dependency_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._dependency_resolves_to_repo:
      why:
        constructs: "dependency resolves to repo produces a value carried by observed call is repo dependency; this edge is documented from the observed product position in the body."
    """
    raw = (name or "").strip()
    if not raw:
        return False
    if raw.split(".", 1)[0] in defined_symbols:
        return True
    return _dependency_resolves_to_repo(raw, import_aliases, allowed_abs)


def _repo_observed_calls(
    observed_calls: Iterable[str],
    import_aliases: Mapping[str, str],
    defined_symbols: set[str],
    allowed_abs: tuple[str, ...],
) -> set[str]:
    """Filter observed call targets to repo-local dependencies.

    Intent
    ------
    Expose the repo observed calls step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps repo observed calls behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set repo_observed_calls_inputs = received_context
    - set repo_observed_calls_effects = local_decisions
    - return repo_observed_calls_effects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._observed_call_is_repo_dependency:
      why:
        computes: "observed call is repo dependency supplies repo-local behavior used by repo observed calls; this edge is documented from an observed call in the body."
    """
    return {
        call
        for call in observed_calls
        if _observed_call_is_repo_dependency(
            call,
            import_aliases=import_aliases,
            defined_symbols=defined_symbols,
            allowed_abs=allowed_abs,
        )
    }


def _meaningful_call_target_names(node: ast.AST) -> tuple[str, ...]:
    """Collect non-builtin, non-self call targets from a function body.

    Intent
    ------
    Expose the meaningful call target names step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps meaningful call target names behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set meaningful_call_target_names_inputs = received_context
    - set meaningful_call_target_names_products = carried_outputs
    - return meaningful_call_target_names_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._flatten_attribute_name:
      why:
        constructs: "flatten attribute name produces a value carried by meaningful call target names; this edge is documented from the observed product position in the body."
    ._iter_lexical_scope_nodes:
      why:
        constructs: "Builds the callable node stream without nested-scope call leakage."
    """
    targets: list[str] = []
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        walk_nodes: Iterable[ast.AST] = _iter_lexical_scope_nodes(node.body)
    else:
        walk_nodes = ast.walk(node)
    for statement in walk_nodes:
        if not isinstance(statement, ast.Call):
            continue
        target = _flatten_attribute_name(statement.func)
        if target is None:
            continue
        base = target.split(".", 1)[0]
        if (
            not base
            or base in _IGNORED_CALL_BASES
            or base in _BUILTIN_SYMBOLS
        ):
            continue
        targets.append(target)
    return tuple(targets)


def _unwrap_call_value(value: ast.AST | None) -> ast.Call | None:
    """Return the call expression inside simple return/await wrappers.

    Intent
    ------
    Expose the unwrap call value step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps unwrap call value behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set unwrap_call_value_inputs = received_context
    - return unwrap_call_value_inputs

    Wraps
    -----
    - none
    """
    if isinstance(value, ast.Call):
        return value
    if isinstance(value, ast.Await) and isinstance(value.value, ast.Call):
        return value.value
    return None


def _call_target_name(value: ast.AST | None) -> str | None:
    """Return the flattened call target from a simple call expression.

    Intent
    ------
    Expose the call target name step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps call target name behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set call_target_name_inputs = received_context
    - set call_target_name_products = carried_outputs
    - return call_target_name_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._flatten_attribute_name:
      why:
        constructs: "flatten attribute name produces a value carried by call target name; this edge is documented from the observed product position in the body."
    ._unwrap_call_value:
      why:
        constructs: "unwrap call value produces a value carried by call target name; this edge is documented from the observed product position in the body."
    """
    call = _unwrap_call_value(value)
    if call is None:
        return None
    return _flatten_attribute_name(call.func)


def _statements_without_docstring(body: Iterable[ast.stmt]) -> tuple[ast.stmt, ...]:
    """Return executable statements after dropping a leading docstring literal.

    Intent
    ------
    Expose the statements without docstring step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps statements without docstring behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set statements_without_docstring_inputs = received_context
    - return statements_without_docstring_inputs

    Wraps
    -----
    - none
    """
    statements = tuple(body)
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        return statements[1:]
    return statements


def _direct_wrapper_target_from_statements(statements: Iterable[ast.stmt]) -> str | None:
    """Detect simple direct wrapper bodies and return their call target.

    Intent
    ------
    Expose the direct wrapper target from statements step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps direct wrapper target from statements behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set direct_wrapper_target_from_statements_inputs = received_context
    - set direct_wrapper_target_from_statements_products = carried_outputs
    - return direct_wrapper_target_from_statements_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._meaningful_call_target_names:
      why:
        computes: "meaningful call target names supplies repo-local behavior used by direct wrapper target from statements; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    ._call_target_name:
      why:
        constructs: "call target name produces a value carried by direct wrapper target from statements; this edge is documented from the observed product position in the body."
    ._direct_wrapper_target_from_statements:
      why:
        constructs: "direct wrapper target from statements produces a value carried by direct wrapper target from statements; this edge is documented from the observed product position in the body."
    ._statements_without_docstring:
      why:
        constructs: "statements without docstring produces a value carried by direct wrapper target from statements; this edge is documented from the observed product position in the body."
    """
    body = _statements_without_docstring(statements)
    if len(body) == 1 and isinstance(body[0], ast.Return):
        return _call_target_name(body[0].value)

    if (
        len(body) == 2
        and isinstance(body[0], ast.Assign)
        and len(body[0].targets) == 1
        and isinstance(body[0].targets[0], ast.Name)
        and isinstance(body[1], ast.Return)
        and isinstance(body[1].value, ast.Name)
        and body[0].targets[0].id == body[1].value.id
    ):
        return _call_target_name(body[0].value)

    if len(body) == 1 and isinstance(body[0], ast.Try):
        candidate = _direct_wrapper_target_from_statements(body[0].body)
        if candidate and not any(_meaningful_call_target_names(handler) for handler in body[0].handlers):
            return candidate

    return None


def _is_direct_thin_wrapper(
    node: ast.AST,
    target: str,
    repo_calls: set[str],
    import_aliases: Mapping[str, str],
) -> bool:
    """Return true for a body that directly returns one repo dependency.

    Intent
    ------
    Expose the is direct thin wrapper step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps is direct thin wrapper behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set is_direct_thin_wrapper_inputs = received_context
    - set is_direct_thin_wrapper_products = carried_outputs
    - return is_direct_thin_wrapper_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._dependency_matches:
      why:
        computes: "dependency matches supplies repo-local behavior used by is direct thin wrapper; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    ._direct_wrapper_target_from_statements:
      why:
        constructs: "direct wrapper target from statements produces a value carried by is direct thin wrapper; this edge is documented from the observed product position in the body."
    ._meaningful_call_target_names:
      why:
        constructs: "meaningful call target names produces a value carried by is direct thin wrapper; this edge is documented from the observed product position in the body."
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    if len(repo_calls) != 1:
        return False
    direct_target = _direct_wrapper_target_from_statements(node.body)
    if not direct_target:
        return False
    if not _dependency_matches(target, direct_target, import_aliases):
        return False
    meaningful_calls = _meaningful_call_target_names(node)
    return len(meaningful_calls) <= 1


def _display_repo_call_target(name: str, defined_symbols: set[str]) -> str:
    """Render observed local call names as leading-dot docstring paths.

    Intent
    ------
    Expose the display repo call target step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps display repo call target behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set display_repo_call_target_inputs = received_context
    - return display_repo_call_target_inputs

    Wraps
    -----
    - none
    """
    raw = (name or "").strip()
    if raw and raw.split(".", 1)[0] in defined_symbols:
        return f".{raw}"
    return raw


def _single_repo_call_has_local_pseudocode_work(parsed: object) -> bool:
    """Return true when pseudocode shows local work around one repo call.

    Intent
    ------
    Expose the single repo call has local pseudocode work step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps single repo call has local pseudocode work behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set single_repo_call_has_local_pseudocode_work_inputs = received_context
    - return single_repo_call_has_local_pseudocode_work_inputs

    Wraps
    -----
    - none
    """
    steps = tuple(getattr(parsed, "pseudocode_steps", ()) or ())
    local_steps = tuple(
        step
        for step in steps
        if not getattr(step, "dependency_kind", "")
        and getattr(step, "kind", "") in {"set", "read", "write", "return", "raise", "if", "while", "for"}
    )
    if any(getattr(step, "kind", "") in {"if", "while", "for", "read", "write"} for step in local_steps):
        return True
    return len(local_steps) >= 2


def _emit_repo_dependency_scope_issue(
    path: Path,
    node_id: str,
    line_no: int,
    *,
    declared: str,
    section: str,
    import_aliases: Mapping[str, str],
    allowed_abs: tuple[str, ...],
) -> Iterable[DocstringValidationIssue]:
    """Emit warning when a repo dependency section names external code.

    Intent
    ------
    Expose the emit repo dependency scope issue step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps emit repo dependency scope issue behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set emit_repo_dependency_scope_issue_inputs = received_context
    - set emit_repo_dependency_scope_issue_products = carried_outputs
    - return emit_repo_dependency_scope_issue_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._dependency_resolves_to_repo:
      why:
        computes: "dependency resolves to repo supplies repo-local behavior used by emit repo dependency scope issue; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    .DocstringValidationIssue:
      why:
        constructs: "DocstringValidationIssue produces a value carried by emit repo dependency scope issue; this edge is documented from the observed product position in the body."
    """
    if _dependency_resolves_to_repo(declared, import_aliases, allowed_abs):
        return
    yield DocstringValidationIssue(
        path=path,
        code="docstring.repo-dependency-not-repo",
        message=(
            f"Declared dependency '{declared}' in {section} resolves outside repo logical roots. "
            "CallsFromRepo and InstantiationsFromRepo must document only repo-local "
            f"dependencies rooted at {', '.join(allowed_abs) or '<none>'} or leading-dot relatives; "
            "omit stdlib and third-party helpers."
        ),
        line=line_no,
        node_id=node_id,
    )


def _emit_dependency_path_issue(
    path: Path,
    node_id: str,
    line_no: int,
    *,
    declared: str,
    section: str,
    allowed_abs: tuple[str, ...],
) -> Iterable[DocstringValidationIssue]:
    """Emit portability warning for unrooted dependency logical paths.

    Intent
    ------
    Expose the emit dependency path issue step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps emit dependency path issue behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set emit_dependency_path_issue_inputs = received_context
    - set emit_dependency_path_issue_products = carried_outputs
    - return emit_dependency_path_issue_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._dependency_path_is_allowed:
      why:
        computes: "dependency path is allowed supplies repo-local behavior used by emit dependency path issue; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    .DocstringValidationIssue:
      why:
        constructs: "DocstringValidationIssue produces a value carried by emit dependency path issue; this edge is documented from the observed product position in the body."
    """
    if _dependency_path_is_allowed(declared, allowed_abs):
        return
    yield DocstringValidationIssue(
        path=path,
        code="docstring.absolute-dependency-not-allowed",
        message=(
            f"Declared dependency '{declared}' in {section} must start with '.' "
            "for a relative path or with an allowed absolute root: "
            f"{', '.join(allowed_abs) or '<none>'}."
        ),
        line=line_no,
        node_id=node_id,
    )


def _iter_module_dependency_issues(
    path: Path,
    node_id: str,
    line_no: int,
    node: ast.AST,
    parsed: object,
    schema_rules: ModuleDependencyConfig,
    import_aliases: Mapping[str, str],
    defined_symbols: set[str],
) -> Iterable[DocstringValidationIssue]:
    """Validate callable module dependency sections with static inference.

    Intent
    ------
    Expose the iter module dependency issues step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps iter module dependency issues behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set iter_module_dependency_issues_inputs = received_context
    - set iter_module_dependency_issues_products = carried_outputs
    - return iter_module_dependency_issues_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._declared_dependency_targets_match:
      why:
        computes: "declared dependency targets match supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."
    ._dependency_is_resolved:
      why:
        computes: "dependency is resolved supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."
    ._dependency_matches:
      why:
        computes: "dependency matches supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."
    ._dispatch_id_variants:
      why:
        computes: "dispatch id variants supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."
    ._dispatch_ids_match:
      why:
        computes: "dispatch ids match supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."
    ._emit_dependency_path_issue:
      why:
        computes: "emit dependency path issue supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."
    ._emit_missing_dependency_issues:
      why:
        computes: "emit missing dependency issues supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."
    ._emit_repo_dependency_scope_issue:
      why:
        computes: "emit repo dependency scope issue supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."
    ._emit_undocumented_dependency_issues:
      why:
        computes: "emit undocumented dependency issues supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."
    ._is_direct_thin_wrapper:
      why:
        computes: "is direct thin wrapper supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."
    ._name_matches_observed:
      why:
        computes: "name matches observed supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."
    ._name_mentioned_in_pseudocode:
      why:
        computes: "name mentioned in pseudocode supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."
    ._single_repo_call_has_local_pseudocode_work:
      why:
        computes: "single repo call has local pseudocode work supplies repo-local behavior used by iter module dependency issues; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    .DocstringValidationIssue:
      why:
        constructs: "DocstringValidationIssue produces a value carried by iter module dependency issues; this edge is documented from the observed product position in the body."
    ._collect_dependency_targets:
      why:
        constructs: "collect dependency targets produces a value carried by iter module dependency issues; this edge is documented from the observed product position in the body."
    ._collect_explicit_dependency_refs:
      why:
        constructs: "collect explicit dependency refs produces a value carried by iter module dependency issues; this edge is documented from the observed product position in the body."
    ._collect_known_dispatch_ids:
      why:
        constructs: "collect known dispatch ids produces a value carried by iter module dependency issues; this edge is documented from the observed product position in the body."
    ._collect_observed_dispatch_ids:
      why:
        constructs: "collect observed dispatch ids produces a value carried by iter module dependency issues; this edge is documented from the observed product position in the body."
    ._collect_pseudocode_tokens:
      why:
        constructs: "collect pseudocode tokens produces a value carried by iter module dependency issues; this edge is documented from the observed product position in the body."
    ._collect_repo_call_targets:
      why:
        constructs: "collect repo call targets produces a value carried by iter module dependency issues; this edge is documented from the observed product position in the body."
    ._display_repo_call_target:
      why:
        constructs: "display repo call target produces a value carried by iter module dependency issues; this edge is documented from the observed product position in the body."
    ._normalize_dependency_name:
      why:
        constructs: "normalize dependency name produces a value carried by iter module dependency issues; this edge is documented from the observed product position in the body."
    ._repo_observed_calls:
      why:
        constructs: "repo observed calls produces a value carried by iter module dependency issues; this edge is documented from the observed product position in the body."
    """
    observed_calls, observed_instantiations = _collect_dependency_targets(
        node=node,
        defined_symbols=defined_symbols,
        import_aliases=import_aliases,
        dependency_rules=schema_rules,
    )

    documented_calls = tuple(getattr(parsed, "module_calls", ()))
    documented_instantiations = tuple(getattr(parsed, "module_instantiates", ()))
    documented_dispatches = tuple(getattr(parsed, "dispatches", ()))

    documented_call_names = {
        _normalize_dependency_name(dependency.name, import_aliases)
        for dependency in documented_calls
    }
    documented_instantiation_names = {
        _normalize_dependency_name(dependency.name, import_aliases)
        for dependency in documented_instantiations
    }

    pseudocode_tokens = _collect_pseudocode_tokens(
        getattr(parsed, "pseudocode_steps", ())
    )
    explicit_refs = _collect_explicit_dependency_refs(parsed)
    if explicit_refs:
        pseudocode_tokens = explicit_refs
    validate_declared_targets_resolved = bool(
        getattr(schema_rules, "validate_declared_targets_resolved", False)
    ) or bool(getattr(schema_rules, "validate_dependency_targets_resolved", False))
    observed_dispatches = _collect_observed_dispatch_ids(node)
    known_dispatch_ids = _collect_known_dispatch_ids()
    documented_dispatch_ids = {
        getattr(dependency, "id", "").strip()
        for dependency in documented_dispatches
        if getattr(dependency, "id", "").strip()
    }
    documented_wrap_targets = {
        getattr(dependency, "target", "").strip()
        for dependency in getattr(parsed, "wraps", ())
        if getattr(dependency, "is_wrapper", False)
        and getattr(dependency, "target", "").strip()
    }
    observed_repo_calls = _repo_observed_calls(
        observed_calls,
        import_aliases=import_aliases,
        defined_symbols=defined_symbols,
        allowed_abs=schema_rules.allowed_abs,
    )
    all_repo_calls = _collect_repo_call_targets(
        node=node,
        defined_symbols=defined_symbols,
        import_aliases=import_aliases,
        dependency_rules=schema_rules,
    )
    if len(all_repo_calls) == 1:
        wrapper_target = next(iter(all_repo_calls))
        has_documented_wrap = any(
            _dependency_matches(wrapper_target, documented, import_aliases)
            for documented in documented_wrap_targets
        )
        has_documented_product = any(
            _dependency_matches(wrapper_target, documented, import_aliases)
            for documented in documented_instantiation_names
        )
        if not has_documented_wrap and not has_documented_product:
            display_target = _display_repo_call_target(wrapper_target, defined_symbols)
            if _is_direct_thin_wrapper(
                node=node,
                target=wrapper_target,
                repo_calls=all_repo_calls,
                import_aliases=import_aliases,
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.wraps-missing-thin-wrapper",
                    message=(
                        f"This callable appears to be a thin wrapper around '{display_target}'. "
                        "Document it in Wraps instead of declaring Wraps as none so graph "
                        "extraction can expose the wrapper edge."
                    ),
                    line=line_no,
                    node_id=node_id,
                    severity="error",
                )
            elif not _single_repo_call_has_local_pseudocode_work(parsed):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.single-repo-call-review",
                    message=(
                        f"This callable has exactly one repo call '{display_target}'. If its "
                        "purpose is mainly delegation, document it in Wraps; otherwise make "
                        "Pseudocode and the dependency why explain the local work around the call."
                    ),
                    line=line_no,
                    node_id=node_id,
                    severity="warning",
                )

    for documented in documented_call_names:
        if any(
            _declared_dependency_targets_match(documented, target, import_aliases)
            for target in documented_wrap_targets
        ):
            yield DocstringValidationIssue(
                path=path,
                code="docstring.dependency-section-overlap",
                message=(
                    f"Dependency '{documented}' is declared in both Wraps and {schema_rules.calls_section}. "
                    "Use Wraps only; wrapper edges outrank ordinary call edges in graph output."
                ),
                line=line_no,
                node_id=node_id,
            )
        if any(
            _declared_dependency_targets_match(documented, target, import_aliases)
            for target in documented_instantiation_names
        ) and not (
            _name_matches_observed(documented, observed_calls, import_aliases)
            and _name_matches_observed(documented, observed_instantiations, import_aliases)
        ):
            yield DocstringValidationIssue(
                path=path,
                code="docstring.dependency-section-overlap",
                message=(
                    f"Dependency '{documented}' is declared in both {schema_rules.instantiates_section} "
                    f"and {schema_rules.calls_section}. Use {schema_rules.instantiates_section} only "
                    "when the dependency product is carried forward."
                ),
                line=line_no,
                node_id=node_id,
            )

    for documented in documented_instantiation_names:
        if any(
            _declared_dependency_targets_match(documented, target, import_aliases)
            for target in documented_wrap_targets
        ):
            yield DocstringValidationIssue(
                path=path,
                code="docstring.dependency-section-overlap",
                message=(
                    f"Dependency '{documented}' is declared in both Wraps and {schema_rules.instantiates_section}. "
                    "Use Wraps only when the callable primarily delegates to that target."
                ),
                line=line_no,
                node_id=node_id,
            )

    if schema_rules.validate_declared_calls:
        for dependency in documented_calls:
            yield from _emit_dependency_path_issue(
                path=path,
                node_id=node_id,
                line_no=line_no,
                declared=dependency.name,
                section=schema_rules.calls_section,
                allowed_abs=schema_rules.allowed_abs,
            )
            yield from _emit_repo_dependency_scope_issue(
                path=path,
                node_id=node_id,
                line_no=line_no,
                declared=dependency.name,
                section=schema_rules.calls_section,
                import_aliases=import_aliases,
                allowed_abs=schema_rules.allowed_abs,
            )
            if validate_declared_targets_resolved and not _dependency_is_resolved(
                dependency.name,
                import_aliases=import_aliases,
                defined_symbols=defined_symbols,
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.module-dependency-unresolved",
                    message=f"Declared dependency '{dependency.name}' is not resolved from imports or local symbols.",
                    line=line_no,
                    node_id=node_id,
                )
            if dependency.implicit:
                continue
            yield from _emit_missing_dependency_issues(
                path=path,
                node_id=node_id,
                line_no=line_no,
                declared=dependency.name,
                observed=observed_calls,
                import_aliases=import_aliases,
                section=schema_rules.calls_section,
            )
            if schema_rules.enforce_declared_dependency_pseudocode_coverage and not _name_mentioned_in_pseudocode(
                dependency.name,
                pseudocode_tokens,
                import_aliases,
                scope=schema_rules.calls_section,
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.pseudocode-dependency-missing",
                    message=(
                        f"Declared dependency '{dependency.name}' is not mentioned in "
                        "dependency references."
                    ),
                    line=line_no,
                    node_id=node_id,
                )

    if schema_rules.validate_declared_instantiations:
        for dependency in documented_instantiations:
            yield from _emit_dependency_path_issue(
                path=path,
                node_id=node_id,
                line_no=line_no,
                declared=dependency.name,
                section=schema_rules.instantiates_section,
                allowed_abs=schema_rules.allowed_abs,
            )
            if validate_declared_targets_resolved and not _dependency_is_resolved(
                dependency.name,
                import_aliases=import_aliases,
                defined_symbols=defined_symbols,
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.module-dependency-unresolved",
                    message=f"Declared dependency '{dependency.name}' is not resolved from imports or local symbols.",
                    line=line_no,
                    node_id=node_id,
                )
            if dependency.implicit:
                continue
            yield from _emit_missing_dependency_issues(
                path=path,
                node_id=node_id,
                line_no=line_no,
                declared=dependency.name,
                observed=observed_instantiations,
                import_aliases=import_aliases,
                section=schema_rules.instantiates_section,
            )
            if schema_rules.enforce_declared_dependency_pseudocode_coverage and not _name_mentioned_in_pseudocode(
                dependency.name,
                pseudocode_tokens,
                import_aliases,
                scope=schema_rules.instantiates_section,
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.pseudocode-dependency-missing",
                    message=(
                        f"Declared dependency '{dependency.name}' is not mentioned in "
                        "dependency references."
                    ),
                    line=line_no,
                    node_id=node_id,
                )

    if schema_rules.report_unlisted_calls:
        for observed in observed_calls:
            if not any(
                _dependency_matches(observed, documented, import_aliases)
                for documented in documented_call_names
            ):
                yield from _emit_undocumented_dependency_issues(
                    path=path,
                    node_id=node_id,
                    line_no=line_no,
                    observed=observed,
                    documented=documented_call_names,
                    import_aliases=import_aliases,
                    section=schema_rules.calls_section,
                )
            elif schema_rules.enforce_observed_dependency_pseudocode_coverage and not _name_mentioned_in_pseudocode(
                observed,
                pseudocode_tokens,
                import_aliases,
                scope=schema_rules.calls_section,
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.pseudocode-dependency-undocumented",
                    message=(
                        f"Observed dependency '{observed}' is not mentioned in "
                        "dependency references."
                    ),
                    line=line_no,
                    node_id=node_id,
                )

    if schema_rules.report_unlisted_instantiations:
        for observed in observed_instantiations:
            if any(
                _dependency_matches(observed, documented, import_aliases)
                for documented in documented_wrap_targets
            ):
                continue
            if not any(
                _dependency_matches(observed, documented, import_aliases)
                for documented in documented_instantiation_names
            ):
                yield from _emit_undocumented_dependency_issues(
                    path=path,
                    node_id=node_id,
                    line_no=line_no,
                    observed=observed,
                    documented=documented_instantiation_names,
                    import_aliases=import_aliases,
                    section=schema_rules.instantiates_section,
                )
            elif schema_rules.enforce_observed_dependency_pseudocode_coverage and not _name_mentioned_in_pseudocode(
                observed,
                pseudocode_tokens,
                import_aliases,
                scope=schema_rules.instantiates_section,
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.pseudocode-dependency-undocumented",
                    message=(
                        f"Observed dependency '{observed}' is not mentioned in "
                        "dependency references."
                    ),
                    line=line_no,
                    node_id=node_id,
                )

    if schema_rules.validate_declared_dispatches:
        for dependency in documented_dispatches:
            dispatch_id = getattr(dependency, "id", "")
            yield from _emit_dependency_path_issue(
                path=path,
                node_id=node_id,
                line_no=line_no,
                declared=dispatch_id,
                section=schema_rules.dispatches_section,
                allowed_abs=schema_rules.allowed_abs,
            )
            if known_dispatch_ids and not any(
                variant in known_dispatch_ids for variant in _dispatch_id_variants(dispatch_id)
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.dispatch-unresolved",
                    message=f"Declared dispatch id '{dispatch_id}' does not match a known public interface id.",
                    line=line_no,
                    node_id=node_id,
                )
            if observed_dispatches and not any(
                _dispatch_ids_match(dispatch_id, observed)
                for observed in observed_dispatches
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.dispatch-not-observed",
                    message=f"Declared dispatch id '{dispatch_id}' is not observed in dispatcher call literals.",
                    line=line_no,
                    node_id=node_id,
                )
            if schema_rules.enforce_declared_dependency_pseudocode_coverage and not _name_mentioned_in_pseudocode(
                dispatch_id,
                pseudocode_tokens,
                import_aliases,
                scope=schema_rules.dispatches_section,
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.pseudocode-dependency-missing",
                    message=(
                        f"Declared dispatch dependency '{dispatch_id}' is not "
                        "mentioned in dependency references."
                    ),
                    line=line_no,
                    node_id=node_id,
                )
        for observed_dispatch in observed_dispatches:
            if any(
                _dispatch_ids_match(observed_dispatch, documented_dispatch)
                for documented_dispatch in documented_dispatch_ids
            ):
                continue
            yield DocstringValidationIssue(
                path=path,
                code="docstring.dispatch-undocumented",
                message=f"Observed dispatch id '{observed_dispatch}' is not listed in docstring.",
                line=line_no,
                node_id=node_id,
            )


def _iter_wrap_issues(
    path: Path,
    node_id: str,
    line_no: int,
    parsed: object,
    import_aliases: Mapping[str, str],
    defined_symbols: set[str],
    observed_calls: set[str],
    observed_instantiations: set[str],
) -> Iterable[DocstringValidationIssue]:
    """Validate Wraps entries against parser expectations and body reachability.

    Intent
    ------
    Expose the iter wrap issues step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps iter wrap issues behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set iter_wrap_issues_inputs = received_context
    - set iter_wrap_issues_products = carried_outputs
    - return iter_wrap_issues_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._dependency_is_resolved:
      why:
        computes: "dependency is resolved supplies repo-local behavior used by iter wrap issues; this edge is documented from an observed call in the body."
    ._name_matches_observed:
      why:
        computes: "name matches observed supplies repo-local behavior used by iter wrap issues; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    .DocstringValidationIssue:
      why:
        constructs: "DocstringValidationIssue produces a value carried by iter wrap issues; this edge is documented from the observed product position in the body."
    """
    wraps = tuple(getattr(parsed, "wraps", ()))
    if not wraps:
        return

    observed_symbols = set(observed_calls) | set(observed_instantiations)
    for wraps_entry in wraps:
        target = getattr(wraps_entry, "target", "").strip()
        if not target:
            continue

        if not all(
            str(getattr(wraps_entry, field, "")).strip()
            for field in ("preprocess", "postprocess", "fixed_arguments")
        ):
            yield DocstringValidationIssue(
                path=path,
                code="docstring.wraps-incomplete",
                message=(
                    f"Wraps entry '{target}' is missing wrapper details."
                ),
                line=line_no,
                node_id=node_id,
            )

        if not _dependency_is_resolved(
            target,
            import_aliases=import_aliases,
            defined_symbols=defined_symbols,
        ) and not _name_matches_observed(
            target,
            observed_symbols,
            import_aliases,
        ):
            yield DocstringValidationIssue(
                path=path,
                code="docstring.wraps-unresolved-target",
                message=f"Wraps target '{target}' is not validated by imports, local symbols, or observed calls.",
                line=line_no,
                node_id=node_id,
            )


def _module_aliases(path: Path) -> list[str]:
    """Collect several module identifiers used for ownership lookups.

    Intent
    ------
    Expose the module aliases step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps module aliases behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set module_aliases_inputs = received_context
    - return module_aliases_inputs

    Wraps
    -----
    - none
    """
    candidates: set[str] = set()
    base = path
    if base.name == "__init__.py":
        base = base.parent
    if base.suffix == ".py":
        base = base.with_suffix("")

    try:
        relative = base.relative_to(Path.cwd())
    except ValueError:
        relative = base

    text = relative.as_posix().lstrip("./")
    if text:
        candidates.add(text)
        candidates.add(text.replace("/", "."))
        candidates.add(text.replace("/", "_"))
        candidates.add(text.rsplit("/", 1)[-1])

    candidates.add(str(base))
    candidates.add(base.as_posix())
    candidates.add(base.name)
    candidates.discard("")
    return sorted(candidates)


def _collect_ownership_index(
    module_paths: Iterable[Path],
    ownership_section: str,
) -> dict[str, dict[str, str]]:
    """Collect parsed ``Ownable`` registries from candidate modules.

    Intent
    ------
    Expose the collect ownership index step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps collect ownership index behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set collect_ownership_index_inputs = received_context
    - set collect_ownership_index_effects = local_decisions
    - return collect_ownership_index_effects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._module_aliases:
      why:
        computes: "module aliases supplies repo-local behavior used by collect ownership index; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    ..common.docstring.docstring_parser.parse_ownable_registry:
      why:
        constructs: "Builds ownership registry entries from module docstrings before callable checks run."
    """
    index: dict[str, dict[str, str]] = {}
    for module_path in module_paths:
        if not module_path.exists() or not module_path.is_file():
            continue
        try:
            source = module_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(module_path))
        except (OSError, SyntaxError):
            continue

        module_doc = ast.get_docstring(tree)
        if not module_doc:
            continue

        owners = parse_ownable_registry(module_doc, section=ownership_section)
        if not owners:
            continue

        for module_id in _module_aliases(module_path):
            if module_id not in index:
                index[module_id] = owners

    return index


def _resolve_ownership_registry(
    module_hint: str | None,
    ownership_index: Mapping[str, dict[str, str]],
    local_aliases: list[str],
) -> tuple[dict[str, str] | None, str | None]:
    """Resolve owner registry and resolved module key from a module hint.

    Intent
    ------
    Expose the resolve ownership registry step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps resolve ownership registry behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set resolve_ownership_registry_inputs = received_context
    - return resolve_ownership_registry_inputs

    Wraps
    -----
    - none
    """
    if module_hint is None:
        for alias in local_aliases:
            if alias in ownership_index:
                return ownership_index[alias], alias
        return None, None

    candidates = {
        module_hint,
        module_hint.replace("/", "."),
        module_hint.replace(".", "/"),
        module_hint.replace(".", "_"),
        module_hint.replace("/", "_"),
    }
    candidates.add(module_hint.strip("/"))
    for candidate in candidates:
        if candidate in ownership_index:
            return ownership_index[candidate], candidate

    for alias in ownership_index:
        for candidate in candidates:
            if alias == candidate or alias.endswith(f"/{candidate}") or alias.endswith(f".{candidate}"):
                return ownership_index[alias], alias
    return None, None


def _iter_parser_issue_records(
    path: Path,
    node_id: str | None,
    parser_issues: tuple[ParserIssue, ...],
    line_hint: int,
) -> Iterable[DocstringValidationIssue]:
    """Project parser issues to a shared issue representation.

    Intent
    ------
    Expose the iter parser issue records step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps iter parser issue records behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set iter_parser_issue_records_inputs = received_context
    - set iter_parser_issue_records_products = carried_outputs
    - return iter_parser_issue_records_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .DocstringValidationIssue:
      why:
        constructs: "DocstringValidationIssue produces a value carried by iter parser issue records; this edge is documented from the observed product position in the body."
    """

    for issue in parser_issues:
        detail = issue.code
        if issue.section:
            detail = f"{issue.code} ({issue.section})"
        message = f"{issue.message} [{detail}]"
        yield DocstringValidationIssue(
            path=path,
            code=issue.code,
            message=message,
            line=line_hint,
            node_id=node_id,
            severity=issue.severity,
        )


def _iter_ownership_issues(
    path: Path,
    node_id: str,
    line_no: int,
    parsed: object,
    ownership_config: OwnershipConfig,
    local_aliases: list[str],
    ownership_index: Mapping[str, dict[str, str]],
    allow_cross_file: bool,
) -> Iterable[DocstringValidationIssue]:
    """Validate ``Owns`` references with optional cross-module lookup.

    Intent
    ------
    Expose the iter ownership issues step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps iter ownership issues behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set iter_ownership_issues_inputs = received_context
    - set iter_ownership_issues_products = carried_outputs
    - return iter_ownership_issues_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .DocstringValidationIssue:
      why:
        constructs: "DocstringValidationIssue produces a value carried by iter ownership issues; this edge is documented from the observed product position in the body."
    ._resolve_ownership_registry:
      why:
        constructs: "resolve ownership registry produces a value carried by iter ownership issues; this edge is documented from the observed product position in the body."

    ..common.docstring.docstring_parser.parse_ownership_reference:
      why:
        constructs: "Builds parsed ownership references so unresolved owner ids can be diagnosed."
    """
    ownerships: list[str] = list(getattr(parsed, "owns", []))
    if ownership_config.single_owner_per_callable and len(ownerships) > 1:
        yield DocstringValidationIssue(
            path=path,
            code="docstring.owning-single",
            message=(
                "Callable declares multiple owners while single-owner ownership is enabled."
            ),
            line=line_no,
            node_id=node_id,
        )
    for entry in ownerships:
        parsed_ref = parse_ownership_reference(entry)
        if parsed_ref is None:
            continue

        module_hint, owner_id = parsed_ref
        if module_hint is None:
            registry, _ = _resolve_ownership_registry(None, ownership_index, local_aliases)
            if registry is None:
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.unknown-owner",
                    message=(
                        f"Owns reference '{owner_id}' cannot be resolved because this"
                        " module does not declare Ownable ownership entries."
                    ),
                    line=line_no,
                    node_id=node_id,
                )
            elif owner_id not in registry:
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.unknown-owner",
                    message=(
                        f"Owns reference '{owner_id}' does not match a declared owner"
                        " in this module's Ownable section."
                    ),
                    line=line_no,
                    node_id=node_id,
                )
            continue

        if not allow_cross_file and module_hint not in local_aliases:
            yield DocstringValidationIssue(
                path=path,
                code="docstring.crossfile-owner-disabled",
                message=(
                    "Owns reference includes a module hint, but cross-file"
                    " ownership validation is disabled."
                ),
                line=line_no,
                node_id=node_id,
            )
            continue

        registry, resolved_module = _resolve_ownership_registry(
            module_hint,
            ownership_index,
            local_aliases,
        )
        if registry is None:
            yield DocstringValidationIssue(
                path=path,
                code="docstring.unknown-owner",
                message=f"Owns reference '{module_hint}:{owner_id}' does not match any known module Ownable registry.",
                line=line_no,
                node_id=node_id,
            )
            continue

        if owner_id not in registry:
            owner_ref = f"{module_hint}:{owner_id}" if module_hint else owner_id
            if resolved_module and resolved_module != module_hint:
                owner_ref = f"{resolved_module}:{owner_id}"
            yield DocstringValidationIssue(
                path=path,
                code="docstring.unknown-owner",
                message=(
                    f"Owns reference '{owner_ref}' does not match a declared owner"
                    " in the referenced module's Ownable section."
                ),
                line=line_no,
                node_id=node_id,
            )


def _iter_module_ownership_registry_issues(
    path: Path,
    parser_issues: tuple[ParserIssue, ...],
    line_hint: int,
) -> Iterable[DocstringValidationIssue]:
    """Keep a narrow compatibility path for parser-level issues on module docs.

    Intent
    ------
    Expose the iter module ownership registry issues step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps iter module ownership registry issues behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set iter_module_ownership_registry_issues_inputs = received_context
    - set iter_module_ownership_registry_issues_effects = local_decisions
    - return iter_module_ownership_registry_issues_effects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_parser_issue_records:
      why:
        computes: "iter parser issue records supplies repo-local behavior used by iter module ownership registry issues; this edge is documented from an observed call in the body."
    """
    for issue in _iter_parser_issue_records(
        path=path,
        node_id=None,
        parser_issues=parser_issues,
        line_hint=line_hint,
    ):
        yield issue


def _validate_node_docstring(
    path: Path,
    node_id: str,
    node_type: str,
    node: ast.AST,
    schema_rules: DocstringSchema,
    ownership_aliases: list[str],
    ownership_index: Mapping[str, dict[str, str]],
    allow_cross_file_ownership: bool,
    import_aliases: Mapping[str, str],
    defined_symbols: set[str],
    shadowed_names: set[str],
) -> tuple[DocstringValidationIssue, ...]:
    """Validate docstring presence and parse quality for one callable/class node.

    Intent
    ------
    Expose the validate node docstring step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps validate node docstring behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set validate_node_docstring_inputs = received_context
    - set validate_node_docstring_products = carried_outputs
    - return validate_node_docstring_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._should_require_docstring:
      why:
        computes: "should require docstring supplies repo-local behavior used by validate node docstring; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    .DocstringValidationIssue:
      why:
        constructs: "DocstringValidationIssue produces a value carried by validate node docstring; this edge is documented from the observed product position in the body."
    ._collect_dependency_targets:
      why:
        constructs: "collect dependency targets produces a value carried by validate node docstring; this edge is documented from the observed product position in the body."
    ._iter_module_dependency_issues:
      why:
        constructs: "iter module dependency issues produces a value carried by validate node docstring; this edge is documented from the observed product position in the body."
    ._iter_ownership_issues:
      why:
        constructs: "iter ownership issues produces a value carried by validate node docstring; this edge is documented from the observed product position in the body."
    ._iter_parser_issue_records:
      why:
        constructs: "iter parser issue records produces a value carried by validate node docstring; this edge is documented from the observed product position in the body."
    ._iter_wrap_issues:
      why:
        constructs: "iter wrap issues produces a value carried by validate node docstring; this edge is documented from the observed product position in the body."
    ._schema_for_node:
      why:
        constructs: "Builds the node-specific policy after conservative structural classification."

    ..common.docstring.docstring_parser.parse_graph_block:
      why:
        constructs: "Builds the parsed callable docstring used for syntax and behavior validation."
    ..common.docstring.docstring_parser.check_graph_docstring:
      why:
        constructs: "Builds parser diagnostics passed into the shared issue projection."
    """
    issues: list[DocstringValidationIssue] = []

    if not _should_require_docstring(node):
        return tuple(issues)

    doc = ast.get_docstring(node)
    if doc is None:
        if not schema_rules.callable.require_docstrings:
            return tuple(issues)
        issues.append(
            DocstringValidationIssue(
                path=path,
                code="docstring.missing",
                message=f"Missing docstring on {node_type} '{node_id}'.",
                line=node.lineno,
                node_id=node_id,
            )
        )
        return tuple(issues)

    node_schema_rules = _schema_for_node(
        node,
        schema_rules,
        import_aliases,
        defined_symbols,
        shadowed_names,
    )

    issues.extend(
        _iter_parser_issue_records(
            path=path,
            node_id=node_id,
            parser_issues=check_graph_docstring(doc, schema_rules=node_schema_rules),
            line_hint=node.lineno,
        )
    )

    parsed = parse_graph_block(doc)
    issues.extend(
        _iter_ownership_issues(
            path=path,
            node_id=node_id,
            line_no=node.lineno,
            parsed=parsed,
            ownership_config=node_schema_rules.callable.ownership,
            local_aliases=ownership_aliases,
            ownership_index=ownership_index,
            allow_cross_file=allow_cross_file_ownership,
        )
    )
    observed_calls, observed_instantiations = _collect_dependency_targets(
        node=node,
        defined_symbols=defined_symbols,
        import_aliases=import_aliases,
        dependency_rules=node_schema_rules.module_dependencies,
    )
    issues.extend(
        _iter_wrap_issues(
            path=path,
            node_id=node_id,
            line_no=node.lineno,
            parsed=parsed,
            import_aliases=import_aliases,
            defined_symbols=defined_symbols,
            observed_calls=observed_calls,
            observed_instantiations=observed_instantiations,
        )
    )
    if not isinstance(node, ast.ClassDef):
        issues.extend(
            _iter_module_dependency_issues(
                path=path,
                node_id=node_id,
                line_no=node.lineno,
                node=node,
                parsed=parsed,
                schema_rules=node_schema_rules.module_dependencies,
                import_aliases=import_aliases,
                defined_symbols=defined_symbols,
            )
        )

    return tuple(issues)


def _checker_from_group(check_group: str) -> tuple[_BaseDocstringChecker, ...]:
    """Resolve requested checker set from string mode.

    Intent
    ------
    Expose the checker from group step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps checker from group behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set checker_from_group_inputs = received_context
    - set checker_from_group_products = carried_outputs
    - return checker_from_group_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BehavioralDocstringChecker:
      why:
        constructs: "BehavioralDocstringChecker produces a value carried by checker from group; this edge is documented from the observed product position in the body."
    .SyntaxDocstringChecker:
      why:
        constructs: "SyntaxDocstringChecker produces a value carried by checker from group; this edge is documented from the observed product position in the body."
    """
    normalized = check_group.strip().lower()
    if normalized == "syntax":
        return (SyntaxDocstringChecker(),)
    if normalized == "behavioral":
        return (BehavioralDocstringChecker(),)
    if normalized == "all":
        return (SyntaxDocstringChecker(), BehavioralDocstringChecker())
    raise ValueError(
        f"Unknown check_group {check_group!r}; expected 'all', 'syntax', or 'behavioral'."
    )


def _module_docstring_issues_by_check(
    path: Path,
    module_doc: str | None,
    *,
    require_module_docstring: bool,
) -> tuple[DocstringValidationIssue, ...]:
    """_module_docstring_issues_by_check supports AST-backed behavioral docstring validation as a documented callable boundary.

    Intent
    ------
    Expose the module docstring issues by check step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps module docstring issues by check behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set module_docstring_issues_by_check_inputs = received_context
    - set module_docstring_issues_by_check_products = carried_outputs
    - return module_docstring_issues_by_check_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .DocstringValidationIssue:
      why:
        constructs: "DocstringValidationIssue produces a value carried by module docstring issues by check; this edge is documented from the observed product position in the body."
    ._iter_module_ownership_registry_issues:
      why:
        constructs: "iter module ownership registry issues produces a value carried by module docstring issues by check; this edge is documented from the observed product position in the body."

    ..common.docstring.docstring_parser.check_pipeline_docstring:
      why:
        constructs: "Builds pipeline diagnostics passed into module issue projection."
    """
    issues: list[DocstringValidationIssue] = []
    if module_doc is None:
        if require_module_docstring:
            issues.append(
                DocstringValidationIssue(
                    path=path,
                    code="docstring.module-missing",
                    message="Missing module docstring.",
                    line=1,
                    node_id=None,
                )
            )
        return tuple(issues)

    issues.extend(
        _iter_module_ownership_registry_issues(
            path=path,
            parser_issues=check_pipeline_docstring(module_doc),
            line_hint=1,
        )
    )

    # Keep parser-level empty/section/format checks with syntax checker behavior.
    return tuple(issues)


def _dedupe_docstring_issues(
    issues: Iterable[DocstringValidationIssue],
) -> tuple[DocstringValidationIssue, ...]:
    """Return validation issues without duplicate records from overlapping groups.

    Intent
    ------
    Expose the dedupe docstring issues step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps dedupe docstring issues behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set dedupe_docstring_issues_inputs = received_context
    - return dedupe_docstring_issues_inputs

    Wraps
    -----
    - none
    """
    deduped: list[DocstringValidationIssue] = []
    seen: set[tuple[Path, str, int | None, str | None, str, str]] = set()
    for issue in issues:
        key = (
            issue.path,
            issue.code,
            issue.line,
            issue.node_id,
            issue.message,
            issue.severity,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return tuple(deduped)


def _normalized_template_text(
    text: str,
    *,
    names: Iterable[str],
) -> str:
    """Normalize one prose sentence for repeated-template detection.

    Intent
    ------
    Expose the normalized template text step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps normalized template text behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set normalized_template_text_inputs = received_context
    - return normalized_template_text_inputs

    Wraps
    -----
    - none
    """
    normalized = text.lower()
    normalized = re.sub(r"`[^`]*`|'[^']*'|\"[^\"]*\"", " <literal> ", normalized)
    for name in sorted({name for name in names if name}, key=len, reverse=True):
        for token in re.split(r"[^A-Za-z0-9_]+", name):
            if token:
                normalized = re.sub(rf"\b{re.escape(token.lower())}\b", " <name> ", normalized)
    normalized = re.sub(r"[^a-z0-9_<>\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _iter_repeated_template_issues(
    path: Path,
    tree: ast.AST,
    *,
    include_nested: bool,
    include_private: bool,
    schema_rules: DocstringSchema,
) -> Iterable[DocstringValidationIssue]:
    """Detect repeated normalized docstring prose templates in one module.

    Intent
    ------
    Expose the iter repeated template issues step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps iter repeated template issues behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set iter_repeated_template_issues_inputs = received_context
    - set iter_repeated_template_issues_products = carried_outputs
    - return iter_repeated_template_issues_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_defined_callables:
      why:
        computes: "iter defined callables supplies repo-local behavior used by iter repeated template issues; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    .DocstringValidationIssue:
      why:
        constructs: "DocstringValidationIssue produces a value carried by iter repeated template issues; this edge is documented from the observed product position in the body."
    ._normalized_template_text:
      why:
        constructs: "normalized template text produces a value carried by iter repeated template issues; this edge is documented from the observed product position in the body."

    ..common.docstring.docstring_parser.parse_graph_block:
      why:
        constructs: "Builds parsed docstring sections whose prose is normalized for boilerplate detection."
    """
    config = schema_rules.module_dependencies.repeated_template_detection
    if not config.enabled:
        return

    records: list[tuple[str, int, str, str]] = []
    for node, node_id, _node_type in _iter_defined_callables(
        tree,
        include_nested=include_nested,
        include_private=include_private,
    ):
        doc = ast.get_docstring(node)
        if not doc:
            continue
        parsed = parse_graph_block(doc)
        names = {node_id, getattr(node, "name", "")}
        names.update(getattr(dependency, "name", "") for dependency in getattr(parsed, "module_calls", ()))
        names.update(getattr(dependency, "name", "") for dependency in getattr(parsed, "module_instantiates", ()))
        names.update(getattr(dependency, "id", "") for dependency in getattr(parsed, "dispatches", ()))
        candidates: list[tuple[str, str]] = []
        if getattr(parsed, "summary", ""):
            candidates.append(("summary", parsed.summary))
        for section in ("Intent", "Rationale"):
            for line in getattr(parsed, "sections", {}).get(section, ()):
                text = str(line).strip()
                if text:
                    candidates.append((section, text))
        for dependency in (
            tuple(getattr(parsed, "module_calls", ()))
            + tuple(getattr(parsed, "module_instantiates", ()))
            + tuple(getattr(parsed, "dispatches", ()))
        ):
            why = str(getattr(dependency, "why", "") or "").strip()
            if why:
                candidates.append(("why", why))
        for section, text in candidates:
            normalized = _normalized_template_text(text, names=names)
            if len(normalized) >= config.min_normalized_chars:
                records.append((normalized, node.lineno, node_id, section))

    grouped: dict[str, list[tuple[int, str, str]]] = {}
    for normalized, line_no, node_id, section in records:
        grouped.setdefault(normalized, []).append((line_no, node_id, section))

    for normalized, occurrences in grouped.items():
        if len(occurrences) < config.min_repetitions:
            continue
        line_no, node_id, section = occurrences[0]
        yield DocstringValidationIssue(
            path=path,
            code="docstring.repeated-template",
            message=(
                f"Repeated docstring template appears {len(occurrences)} times after replacing "
                "callable/dependency names. Rewrite each occurrence to describe the specific behavior "
                "or edge contribution. Repeated normalized text starts with: "
                f"{normalized[:120]!r}."
            ),
            line=line_no,
            node_id=node_id,
        )


def _iter_pseudocode_output_use_issues(
    path: Path,
    node_id: str,
    line_no: int,
    parsed: object,
    schema_rules: ModuleDependencyConfig,
) -> Iterable[DocstringValidationIssue]:
    """Require assigned dependency outputs to feed later pseudocode steps.

    Intent
    ------
    Expose the iter pseudocode output use issues step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps iter pseudocode output use issues behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set iter_pseudocode_output_use_issues_inputs = received_context
    - set iter_pseudocode_output_use_issues_products = carried_outputs
    - return iter_pseudocode_output_use_issues_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .DocstringValidationIssue:
      why:
        constructs: "DocstringValidationIssue produces a value carried by iter pseudocode output use issues; this edge is documented from the observed product position in the body."
    """
    if not schema_rules.pseudocode_quality.require_assigned_dependency_output_use:
        return

    def _step_search_text(step: object) -> str:
        """Build searchable pseudocode text for one dependency-output use check.

        Intent
        ------
        Expose the step search text step in AST-backed behavioral docstring validation so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps step search text behavior separate inside AST-backed behavioral docstring validation; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set step_search_text_inputs = received_context
        - return step_search_text_inputs

        Wraps
        -----
        - none
        """
        fields = (
            "text",
            "raw",
            "args",
            "condition",
            "loop_iterable",
            "resource_id",
            "expression",
        )
        return " ".join(
            str(getattr(step, field, "") or "")
            for field in fields
        )

    steps = tuple(getattr(parsed, "pseudocode_steps", ()))
    dependency_kinds = {"call", "dispatch", "instantiate"}
    for index, step in enumerate(steps):
        output = str(getattr(step, "output", "") or "").strip()
        if not output or getattr(step, "kind", "") not in dependency_kinds:
            continue
        later_text = "\n".join(_step_search_text(later_step) for later_step in steps[index + 1 :])
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(output)}(?![A-Za-z0-9_])", later_text):
            continue
        yield DocstringValidationIssue(
            path=path,
            code="docstring.pseudocode-output-unused",
            message=(
                f"Pseudocode assigns dependency output '{output}' but no later step uses it. "
                "Use the output in a following condition, call, construction, dispatch, or return, "
                "or omit the assignment."
            ),
            line=line_no,
            node_id=node_id,
        )


def validate_module_docstrings(
    module_path: str | Path,
    *,
    include_nested: bool = True,
    include_private: bool = True,
    require_module_docstring: bool = True,
    peer_module_paths: Iterable[str | Path] | None = None,
    allow_cross_file_ownership: bool = False,
    check_group: str = "all",
) -> tuple[DocstringValidationIssue, ...]:
    """Validate module and callable docstrings for configured syntax and behavior checks.

    Intent
    ------
    Run the parser-backed and AST-backed docstring checks for one Python module under
    the effective repository docstring policy.

    Rationale
    ---------
    This is the public enforcement boundary used by tests, certification, and graph
    extraction before documentation is trusted as machine-readable structure.

    Pseudocode
    ----------
    - set module_path_obj = normalized module path
    - set schema_rules = apply profile policy to module_path_obj
    - checkers = ._checker_from_group(check_group)
    - set collected_issues = collected_issues plus schema_rules plus checkers
    - return collected_issues

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ..common.docstring.docstring_policy.load_docstring_schema:
      why:
        reads: "Loads the standard and repository configuration that define validation policy."
    ..common.test_discovery.is_test_module:
      why:
        computes: "Selects syntax-only validation for test modules under the configured profile."
    ._iter_defined_callables:
      why:
        computes: "Streams callable AST nodes that require docstring validation."

    InstantiationsFromRepo
    ----------------------
    ..common.docstring.docstring_policy.apply_docstring_profiles:
      why:
        transforms: "Builds path-specific policy rules before any checks execute."
    ._collect_import_aliases:
      why:
        constructs: "Builds import-alias maps used to resolve documented dependency paths."
    ._collect_callable_import_aliases:
      why:
        constructs: "Builds callable-specific lexical alias maps without sibling or class leakage."
    ._collect_defined_symbols:
      why:
        constructs: "Builds the local symbol table used for dependency resolution checks."
    ._module_aliases:
      why:
        constructs: "Builds ownership alias candidates from the validated module path."
    ._collect_ownership_index:
      why:
        constructs: "Builds the owner registry consulted by ownership checks."
    ._module_docstring_issues_by_check:
      why:
        constructs: "Builds module-level diagnostics before callable checks run."
    ._checker_from_group:
      why:
        constructs: "Builds the syntax and behavioral checker sequence selected by check_group."
    ._dedupe_docstring_issues:
      why:
        constructs: "Builds the final duplicate-free diagnostic tuple returned to callers."
    ._iter_repeated_template_issues:
      why:
        constructs: "Builds repeated-template diagnostics after node-local checks finish."
    """

    path = Path(module_path)
    effective_check_group = (
        "syntax" if check_group == "all" and _is_repo_test_module(path) else check_group
    )
    schema_rules = apply_docstring_profiles(load_docstring_schema(), path)
    effective_allow_cross_file = (
        allow_cross_file_ownership and schema_rules.callable.ownership.cross_file_enabled
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    issues: list[DocstringValidationIssue] = []
    import_aliases, has_wildcard_import = _collect_import_aliases(tree)
    if has_wildcard_import:
        import_aliases["*"] = "*"
    aliases_by_node, shadows_by_node = _collect_callable_import_aliases(
        tree,
        import_aliases,
    )
    defined_symbols = _collect_defined_symbols(tree)

    ownership_aliases = _module_aliases(path)
    ownership_paths: list[Path] = [path]
    if effective_allow_cross_file and peer_module_paths is not None:
        for peer_path in peer_module_paths:
            peer = Path(peer_path)
            if peer != path:
                ownership_paths.append(peer)

    ownership_index = _collect_ownership_index(
        ownership_paths,
        ownership_section=schema_rules.module.ownership_registry.section,
    )

    module_doc = ast.get_docstring(tree)
    module_issues = _module_docstring_issues_by_check(
        path=path,
        module_doc=module_doc,
        require_module_docstring=require_module_docstring,
    )
    checkers = _checker_from_group(effective_check_group)
    for checker in checkers:
        issues.extend(checker._filter_scoped(module_issues))

    for node, node_id, node_type in _iter_defined_callables(
        tree,
        include_nested=include_nested,
        include_private=include_private,
    ):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        node_import_aliases = aliases_by_node.get(node, import_aliases)
        node_shadowed_names = shadows_by_node.get(node, set())
        for checker in checkers:
            issues.extend(
                checker.validate_node(
                    path=path,
                    node_id=node_id,
                    node_type=node_type,
                    node=node,
                    schema_rules=schema_rules,
                    ownership_aliases=ownership_aliases,
                    ownership_index=ownership_index,
                    allow_cross_file_ownership=effective_allow_cross_file,
                    import_aliases=node_import_aliases,
                    defined_symbols=defined_symbols,
                    shadowed_names=node_shadowed_names,
                )
            )

    issues.extend(
        _iter_repeated_template_issues(
            path,
            tree,
            include_nested=include_nested,
            include_private=include_private,
            schema_rules=schema_rules,
        )
    )

    return _dedupe_docstring_issues(issues)
