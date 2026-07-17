import hashlib
import importlib.util
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures/standards"
GUIDELINES = ROOT / "references/skill-standards/skill-guidelines.standard.yaml"
PROFILE = ROOT / "references/document-standards/document-profile.standard.yaml"
GUIDELINES_VIEW = ROOT / "references/skill-standards/skill-guidelines.md"
PROFILE_VIEW = ROOT / "references/document-standards/document-profile.md"
SOURCE_DIGESTS = {
    "skill-guidelines.md": "1a18981c0c8618b0ffdbd57bd54d272d9cfa9cccdfd063367f68f5b6c14be3a5",
    "document-profile-schema.md": "178bbd7c1fc076208fe576f871ab7bba936941fce83fdb7a46f784b1cb28d967",
}

def load_module(name, relative_path):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

validator = load_module("migrated_validator", "references/standards/validate_standard_v6.py")
renderer = load_module("migrated_renderer", "references/standards/render_standard_v6.py")

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
    for standard, view in ((GUIDELINES, GUIDELINES_VIEW), (PROFILE, PROFILE_VIEW)):
        assert validator.validate_file(standard, ROOT) == []
        document = yaml.safe_load(standard.read_text()); rendered = renderer.render_document(document)
        assert rendered == renderer.render_document(document) == view.read_text()

def test_render_mode_is_schema_checked_and_defaults_to_semantic():
    guidelines = yaml.safe_load(GUIDELINES.read_text())
    assert guidelines["render_mode"] == "source-faithful"
    invalid = dict(guidelines); invalid["render_mode"] = "id-convention"
    assert any("render_mode" in error or "not one of" in error for error in validator.validate_document(invalid, ROOT))
    profile = yaml.safe_load(PROFILE.read_text())
    assert "render_mode" not in profile
    assert renderer.render_document(profile) == PROFILE_VIEW.read_text()

def test_every_source_block_is_exactly_preserved_once():
    for fixture_name, map_name, standard in (
        ("skill-guidelines.md", "skill-guidelines-source-map.yaml", GUIDELINES),
        ("document-profile-schema.md", "document-profile-source-map.yaml", PROFILE),
    ):
        fixture = FIXTURES / fixture_name; lines = fixture.read_text().splitlines(); mapping = yaml.safe_load((FIXTURES / map_name).read_text())
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == SOURCE_DIGESTS[fixture_name] == mapping["source_sha256"]
        indexed = nodes(yaml.safe_load(standard.read_text())); covered = []
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
    document = yaml.safe_load(GUIDELINES.read_text()); titles = {family["title"] for family in document["standards"]}
    assert document["render_mode"] == "source-faithful"
    source = (FIXTURES / "skill-guidelines.md").read_text()
    markdown_titles = {m.group(1) for m in re.finditer(r"^#{1,6}\s+(.+)$", source, re.M)}
    markdown_titles.discard("Skill Module Standards")
    bold_titles = {m.group(1) for m in re.finditer(r"^\*\*(.+?)\*\*", source, re.M)}
    assert markdown_titles | bold_titles <= titles
    rendered = GUIDELINES_VIEW.read_text()
    for phrase in ("A skill is a software module", "Blueprint authoring", "Dispatcher role", "Validator and test conventions"):
        assert phrase in rendered
    assert not re.search(r"^#{2,6} (?:Requirement|Guidance|Example|Procedure) [0-9]{3}$", rendered, re.M)
    assert rendered.count("# Skill Module Standards") == 1
    ordered = ["## A skill is a software module.", "## 1. Skill identity and contract come first", "## Blueprint authoring", "## Machine interfaces", "## Validator and test conventions"]
    assert [rendered.index(text) for text in ordered] == sorted(rendered.index(text) for text in ordered)
    source_size = len((FIXTURES / "skill-guidelines.md").read_text())
    assert len(rendered) < source_size * 1.35
    assert "- `category`\n\n- `role`\n\n- `kind`" in rendered
    assert "```python\nfrom officina.runtime.python_machine_interface" in rendered and "class Interface(PythonMachineInterface):" in rendered
    assert "1. Create `validators/<name>.py`" in rendered and "4. Add a `tests/validate_<name>.py`" in rendered

def test_every_fenced_source_block_is_one_verbatim_unit_and_contiguous_in_view():
    source = (FIXTURES / "skill-guidelines.md").read_text()
    fenced = re.findall(r"^```[^\n]*\n.*?^```$", source, re.M | re.S)
    assert fenced
    rendered = GUIDELINES_VIEW.read_text()
    mapping = yaml.safe_load((FIXTURES / "skill-guidelines-source-map.yaml").read_text())["units"]
    semantic_text = {unit["text"] for unit in mapping if unit["kind"] == "semantic"}
    for block in fenced:
        assert block in semantic_text
        assert block in rendered
    tags = {block.splitlines()[0] for block in fenced}
    assert {"```python", "```bash", "```yaml", "```text"} <= tags
    assert any("regex" in tag for tag in tags)

def test_guideline_block_ids_are_frozen_family_local_ids():
    frozen = yaml.safe_load((FIXTURES / "skill-guidelines-frozen-ids.yaml").read_text())
    mapping = yaml.safe_load((FIXTURES / "skill-guidelines-source-map.yaml").read_text())["units"]
    semantic = [unit for unit in mapping if unit["kind"] == "semantic"]
    assert len(frozen["blocks"]) == len(semantic)
    assert len(set(frozen["blocks"].values())) == len(semantic)
    pattern = re.compile(r"^skill-guidelines\.[a-z0-9-]+\.(?:requirement|guidance|example|procedure)-[0-9]{3}$")
    assert all(pattern.fullmatch(value) for value in frozen["blocks"].values())
    for unit in semantic:
        assert unit["target"]["target_ref"].split("#", 1)[0] == frozen["blocks"][f'{unit["start"]}-{unit["end"]}']

def test_enforcement_inventory_is_exhaustive_and_typed():
    source = (FIXTURES / "skill-guidelines.md").read_text()
    extracted = {p.rstrip(".,;:") for p in re.findall(r"(?:\.githooks|references|validators|tests|skills|src)/[A-Za-z0-9_.*<>/{},-]+(?:\.[A-Za-z0-9_*<>/{},-]+)?", source)}
    inventory = yaml.safe_load((FIXTURES / "skill-guidelines-enforcement-ledger.yaml").read_text())["references"]
    assert {entry["path"] for entry in inventory} == extracted
    kinds = {"validation-entrypoint", "validator-or-test", "schema-authority", "implementation-reference", "documentation-reference", "generated-artifact", "directory-or-package"}
    assert all(entry["kind"] in kinds for entry in inventory)
    hook = next(entry for entry in inventory if entry["path"] == ".githooks/pre-commit")
    assert hook["kind"] == "validation-entrypoint" and hook["integration_point"] == "precommit"

def test_document_profile_scope_fields_template_and_normalization():
    rendered = PROFILE_VIEW.read_text()
    assert "canonical schema for top-of-document TeX profile comments" in rendered
    for field in ("Document type", "Field/subfield", "Purpose", "Audience", "Assumed background", "Target level of rigor/detail", "Expected document length", "Relationship to main paper or companion documents"):
        assert field in rendered and f"% {field}:" in rendered
    for phrase in ("may be left unspecified", "may be left blank when irrelevant", "Infer fields only", "Reader familiarity", "journal-article", "conference-article", "research-presentation", "research-notes"):
        assert phrase in rendered
