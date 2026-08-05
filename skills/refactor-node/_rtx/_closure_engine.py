"""Resolve registered node ownership and materialize effective standards.

The query is deliberately read-only.  It converts the repository's authored
blueprint and standard graphs into one JSON result so the LLM refactoring
interfaces do not need to reimplement ownership, import closure, applicability,
evidence, or remedy selection in prose.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterator, Mapping, NamedTuple, Sequence

from officina.common.standard_extractor import extract_standard
from officina.runtime.python_machine_interface import PythonMachineInterface
from officina.common.blueprint_graph import BlueprintGraphError, BlueprintNode, resolved_node_content_paths
from officina.common.blueprint_inventory import collect_blueprints


STANDARD_LEAVES = {
    ("module", "python"): "python-module.standard.yaml",
    ("behavioral_source", "python"): "python-behavioral-source.standard.yaml",
    ("module", "markdown"): "instruction-module.standard.yaml",
    ("behavioral_source", "markdown"): "instruction-behavioral-source.standard.yaml",
}
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


class Partition(NamedTuple):
    """One resolved owner whose gateway selects exactly one standard leaf."""

    node_id: str
    node_type: str
    module_root: Path
    blueprint_path: Path
    declared_gateway_language: str
    gateway_language: str | None
    gateway_path: str
    owned_content: list[str]
    excluded_child_roots: list[str]
    declaration_files: list[str]
    selected_scope: str
    resolved_files: list[str]
    leaf: str | None
    target_facts: dict[str, Any]


class OwnershipIndex(NamedTuple):
    """Scoped canonical inventory plus its effective direct-file ownership."""

    nodes: dict[str, BlueprintNode]
    source_modules: dict[str, str]
    module_sources: dict[str, tuple[str, ...]]
    direct_file_owners: dict[Path, str]


def normalize_gateway_language(language: object) -> str | None:
    """Map live gateway declarations, including Python version constraints, to a family."""

    if not isinstance(language, str):
        return None
    normalized = language.strip().lower()
    if normalized == "markdown":
        return "markdown"
    if re.fullmatch(r"python(?:\s*(?:>=|<=|==|~=|>|<)\s*\d+(?:\.\d+)*)?", normalized):
        return "python"
    return None


def _ownership_index(repo_root: Path, target: str) -> OwnershipIndex:
    """Build exact ownership with the canonical inventory and content resolver."""

    inventory = collect_blueprints(repo_root)
    nodes: dict[str, BlueprintNode] = {}
    for document in inventory.documents:
        if document.node_id is None or document.node_type not in {"module", "behavioral_source"}:
            continue
        if document.node_id in nodes:
            raise ValueError(f"duplicate registered node id {document.node_id!r}")
        declaration = dict(document.declaration)
        gateway = declaration.get("gateway")
        gateway_relative = gateway.get("path") if isinstance(gateway, dict) else None
        nodes[document.node_id] = BlueprintNode(
            node_id=document.node_id,
            node_type=document.node_type,
            version=int(declaration.get("version", 0)),
            module_root=document.module_root,
            blueprint_path=document.path,
            gateway_path=(
                document.module_root / gateway_relative
                if isinstance(gateway_relative, str)
                else None
            ),
            declaration=declaration,
        )

    source_modules: dict[str, str] = {}
    module_sources: dict[str, tuple[str, ...]] = {}
    child_roots: dict[str, tuple[Path, ...]] = {}
    for module_id, module in sorted(nodes.items()):
        if module.node_type != "module":
            continue
        registered_sources: list[str] = []
        for source_id, declaration in sorted(module.declaration.get("sources", {}).items()):
            source = nodes.get(source_id)
            if source is None or source.node_type != "behavioral_source":
                raise ValueError(f"{module_id}: missing registered source {source_id}")
            if source_id in source_modules:
                raise ValueError(
                    f"{source_id}: registered by both {source_modules[source_id]} and {module_id}"
                )
            locator = declaration.get("blueprint") if isinstance(declaration, dict) else None
            relative = locator.get("path") if isinstance(locator, dict) else None
            if (
                not isinstance(locator, dict)
                or locator.get("base") != "module-root"
                or not isinstance(relative, str)
                or (module.module_root / relative).resolve() != source.blueprint_path.resolve()
            ):
                raise ValueError(f"{module_id}: invalid source locator for {source_id}")
            if source.module_root != module.module_root:
                raise ValueError(f"{module_id}: source {source_id} belongs to another module")
            registered_sources.append(source_id)
            source_modules[source_id] = module_id
        module_sources[module_id] = tuple(registered_sources)
        roots = []
        for child_namespace, locator in sorted(module.declaration.get("children", {}).items()):
            child_id = (
                child_namespace
                if child_namespace.startswith(f"{module_id}.")
                else f"{module_id}.{child_namespace}"
            )
            child = nodes.get(child_id)
            if child is None or child.node_type != "module":
                raise ValueError(f"{module_id}: missing registered child {child_id}")
            roots.append(child.module_root)
        child_roots[module_id] = tuple(roots)

    if target in nodes:
        target_node = nodes[target]
        relevant_modules = {
            target
            if target_node.node_type == "module"
            else source_modules[target]
        }
    else:
        candidate = Path(target)
        selected = (
            candidate.resolve()
            if candidate.is_absolute()
            else (repo_root / candidate).resolve()
        )
        containing = [
            module_id
            for module_id, node in nodes.items()
            if node.node_type == "module" and selected.is_relative_to(node.module_root)
        ]
        relevant_modules = (
            {max(containing, key=lambda item: len(nodes[item].module_root.parts))}
            if containing
            else set()
        )

    blueprint_paths = {node.blueprint_path.resolve() for node in nodes.values()}
    direct_owners: dict[Path, str] = {}
    for module_id in sorted(relevant_modules):
        source_ids = module_sources[module_id]
        module = nodes[module_id]
        module_content = set(
            resolved_node_content_paths(
                module,
                repo_root,
                excluded_module_roots=child_roots[module_id],
            )
        )
        if module_content & blueprint_paths:
            raise BlueprintGraphError(f"{module.blueprint_path}: content cannot include blueprint files")
        source_owners: dict[Path, str] = {}
        for source_id in source_ids:
            source = nodes[source_id]
            source_content = set(
                resolved_node_content_paths(
                    source,
                    repo_root,
                    excluded_module_roots=child_roots[module_id],
                )
            )
            missing = source_content - module_content
            if missing:
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: source content is outside module {module_id}"
                )
            for path in source_content:
                previous = source_owners.get(path)
                if previous is not None:
                    raise BlueprintGraphError(
                        f"{source.blueprint_path}: sibling sources {previous} and {source_id} overlap"
                    )
                source_owners[path] = source_id
                direct_owners[path] = source_id
        for path in module_content - set(source_owners):
            direct_owners[path] = module_id
    return OwnershipIndex(nodes, source_modules, module_sources, direct_owners)


def _partition(
    repo_root: Path,
    module_root: Path,
    blueprint_path: Path,
    declaration: dict[str, Any],
    *,
    module_declaration: dict[str, Any] | None = None,
    selected_scope: Path | None = None,
    selected_node: str | None = None,
    direct_file_owners: dict[Path, str] | None = None,
) -> Partition:
    """Convert one registered declaration to its supported leaf selection."""

    gateway = declaration.get("gateway")
    declared_language = gateway.get("language") if isinstance(gateway, dict) else None
    language = normalize_gateway_language(declared_language)
    node_type = declaration.get("node_type")
    node_id = declaration.get("id")
    if not isinstance(node_id, str) or not isinstance(node_type, str):
        raise ValueError(f"{blueprint_path}: missing node identity")
    leaf = STANDARD_LEAVES.get((node_type, language or ""))
    module = module_declaration or declaration
    child_roots: list[str] = []
    for child_id, locator in sorted(module.get("children", {}).items()):
        if module.get("schema_version") == 6:
            if locator != {}:
                raise ValueError(f"{child_id}: invalid registered-child locator")
            relative = f"{child_id}/blueprint.yaml"
        else:
            if not isinstance(locator, dict) or locator.get("base") != "module-root":
                raise ValueError(f"{child_id}: invalid registered-child locator")
            relative = locator.get("path")
            if not isinstance(relative, str):
                raise ValueError(f"{child_id}: registered-child path is missing")
        child_marker = (module_root / relative).resolve()
        if not child_marker.is_relative_to(module_root.resolve()):
            raise ValueError(f"{child_id}: registered-child locator escapes its module")
        child_roots.append(child_marker.parent.relative_to(repo_root).as_posix())
    content_patterns = [
        pattern for pattern in declaration.get("content", []) if isinstance(pattern, str)
    ]
    resolved_files = [
        path.relative_to(repo_root).as_posix()
        for path, owner_id in sorted((direct_file_owners or {}).items())
        if owner_id == node_id
    ]
    if selected_node is None and selected_scope is not None:
        scope = selected_scope.resolve()
        resolved_files = [
            relative
            for relative in resolved_files
            if (repo_root / relative).resolve() == scope
            or (
                scope.is_dir()
                and (repo_root / relative).resolve().is_relative_to(scope)
            )
        ]
    target_facts: dict[str, Any] = {"node.id": module.get("id", node_id)}
    discovery = module.get("discovery")
    if isinstance(discovery, dict):
        catalog = discovery.get("catalog")
        if isinstance(catalog, dict):
            for field in ("domain", "topics", "visibility"):
                if field in catalog:
                    target_facts[f"node.catalog.{field}"] = catalog[field]
        if "activated_by" in discovery:
            target_facts["node.activated_by"] = discovery["activated_by"]
        if "persistent_modifier" in discovery:
            target_facts["node.persistent_modifier"] = discovery[
                "persistent_modifier"
            ]
    module_id = module.get("id")
    target_facts["node.is-personal-override"] = (
        isinstance(module_id, str) and module_id.startswith("my-")
    )
    validator_prefixes = ("validators/", "skills/skill-maker/validators/")
    target_facts["node.is-repository-validator"] = any(
        relative.startswith(validator_prefixes) for relative in resolved_files
    )
    target_facts["node.type"] = node_type
    target_facts["gateway.language"] = language
    return Partition(
        node_id=node_id,
        node_type=node_type,
        module_root=module_root,
        blueprint_path=blueprint_path,
        declared_gateway_language=str(declared_language or ""),
        gateway_language=language,
        gateway_path=str(gateway.get("path", "")),
        owned_content=content_patterns,
        excluded_child_roots=child_roots,
        declaration_files=[blueprint_path.relative_to(repo_root).as_posix()],
        selected_scope=(
            f"node:{selected_node}"
            if selected_node is not None
            else (selected_scope or module_root).relative_to(repo_root).as_posix()
        ),
        resolved_files=resolved_files,
        leaf=leaf,
        target_facts=target_facts,
    )


def resolve_partitions(repo_root: Path, target: str) -> list[Partition]:
    """Resolve a registered node or path through the canonical ownership graph."""

    repo_root = Path(repo_root).resolve()
    graph = _ownership_index(repo_root, target)
    direct_owners = dict(graph.direct_file_owners)

    def node_partition(
        node_id: str,
        *,
        selected_scope: Path | None = None,
        selected_node: str | None = None,
    ) -> Partition:
        node = graph.nodes[node_id]
        module_id = (
            node_id
            if node.node_type == "module"
            else graph.source_modules[node_id]
        )
        module = graph.nodes[module_id]
        return _partition(
            repo_root,
            module.module_root,
            node.blueprint_path,
            dict(node.declaration),
            module_declaration=(
                None if node.node_type == "module" else dict(module.declaration)
            ),
            selected_scope=selected_scope,
            selected_node=selected_node,
            direct_file_owners=direct_owners,
        )

    def whole_module(module_id: str) -> list[Partition]:
        return [
            node_partition(module_id, selected_node=module_id),
            *(
                node_partition(source_id, selected_node=module_id)
                for source_id in graph.module_sources.get(module_id, ())
            ),
        ]

    if target in graph.nodes:
        node = graph.nodes[target]
        if node.node_type == "module":
            return whole_module(target)
        return [node_partition(target, selected_node=target)]

    candidate = Path(target)
    selected = (
        candidate.resolve()
        if candidate.is_absolute()
        else (repo_root / candidate).resolve()
    )
    if not selected.exists() or not selected.is_relative_to(repo_root):
        raise ValueError(
            f"target is neither a registered node nor a repository path: {target}"
        )
    module_roots = {
        node.module_root: node_id
        for node_id, node in graph.nodes.items()
        if node.node_type == "module"
    }
    if selected in module_roots:
        return whole_module(module_roots[selected])
    if selected.is_file():
        declaration_owner = next(
            (
                node_id
                for node_id, node in graph.nodes.items()
                if node.blueprint_path.resolve() == selected
            ),
            None,
        )
        if declaration_owner is not None:
            return [node_partition(declaration_owner, selected_scope=selected)]
        owner = direct_owners.get(selected)
        if owner is None:
            raise ValueError(f"repository path has no direct registered owner: {target}")
        return [node_partition(owner, selected_scope=selected)]

    selected_owner_ids = sorted(
        {
            owner
            for path, owner in direct_owners.items()
            if path.is_relative_to(selected)
        }
    )
    if not selected_owner_ids:
        raise ValueError(f"selected directory has no directly owned files: {target}")
    return [
        node_partition(owner, selected_scope=selected)
        for owner in selected_owner_ids
    ]


def materialize_standard(
    repo_root: Path,
    leaf: str,
    *,
    facts: dict[str, Any] | None = None,
    view: str = "requirements",
    refs: Sequence[Mapping[str, str]] | None = None,
    record_query: Mapping[str, Any] | None = None,
    scope_refs_by_document: bool = False,
) -> dict[str, Any]:
    """Project generic extracted records into the refactoring query contract."""

    if view not in STANDARD_VIEWS:
        raise ValueError(
            f"unsupported standards view {view!r}; choose one of: "
            + ", ".join(STANDARD_VIEWS)
        )

    if record_query is not None:
        if refs:
            raise ValueError("record queries cannot be combined with standard refs")
        extracted = extract_standard(
            Path(repo_root),
            Path("references") / "node-standards" / leaf,
            facts=facts,
            query=record_query,
        )
        return {
            "leaf": leaf,
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
        Path(repo_root),
        Path("references") / "node-standards" / leaf,
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
    if scope_refs_by_document:
        closure_documents = {document["id"] for document in extracted["documents"]}
        selected_refs = {
            ref for ref in selected_refs if ref[0] in closure_documents
        }
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
        "leaf": leaf,
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
            "leaf",
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
    """Validate exact document/ref pairs supplied by a standards consumer."""

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
    """Return only applicable or unresolved normative assertions."""

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
    """Index applicable context families without loading their contents."""

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
    """Expand selected semantic refs through their declared ancestors."""

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
    """Return interpretive records from only the selected records' nearest families."""

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
    """Yield nested typed reference mappings without interpreting their relation."""

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
    """Return evidence connected to selected semantic refs and only its artifacts."""

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


def _intern_catalog_record(
    catalog: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
    bucket: str,
    key: str,
    value: dict[str, Any],
) -> int:
    """Store immutable record content once and fail on conflicting projections."""

    records = catalog.setdefault(bucket, [])
    bucket_indexes = indexes.setdefault(bucket, {})
    existing = bucket_indexes.get(key)
    if existing is not None and records[existing] != value:
        raise ValueError(f"conflicting catalog projections for {key}")
    if existing is not None:
        return existing
    index = len(records)
    records.append(value)
    bucket_indexes[key] = index
    return index


def _overlay_ref(item: Mapping[str, Any], index: int) -> int | dict[str, Any]:
    """Represent true applicability by a ref and unknown applicability by a small overlay."""

    if item.get("applicability") != "unknown":
        return index
    return {"index": index, "missing_facts": item.get("missing_facts", [])}


def _intern_materialization(
    materialized: Mapping[str, Any],
    *,
    catalog: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
    documents: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Preserve a layered result as shared content plus a fact-specific overlay."""

    for document in materialized["documents"]:
        document_id = document["id"]
        value = {key: document[key] for key in ("path", "digest")}
        previous = documents.get(document_id)
        if previous is not None and previous != value:
            raise ValueError(f"conflicting document metadata for {document_id}")
        documents[document_id] = value

    overlay: dict[str, Any] = {
        "leaf": materialized["leaf"],
        "facts": materialized["facts"],
        "documents": [document["id"] for document in materialized["documents"]],
    }
    view = materialized["view"]
    if view == "requirements":
        requirement_overlay: dict[str, list[Any]] = {"true": [], "unknown": []}
        for state, items in materialized["requirements"].items():
            for item in items:
                key = f'{item["document"]}::{item["id"]}'
                index = _intern_catalog_record(
                    catalog,
                    indexes,
                    "requirements",
                    key,
                    {
                        "document": item["document"],
                        "ref": item["id"],
                        "modality": item["modality"],
                        "statement": item["statement"],
                    },
                )
                requirement_overlay[state].append(_overlay_ref(item, index))
        context_overlay: dict[str, list[Any]] = {"true": [], "unknown": []}
        for state, items in materialized["context_index"].items():
            for item in items:
                key = f'{item["document"]}::{item["id"]}'
                value = {
                    field: item[field]
                    for field in ("title", "summary")
                    if field in item
                }
                value = {
                    "document": item["document"],
                    "ref": item["id"],
                    **value,
                }
                index = _intern_catalog_record(
                    catalog, indexes, "context_index", key, value
                )
                context_overlay[state].append(_overlay_ref(item, index))
        overlay["requirements"] = requirement_overlay
        overlay["context_index"] = context_overlay
        return overlay

    if view == "context":
        context_overlay = {"true": [], "unknown": []}
        for item in materialized["context"]:
            key = f'{item["document"]}::{item["id"]}'
            value = {"kind": item["kind"]}
            if "content" in item:
                value["content"] = item["content"]
            value = {
                "document": item["document"],
                "ref": item["id"],
                **value,
            }
            index = _intern_catalog_record(
                catalog, indexes, "context", key, value
            )
            state = "unknown" if item["applicability"] == "unknown" else "true"
            context_overlay[state].append(_overlay_ref(item, index))
        overlay["context"] = context_overlay
        return overlay

    if view == "evidence":
        evidence_refs = []
        for document, sections in materialized["evidence"].items():
            for section, records in sections.items():
                for record_id, content in records.items():
                    key = f"{document}::{section}::{record_id}"
                    index = _intern_catalog_record(
                        catalog,
                        indexes,
                        "evidence",
                        key,
                        {
                            "document": document,
                            "ref": record_id,
                            "section": section,
                            "content": content,
                        },
                    )
                    evidence_refs.append(index)
        artifact_refs = []
        for document, records in materialized["artifacts"].items():
            for record_id, content in records.items():
                key = f"{document}::{record_id}"
                index = _intern_catalog_record(
                    catalog,
                    indexes,
                    "artifacts",
                    key,
                    {"document": document, "ref": record_id, "content": content},
                )
                artifact_refs.append(index)
        overlay["evidence"] = evidence_refs
        overlay["artifacts"] = artifact_refs
        return overlay

    if view == "remedies":
        remedy_overlay = {"true": [], "unknown": []}
        for item in materialized["remedies"]:
            key = f'{item["document"]}::{item["id"]}'
            index = _intern_catalog_record(
                catalog,
                indexes,
                "remedies",
                key,
                {
                    "document": item["document"],
                    "ref": item["id"],
                    "source": item["source"],
                    "target": item["target"],
                },
            )
            state = "unknown" if item["applicability"] == "unknown" else "true"
            remedy_overlay[state].append(_overlay_ref(item, index))
        procedure_overlay = {"true": [], "unknown": []}
        for item in materialized["procedures"]:
            key = f'{item["document"]}::{item["id"]}'
            index = _intern_catalog_record(
                catalog,
                indexes,
                "procedures",
                key,
                {
                    "document": item["document"],
                    "ref": item["id"],
                    "content": item["content"],
                },
            )
            state = "unknown" if item["applicability"] == "unknown" else "true"
            procedure_overlay[state].append(_overlay_ref(item, index))
        overlay["remedies"] = remedy_overlay
        overlay["procedures"] = procedure_overlay
        return overlay

    raise ValueError(f"cannot intern standards view {view!r}")


def _combine_applicability(left: str, right: str) -> str:
    """Conjoin applicability states when projecting remedy links."""

    if "false" in {left, right}:
        return "false"
    if "unknown" in {left, right}:
        return "unknown"
    return "true"


def query(
    repo_root: Path,
    target: str,
    *,
    facts: dict[str, Any] | None = None,
    view: str = "requirements",
    refs: Sequence[Mapping[str, str]] | None = None,
    record_query: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return owner partitions and their materialized authoritative standards."""

    repo_root = Path(repo_root).resolve()
    requested_refs = _normalize_standard_refs(refs)
    partitions = []
    standards: dict[str, dict[str, Any]] = {}
    catalog: dict[str, list[dict[str, Any]]] = {}
    catalog_indexes: dict[str, dict[str, int]] = {}
    documents: dict[str, dict[str, Any]] = {}
    intern_results = record_query is None and view != "full"
    for partition in resolve_partitions(repo_root, target):
        partition_facts = dict(partition.target_facts)
        for name, value in (facts or {}).items():
            if name in partition_facts and partition_facts[name] != value:
                raise ValueError(
                    f"target fact {name}={value!r} contradicts blueprint fact "
                    f"{partition_facts[name]!r}"
                )
            partition_facts[name] = value
        result_partition = {
                "owner": {
                    "node_id": partition.node_id,
                    "node_type": partition.node_type,
                    "module_root": partition.module_root.relative_to(repo_root).as_posix(),
                    "blueprint": partition.blueprint_path.relative_to(repo_root).as_posix(),
                    "gateway_language": partition.gateway_language,
                    "declared_gateway_language": partition.declared_gateway_language,
                    "gateway_path": partition.gateway_path,
                    "owned_content": partition.owned_content,
                    "excluded_child_roots": partition.excluded_child_roots,
                    "declaration_files": partition.declaration_files,
                    "selected_scope": partition.selected_scope,
                    "resolved_files": partition.resolved_files,
                },
            }
        if partition.leaf is not None:
            fact_fingerprint = hashlib.sha256(
                json.dumps(partition_facts, sort_keys=True).encode("utf-8")
            ).hexdigest()[:12]
            standard_ref = f"{partition.leaf}#{fact_fingerprint}"
            if standard_ref not in standards:
                materialized = materialize_standard(
                    repo_root,
                    partition.leaf,
                    facts=partition_facts,
                    view=view,
                    refs=refs,
                    record_query=record_query,
                    scope_refs_by_document=True,
                )
                standards[standard_ref] = (
                    _intern_materialization(
                        materialized,
                        catalog=catalog,
                        indexes=catalog_indexes,
                        documents=documents,
                    )
                    if intern_results
                    else materialized
                )
            result_partition["standard_ref"] = standard_ref
        else:
            result_partition["unsupported"] = {
                "reason": "unsupported gateway language",
                "gateway_language": partition.declared_gateway_language,
            }
        partitions.append(result_partition)
    closure_documents = (
        set(documents)
        if intern_results
        else {
            document["id"]
            for standard in standards.values()
            for document in standard["documents"]
        }
    )
    outside = sorted(
        (document, record_ref)
        for document, record_ref in requested_refs
        if document not in closure_documents
    )
    if outside:
        rendered = ", ".join(f"{document}:{record_ref}" for document, record_ref in outside)
        raise ValueError(
            "standard refs are outside every selected standard closure: " + rendered
        )
    result = {
        "repository_root": str(repo_root),
        "target": target,
        "view": "query" if record_query is not None else view,
        "partitions": partitions,
        "standards": standards,
    }
    if intern_results:
        result["available_views"] = list(STANDARD_VIEWS)
        result["documents"] = documents
        result["catalog"] = catalog
    return result


class Interface(PythonMachineInterface):
    """Expose the deterministic standards query through the repository dispatcher."""

    prog = "query-standards"
    description = "Resolve a node or owned path and emit its effective standards as JSON."

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("target")
        parser.add_argument("--repo-root", default=".")
        parser.add_argument("--facts-json", default="{}")
        parser.add_argument("--view", choices=STANDARD_VIEWS, default="requirements")
        parser.add_argument("--refs-json", default="[]")
        parser.add_argument("--query-json")
        return parser

    def run(self, args: argparse.Namespace) -> int:
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
                    args.target,
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
