"""Validate the repository's canonical standards and generated Markdown views."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import jsonschema
import yaml


GENERATED_VIEW_STANDARDS = {
    Path("references/document-standards/document-profile.standard.yaml"),
}
TOOLING_ARTIFACTS = (
    Path("references/standards/standard-v6.schema.json"),
    Path("references/standards/validate_standard_v6.py"),
    Path("references/standards/render_standard_v6.py"),
    Path("references/standards/docstring_format.schema.json"),
)
NON_STANDARD_V6_PATHS = {
    Path("references/standards/docstring.standard.yaml"),
}
DOCSTRING_STANDARD_PATH = Path("references/standards/docstring.standard.yaml")
DOCSTRING_SCHEMA_PATH = Path("references/standards/docstring_format.schema.json")


def _display(path: Path) -> str:
    """Render a repository path in stable POSIX form.

    Intent
    ------
    Keep standards diagnostics portable across host path conventions.

    Rationale
    ---------
    Validation output is compared in tests and read across platforms, so separators
    must not depend on the machine running the gate.

    Pseudocode
    ----------
    - return path in POSIX form

    Wraps
    -----
    - none
    """
    return path.as_posix()


def _load_tool(repo_root: Path, module_name: str):
    """Load one standards helper module from the repository under validation.

    Intent
    ------
    Execute validation and rendering with the candidate repository's own tooling.

    Rationale
    ---------
    Loading by explicit path prevents an installed or caller-repository module from
    silently validating a different standards contract.

    Pseudocode
    ----------
    - set tool_path = repository standards tool path
    - if tool cannot be loaded:
      - raise ImportError
    - return loaded tool module

    Wraps
    -----
    - none
    """
    path = repo_root / "references" / "standards" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"_standard_documents_{module_name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load standards tool {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(repo_root: Path) -> list[str]:
    """Validate canonical standards, companion schemas, and registered views.

    Intent
    ------
    Return every repository standards finding through one deterministic validator gate.

    Rationale
    ---------
    Canonical policy is trustworthy only when required tooling exists, each authority
    satisfies its schema, and every registered generated view matches its source.

    Pseudocode
    ----------
    - set repo_root = normalized repository root
    - if standards tooling is missing:
      - return tooling findings
    - tools = _load_tool(repo_root)
    - for standard in discovered standards:
      - set errors = errors plus schema and view findings
    - return errors

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._display:
      why:
        computes: "Formats repository-relative artifact paths for stable diagnostics."

    InstantiationsFromRepo
    ----------------------
    ._load_tool:
      why:
        constructs: "Loads the candidate repository's validator and renderer modules."
    """
    repo_root = Path(repo_root)
    tooling_root = repo_root / "references" / "standards"
    if not tooling_root.is_dir():
        return ["references/standards: missing standards tooling directory"]
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
        return errors + [f"references/standards: cannot load standards tooling: {exc}"]

    for relative in sorted(discovered):
        path = repo_root / relative
        if relative == DOCSTRING_STANDARD_PATH:
            try:
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
                schema = yaml.safe_load(
                    (repo_root / DOCSTRING_SCHEMA_PATH).read_text(encoding="utf-8")
                )
                jsonschema.Draft7Validator(schema).validate(document)
            except (OSError, yaml.YAMLError, jsonschema.SchemaError) as exc:
                errors.append(f"{_display(relative)}: cannot load document or schema: {exc}")
            except jsonschema.ValidationError as exc:
                errors.append(
                    f"{_display(relative)}: schema validation failed: {exc.message}"
                )
            continue
        if relative in NON_STANDARD_V6_PATHS:
            continue
        errors.extend(
            f"{_display(relative)}: {error}"
            for error in validator.validate_file(path, root=repo_root)
        )
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            if document.get("canonical_path") != relative.as_posix():
                errors.append(
                    f"{_display(relative)}: canonical_path must equal "
                    f"{_display(relative)}; "
                    f"found {document.get('canonical_path')!r}"
                )
            rendered = renderer.render_document(document)
        except Exception as exc:
            errors.append(f"{_display(relative)}: cannot render standard: {exc}")
            continue
        if relative not in GENERATED_VIEW_STANDARDS:
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
