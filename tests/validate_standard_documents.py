from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "validators" / "standard_documents.py"
CANONICAL = (
    "references/node-standards/refactoring.standard.yaml",
    "references/document-standards/document-profile.standard.yaml",
    "references/standards/docstring.standard.yaml",
)


def _load_validator():
    spec = importlib.util.spec_from_file_location("standard_documents", VALIDATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy_standard_repo(tmp_path: Path) -> Path:
    for relative in CANONICAL:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
        rendered_relative = Path(relative.removesuffix(".standard.yaml") + ".md")
        rendered = tmp_path / rendered_relative
        source_rendered = ROOT / rendered_relative
        if source_rendered.is_file():
            rendered.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_rendered, rendered)
    tooling = tmp_path / "references" / "standards"
    tooling.mkdir(parents=True, exist_ok=True)
    for name in ("standard-v6.schema.json", "validate_standard_v6.py", "render_standard_v6.py"):
        shutil.copy2(ROOT / "references" / "standards" / name, tooling / name)
    return tmp_path


def test_repository_canonical_standards_are_valid_and_fresh():
    validator = _load_validator()
    assert validator.validate(ROOT) == []


def test_repository_validation_prepares_the_v6_schema_once(tmp_path, monkeypatch):
    repo = _copy_standard_repo(tmp_path)
    original_validator_for = jsonschema.validators.validator_for
    preparation_count = 0

    def counting_validator_for(schema, *args, **kwargs):
        nonlocal preparation_count
        if isinstance(schema, dict) and str(schema.get("$id", "")).endswith(
            "/standard-v6.schema.json"
        ):
            preparation_count += 1
        return original_validator_for(schema, *args, **kwargs)

    monkeypatch.setattr(jsonschema.validators, "validator_for", counting_validator_for)

    assert _load_validator().validate(repo) == []
    assert preparation_count == 1


def test_accepts_utf8_standards_and_crlf_views_under_windows_default_encoding(
    tmp_path, monkeypatch
):
    repo = _copy_standard_repo(tmp_path)
    for relative in CANONICAL:
        view = repo / Path(relative.removesuffix(".standard.yaml") + ".md")
        if not view.is_file():
            continue
        text = view.read_text(encoding="utf-8")
        view.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

    original_read_text = Path.read_text

    def read_text_with_windows_default(path, encoding=None, errors=None):
        return original_read_text(
            path,
            encoding=encoding or "cp1252",
            errors=errors,
        )

    monkeypatch.setattr(Path, "read_text", read_text_with_windows_default)

    assert _load_validator().validate(repo) == []


def test_rejects_schema_or_semantically_invalid_standard(tmp_path):
    repo = _copy_standard_repo(tmp_path)
    path = repo / CANONICAL[0]
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    remedy = next(link for link in document["links"].values() if link["relation"] == "remedied-by")
    remedy["source"]["kind"] = "procedure"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    errors = _load_validator().validate(repo)

    assert any("remedied-by source must be a family" in error for error in errors)


def test_rejects_json_schema_invalid_standard(tmp_path):
    repo = _copy_standard_repo(tmp_path)
    path = repo / CANONICAL[0]
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    del document["title"]
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    errors = _load_validator().validate(repo)

    assert any(f"{CANONICAL[0]}: schema validation failed" in error for error in errors)


def test_reports_malformed_yaml_without_crashing(tmp_path):
    repo = _copy_standard_repo(tmp_path)
    path = repo / CANONICAL[1]
    path.write_text("standards: [\n", encoding="utf-8")

    errors = _load_validator().validate(repo)

    assert any(f"{CANONICAL[1]}: cannot load document" in error for error in errors)


def test_rejects_stale_generated_markdown(tmp_path):
    repo = _copy_standard_repo(tmp_path)
    view = repo / "references/document-standards/document-profile.md"
    view.write_text(
        view.read_text(encoding="utf-8") + "\nstale edit\n",
        encoding="utf-8",
    )

    errors = _load_validator().validate(repo)

    assert errors == [
        "references/document-standards/document-profile.md: generated view is stale; "
        "render references/document-standards/document-profile.standard.yaml"
    ]


def test_discovers_additional_v6_standard_without_requiring_generated_view(tmp_path):
    repo = _copy_standard_repo(tmp_path)
    source = repo / CANONICAL[0]
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    relative = Path("references/node-standards/node.standard.yaml")
    document["id"] = "node-standards.node"
    document["canonical_path"] = relative.as_posix()
    document["title"] = "Node Standard"
    document["purpose"] = "Define requirements common to repository nodes."
    extra = repo / relative
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    errors = _load_validator().validate(repo)

    assert errors == []


def test_validates_discovered_v6_standard(tmp_path):
    repo = _copy_standard_repo(tmp_path)
    source = repo / CANONICAL[0]
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    relative = Path("references/node-standards/node.standard.yaml")
    document["id"] = "node-standards.node"
    document["canonical_path"] = relative.as_posix()
    del document["title"]
    extra = repo / relative
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    errors = _load_validator().validate(repo)

    assert any(
        f"{relative.as_posix()}: schema validation failed" in error
        for error in errors
    )


def test_rejects_canonical_path_mismatch_at_allowlisted_location(tmp_path):
    repo = _copy_standard_repo(tmp_path)
    first = repo / CANONICAL[0]
    second = repo / CANONICAL[1]
    first_document = yaml.safe_load(first.read_text(encoding="utf-8"))
    second_document = yaml.safe_load(second.read_text(encoding="utf-8"))
    first_document["canonical_path"], second_document["canonical_path"] = (
        second_document["canonical_path"],
        first_document["canonical_path"],
    )
    first.write_text(
        yaml.safe_dump(first_document, sort_keys=False),
        encoding="utf-8",
    )
    second.write_text(
        yaml.safe_dump(second_document, sort_keys=False),
        encoding="utf-8",
    )

    errors = _load_validator().validate(repo)

    assert any(
        f"{CANONICAL[0]}: canonical_path must equal {CANONICAL[0]}" in error
        for error in errors
    )
    assert any(
        f"{CANONICAL[1]}: canonical_path must equal {CANONICAL[1]}" in error
        for error in errors
    )


def test_fails_closed_when_standards_directory_is_missing(tmp_path):
    errors = _load_validator().validate(tmp_path)

    assert errors == ["references/standards: missing standards tooling directory"]


def test_fails_closed_when_schema_or_tool_is_missing(tmp_path):
    repo = _copy_standard_repo(tmp_path)
    (repo / "references/standards/standard-v6.schema.json").unlink()
    (repo / "references/standards/render_standard_v6.py").unlink()

    errors = _load_validator().validate(repo)

    assert "references/standards/standard-v6.schema.json: missing standards tooling artifact" in errors
    assert "references/standards/render_standard_v6.py: missing standards tooling artifact" in errors


def test_fails_closed_when_generated_view_is_missing(tmp_path):
    repo = _copy_standard_repo(tmp_path)
    view = repo / "references/document-standards/document-profile.md"
    view.unlink()

    errors = _load_validator().validate(repo)

    assert errors == [
        f"{view.relative_to(repo).as_posix()}: missing generated view"
    ]
