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
    assert root["children"] == {}
    assert set(root["exports"]) == {
        "refactor-node.interface.default",
        "refactor-node.interface.refactor-instructions",
        "refactor-node.interface.refactor-python",
    }


def test_gateway_routes_to_both_language_sources() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    gateway, _ = _source_for_export(root, "refactor-node.interface.default")

    assert gateway["dependencies"] == []
    assert gateway["interfaces"]["refactor-node.source.gateway.interface.default"][
        "contract"
    ]["execution"]["state_effect"] == "mutating"
    assert gateway["uses_interfaces"] == [
        {
            "interface": "standards.interface.query-standard",
            "version": 1,
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


def test_router_uses_the_common_explicit_standard_query() -> None:
    common_root = _load_yaml(
        SKILL_ROOT.parents[1] / "src" / "officina" / "standards" / "blueprint.yaml"
    )
    exported = common_root["exports"]["standards.interface.query-standard"]
    assert exported["source_interface"] == (
        "standards.source.query.interface.query-standard"
    )
    assert "refactor-node" in exported["access"]["allowed_callers"]


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

    assert "standards.interface.query-standard" in normalized
    for standard in (
        "python-module.standard.yaml",
        "python-behavioral-source.standard.yaml",
        "instruction-module.standard.yaml",
        "instruction-behavioral-source.standard.yaml",
    ):
        assert standard in normalized
    assert "complete pinned import closure" in normalized
    assert "mixed" in normalized
    assert "unknown" in normalized
    assert "Never silently discard" in normalized
    assert "remedied-by" in normalized


def test_router_discovers_implementation_children_before_explicit_query() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    preflight = " ".join(
        skill.partition("## Preflight")[2]
        .partition("## Standards retrieval")[0]
        .split()
    )

    assert "implementation child" in preflight
    assert "before querying policy" in preflight.lower()
    assert "standards.interface.query-standard" in preflight
    assert "inferred target" in preflight


def test_router_verifies_current_query_provenance_fields() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    preflight = " ".join(
        skill.partition("## Preflight")[2].partition("## Standards retrieval")[0].split()
    )

    for field in (
        "caller",
        "target",
        "repository root",
        "selected standard path",
        "task facts",
        "view",
        "refs",
    ):
        assert field in preflight
    assert "refactor-node.interface.query-standards" not in skill


def test_router_requires_preservation_evidence_and_review_gates() -> None:
    skill = " ".join(
        (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split()
    )

    for requirement in (
        "preservation map",
        "reverse consumers",
        "canonical evidence",
        "supplemental change-relevant evidence",
        "semantic review",
        "exact diff against the preservation map",
        "An unvalidated move is non-final",
    ):
        assert requirement in skill


def test_instruction_routes_extend_the_preservation_map() -> None:
    instruction = " ".join(
        (SKILL_ROOT / "instructions/instruction-refactoring.md")
        .read_text(encoding="utf-8")
        .split()
    )
    python = " ".join(
        (SKILL_ROOT / "instructions/python-refactoring.md")
        .read_text(encoding="utf-8")
        .split()
    )

    assert "Extend the router's preservation map" in instruction
    assert "predicate, branch outcome, fallback or recovery" in instruction
    assert "producer output through authorized consumer invocation" in instruction
    assert "Extend the router map" in python
    assert "focused or reverse integration evidence" in python


def test_each_mutating_route_handles_approved_reentry() -> None:
    for relative in (
        "instructions/python-refactoring.md",
        "instructions/instruction-refactoring.md",
    ):
        text = " ".join((SKILL_ROOT / relative).read_text(encoding="utf-8").split())
        assert "On approved re-entry" in text
        assert "apply exactly the approved move" in text
        assert "return the exact diff and verification evidence" in text
