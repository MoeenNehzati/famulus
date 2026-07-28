#!/usr/bin/env python3
"""Validation helpers for docstring consistency and parser-level metadata quality."""

from __future__ import annotations

import ast
import builtins
import re
from dataclasses import dataclass
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
from ..common.docstring.docstring_schema import (
    ModuleDependencyConfig,
    DocstringSchema,
    OwnershipConfig,
    load_docstring_check_categories,
    load_docstring_schema,
)

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
    "elif",
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
    normalized = _normalize_dependency_name(stripped, import_aliases)
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
    pseudocode_tokens: set[str],
    import_aliases: Mapping[str, str],
) -> bool:
    """Match a name against collected pseudocode identifiers."""
    for variant in _dependency_name_variants(name, import_aliases):
        if variant in pseudocode_tokens:
            return True
    return False


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
    return (
        _normalize_dependency_name(declared, import_aliases) == observed
        or _normalize_dependency_name(observed, import_aliases) == declared
    )


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

    for statement in ast.walk(node):
        if not isinstance(statement, ast.Call):
            continue

        target_name = _flatten_attribute_name(statement.func)
        if target_name is None:
            continue

        target_name = target_name.strip()
        if not target_name:
            continue

        base = target_name.split(".", 1)[0]
        if (
            not base
            or base in _IGNORED_CALL_BASES
            or base in _BUILTIN_SYMBOLS
            or base in defined_symbols
        ):
            continue

        if dependency_rules.ignore_non_external and base not in import_aliases:
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

    if schema_rules.validate_declared_calls:
        for dependency in documented_calls:
            if dependency.implicit:
                continue
            if schema_rules.validate_declared_targets_resolved and not _dependency_is_resolved(
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
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.pseudocode-dependency-missing",
                    message=(
                        f"Declared dependency '{dependency.name}' is not mentioned in "
                        "Pseudocode."
                    ),
                    line=line_no,
                    node_id=node_id,
                )

    if schema_rules.validate_declared_instantiations:
        for dependency in documented_instantiations:
            if dependency.implicit:
                continue
            if schema_rules.validate_declared_targets_resolved and not _dependency_is_resolved(
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
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.pseudocode-dependency-missing",
                    message=(
                        f"Declared dependency '{dependency.name}' is not mentioned in "
                        "Pseudocode."
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
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.pseudocode-dependency-undocumented",
                    message=(
                        f"Observed dependency '{observed}' is not mentioned in "
                        "Pseudocode."
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
            ):
                yield DocstringValidationIssue(
                    path=path,
                    code="docstring.pseudocode-dependency-undocumented",
                    message=(
                        f"Observed dependency '{observed}' is not mentioned in "
                        "Pseudocode."
                    ),
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
            code="docstring.formatting",
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
            parser_issues=check_graph_docstring(doc),
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
    schema_rules = load_docstring_schema()
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
    checkers = _checker_from_group(check_group)
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

    return tuple(issues)
