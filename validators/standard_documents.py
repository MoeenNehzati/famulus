"""Validate the repository's canonical standards and generated Markdown views."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


GENERATED_VIEW_STANDARDS = {
    Path("references/document-standards/document-profile.standard.yaml"),
}
TOOLING_ARTIFACTS = (
    Path("references/standards-schema/standard-v6.schema.json"),
    Path("references/standards-schema/validate_standard_v6.py"),
    Path("references/standards-schema/render_standard_v6.py"),
)
NON_STANDARD_V6_PATHS = {
    Path("references/standards-schema/docstring.standard.yaml"),
}


def _display(path: Path) -> str:
    """Render one repository-relative path in POSIX form.

    Intent
    ------
    Convert Path values into stable slash-separated validator messages.

    Rationale
    ---------
    Stable paths keep diagnostics identical across supported operating systems.

    Pseudocode
    ----------
    - set display_path = POSIX representation of path
    - return display path

    Wraps
    -----
    - none
    """
    return path.as_posix()


def _load_tool(repo_root: Path, module_name: str):
    """Load one standards tool from the validated repository.

    Intent
    ------
    Create and execute an import specification for the repository-local tool module.

    Rationale
    ---------
    Loading from the supplied repository root keeps validator behavior aligned with the files being checked.

    Pseudocode
    ----------
    - set tool_path = repository standards tool path
    - set module_specification = specification for tool path
    - if loader is missing:
      - raise import error
    - return executed tool module

    Wraps
    -----
    - none
    """
    path = repo_root / "references" / "standards-schema" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"_standard_documents_{module_name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load standards tool {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(repo_root: Path) -> list[str]:
    """Validate canonical standards and generated Markdown views.

    Intent
    ------
    Discover standard-v6 files, reuse one prepared schema validator, and preserve existing rendering and diagnostic order.

    Rationale
    ---------
    One repository-scan boundary is the narrowest safe lifetime for immutable schema preparation.

    Pseudocode
    ----------
    - set discovered_standards = sorted repository standard files
    - set schema_validator = one prepared standard-v6 validator
    - for standard in discovered standards:
      - set findings = file validation and generated-view checks
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._display:
      why:
        computes: "Formats repository-relative paths used in stable validator findings."

    InstantiationsFromRepo
    ----------------------
    ._load_tool:
      why:
        constructs: "Builds the repository-local validator and renderer modules used by the scan."
    """
    repo_root = Path(repo_root)
    tooling_root = repo_root / "references" / "standards-schema"
    if not tooling_root.is_dir():
        return ["references/standards-schema: missing standards tooling directory"]
    missing_tooling = [
        f"{_display(path)}: missing standards tooling artifact"
        for path in TOOLING_ARTIFACTS
        if not (repo_root / path).is_file()
    ]
    if missing_tooling:
        return missing_tooling
    discovered = {
        path.relative_to(repo_root)
        for path in (repo_root / "references").rglob("*.standard.yaml")
    }
    errors = []

    try:
        validator = _load_tool(repo_root, "validate_standard_v6")
        renderer = _load_tool(repo_root, "render_standard_v6")
    except (ImportError, OSError) as exc:
        return errors + [f"references/standards-schema: cannot load standards tooling: {exc}"]

    schema_validator = validator._prepare_schema_validator()
    for relative in sorted(discovered):
        path = repo_root / relative
        if relative in NON_STANDARD_V6_PATHS:
            continue
        cache = {}
        errors.extend(
            f"{_display(relative)}: {error}"
            for error in validator.validate_file(
                path,
                root=repo_root,
                cache=cache,
                _schema_validator=schema_validator,
            )
        )
        try:
            cached = cache.get(path.resolve())
            document = (
                cached[0]
                if cached is not None
                else yaml.safe_load(path.read_text(encoding="utf-8"))
            )
            if document.get("canonical_path") != relative.as_posix():
                errors.append(
                    f"{_display(relative)}: canonical_path must equal "
                    f"{_display(relative)}; "
                    f"found {document.get('canonical_path')!r}"
                )
        except Exception as exc:
            errors.append(f"{_display(relative)}: cannot render standard: {exc}")
            continue
        if relative not in GENERATED_VIEW_STANDARDS:
            continue
        try:
            rendered = renderer.render_document(document)
        except Exception as exc:
            errors.append(f"{_display(relative)}: cannot render standard: {exc}")
            continue
        view_relative = Path(str(relative).removesuffix(".standard.yaml") + ".md")
        view_path = repo_root / view_relative
        if not view_path.is_file():
            errors.append(f"{_display(view_relative)}: missing generated view")
        elif view_path.read_text(encoding="utf-8") != rendered:
            errors.append(
                f"{_display(view_relative)}: generated view is stale; "
                f"render {_display(relative)}"
            )
    return errors
