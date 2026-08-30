from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import jsonschema
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "validators" / "standard_documents.py"
STANDARD = "references/node-standards/refactoring.standard.yaml"
GENERATED_STANDARD = "references/document-standards/document-profile.standard.yaml"
TOOLING = (
    "standard-v6.schema.json",
    "validate_standard_v6.py",
    "render_standard_v6.py",
)


def _load_validator():
    spec = importlib.util.spec_from_file_location("standard_documents", VALIDATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validator_module():
    return _load_validator()


def _copy_standard_repo(
    tmp_path: Path,
    *,
    standards: tuple[str, ...] = (STANDARD,),
) -> Path:
    for relative in standards:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
        rendered_relative = Path(relative.removesuffix(".standard.yaml") + ".md")
        rendered = tmp_path / rendered_relative
        source_rendered = ROOT / rendered_relative
        if source_rendered.is_file():
            rendered.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_rendered, rendered)
    tooling = tmp_path / "references" / "standards-schema"
    tooling.mkdir(parents=True, exist_ok=True)
    for name in TOOLING:
        shutil.copy2(ROOT / "references" / "standards-schema" / name, tooling / name)
    return tmp_path


def test_repository_validation_prepares_the_v6_schema_once(
    tmp_path, monkeypatch, validator_module
):
    repo = _copy_standard_repo(
        tmp_path,
        standards=(STANDARD, GENERATED_STANDARD),
    )
    original_validator_for = jsonschema.validators.validator_for
    original_load_tool = validator_module._load_tool
    preparation_count = 0
    top_level_caches = []

    def counting_validator_for(schema, *args, **kwargs):
        nonlocal preparation_count
        if isinstance(schema, dict) and str(schema.get("$id", "")).endswith(
            "/standard-v6.schema.json"
        ):
            preparation_count += 1
        return original_validator_for(schema, *args, **kwargs)

    def load_tool_with_observed_cache(repo_root, module_name):
        module = original_load_tool(repo_root, module_name)
        if module_name != "validate_standard_v6":
            return module
        original_validate_file = module.validate_file

        def validate_file(
            path,
            root=None,
            cache=None,
            _stack=None,
            _schema_validator=None,
        ):
            if _stack is None:
                top_level_caches.append(cache)
            return original_validate_file(
                path,
                root,
                cache,
                _stack,
                _schema_validator,
            )

        module.validate_file = validate_file
        return module

    monkeypatch.setattr(jsonschema.validators, "validator_for", counting_validator_for)
    monkeypatch.setattr(validator_module, "_load_tool", load_tool_with_observed_cache)

    assert validator_module.validate(repo) == []
    assert preparation_count == 1
    assert len(top_level_caches) == 2
    assert all(cache is not None for cache in top_level_caches)
    assert len({id(cache) for cache in top_level_caches}) == 2


def test_repository_validation_renders_only_registered_generated_views(
    tmp_path, monkeypatch, validator_module
):
    repo = _copy_standard_repo(
        tmp_path,
        standards=(STANDARD, GENERATED_STANDARD),
    )
    original_load_tool = validator_module._load_tool
    rendered_standards = []

    def load_tool_with_observed_rendering(repo_root, module_name):
        module = original_load_tool(repo_root, module_name)
        if module_name != "render_standard_v6":
            return module
        original_render_document = module.render_document

        def render_document(document):
            rendered_standards.append(document["canonical_path"])
            return original_render_document(document)

        module.render_document = render_document
        return module

    monkeypatch.setattr(
        validator_module,
        "_load_tool",
        load_tool_with_observed_rendering,
    )

    assert validator_module.validate(repo) == []
    assert rendered_standards == [GENERATED_STANDARD]


def test_accepts_utf8_standards_and_crlf_views_under_windows_default_encoding(
    tmp_path, monkeypatch, validator_module
):
    repo = _copy_standard_repo(tmp_path, standards=(GENERATED_STANDARD,))
    view = repo / Path(GENERATED_STANDARD.removesuffix(".standard.yaml") + ".md")
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

    assert validator_module.validate(repo) == []


def test_reports_distinct_document_validation_failures(tmp_path, validator_module):
    repo = _copy_standard_repo(tmp_path)
    source = repo / STANDARD
    source_document = yaml.safe_load(source.read_text(encoding="utf-8"))

    semantic_relative = Path(
        "references/node-standards/semantic-invalid.standard.yaml"
    )
    semantic_document = source_document.copy()
    semantic_document["id"] = "node-standards.semantic-invalid"
    semantic_document["canonical_path"] = semantic_relative.as_posix()
    semantic_document["links"] = {
        name: link.copy() for name, link in source_document["links"].items()
    }
    remedy = next(
        link
        for link in semantic_document["links"].values()
        if link["relation"] == "remedied-by"
    )
    remedy["source"] = remedy["source"].copy()
    remedy["source"]["kind"] = "procedure"
    semantic_path = repo / semantic_relative
    semantic_path.write_text(
        yaml.safe_dump(semantic_document, sort_keys=False), encoding="utf-8"
    )

    schema_relative = Path("references/node-standards/schema-invalid.standard.yaml")
    schema_document = source_document.copy()
    schema_document["id"] = "node-standards.schema-invalid"
    schema_document["canonical_path"] = schema_relative.as_posix()
    del schema_document["title"]
    schema_path = repo / schema_relative
    schema_path.write_text(
        yaml.safe_dump(schema_document, sort_keys=False), encoding="utf-8"
    )

    malformed_relative = Path("references/node-standards/malformed.standard.yaml")
    malformed_path = repo / malformed_relative
    malformed_path.write_text("standards: [\n", encoding="utf-8")

    errors = validator_module.validate(repo)

    assert any(
        error.startswith(f"{semantic_relative.as_posix()}:")
        and "remedied-by source must be a family" in error
        for error in errors
    )
    assert any(
        f"{schema_relative.as_posix()}: schema validation failed" in error
        for error in errors
    )
    assert any(
        f"{malformed_relative.as_posix()}: cannot load document" in error
        for error in errors
    )


def test_rejects_stale_generated_markdown(tmp_path, validator_module):
    repo = _copy_standard_repo(tmp_path, standards=(GENERATED_STANDARD,))
    view = repo / "references/document-standards/document-profile.md"
    view.write_text(
        view.read_text(encoding="utf-8") + "\nstale edit\n",
        encoding="utf-8",
    )

    errors = validator_module.validate(repo)

    assert errors == [
        "references/document-standards/document-profile.md: generated view is stale; "
        "render references/document-standards/document-profile.standard.yaml"
    ]


def test_discovers_and_validates_additional_v6_standards(
    tmp_path, monkeypatch, validator_module
):
    repo = _copy_standard_repo(tmp_path)
    source = repo / STANDARD
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    valid_relative = Path("references/node-standards/node.standard.yaml")
    document["id"] = "node-standards.node"
    document["canonical_path"] = valid_relative.as_posix()
    document["title"] = "Node Standard"
    document["purpose"] = "Define requirements common to repository nodes."
    valid_path = repo / valid_relative
    valid_path.parent.mkdir(parents=True, exist_ok=True)
    valid_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    invalid_relative = Path("references/node-standards/node-invalid.standard.yaml")
    invalid_document = document.copy()
    invalid_document["id"] = "node-standards.node-invalid"
    invalid_document["canonical_path"] = invalid_relative.as_posix()
    del invalid_document["title"]
    invalid_path = repo / invalid_relative
    invalid_path.write_text(
        yaml.safe_dump(invalid_document, sort_keys=False), encoding="utf-8"
    )

    original_load_tool = validator_module._load_tool
    validated_paths = []

    def load_tool_with_observed_paths(repo_root, module_name):
        module = original_load_tool(repo_root, module_name)
        if module_name != "validate_standard_v6":
            return module
        original_validate_file = module.validate_file

        def validate_file(path, *args, **kwargs):
            validated_paths.append(path.relative_to(repo))
            return original_validate_file(path, *args, **kwargs)

        module.validate_file = validate_file
        return module

    monkeypatch.setattr(validator_module, "_load_tool", load_tool_with_observed_paths)

    errors = validator_module.validate(repo)

    assert valid_relative in validated_paths
    assert any(
        f"{invalid_relative.as_posix()}: schema validation failed" in error
        for error in errors
    )
    assert all(
        error.startswith(f"{invalid_relative.as_posix()}:") for error in errors
    )


def test_rejects_canonical_path_mismatch_at_allowlisted_location(
    tmp_path, validator_module
):
    standards = (STANDARD, GENERATED_STANDARD)
    repo = _copy_standard_repo(tmp_path, standards=standards)
    first = repo / standards[0]
    second = repo / standards[1]
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

    errors = validator_module.validate(repo)

    assert any(
        f"{standards[0]}: canonical_path must equal {standards[0]}" in error
        for error in errors
    )
    assert any(
        f"{standards[1]}: canonical_path must equal {standards[1]}" in error
        for error in errors
    )


def test_fails_closed_when_standards_directory_is_missing(tmp_path, validator_module):
    errors = validator_module.validate(tmp_path)

    assert errors == ["references/standards-schema: missing standards tooling directory"]


def test_fails_closed_when_schema_or_tool_is_missing(tmp_path, validator_module):
    repo = _copy_standard_repo(tmp_path, standards=())
    (repo / "references/standards-schema/standard-v6.schema.json").unlink()
    (repo / "references/standards-schema/render_standard_v6.py").unlink()

    errors = validator_module.validate(repo)

    assert "references/standards-schema/standard-v6.schema.json: missing standards tooling artifact" in errors
    assert "references/standards-schema/render_standard_v6.py: missing standards tooling artifact" in errors


def test_fails_closed_when_generated_view_is_missing(tmp_path, validator_module):
    repo = _copy_standard_repo(tmp_path, standards=(GENERATED_STANDARD,))
    view = repo / "references/document-standards/document-profile.md"
    view.unlink()

    errors = validator_module.validate(repo)

    assert errors == [
        f"{view.relative_to(repo).as_posix()}: missing generated view"
    ]
