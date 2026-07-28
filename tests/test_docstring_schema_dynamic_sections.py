from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.common.docstring.docstring_parser import check_graph_docstring, parse_graph_block  # noqa: E402


_CUSTOM_DOCSTRING_STANDARD = """\
name: docstring_format
docstring_format_version: 7
strict: true

callable:
  required_summary: true
  required_sections:
    - Graph
    - Role
    - Contracts
  optional_sections:
    - Phase
    - NonInferableCalls
    - Wraps
    - Owns
    - Contracts
    - CallsFromModule
    - InstantiationsFromModule
  module_dependencies:
    calls_section: CallsFromModule
    instantiates_section: InstantiationsFromModule
  wraps:
    syntax: "<target> -> preprocess: <text>; postprocess: <text>; fixed_arguments: <text>"
    fields:
      - target
      - preprocess
      - postprocess
      - fixed_arguments
  ownership:
    section: Owns
    section_required: false
    allows_multiple: false
    single_owner_per_callable: true
    owner_resolution: module:Ownable
    owner_reference_delimiter: ":"
    owner_reference_syntax: "<owner_id> or <module_path>:<owner_id>"
    cross_file:
      enabled: true
      delimiter: ":"
  pseudocode:
    section: Pseudocode

pipeline:
  required: false
  required_sections:
    - Phases
    - PhaseMembers
  optional_sections:
    - PhaseEdges
    - NonInferableCalls
    - Description

module:
  required: false
  required_summary: true
  required_sections:
    - Includes
  optional_sections:
    - Workflow
    - Traces
    - Description
    - Ownable
  ownership_registry:
    section: Ownable
    syntax: "<owner_id>: <brief responsibility>"
    allows_multiple: true
"""


def _install_custom_format(tmp_path: Path, monkeypatch) -> None:
    schema_path = tmp_path / "docstring.standard.yaml"
    schema_path.write_text(_CUSTOM_DOCSTRING_STANDARD, encoding="utf-8")
    monkeypatch.setattr(
        "officina.common.docstring.docstring_schema.resolve_docstring_schema_path",
        lambda _path=None: schema_path,
    )


def test_dynamic_section_is_parsed_and_validated_from_schema(tmp_path: Path, monkeypatch) -> None:
    """New callable sections are parsed and enforced from schema alone."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Compute a node-certifier result.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

    Contracts
    ---------
    - preconditions: non-empty input fields
    - postconditions: stable digest emitted
    """
    spec = parse_graph_block(doc)
    assert spec.sections["Contracts"][0].strip() == "- preconditions: non-empty input fields"
    assert spec.sections["Role"][0].strip().startswith("Owns hash")

    issues = check_graph_docstring(doc)
    assert not any(issue.code == "docstring.section-missing" for issue in issues)


def test_pseudocode_section_is_parsed_and_classified_from_schema(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Pseudocode lines are parsed with control-flow step kinds derived from schema."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Build a node hash state.

    Graph
    -----
    Coordinate hash computation and normalization.

    Role
    ----
    Owns hash pipeline and fallback behavior.

    Pseudocode
    ---------
    if input is missing:
    for each item in payload:
      if item is invalid:
        raise ValueError
      else:
        keep item
    return ordered payload
    """
    spec = parse_graph_block(doc)
    kinds = [step.kind for step in spec.pseudocode_steps]
    assert kinds == ["if", "for_each", "if", "step", "else", "step", "step"]


def test_pseudocode_control_line_validation(tmp_path: Path, monkeypatch) -> None:
    """Control-like pseudocode lines require suffix details when policy enables it."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Build a node hash state.

    Graph
    -----
    Coordinate hash computation and normalization.

    Role
    ----
    Owns hash pipeline and fallback behavior.

    Pseudocode
    ---------
    if
    for each:
    return ordered payload
    """
    issues = check_graph_docstring(doc)
    assert any(
        issue.code in {"docstring.pseudocode-colon", "docstring.invalid-pseudocode"}
        for issue in issues
    )


def test_schema_can_require_a_custom_section(tmp_path: Path, monkeypatch) -> None:
    """A custom required section appears as a validation issue when missing."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Compute a node-certifier result.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.
    """
    issues = check_graph_docstring(doc)
    assert any(
        issue.code == "docstring.section-missing" and issue.section == "Contracts"
        for issue in issues
    )


def test_module_dependency_sections_are_parsed_and_validated_from_schema(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Callable-level dependency sections are captured and validated from schema."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Compute a node-certifier result.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

    CallsFromModule
    --------------
    validator.parse -> parse edge source
    hashlib -> hash helper
    NodeHashState() -> instantiate node hash wrapper

    InstantiationsFromModule
    ------------------------
    NodeHashState() -> instantiate state for this step
    """
    spec = parse_graph_block(doc)
    assert [entry.name for entry in spec.module_calls] == ["validator.parse", "hashlib", "NodeHashState"]
    assert [entry.implicit for entry in spec.module_calls] == [False, False, False]
    assert [entry.name for entry in spec.module_instantiates] == ["NodeHashState"]
    assert [entry.implicit for entry in spec.module_instantiates] == [False]

    issues = check_graph_docstring(doc)
    assert not any(issue.code == "docstring.invalid-module-dependency" for issue in issues)


def test_module_dependency_sections_validate_bad_reference_syntax(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Invalid module dependency entries are reported via validation warnings."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Compute a node-certifier result.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

    CallsFromModule
    --------------
    validator.parse
    invalid-name()
    """
    issues = check_graph_docstring(doc)
    assert any(
        issue.code == "docstring.invalid-module-dependency"
        and issue.section == "CallsFromModule"
        for issue in issues
    )


def test_module_dependency_implicit_entries_are_supported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Implicit module dependency entries parse with explicit implicit flags."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Compute a node-certifier result.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

    CallsFromModule
    --------------
    validator.parse [implicit] -> optional pre-validation step
    hashlib -> direct hashing call

    InstantiationsFromModule
    ------------------------
    NodeHashState() [implicit] -> helper init for internal state
    """
    spec = parse_graph_block(doc)
    assert [entry.name for entry in spec.module_calls] == ["validator.parse", "hashlib"]
    assert [entry.implicit for entry in spec.module_calls] == [True, False]
    assert [entry.name for entry in spec.module_instantiates] == ["NodeHashState"]
    assert [entry.implicit for entry in spec.module_instantiates] == [True]

    issues = check_graph_docstring(doc)
    assert not any(issue.code == "docstring.invalid-module-dependency" for issue in issues)


def test_module_dependency_implicit_can_be_disabled_in_schema(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Implicit markers are rejected when schema disables them."""
    strict_format = _CUSTOM_DOCSTRING_STANDARD.replace(
        "  module_dependencies:\n    calls_section: CallsFromModule\n    instantiates_section: InstantiationsFromModule\n",
        "  module_dependencies:\n    calls_section: CallsFromModule\n    instantiates_section: InstantiationsFromModule\n    allow_implicit: false\n",
    )
    schema_path = tmp_path / "docstring.standard.yaml"
    schema_path.write_text(strict_format, encoding="utf-8")
    monkeypatch.setattr(
        "officina.common.docstring.docstring_schema.resolve_docstring_schema_path",
        lambda _path=None: schema_path,
    )

    doc = """
    Compute a node-certifier result.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

        CallsFromModule
        --------------
        validator.parse [implicit] -> optional pre-validation step
        hashlib -> direct hash call
    """
    spec = parse_graph_block(doc)
    assert [entry.name for entry in spec.module_calls] == ["hashlib"]
    assert [entry.implicit for entry in spec.module_calls] == [False]

    issues = check_graph_docstring(doc)
    assert any(
        issue.code == "docstring.invalid-module-dependency"
        and issue.section == "CallsFromModule"
        for issue in issues
    )


def test_module_dependency_name_helpers_return_plain_strings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Dependency helpers return names with optional implicit filtering."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Compute a node-certifier result.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

    CallsFromModule
    --------------
    validator.parse [implicit] -> optional pre-validation step
    hashlib -> hash helper

    InstantiationsFromModule
    ------------------------
    NodeHashState() [implicit] -> instantiate node state
    Node() -> instantiate helper object
    """
    spec = parse_graph_block(doc)
    assert spec.module_call_names() == ["validator.parse", "hashlib"]
    assert spec.module_call_names(include_implicit=False) == ["hashlib"]
    assert spec.module_instantiates_names() == ["NodeHashState", "Node"]
    assert spec.module_instantiates_names(include_implicit=False) == ["Node"]
