from __future__ import annotations

import pytest
import shutil
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.common.docstring.docstring_parser import check_graph_docstring, parse_graph_block  # noqa: E402
from officina.common.docstring.docstring_schema import load_docstring_schema  # noqa: E402
from officina.common.docstring import docstring_policy  # noqa: E402
from officina.validators.docstring_validator import validate_module_docstrings  # noqa: E402


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
    - Intent
    - Rationale
    - Phase
    - NonInferableCalls
    - Wraps
    - Owns
    - Contracts
    - CallsFromRepo
    - InstantiationsFromRepo
  forbidden_summary_phrases:
    - documents the certifier control point
    - repo-visible dependencies
  forbidden_intent_phrases:
    - dependency declarations that the docstring graph can parse
  forbidden_rationale_phrases:
    - compact docstrings whose declared calls and constructors stay aligned
    - static dependency validation
  forbidden_pseudocode_phrases:
    - immutable_record = constructor_fields
  module_dependencies:
    calls_section: CallsFromRepo
    instantiates_section: InstantiationsFromRepo
    dependency_why:
      allow_legacy_string: true
    pseudocode_quality:
      require_assigned_dependency_output_use: false
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


def _install_custom_config(tmp_path: Path, monkeypatch, text: str) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(
        "officina.common.docstring.docstring_schema.resolve_docstring_config_path",
        lambda _path=None: config_path,
    )
    monkeypatch.setattr(
        "officina.common.docstring.docstring_policy.resolve_docstring_config_path",
        lambda _path=None: config_path,
    )


def _validate_source(
    tmp_path: Path,
    source: str,
    *,
    check_group: str = "all",
):
    """Validate one temporary production-like module through the public API."""
    module_path = tmp_path / "sample.py"
    module_path.write_text(source, encoding="utf-8")
    return validate_module_docstrings(
        module_path,
        require_module_docstring=False,
        check_group=check_group,
    )


def _dependency_doc(
    summary: str,
    *,
    calls: tuple[str, ...] = (),
    products: tuple[str, ...] = (),
) -> str:
    """Build a complete callable docstring fixture with literal dependency edges."""
    sections = [
        summary,
        "",
        "Intent",
        "------",
        "Exercise the dependency classification contract under a controlled source fixture.",
        "",
        "Rationale",
        "---------",
        "The fixture keeps lexical resolution and product-position expectations independently observable.",
        "",
        "Pseudocode",
        "----------",
        "- return dependency classification result",
        "",
        "Wraps",
        "-----",
        "- none",
    ]
    if calls:
        sections.extend(("", "CallsFromRepo", "-------------"))
        for name in calls:
            sections.extend(
                (
                    f"{name}:",
                    "  why:",
                    "    computes: \"Invokes the repo dependency as an operation in the classification fixture.\"",
                )
            )
    if products:
        sections.extend(("", "InstantiationsFromRepo", "----------------------"))
        for name in products:
            sections.extend(
                (
                    f"{name}:",
                    "  why:",
                    "    constructs: \"Carries the repo dependency result into the recognized consumer fixture.\"",
                )
            )
    return "\n".join(sections)


def _function_source(name: str, doc: str, body: str, *, indent: str = "") -> str:
    """Render one nested or top-level function fixture."""
    rendered_doc = textwrap.indent(doc, f"{indent}    ")
    rendered_body = textwrap.indent(textwrap.dedent(body).strip(), f"{indent}    ")
    return (
        f'{indent}def {name}():\n'
        f'{indent}    """\n{rendered_doc}\n{indent}    """\n'
        f"{rendered_body}\n"
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
    - if input is missing:
      - raise ValueError
    - for item in payload:
      - if item is invalid:
        - raise ValueError
      - else:
        - set kept = item
    - return ordered_payload
    """
    spec = parse_graph_block(doc)
    kinds = [step.kind for step in spec.pseudocode_steps]
    assert kinds == ["if", "raise", "for", "if", "raise", "else", "set", "return"]


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
    assert any(issue.code == "docstring.invalid-pseudocode" for issue in issues)


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

    CallsFromRepo
    --------------
    validator.parse:
      why: "Parses edge source declarations."
    hashlib:
      why: "Provides hashing helpers."
    NodeHashState:
      why: "Instantiates node hash wrapper."

    InstantiationsFromRepo
    ------------------------
    NodeHashState:
      why: "Instantiates state for this step."
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

    CallsFromRepo
    --------------
    validator.parse
    invalid-name()
    """
    issues = check_graph_docstring(doc)
    assert any(
        issue.code == "docstring.invalid-module-dependency"
        and issue.section == "CallsFromRepo"
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

    CallsFromRepo
    --------------
    "validator.parse [implicit]":
      why: "Runs optional pre-validation."
    hashlib:
      why: "Runs direct hashing call."

    InstantiationsFromRepo
    ------------------------
    "NodeHashState [implicit]":
      why: "Initializes helper internal state."
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
        "  module_dependencies:\n    calls_section: CallsFromRepo\n    instantiates_section: InstantiationsFromRepo\n",
        "  module_dependencies:\n    calls_section: CallsFromRepo\n    instantiates_section: InstantiationsFromRepo\n    allow_implicit: false\n",
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

        CallsFromRepo
        --------------
        "validator.parse [implicit]":
          why: "Runs optional pre-validation."
        hashlib:
          why: "Runs direct hash call."
    """
    spec = parse_graph_block(doc)
    assert [entry.name for entry in spec.module_calls] == ["hashlib"]
    assert [entry.implicit for entry in spec.module_calls] == [False]

    issues = check_graph_docstring(doc)
    assert any(
        issue.code == "docstring.invalid-module-dependency"
        and issue.section == "CallsFromRepo"
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

    CallsFromRepo
    --------------
    "validator.parse [implicit]":
      why: "Runs optional pre-validation."
    hashlib:
      why: "Runs hash helper."

    InstantiationsFromRepo
    ------------------------
    "NodeHashState [implicit]":
      why: "Instantiates node state."
    Node:
      why: "Instantiates helper object."
    """
    spec = parse_graph_block(doc)
    assert spec.module_call_names() == ["validator.parse", "hashlib"]
    assert spec.module_call_names(include_implicit=False) == ["hashlib"]
    assert spec.module_instantiates_names() == ["NodeHashState", "Node"]
    assert spec.module_instantiates_names(include_implicit=False) == ["Node"]


def test_repo_dependency_tree_sections_are_flattened(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Shared-prefix dependency trees flatten into dotted dependency names."""
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
    - preconditions: input is available

    CallsFromRepo
    --------------
    ._node_certifier._v4_module_renames:
      why: "Maps legacy subject ids to canonical review targets."
    officina.common:
      repository_paths:
        repository_relative_path:
          why: "Normalizes candidate blueprint paths."

    InstantiationsFromRepo
    ------------------------
    ._node_certifier.V4LegacyReviewContext:
      why: "Constructs typed rows for legacy claims."
    """
    spec = parse_graph_block(doc)
    assert [entry.name for entry in spec.module_calls] == [
        "._node_certifier._v4_module_renames",
        "officina.common.repository_paths.repository_relative_path",
    ]
    assert [entry.name for entry in spec.module_instantiates] == [
        "._node_certifier.V4LegacyReviewContext"
    ]


def test_dispatch_tree_section_parses_ids_and_why(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Dispatch dependency trees parse into dispatch id/why records."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Dispatch the certification interface.

    Graph
    -----
    Run a configured interface dependency.

    Role
    ----
    Owns interface dispatch handoff.

    Contracts
    ---------
    - preconditions: dispatcher id is configured

    Dispatches
    ----------
    skills.skill-certifier:
      interface:
        default:
          why: "Dispatches CLI invocation to the certification entrypoint."
    """
    spec = parse_graph_block(doc)
    assert spec.dispatch_ids() == ["skills.skill-certifier.interface.default"]
    assert spec.dispatches[0].why.startswith("Dispatches CLI")


def test_dependency_path_rule_accepts_allowed_absolute_and_relative(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Allowed roots and leading-dot relative paths pass portability checks."""
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
    - preconditions: input is available

    CallsFromRepo
    --------------
    ._node_certifier.helper:
      why: "Uses local helper."
    officina.common.repository_paths.repository_relative_path:
      why: "Uses an allowed absolute repo root."

    Dispatches
    ----------
    skills.skill-certifier.interface.default:
      why: "Uses an allowed skill interface root."
    """
    issues = check_graph_docstring(doc)
    assert not any(issue.code == "docstring.absolute-dependency-not-allowed" for issue in issues)


def test_dependency_path_rule_rejects_bare_and_unknown_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Bare relative-looking paths and unknown absolute roots are rejected."""
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
    - preconditions: input is available

    CallsFromRepo
    --------------
    _node_certifier.helper:
      why: "Missing leading dot."
    common.repository_paths.repository_relative_path:
      why: "Starts from an unapproved root."

    Dispatches
    ----------
    skill-certifier.interface.default:
      why: "Missing the skills root."
    """
    issues = check_graph_docstring(doc)
    bad_paths = [
        issue.message
        for issue in issues
        if issue.code == "docstring.absolute-dependency-not-allowed"
    ]
    assert len(bad_paths) == 3


def test_legacy_flat_dependency_syntax_can_be_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Legacy flat dependency lines are rejected when config disables them."""
    _install_custom_format(tmp_path, monkeypatch)
    _install_custom_config(
        tmp_path,
        monkeypatch,
        """\
allowed_abs:
  - officina
  - skills
names_for_dependency_sections:
  calls: CallsFromRepo
  instantiations: InstantiationsFromRepo
  dispatches: Dispatches
dependency_syntax:
  allow_legacy_flat: false
  require_why: true
""",
    )

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
    - preconditions: input is available

    CallsFromRepo
    --------------
    officina.common.repository_paths.repository_relative_path -> normalize path
    """
    issues = check_graph_docstring(doc)
    assert any(issue.code == "docstring.invalid-module-dependency" for issue in issues)


def test_repo_default_disables_legacy_flat_dependency_syntax() -> None:
    """The active repo config rejects legacy flat dependency declarations by default."""
    rules = load_docstring_schema()
    assert rules.module_dependencies.allow_legacy_flat is False


def test_colon_dependency_section_header_is_reported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Known section names written as YAML keys are explicit header errors."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Load repository graph data.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

    Contracts
    ---------
    - preconditions: input is available

    Pseudocode
    ----------
    - Load the graph.
    CallsFromRepo:
      officina.common.blueprint_graph.load_repository_blueprint_graph:
        why: "Loads blueprint graph data."

    Wraps
    -----
    none
    """
    issues = check_graph_docstring(doc)
    assert any(
        issue.code == "docstring.invalid-section-header"
        and issue.section == "CallsFromRepo"
        for issue in issues
    )


def test_strict_pseudocode_refs_accept_leading_dot_relative_dependencies(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Relative dependency markers preserve the leading-dot logical address."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Load repository graph data.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

    Contracts
    ---------
    - preconditions: input is available

    Pseudocode
    ----------
    - value = @._node_certifier._helper()

    CallsFromRepo
    --------------
    ._node_certifier._helper:
      why: "Runs the local helper used by this callable."
    """
    spec = parse_graph_block(doc)
    assert spec.pseudocode_dependency_refs == [
        "CallsFromRepo:._node_certifier._helper"
    ]
    assert not any(issue.code == "docstring.invalid-pseudocode" for issue in check_graph_docstring(doc))


def test_configured_forbidden_boilerplate_phrases_are_reported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Configured phrase bans catch syntactically valid but uninformative docs."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Function sample documents the certifier control point and its repo-visible dependencies.

    Intent
    ------
    Expose the sample behavior with dependency declarations that the docstring graph can parse.

    Rationale
    ---------
    The helper keeps compact docstrings whose declared calls and constructors stay aligned with static dependency validation.

    Pseudocode
    ----------
    - set immutable_record = constructor_fields

    Graph
    -----
    Build a placeholder-resistant documentation graph.

    Role
    ----
    Exercises configured forbidden phrase validation.

    Contracts
    ---------
    - preconditions: input is available

    Wraps
    -----
    none
    """
    issue_codes = {issue.code for issue in check_graph_docstring(doc)}
    assert "docstring.summary-forbidden" in issue_codes
    assert "docstring.intent-forbidden" in issue_codes
    assert "docstring.rationale-forbidden" in issue_codes
    assert "docstring.pseudocode-forbidden" in issue_codes


def test_dependency_why_action_key_is_parsed_and_validated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Compact why action keys become graphable dependency edge labels."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Normalize repository paths.

    Graph
    -----
    Build deterministic path metadata.

    Role
    ----
    Owns path normalization for this stage.

    Contracts
    ---------
    - preconditions: root exists

    Pseudocode
    ----------
    - path = @repository_relative_path(root)

    CallsFromRepo
    --------------
    officina.common.repository_paths.repository_relative_path:
      why:
        transforms: "Normalizes candidate paths before manifest comparison."
    """
    spec = parse_graph_block(doc)
    assert spec.module_calls[0].why_action == "transforms"
    assert spec.module_calls[0].why == "Normalizes candidate paths before manifest comparison."
    assert "docstring.dependency-why-action" not in {
        issue.code for issue in check_graph_docstring(doc)
    }


def test_dependency_why_action_rejects_legacy_unknown_multi_and_short_misc(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Dependency why action syntax gives guidance for non-graphable edge labels."""
    schema_path = tmp_path / "docstring.standard.yaml"
    schema_path.write_text(
        _CUSTOM_DOCSTRING_STANDARD.replace(
            "      allow_legacy_string: true",
            "      allow_legacy_string: false\n      misc_min_chars: 40",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "officina.common.docstring.docstring_schema.resolve_docstring_schema_path",
        lambda _path=None: schema_path,
    )

    def issue_codes_for(why_block: str) -> set[str]:
        doc = f"""
        Normalize repository paths.

        Graph
        -----
        Build deterministic path metadata.

        Role
        ----
        Owns path normalization for this stage.

        Contracts
        ---------
        - preconditions: root exists

        Pseudocode
        ----------
        - path = @repository_relative_path(root)

        CallsFromRepo
        --------------
        officina.common.repository_paths.repository_relative_path:
{why_block}
        """
        return {issue.code for issue in check_graph_docstring(doc)}

    assert "docstring.dependency-why-action" in issue_codes_for(
        '          why: "Legacy string rationale."\n'
    )
    assert "docstring.dependency-why-action" in issue_codes_for(
        '          why:\n            unknown: "Uses an unknown action."\n'
    )
    assert "docstring.dependency-why-action" in issue_codes_for(
        '          why:\n            reads: "Reads paths."\n            transforms: "Normalizes paths."\n'
    )
    assert "docstring.dependency-why-action" in issue_codes_for(
        '          why:\n            misc: "Too short."\n'
    )


def test_pseudocode_dataflow_quality_reports_placeholders_and_unused_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Pseudocode quality checks require graphable variables and data use."""
    schema_path = tmp_path / "docstring.standard.yaml"
    config_path = tmp_path / "config.yaml"
    schema_path.write_text(
        _CUSTOM_DOCSTRING_STANDARD.replace(
            "      require_assigned_dependency_output_use: false",
            "      require_assigned_dependency_output_use: true",
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        "pseudocode_quality:\n"
        "  forbidden_variables:\n"
        "    - value\n"
        "    - out\n"
        "    - state\n"
        "    - args\n"
        "    - data\n"
        "  require_assigned_dependency_output_use: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "officina.common.docstring.docstring_schema.resolve_docstring_schema_path",
        lambda _path=None: schema_path,
    )
    monkeypatch.setattr(
        "officina.common.docstring.docstring_policy.resolve_docstring_config_path",
        lambda _path=None: config_path,
    )

    doc = """
    Load graph data.

    Graph
    -----
    Build deterministic graph metadata.

    Role
    ----
    Owns graph loading for this stage.

    Contracts
    ---------
    - preconditions: root exists

    Pseudocode
    ----------
    - value = @load_repository_blueprint_graph(state)
    - digest = @sha256(raw_bytes)
    - return ok

    CallsFromRepo
    --------------
    officina.common.blueprint_graph.load_repository_blueprint_graph:
      why:
        reads: "Loads graph declarations from the repository blueprint."
    .sha256:
      why:
        computes: "Computes the digest used for comparison."
    """
    issue_codes = {issue.code for issue in check_graph_docstring(doc)}
    assert "docstring.pseudocode-placeholder-variable" in issue_codes
    assert "docstring.pseudocode-placeholder-argument" in issue_codes
    assert "docstring.pseudocode-output-unused" in issue_codes


def test_repeated_template_detection_reports_normalized_module_boilerplate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Repeated-template detection catches copy-edited callable prose mechanically."""
    schema_path = tmp_path / "docstring.standard.yaml"
    schema_path.write_text(
        _CUSTOM_DOCSTRING_STANDARD.replace(
            "    pseudocode_quality:\n",
            "    repeated_template_detection:\n"
            "      enabled: true\n"
            "      min_repetitions: 3\n"
            "      min_normalized_chars: 40\n"
            "    pseudocode_quality:\n",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "officina.common.docstring.docstring_schema.resolve_docstring_schema_path",
        lambda _path=None: schema_path,
    )
    _install_custom_config(
        tmp_path,
        monkeypatch,
        """\
repeated_template_detection:
  enabled: true
  min_repetitions: 3
  min_normalized_chars: 40
""",
    )

    module_path = tmp_path / "sample.py"
    template = '''def {name}():\n'''
    template += '''    """Function {name} validates the named module operation.\n\n'''
    template += '''    Graph\n    -----\n    Build deterministic graph metadata.\n\n'''
    template += '''    Role\n    ----\n    Owns graph loading for this stage.\n\n'''
    template += '''    Contracts\n    ---------\n    - preconditions: root exists\n\n'''
    template += '''    Pseudocode\n    ----------\n    - return {name}\n    """\n'''
    template += '''    return "{name}"\n\n'''
    module_path.write_text(
        '"""Template sample."""\n\n'
        + template.format(name="alpha")
        + template.format(name="beta")
        + template.format(name="gamma"),
        encoding="utf-8",
    )
    issues = validate_module_docstrings(
        module_path,
        require_module_docstring=False,
        check_group="syntax",
    )
    assert any(issue.code == "docstring.repeated-template" for issue in issues)


def test_config_profile_enables_repeated_template_detection_by_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Repo config profiles enable stricter prose checks for matching paths only."""
    _install_custom_format(tmp_path, monkeypatch)
    _install_custom_config(
        tmp_path,
        monkeypatch,
        """\
allowed_abs:
  - officina
  - skills
repeated_template_detection:
  enabled: false
  min_repetitions: 3
  min_normalized_chars: 40
profiles:
  graphable_skill_runtime:
    applies_to:
      - skills/*.py
    checks:
      repeated_template_detection: true
""",
    )

    module_path = tmp_path / "skills" / "sample.py"
    module_path.parent.mkdir()
    template = '''def {name}():\n'''
    template += '''    """Function {name} validates the named module operation.\n\n'''
    template += '''    Graph\n    -----\n    Build deterministic graph metadata.\n\n'''
    template += '''    Role\n    ----\n    Owns graph loading for this stage.\n\n'''
    template += '''    Contracts\n    ---------\n    - preconditions: root exists\n\n'''
    template += '''    Pseudocode\n    ----------\n    - return {name}\n    """\n'''
    template += '''    return "{name}"\n\n'''
    module_path.write_text(
        '"""Template sample."""\n\n'
        + template.format(name="alpha")
        + template.format(name="beta")
        + template.format(name="gamma"),
        encoding="utf-8",
    )

    issues = validate_module_docstrings(
        module_path,
        require_module_docstring=False,
        check_group="syntax",
    )
    assert any(issue.code == "docstring.repeated-template" for issue in issues)


def test_config_profile_enables_pseudocode_output_use_by_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Path profiles can require dependency outputs to feed later pseudocode steps."""
    _install_custom_format(tmp_path, monkeypatch)
    _install_custom_config(
        tmp_path,
        monkeypatch,
        """\
allowed_abs:
  - officina
  - skills
pseudocode_quality:
  forbidden_variables:
    - value
    - out
    - state
    - args
    - data
  require_assigned_dependency_output_use: false
profiles:
  graphable_skill_runtime:
    applies_to:
      - skills/*.py
    checks:
      pseudocode_output_use: true
""",
    )

    module_path = tmp_path / "skills" / "sample.py"
    module_path.parent.mkdir()
    module_path.write_text(
        '''"""Pseudocode profile sample."""\n\n'''
        '''def entry():\n'''
        '''    """Load graph data.\n\n'''
        '''    Graph\n    -----\n    Build deterministic graph metadata.\n\n'''
        '''    Role\n    ----\n    Owns graph loading for this stage.\n\n'''
        '''    Contracts\n    ---------\n    - preconditions: root exists\n\n'''
        '''    Pseudocode\n    ----------\n    - graph = @load_repository_blueprint_graph(root)\n'''
        '''    - return ok\n\n'''
        '''    CallsFromRepo\n    --------------\n'''
        '''    officina.common.blueprint_graph.load_repository_blueprint_graph:\n'''
        '''      why:\n        reads: "Loads repository blueprint declarations."\n'''
        '''    """\n'''
        '''    return None\n''',
        encoding="utf-8",
    )

    issues = validate_module_docstrings(
        module_path,
        require_module_docstring=False,
        check_group="syntax",
    )
    assert any(issue.code == "docstring.pseudocode-output-unused" for issue in issues)


def test_resources_and_dataflow_sections_parse_compact_structured_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Resource and dataflow sections expose compact graph nodes and edges."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Load repository graph data.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

    Contracts
    ---------
    - preconditions: input is available

    Resources
    ---------
    docstring.config:
      kind: file
      access: read
      why: "Loads repo-local policy roots."

    Dataflow
    --------
    - from: docstring.config
      to: .load_docstring_schema
      kind: config
      why: "Feeds repo-local roots into effective policy."
    """
    spec = parse_graph_block(doc)
    assert [resource.id for resource in spec.resources] == ["docstring.config"]
    assert spec.resources[0].kind == "file"
    assert spec.resources[0].access == "read"
    assert [(edge.source, edge.target, edge.kind) for edge in spec.dataflows] == [
        ("docstring.config", ".load_docstring_schema", "config")
    ]


def test_invalid_resources_and_dataflow_are_reported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Malformed resource and dataflow sections have explicit validator codes."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Load repository graph data.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

    Contracts
    ---------
    - preconditions: input is available

    Pseudocode
    ----------
    - return result

    Resources
    ---------
    docstring.config:
      kind: file

    Dataflow
    --------
    docstring.config:
      to: .load_docstring_schema
    """
    issues = check_graph_docstring(doc)
    assert any(issue.code == "docstring.invalid-resource" for issue in issues)
    assert any(issue.code == "docstring.invalid-dataflow" for issue in issues)


def test_relative_repo_dependency_is_grounded_to_local_call(tmp_path: Path) -> None:
    """Leading-dot dependency declarations resolve and match local call bodies."""
    module_path = tmp_path / "sample.py"
    module_path.write_text(
        '''"""Sample module for dependency checks."""\n\n'''
        '''def _helper():\n'''
        '''    """Build a local helper result.\n\n'''
        '''    Intent\n    ------\n    Produce a local helper value.\n\n'''
        '''    Rationale\n    ---------\n    Keeps local dependency grounding observable in tests.\n\n'''
        '''    Pseudocode\n    ----------\n    - Return a constant helper value.\n\n'''
        '''    Wraps\n    -----\n    - none\n    """\n'''
        '''    return 1\n\n'''
        '''def entry():\n'''
        '''    """Build a local entry result.\n\n'''
        '''    Intent\n    ------\n    Use the local helper dependency.\n\n'''
        '''    Rationale\n    ---------\n    Documents the local helper edge so graph extraction can ground it.\n\n'''
        '''    Pseudocode\n    ----------\n    - value = @_helper()\n\n'''
        '''    Wraps\n    -----\n    - none\n\n'''
        '''    CallsFromRepo\n    --------------\n    ._helper:\n      why: "Uses the local helper."\n    """\n'''
        '''    _helper()\n'''
        '''    return 1\n''',
        encoding="utf-8",
    )
    issues = validate_module_docstrings(
        module_path,
        require_module_docstring=False,
        check_group="behavioral",
    )
    entry_codes = {issue.code for issue in issues if issue.node_id == "entry"}
    assert "docstring.module-dependency-not-observed" not in entry_codes
    assert "docstring.module-dependency-unresolved" not in entry_codes


def test_implicit_relative_dependency_must_still_resolve(tmp_path: Path) -> None:
    """Implicit dependencies skip body observation but not logical target grounding."""
    module_path = tmp_path / "sample.py"
    module_path.write_text(
        '''"""Sample module for implicit dependency checks."""\n\n'''
        '''def entry():\n'''
        '''    """Build a local entry result.\n\n'''
        '''    Intent\n    ------\n    Use a documented implicit dependency.\n\n'''
        '''    Rationale\n    ---------\n    Ensures implicit dependencies are still grounded to known logical nodes.\n\n'''
        '''    Pseudocode\n    ----------\n    - Return a constant value.\n\n'''
        '''    Wraps\n    -----\n    - none\n\n'''
        '''    CallsFromRepo\n    --------------\n    "._missing [implicit]":\n      why: "Represents an implicit local dependency that should still resolve."\n    """\n'''
        '''    return 1\n''',
        encoding="utf-8",
    )
    issues = validate_module_docstrings(
        module_path,
        require_module_docstring=False,
        check_group="behavioral",
    )
    assert any(
        issue.node_id == "entry"
        and issue.code == "docstring.module-dependency-unresolved"
        for issue in issues
    )


def test_declared_dispatch_must_be_known_and_observed_in_body(tmp_path: Path) -> None:
    """Dispatch declarations are grounded to known ids and dispatcher call literals."""
    module_path = tmp_path / "sample.py"
    module_path.write_text(
        '''"""Sample module for dispatch dependency checks."""\n\n'''
        '''import subprocess\n\n'''
        '''def entry():\n'''
        '''    """Dispatch a documented interface.\n\n'''
        '''    Intent\n    ------\n    Dispatch to a skill interface after validation.\n\n'''
        '''    Rationale\n    ---------\n    Keeps interface handoff behavior explicit for graph extraction.\n\n'''
        '''    Pseudocode\n    ----------\n    - #skills.not-real.interface.default()\n\n'''
        '''    Wraps\n    -----\n    - none\n\n'''
        '''    Dispatches\n    ----------\n    skills.not-real.interface.default:\n      why: "Documents a missing interface id."\n    """\n'''
        '''    return subprocess.run(["dispatcher", "--caller-skill", "sample", "skills.other.interface.default"], check=False).returncode\n''',
        encoding="utf-8",
    )
    issues = validate_module_docstrings(
        module_path,
        require_module_docstring=False,
        check_group="behavioral",
    )
    entry_codes = {issue.code for issue in issues if issue.node_id == "entry"}
    assert "docstring.dispatch-unresolved" in entry_codes
    assert "docstring.dispatch-not-observed" in entry_codes
    assert "docstring.dispatch-undocumented" in entry_codes


def test_strict_pseudocode_parses_typed_compact_steps(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Strict pseudocode produces typed graphable steps from compact syntax."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Certify a repository node.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

    Contracts
    ---------
    - preconditions: input is available

    Pseudocode
    ----------
    - graph = @load_repository_blueprint_graph(root)
    - for node in graph.nodes:
      - authority = @resolve_node_authority(node)
      - if authority is missing:
        - result = CertificationResult(status=`fail_closed`)
        - continue
      - review = #skill-certifier.interface.default(node)
    - return result

    CallsFromRepo
    --------------
    officina.common.blueprint_graph.load_repository_blueprint_graph:
      why: "Loads logical graph declarations."
    .resolve_node_authority:
      why: "Resolves local authority."

    InstantiationsFromRepo
    ------------------------
    .CertificationResult:
      why: "Builds typed certification decisions."

    Dispatches
    ----------
    skills.skill-certifier.interface.default:
      why: "Runs semantic review."
    """
    spec = parse_graph_block(doc)
    assert [step.kind for step in spec.pseudocode_steps] == [
        "call",
        "for",
        "call",
        "if",
        "instantiate",
        "continue",
        "dispatch",
        "return",
    ]
    assert spec.pseudocode_steps[0].output == "graph"
    assert spec.pseudocode_steps[0].ref == "load_repository_blueprint_graph"
    assert spec.pseudocode_steps[1].loop_variable == "node"
    assert spec.pseudocode_steps[1].loop_iterable == "graph.nodes"
    assert spec.pseudocode_steps[3].condition == "authority is missing"
    assert spec.pseudocode_steps[4].ref == "CertificationResult"
    assert spec.pseudocode_steps[6].ref == "skill-certifier.interface.default"
    assert not any(issue.code == "docstring.invalid-pseudocode" for issue in check_graph_docstring(doc))


def test_strict_pseudocode_rejects_old_scoped_refs_and_prose(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Old prose marker style is not valid strict pseudocode."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Certify a repository node.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

    Contracts
    ---------
    - preconditions: input is available

    Pseudocode
    ----------
    - Call @CallsFromRepo:load_repository_blueprint_graph before returning.

    CallsFromRepo
    --------------
    officina.common.blueprint_graph.load_repository_blueprint_graph:
      why: "Loads logical graph declarations."
    """
    issues = check_graph_docstring(doc)
    assert any(issue.code == "docstring.invalid-pseudocode" for issue in issues)


def test_strict_pseudocode_rejects_bad_control_forms(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Only if, while, for, and else control forms are accepted."""
    _install_custom_format(tmp_path, monkeypatch)

    doc = """
    Certify a repository node.

    Graph
    -----
    Build deterministic hashes and metadata for one node.

    Role
    ----
    Owns hash-state transformations for this stage.

    Contracts
    ---------
    - preconditions: input is available

    Pseudocode
    ----------
    - if ready: return result
    - elif fallback:
      - return fallback
    - else:
      - return result
    """
    issues = check_graph_docstring(doc)
    assert any(issue.code == "docstring.invalid-pseudocode" for issue in issues)
    assert any(issue.code == "docstring.pseudocode-else-unmatched" for issue in issues)


def test_class_docstring_does_not_inherit_method_body_dependencies(tmp_path: Path) -> None:
    """Class docs describe the interface, not every dependency used by methods."""
    module_path = tmp_path / "sample.py"
    module_path.write_text(
        '''"""Class dependency sample."""\n\n'''
        '''from pathlib import Path\n\n'''
        '''class Builder:\n'''
        '''    """Coordinate path building methods.\n\n'''
        '''    Intent\n    ------\n    Expose the path-building interface.\n\n'''
        '''    Rationale\n    ---------\n    Class documentation should describe interface responsibility without duplicating method dependencies.\n\n'''
        '''    Pseudocode\n    ----------\n    - return builder interface\n\n'''
        '''    Wraps\n    -----\n    - none\n\n'''
        '''    CallsFromRepo\n    --------------\n    .build:\n      why:\n        orchestrates: "Names the method-level operation exposed by this class interface."\n\n'''
        '''    """\n\n'''
        '''    def build(self):\n'''
        '''        """Build a pathlib path.\n\n'''
        '''        Intent\n        ------\n        Convert the literal path into a Path instance.\n\n'''
        '''        Rationale\n        ---------\n        The method owns the concrete constructor dependency.\n\n'''
        '''        Pseudocode\n        ----------\n        - path = Path(raw_path)\n        - return path\n\n'''
        '''        Wraps\n        -----\n        - none\n\n'''
        '''        InstantiationsFromRepo\n        ------------------------\n        .Path:\n          why:\n            constructs: "Creates the path object returned by this method."\n        """\n'''
        '''        return Path("x")\n''',
        encoding="utf-8",
    )
    issues = validate_module_docstrings(
        module_path,
        require_module_docstring=False,
        check_group="behavioral",
    )
    class_codes = {issue.code for issue in issues if issue.node_id == "Builder"}
    assert "docstring.module-dependency-undocumented" not in class_codes


def test_later_config_profile_can_relax_runtime_tests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Profiles are ordered so test paths can opt out of runtime prose repetition checks."""
    _install_custom_format(tmp_path, monkeypatch)
    _install_custom_config(
        tmp_path,
        monkeypatch,
        """\
profiles:
  runtime:
    applies_to:
      - skills/*/_rtx/**/*.py
    checks:
      repeated_template_detection: true
  runtime_tests:
    applies_to:
      - skills/*/_rtx/tests/*.py
      - skills/*/_rtx/tests/**/*.py
    checks:
      repeated_template_detection: false
""",
    )

    module_path = tmp_path / "skills" / "sample" / "_rtx" / "tests" / "test_sample.py"
    module_path.parent.mkdir(parents=True)
    template = '''def test_{name}():\n'''
    template += '''    """Test {name} validates the same command-line behavior.\n\n'''
    template += '''    Graph\n    -----\n    Build deterministic graph metadata.\n\n'''
    template += '''    Role\n    ----\n    Owns graph loading for this stage.\n\n'''
    template += '''    Contracts\n    ---------\n    - preconditions: root exists\n\n'''
    template += '''    Pseudocode\n    ----------\n    - return {name}\n    """\n'''
    template += '''    assert "{name}"\n\n'''
    module_path.write_text(
        '"""Template test sample."""\n\n'
        + template.format(name="alpha")
        + template.format(name="beta")
        + template.format(name="gamma"),
        encoding="utf-8",
    )
    issues = validate_module_docstrings(
        module_path,
        require_module_docstring=False,
        check_group="syntax",
    )
    assert not any(issue.code == "docstring.repeated-template" for issue in issues)


def test_config_profile_relaxes_required_sections_and_missing_docstrings_by_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Test profiles can keep lightweight docstrings without global parser warnings."""
    _install_custom_format(tmp_path, monkeypatch)
    _install_custom_config(
        tmp_path,
        monkeypatch,
        """\
profiles:
  runtime_tests:
    applies_to:
      - skills/*/_rtx/tests/*.py
    callable:
      require_docstrings: false
      required_sections: []
      min_pseudocode_steps: 0
""",
    )

    module_path = tmp_path / "skills" / "sample" / "_rtx" / "tests" / "test_sample.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        '''"""Profile relaxation sample."""\n\n'''
        '''def helper():\n'''
        '''    """Prepare fixture data for the test."""\n'''
        '''    return 1\n\n'''
        '''def test_without_docstring():\n'''
        '''    assert helper() == 1\n''',
        encoding="utf-8",
    )

    issues = validate_module_docstrings(
        module_path,
        require_module_docstring=False,
        check_group="syntax",
    )
    assert not any(
        issue.code in {
            "docstring.missing",
            "docstring.section-missing",
            "docstring.pseudocode-step-min",
        }
        for issue in issues
    )


def test_config_profile_rejects_unknown_check_keys(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Profile check names are validated so typos do not silently disable checks."""
    _install_custom_format(tmp_path, monkeypatch)
    _install_custom_config(
        tmp_path,
        monkeypatch,
        """\
profiles:
  typo_profile:
    applies_to:
      - skills/*.py
    checks:
      repeated_templates: true
""",
    )

    with pytest.raises(ValueError, match="Additional properties are not allowed"):
        load_docstring_schema()


def test_config_profile_rejects_malformed_applies_to(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Profile path patterns must be explicit strings."""
    _install_custom_format(tmp_path, monkeypatch)
    _install_custom_config(
        tmp_path,
        monkeypatch,
        """\
profiles:
  bad_profile:
    applies_to: skills/*.py
    checks:
      repeated_template_detection: true
""",
    )

    with pytest.raises(ValueError, match="applies_to"):
        load_docstring_schema()


@pytest.mark.parametrize(
    "source",
    (
        '''from dataclasses import dataclass\n\n@dataclass\nclass Candidate:\n    """Carry immutable candidate fields."""\n    value: int\n    label: str = "ready"\n''',
        '''import dataclasses as dc\n\n@dc.dataclass\nclass Candidate:\n    """Carry aliased dataclass fields."""\n    value: int = dc.field(default=1)\n''',
        '''from dataclasses import dataclass as record, field as slot\n\n@record\nclass Candidate:\n    """Carry imported decorator fields."""\n    value: int = slot(default=1)\n''',
        '''from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Candidate:\n    """Carry configured dataclass fields."""\n    value: int\n''',
        '''import dataclasses as dc\n\n@dc.dataclass(frozen=True)\nclass Candidate:\n    """Carry aliased configured fields."""\n    value: int\n''',
        '''from dataclasses import dataclass as record\n\n@record(frozen=True)\nclass Candidate:\n    """Carry imported configured fields."""\n    value: int\n''',
        '''from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Candidate:\n    """Carry configured fields with optional explanatory sections.\n\n    Intent\n    ------\n    Preserve useful detail when a compact structural class already documents it.\n\n    Rationale\n    ---------\n    Compact treatment waives explanatory sections but must not reject them when present.\n    """\n    value: int\n''',
    ),
)
def test_compact_dataclass_accepts_only_resolved_stdlib_passive_fields(
    tmp_path: Path,
    source: str,
) -> None:
    """A sole resolved stdlib dataclass with passive fields needs only a summary."""
    issues = _validate_source(tmp_path, source, check_group="syntax")

    assert [issue for issue in issues if issue.node_id == "Candidate"] == []


@pytest.mark.parametrize(
    "source",
    (
        '''def dataclass(cls):\n    return cls\n\n@dataclass\nclass Candidate:\n    """Reject a spoofed decorator marker."""\n    value: int\n''',
        '''from dataclasses import dataclass\n\ndef tagged(cls):\n    return cls\n\n@tagged\n@dataclass\nclass Candidate:\n    """Reject an additional decorator marker."""\n    value: int\n''',
        '''from dataclasses import dataclass\n\n@dataclass\nclass Candidate:\n    """Reject methods in compact dataclasses."""\n    value: int\n    def method(self):\n        return self.value\n''',
        '''from dataclasses import dataclass\n\n@dataclass\nclass Candidate:\n    """Reject nested compact declarations."""\n    class Nested:\n        pass\n''',
        '''from dataclasses import dataclass\n\n@dataclass\nclass Candidate:\n    """Reject active field default calls."""\n    value: int = build_value()\n''',
        '''from dataclasses import dataclass\n\n@dataclass\nclass Candidate:\n    """Reject executable class assignments."""\n    value = 1\n''',
        '''class Candidate:\n    """Keep ordinary empty classes on the full profile."""\n    pass\n''',
    ),
)
def test_compact_dataclass_disqualifiers_keep_the_full_profile(
    tmp_path: Path,
    source: str,
) -> None:
    """Spoofed, decorated, active, or non-field classes retain full sections."""
    issues = _validate_source(tmp_path, source, check_group="syntax")

    assert any(
        issue.node_id == "Candidate" and issue.code == "docstring.section-missing"
        for issue in issues
    )


@pytest.mark.parametrize(
    "source",
    (
        '''@dataclasses.dataclass\nclass Candidate:\n    """Reject an unimported qualified decorator."""\n    value: int\n''',
        '''dataclasses = object()\n\n@dataclasses.dataclass\nclass Candidate:\n    """Reject a locally shadowed decorator root."""\n    value: int\n''',
        '''from dataclasses import dataclass\n\n@dataclass\nclass Candidate:\n    """Reject an unimported qualified field root."""\n    value: int = dataclasses.field(default=1)\n''',
        '''import dataclasses\nfrom dataclasses import dataclass\ndataclasses = object()\n\n@dataclass\nclass Candidate:\n    """Reject a locally shadowed field root."""\n    value: int = dataclasses.field(default=1)\n''',
    ),
)
def test_compact_dataclass_requires_visible_exact_stdlib_resolution(
    tmp_path: Path,
    source: str,
) -> None:
    """Qualified dataclass and field names are never trusted without visible imports."""
    issues = _validate_source(tmp_path, source, check_group="syntax")

    assert any(
        issue.node_id == "Candidate" and issue.code == "docstring.section-missing"
        for issue in issues
    )


def test_compact_dataclass_field_resolution_tracks_class_statement_order(
    tmp_path: Path,
) -> None:
    """A class target shadows an imported field root only after its RHS is evaluated."""
    source = '''import dataclasses\n\n@dataclasses.dataclass\nclass First:\n    """Allow the imported root while evaluating its binding RHS."""\n    dataclasses: object = dataclasses.field(default=None)\n\n@dataclasses.dataclass\nclass Candidate:\n    """Reject a field root shadowed by an earlier class binding."""\n    dataclasses: object = None\n    value: int = dataclasses.field(default=1)\n'''

    issues = _validate_source(tmp_path, source, check_group="syntax")

    assert [issue for issue in issues if issue.node_id == "First"] == []
    assert any(
        issue.node_id == "Candidate" and issue.code == "docstring.section-missing"
        for issue in issues
    )


@pytest.mark.parametrize("body", ("pass", "...", ""))
def test_compact_builtin_exception_accepts_only_passive_markers(
    tmp_path: Path,
    body: str,
) -> None:
    """An undecorated direct builtin exception marker needs only a summary."""
    suffix = f"    {body}\n" if body else ""
    source = (
        'class Candidate(ValueError):\n'
        '    """Report invalid candidate input."""\n'
        f"{suffix}"
    )

    issues = _validate_source(tmp_path, source, check_group="syntax")

    assert [issue for issue in issues if issue.node_id == "Candidate"] == []


@pytest.mark.parametrize(
    "source",
    (
        '''class ProjectError(Exception):\n    """Define the complete project exception base.\n\n    Intent\n    ------\n    Provide a project error marker.\n\n    Rationale\n    ---------\n    The complete declaration keeps the derived-class disqualifier fixture valid.\n\n    Pseudocode\n    ----------\n    - return project error marker\n\n    Wraps\n    -----\n    - none\n    """\n\nclass Candidate(ProjectError):\n    """Reject project-derived exceptions."""\n''',
        '''class Candidate(ValueError, TypeError):\n    """Reject multiple direct exception bases."""\n''',
        '''def tagged(cls):\n    return cls\n\n@tagged\nclass Candidate(ValueError):\n    """Reject decorated exception markers."""\n''',
        '''class Candidate(ValueError):\n    """Reject methods on exception markers."""\n    def explain(self):\n        return "bad"\n''',
        '''ValueError = RuntimeError\n\nclass Candidate(ValueError):\n    """Reject shadowed builtin exception names."""\n''',
        '''from custom import *\n\nclass Candidate(ValueError):\n    """Reject builtin names made ambiguous by a wildcard import."""\n''',
    ),
)
def test_compact_builtin_exception_disqualifiers_keep_the_full_profile(
    tmp_path: Path,
    source: str,
) -> None:
    """Only exact passive direct builtin exception markers are compact."""
    issues = _validate_source(tmp_path, source, check_group="syntax")

    assert any(
        issue.node_id == "Candidate" and issue.code == "docstring.section-missing"
        for issue in issues
    )


@pytest.mark.parametrize(
    "outer_header",
    (
        "def outer(ValueError):",
        "def outer():\n    ValueError = RuntimeError",
        "def outer():\n    del ValueError",
    ),
)
def test_compact_builtin_exception_rejects_enclosing_nonimport_shadows(
    tmp_path: Path,
    outer_header: str,
) -> None:
    """Parameters and assignments in enclosing functions shadow builtin exceptions."""
    source = (
        f"{outer_header}\n"
        "    class Candidate(ValueError):\n"
        '        """Reject a shadowed builtin exception marker."""\n'
        "    return Candidate\n"
    )

    issues = _validate_source(tmp_path, source, check_group="syntax")

    assert any(
        issue.node_id == "outer.Candidate"
        and issue.code == "docstring.section-missing"
        for issue in issues
    )


@pytest.mark.parametrize(
    "source,node_id",
    (
        (
            '''import dataclasses\n\nclass Outer:\n    dataclasses = object()\n\n    @dataclasses.dataclass\n    class Candidate:\n        """Reject a decorator root shadowed in the executing class namespace."""\n        value: int\n''',
            "Outer.Candidate",
        ),
        (
            '''class Outer:\n    ValueError = RuntimeError\n\n    class Candidate(ValueError):\n        """Reject a builtin base shadowed in the executing class namespace."""\n''',
            "Outer.Candidate",
        ),
        (
            '''class Outer:\n    match object():\n        case ValueError:\n            pass\n\n    class Candidate(ValueError):\n        """Reject a pattern-bound base in the executing class namespace."""\n''',
            "Outer.Candidate",
        ),
    ),
)
def test_compact_class_headers_respect_prior_enclosing_class_bindings(
    tmp_path: Path,
    source: str,
    node_id: str,
) -> None:
    """Nested class headers resolve after earlier containing-class bindings execute."""
    issues = _validate_source(tmp_path, source, check_group="syntax")

    assert any(
        issue.node_id == node_id and issue.code == "docstring.section-missing"
        for issue in issues
    )


def test_callable_local_import_is_visible_to_dependency_validation(tmp_path: Path) -> None:
    """A function-local repo import resolves its documented operation edge."""
    dependency = "officina.common.repository_paths.repository_relative_path"
    source = _function_source(
        "entry",
        _dependency_doc("Resolve one repository-relative input.", calls=(dependency,)),
        """
        from officina.common.repository_paths import repository_relative_path as resolve
        resolve("sample")
        return None
        """,
    )

    issues = _validate_source(tmp_path, source, check_group="behavioral")
    entry_codes = {issue.code for issue in issues if issue.node_id == "entry"}

    assert "docstring.module-dependency-not-observed" not in entry_codes
    assert "docstring.module-dependency-unresolved" not in entry_codes
    assert "docstring.module-dependency-undocumented" not in entry_codes


def test_nested_callable_inherits_enclosing_import_without_parent_absorption(
    tmp_path: Path,
) -> None:
    """Nested closures see enclosing imports while parents ignore nested calls."""
    dependency = "officina.common.repository_paths.repository_relative_path"
    outer_doc = _dependency_doc("Create a nested repository resolver.")
    inner_doc = _dependency_doc("Resolve input inside a nested closure.", calls=(dependency,))
    source = (
        'def outer():\n'
        '    """\n'
        f'{textwrap.indent(outer_doc, "    ")}\n'
        '    """\n'
        '    from officina.common.repository_paths import repository_relative_path as resolve\n'
        '    def inner():\n'
        '        """\n'
        f'{textwrap.indent(inner_doc, "        ")}\n'
        '        """\n'
        '        resolve("sample")\n'
        '        return None\n'
        '    return inner\n'
    )

    issues = _validate_source(tmp_path, source, check_group="behavioral")
    outer_codes = {issue.code for issue in issues if issue.node_id == "outer"}
    inner_codes = {issue.code for issue in issues if issue.node_id == "outer.inner"}

    assert "docstring.module-dependency-undocumented" not in outer_codes
    assert "docstring.module-dependency-not-observed" not in inner_codes
    assert "docstring.module-dependency-unresolved" not in inner_codes


@pytest.mark.parametrize(
    "body",
    (
        "from pathlib import Path as target\ntarget('sample')\nreturn None",
        "target = lambda value: value\ntarget('sample')\nreturn None",
    ),
)
def test_current_scope_bindings_shadow_inherited_repo_aliases(
    tmp_path: Path,
    body: str,
) -> None:
    """A local import or assignment prevents inherited repo-alias classification."""
    source = (
        "from officina.common.repository_paths import repository_relative_path as target\n\n"
        + _function_source(
            "entry",
            _dependency_doc("Use a locally shadowed helper binding."),
            body,
        )
    )

    issues = _validate_source(tmp_path, source, check_group="behavioral")

    assert not any(
        issue.node_id == "entry"
        and issue.code == "docstring.module-dependency-undocumented"
        for issue in issues
    )


def test_sibling_and_class_body_imports_do_not_leak_into_callable_scope(
    tmp_path: Path,
) -> None:
    """Non-lexical imports never make an unrelated callable look repo-dependent."""
    sibling = _function_source(
        "first",
        _dependency_doc("Define a sibling-local repository import."),
        """
        from officina.common.repository_paths import repository_relative_path as leaked
        return None
        """,
    )
    second = _function_source(
        "second",
        _dependency_doc("Call an unresolved sibling name locally."),
        """
        leaked("sample")
        return None
        """,
    )
    method_doc = textwrap.indent(
        _dependency_doc("Call an unresolved class-body name locally."),
        "        ",
    )
    source = (
        sibling
        + "\n"
        + second
        + "\nclass Holder:\n"
        + '    """Hold a complete fixture class.\n\n'
          '    Intent\n    ------\n    Group one non-lexical method fixture.\n\n'
          '    Rationale\n    ---------\n    The class exists to test that class-body imports do not leak into method scope.\n\n'
          '    Pseudocode\n    ----------\n    - return holder interface\n\n'
          '    Wraps\n    -----\n    - none\n    """\n'
        + "    from officina.common.repository_paths import repository_relative_path as leaked\n"
        + "    def method(self):\n"
        + "        \"\"\"\n"
        + method_doc
        + "\n        \"\"\"\n"
        + "        leaked('sample')\n"
        + "        return None\n"
    )

    issues = _validate_source(tmp_path, source, check_group="behavioral")

    assert not any(
        issue.node_id in {"second", "Holder.method"}
        and issue.code == "docstring.module-dependency-undocumented"
        for issue in issues
    )


@pytest.mark.parametrize(
    "expression",
    (
        "consume(produce())",
        "consume(value=produce())",
    ),
)
def test_repo_consumer_arguments_classify_repo_results_as_products(
    tmp_path: Path,
    expression: str,
) -> None:
    """Repo results passed into recognized repo calls are product dependencies."""
    producer = "officina.common.repository_paths.repository_relative_path"
    consumer = "officina.common.repository_paths.normalize_repository_root"
    source = (
        "from officina.common.repository_paths import repository_relative_path as produce\n"
        "from officina.common.repository_paths import normalize_repository_root as consume\n\n"
        + _function_source(
            "entry",
            _dependency_doc(
                "Feed a repository result into a repository consumer.",
                calls=(consumer,),
                products=(producer,),
            ),
            f"{expression}\nreturn None",
        )
    )

    issues = _validate_source(tmp_path, source, check_group="behavioral")
    entry_codes = {issue.code for issue in issues if issue.node_id == "entry"}

    assert "docstring.module-dependency-not-observed" not in entry_codes
    assert "docstring.module-dependency-undocumented" not in entry_codes


@pytest.mark.parametrize(
    ("prefix", "expression"),
    (
        ("", "print(produce())"),
        ("import json\n", "json.dumps(produce())"),
        ("", "external.consume(produce())"),
    ),
)
def test_nonrepo_consumers_do_not_classify_repo_results_as_products(
    tmp_path: Path,
    prefix: str,
    expression: str,
) -> None:
    """Builtin, stdlib, and unknown sinks leave nested repo results as calls."""
    producer = "officina.common.repository_paths.repository_relative_path"
    source = (
        prefix
        + "from officina.common.repository_paths import repository_relative_path as produce\n\n"
        + _function_source(
            "entry",
            _dependency_doc("Pass a repository result to a non-repo sink.", calls=(producer,)),
            f"{expression}\nreturn None",
        )
    )

    issues = _validate_source(tmp_path, source, check_group="behavioral")
    entry_codes = {issue.code for issue in issues if issue.node_id == "entry"}

    assert "docstring.module-dependency-not-observed" not in entry_codes
    assert "docstring.module-dependency-undocumented" not in entry_codes


def test_unknown_uppercase_keyword_consumer_does_not_create_product(
    tmp_path: Path,
) -> None:
    """An unresolved constructor-like sink cannot promote a nested repo result."""
    producer = "officina.common.repository_paths.repository_relative_path"
    source = (
        "from officina.common.repository_paths import repository_relative_path as produce\n\n"
        + _function_source(
            "entry",
            _dependency_doc(
                "Return an unknown wrapper around a repository operation.",
                calls=(producer,),
            ),
            "return External(value=produce())",
        )
    )

    issues = _validate_source(tmp_path, source, check_group="behavioral")
    entry_codes = {issue.code for issue in issues if issue.node_id == "entry"}

    assert "docstring.module-dependency-not-observed" not in entry_codes
    assert "docstring.module-dependency-undocumented" not in entry_codes


def test_runtime_loader_rejects_unsupported_compact_structural_kind(tmp_path: Path) -> None:
    """Runtime parsing fails closed on an unknown compact structural kind."""
    policy_path = tmp_path / "docstring.standard.yaml"
    policy_path.write_text(
        """\
docstring_format_version: 31
name: docstring_format
callable:
  compact_structural_kinds:
    - protocol
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="compact_structural_kinds"):
        load_docstring_schema(policy_path)


def test_missing_no_argument_policy_fallback_exactly_matches_canonical_policy(
    monkeypatch,
) -> None:
    """The built-in compatibility policy has exact canonical runtime parity."""
    canonical = Path(__file__).resolve().parents[1] / "references/standards/docstring.standard.yaml"
    canonical_policy = load_docstring_schema(canonical)
    monkeypatch.setattr(
        docstring_policy,
        "resolve_docstring_schema_path",
        lambda path=None: Path(path) if path is not None else None,
    )

    assert load_docstring_schema() == canonical_policy


def test_explicit_missing_policy_fails_closed(tmp_path: Path) -> None:
    """An explicitly requested policy path cannot silently become built-in fallback."""
    with pytest.raises(FileNotFoundError, match="docstring policy"):
        load_docstring_schema(tmp_path / "missing.yaml")


@pytest.mark.parametrize(
    "policy_text",
    (
        "docstring_format_version: 31\nname: docstring_format\ncallable: []\n",
        "standards: [\n",
        "- not\n- a\n- mapping\n",
    ),
)
def test_existing_canonical_policy_fails_closed_before_fallback(
    tmp_path: Path,
    policy_text: str,
) -> None:
    """Schema-invalid, malformed, and non-mapping canonical files never use fallback."""
    policy_path = tmp_path / "docstring.standard.yaml"
    policy_path.write_text(policy_text, encoding="utf-8")
    source_schema = (
        Path(__file__).resolve().parents[1]
        / "references/standards/docstring_format.schema.json"
    )
    shutil.copy2(source_schema, tmp_path / "docstring_format.schema.json")

    with pytest.raises(ValueError, match="docstring policy"):
        load_docstring_schema(policy_path)


@pytest.mark.parametrize("explicit", (False, True))
def test_schema_invalid_candidate_policy_fails_closed(
    tmp_path: Path,
    monkeypatch,
    explicit: bool,
) -> None:
    """Discovered and explicit candidate standards obey the companion schema."""
    module_path = tmp_path / "src/officina/common/docstring/docstring_policy.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("", encoding="utf-8")
    policy_path = tmp_path / "references/docstring.standard.candidate.yaml"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        "docstring_format_version: 31\nname: docstring_format\ncallable: []\n",
        encoding="utf-8",
    )
    source_schema = (
        Path(__file__).resolve().parents[1]
        / "references/standards/docstring_format.schema.json"
    )
    shutil.copy2(source_schema, policy_path.with_name("docstring_format.schema.json"))
    monkeypatch.setattr(docstring_policy, "__file__", str(module_path))

    with pytest.raises(ValueError, match="docstring policy"):
        load_docstring_schema(policy_path if explicit else None)


def test_explicit_legacy_policy_remains_loadable(tmp_path: Path) -> None:
    """A caller-supplied legacy policy path remains a supported compatibility input."""
    legacy = tmp_path / "docstring_format.yaml"
    legacy.write_text(
        """\
docstring_format_version: 27
name: docstring_format
callable:
  required_sections:
    - Intent
""",
        encoding="utf-8",
    )

    loaded = load_docstring_schema(legacy)

    assert loaded.callable.required_sections == ("Intent",)


def test_implicit_resolution_does_not_autodiscover_legacy_policy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """No-argument resolution ignores a legacy file when canonical files are absent."""
    module_path = tmp_path / "src/officina/common/docstring/docstring_policy.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("", encoding="utf-8")
    legacy = tmp_path / "references/standards/docstring_format.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("name: legacy\n", encoding="utf-8")
    monkeypatch.setattr(docstring_policy, "__file__", str(module_path))

    assert docstring_policy.resolve_docstring_schema_path() is None
