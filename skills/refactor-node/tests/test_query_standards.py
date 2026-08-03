from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME = REPO_ROOT / "skills" / "refactor-node" / "_rtx" / "_closure_engine.py"


def _load_runtime():
    src = str(REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    spec = importlib.util.spec_from_file_location("refactor_node_query", RUNTIME)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _module(
    module_id: str,
    *,
    language: str,
    sources: dict | None = None,
    children: dict | None = None,
    gateway_path: str = "SKILL.md",
    content: list[str] | None = None,
) -> dict:
    document = {
        "schema_version": 5,
        "node_type": "module",
        "id": module_id,
        "version": 1,
        "gateway": {"language": language, "path": gateway_path},
        "content": content or [gateway_path.replace(".", r"\.")],
        "children": children or {},
        "namespace_exports": {},
        "exports": {},
        "sources": sources or {},
        "authority": {"owns_filesystem": [], "suggested_permissions": {"bash": [], "network": []}},
    }
    if module_id == "demo":
        document["discovery"] = {"mechanism": "skill"}
    return document


def _source(
    source_id: str,
    *,
    language: str,
    path: str,
    content: list[str] | None = None,
) -> dict:
    return {
        "schema_version": 5,
        "node_type": "behavioral_source",
        "id": source_id,
        "version": 1,
        "gateway": {"language": language, "path": path},
        "content": content or [path.replace(".", r"\.")],
        "dependencies": [],
        "uses_interfaces": [],
        "interfaces": {},
    }


def test_normalizes_versioned_python_and_instruction_gateways() -> None:
    runtime = _load_runtime()

    assert runtime.normalize_gateway_language("Python>=3.11") == "python"
    assert runtime.normalize_gateway_language("Python") == "python"
    assert runtime.normalize_gateway_language("Markdown") == "markdown"
    assert runtime.normalize_gateway_language("Rust") is None


def test_resolves_whole_module_and_owned_source_partitions(tmp_path: Path) -> None:
    runtime = _load_runtime()
    module_root = tmp_path / "skills" / "demo"
    _write_yaml(
        module_root / "blueprint.yaml",
        _module(
            "demo",
            language="Markdown",
            sources={
                "demo.source.worker": {
                    "blueprint": {"base": "module-root", "path": "blueprints/worker.yaml"}
                }
            },
            children={
                "demo-child": {
                    "base": "module-root",
                    "path": "demo-child/blueprint.yaml",
                }
            },
            content=[r"SKILL\.md", r"worker\.py"],
        ),
    )
    child_root = module_root / "demo-child"
    _write_yaml(
        child_root / "blueprint.yaml",
        _module(
            "demo-child",
            language="Python>=3.11",
            gateway_path="__init__.py",
        ),
    )
    (child_root / "__init__.py").write_text("", encoding="utf-8")
    _write_yaml(
        module_root / "blueprints" / "worker.yaml",
        _source("demo.source.worker", language="Markdown", path="worker.py"),
    )
    (module_root / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    (module_root / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")

    whole = runtime.resolve_partitions(tmp_path, "demo")
    selected = runtime.resolve_partitions(tmp_path, str(module_root / "worker.py"))
    selected_module_blueprint = runtime.resolve_partitions(
        tmp_path, str(module_root / "blueprint.yaml")
    )
    selected_source_blueprint = runtime.resolve_partitions(
        tmp_path, str(module_root / "blueprints" / "worker.yaml")
    )

    assert [(part.node_id, part.leaf) for part in whole] == [
        ("demo", "instruction-module.standard.yaml"),
        ("demo.source.worker", "instruction-behavioral-source.standard.yaml"),
    ]
    assert [(part.node_id, part.leaf) for part in selected] == [
        ("demo.source.worker", "instruction-behavioral-source.standard.yaml")
    ]
    assert whole[0].gateway_path == "SKILL.md"
    assert whole[0].owned_content == [r"SKILL\.md", r"worker\.py"]
    assert whole[0].excluded_child_roots == ["skills/demo/demo-child"]
    assert whole[0].declaration_files == ["skills/demo/blueprint.yaml"]
    assert whole[1].declaration_files == ["skills/demo/blueprints/worker.yaml"]
    assert whole[0].resolved_files == ["skills/demo/SKILL.md"]
    assert whole[1].resolved_files == ["skills/demo/worker.py"]
    assert selected[0].selected_scope == "skills/demo/worker.py"
    assert selected_module_blueprint[0].node_id == "demo"
    assert selected_source_blueprint[0].node_id == "demo.source.worker"

    partial = module_root / "notes"
    partial.mkdir()
    (partial / "readme.txt").write_text("notes\n", encoding="utf-8")
    with pytest.raises(ValueError, match="directly owned"):
        runtime.resolve_partitions(tmp_path, str(partial))


def test_source_id_selects_every_direct_owned_file(tmp_path: Path) -> None:
    runtime = _load_runtime()
    module_root = tmp_path / "modules" / "worker"
    _write_yaml(
        module_root / "blueprint.yaml",
        _module(
            "worker",
            language="Python",
            gateway_path="__init__.py",
            content=[r"__init__\.py", r"first\.py", r"second\.py"],
            sources={
                "worker.source.files": {
                    "blueprint": {"base": "module-root", "path": "blueprints/files.yaml"}
                }
            },
        ),
    )
    _write_yaml(
        module_root / "blueprints" / "files.yaml",
        _source(
            "worker.source.files",
            language="Python",
            path="first.py",
            content=[r"first\.py", r"second\.py"],
        ),
    )
    (module_root / "first.py").write_text("FIRST = 1\n", encoding="utf-8")
    (module_root / "second.py").write_text("SECOND = 2\n", encoding="utf-8")
    (module_root / "__init__.py").write_text("", encoding="utf-8")

    [partition] = runtime.resolve_partitions(tmp_path, "worker.source.files")

    assert partition.selected_scope == "node:worker.source.files"
    assert partition.resolved_files == [
        "modules/worker/first.py",
        "modules/worker/second.py",
    ]


def test_duplicate_registered_node_ids_fail_closed(tmp_path: Path) -> None:
    runtime = _load_runtime()
    _write_yaml(
        tmp_path / "skills" / "first" / "blueprint.yaml",
        _module("duplicate", language="Markdown"),
    )
    _write_yaml(
        tmp_path / "skills" / "second" / "blueprint.yaml",
        _module("duplicate", language="Markdown"),
    )

    with pytest.raises(ValueError, match="duplicate"):
        runtime.resolve_partitions(tmp_path, "duplicate")


def test_source_registration_locator_must_match_the_owned_source(tmp_path: Path) -> None:
    runtime = _load_runtime()
    module_root = tmp_path / "modules" / "locator-demo"
    _write_yaml(
        module_root / "blueprint.yaml",
        _module(
            "locator-demo",
            language="Python",
            gateway_path="__init__.py",
            sources={
                "locator-demo.source.worker": {
                    "blueprint": {"base": "module-root", "path": "blueprints/wrong.yaml"}
                }
            },
        ),
    )
    _write_yaml(
        module_root / "blueprints" / "worker.yaml",
        _source("locator-demo.source.worker", language="Python", path="__init__.py"),
    )
    (module_root / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid source locator"):
        runtime.resolve_partitions(tmp_path, "locator-demo")


def test_mixed_module_retains_unsupported_partition_without_aborting(tmp_path: Path) -> None:
    runtime = _load_runtime()
    module_root = tmp_path / "modules" / "mixed"
    _write_yaml(
        module_root / "blueprint.yaml",
        _module(
            "mixed",
            language="Python",
            gateway_path="__init__.py",
            content=[r"__init__\.py", r"config\.yaml"],
            sources={
                "mixed.source.config": {
                    "blueprint": {"base": "module-root", "path": "blueprints/config.yaml"}
                }
            },
        ),
    )
    _write_yaml(
        module_root / "blueprints" / "config.yaml",
        _source("mixed.source.config", language="YAML", path="config.yaml"),
    )
    (module_root / "config.yaml").write_text("enabled: true\n", encoding="utf-8")
    (module_root / "__init__.py").write_text("", encoding="utf-8")

    partitions = runtime.resolve_partitions(tmp_path, "mixed")

    assert partitions[0].leaf == "python-module.standard.yaml"
    assert partitions[1].node_id == "mixed.source.config"
    assert partitions[1].leaf is None
    assert partitions[1].declared_gateway_language == "YAML"


def test_materializes_authoritative_python_closure_and_unknown_applicability() -> None:
    runtime = _load_runtime()

    result = runtime.materialize_standard(
        REPO_ROOT,
        "python-module.standard.yaml",
        facts={"task.kind": "refactor", "node.type": "module"},
        view="full",
    )

    assert {
        "node-standards.node",
        "node-standards.refactoring",
        "node-standards.module",
        "node-standards.python-node",
        "node-standards.python-ood",
        "node-standards.python-module",
    }.issubset({document["id"] for document in result["documents"]})
    assert any(
        item["id"] == "python-ood.behavioral-contract"
        for item in result["items"]["true"]
    )
    assert any(
        item["id"] == "skill-guidelines.adding-validator"
        for item in result["items"]["unknown"]
    )
    assert any(
        item["id"] == "skill-guidelines.adding-validator.guidance-002"
        for item in result["items"]["unknown"]
    )
    unknown = next(
        item
        for item in result["items"]["unknown"]
        if item["id"] == "skill-guidelines.adding-validator"
    )
    assert unknown["missing_facts"] == ["node.is-repository-validator"]
    procedure = next(
        item
        for item in result["items"]["true"]
        if item["id"] == "python-ood.remedies.extract-or-move-responsibility"
    )
    assert procedure["content"]["ordered"] is True
    assert procedure["content"]["steps"]
    assert procedure["content"]["invariants"]
    assert procedure["content"]["completion_conditions"]
    assert procedure["content"]["risk"]["level"] == "high"
    rule = next(
        item
        for item in result["items"]["true"]
        if item["id"] == "python-ood.behavioral-contract"
    )
    assert rule["ancestors"] == ["python-ood.behavior-preservation"]
    assert result["items"]["unknown"]
    assert result["remedies"]
    imported_remedy = next(
        remedy
        for remedy in result["remedies"]
        if remedy["id"] == "python-portability-remedy"
    )
    assert imported_remedy["target"]["document"] == "node-standards.node"
    assert result["artifacts"]
    reviews = result["evidence"]["node-standards.node"]["semantic_reviews"]
    assert {
        review["instructions"]["instruction_id"] for review in reviews.values()
    } == {
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


def test_python_source_excludes_instruction_module_smells() -> None:
    runtime = _load_runtime()

    result = runtime.materialize_standard(
        REPO_ROOT,
        "python-behavioral-source.standard.yaml",
        facts={"node.type": "behavioral_source"},
    )

    false_ids = {item["id"] for item in result["items"]["false"]}
    assert "skill-refactoring.smells.bloated-skill" in false_ids
    true_ids = {item["id"] for item in result["items"]["true"]}
    assert "skill-refactoring.smells.monolithic-script" in true_ids


def test_every_materialized_assertion_resolves_a_rule_or_ancestor_remedy() -> None:
    runtime = _load_runtime()
    leaves = (
        "python-module.standard.yaml",
        "python-behavioral-source.standard.yaml",
        "instruction-module.standard.yaml",
        "instruction-behavioral-source.standard.yaml",
    )
    uncovered: list[str] = []

    for leaf in leaves:
        result = runtime.materialize_standard(
            REPO_ROOT,
            leaf,
            facts={"task.kind": "refactor"},
            view="full",
        )
        remedy_sources = {
            (remedy["source"]["document"], remedy["source"]["ref"])
            for remedy in result["remedies"]
        }
        for bucket in ("true", "unknown"):
            for item in result["items"][bucket]:
                if item["kind"] != "rule":
                    continue
                document = item["document"]
                lineage = {
                    (document, ref)
                    for ref in (*item["ancestors"], item["id"])
                }
                for assertion in item["content"]["assertions"]:
                    assertion_ref = f'{item["id"]}#{assertion["id"]}'
                    if remedy_sources.isdisjoint(
                        {*lineage, (document, assertion_ref)}
                    ):
                        uncovered.append(f"{leaf}:{document}:{assertion_ref}")

    assert uncovered == []


def test_query_result_contains_owner_scope_and_materialized_standard() -> None:
    runtime = _load_runtime()

    result = runtime.query(
        REPO_ROOT,
        "refactor-node",
        facts={"task.kind": "refactor"},
    )

    assert result["target"] == "refactor-node"
    assert result["partitions"]
    partition = result["partitions"][0]
    standard = result["standards"][partition["standard_ref"]]
    assert partition["owner"]["node_id"] == "refactor-node"
    assert standard["items"]["true"]
    assert standard["facts"]["node.catalog.domain"] == "assistant-development"
    assert standard["facts"]["node.catalog.topics"] == [
        "assistant-authoring",
        "assistant-architecture",
        "assistant-assurance",
        "repository-workflow",
    ]
    assert standard["facts"]["node.catalog.visibility"] == "featured"
    assert standard["facts"]["node.activated_by"] == [
        "user-request",
        "skill-workflow",
    ]
    assert standard["facts"]["node.persistent_modifier"] is False
    assert standard["facts"]["node.is-personal-override"] is False
    assert standard["facts"]["node.is-repository-validator"] is False
    false_ids = {
        item["id"]
        for item in standard["items"]["false"]
    }
    assert "skill-guidelines.personal-override-structure" in false_ids
    assert "skill-guidelines.change-publication-workflow" in false_ids


def test_whole_module_deduplicates_equivalent_materializations() -> None:
    runtime = _load_runtime()

    result = runtime.query(REPO_ROOT, "refactor-node", facts={"task.kind": "refactor"})

    supported = [part for part in result["partitions"] if "standard_ref" in part]
    assert len(result["standards"]) < len(supported)
    assert result["view"] == "requirements"
    assert len(json.dumps(result)) < 180_000


def test_requirements_view_is_the_compact_default() -> None:
    runtime = _load_runtime()

    compact = runtime.materialize_standard(
        REPO_ROOT,
        "python-behavioral-source.standard.yaml",
        facts={"task.kind": "refactor"},
    )
    full = runtime.materialize_standard(
        REPO_ROOT,
        "python-behavioral-source.standard.yaml",
        facts={"task.kind": "refactor"},
        view="full",
    )

    assert compact["view"] == "requirements"
    assert compact["available_views"] == [
        "requirements",
        "evidence",
        "remedies",
        "full",
    ]
    assert set(compact) == {
        "leaf",
        "view",
        "available_views",
        "facts",
        "documents",
        "items",
    }
    assert not any(
        item["kind"] == "procedure"
        for bucket in compact["items"].values()
        for item in bucket
    )
    assert len(json.dumps(compact)) < 80_000
    assert len(json.dumps(compact)) < len(json.dumps(full))


def test_evidence_and_remedy_views_materialize_only_requested_detail() -> None:
    runtime = _load_runtime()
    arguments = (
        REPO_ROOT,
        "python-behavioral-source.standard.yaml",
    )
    facts = {"task.kind": "refactor"}

    evidence = runtime.materialize_standard(*arguments, facts=facts, view="evidence")
    remedies = runtime.materialize_standard(*arguments, facts=facts, view="remedies")

    assert set(evidence) == {
        "leaf",
        "view",
        "available_views",
        "facts",
        "documents",
        "evidence",
        "artifacts",
    }
    assert evidence["evidence"]
    assert evidence["artifacts"]
    assert set(remedies) == {
        "leaf",
        "view",
        "available_views",
        "facts",
        "documents",
        "remedies",
        "procedures",
    }
    assert remedies["remedies"]
    assert remedies["procedures"]
    assert all(
        procedure["kind"] == "procedure"
        for procedure in remedies["procedures"]
    )


def test_interface_accepts_an_explicit_standard_view() -> None:
    runtime = _load_runtime()
    args = runtime.Interface().build_parser().parse_args(
        [
            "refactor-node",
            "--repo-root",
            str(REPO_ROOT),
            "--facts-json",
            '{"task.kind":"refactor"}',
            "--view",
            "remedies",
        ]
    )

    assert args.view == "remedies"


def test_interface_rejects_non_object_facts_with_a_domain_error() -> None:
    runtime = _load_runtime()
    interface = runtime.Interface()
    args = interface.build_parser().parse_args(
        ["refactor-node", "--repo-root", str(REPO_ROOT), "--facts-json", "[]"]
    )

    with pytest.raises(ValueError, match="must decode to an object"):
        interface.run(args)
