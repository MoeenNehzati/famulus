import hashlib
import importlib.util
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures/standards"
NODE_STANDARDS = ROOT / "references/node-standards"
FROZEN_GUIDELINES = FIXTURES / "skill-guidelines.v1.standard.yaml"
FROZEN_GUIDELINES_VIEW = FIXTURES / "skill-guidelines.v1.md"
PROFILE = ROOT / "references/document-standards/document-profile.standard.yaml"
PROFILE_VIEW = ROOT / "references/document-standards/document-profile.md"
SOURCE_DIGESTS = {
    "skill-guidelines.md": "9b4d081de0dbbaf116b3a5e46db9a21b3f09d254a2801bbf305d458990c99d05",
    "document-profile-schema.md": "178bbd7c1fc076208fe576f871ab7bba936941fce83fdb7a46f784b1cb28d967",
}

def load_module(name, relative_path):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

renderer = load_module("migrated_renderer", "references/standards-schema/render_standard_v6.py")

def nodes(document):
    result = {}
    def visit(items):
        for item in items:
            result[item["id"]] = item
            for assertion in item.get("assertions", []): result[f'{item["id"]}#{assertion["id"]}'] = assertion
            visit(item.get("children", []))
    visit(document["standards"]); return result

def resolve(value, pointer):
    for token in pointer.strip("/").split("/") if pointer != "/" else []: value = value[int(token)] if isinstance(value, list) else value[token]
    return value

def test_skill_guidelines_make_v5_the_only_live_blueprint_authoring_family():
    node_standard_texts = {
        path.name: path.read_text(encoding="utf-8")
        for path in NODE_STANDARDS.glob("*.standard.yaml")
    }
    document = yaml.safe_load(node_standard_texts["node.standard.yaml"])
    families = {family["id"]: family for family in document["standards"]}
    live = families["skill-guidelines.module-behavioral-source-v5"]
    assert "sole live blueprint and interface authoring authority" in live["summary"]

    serialized = "\n".join(node_standard_texts.values())
    node_kinds = next(
        rule
        for rule in live["children"]
        if rule["id"] == "skill-guidelines.module-behavioral-source-v5.node-kinds"
    )
    node_kind_assertions = {
        assertion["id"]: assertion for assertion in node_kinds["assertions"]
    }
    assert set(node_kind_assertions) == {
        "blueprint-node-type",
        "optional-implementation-child",
        "implementation-child-contract",
    }
    optional_child = node_kind_assertions["optional-implementation-child"]
    assert optional_child["modality"] == "required"
    assert "at most one direct non-discoverable implementation child" in optional_child[
        "statement"
    ]
    assert "need not declare one" in optional_child["statement"]
    blueprint_type = node_kind_assertions["blueprint-node-type"]["statement"]
    for required in ("schema_version: 5", "node_type", "module", "behavioral_source"):
        assert required in blueprint_type
    statements = "\n".join(
        assertion["statement"]
        for rule in live["children"]
        for assertion in rule["assertions"]
    )
    assert "Hash what you own; certify what you depend on." in statements
    for required in (
        "discovery",
        "gateway",
        "content",
        "sources",
        "exports",
        "source_interface",
    ):
        assert required in serialized

    legacy_markers = (
        "machine" "-interface",
        "llm" "-interface",
        "machine" "-module",
        "behavior" "-source",
        ".machine" ".",
        ".llm" ".",
        "default_interface",
        "blueprint_type",
        "skill_interface",
        "hidden sidecar",
        "skill" "-audit",
        "task 6 migration input",
        "schema version 3",
        "schema-version-2",
        ".last_audit",
        ".pooled-blueprint",
    )
    for name, text in node_standard_texts.items():
        lowered = text.lower()
        assert not [marker for marker in legacy_markers if marker in lowered], name

def test_interface_design_is_one_source_owned_authority():
    standards_root = ROOT / "references/skill-standards"
    guide = standards_root / "interface-design.md"
    guide_text = guide.read_text(encoding="utf-8")
    lowered = guide_text.lower()

    assert "interface is a named contract owned by one `behavioral_source`" in lowered
    assert re.search(r"gateway\s+language and process binding are orthogonal", lowered)
    for retired in (
        standards_root / ("llm" "-interface-design.md"),
        standards_root / ("instruction" "-source-design.md"),
        standards_root / "blueprints" / ("llm" "-interface-design.yaml"),
        standards_root / "blueprints" / ("instruction" "-source-design.yaml"),
    ):
        assert not retired.exists()
    for marker in (
        "llm" "-interface",
        "machine" "-interface",
        "llm" "_interfaces/",
        ".llm" ".",
        ".machine" ".",
    ):
        assert marker not in lowered

    root_blueprint = yaml.safe_load((standards_root / "blueprint.yaml").read_text(encoding="utf-8"))
    assert "skill-standards.source.interface-design" in root_blueprint["sources"]
    design_blueprint = yaml.safe_load(
        (standards_root / "blueprints/interface-design.yaml").read_text(encoding="utf-8")
    )
    assert design_blueprint["gateway"]["path"] == "interface-design.md"
    assert design_blueprint["id"] == "skill-standards.source.interface-design"

    consumer = (ROOT / "skills/refactor-node/SKILL.md").read_text(encoding="utf-8")
    consumer_blueprint = (
        ROOT / "skills/refactor-node/blueprints/gateway.yaml"
    ).read_text(encoding="utf-8")
    assert "references/skill-standards/interface-design.md" not in consumer
    assert "skill-standards.source.interface-design" not in consumer_blueprint
def test_skill_hooks_describe_the_live_v5_guideline_contract():
    blueprint_hook = (ROOT / ".githooks/skill/check-blueprints").read_text(encoding="utf-8")
    assert "v5 module/source" in blueprint_hook
    assert "v3" not in blueprint_hook
    assert "machine" "-module" not in blueprint_hook

    dependency_hook = (ROOT / ".githooks/skill/check-dependencies").read_text(encoding="utf-8")
    assert "source `uses_interfaces`" in dependency_hook
    assert "depends_on" not in dependency_hook

def test_render_mode_defaults_to_semantic():
    document = {
        "canonical_path": "synthetic.standard.yaml",
        "title": "Synthetic",
        "purpose": "Exercise semantic rendering.",
        "standards": [
            {
                "kind": "rule",
                "id": "synthetic.rule",
                "title": "Synthetic rule",
                "assertions": [
                    {
                        "id": "required-behavior",
                        "modality": "required",
                        "statement": "Preserve the semantic default.",
                    }
                ],
            }
        ],
    }
    assert "render_mode" not in document
    rendered = renderer.render_document(document)
    assert "## Synthetic rule" in rendered
    assert "- **required** — Preserve the semantic default." in rendered


def test_frozen_v1_sources_preserve_content_structure_and_provenance():
    source_bytes = {
        name: (FIXTURES / name).read_bytes()
        for name in SOURCE_DIGESTS
    }
    source_text = {
        name: content.decode("utf-8") for name, content in source_bytes.items()
    }
    mappings = {
        "skill-guidelines.md": yaml.safe_load(
            (FIXTURES / "skill-guidelines-source-map.yaml").read_text(
                encoding="utf-8"
            )
        ),
        "document-profile-schema.md": yaml.safe_load(
            (FIXTURES / "document-profile-source-map.yaml").read_text(
                encoding="utf-8"
            )
        ),
    }
    documents = {
        "skill-guidelines.md": yaml.safe_load(
            FROZEN_GUIDELINES.read_text(encoding="utf-8")
        ),
        "document-profile-schema.md": yaml.safe_load(
            PROFILE.read_text(encoding="utf-8")
        ),
    }
    rendered = FROZEN_GUIDELINES_VIEW.read_text(encoding="utf-8")

    for fixture_name in SOURCE_DIGESTS:
        lines = source_text[fixture_name].splitlines()
        mapping = mappings[fixture_name]
        assert (
            hashlib.sha256(source_bytes[fixture_name]).hexdigest()
            == SOURCE_DIGESTS[fixture_name]
            == mapping["source_sha256"]
        )
        indexed = nodes(documents[fixture_name])
        covered = []
        for unit in mapping["units"]:
            text = "\n".join(lines[unit["start"] - 1 : unit["end"]])
            assert text == unit["text"]
            assert unit["content_digest"] == "sha256:" + hashlib.sha256(
                (text + "\n").encode()
            ).hexdigest()
            covered.extend(
                line_number
                for line_number in range(unit["start"], unit["end"] + 1)
                if lines[line_number - 1].strip()
            )
            if unit["kind"] == "contextual":
                assert "target" not in unit and unit["contextual_reason"]
                if fixture_name == "skill-guidelines.md":
                    assert all(
                        lines[line_number - 1].strip() == "---"
                        or lines[line_number - 1].strip().startswith("```")
                        for line_number in range(unit["start"], unit["end"] + 1)
                    )
            else:
                assert unit["kind"] == "semantic"
                target = unit.get("target") or unit["targets"][0]
                assert (
                    resolve(indexed[target["target_ref"]], target["field"])
                    == target["expected"]
                )
                if fixture_name == "skill-guidelines.md":
                    assert target["expected"] == text
        expected = {
            line_number
            for line_number, line in enumerate(lines, 1)
            if line.strip()
        }
        assert set(covered) == expected
        assert len(covered) == len(set(covered))

    frozen_document = documents["skill-guidelines.md"]
    titles = {family["title"] for family in frozen_document["standards"]}
    assert frozen_document["render_mode"] == "source-faithful"
    source = source_text["skill-guidelines.md"]
    markdown_titles = {m.group(1) for m in re.finditer(r"^#{1,6}\s+(.+)$", source, re.M)}
    markdown_titles.discard("Skill Module Standards")
    bold_titles = {m.group(1) for m in re.finditer(r"^\*\*(.+?)\*\*", source, re.M)}
    assert markdown_titles | bold_titles <= titles
    for phrase in ("A skill is a software module", "Blueprint authoring", "Dispatcher role", "Validator and test conventions"):
        assert phrase in rendered
    assert not re.search(r"^#{2,6} (?:Requirement|Guidance|Example|Procedure) [0-9]{3}$", rendered, re.M)
    assert rendered.count("# Skill Module Standards") == 1
    ordered = ["## A skill is a software module.", "## 1. Skill identity and contract come first", "## Blueprint authoring", "## Canonical interface names", "## Validator and test conventions"]
    assert [rendered.index(text) for text in ordered] == sorted(rendered.index(text) for text in ordered)
    source_size = len(source)
    assert len(rendered) < source_size * 1.35
    assert "the module blueprint declares sources, exports, and export access" in rendered
    assert "each behavioral source declares its own interfaces and direct dependencies" in rendered
    assert "```python\nfrom officina.runtime.python_machine_interface" in rendered and "class Interface(PythonMachineInterface):" in rendered
    assert "1. Create `validators/<name>.py`" in rendered and "4. Add a `tests/validate_<name>.py`" in rendered

    fenced = re.findall(r"^```[^\n]*\n.*?^```$", source, re.M | re.S)
    assert fenced
    mapping = mappings["skill-guidelines.md"]["units"]
    semantic_text = {unit["text"] for unit in mapping if unit["kind"] == "semantic"}
    for block in fenced:
        assert block in semantic_text
        assert block in rendered
    tags = {block.splitlines()[0] for block in fenced}
    assert {"```python", "```bash", "```text"} <= tags
    assert any("regex" in tag for tag in tags)

    frozen = yaml.safe_load((FIXTURES / "skill-guidelines-frozen-ids.yaml").read_text(encoding="utf-8"))
    semantic = [unit for unit in mapping if unit["kind"] == "semantic"]
    assert len(frozen["blocks"]) == len(semantic)
    assert len(set(frozen["blocks"].values())) == len(semantic)
    pattern = re.compile(r"^skill-guidelines\.[a-z0-9-]+\.(?:requirement|guidance|example|procedure)-[0-9]{3}$")
    assert all(pattern.fullmatch(value) for value in frozen["blocks"].values())
    for unit in semantic:
        assert unit["target"]["target_ref"].split("#", 1)[0] == frozen["blocks"][f'{unit["start"]}-{unit["end"]}']

    extracted = {p.rstrip(".,;:") for p in re.findall(r"(?:\.githooks|references|validators|tests|skills|src)/[A-Za-z0-9_.*<>/{},-]+(?:\.[A-Za-z0-9_*<>/{},-]+)?", source)}
    inventory = yaml.safe_load((FIXTURES / "skill-guidelines-enforcement-ledger.yaml").read_text(encoding="utf-8"))["references"]
    assert {entry["path"] for entry in inventory} == extracted
    kinds = {"validation-entrypoint", "validator-or-test", "schema-authority", "implementation-reference", "documentation-reference", "generated-artifact", "directory-or-package"}
    assert all(entry["kind"] in kinds for entry in inventory)
    hook = next(entry for entry in inventory if entry["path"] == ".githooks/pre-commit")
    assert hook["kind"] == "validation-entrypoint" and hook["integration_point"] == "precommit"

    disposition = yaml.safe_load(
        (NODE_STANDARDS / "authority-disposition.yaml").read_text(encoding="utf-8")
    )
    assert disposition["source_authorities"]["skill-guidelines"]["status"] == "replaced"
    assert not (ROOT / "references/skill-standards/skill-guidelines.standard.yaml").exists()
    assert not (ROOT / "references/skill-standards/skill-guidelines.md").exists()
    assert frozen_document["standard_version"] == "1.0.0"
    assert frozen_document["revision"] == 3
    assert any(
        family["id"] == "skill-guidelines.module-behavioral-source-v4"
        for family in frozen_document["standards"]
    )

def test_document_profile_scope_fields_template_and_normalization():
    rendered = PROFILE_VIEW.read_text(encoding="utf-8")
    assert "canonical schema for top-of-document TeX profile comments" in rendered
    for field in ("Document type", "Field/subfield", "Purpose", "Audience", "Assumed background", "Target level of rigor/detail", "Expected document length", "Relationship to main paper or companion documents"):
        assert field in rendered and f"% {field}:" in rendered
    for phrase in ("may be left unspecified", "may be left blank when irrelevant", "Infer fields only", "Reader familiarity", "journal-article", "conference-article", "research-presentation", "research-notes"):
        assert phrase in rendered
