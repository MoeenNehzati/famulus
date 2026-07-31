#!/usr/bin/env python3
"""Validation helpers for docstring consistency and parser-level metadata quality."""

from __future__ import annotations

import ast
import builtins
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

from ..common.docstring.docstring_parser import (
    ParserIssue,
    check_graph_docstring,
    check_pipeline_docstring,
    parse_graph_block,
    parse_ownership_reference,
    parse_ownable_registry,
)
from ..common.docstring.docstring_schema import (
    ModuleDependencyConfig,
    DocstringSchema,
    OwnershipConfig,
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
    """Return the logical tail for a leading-dot relative dependency."""
    raw = (name or "").strip()
    if not raw.startswith("."):
        return ""
    return raw.lstrip(".")


def _dependency_name_variants(
    name: str,
    import_aliases: Mapping[str, str],
) -> tuple[str, ...]:
    """Build candidate forms for matching dependency mentions in pseudocode."""
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
    """Best-effort check that a dependency can be explained by imports or local symbol usage."""
    if not name:
        return False

    stripped = name.strip()
    if not stripped:
        return False

    relative = _relative_dependency_tail(stripped)
    if relative:
        relative_head = relative.split(".", 1)[0]
        relative_tail = relative.rsplit(".", 1)[-1]
        return (
            relative in defined_symbols
            or relative_head in defined_symbols
            or relative_tail in defined_symbols
            or relative_head in import_aliases
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
    """Collect identifier-like tokens from pseudocode lines for behavior checks."""
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
    """Collect canonical dependency markers from parser output."""
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
    """Match a name against a set of observed calls using import-aware equivalence."""
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
    """Match a name against collected pseudocode identifiers."""
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
    """Split ``section:name`` to scope + identifier if present."""
    raw = (ref or "").strip()
    if ":" not in raw:
        return None, raw
    section, name = [part.strip() for part in raw.split(":", 1)]
    if not section or not name:
        return None, raw
    return section, name


@dataclass(frozen=True)
class DocstringValidationIssue:
    """A single docstring validation finding."""

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
        "docstring.repeated-template",
        "docstring.missing-graphpipeline",
    }
)

_FALLBACK_BEHAVIORAL_CHECK_CODES: frozenset[str] = frozenset(
    {
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
        "docstring.wraps-incomplete",
        "docstring.wraps-unresolved-target",
        "docstring.pseudocode-dependency-missing",
        "docstring.pseudocode-dependency-undocumented",
        "docstring.owning-single",
    }
)


def _resolve_check_group_codes(check_group: str) -> tuple[str, ...]:
    """Resolve checker scope from policy if present, else use fallback buckets."""
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
    """Base class for grouping validation checks."""

    check_codes: frozenset[str] = frozenset()

    def __init__(self, check_codes: Iterable[str] | None = None) -> None:
        if check_codes is None:
            self.check_codes = frozenset()
        else:
            self.check_codes = frozenset(code for code in check_codes if code)

    def _issue_in_scope(self, issue: DocstringValidationIssue) -> bool:
        return issue.code in self.check_codes

    def _filter_scoped(self, issues: Iterable[DocstringValidationIssue]) -> tuple[DocstringValidationIssue, ...]:
        return tuple(issue for issue in issues if self._issue_in_scope(issue))


class SyntaxDocstringChecker(_BaseDocstringChecker):
    """Collect parse/format/syntax-facing docstring diagnostics."""

    def __init__(self, check_codes: Iterable[str] | None = None) -> None:
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
    ) -> tuple[DocstringValidationIssue, ...]:
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
            )
        )


class BehavioralDocstringChecker(_BaseDocstringChecker):
    """Collect semantic/behavioral docstring diagnostics."""

    def __init__(self, check_codes: Iterable[str] | None = None) -> None:
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
    ) -> tuple[DocstringValidationIssue, ...]:
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
            )
        )


def _should_require_docstring(node: ast.AST) -> bool:
    """All callables/classes require docstrings by default."""
    del node
    return True


def _iter_defined_callables(
    tree: ast.AST,
    include_nested: bool = True,
    include_private: bool = True,
) -> Iterable[tuple[ast.AST, str, str]]:
    """Yield callable/class nodes with fully qualified IDs."""

    def walk(parent_nodes: list[str], body: list[ast.stmt]) -> Iterable[tuple[ast.AST, str, str]]:
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
    """Collect imported symbol aliases for module-level dependency filtering."""
    aliases: dict[str, str] = {}
    has_wildcard_import = False

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                key = alias.asname or alias.name
                aliases[key.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                has_wildcard_import = True
            if node.module is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                key = alias.asname or alias.name
                aliases[key] = f"{node.module}.{alias.name}"

    return aliases, has_wildcard_import


def _collect_defined_symbols(tree: ast.AST) -> set[str]:
    """Collect top-level callable and class names."""
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
    return symbols


def _flatten_attribute_name(node: ast.AST) -> str | None:
    """Convert a call target expression into a dotted symbol name."""
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
    """Resolve a dotted dependency name through import alias mappings."""
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
    """Return ``True`` when declared and observed identifiers refer to the same symbol."""
    if declared == observed:
        return True
    declared_variants = set(_dependency_name_variants(declared, import_aliases))
    observed_variants = set(_dependency_name_variants(observed, import_aliases))
    return bool(declared_variants & observed_variants)


def _looks_like_constructor(name: str) -> bool:
    """Heuristic for identifying constructor calls from symbol names."""
    return name.split(".")[-1][:1].isupper()


def _collect_dependency_targets(
    node: ast.AST,
    defined_symbols: set[str],
    import_aliases: Mapping[str, str],
    dependency_rules: ModuleDependencyConfig,
) -> tuple[set[str], set[str]]:
    """Collect observed module calls and instantiations from one callable node."""
    calls: set[str] = set()
    instantiations: set[str] = set()
    has_calls = bool(dependency_rules.calls_section)
    has_instantiations = bool(dependency_rules.instantiates_section)
    walk_roots: Iterable[ast.AST]
    if isinstance(node, ast.ClassDef):
        walk_roots = tuple(
            statement
            for statement in node.body
            if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        walk_roots = tuple(node.body)
    else:
        walk_roots = (node,)

    for root in walk_roots:
        for statement in ast.walk(root):
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
            if _looks_like_constructor(normalized):
                if has_instantiations:
                    instantiations.add(normalized)
                elif has_calls:
                    calls.add(normalized)
            elif has_calls:
                calls.add(normalized)

    return calls, instantiations


def _dispatch_id_variants(dispatch_id: str) -> set[str]:
    """Return equivalent logical spellings for a dispatch interface id."""
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
    """Return true when two dispatch ids are equivalent logical spellings."""
    return bool(_dispatch_id_variants(left) & _dispatch_id_variants(right))


def _collect_known_dispatch_ids(repo_root: Path | None = None) -> set[str]:
    """Collect public interface ids advertised by skill contracts."""
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
    """Collect dispatcher interface ids from literal dispatcher call arguments."""
    observed: set[str] = set()
    for statement in ast.walk(node):
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
) -> Iterable[DocstringValidationIssue]:
    """Emit a warning when a declaration is not seen in observed calls."""
    for observed_name in observed:
        if _dependency_matches(declared, observed_name, import_aliases):
            return
    yield DocstringValidationIssue(
        path=path,
        code="docstring.module-dependency-not-observed",
        message=f"Declared dependency '{declared}' is not observed in function body.",
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
) -> Iterable[DocstringValidationIssue]:
    """Emit a warning when observed dependency is not documented."""
    for documented_name in documented:
        if _dependency_matches(observed, documented_name, import_aliases):
            return
    yield DocstringValidationIssue(
        path=path,
        code="docstring.module-dependency-undocumented",
        message=f"Observed dependency '{observed}' is not listed in docstring.",
        line=line_no,
        node_id=node_id,
    )


def _dependency_path_is_allowed(name: str, allowed_abs: tuple[str, ...]) -> bool:
    """Return True iff a dependency path is explicitly relative or allowed absolute."""
    raw = (name or "").strip()
    if not raw:
        return False
    if raw.startswith("."):
        return True
    return raw.split(".", 1)[0] in set(allowed_abs)


def _emit_dependency_path_issue(
    path: Path,
    node_id: str,
    line_no: int,
    *,
    declared: str,
    section: str,
    allowed_abs: tuple[str, ...],
) -> Iterable[DocstringValidationIssue]:
    """Emit portability warning for unrooted dependency logical paths."""
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
    """Validate callable module dependency sections with static inference."""
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
    """Validate Wraps entries against parser expectations and body reachability."""
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
    """Collect several module identifiers used for ownership lookups."""
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
    """Collect parsed ``Ownable`` registries from candidate modules."""
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
    """Resolve owner registry and resolved module key from a module hint."""
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
    """Project parser issues to a shared issue representation."""

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
    """Validate ``Owns`` references with optional cross-module lookup."""
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
    """Keep a narrow compatibility path for parser-level issues on module docs."""
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
) -> tuple[DocstringValidationIssue, ...]:
    """Validate docstring presence and parse quality for one callable/class node."""
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

    issues.extend(
        _iter_parser_issue_records(
            path=path,
            node_id=node_id,
            parser_issues=check_graph_docstring(doc, schema_rules=schema_rules),
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
            ownership_config=schema_rules.callable.ownership,
            local_aliases=ownership_aliases,
            ownership_index=ownership_index,
            allow_cross_file=allow_cross_file_ownership,
        )
    )
    observed_calls, observed_instantiations = _collect_dependency_targets(
        node=node,
        defined_symbols=defined_symbols,
        import_aliases=import_aliases,
        dependency_rules=schema_rules.module_dependencies,
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
                schema_rules=schema_rules.module_dependencies,
                import_aliases=import_aliases,
                defined_symbols=defined_symbols,
            )
        )

    return tuple(issues)


def _checker_from_group(check_group: str) -> tuple[_BaseDocstringChecker, ...]:
    """Resolve requested checker set from string mode."""
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
    """Return validation issues without duplicate records from overlapping groups."""
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
    """Normalize one prose sentence for repeated-template detection."""
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
    """Detect repeated normalized docstring prose templates in one module."""
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
                "or edge contribution."
            ),
            line=line_no,
            node_id=node_id,
        )


def _path_matches_profile_pattern(path: Path, pattern: str) -> bool:
    """Return true when a config profile pattern applies to a module path."""
    raw_pattern = (pattern or "").strip()
    if not raw_pattern:
        return False

    candidates: set[str] = {path.as_posix().lstrip("/")}
    try:
        candidates.add(path.resolve().relative_to(Path.cwd().resolve()).as_posix())
    except ValueError:
        pass

    parts = path.as_posix().strip("/").split("/")
    for index in range(len(parts)):
        candidates.add("/".join(parts[index:]))

    return any(
        candidate == raw_pattern or PurePosixPath(candidate).match(raw_pattern)
        for candidate in candidates
        if candidate
    )


def _apply_docstring_profiles(schema_rules: DocstringSchema, path: Path) -> DocstringSchema:
    """Apply repo-configured path profiles to the effective validator policy."""
    module_rules = schema_rules.module_dependencies
    callable_rules = schema_rules.callable
    for profile in getattr(schema_rules.config, "profiles", ()):
        if not any(
            _path_matches_profile_pattern(path, pattern)
            for pattern in getattr(profile, "applies_to", ())
        ):
            continue

        if getattr(profile, "callable_require_docstrings", None) is not None:
            callable_rules = replace(
                callable_rules,
                require_docstrings=bool(profile.callable_require_docstrings),
            )
        if getattr(profile, "callable_required_sections", None) is not None:
            callable_rules = replace(
                callable_rules,
                required_sections=tuple(profile.callable_required_sections or ()),
            )
        if getattr(profile, "callable_min_pseudocode_steps", None) is not None:
            min_steps = int(profile.callable_min_pseudocode_steps or 0)
            callable_rules = replace(
                callable_rules,
                min_pseudocode_steps=min_steps,
                pseudocode=replace(
                    callable_rules.pseudocode,
                    min_steps=min_steps,
                ),
            )

        checks = getattr(profile, "checks", {})
        if "repeated_template_detection" in checks:
            module_rules = replace(
                module_rules,
                repeated_template_detection=replace(
                    module_rules.repeated_template_detection,
                    enabled=bool(checks["repeated_template_detection"]),
                ),
            )
        if "pseudocode_output_use" in checks or "pseudocode_dataflow" in checks:
            enabled = bool(
                checks.get(
                    "pseudocode_output_use",
                    checks.get("pseudocode_dataflow", False),
                )
            )
            module_rules = replace(
                module_rules,
                pseudocode_quality=replace(
                    module_rules.pseudocode_quality,
                    require_assigned_dependency_output_use=enabled,
                ),
            )
        if "dependency_why_action" in checks:
            module_rules = replace(
                module_rules,
                dependency_why=replace(
                    module_rules.dependency_why,
                    allow_legacy_string=not bool(checks["dependency_why_action"]),
                ),
            )

    if module_rules is schema_rules.module_dependencies and callable_rules is schema_rules.callable:
        return schema_rules
    return replace(
        schema_rules,
        callable=callable_rules,
        module_dependencies=module_rules,
    )


def _iter_pseudocode_output_use_issues(
    path: Path,
    node_id: str,
    line_no: int,
    parsed: object,
    schema_rules: ModuleDependencyConfig,
) -> Iterable[DocstringValidationIssue]:
    """Require assigned dependency outputs to feed later pseudocode steps."""
    if not schema_rules.pseudocode_quality.require_assigned_dependency_output_use:
        return

    def _step_search_text(step: object) -> str:
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
    """Validate module-level and callable-level docstring expectations.

    Args:
        check_group:
            One of ``"all"``, ``"syntax"``, or ``"behavioral"``.
            ``"syntax"`` filters checks like malformed docstring blocks and section
            violations. ``"behavioral"`` filters checks like ownership/dependency
            semantics and callability consistency.
    """

    path = Path(module_path)
    effective_check_group = (
        "syntax" if check_group == "all" and _is_repo_test_module(path) else check_group
    )
    schema_rules = _apply_docstring_profiles(load_docstring_schema(), path)
    effective_allow_cross_file = (
        allow_cross_file_ownership and schema_rules.callable.ownership.cross_file_enabled
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    issues: list[DocstringValidationIssue] = []
    import_aliases, _ = _collect_import_aliases(tree)
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
                    import_aliases=import_aliases,
                    defined_symbols=defined_symbols,
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
