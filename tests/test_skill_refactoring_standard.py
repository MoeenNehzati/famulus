import importlib.util
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
STANDARD = ROOT / "references/node-standards/refactoring.standard.yaml"

def load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = load_module("skill_refactoring_renderer", "references/standards-schema/render_standard_v6.py")


EXPECTED_SMELLS = {
    "bloated-skill": ("Bloated SKILL.md", "SKILL.md is long", "God Class / Long Method."),
    "executable-logic": ("Executable logic in SKILL.md", "runnable code appears inline", "Wrong layer of abstraction."),
    "missing-contract-artifacts": ("Missing or incomplete contract artifacts", "missing `blueprint.yaml`", "Missing interface declaration."),
    "duplicated-guidelines": ("Duplicated guidelines", "copy-pasted here", "Duplicated Code."),
    "mixed-abstraction-levels": ("Mixed abstraction levels", "both directs high-level orchestration", "Mixed Levels of Abstraction."),
    "dead-content": ("Dead content", "Motivational paragraphs", "Comments that restate the code."),
    "undeclared-interface": ("Undeclared interface", "never states what inputs it expects", "Undocumented public API."),
    "wrong-or-missing-category": ("Wrong or missing Category", "typed enum", None),
    "state-in-wrong-location": ("State in wrong location", "writes persistent data", "Feature Envy / wrong module."),
    "credentials-in-skill-directory": ("Credentials in skill directory", "Passwords, tokens, or API keys", None),
    "monolithic-script": ("Monolithic script", "multiple unrelated responsibilities", "Long Method."),
    "god-skill": ("God skill", "several unrelated use cases", "God Class."),
    "thin-skill": ("Thin skill", "almost no logic on top of a sub-skill", "Middle Man."),
    "leaky-internals": ("Leaky internals", "bypasses the dispatcher", "Inappropriate Intimacy."),
    "mixed-interface-responsibilities": (
        "Mixed interface responsibilities",
        "multiple substantial use cases",
        "Divergent Change / multiple responsibilities.",
    ),
}

EXPECTED_MAPPINGS = {
    "bloated-skill": {"extract-sub-skill", "extract-script", "purge-dead-content"},
    "executable-logic": {"extract-script"},
    "missing-contract-artifacts": {"add-fix-blueprint", "sync-generated-contract-artifacts"},
    "duplicated-guidelines": {"extract-reference", "inline-to-reference"},
    "mixed-abstraction-levels": {"clarify-interface", "extract-script"},
    "dead-content": {"purge-dead-content"},
    "undeclared-interface": {"clarify-interface"},
    "wrong-or-missing-category": {"declare-fix-category"},
    "state-in-wrong-location": {"relocate-state"},
    "credentials-in-skill-directory": {"relocate-credentials"},
    "monolithic-script": {"decompose-script"},
    "god-skill": {"extract-sub-skill"},
    "thin-skill": {"inline-thin-skill"},
    "leaky-internals": {"depend-on-interface"},
    "mixed-interface-responsibilities": {"decompose-interface"},
}

EXPECTED_WORKFLOW_MAPPINGS = {
    "ordering-rules": {"restore-safe-order"},
}

EXPECTED_REMEDIES = {
    "purge-dead-content": ("Purge Dead Content", "Remove motivational paragraphs", "Every instruction that directs behavior", "low"),
    "tighten-description": ("Tighten Description", "Rewrite the YAML `description` field", "All existing trigger conditions", "low"),
    "declare-fix-category": ("Declare/fix Category", "Set or correct `category`", None, "low"),
    "sync-generated-contract-artifacts": ("Sync generated contract artifacts", "regenerate the generated contract/interface blocks", None, "medium"),
    "add-fix-blueprint": ("Add/fix blueprint", "Create or correct `blueprint.yaml`", "contract artifacts remain complete and synchronized", "low"),
    "clarify-interface": ("Clarify Interface", "what inputs the skill expects", "Actual behavior", "low"),
    "extract-reference": ("Extract Reference", "move it to top-level", "Content must be identical", "low"),
    "inline-to-reference": ("Inline to Reference", "Replace duplicated local text", "reference content remains available", "low"),
    "extract-script": ("Extract Script", "Move any executable logic", "exactly the same logic", "medium"),
    "relocate-state": ("Relocate State", "Move persistent data files", "Data format and content", "medium"),
    "relocate-credentials": ("Relocate Credentials", "Move credentials", None, "medium"),
    "extract-sub-skill": ("Extract Sub-skill", "Identify a coherent sub-responsibility", "aggregate behavior must be identical", "high"),
    "decompose-script": ("Decompose Script", "Split one selected Python behavioral source", "same output as before", "high"),
    "inline-thin-skill": ("Inline Thin Skill", "near-empty pass-through", "same behavior", "high"),
    "depend-on-interface": ("Depend on Interface", "Replace the raw file call", "Output and side effects", "medium"),
    "decompose-interface": (
        "Decompose Instruction Interface",
        "split substantial routes",
        "public default entry continues",
        "high",
    ),
}

SMELL_TARGETS = {
    title: f"skill-refactoring.smells.{smell_id}"
    for smell_id, (title, _, _) in EXPECTED_SMELLS.items()
}

REMEDY_TARGETS = {
    title: f"skill-refactoring.remedies.{remedy_id}"
    for remedy_id, (title, _, _, _) in EXPECTED_REMEDIES.items()
    if remedy_id not in {"add-fix-blueprint", "inline-to-reference"}
}


def load_standard():
    return yaml.safe_load(STANDARD.read_text(encoding="utf-8"))


def descendants_by_id(document, family_id):
    family = next(node for node in document["standards"] if node["id"] == family_id)
    descendants = {}

    def visit(nodes):
        for child in nodes:
            descendants[child["id"].rsplit(".", 1)[-1]] = child
            visit(child.get("children", []))

    visit(family["children"])
    return descendants


def semantic_nodes(document):
    nodes = {}

    def visit(items):
        for node in items:
            nodes[node["id"]] = node
            visit(node.get("children", []))

    visit(document["standards"])
    return nodes


def test_standard_has_expected_identity_and_explicit_canonical_path():
    document = load_standard()
    assert document["id"] == "node-standards.refactoring"
    assert document["canonical_path"] == "references/node-standards/refactoring.standard.yaml"


def test_category_remedy_uses_schema_without_documentation_dependency():
    document = load_standard()
    remedy = semantic_nodes(document)[
        "skill-refactoring.remedies.declare-fix-category"
    ]

    assert remedy["steps"][0]["instruction"] == (
        "Use a typed enum value from `references/blueprint-schema/schema.json`."
    )


def test_all_diagnostic_signals_are_preserved_without_analogy_labels():
    smells = descendants_by_id(load_standard(), "skill-refactoring.diagnostic-signals")
    smells = {key: value for key, value in smells.items() if value["kind"] == "family"}
    assert set(smells) == set(EXPECTED_SMELLS)
    for smell_id, (title, signal, _) in EXPECTED_SMELLS.items():
        node = smells[smell_id]
        assert node["title"] == title
        definitions = {child["term"]: child["meaning"] for child in node["children"]}
        assert signal in definitions["Signal"]
        assert "Analog" not in definitions


def test_all_remedies_preserve_body_conditions_verification_and_risk():
    remedies = {
        key: value
        for key, value in descendants_by_id(load_standard(), "skill-refactoring.refactoring-moves").items()
        if value["kind"] == "procedure"
    }
    assert set(remedies) == set(EXPECTED_REMEDIES)
    for remedy_id, (title, body, preserve, risk) in EXPECTED_REMEDIES.items():
        node = remedies[remedy_id]
        serialized = yaml.safe_dump(node, sort_keys=False, width=10000)
        assert node["title"] == title
        assert body in serialized
        if preserve:
            assert preserve in serialized
        assert node["risk"]["level"] == risk
    assert "loaded only on the route that needs it" in yaml.safe_dump(remedies["extract-reference"], width=10000)
    assert "Record the selected source's public entry behavior" in yaml.safe_dump(
        remedies["decompose-script"], width=10000
    )
    assert "Verify output is equivalent" in yaml.safe_dump(remedies["depend-on-interface"], width=10000)
    verification_expectations = {
        "extract-reference": "invoke the skill and confirm the reference content is loaded",
        "extract-script": "confirm it produces the same result",
        "relocate-credentials": "verify scripts still authenticate",
        "extract-sub-skill": "stop without creating, moving, or deleting files",
        "decompose-script": "Run the recorded checks",
        "depend-on-interface": "Verify output is equivalent",
    }
    for remedy_id, expected in verification_expectations.items():
        assert expected in yaml.safe_dump(remedies[remedy_id], sort_keys=False, width=10000)
    risk_expectations = {
        "purge-dead-content": "changes no behavior",
        "tighten-description": "triggers are preserved",
        "declare-fix-category": "None",
        "sync-generated-contract-artifacts": "replace or remove stale derived content",
        "clarify-interface": "Low",
        "extract-reference": "test the loading behavior",
        "extract-script": "subtle change",
        "relocate-state": "break scripts silently",
        "relocate-credentials": "every reference",
        "extract-sub-skill": "separate approval",
        "decompose-script": "owner-local responsibility",
        "inline-thin-skill": "external callers",
        "depend-on-interface": "more or different output",
    }
    for remedy_id, expected in risk_expectations.items():
        assert expected in remedies[remedy_id]["risk"]["statement"]


def test_every_smell_has_exact_complete_remedied_by_mapping():
    document = load_standard()
    actual = {smell: set() for smell in EXPECTED_SMELLS}
    workflow = {rule: set() for rule in EXPECTED_WORKFLOW_MAPPINGS}
    for link in document["links"].values():
        assert link["relation"] == "remedied-by"
        smell = link["source"]["ref"].rsplit(".", 1)[-1]
        remedy = link["target"]["ref"].rsplit(".", 1)[-1]
        if smell in actual:
            actual[smell].add(remedy)
        else:
            workflow[smell].add(remedy)
    assert actual == EXPECTED_MAPPINGS
    assert workflow == EXPECTED_WORKFLOW_MAPPINGS


def test_risk_ordering_and_global_ordering_rules_are_explicit():
    document = load_standard()
    moves = next(node for node in document["standards"] if node["id"] == "skill-refactoring.refactoring-moves")
    assert [group["title"] for group in moves["children"]] == [
        "Safe moves — apply first",
        "Medium moves — apply after safe moves are done",
        "Structural moves — apply last, one at a time",
    ]
    rules = next(node for node in document["standards"] if node["id"] == "skill-refactoring.ordering-rules")
    statements = [assertion["statement"] for assertion in rules["assertions"]]
    assert statements == [
        (
            "Map affected behavior, ownership, dependency, authorization, and "
            "verification edges."
        ),
        "Apply all safe moves first, verify, then medium, then structural.",
        "Verify between structural moves; never batch them.",
        "An unvalidated move is non-final; fix and rerun within scope or revert.",
    ]


def test_rendering_is_deterministic_without_a_registered_generated_view():
    document = load_standard()
    first = renderer.render_document(document)
    second = renderer.render_document(document)
    assert first == second
    assert "Remedies: Add/fix blueprint" in first
    assert "Remedies: Inline to Reference" in first
    assert first.count("Addresses:") == sum(
        len(value)
        for mappings in (EXPECTED_MAPPINGS, EXPECTED_WORKFLOW_MAPPINGS)
        for value in mappings.values()
    )
