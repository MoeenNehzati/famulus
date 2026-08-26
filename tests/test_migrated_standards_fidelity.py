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
    "skill-guidelines.md": "2ed65f9c5b93832221181a330a16ca72871c364925416c7d4712d42b18b52307",
    "document-profile-schema.md": "178bbd7c1fc076208fe576f871ab7bba936941fce83fdb7a46f784b1cb28d967",
}

def load_module(name, relative_path):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

validator = load_module("migrated_validator", "references/standards-schema/validate_standard_v6.py")
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

def test_standards_validate_and_generated_views_are_fresh():
    for standard in NODE_STANDARDS.glob("*.standard.yaml"):
        assert validator.validate_file(standard, ROOT) == []
    assert validator.validate_file(PROFILE, ROOT) == []
    document = yaml.safe_load(PROFILE.read_text(encoding="utf-8")); rendered = renderer.render_document(document)
    assert rendered == renderer.render_document(document) == PROFILE_VIEW.read_text(encoding="utf-8")

def test_skill_guidelines_make_v5_the_only_live_blueprint_authoring_family():
    document = yaml.safe_load((NODE_STANDARDS / "node.standard.yaml").read_text(encoding="utf-8"))
    families = {family["id"]: family for family in document["standards"]}
    live = families["skill-guidelines.module-behavioral-source-v5"]
    assert "sole live blueprint and interface authoring authority" in live["summary"]

    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in NODE_STANDARDS.glob("*.standard.yaml")
    )
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
    for path in NODE_STANDARDS.glob("*.standard.yaml"):
        text = path.read_text(encoding="utf-8").lower()
        assert not [marker for marker in legacy_markers if marker in text], path

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
    disposition = yaml.safe_load(
        (NODE_STANDARDS / "authority-disposition.yaml").read_text(encoding="utf-8")
    )
    assert disposition["source_authorities"]["interface-design"]["status"] == "excluded"

def test_skill_hooks_describe_the_live_v5_guideline_contract():
    blueprint_hook = (ROOT / ".githooks/skill/check-blueprints").read_text(encoding="utf-8")
    assert "v5 module/source" in blueprint_hook
    assert "v3" not in blueprint_hook
    assert "machine" "-module" not in blueprint_hook

    dependency_hook = (ROOT / ".githooks/skill/check-dependencies").read_text(encoding="utf-8")
    assert "source `uses_interfaces`" in dependency_hook
    assert "depends_on" not in dependency_hook

def test_render_mode_is_schema_checked_and_defaults_to_semantic():
    profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    assert "render_mode" not in profile
    assert renderer.render_document(profile) == PROFILE_VIEW.read_text(encoding="utf-8")

def test_every_source_block_is_exactly_preserved_once():
    for fixture_name, map_name, standard in (
        ("skill-guidelines.md", "skill-guidelines-source-map.yaml", FROZEN_GUIDELINES),
        ("document-profile-schema.md", "document-profile-source-map.yaml", PROFILE),
    ):
        fixture = FIXTURES / fixture_name; lines = fixture.read_text(encoding="utf-8").splitlines(); mapping = yaml.safe_load((FIXTURES / map_name).read_text(encoding="utf-8"))
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == SOURCE_DIGESTS[fixture_name] == mapping["source_sha256"]
        indexed = nodes(yaml.safe_load(standard.read_text(encoding="utf-8"))); covered = []
        for unit in mapping["units"]:
            text = "\n".join(lines[unit["start"]-1:unit["end"]]); assert text == unit["text"]
            assert unit["content_digest"] == "sha256:" + hashlib.sha256((text+"\n").encode()).hexdigest()
            covered.extend(n for n in range(unit["start"], unit["end"]+1) if lines[n-1].strip())
            if unit["kind"] == "contextual":
                assert "target" not in unit and unit["contextual_reason"]
                if fixture_name == "skill-guidelines.md":
                    assert all(lines[n-1].strip() == "---" or lines[n-1].strip().startswith("```") for n in range(unit["start"], unit["end"]+1))
            else:
                assert unit["kind"] == "semantic"
                target = unit.get("target") or unit["targets"][0]
                assert resolve(indexed[target["target_ref"]], target["field"]) == target["expected"]
                if fixture_name == "skill-guidelines.md": assert target["expected"] == text
        expected = {n for n,line in enumerate(lines,1) if line.strip()}
        assert set(covered) == expected and len(covered) == len(set(covered))

def test_guideline_headings_are_preserved_as_families_and_view_is_readable():
    document = yaml.safe_load(FROZEN_GUIDELINES.read_text(encoding="utf-8")); titles = {family["title"] for family in document["standards"]}
    assert document["render_mode"] == "source-faithful"
    source = (FIXTURES / "skill-guidelines.md").read_text(encoding="utf-8")
    markdown_titles = {m.group(1) for m in re.finditer(r"^#{1,6}\s+(.+)$", source, re.M)}
    markdown_titles.discard("Skill Module Standards")
    bold_titles = {m.group(1) for m in re.finditer(r"^\*\*(.+?)\*\*", source, re.M)}
    assert markdown_titles | bold_titles <= titles
    rendered = FROZEN_GUIDELINES_VIEW.read_text(encoding="utf-8")
    for phrase in ("A skill is a software module", "Blueprint authoring", "Dispatcher role", "Validator and test conventions"):
        assert phrase in rendered
    assert not re.search(r"^#{2,6} (?:Requirement|Guidance|Example|Procedure) [0-9]{3}$", rendered, re.M)
    assert rendered.count("# Skill Module Standards") == 1
    ordered = ["## A skill is a software module.", "## 1. Skill identity and contract come first", "## Blueprint authoring", "## Canonical interface names", "## Validator and test conventions"]
    assert [rendered.index(text) for text in ordered] == sorted(rendered.index(text) for text in ordered)
    source_size = len((FIXTURES / "skill-guidelines.md").read_text(encoding="utf-8"))
    assert len(rendered) < source_size * 1.35
    assert "the module blueprint declares sources, exports, and export access" in rendered
    assert "each behavioral source declares its own interfaces and direct dependencies" in rendered
    assert "```python\nfrom officina.runtime.python_machine_interface" in rendered and "class Interface(PythonMachineInterface):" in rendered
    assert "1. Create `validators/<name>.py`" in rendered and "4. Add a `tests/validate_<name>.py`" in rendered

def test_every_fenced_source_block_is_one_verbatim_unit_and_contiguous_in_view():
    source = (FIXTURES / "skill-guidelines.md").read_text(encoding="utf-8")
    fenced = re.findall(r"^```[^\n]*\n.*?^```$", source, re.M | re.S)
    assert fenced
    rendered = FROZEN_GUIDELINES_VIEW.read_text(encoding="utf-8")
    mapping = yaml.safe_load((FIXTURES / "skill-guidelines-source-map.yaml").read_text(encoding="utf-8"))["units"]
    semantic_text = {unit["text"] for unit in mapping if unit["kind"] == "semantic"}
    for block in fenced:
        assert block in semantic_text
        assert block in rendered
    tags = {block.splitlines()[0] for block in fenced}
    assert {"```python", "```bash", "```text"} <= tags
    assert any("regex" in tag for tag in tags)

def test_guideline_block_ids_are_frozen_family_local_ids():
    frozen = yaml.safe_load((FIXTURES / "skill-guidelines-frozen-ids.yaml").read_text(encoding="utf-8"))
    mapping = yaml.safe_load((FIXTURES / "skill-guidelines-source-map.yaml").read_text(encoding="utf-8"))["units"]
    semantic = [unit for unit in mapping if unit["kind"] == "semantic"]
    assert len(frozen["blocks"]) == len(semantic)
    assert len(set(frozen["blocks"].values())) == len(semantic)
    pattern = re.compile(r"^skill-guidelines\.[a-z0-9-]+\.(?:requirement|guidance|example|procedure)-[0-9]{3}$")
    assert all(pattern.fullmatch(value) for value in frozen["blocks"].values())
    for unit in semantic:
        assert unit["target"]["target_ref"].split("#", 1)[0] == frozen["blocks"][f'{unit["start"]}-{unit["end"]}']

def test_enforcement_inventory_is_exhaustive_and_typed():
    source = (FIXTURES / "skill-guidelines.md").read_text(encoding="utf-8")
    extracted = {p.rstrip(".,;:") for p in re.findall(r"(?:\.githooks|references|validators|tests|skills|src)/[A-Za-z0-9_.*<>/{},-]+(?:\.[A-Za-z0-9_*<>/{},-]+)?", source)}
    inventory = yaml.safe_load((FIXTURES / "skill-guidelines-enforcement-ledger.yaml").read_text(encoding="utf-8"))["references"]
    assert {entry["path"] for entry in inventory} == extracted
    kinds = {"validation-entrypoint", "validator-or-test", "schema-authority", "implementation-reference", "documentation-reference", "generated-artifact", "directory-or-package"}
    assert all(entry["kind"] in kinds for entry in inventory)
    hook = next(entry for entry in inventory if entry["path"] == ".githooks/pre-commit")
    assert hook["kind"] == "validation-entrypoint" and hook["integration_point"] == "precommit"

def test_document_profile_scope_fields_template_and_normalization():
    rendered = PROFILE_VIEW.read_text(encoding="utf-8")
    assert "canonical schema for top-of-document TeX profile comments" in rendered
    for field in ("Document type", "Field/subfield", "Purpose", "Audience", "Assumed background", "Target level of rigor/detail", "Expected document length", "Relationship to main paper or companion documents"):
        assert field in rendered and f"% {field}:" in rendered
    for phrase in ("may be left unspecified", "may be left blank when irrelevant", "Infer fields only", "Reader familiarity", "journal-article", "conference-article", "research-presentation", "research-notes"):
        assert phrase in rendered


def test_layered_cutover_preserves_frozen_v1_source_fixtures() -> None:
    disposition = yaml.safe_load(
        (NODE_STANDARDS / "authority-disposition.yaml").read_text(encoding="utf-8")
    )
    assert disposition["source_authorities"]["skill-guidelines"]["status"] == "replaced"
    assert not (ROOT / "references/skill-standards/skill-guidelines.standard.yaml").exists()
    assert not (ROOT / "references/skill-standards/skill-guidelines.md").exists()
    frozen = yaml.safe_load(FROZEN_GUIDELINES.read_text(encoding="utf-8"))
    assert frozen["standard_version"] == "1.0.0"
    assert frozen["revision"] == 3
    assert any(
        family["id"] == "skill-guidelines.module-behavioral-source-v4"
        for family in frozen["standards"]
    )
    for fixture_name, digest in SOURCE_DIGESTS.items():
        assert (
            hashlib.sha256((FIXTURES / fixture_name).read_bytes()).hexdigest()
            == digest
        )
