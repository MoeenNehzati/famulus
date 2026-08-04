from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
NODE_STANDARDS = ROOT / "references" / "node-standards"
DISPOSITION = NODE_STANDARDS / "authority-disposition.yaml"


def _load(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _migration_audit() -> dict:
    return _load(DISPOSITION)["migration_audit"]["skill-guidelines"]


def _atomized_assertions() -> dict[str, set[str]]:
    return {
        source: set(replacements)
        for source, replacements in _migration_audit()["atomized_assertions"].items()
    }


def test_authority_disposition_declares_runtime_and_source_roles() -> None:
    disposition = _load(DISPOSITION)

    assert disposition["runtime_authority"] == (
        "pinned import closures rooted at references/node-standards/*.standard.yaml"
    )
    assert disposition["source_authorities"]["skill-guidelines"]["status"] == "replaced"
    assert disposition["source_authorities"]["skill-refactoring"]["status"] == "migrated"
    assert disposition["source_authorities"]["interface-design"]["status"] == "excluded"
    assert disposition["retirements"]["python-ood-refactoring-reference"]["status"] == "migrated"
    assert disposition["supersessions"]["credential-storage"]["replacement"] == (
        "secret-store-for-secrets-config-dir-for-sensitive-nonsecrets"
    )

    interface_design = disposition["source_authorities"]["interface-design"]
    path = ROOT / interface_design["path"]
    assert interface_design["digest"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    refactoring = disposition["source_authorities"]["skill-refactoring"]
    replacement = ROOT / refactoring["replacement"]
    assert refactoring["replacement_digest"] == (
        "sha256:" + hashlib.sha256(replacement.read_bytes()).hexdigest()
    )
    research = disposition["research_basis"]["python-ood"]
    assert len(research["primary_sources"]) >= 5
    assert len(research["skill_baselines"]) >= 2


def test_every_python_ood_rule_has_a_declared_remedy() -> None:
    document = _load(NODE_STANDARDS / "python-ood.standard.yaml")
    remedy_sources = {
        link["source"]["ref"]
        for link in document["links"].values()
        if link["relation"] == "remedied-by"
    }

    uncovered: list[str] = []

    def walk(items: list[dict], ancestors: tuple[str, ...] = ()) -> None:
        for item in items:
            lineage = (*ancestors, item["id"])
            if item["kind"] == "rule" and remedy_sources.isdisjoint(lineage):
                uncovered.append(item["id"])
            walk(item.get("children", []), lineage)

    walk(document["standards"])
    assert uncovered == []
    assert document["links"]["patch-remedy"]["target"]["ref"] == (
        "python-ood.remedies.patch-at-lookup-site"
    )
    assert document["links"]["behavior-remedy"]["target"]["ref"] == (
        "python-ood.remedies.restore-observable-contract"
    )


def test_semantic_review_catalog_is_complete_and_query_owned() -> None:
    document = _load(NODE_STANDARDS / "node.standard.yaml")
    expected = {
        "review-identity",
        "review-blueprint",
        "review-interfaces",
        "review-runtime",
        "review-state-security",
        "review-portability",
        "review-instructions",
        "review-workflow",
        "review-validation",
    }
    reviews = document["semantic_reviews"]
    assert {
        review["instructions"]["instruction_id"] for review in reviews.values()
    } == expected
    assert all(
        review["instructions"]["artifact"]["ref"]
        == "artifact.semantic-review-instructions"
        for review in reviews.values()
    )
    artifact = document["artifacts"]["artifact.semantic-review-instructions"]
    assert artifact["path"] == "references/node-standards/semantic-review.md"
    instructions = (ROOT / artifact["path"]).read_text(encoding="utf-8")
    assert all(f"## {instruction_id}" in instructions for instruction_id in expected)


def test_every_node_standard_assertion_has_a_rule_or_ancestor_remedy() -> None:
    uncovered: list[str] = []

    for path in sorted(NODE_STANDARDS.glob("*.standard.yaml")):
        document = _load(path)
        remedy_sources = {
            link["source"]["ref"]
            for link in document.get("links", {}).values()
            if link["relation"] == "remedied-by"
        }

        def walk(items: list[dict], ancestors: tuple[str, ...] = ()) -> None:
            for item in items:
                lineage = (*ancestors, item["id"])
                if item["kind"] == "rule":
                    for assertion in item["assertions"]:
                        assertion_lineage = (*lineage, f'{item["id"]}#{assertion["id"]}')
                        if remedy_sources.isdisjoint(assertion_lineage):
                            uncovered.append(
                                f'{document["id"]}:{item["id"]}#{assertion["id"]}'
                            )
                walk(item.get("children", []), lineage)

        walk(document["standards"])

    assert uncovered == []


def _semantic_ids(items: list[dict]) -> list[str]:
    result: list[str] = []
    for item in items:
        result.append(item["id"])
        if item["kind"] == "rule":
            result.extend(f'{item["id"]}#{assertion["id"]}' for assertion in item["assertions"])
        if item["kind"] == "procedure":
            result.extend(f'{item["id"]}#{step["id"]}' for step in item["steps"])
        result.extend(_semantic_ids(item.get("children", [])))
    return result


def _closure(path: Path) -> dict[str, dict]:
    documents: dict[str, dict] = {}

    def visit(current: Path) -> None:
        document = _load(current)
        if document["id"] in documents:
            return
        documents[document["id"]] = document
        for declaration in document.get("imports", {}).values():
            artifact = document["artifacts"][declaration["artifact"]["ref"]]
            visit(ROOT / artifact["path"])

    visit(path)
    return documents


def _closure_ids(name: str) -> set[str]:
    documents = _closure(NODE_STANDARDS / f"{name}.standard.yaml")
    return {
        semantic_id
        for document in documents.values()
        for semantic_id in _semantic_ids(document["standards"])
    }


def _semantic_nodes(items: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in items:
        result[item["id"]] = item
        if item["kind"] == "rule":
            result.update(
                {f'{item["id"]}#{value["id"]}': value for value in item["assertions"]}
            )
        if item["kind"] == "procedure":
            result.update(
                {f'{item["id"]}#{value["id"]}': value for value in item["steps"]}
            )
        result.update(_semantic_nodes(item.get("children", [])))
    return result


def test_migration_manifest_proves_complete_unique_legacy_semantics() -> None:
    audit = _migration_audit()
    atomized = _atomized_assertions()
    economy_retirements = set(audit["economy_retirements"])
    ownership = Counter(
        semantic_id
        for path in NODE_STANDARDS.glob("*.standard.yaml")
        for semantic_id in _semantic_ids(_load(path)["standards"])
    )
    reconstructed = {
        semantic_id
        for semantic_id in ownership
        if semantic_id.startswith("skill-guidelines.")
    }
    for original, replacements in atomized.items():
        reconstructed.difference_update(replacements)
        reconstructed.add(original)
    reconstructed.update(economy_retirements)

    payload = "\n".join(sorted(reconstructed)) + "\n"
    assert len(reconstructed) == audit["source_semantic_items"]
    assert "sha256:" + hashlib.sha256(payload.encode()).hexdigest() == (
        audit["source_semantic_id_digest"]
    )
    assert all(ownership[item] == 1 for item in reconstructed if item in ownership)
    assert audit["duplicate_items"] == 0
    assert audit["unresolved_items"] == 0
    assert all(ownership[item] == 0 for item in economy_retirements)

    rewritten = set(audit["rewritten_same_id_leaves"])
    replacements = {item for values in atomized.values() for item in values}
    exact_leaves: dict[str, dict] = {}
    for path in NODE_STANDARDS.glob("*.standard.yaml"):
        for semantic_id, value in _semantic_nodes(_load(path)["standards"]).items():
            is_leaf = "#" in semantic_id or value["kind"] not in {
                "family", "rule", "procedure"
            }
            if (
                is_leaf
                and semantic_id.startswith("skill-guidelines.")
                and semantic_id not in rewritten
                and semantic_id not in replacements
            ):
                exact_leaves[semantic_id] = value
    digest = "sha256:" + hashlib.sha256(
        json.dumps(exact_leaves, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert len(exact_leaves) == audit["exact_unchanged_leaf_items"]
    assert digest == audit["exact_unchanged_leaf_digest"]


def test_context_guidance_contains_no_formatting_only_headings() -> None:
    headings: list[str] = []
    for path in NODE_STANDARDS.glob("*.standard.yaml"):
        for semantic_id, item in _semantic_nodes(_load(path)["standards"]).items():
            if item.get("kind") != "guidance":
                continue
            statement = item["statement"].strip()
            if statement.startswith("#") and statement.lstrip("#").startswith(" "):
                headings.append(f"{path.name}:{semantic_id}")

    assert headings == []


def test_standard_prose_contains_no_legacy_presentation_scaffolding() -> None:
    residue: list[str] = []
    for path in NODE_STANDARDS.glob("*.standard.yaml"):
        for semantic_id, item in _semantic_nodes(_load(path)["standards"]).items():
            title = item.get("title", "").strip()
            statement = item.get("statement", "").strip()
            if title and re.match(r"^\d+\.\s", title):
                residue.append(f"{path.name}:{semantic_id}:numbered-title")
            if title and statement:
                plain_title = re.sub(r"^\d+\.\s*", "", title).rstrip(". —-").lower()
                plain_statement = re.sub(r"[`*_#]", "", statement)
                plain_statement = re.sub(r"^\d+\.\s*", "", plain_statement)
                plain_statement = plain_statement.rstrip(". —-").lower()
                if plain_statement == plain_title:
                    residue.append(f"{path.name}:{semantic_id}:repeated-title")

    assert residue == []


def test_node_standards_exclude_workflow_only_and_analogy_only_material() -> None:
    semantic_ids = {
        semantic_id
        for path in NODE_STANDARDS.glob("*.standard.yaml")
        for semantic_id in _semantic_ids(_load(path)["standards"])
    }

    assert "skill-guidelines.change-publication-workflow" not in semantic_ids
    assert "skill-guidelines.skill-taxonomy.guidance-001" not in semantic_ids
    assert not any(semantic_id.endswith(".analog") for semantic_id in semantic_ids)


def test_normative_and_procedural_content_uses_operational_semantic_kinds() -> None:
    behavioral = _semantic_nodes(
        _load(NODE_STANDARDS / "behavioral-source.standard.yaml")["standards"]
    )
    instruction = _semantic_nodes(
        _load(NODE_STANDARDS / "instruction-node.standard.yaml")["standards"]
    )
    python_module = _load(NODE_STANDARDS / "python-module.standard.yaml")
    python_nodes = _semantic_nodes(python_module["standards"])

    source_ownership = behavioral[
        "node-standards.source-interface-ownership.requirement"
    ]
    assert source_ownership["kind"] == "rule"
    assert {item["id"] for item in source_ownership["assertions"]} == {
        "description-owned",
        "usage-complete",
        "pattern-notes-specific",
    }
    assert instruction["skill-guidelines.output-focused-writing.requirement"][
        "kind"
    ] == "rule"
    assert python_nodes["node-standards.add-validator.requirement"]["kind"] == (
        "rule"
    )
    assert python_nodes["node-standards.procedures.add-validator"]["kind"] == (
        "procedure"
    )
    assert any(
        link["source"]["ref"] == "skill-guidelines.adding-validator"
        and link["target"]["ref"] == "node-standards.procedures.add-validator"
        for link in python_module["links"].values()
    )


def test_atomized_assertions_have_exact_unique_replacements() -> None:
    ownership = Counter(
        semantic_id
        for path in NODE_STANDARDS.glob("*.standard.yaml")
        for semantic_id in _semantic_ids(_load(path)["standards"])
    )

    for original, replacements in _atomized_assertions().items():
        assert ownership[original] == 0
        assert all(ownership[replacement] == 1 for replacement in replacements)


def test_mixed_family_rules_land_in_their_actual_layers() -> None:
    behavioral = _closure_ids("behavioral-source")
    python_behavioral = _closure_ids("python-behavioral-source")
    instruction_behavioral = _closure_ids("instruction-behavioral-source")
    node = _closure_ids("node")
    python_node = _closure_ids("python-node")

    assert "skill-guidelines.module-behavioral-source-v5.node-kinds" in behavioral
    assert "skill-guidelines.module-behavioral-source-v5.ownership" in behavioral
    assert "skill-guidelines.blueprint-authoring.requirement-002" in behavioral
    assert "skill-guidelines.canonical-interface-names.requirement-002" in python_behavioral
    assert "skill-guidelines.canonical-interface-names.requirement-003" in python_behavioral
    assert "skill-guidelines.canonical-interface-names.requirement-002" not in instruction_behavioral
    assert "skill-guidelines.canonical-interface-names.requirement-003" not in instruction_behavioral
    assert "skill-guidelines.cross-platform-tools.requirement-002" not in node
    assert "skill-guidelines.cross-platform-tools.requirement-003" not in node
    assert "skill-guidelines.cross-platform-tools.requirement-004" not in node
    assert "skill-guidelines.cross-platform-tools.requirement-002" in python_node
    assert "skill-guidelines.cross-platform-tools.requirement-003" in python_node
    assert "skill-guidelines.cross-platform-tools.requirement-004" in python_node


def test_remaining_mixed_families_are_split_by_structure_and_gateway() -> None:
    node = _closure_ids("node")
    module = _closure_ids("module")
    behavioral = _closure_ids("behavioral-source")
    python_node = _closure_ids("python-node")
    instruction_node = _closure_ids("instruction-node")
    instruction_behavioral = _closure_ids("instruction-behavioral-source")

    assert "skill-guidelines.system-evolution.requirement-002" in python_node
    assert "skill-guidelines.system-evolution.requirement-003" in python_node
    assert "skill-guidelines.system-evolution.requirement-002" not in node
    assert "skill-guidelines.system-evolution.requirement-004" in instruction_behavioral
    assert "skill-guidelines.system-evolution.requirement-004" not in node

    assert "skill-guidelines.canonical-blueprint-ownership.requirement-001" in node
    assert "skill-guidelines.canonical-blueprint-ownership.requirement-002" in node
    assert "node-standards.source-interface-ownership.requirement" in behavioral
    assert "skill-guidelines.canonical-blueprint-ownership.requirement-003" in instruction_behavioral
    assert "skill-guidelines.canonical-blueprint-ownership.requirement-004" in instruction_behavioral
    assert "skill-guidelines.canonical-blueprint-ownership.requirement-005" in instruction_behavioral
    assert "skill-guidelines.canonical-blueprint-ownership.requirement-003" not in module

    assert "skill-guidelines.assistant-neutrality.requirement-001" in node
    assert "skill-guidelines.assistant-neutrality.requirement-003" in node
    assert "skill-guidelines.assistant-neutrality.requirement-004" in node
    assert "skill-guidelines.assistant-neutrality.requirement-002" in python_node
    assert "skill-guidelines.assistant-neutrality.requirement-005" in python_node
    assert "skill-guidelines.assistant-neutrality.requirement-006" in python_node
    assert "skill-guidelines.assistant-neutrality.requirement-002" not in instruction_node
    assert "node-standards.instruction-source-neutrality.requirement" in instruction_behavioral

    assert "node-standards.module-blueprint-authoring.requirement" in module
    assert "node-standards.behavioral-source-blueprint-authoring.requirement" in behavioral


def test_private_runtime_rules_are_split_between_python_and_instruction_sources() -> None:
    behavioral = _closure_ids("behavioral-source")
    python_behavioral = _closure_ids("python-behavioral-source")
    instruction_behavioral = _closure_ids("instruction-behavioral-source")

    for suffix in ("requirement-001", "requirement-002", "requirement-004"):
        assert f"skill-guidelines.private-runtime-files.{suffix}" in python_behavioral
    assert "skill-guidelines.private-runtime-files.requirement-003" not in python_behavioral
    assert "skill-guidelines.private-runtime-files.requirement-005" not in python_behavioral
    assert "skill-guidelines.private-runtime-files.requirement-005" in instruction_behavioral
    assert "node-standards.public-runtime-doc-boundary.requirement" in instruction_behavioral
    assert "skill-guidelines.private-runtime-files.requirement-006" in behavioral


def test_runtime_validator_coverage_is_layer_local_and_graph_aware() -> None:
    python_document = _load(
        NODE_STANDARDS / "python-behavioral-source.standard.yaml"
    )
    instruction_document = _load(
        NODE_STANDARDS / "instruction-behavioral-source.standard.yaml"
    )

    python_check = python_document["checks"]["python-source.private-runtime-layout"]
    instruction_check = instruction_document["checks"][
        "instruction-source.private-runtime-references"
    ]
    assert python_check["selector"]["symbol"] == "validate_with_graph"
    assert instruction_check["selector"]["symbol"] == "validate_with_graph"
    assert any(
        target["target"]["ref"] == "python-source.private-runtime-layout"
        and target["relation"] == "tests-check"
        and target["polarity"] == "mixed"
        for test in python_document["tests"].values()
        for target in test["targets"]
    )
    assert any(
        target["target"]["ref"] == "instruction-source.private-runtime-references"
        and target["relation"] == "tests-check"
        and target["polarity"] == "mixed"
        for test in instruction_document["tests"].values()
        for target in test["targets"]
    )
    runtime_location = next(
        assurance
        for assurance in python_document["assurances"].values()
        if assurance["assertion"]["ref"].endswith("#runtime-implementation-location")
    )
    assert runtime_location["strength"] == "partial"
    assert "scripts" in runtime_location["limitation"]


def test_every_leaf_declares_skill_owned_domain() -> None:
    for path in NODE_STANDARDS.glob("*.standard.yaml"):
        document = _load(path)
        assert document["domain_facts"]["node.skill-owned"] is True

    root = _load(NODE_STANDARDS / "node.standard.yaml")
    assert "skill-system" in root["purpose"]


def test_conditional_policies_use_target_facts_in_the_narrowest_layer() -> None:
    predicates = {}

    def visit(items: list[dict]) -> None:
        for item in items:
            if "applies_when" in item:
                predicates[item["id"]] = item["applies_when"]
            if item["kind"] == "rule":
                for assertion in item["assertions"]:
                    if "applies_when" in assertion:
                        predicates[f'{item["id"]}#{assertion["id"]}'] = assertion["applies_when"]
            visit(item.get("children", []))

    for path in NODE_STANDARDS.glob("*.standard.yaml"):
        visit(_load(path)["standards"])

    assert predicates["skill-guidelines.validator-test-conventions"] == {
        "fact": "node.is-repository-validator",
        "equals": True,
    }
    assert predicates["skill-guidelines.adding-validator"] == {
        "fact": "node.is-repository-validator",
        "equals": True,
    }
    assert predicates["skill-guidelines.personal-override-structure"] == {
        "fact": "node.is-personal-override",
        "equals": True,
    }


def test_runtime_code_boundary_has_atomic_detection_and_remedy_chain() -> None:
    document = _load(
        NODE_STANDARDS / "instruction-behavioral-source.standard.yaml"
    )
    ids = set(_semantic_ids(document["standards"]))
    rule_id = "skill-guidelines.runtime-code-boundary.requirement-001"
    assertion_ids = {
        semantic_id
        for semantic_id in ids
        if semantic_id.startswith(rule_id + "#")
    }

    assert assertion_ids == {
        rule_id + "#no-inline-executable-logic",
        rule_id + "#no-executable-references",
        rule_id + "#no-opaque-runtime-paths",
        rule_id + "#execution-behind-interface",
    }
    executable_assertion = rule_id + "#no-executable-references"
    rule = next(
        item
        for family in document["standards"]
        for item in family.get("children", [])
        if item.get("id") == rule_id
    )
    assertion = next(
        item for item in rule["assertions"] if item["id"] == "no-executable-references"
    )
    assert {example["kind"] for example in assertion["examples"]} >= {"invalid"}

    check_id = "instruction-source.skill-body-execution"
    test_id = "instruction-source.skill-body-execution-tests"
    extraction_id = "node-standards.remedies.extract-executable-logic"
    cleanup_id = "node-standards.remedies.remove-leaked-implementation-reference"
    assert check_id in document["checks"]
    assert test_id in document["tests"]
    assert any(
        assurance["assertion"]["ref"] == executable_assertion
        and assurance["mechanism"]["ref"] == check_id
        for assurance in document["assurances"].values()
    )
    assurance = next(
        assurance
        for assurance in document["assurances"].values()
        if assurance["assertion"]["ref"] == executable_assertion
    )
    assert assurance["strength"] == "partial"
    assert "suffix" in assurance["limitation"].lower()
    assert extraction_id in ids
    assert cleanup_id in ids
    assert any(
        link["source"]["ref"] == executable_assertion
        and link["relation"] == "remedied-by"
        and link["target"]["ref"] == cleanup_id
        for link in document["links"].values()
    )
    assert any(
        link["source"]["ref"] == rule_id + "#execution-behind-interface"
        and link["target"]["ref"] == extraction_id
        for link in document["links"].values()
    )
