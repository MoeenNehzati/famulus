"""Validate and materialize an authoritative standard import closure.

The extractor is the shared boundary between authored standard documents and
their consumers.  It accepts one leaf document, forces validation of every
pinned import, resolves the complete closure, evaluates applicability against
declared and caller-supplied facts, and returns a compact projection suitable
for deterministic tools or an LLM interface.

Consumers must select the appropriate leaf.  They do not follow imports,
reinterpret predicates, or reconstruct evidence and remedy relationships.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

import yaml


class StandardExtractionError(ValueError):
    """Raised when a standard closure or extraction query is invalid."""


@dataclass(frozen=True)
class StandardRecord:
    """One independently queryable object from a validated standard closure."""

    document: str
    section: str
    record_id: str
    kind: str
    path: str
    ancestors: tuple[str, ...]
    applicability: str
    missing_facts: tuple[str, ...]
    data: dict[str, Any]


@dataclass(frozen=True)
class MatchEvidence:
    """The concrete record field that made one query predicate true."""

    selector: str
    op: str
    path: str
    value: Any


_SECTION_KINDS = {
    "imports": "import",
    "artifacts": "artifact",
    "links": "link",
    "checks": "check",
    "tests": "test",
    "assurances": "assurance",
    "semantic_reviews": "semantic-review",
    "evidence_claims": "evidence-claim",
    "schema_authorities": "schema-authority",
    "schema_authority_links": "schema-authority-link",
    "sources": "source",
    "source_units": "source-unit",
    "external_exceptions": "external-exception",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load one mapping-valued YAML document with a path-specific error."""

    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: document root must be a mapping")
    return value


def _repository_path(repo_root: Path, path: Path) -> Path:
    """Resolve a standard path below its repository without permitting escape."""

    candidate = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
    if not candidate.is_relative_to(repo_root):
        raise ValueError(f"standard leaf is outside repository root: {path}")
    if not candidate.is_file():
        raise ValueError(f"standard leaf does not exist: {candidate}")
    return candidate


def _validator_module(repo_root: Path):
    """Load the repository validator that owns schema and predicate semantics."""

    path = repo_root / "references" / "standards" / "validate_standard_v6.py"
    if not path.is_file():
        raise ValueError(f"cannot load standard validator at {path}")
    module_name = (
        "officina_standard_validator_"
        + hashlib.sha256(str(repo_root).encode("utf-8")).hexdigest()[:12]
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load standard validator at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _combine_state(left: str, right: str) -> str:
    """Conjoin two true, false, or unknown applicability states."""

    if "false" in {left, right}:
        return "false"
    if "unknown" in {left, right}:
        return "unknown"
    return "true"


def _missing_predicate_facts(
    predicate: dict[str, Any],
    facts: dict[str, Any],
    evaluator: Any,
) -> set[str]:
    """Return absent facts that keep a predicate in the unknown state."""

    if evaluator(predicate, facts) != "unknown":
        return set()
    if "fact" in predicate:
        fact = predicate["fact"]
        return {fact} if fact not in facts else set()
    if "not" in predicate:
        return _missing_predicate_facts(predicate["not"], facts, evaluator)
    operator = "all" if "all" in predicate else "any"
    return set().union(
        *(
            _missing_predicate_facts(child, facts, evaluator)
            for child in predicate[operator]
            if evaluator(child, facts) == "unknown"
        )
    )


def _join_path(parent: str, child: str) -> str:
    return child if not parent else f"{parent}.{child}"


def _select_parts(
    path: str, value: Any, parts: Sequence[str]
) -> list[tuple[str, Any]]:
    if not parts:
        return [(path, value)]
    part = parts[0]
    rest = parts[1:]
    if part == "**":
        matches = _select_parts(path, value, rest)
        for child_path, child in _iter_children(path, value):
            matches.extend(_select_parts(child_path, child, parts))
        return matches
    matches: list[tuple[str, Any]] = []
    for child_path, child in _select_child(path, value, part):
        matches.extend(_select_parts(child_path, child, rest))
    return matches


def _iter_children(path: str, value: Any) -> list[tuple[str, Any]]:
    if isinstance(value, Mapping):
        return [(_join_path(path, str(key)), child) for key, child in value.items()]
    if isinstance(value, list):
        return [
            (_join_path(path, str(index)), child)
            for index, child in enumerate(value)
        ]
    return []


def _select_child(path: str, value: Any, part: str) -> list[tuple[str, Any]]:
    if part == "*":
        return _iter_children(path, value)
    if isinstance(value, Mapping) and part in value:
        return [(_join_path(path, part), value[part])]
    if isinstance(value, list) and part.isdigit():
        index = int(part)
        if 0 <= index < len(value):
            return [(_join_path(path, part), value[index])]
    return []


def select_values(data: Any, selector: str) -> list[tuple[str, Any]]:
    """Resolve dotted ``*`` and ``**`` selectors against structured data."""

    if not isinstance(selector, str) or not selector:
        raise StandardExtractionError("selector must be a non-empty string")
    return _select_parts("", data, selector.split("."))


def _record_builtins(record: StandardRecord) -> dict[str, Any]:
    return {
        "$document": record.document,
        "$section": record.section,
        "$id": record.record_id,
        "$kind": record.kind,
        "$path": record.path,
        "$ancestors": list(record.ancestors),
        "$applicability": record.applicability,
        "$missing_facts": list(record.missing_facts),
    }


def _record_values(
    record: StandardRecord, selector: str
) -> list[tuple[str, Any]]:
    builtins = _record_builtins(record)
    if selector in builtins:
        return [(selector, builtins[selector])]
    return select_values(record.data, selector)


def _regex_flags(raw_flags: Any) -> int:
    if raw_flags in (None, ""):
        return 0
    if not isinstance(raw_flags, str):
        raise StandardExtractionError("regex flags must be a string")
    flags = 0
    for flag in raw_flags:
        if flag == "i":
            flags |= re.IGNORECASE
        elif flag == "m":
            flags |= re.MULTILINE
        elif flag == "s":
            flags |= re.DOTALL
        else:
            raise StandardExtractionError(f"unsupported regex flag {flag!r}")
    return flags


def _stringify_for_regex(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _contains(value: Any, expected: Any) -> bool:
    if isinstance(value, Mapping):
        return expected in value
    if isinstance(value, (list, tuple, set)):
        return expected in value
    if isinstance(value, str):
        return str(expected) in value
    return False


def _match_predicate(
    record: StandardRecord, predicate: Mapping[str, Any]
) -> tuple[bool, list[MatchEvidence]]:
    selector = predicate.get("path")
    op = predicate.get("op", "exists")
    if not isinstance(selector, str) or not selector:
        raise StandardExtractionError("predicate requires non-empty string path")
    if not isinstance(op, str):
        raise StandardExtractionError("predicate op must be a string")
    values = _record_values(record, selector)
    if op == "exists":
        matched_values = values
    elif op == "missing":
        matched_values = [(selector, None)] if not values else []
    elif op in {"eq", "neq", "contains"}:
        expected = predicate.get("value")
        matched_values = [
            (path, value)
            for path, value in values
            if (
                (op == "eq" and value == expected)
                or (op == "neq" and value != expected)
                or (op == "contains" and _contains(value, expected))
            )
        ]
    elif op in {"regex", "not_regex"}:
        pattern = predicate.get("pattern")
        if not isinstance(pattern, str):
            raise StandardExtractionError(
                f"{selector}: regex predicate requires string pattern"
            )
        try:
            regex = re.compile(
                pattern, _regex_flags(predicate.get("flags", ""))
            )
        except re.error as exc:
            raise StandardExtractionError(
                f"{selector}: invalid regex {pattern!r}: {exc}"
            ) from exc
        matched_values = []
        for path, value in values:
            matched = regex.search(_stringify_for_regex(value)) is not None
            if (op == "regex" and matched) or (
                op == "not_regex" and not matched
            ):
                matched_values.append((path, value))
    else:
        raise StandardExtractionError(
            f"{selector}: unsupported filter op {op!r}"
        )
    return bool(matched_values), [
        MatchEvidence(selector, op, path, value)
        for path, value in matched_values
    ]


def _matches_filter(
    record: StandardRecord,
    filter_spec: Mapping[str, Any] | Sequence[Any] | None,
) -> tuple[bool, list[MatchEvidence]]:
    if filter_spec is None or filter_spec == {}:
        return True, []
    if isinstance(filter_spec, Sequence) and not isinstance(
        filter_spec, (str, bytes)
    ):
        filter_spec = {"all": filter_spec}
    if not isinstance(filter_spec, Mapping):
        raise StandardExtractionError("filter must be a mapping, list, or null")
    if "all" in filter_spec:
        children = filter_spec["all"]
        if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
            raise StandardExtractionError("filter.all must be a list")
        evidence: list[MatchEvidence] = []
        for child in children:
            matched, child_evidence = _matches_filter(record, child)
            if not matched:
                return False, []
            evidence.extend(child_evidence)
        return True, evidence
    if "any" in filter_spec:
        children = filter_spec["any"]
        if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
            raise StandardExtractionError("filter.any must be a list")
        evidence: list[MatchEvidence] = []
        for child in children:
            matched, child_evidence = _matches_filter(record, child)
            if matched:
                evidence.extend(child_evidence)
        return bool(evidence), evidence
    if "not" in filter_spec:
        matched, _ = _matches_filter(record, filter_spec["not"])
        return (
            (False, [])
            if matched
            else (
                True,
                [MatchEvidence("$not", "not", "", True)],
            )
        )
    return _match_predicate(record, filter_spec)


def _collapse_selected_values(
    selected: Sequence[tuple[str, Any]], *, force_list: bool = False
) -> Any:
    if not selected:
        return []
    if len(selected) == 1 and not force_list:
        return selected[0][1]
    return [value for _, value in selected]


def _record_identity(record: StandardRecord) -> dict[str, Any]:
    value: dict[str, Any] = {
        "document": record.document,
        "section": record.section,
        "id": record.record_id,
        "kind": record.kind,
        "path": record.path,
        "ancestors": list(record.ancestors),
        "applicability": record.applicability,
    }
    if record.missing_facts:
        value["missing_facts"] = list(record.missing_facts)
    return value


def _project_record(
    record: StandardRecord,
    select_spec: str | Sequence[Any] | None,
    *,
    matches: Sequence[MatchEvidence],
    explain: bool,
) -> dict[str, Any]:
    if select_spec is None:
        row = _record_identity(record)
    elif select_spec == "all":
        row = {**_record_identity(record), "data": record.data}
    else:
        if not isinstance(select_spec, Sequence) or isinstance(
            select_spec, (str, bytes)
        ):
            raise StandardExtractionError("select must be 'all', a list, or null")
        row = {}
        values: dict[str, Any] = {}
        identity = _record_identity(record)
        for item in select_spec:
            if isinstance(item, str):
                if item in identity:
                    row[item] = identity[item]
                else:
                    selected = select_values(record.data, item)
                    values[item] = _collapse_selected_values(
                        selected, force_list="*" in item
                    )
                continue
            if isinstance(item, Mapping):
                alias = item.get("as")
                selector = item.get("path")
                if not isinstance(alias, str) or not alias:
                    raise StandardExtractionError(
                        "select mapping requires non-empty string 'as'"
                    )
                if not isinstance(selector, str) or not selector:
                    raise StandardExtractionError(
                        f"select mapping {alias!r} requires non-empty string 'path'"
                    )
                selected = _record_values(record, selector)
                values[alias] = _collapse_selected_values(
                    selected, force_list="*" in selector
                )
                continue
            raise StandardExtractionError(
                "select entries must be strings or mappings"
            )
        if values:
            row["values"] = values
    if explain:
        row["matches"] = [asdict(match) for match in matches]
    return row


def query_standard_records(
    records: Sequence[StandardRecord],
    query: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Filter and project closure records with blueprint-search-style queries."""

    query = query or {}
    if not isinstance(query, Mapping):
        raise StandardExtractionError("query must be a mapping")
    unknown = set(query) - {"filter", "select", "explain"}
    if unknown:
        raise StandardExtractionError(
            "unsupported query fields: " + ", ".join(sorted(unknown))
        )
    rows = []
    for record in records:
        matched, evidence = _matches_filter(record, query.get("filter"))
        if matched:
            rows.append(
                _project_record(
                    record,
                    query.get("select"),
                    matches=evidence,
                    explain=bool(query.get("explain", False)),
                )
            )
    return rows


def extract_standard(
    repo_root: Path,
    leaf_path: Path,
    *,
    facts: dict[str, Any] | None = None,
    query: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Query one validated, deduplicated, applicability-aware standard closure.

    Args:
        repo_root: Repository containing the standard schema, validator, and all
            artifacts referenced by the closure.
        leaf_path: Absolute or repository-relative path to the most specialized
            standard.  Every imported standard is discovered from this document.
        facts: Inspected target facts used in addition to the closure's declared
            domain facts when evaluating ``applies_when`` predicates.
        query: Optional blueprint-search-style field filter and projection. The
            filter supports ``all``, ``any``, ``not``, dotted paths, ``*`` and
            ``**`` wildcards, equality, containment, and regular expressions.

    Raises:
        ValueError: If the leaf escapes the repository, the validator or any
            pinned import is invalid, facts contradict the closure, or an
            imported artifact cannot be resolved.

    The returned document order is imports before importers.  Every record
    carries its document, section, semantic ancestry, and true, false, or
    unknown applicability; unknown records name facts needed for a later query.
    """

    repo_root = Path(repo_root).resolve()
    leaf_path = _repository_path(repo_root, Path(leaf_path))
    validator = _validator_module(repo_root)
    errors = validator.validate_file(leaf_path, root=repo_root)
    if errors:
        raise ValueError("invalid standard closure: " + "; ".join(errors))

    documents: list[tuple[Path, dict[str, Any]]] = []
    seen: dict[str, Path] = {}

    def visit(path: Path) -> None:
        path = _repository_path(repo_root, path)
        document = _load_yaml(path)
        document_id = document["id"]
        previous = seen.get(document_id)
        if previous is not None:
            if previous != path:
                raise ValueError(
                    f"standard id {document_id!r} resolves to both {previous} and {path}"
                )
            return
        for declaration in document.get("imports", {}).values():
            artifact = document["artifacts"][declaration["artifact"]["ref"]]
            visit(repo_root / artifact["path"])
        seen[document_id] = path
        documents.append((path, document))

    visit(leaf_path)
    effective_facts: dict[str, Any] = {}
    for _, document in documents:
        for name, value in document.get("domain_facts", {}).items():
            previous = effective_facts.get(name, value)
            if type(previous) is not type(value) or previous != value:
                raise ValueError(f"standard closure disagrees on domain fact {name}")
            effective_facts[name] = value
    for name, value in (facts or {}).items():
        if name in effective_facts:
            previous = effective_facts[name]
            if type(previous) is not type(value) or previous != value:
                raise ValueError(
                    f"target fact {name}={value!r} contradicts closure fact {previous!r}"
                )
        effective_facts[name] = value

    records: list[StandardRecord] = []
    document_collections = {"standards", *_SECTION_KINDS}
    for _, document in documents:
        records.append(
            StandardRecord(
                document=document["id"],
                section="document",
                record_id=document["id"],
                kind="standard-document",
                path="document",
                ancestors=(),
                applicability="true",
                missing_facts=(),
                data={
                    key: value
                    for key, value in document.items()
                    if key not in document_collections and key != "id"
                },
            )
        )

    def record_semantic(
        document_id: str,
        item: dict[str, Any],
        item_id: str,
        kind: str,
        parent_state: str,
        parent_missing: set[str],
        ancestors: tuple[str, ...],
    ) -> tuple[str, set[str]]:
        predicate = item.get("applies_when")
        own = (
            validator.evaluate_predicate(predicate, effective_facts)
            if predicate is not None
            else "true"
        )
        state = _combine_state(parent_state, own)
        missing = set(parent_missing)
        if predicate is not None:
            missing.update(
                _missing_predicate_facts(
                    predicate,
                    effective_facts,
                    validator.evaluate_predicate,
                )
            )
        records.append(
            StandardRecord(
                document=document_id,
                section="standards",
                record_id=item_id,
                kind=kind,
                path="/".join(("standards", *ancestors, item_id)),
                ancestors=ancestors,
                applicability=state,
                missing_facts=tuple(sorted(missing)) if state == "unknown" else (),
                data={
                    key: value
                    for key, value in item.items()
                    if key not in {"children", "kind", "id"}
                },
            )
        )
        return state, missing

    def walk(
        document_id: str,
        items: list[dict[str, Any]],
        parent_state: str = "true",
        parent_missing: set[str] | None = None,
        ancestors: tuple[str, ...] = (),
    ) -> None:
        inherited_missing = parent_missing or set()
        for item in items:
            state, missing = record_semantic(
                document_id,
                item,
                item["id"],
                item["kind"],
                parent_state,
                inherited_missing,
                ancestors,
            )
            if item["kind"] == "rule":
                for assertion in item["assertions"]:
                    record_semantic(
                        document_id,
                        assertion,
                        f'{item["id"]}#{assertion["id"]}',
                        "assertion",
                        state,
                        missing,
                        (*ancestors, item["id"]),
                    )
            if item["kind"] == "procedure":
                for step in item["steps"]:
                    record_semantic(
                        document_id,
                        step,
                        f'{item["id"]}#{step["id"]}',
                        "step",
                        state,
                        missing,
                        (*ancestors, item["id"]),
                    )
            walk(
                document_id,
                item.get("children", []),
                state,
                missing,
                (*ancestors, item["id"]),
            )

    for _, document in documents:
        walk(document["id"], document["standards"])
    for _, document in documents:
        for section, kind in _SECTION_KINDS.items():
            for record_id, data in document.get(section, {}).items():
                records.append(
                    StandardRecord(
                        document=document["id"],
                        section=section,
                        record_id=record_id,
                        kind=kind,
                        path=f"{section}/{record_id}",
                        ancestors=(),
                        applicability="true",
                        missing_facts=(),
                        data=dict(data),
                    )
                )

    return {
        "leaf": leaf_path.relative_to(repo_root).as_posix(),
        "facts": effective_facts,
        "documents": [
            {
                "id": document["id"],
                "path": path.relative_to(repo_root).as_posix(),
                "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path, document in documents
        ],
        "records": query_standard_records(records, query),
    }


__all__ = [
    "MatchEvidence",
    "StandardExtractionError",
    "StandardRecord",
    "extract_standard",
    "query_standard_records",
    "select_values",
]
