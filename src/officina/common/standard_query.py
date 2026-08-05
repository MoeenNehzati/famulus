"""Query one explicitly selected standard and its pinned import closure.

The query is deliberately read-only. Callers select the root standard from
their existing task context; this module validates its complete import closure
and projects requirements, context, evidence, remedies, or generic records.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from officina.common.standard_extractor import extract_standard
from officina.runtime.python_machine_interface import PythonMachineInterface
REFACTOR_RECORD_SECTIONS = (
    r"^(standards|imports|links|artifacts|checks|tests|assurances|"
    r"semantic_reviews|evidence_claims)$"
)
STANDARD_VIEWS = ("requirements", "context", "evidence", "remedies", "full")
CONTEXT_KINDS = {"family", "definition", "guidance", "example"}
EVIDENCE_SECTIONS = {
    "checks",
    "tests",
    "assurances",
    "semantic_reviews",
    "evidence_claims",
}


def materialize_standard(
    repo_root: Path,
    standard_path: Path,
    *,
    facts: dict[str, Any] | None = None,
    view: str = "requirements",
    refs: Sequence[Mapping[str, str]] | None = None,
    record_query: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one explicitly selected standard closure into a query view.

    Intent
    ------
    Validate and extract one caller-selected root, then return only the requested
    requirements, context, evidence, remedies, full data, or generic records.

    Rationale
    ---------
    Explicit roots keep task context with the caller while one validated closure
    preserves imported standards and exact document-reference identities.

    Pseudocode
    ----------
    - set selected_refs = validated document and semantic reference pairs
    - set extracted = validated root closure and relevant standard records
    - set semantic_views = applicability requirements context evidence and remedies
    - if view is full or requirements:
      - return the corresponding root projection
    - set selected_lineage = selected references plus semantic ancestors
    - return the selected context evidence or remedy projection

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.common.standard_extractor.extract_standard:
      why:
        constructs: "Builds the validated closure facts documents and records used by every view."
    ._combine_applicability:
      why:
        constructs: "Builds the applicability state of each projected remedy link."
    ._compact_context_index:
      why:
        constructs: "Builds the lightweight context index returned with requirements."
    ._compact_requirements:
      why:
        constructs: "Builds applicable and unresolved normative requirement buckets."
    ._normalize_standard_refs:
      why:
        constructs: "Builds the exact selected reference set after input validation."
    ._select_context:
      why:
        constructs: "Builds interpretive context for the selected semantic families."
    ._select_evidence:
      why:
        constructs: "Builds connected evidence and its referenced artifacts."
    ._selected_semantic_lineage:
      why:
        constructs: "Builds the semantic lineage used by evidence and remedy selection."
    """

    repo_root = Path(repo_root).resolve()
    standard_path = Path(standard_path)

    if view not in STANDARD_VIEWS:
        raise ValueError(
            f"unsupported standards view {view!r}; choose one of: "
            + ", ".join(STANDARD_VIEWS)
        )

    if record_query is not None:
        if refs:
            raise ValueError("record queries cannot be combined with standard refs")
        extracted = extract_standard(
            repo_root,
            standard_path,
            facts=facts,
            query=record_query,
        )
        return {
            "standard": extracted["leaf"],
            "view": "query",
            "available_views": list(STANDARD_VIEWS),
            "facts": extracted["facts"],
            "documents": extracted["documents"],
            "records": extracted["records"],
        }

    selected_refs = _normalize_standard_refs(refs)
    if view in {"context", "evidence", "remedies"} and not selected_refs:
        raise ValueError(f"{view} view requires at least one standard ref")
    if view in {"requirements", "full"} and selected_refs:
        raise ValueError(f"{view} view does not accept standard refs")

    extracted = extract_standard(
        repo_root,
        standard_path,
        facts=facts,
        query={
            "filter": {
                "path": "$section",
                "op": "regex",
                "pattern": REFACTOR_RECORD_SECTIONS,
            },
            "select": "all",
        },
    )
    records = extracted["records"]
    closure_documents = {document["id"] for document in extracted["documents"]}
    outside = sorted(
        (document, record_ref)
        for document, record_ref in selected_refs
        if document not in closure_documents
    )
    if outside:
        rendered = ", ".join(
            f"{document}:{record_ref}" for document, record_ref in outside
        )
        raise ValueError("standard refs are outside selected closure: " + rendered)
    semantic = [record for record in records if record["section"] == "standards"]
    states = {
        (record["document"], record["id"]): record["applicability"]
        for record in semantic
    }
    item_buckets: dict[str, list[dict[str, Any]]] = {
        "true": [],
        "false": [],
        "unknown": [],
    }
    for record in semantic:
        state = record["applicability"]
        kind = record["kind"]
        data = record["data"]
        if kind in {"assertion", "step"} and "applies_when" not in data:
            continue
        if kind == "guidance" and state == "false":
            continue
        item = {
            key: record[key]
            for key in ("document", "kind", "id", "ancestors", "applicability")
        }
        if state != "false":
            item["content"] = data
        if state == "unknown":
            item["missing_facts"] = record["missing_facts"]
        item_buckets[state].append(item)

    aliases: dict[str, dict[str, str]] = {}
    for record in records:
        if record["section"] == "imports":
            aliases.setdefault(record["document"], {})[record["id"]] = record[
                "data"
            ]["standard_id"]

    remedies = []
    for record in records:
        if record["section"] != "links" or record["data"]["relation"] != "remedied-by":
            continue
        document_id = record["document"]
        link = record["data"]
        document_aliases = aliases.get(document_id, {})
        source_document = document_aliases.get(
            link["source"].get("document"), document_id
        )
        target_document = document_aliases.get(
            link["target"].get("document"), document_id
        )
        source_state = states.get(
            (source_document, link["source"]["ref"]), "unknown"
        )
        target_state = states.get(
            (target_document, link["target"]["ref"]), "unknown"
        )
        applicability = _combine_applicability(source_state, target_state)
        if applicability == "false":
            continue
        source = dict(link["source"])
        source["document"] = source_document
        target = dict(link["target"])
        target["document"] = target_document
        remedies.append(
            {
                "document": document_id,
                "id": record["id"],
                "source": source,
                "target": target,
                "applicability": applicability,
            }
        )

    evidence: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        if record["section"] in EVIDENCE_SECTIONS:
            evidence.setdefault(record["document"], {}).setdefault(
                record["section"], {}
            )[record["id"]] = record["data"]

    artifacts: dict[str, dict[str, Any]] = {}
    for record in records:
        if record["section"] == "artifacts":
            artifacts.setdefault(record["document"], {})[record["id"]] = record[
                "data"
            ]

    full = {
        "standard": extracted["leaf"],
        "view": view,
        "available_views": list(STANDARD_VIEWS),
        "facts": extracted["facts"],
        "documents": extracted["documents"],
        "items": item_buckets,
        "remedies": remedies,
        "evidence": evidence,
        "artifacts": artifacts,
    }
    if view == "full":
        return full
    common = {
        key: full[key]
        for key in (
            "standard",
            "view",
            "available_views",
            "facts",
            "documents",
        )
    }
    semantic_by_ref = {
        (record["document"], record["id"]): record for record in semantic
    }
    if view == "requirements":
        common["requirements"] = _compact_requirements(semantic)
        common["context_index"] = _compact_context_index(semantic)
        return common
    selected_lineage = _selected_semantic_lineage(semantic_by_ref, selected_refs)
    if view == "context":
        common["context"] = _select_context(semantic, semantic_by_ref, selected_refs)
        return common
    if view == "evidence":
        selected_evidence, selected_artifacts = _select_evidence(
            records,
            aliases,
            selected_lineage,
        )
        common["evidence"] = selected_evidence
        common["artifacts"] = selected_artifacts
        return common
    selected_remedies = [
        remedy
        for remedy in remedies
        if (remedy["source"]["document"], remedy["source"]["ref"])
        in selected_lineage
    ]
    procedure_refs = {
        (remedy["target"]["document"], remedy["target"]["ref"])
        for remedy in selected_remedies
    }
    common["remedies"] = selected_remedies
    common["procedures"] = [
        item
        for state in ("true", "unknown")
        for item in item_buckets[state]
        if item["kind"] == "procedure"
        and (item["document"], item["id"]) in procedure_refs
    ]
    return common


def _normalize_standard_refs(
    refs: Sequence[Mapping[str, str]] | None,
) -> set[tuple[str, str]]:
    """Validate exact document-reference pairs supplied by a consumer.

    Intent
    ------
    Normalize optional reference objects into unique immutable pairs.

    Rationale
    ---------
    Strict keys and nonempty strings prevent ambiguous cross-document selection.

    Pseudocode
    ----------
    - set normalized = empty reference set
    - for ref in supplied references:
      - if ref is not one exact document and ref object:
        - raise reference validation error
      - set normalized = normalized plus document and ref pair
    - return normalized

    Wraps
    -----
    - none
    """

    normalized: set[tuple[str, str]] = set()
    for index, ref in enumerate(refs or ()):
        if not isinstance(ref, Mapping):
            raise ValueError(f"standard ref {index} must be an object")
        if set(ref) != {"document", "ref"}:
            raise ValueError(
                f"standard ref {index} must contain only document and ref"
            )
        document = ref.get("document")
        record_ref = ref.get("ref")
        if not isinstance(document, str) or not document:
            raise ValueError(f"standard ref {index}.document must be a non-empty string")
        if not isinstance(record_ref, str) or not record_ref:
            raise ValueError(f"standard ref {index}.ref must be a non-empty string")
        normalized.add((document, record_ref))
    return normalized


def _compact_requirements(
    semantic: Sequence[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Return applicable and unresolved normative assertions.

    Intent
    ------
    Compress semantic records into true and unknown requirement buckets.

    Rationale
    ---------
    Consumers need normative decisions and missing facts without unrelated content.

    Pseudocode
    ----------
    - set buckets = empty true and unknown requirement lists
    - for record in semantic records:
      - if record is an applicable or unresolved assertion:
        - set buckets = buckets plus its normative projection
    - return buckets

    Wraps
    -----
    - none
    """

    buckets: dict[str, list[dict[str, Any]]] = {"true": [], "unknown": []}
    for record in semantic:
        state = record["applicability"]
        if record["kind"] != "assertion" or state not in buckets:
            continue
        requirement = {
            key: record[key]
            for key in ("document", "id", "ancestors", "applicability")
        }
        data = record["data"]
        requirement["modality"] = data["modality"]
        requirement["statement"] = data["statement"]
        if state == "unknown":
            requirement["missing_facts"] = record["missing_facts"]
        buckets[state].append(requirement)
    return buckets


def _compact_context_index(
    semantic: Sequence[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Index applicable context families without loading their contents.

    Intent
    ------
    Return family identities that contain usable definitions, guidance, or examples.

    Rationale
    ---------
    A compact index lets callers choose relevant context before requesting its text.

    Pseudocode
    ----------
    - set contextual_ancestors = families containing contextual descendants
    - set buckets = empty true and unknown family lists
    - for record in semantic records:
      - if record is an applicable contextual family:
        - set buckets = buckets plus its compact identity
    - return buckets

    Wraps
    -----
    - none
    """

    contextual_ancestors = {
        (record["document"], ancestor)
        for record in semantic
        if record["kind"] in CONTEXT_KINDS - {"family"}
        for ancestor in record["ancestors"]
    }
    buckets: dict[str, list[dict[str, Any]]] = {"true": [], "unknown": []}
    for record in semantic:
        state = record["applicability"]
        if (
            record["kind"] != "family"
            or state not in buckets
            or (record["document"], record["id"]) not in contextual_ancestors
        ):
            continue
        item = {
            key: record[key]
            for key in ("document", "id", "applicability")
        }
        for key in ("title", "summary"):
            if key in record["data"]:
                item[key] = record["data"][key]
        if state == "unknown":
            item["missing_facts"] = record["missing_facts"]
        buckets[state].append(item)
    return buckets


def _selected_semantic_lineage(
    semantic_by_ref: Mapping[tuple[str, str], dict[str, Any]],
    selected_refs: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Expand selected semantic references through declared ancestors.

    Intent
    ------
    Validate selected references and return their complete within-document lineage.

    Rationale
    ---------
    Evidence and remedies attached to an ancestor remain relevant to its descendants.

    Pseudocode
    ----------
    - set missing = selected references absent from the semantic index
    - if missing is nonempty:
      - raise unknown reference error
    - set lineage = selected references plus each record's ancestors
    - return lineage

    Wraps
    -----
    - none
    """

    missing = sorted(selected_refs - set(semantic_by_ref))
    if missing:
        rendered = ", ".join(f"{document}:{ref}" for document, ref in missing)
        raise ValueError(f"unknown standard refs: {rendered}")
    lineage = set(selected_refs)
    for document, record_ref in selected_refs:
        lineage.update(
            (document, ancestor)
            for ancestor in semantic_by_ref[(document, record_ref)]["ancestors"]
        )
    return lineage


def _select_context(
    semantic: Sequence[dict[str, Any]],
    semantic_by_ref: Mapping[tuple[str, str], dict[str, Any]],
    selected_refs: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Return interpretive records from selected references' nearest families.

    Intent
    ------
    Select applicable families, definitions, guidance, and examples surrounding refs.

    Rationale
    ---------
    Nearest-family scoping supplies enough interpretation without unrelated standards text.

    Pseudocode
    ----------
    - set selection_state = validated semantic lineage
    - set roots = nearest family ancestor for each selected reference
    - set context = applicable contextual records beneath roots
    - return context

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._selected_semantic_lineage:
      why:
        validates: "Rejects unknown selected refs before their family roots are traversed."
    """

    _selected_semantic_lineage(semantic_by_ref, selected_refs)
    roots: set[tuple[str, str]] = set()
    for document, record_ref in selected_refs:
        record = semantic_by_ref[(document, record_ref)]
        root = record_ref
        for ancestor in reversed(record["ancestors"]):
            candidate = semantic_by_ref[(document, ancestor)]
            if candidate["kind"] == "family":
                root = ancestor
                break
        roots.add((document, root))

    context = []
    for record in semantic:
        if record["kind"] not in CONTEXT_KINDS or record["applicability"] == "false":
            continue
        if not any(
            record["document"] == document
            and (record["id"] == root or root in record["ancestors"])
            for document, root in roots
        ):
            continue
        item = {
            key: record[key]
            for key in ("document", "kind", "id", "ancestors", "applicability")
        }
        content = {
            key: value
            for key, value in record["data"].items()
            if key in {"title", "summary", "term", "meaning", "statement"}
        }
        if content:
            item["content"] = content
        if record["applicability"] == "unknown":
            item["missing_facts"] = record["missing_facts"]
        context.append(item)
    return context


def _iter_reference_objects(value: Any) -> Iterator[Mapping[str, Any]]:
    """Yield nested typed reference mappings without interpreting relations.

    Intent
    ------
    Traverse mappings and lists to expose objects containing string kind and ref fields.

    Rationale
    ---------
    Evidence schemas place typed references at multiple nesting depths.

    Pseudocode
    ----------
    - if value is a typed reference mapping:
      - set yielded_values = yielded_values plus value
    - for child in nested mapping or list values:
      - set yielded_values = yielded_values plus nested references from child

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_reference_objects:
      why:
        orchestrates: "Recursively traverses nested mapping and list children."
    """

    if isinstance(value, Mapping):
        if isinstance(value.get("kind"), str) and isinstance(value.get("ref"), str):
            yield value
        for child in value.values():
            yield from _iter_reference_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_reference_objects(child)


def _resolve_pointer(
    owner_document: str,
    pointer: Mapping[str, Any],
    aliases: Mapping[str, Mapping[str, str]],
) -> tuple[str, str, str]:
    """Resolve one typed pointer to a canonical document-kind-reference triple.

    Intent
    ------
    Replace an optional local import alias with its closure document identity.

    Rationale
    ---------
    Connected-evidence traversal requires comparable canonical reference keys.

    Pseudocode
    ----------
    - set document = aliased target document or owner document
    - return document pointer kind and pointer reference

    Wraps
    -----
    - none
    """
    alias = pointer.get("document")
    document = (
        aliases.get(owner_document, {}).get(alias, owner_document)
        if isinstance(alias, str)
        else owner_document
    )
    return document, str(pointer["kind"]), str(pointer["ref"])


def _select_evidence(
    records: Sequence[dict[str, Any]],
    aliases: Mapping[str, Mapping[str, str]],
    selected_lineage: set[tuple[str, str]],
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Return connected evidence and only the artifacts it references.

    Intent
    ------
    Traverse typed evidence links from selected semantic lineage to a fixed point.

    Rationale
    ---------
    Evidence views must include supporting chains without dumping unrelated artifacts.

    Pseudocode
    ----------
    - set evidence_keys = indexed evidence records
    - set pointers = canonical typed references found in each evidence record
    - set selected = records directly connected to semantic lineage
    - while another evidence record connects to selected:
      - set selected = selected plus the connected record
    - set artifacts = artifact records referenced by selected evidence
    - return selected evidence and artifacts

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_reference_objects:
      why:
        orchestrates: "Traverses nested evidence values for typed references."

    InstantiationsFromRepo
    ----------------------
    ._resolve_pointer:
      why:
        constructs: "Builds canonical keys for every discovered typed reference."
    """

    evidence_records = [
        record for record in records if record["section"] in EVIDENCE_SECTIONS
    ]
    evidence_keys = {
        (record["document"], record["kind"], record["id"]): record
        for record in evidence_records
    }
    pointers = {
        key: [
            _resolve_pointer(record["document"], pointer, aliases)
            for pointer in _iter_reference_objects(record["data"])
        ]
        for key, record in evidence_keys.items()
    }
    selected = {
        key
        for key, values in pointers.items()
        if any((document, record_ref) in selected_lineage for document, _, record_ref in values)
    }
    changed = True
    while changed:
        changed = False
        for key, values in pointers.items():
            if key in selected:
                for pointer in values:
                    if pointer in evidence_keys and pointer not in selected:
                        selected.add(pointer)
                        changed = True
            elif any(pointer in selected for pointer in values):
                selected.add(key)
                changed = True

    selected_artifact_refs = {
        (document, record_ref)
        for key in selected
        for document, kind, record_ref in pointers[key]
        if kind == "artifact"
    }
    for key, record in evidence_keys.items():
        if record["section"] != "evidence_claims":
            continue
        if any(
            (document, record_ref) in selected_artifact_refs
            for document, kind, record_ref in pointers[key]
            if kind == "artifact"
        ):
            selected.add(key)

    evidence: dict[str, dict[str, dict[str, Any]]] = {}
    for key in sorted(selected):
        record = evidence_keys[key]
        evidence.setdefault(record["document"], {}).setdefault(
            record["section"], {}
        )[record["id"]] = record["data"]

    artifacts: dict[str, dict[str, Any]] = {}
    artifact_records = {
        (record["document"], record["id"]): record
        for record in records
        if record["section"] == "artifacts"
    }
    for key in selected:
        for document, kind, record_ref in pointers[key]:
            if kind != "artifact":
                continue
            artifact = artifact_records.get((document, record_ref))
            if artifact is not None:
                artifacts.setdefault(document, {})[record_ref] = artifact["data"]
    return evidence, artifacts


def _combine_applicability(left: str, right: str) -> str:
    """Conjoin applicability states when projecting remedy links.

    Intent
    ------
    Return false, unknown, or true for a link's source-target applicability pair.

    Rationale
    ---------
    A remedy is usable only to the degree that both linked semantics apply.

    Pseudocode
    ----------
    - if either state is false:
      - return false
    - if either state is unknown:
      - return unknown
    - return true

    Wraps
    -----
    - none
    """

    if "false" in {left, right}:
        return "false"
    if "unknown" in {left, right}:
        return "unknown"
    return "true"


def query(
    repo_root: Path,
    standard_path: Path,
    *,
    facts: dict[str, Any] | None = None,
    view: str = "requirements",
    refs: Sequence[Mapping[str, str]] | None = None,
    record_query: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one materialized standard closure selected by the caller.

    Intent
    ------
    Add repository and root-document identity to the requested standard projection.

    Rationale
    ---------
    Dispatcher consumers need provenance alongside the view-specific payload.

    Pseudocode
    ----------
    - set materialized = selected standard view
    - set root = closure document matching the selected root path
    - return repository root root document and materialized view

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .materialize_standard:
      why:
        constructs: "Builds the view-specific closure projection extended with provenance."
    """

    repo_root = Path(repo_root).resolve()
    materialized = materialize_standard(
        repo_root,
        standard_path,
        facts=facts,
        view=view,
        refs=refs,
        record_query=record_query,
    )
    root = next(
        document
        for document in materialized["documents"]
        if document["path"] == materialized["standard"]
    )
    return {
        "repository_root": str(repo_root),
        "root_document": root["id"],
        **materialized,
    }


class Interface(PythonMachineInterface):
    """Expose explicit-root standard queries through the dispatcher.

    Intent
    ------
    Define the machine-interface parser and execution adapter for query requests.

    Rationale
    ---------
    A public dispatcher interface hides runtime paths while preserving explicit roots.

    Pseudocode
    ----------
    - set interface = explicit standard-query command contract

    Wraps
    -----
    - none
    """

    prog = "query-standard"
    description = "Query one explicit standard and its complete pinned import closure."

    def build_parser(self) -> argparse.ArgumentParser:
        """Build command-line arguments for explicit-root standard queries.

        Intent
        ------
        Add root path, repository, facts, view, references, and generic-query options.

        Rationale
        ---------
        JSON options preserve structured dispatcher transport without private files.

        Pseudocode
        ----------
        - set parser = base machine-interface parser
        - set parser = parser plus standard-query arguments
        - return parser

        Wraps
        -----
        - none
        """
        parser = super().build_parser()
        parser.add_argument("standard_path")
        parser.add_argument("--repo-root", default=".")
        parser.add_argument("--facts-json", default="{}")
        parser.add_argument("--view", choices=STANDARD_VIEWS, default="requirements")
        parser.add_argument("--refs-json", default="[]")
        parser.add_argument("--query-json")
        return parser

    def run(self, args: argparse.Namespace) -> int:
        """Validate JSON options, execute the query, and print canonical JSON.

        Intent
        ------
        Convert parsed dispatcher arguments into one explicit standard query response.

        Rationale
        ---------
        Strict input shapes fail early and deterministic output supports agent consumers.

        Pseudocode
        ----------
        - set facts = decoded facts object
        - set refs = decoded reference list
        - set record_query = optional decoded generic query object
        - if any decoded value has the wrong shape:
          - raise query argument error
        - set response = explicit-root query result
        - set output_state = canonical JSON printed to standard output
        - return success

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        .query:
          why:
            orchestrates: "Executes the explicit-root query serialized for the dispatcher."
        """
        try:
            facts = json.loads(args.facts_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--facts-json must be valid JSON: {exc}") from exc
        if not isinstance(facts, dict):
            raise ValueError("--facts-json must decode to an object")
        try:
            refs = json.loads(args.refs_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--refs-json must be valid JSON: {exc}") from exc
        if not isinstance(refs, list):
            raise ValueError("--refs-json must decode to a list")
        record_query = None
        if args.query_json is not None:
            try:
                record_query = json.loads(args.query_json)
            except json.JSONDecodeError as exc:
                raise ValueError(f"--query-json must be valid JSON: {exc}") from exc
            if not isinstance(record_query, dict):
                raise ValueError("--query-json must decode to an object")
            if args.view != "requirements":
                raise ValueError("--query-json cannot be combined with --view")
        print(
            json.dumps(
                query(
                    Path(args.repo_root),
                    Path(args.standard_path),
                    facts=facts,
                    view=args.view,
                    refs=refs,
                    record_query=record_query,
                ),
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
