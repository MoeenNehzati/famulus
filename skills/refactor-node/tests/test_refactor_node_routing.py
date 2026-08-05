import json
from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _source_for_interface(
    root: dict, source_interface: str
) -> tuple[dict, dict]:
    source_id, separator, _ = source_interface.rpartition(".interface.")
    assert separator
    locator = root["sources"][source_id]["blueprint"]
    assert locator["base"] == "module-root"
    source = _load_yaml(SKILL_ROOT / locator["path"])
    assert source["id"] == source_id
    return source, source["interfaces"][source_interface]


def _source_for_export(root: dict, export_id: str) -> tuple[dict, dict]:
    source_interface = root["exports"][export_id]["source_interface"]
    return _source_for_interface(root, source_interface)


def test_module_identity_and_public_interfaces() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")

    assert root["id"] == "refactor-node"
    assert root["children"] == {
        "refactor-node-rtx": {
            "base": "module-root",
            "path": "_rtx/blueprint.yaml",
        }
    }
    assert set(root["exports"]) == {
        "refactor-node.interface.default",
        "refactor-node.interface.query-standards",
        "refactor-node.interface.refactor-instructions",
        "refactor-node.interface.refactor-python",
    }
    assert (SKILL_ROOT / "_rtx" / "_closure_engine.py").is_file()


def test_gateway_routes_to_both_language_sources() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    gateway, _ = _source_for_export(root, "refactor-node.interface.default")

    assert gateway["dependencies"] == []
    assert gateway["interfaces"]["refactor-node.source.gateway.interface.default"][
        "contract"
    ]["execution"]["state_effect"] == "mutating"
    assert gateway["uses_interfaces"] == [
        {
            "interface": (
                "refactor-node.interface.query-standards"
            ),
            "version": 4,
        },
        {
            "interface": (
                "refactor-node.source.instruction-refactoring.interface."
                "refactor-instructions"
            ),
            "version": 1,
        },
        {
            "interface": (
                "refactor-node.source.python-refactoring.interface."
                "refactor-python"
            ),
            "version": 1,
        }
    ]


def test_query_uses_the_common_standard_extractor_interface() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    runtime_locator = root["children"]["refactor-node-rtx"]
    runtime_root = _load_yaml(SKILL_ROOT / runtime_locator["path"])
    source_locator = runtime_root["sources"][
        "refactor-node-rtx.source.query-standards"
    ]["blueprint"]
    query_source = _load_yaml(
        SKILL_ROOT / "_rtx" / source_locator["path"]
    )

    assert query_source["uses_interfaces"] == [
        {"interface": "common.interface.standard-extractor", "version": 1}
    ]

    common_root = _load_yaml(
        SKILL_ROOT.parents[1] / "src" / "officina" / "common" / "blueprint.yaml"
    )
    exported = common_root["exports"]["common.interface.standard-extractor"]
    assert exported["source_interface"] == (
        "common.source.standard-extractor.interface.python-api"
    )
    assert "refactor-node-rtx" in exported["access"]["allowed_callers"]

    certification_basis = json.loads(
        (
            SKILL_ROOT.parents[1]
            / "references"
            / "certification"
            / "certification-basis-roots.json"
        ).read_text(encoding="utf-8")
    )
    assert "src/officina/common/configured_schema.py" in certification_basis
    assert "src/officina/dispatcher/catalog.py" in certification_basis


def test_query_contract_exposes_compact_and_on_demand_views() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    facade = root["exports"]["refactor-node.interface.query-standards"]
    runtime_root = _load_yaml(SKILL_ROOT / "_rtx" / "blueprint.yaml")
    source_locator = runtime_root["sources"][
        "refactor-node-rtx.source.query-standards"
    ]["blueprint"]
    query_source = _load_yaml(SKILL_ROOT / "_rtx" / source_locator["path"])
    interface = query_source["interfaces"][
        "refactor-node-rtx.source.query-standards.interface.query-standards"
    ]

    assert facade["facade_interface"]["version"] == 4
    assert interface["version"] == 4
    assert interface["contract"]["arguments"]["view"]["default"] == "requirements"
    output = interface["contract"]["outputs"][0]
    assert output["schema"] == {
        "path": "schemas/query-result.schema.json",
        "fragment": "#",
    }
    assert "type" not in output
    assert r"schemas/query-result\.schema\.json" in query_source["content"]
    flags = interface["process_binding"]["patterns"][0]["allowed_flags"]
    assert {"--view", "--refs-json", "--query-json"}.issubset(flags)
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    for view in ("requirements", "context", "evidence", "remedies", "full"):
        assert f"--view {view}" in skill
    assert "--refs-json" in skill
    assert "--query-json" in skill


def test_router_and_python_export_have_distinct_owned_gateways() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    gateway, _ = _source_for_export(root, "refactor-node.interface.default")
    python_source, _ = _source_for_export(
        root, "refactor-node.interface.refactor-python"
    )
    instruction_source, _ = _source_for_export(
        root, "refactor-node.interface.refactor-instructions"
    )

    assert gateway["gateway"] == {"language": "Markdown", "path": "SKILL.md"}
    assert gateway["content"] == [r"SKILL\.md"]
    assert python_source["gateway"] == {
        "language": "Markdown",
        "path": "instructions/python-refactoring.md",
    }
    assert instruction_source["gateway"] == {
        "language": "Markdown",
        "path": "instructions/instruction-refactoring.md",
    }


def test_python_interface_is_source_owned_and_approval_gated() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    source, interface = _source_for_export(
        root, "refactor-node.interface.refactor-python"
    )

    assert source["content"] == [
        r"instructions/python\-refactoring\.md",
    ]
    contract = interface["contract"]
    assert contract["execution"]["state_effect"] == "mutating"
    assert contract["interaction"]["unattended_outcome"] == "approval-required"
    assert {outcome["id"] for outcome in contract["outcomes"]} == {
        "proposal-ready",
        "approval-required",
        "refactored",
        "partial",
        "failed",
    }


def test_python_material_is_owned_only_by_the_python_source() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    gateway, _ = _source_for_export(root, "refactor-node.interface.default")
    python_source, _ = _source_for_export(
        root, "refactor-node.interface.refactor-python"
    )
    instruction_source, _ = _source_for_export(
        root, "refactor-node.interface.refactor-instructions"
    )

    assert gateway["content"] == [r"SKILL\.md"]
    assert python_source["content"] == [
        r"instructions/python\-refactoring\.md",
    ]
    assert instruction_source["content"] == [
        r"instructions/instruction\-refactoring\.md"
    ]


def test_instruction_interface_is_source_owned_and_approval_gated() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    source, interface = _source_for_export(
        root, "refactor-node.interface.refactor-instructions"
    )

    assert source["id"] == "refactor-node.source.instruction-refactoring"
    contract = interface["contract"]
    assert contract["execution"]["state_effect"] == "mutating"
    assert contract["interaction"]["unattended_outcome"] == "approval-required"


def test_router_declares_exact_standard_leaf_mapping_and_closure_rules() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "query-standards" in normalized
    assert "unknown" in normalized
    assert "Never silently discard" in normalized
    assert "remedied-by" in normalized
