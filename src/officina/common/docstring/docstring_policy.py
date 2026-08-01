#!/usr/bin/env python3
"""Docstring policy loading and repo-local config adaptation.

The standard YAML describes the portable docstring contract. The local
``config.yaml`` describes repo-specific choices such as allowed absolute roots
and dependency section names. This module materializes both into typed objects
used by parser and validator code.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath

import yaml

DOCSTRING_STANDARD_FILE = "docstring.standard.yaml"
DOCSTRING_STANDARD_CANDIDATE_FILE = "docstring.standard.candidate.yaml"
DOCSTRING_LEGACY_FORMAT_FILE = "docstring_format.yaml"
DOCSTRING_CONFIG_FILE = "config.yaml"

_ALLOWED_PROFILE_CHECKS: frozenset[str] = frozenset(
    {
        "dependency_why_action",
        "instantiation_product_pseudocode",
        "pseudocode_dataflow",
        "pseudocode_output_use",
        "repeated_template_detection",
    }
)
_ALLOWED_PROFILE_CALLABLE_KEYS: frozenset[str] = frozenset(
    {
        "require_docstrings",
        "required_sections",
        "min_pseudocode_steps",
    }
)


@dataclass(frozen=True)
class DependencySectionNames:
    """Configured names for graphable dependency sections.

    Intent
    ------
    Keep the repo's public dependency-section spelling in one typed configuration
    record.

    Rationale
    ---------
    Section names are naming policy, not parser code; storing them here lets config
    rename dependency sections without changing validators.

    Pseudocode
    ----------
    - set dependency_section_contract = calls instantiations and dispatches names
    - return dependency_section_contract

    Wraps
    -----
    - none
    """

    calls: str = "CallsFromRepo"
    instantiations: str = "InstantiationsFromRepo"
    dispatches: str = "Dispatches"


@dataclass(frozen=True)
class DependencySyntaxConfig:
    """Policy switches for dependency section syntax.

    Intent
    ------
    Record whether dependency sections require structured why entries and whether
    legacy flat syntax is still accepted.

    Rationale
    ---------
    Syntax migration decisions belong in declarative policy so validators can enforce
    the current standard without hard-coded repository choices.

    Pseudocode
    ----------
    - set dependency_syntax_contract = legacy and rationale flags
    - return dependency_syntax_contract

    Wraps
    -----
    - none
    """

    allow_legacy_flat: bool = False
    require_why: bool = True


@dataclass(frozen=True)
class DependencyWhyConfig:
    """Allowed action-key policy for dependency rationale text.

    Intent
    ------
    Define the graphable verbs accepted under dependency `why` entries and the
    minimum detail required for miscellaneous rationales.

    Rationale
    ---------
    Typed action keys make edge labels extractable while still allowing a constrained
    escape hatch for unusual dependencies.

    Pseudocode
    ----------
    - set dependency_why_contract = actions legacy flag and misc length
    - return dependency_why_contract

    Wraps
    -----
    - none
    """

    actions: tuple[str, ...] = (
        "reads",
        "writes",
        "transforms",
        "validates",
        "constructs",
        "dispatches",
        "serializes",
        "parses",
        "computes",
        "orchestrates",
        "raises",
        "misc",
    )
    section_actions: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "calls": (
                "reads",
                "writes",
                "validates",
                "parses",
                "computes",
                "orchestrates",
                "transforms",
                "dispatches",
                "serializes",
                "misc",
            ),
            "instantiations": (
                "constructs",
                "raises",
                "transforms",
                "serializes",
                "misc",
            ),
            "dispatches": ("dispatches",),
        }
    )
    allow_legacy_string: bool = False
    misc_min_chars: int = 80


@dataclass(frozen=True)
class PseudocodeQualityConfig:
    """Mechanical quality policy for strict pseudocode blocks.

    Intent
    ------
    Store forbidden generic variable names and output-use requirements for dependency
    markers in pseudocode.

    Rationale
    ---------
    Pseudocode is intended to support flow extraction, so generic data names and dead
    assigned outputs need configurable checks.

    Pseudocode
    ----------
    - set pseudocode_quality_contract = forbidden names and output-use flag
    - return pseudocode_quality_contract

    Wraps
    -----
    - none
    """

    forbidden_variables: tuple[str, ...] = ("value", "out", "state", "args", "data")
    require_assigned_dependency_output_use: bool = False


@dataclass(frozen=True)
class RepeatedTemplateConfig:
    """Policy for detecting repeated boilerplate docstring templates.

    Intent
    ------
    Configure when normalized repeated prose should be reported as low-information
    documentation.

    Rationale
    ---------
    Generated docs can pass structural checks while still saying the same thing many
    times; this policy lets production profiles reject that pattern.

    Pseudocode
    ----------
    - set repeated_template_contract = enabled flag repetition count and text length
    - return repeated_template_contract

    Wraps
    -----
    - none
    """

    enabled: bool = False
    min_repetitions: int = 3
    min_normalized_chars: int = 40


@dataclass(frozen=True)
class DocstringProfileConfig:
    """Path-scoped validation profile loaded from repo config.

    Intent
    ------
    Bind path patterns to checker toggles and callable docstring requirements.

    Rationale
    ---------
    Production code, tests, and generated artifacts need different strictness while
    sharing the same parser and validator implementation.

    Pseudocode
    ----------
    - set profile_contract = name path patterns checks and callable overrides
    - return profile_contract

    Wraps
    -----
    - none
    """

    name: str
    applies_to: tuple[str, ...] = ()
    checks: dict[str, bool] = field(default_factory=dict)
    callable_require_docstrings: bool | None = None
    callable_required_sections: tuple[str, ...] | None = None
    callable_min_pseudocode_steps: int | None = None


@dataclass(frozen=True)
class DocstringRuntimeConfig:
    """Repository-local docstring configuration after YAML parsing.

    Intent
    ------
    Collect allowed absolute roots, dependency section names, syntax switches,
    quality policy, ownership policy, and profiles.

    Rationale
    ---------
    The runtime config is the repository-specific layer applied over the portable
    standard before validators run.

    Pseudocode
    ----------
    - set runtime_config_contract = repo overrides and profile declarations
    - return runtime_config_contract

    Wraps
    -----
    - none
    """

    allowed_abs: tuple[str, ...] = ("officina", "skills")
    names_for_dependency_sections: DependencySectionNames = field(
        default_factory=DependencySectionNames
    )
    dependency_syntax: DependencySyntaxConfig = field(
        default_factory=DependencySyntaxConfig
    )
    dependency_why: DependencyWhyConfig = field(default_factory=DependencyWhyConfig)
    pseudocode_quality: PseudocodeQualityConfig = field(default_factory=PseudocodeQualityConfig)
    repeated_template_detection: RepeatedTemplateConfig = field(default_factory=RepeatedTemplateConfig)
    profiles: tuple[DocstringProfileConfig, ...] = ()


@dataclass(frozen=True)
class OwnershipConfig:
    """Callable ownership policy loaded from the standard or repo config.

    Intent
    ------
    Describe which docstring section declares ownership and how ownership ids are
    resolved for callable-level semantic responsibility checks.

    Rationale
    ---------
    Ownership policy is separate from dependency policy because an owner records who
    is responsible for behavior, not which repo symbol is called.

    Pseudocode
    ----------
    - set ownership_contract = section resolution and multiplicity flags
    - return ownership_contract

    Wraps
    -----
    - none
    """

    section: str = "Owns"
    section_required: bool = False
    allows_multiple: bool = False
    single_owner_per_callable: bool = True
    owner_resolution: str = "module:Ownable"
    owner_section: str = "Ownable"
    cross_file_enabled: bool = False


@dataclass(frozen=True)
class ModuleOwnershipConfig:
    """Module-level registry policy for declared ownable capabilities.

    Intent
    ------
    Store the section name and multiplicity rule for module docstring ownership
    registries.

    Rationale
    ---------
    Callable ownership checks need a stable registry shape before they can validate
    whether an `Owns` entry resolves.

    Pseudocode
    ----------
    - set module_ownership_contract = registry section and multiplicity flag
    - return module_ownership_contract

    Wraps
    -----
    - none
    """

    section: str = "Ownable"
    allows_multiple: bool = True


@dataclass(frozen=True)
class PseudocodeDocstringSchema:
    """Policy for the callable Pseudocode section.

    Intent
    ------
    Define the Pseudocode section name, step limits, and strict structured-step
    requirements used by parser checks.

    Rationale
    ---------
    Pseudocode is the bridge from prose to flow extraction, so its policy must be
    explicit about minimum structure and maximum verbosity.

    Pseudocode
    ----------
    - set pseudocode_policy_contract = section name step limits and strict flag
    - return pseudocode_policy_contract

    Wraps
    -----
    - none
    """

    section: str = "Pseudocode"
    min_steps: int = 1
    max_steps: int = 16
    max_step_chars: int = 140
    max_total_chars: int = 640

    def section_names(self) -> tuple[str, ...]:
        """Return section names required by pseudocode policy.

        Intent
        ------
        Expose the configured Pseudocode section name as a tuple for generic section
        validators.

        Rationale
        ---------
        Validators consume every policy object through the same section_names interface,
        so this method adapts a single section field to that common contract.

        Pseudocode
        ----------
        - return pseudocode section name tuple

        Wraps
        -----
        - none
        """
        return (self.section,)


@dataclass(frozen=True)
class CallableDocstringSchema:
    """Policy for function, method, and class docstrings.

    Intent
    ------
    Define required narrative sections, summary/rationale length limits, ownership
    rules, and graphable dependency syntax for callable docs.

    Rationale
    ---------
    Callable docstrings are the main source for codebase graphs, so their policy
    combines human explanation requirements with extractable dependency metadata.

    Pseudocode
    ----------
    - set callable_policy_contract = narrative sections dependency policy and ownership policy
    - return callable_policy_contract

    Wraps
    -----
    - none
    """

    require_docstrings: bool = True
    required_summary: bool = True
    required_sections: tuple[str, ...] = ("Intent", "Rationale")
    summary_min_chars: int = 20
    summary_max_chars: int = 240
    rationale_min_chars: int = 45
    rationale_max_chars: int = 1200
    optional_sections: tuple[str, ...] = (
        "Graph",
        "Role",
        "Phase",
        "NonInferableCalls",
        "Wraps",
        "Owns",
        "Resources",
        "Dataflow",
    )
    pseudocode: PseudocodeDocstringSchema = field(default_factory=PseudocodeDocstringSchema)
    dependency_reference_sections: tuple[str, ...] = ()
    ownership: OwnershipConfig = field(default_factory=OwnershipConfig)
    min_pseudocode_steps: int = 1
    forbidden_summary_phrases: tuple[str, ...] = (
        "what does this do",
        "todo",
        "tbd",
        "placeholder",
        "implement me",
        "to be implemented",
        "documents the certifier control point",
        "repo-visible dependencies",
        "performs or represents the certification operation described by its inputs and outputs",
        "operation described by its inputs and outputs",
        "data carried between certification stages",
        "handles certification data or control flow for this module",
    )
    forbidden_intent_phrases: tuple[str, ...] = (
        "dependency declarations that the docstring graph can parse",
        "docstring graph can parse",
        "static dependency validation",
        "compact docstrings whose declared calls and product dependencies stay aligned",
        "operation described by its inputs and outputs",
        "data carried between certification stages",
        "boundary keeps its certification responsibility separate from adjacent repository steps",
    )
    forbidden_rationale_phrases: tuple[str, ...] = (
        "this callable exists to implement the behavior described in its contract documentation",
        "this callable exists to implement the behavior described in the contract documentation",
        "implemented as described in the contract documentation",
        "implemented as described in contract documentation",
        "dependency declarations that the docstring graph can parse",
        "docstring graph can parse",
        "static dependency validation",
        "compact docstrings whose declared calls and product dependencies stay aligned",
        "operation described by its inputs and outputs",
        "combines repository evidence and helper results to produce or validate certification state",
        "keeps repository evidence checks separate from payload assembly and signing",
    )
    forbidden_pseudocode_phrases: tuple[str, ...] = (
        "execute the implementation steps described in source code",
        "implementation steps described in source code",
        "same as source code",
        "return result",
        "immutable_record = constructor_fields",
        "value = @",
        "out = @",
        "(args)",
        "(state)",
    )

    def section_names(self) -> tuple[str, ...]:
        """Return callable section names controlled by callable policy.

        Intent
        ------
        Combine required, optional, pseudocode, and ownership sections into one tuple for
        callable docstring parsing.

        Rationale
        ---------
        The parser needs one ordered set of section headers even though the policy stores
        those headers by responsibility.

        Pseudocode
        ----------
        - set callable_sections = required optional pseudocode ownership and dependencies
        - return callable_sections

        Wraps
        -----
        - none
        """
        sections: list[str] = []
        sections.extend(self.required_sections)
        sections.extend(self.optional_sections)
        sections.extend(self.pseudocode.section_names())
        sections.extend(self.dependency_reference_sections)
        return tuple(dict.fromkeys(sections).keys())


@dataclass(frozen=True)
class ModuleDependencyConfig:
    """Policy for graphable repo dependency declarations.

    Intent
    ------
    Configure call, product, dispatch, resource, wrapper, and dataflow sections plus
    repo-local path and rationale requirements.

    Rationale
    ---------
    Dependency sections drive graph edges, so their policy must distinguish operation
    calls from carried products and non-Python dispatch interfaces.

    Pseudocode
    ----------
    - set dependency_policy_contract = section names scope rules and quality checks
    - return dependency_policy_contract

    Wraps
    -----
    - none
    """

    calls_section: str = "CallsFromRepo"
    instantiates_section: str = "InstantiationsFromRepo"
    dispatches_section: str = "Dispatches"
    allow_implicit: bool = True
    allow_legacy_flat: bool = False
    require_why: bool = True
    dependency_why: DependencyWhyConfig = field(default_factory=DependencyWhyConfig)
    pseudocode_quality: PseudocodeQualityConfig = field(default_factory=PseudocodeQualityConfig)
    repeated_template_detection: RepeatedTemplateConfig = field(default_factory=RepeatedTemplateConfig)
    allowed_abs: tuple[str, ...] = ("officina", "skills")
    forbidden_why_phrases: tuple[str, ...] = (
        "observed call dependency",
        "observed product dependency",
        "observed dependency",
        "docstring graph can parse",
        "repo-visible dependencies",
        "static dependency validation",
        "provides the named helper operation needed by function",
        "supplies the dependency result used by function",
        "to complete its certification step",
        "creates the value or error object required by function",
        "creates the value or error object required by method",
        "creates the typed object needed to carry this operation's structured result or failure",
        "checks the relevant certification invariant before the next phase proceeds",
    )
    validate_declared_calls: bool = True
    validate_declared_instantiations: bool = True
    validate_declared_dispatches: bool = True
    report_unlisted_calls: bool = False
    report_unlisted_instantiations: bool = False
    ignore_non_external: bool = True
    validate_dependency_targets_resolved: bool = False
    require_repo_dependency_targets: bool = False
    enforce_declared_dependency_pseudocode_coverage: bool = False
    enforce_observed_dependency_pseudocode_coverage: bool = False

    def section_names(self) -> tuple[str, ...]:
        """Return graphable dependency section names.

        Intent
        ------
        Expose CallsFromRepo, InstantiationsFromRepo, Dispatches, Resources, and Dataflow
        names through one policy method.

        Rationale
        ---------
        Dependency validators and parsers need the configured section names without
        knowing how the dependency policy object stores each category.

        Pseudocode
        ----------
        - set dependency_sections = call product dispatch resource and dataflow names
        - return dependency_sections

        Wraps
        -----
        - none
        """
        return (
            self.calls_section,
            self.instantiates_section,
            self.dispatches_section,
        )


@dataclass(frozen=True)
class PipelineDocstringSchema:
    """Policy for graph-pipeline docstrings.

    Intent
    ------
    Define required pipeline narrative sections and optional ownership/resource/data
    sections for graph specifications.

    Rationale
    ---------
    Pipeline documentation has different structure from callable docs but still needs
    the same extractable graph vocabulary.

    Pseudocode
    ----------
    - set pipeline_policy_contract = pipeline sections resources and ownership
    - return pipeline_policy_contract

    Wraps
    -----
    - none
    """

    required: bool = False
    required_sections: tuple[str, ...] = ("Phases", "PhaseMembers")
    optional_sections: tuple[str, ...] = ("PhaseEdges", "NonInferableCalls", "Description")

    def section_names(self) -> tuple[str, ...]:
        """Return section names accepted for pipeline docstrings.

        Intent
        ------
        Merge required pipeline sections with optional ownership, resource, and dataflow
        sections.

        Rationale
        ---------
        Pipeline docstrings share graph concepts with callables but use a distinct set of
        required narrative sections.

        Pseudocode
        ----------
        - set pipeline_sections = required optional ownership resource and dataflow names
        - return pipeline_sections

        Wraps
        -----
        - none
        """
        return self.required_sections + self.optional_sections


@dataclass(frozen=True)
class ModuleDocstringSchema:
    """Policy for module-level docstrings.

    Intent
    ------
    Define module summary requirements, allowed module sections, and the ownable
    registry section.

    Rationale
    ---------
    Module docs establish the local vocabulary that callable ownership declarations
    can reference.

    Pseudocode
    ----------
    - set module_policy_contract = module sections and registry policy
    - return module_policy_contract

    Wraps
    -----
    - none
    """

    required: bool = False
    required_summary: bool = True
    required_sections: tuple[str, ...] = ("Includes",)
    optional_sections: tuple[str, ...] = ("Workflow", "Traces", "Description", "Ownable")
    ownership_registry: ModuleOwnershipConfig = field(default_factory=ModuleOwnershipConfig)

    def section_names(self) -> tuple[str, ...]:
        """Return section names accepted for module docstrings.

        Intent
        ------
        Expose module-level required, optional, and ownership-registry sections as one
        header list.

        Rationale
        ---------
        Module checks need a complete section list to distinguish supported headers from
        unknown headings.

        Pseudocode
        ----------
        - set module_sections = required optional and ownership-registry names
        - return module_sections

        Wraps
        -----
        - none
        """
        return self.required_sections + self.optional_sections


@dataclass(frozen=True)
class DocstringSchema:
    """Complete docstring policy materialized from standard and config.

    Intent
    ------
    Bundle callable, pipeline, module, dependency, and ownership policy into the
    single object consumed by parsers and validators.

    Rationale
    ---------
    One policy object keeps the parser, local syntax checker, behavioral checker, and
    documentation examples tied to the same active standard.

    Pseudocode
    ----------
    - set docstring_policy_contract = callable pipeline module and dependency policy
    - return docstring_policy_contract

    Wraps
    -----
    - none
    """

    name: str = "docstring_policy"
    strict: bool = True
    callable: CallableDocstringSchema = field(default_factory=CallableDocstringSchema)
    module_dependencies: ModuleDependencyConfig = field(default_factory=ModuleDependencyConfig)
    config: DocstringRuntimeConfig = field(default_factory=DocstringRuntimeConfig)
    pipeline: PipelineDocstringSchema = field(default_factory=PipelineDocstringSchema)
    module: ModuleDocstringSchema = field(default_factory=ModuleDocstringSchema)

    def section_names(self) -> tuple[str, ...]:
        """Return every section name recognized by the active docstring policy.

        Intent
        ------
        Combine callable, pipeline, module, dependency, ownership, resource, and dataflow
        section names into one de-duplicated tuple.

        Rationale
        ---------
        Top-level parsing and validation need the full header vocabulary independent of
        which specific document profile is being checked.

        Pseudocode
        ----------
        - set all_sections = union of child policy section names
        - return all_sections

        Wraps
        -----
        - none
        """
        return (
            self.callable.section_names()
            + self.module_dependencies.section_names()
            + self.pipeline.section_names()
            + self.module.section_names()
        )


DocstringPolicy = DocstringSchema
CallableDocstringPolicy = CallableDocstringSchema


def _safe_bool(value: object, fallback: bool = False) -> bool:
    """Coerce common YAML boolean spellings while preserving a fallback.

    Intent
    ------
    Expose the safe bool step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps safe bool behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set safe_bool_inputs = received_context
    - return safe_bool_inputs

    Wraps
    -----
    - none
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes", "on"}
    return fallback


def _safe_str(value: object, fallback: str) -> str:
    """Return a string config value, or the provided fallback.

    Intent
    ------
    Expose the safe str step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps safe str behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set safe_str_inputs = received_context
    - return safe_str_inputs

    Wraps
    -----
    - none
    """
    if isinstance(value, str):
        return value
    return fallback


def _safe_str_tuple(values: object, fallback: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Coerce YAML string lists into a tuple while dropping non-string items.

    Intent
    ------
    Expose the safe str tuple step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps safe str tuple behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set safe_str_tuple_inputs = received_context
    - return safe_str_tuple_inputs

    Wraps
    -----
    - none
    """
    if values is None:
        return fallback
    if isinstance(values, tuple):
        return tuple(item for item in values if isinstance(item, str))
    if isinstance(values, list):
        return tuple(item for item in values if isinstance(item, str))
    return fallback


def _safe_int(value: object, fallback: int = 0) -> int:
    """Coerce non-negative integer config values while rejecting booleans.

    Intent
    ------
    Expose the safe int step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps safe int behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set safe_int_inputs = received_context
    - return safe_int_inputs

    Wraps
    -----
    - none
    """
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value.strip()))
    return fallback


def _safe_check_codes(values: object) -> tuple[str, ...]:
    """Normalize check-category entries into unique issue-code strings.

    Intent
    ------
    Expose the safe check codes step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps safe check codes behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set safe_check_codes_inputs = received_context
    - set safe_check_codes_effects = local_decisions
    - return safe_check_codes_effects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._safe_str:
      why:
        computes: "safe str supplies repo-local behavior used by safe check codes; this edge is documented from an observed call in the body."
    """
    if values is None:
        return ()
    if isinstance(values, tuple):
        raw_items = values
    elif isinstance(values, list):
        raw_items = tuple(values)
    else:
        return ()

    normalized: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            code = item.strip()
        elif isinstance(item, dict):
            code = _safe_str(item.get("code"), "").strip()
        else:
            continue
        if code:
            normalized.append(code)
    return tuple(dict.fromkeys(normalized))


def _safe_bool_mapping(values: object) -> dict[str, bool]:
    """Normalize a YAML mapping whose values are boolean-like toggles.

    Intent
    ------
    Expose the safe bool mapping step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps safe bool mapping behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set safe_bool_mapping_inputs = received_context
    - set safe_bool_mapping_products = carried_outputs
    - return safe_bool_mapping_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._safe_bool:
      why:
        constructs: "safe bool produces a value carried by safe bool mapping; this edge is documented from the observed product position in the body."
    """
    if not isinstance(values, dict):
        return {}
    normalized: dict[str, bool] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            continue
        name = key.strip()
        if name:
            normalized[name] = _safe_bool(value, False)
    return normalized


def _parse_profile_configs(values: object) -> tuple[DocstringProfileConfig, ...]:
    """Parse configured docstring profiles from YAML values.

    Intent
    ------
    Convert path-profile declarations into typed profile records and reject unknown
    profile check keys early.

    Rationale
    ---------
    Profile parsing is the boundary where repository-specific strictness becomes a
    validated policy object rather than ad hoc checker state.

    Pseudocode
    ----------
    - if values is not a mapping:
      - return
    - for raw_name in profile_entries:
      - path_patterns = ._safe_str_tuple(profile_paths)
      - check_flags = ._safe_bool_mapping(profile_checks)
      - min_steps = @._safe_int(profile_step_limit)
      - required_docs = @._safe_bool(profile_docstring_flag)
      - profile_config = DocstringProfileConfig(path_patterns, check_flags)
    - set profile_config_inputs = min_steps plus required_docs plus profile_config
    - return profile_config_inputs

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._safe_bool:
      why:
        computes: "Normalizes optional require-docstrings flags for profile overrides."
    ._safe_int:
      why:
        computes: "Normalizes optional pseudocode-step limits for profile overrides."
    ._safe_str_tuple:
      why:
        computes: "Normalizes YAML section lists that are read but not carried directly."

    InstantiationsFromRepo
    ----------------------
    ._safe_str_tuple:
      why:
        constructs: "Builds immutable path and section tuples stored on profile records."
    ._safe_bool_mapping:
      why:
        constructs: "Builds normalized profile check flags from YAML mapping values."
    .DocstringProfileConfig:
      why:
        constructs: "Builds typed profile records consumed by policy materialization."
    """
    if not isinstance(values, dict):
        return ()

    profiles: list[DocstringProfileConfig] = []
    for raw_name, raw_profile in values.items():
        if not isinstance(raw_name, str) or not isinstance(raw_profile, dict):
            continue
        name = raw_name.strip()
        if not name:
            continue
        raw_applies_to = raw_profile.get("applies_to")
        if not isinstance(raw_applies_to, (list, tuple)):
            raise ValueError(f"Docstring profile '{name}' applies_to must be a list of strings.")
        if not all(isinstance(item, str) and item.strip() for item in raw_applies_to):
            raise ValueError(f"Docstring profile '{name}' applies_to entries must be non-empty strings.")

        checks = _safe_bool_mapping(raw_profile.get("checks"))
        unknown_checks = sorted(set(checks) - _ALLOWED_PROFILE_CHECKS)
        if unknown_checks:
            raise ValueError(
                f"Docstring profile '{name}' has unknown profile check key(s): "
                f"{', '.join(unknown_checks)}."
            )

        callable_values = raw_profile.get("callable", {})
        if not isinstance(callable_values, dict):
            callable_values = {}
        unknown_callable_keys = sorted(set(callable_values) - _ALLOWED_PROFILE_CALLABLE_KEYS)
        if unknown_callable_keys:
            raise ValueError(
                f"Docstring profile '{name}' has unknown callable key(s): "
                f"{', '.join(unknown_callable_keys)}."
            )
        min_steps_value = callable_values.get("min_pseudocode_steps")
        profiles.append(
            DocstringProfileConfig(
                name=name,
                applies_to=_safe_str_tuple(raw_applies_to, ()),
                checks=checks,
                callable_require_docstrings=(
                    _safe_bool(
                        callable_values.get("require_docstrings"),
                        True,
                    )
                    if "require_docstrings" in callable_values
                    else None
                ),
                callable_required_sections=(
                    _safe_str_tuple(callable_values.get("required_sections"), ())
                    if "required_sections" in callable_values
                    else None
                ),
                callable_min_pseudocode_steps=(
                    _safe_int(min_steps_value, 0)
                    if min_steps_value is not None
                    else None
                ),
            )
        )
    return tuple(profiles)


def _load_yaml(path: Path) -> object:
    """Load a YAML document, returning ``None`` when the file is unreadable.

    Intent
    ------
    Expose the load yaml step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps load yaml behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set load_yaml_inputs = received_context
    - return load_yaml_inputs

    Wraps
    -----
    - none
    """
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _parse_ownership_config(
    value: object,
    default: OwnershipConfig,
) -> OwnershipConfig:
    """Parse callable ownership configuration while preserving defaults.

    Intent
    ------
    Read the ownership section from repository YAML and merge present values over the
    standard defaults.

    Rationale
    ---------
    Ownership settings affect semantic-responsibility checks, so malformed YAML must
    fall back predictably instead of leaking partial configuration into validators.

    Pseudocode
    ----------
    - if ownership_mapping is not a mapping:
      - return default
    - owner_section = ._safe_str(ownership_section_text)
    - cross_file_enabled = ._safe_bool(cross_file_flag)
    - ownership_config = OwnershipConfig(owner_section, cross_file_enabled)
    - return ownership_config

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._safe_bool:
      why:
        computes: "Reads boolean ownership flags while preserving configured defaults."

    InstantiationsFromRepo
    ----------------------
    ._safe_str:
      why:
        constructs: "Builds normalized ownership section names from YAML values."
    ._safe_bool:
      why:
        constructs: "Builds boolean ownership flags stored on the resulting policy object."
    .OwnershipConfig:
      why:
        constructs: "Builds the typed ownership policy embedded in the docstring policy."
    """
    if not isinstance(value, dict):
        return default

    owner_resolution = _safe_str(value.get("owner_resolution"), default.owner_resolution)
    owner_section = default.owner_section
    if ":" in owner_resolution:
        owner_section = owner_resolution.split(":", 1)[1].strip() or default.owner_section

    cross_file = value.get("cross_file", {})
    cross_file_enabled = (
        _safe_bool(cross_file.get("enabled"), default.cross_file_enabled)
        if isinstance(cross_file, dict)
        else False
    )

    return OwnershipConfig(
        section=_safe_str(value.get("section"), default.section),
        section_required=_safe_bool(value.get("section_required"), default.section_required),
        allows_multiple=_safe_bool(value.get("allows_multiple"), default.allows_multiple),
        single_owner_per_callable=_safe_bool(
            value.get("single_owner_per_callable"),
            default.single_owner_per_callable,
        ),
        owner_resolution=owner_resolution,
        owner_section=owner_section,
        cross_file_enabled=cross_file_enabled,
    )


def _parse_module_ownership_config(
    value: object,
    default: ModuleOwnershipConfig,
) -> ModuleOwnershipConfig:
    """Parse module-level ownership registry policy from YAML.

    Intent
    ------
    Expose the parse module ownership config step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps parse module ownership config behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set parse_module_ownership_config_inputs = received_context
    - set parse_module_ownership_config_products = carried_outputs
    - return parse_module_ownership_config_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .ModuleOwnershipConfig:
      why:
        constructs: "ModuleOwnershipConfig produces a value carried by parse module ownership config; this edge is documented from the observed product position in the body."
    ._safe_bool:
      why:
        constructs: "safe bool produces a value carried by parse module ownership config; this edge is documented from the observed product position in the body."
    ._safe_str:
      why:
        constructs: "safe str produces a value carried by parse module ownership config; this edge is documented from the observed product position in the body."
    """
    if not isinstance(value, dict):
        return default
    return ModuleOwnershipConfig(
        section=_safe_str(value.get("section"), default.section),
        allows_multiple=_safe_bool(value.get("allows_multiple"), default.allows_multiple),
    )


def _default_docstring_schema() -> DocstringSchema:
    """Return the built-in policy after applying repo-local config.

    Intent
    ------
    Expose the default docstring schema step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps default docstring schema behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set default_docstring_schema_inputs = received_context
    - set default_docstring_schema_products = carried_outputs
    - return default_docstring_schema_products

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .DocstringSchema:
      why:
        computes: "DocstringSchema supplies repo-local behavior used by default docstring schema; this edge is documented from an observed call in the body."
    .load_docstring_config:
      why:
        reads: "load docstring config supplies repo-local behavior used by default docstring schema; this edge is documented from an observed call in the body."

    InstantiationsFromRepo
    ----------------------
    .apply_config_to_policy:
      why:
        transforms: "apply config to policy produces a value carried by default docstring schema; this edge is documented from the observed product position in the body."
    """
    return apply_config_to_policy(DocstringSchema(), load_docstring_config())


def resolve_docstring_schema_path(path: str | Path | None = None) -> Path | None:
    """Find the portable docstring standard file from an explicit or repo-relative path.

    Intent
    ------
    Expose the resolve docstring schema path step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps resolve docstring schema path behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set resolve_docstring_schema_path_inputs = received_context
    - return resolve_docstring_schema_path_inputs

    Wraps
    -----
    - none
    """
    if path is not None:
        return Path(path)

    def _resolve_candidates(base: Path) -> list[Path]:
        """_resolve_candidates supports portable standard loading and repo-profile policy materialization as a documented callable boundary.

        Intent
        ------
        Expose the resolve candidates step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

        Rationale
        ---------
        This boundary keeps resolve candidates behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

        Pseudocode
        ----------
        - set resolve_candidates_inputs = received_context
        - return resolve_candidates_inputs

        Wraps
        -----
        - none
        """
        return [
            base / DOCSTRING_STANDARD_FILE,
            base / DOCSTRING_STANDARD_CANDIDATE_FILE,
            base / DOCSTRING_LEGACY_FORMAT_FILE,
            base / "standards" / DOCSTRING_STANDARD_FILE,
            base / "standards" / DOCSTRING_STANDARD_CANDIDATE_FILE,
            base / "standards" / DOCSTRING_LEGACY_FORMAT_FILE,
        ]

    module_path = Path(__file__).resolve()
    for parent in (module_path, *module_path.parents):
        for candidate in _resolve_candidates(parent / "references"):
            if candidate.exists():
                return candidate
    return None


def resolve_docstring_config_path(path: str | Path | None = None) -> Path:
    """Resolve the repo-local docstring config file path.

    Intent
    ------
    Expose the resolve docstring config path step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps resolve docstring config path behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set resolve_docstring_config_path_inputs = received_context
    - return resolve_docstring_config_path_inputs

    Wraps
    -----
    - none
    """
    if path is not None:
        return Path(path)
    return Path(__file__).resolve().with_name(DOCSTRING_CONFIG_FILE)


def load_docstring_config(path: str | Path | None = None) -> DocstringRuntimeConfig:
    """Load repository docstring configuration from config.yaml.

    Intent
    ------
    Read the optional repo config file and convert it into a typed runtime config
    used to customize the portable standard.

    Rationale
    ---------
    Repository-specific roots, section names, rationale actions, quality checks, and
    profiles should be editable in YAML rather than Python code.

    Pseudocode
    ----------
    - config_path = .resolve_docstring_config_path(optional_config_path)
    - if config_path is missing:
      - return default runtime config
    - config_values = ._load_yaml(config_path)
    - runtime_config = DocstringRuntimeConfig(config_values)
    - return runtime_config

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .resolve_docstring_config_path:
      why:
        constructs: "Builds the repository config path searched by the loader."
    ._load_yaml:
      why:
        constructs: "Builds the raw YAML mapping used to configure docstring policy."
    .DocstringRuntimeConfig:
      why:
        constructs: "Builds the typed runtime configuration returned to policy loading."

    .DependencySectionNames:
      why:
        constructs: "Builds configured dependency-section names from names_for_dependency_sections."
    .DependencySyntaxConfig:
      why:
        constructs: "Builds syntax toggles that decide whether tree syntax and why mappings are mandatory."
    .DependencyWhyConfig:
      why:
        constructs: "Builds the allowed action-key vocabulary for dependency rationale blocks."
    .PseudocodeQualityConfig:
      why:
        constructs: "Builds pseudocode dataflow checks such as forbidden generic variables."
    .RepeatedTemplateConfig:
      why:
        constructs: "Builds boilerplate-detection thresholds for profile-driven quality checks."
    ._parse_profile_configs:
      why:
        constructs: "Builds the parse_profile_configs contribution used by load_docstring_config."
    ._safe_bool:
      why:
        constructs: "Builds the safe_bool contribution used by load_docstring_config."
    ._safe_int:
      why:
        constructs: "Builds the safe_int contribution used by load_docstring_config."
    ._safe_str:
      why:
        constructs: "Builds the safe_str contribution used by load_docstring_config."
    ._safe_str_tuple:
      why:
        constructs: "Builds the safe_str_tuple contribution used by load_docstring_config."
    """
    config_path = resolve_docstring_config_path(path)
    config_value = _load_yaml(config_path)
    default = DocstringRuntimeConfig()
    if not isinstance(config_value, dict):
        return default

    section_values = config_value.get("names_for_dependency_sections", {})
    section_defaults = default.names_for_dependency_sections
    if not isinstance(section_values, dict):
        section_values = {}

    syntax_values = config_value.get("dependency_syntax", {})
    syntax_defaults = default.dependency_syntax
    if not isinstance(syntax_values, dict):
        syntax_values = {}
    why_values = config_value.get("dependency_why", {})
    why_defaults = default.dependency_why
    if not isinstance(why_values, dict):
        why_values = {}
    section_action_values = why_values.get("section_actions", {})
    section_actions = dict(why_defaults.section_actions)
    if isinstance(section_action_values, dict):
        for raw_key, raw_actions in section_action_values.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                continue
            key = raw_key.strip()
            section_actions[key] = _safe_str_tuple(
                raw_actions,
                section_actions.get(key, ()),
            )
    pseudocode_values = config_value.get("pseudocode_quality", {})
    pseudocode_defaults = default.pseudocode_quality
    if not isinstance(pseudocode_values, dict):
        pseudocode_values = {}
    repeated_template_values = config_value.get("repeated_template_detection", {})
    repeated_template_defaults = default.repeated_template_detection
    if not isinstance(repeated_template_values, dict):
        repeated_template_values = {}

    return DocstringRuntimeConfig(
        allowed_abs=_safe_str_tuple(config_value.get("allowed_abs"), default.allowed_abs),
        names_for_dependency_sections=DependencySectionNames(
            calls=_safe_str(section_values.get("calls"), section_defaults.calls),
            instantiations=_safe_str(
                section_values.get("instantiations"),
                section_defaults.instantiations,
            ),
            dispatches=_safe_str(
                section_values.get("dispatches"),
                section_defaults.dispatches,
            ),
        ),
        dependency_syntax=DependencySyntaxConfig(
            allow_legacy_flat=_safe_bool(
                syntax_values.get("allow_legacy_flat"),
                syntax_defaults.allow_legacy_flat,
            ),
            require_why=_safe_bool(
                syntax_values.get("require_why"),
                syntax_defaults.require_why,
            ),
        ),
        dependency_why=DependencyWhyConfig(
            actions=_safe_str_tuple(why_values.get("actions"), why_defaults.actions),
            section_actions=section_actions,
            allow_legacy_string=_safe_bool(
                why_values.get("allow_legacy_string"),
                why_defaults.allow_legacy_string,
            ),
            misc_min_chars=_safe_int(
                why_values.get("misc_min_chars"),
                why_defaults.misc_min_chars,
            ),
        ),
        pseudocode_quality=PseudocodeQualityConfig(
            forbidden_variables=_safe_str_tuple(
                pseudocode_values.get("forbidden_variables"),
                pseudocode_defaults.forbidden_variables,
            ),
            require_assigned_dependency_output_use=_safe_bool(
                pseudocode_values.get("require_assigned_dependency_output_use"),
                pseudocode_defaults.require_assigned_dependency_output_use,
            ),
        ),
        repeated_template_detection=RepeatedTemplateConfig(
            enabled=_safe_bool(
                repeated_template_values.get("enabled"),
                repeated_template_defaults.enabled,
            ),
            min_repetitions=_safe_int(
                repeated_template_values.get("min_repetitions"),
                repeated_template_defaults.min_repetitions,
            ),
            min_normalized_chars=_safe_int(
                repeated_template_values.get("min_normalized_chars"),
                repeated_template_defaults.min_normalized_chars,
            ),
        ),
        profiles=_parse_profile_configs(config_value.get("profiles")),
    )


def apply_config_to_policy(
    base_policy: DocstringSchema,
    config: DocstringRuntimeConfig,
) -> DocstringSchema:
    """Inject repo-local config into the portable docstring policy.

    Intent
    ------
    Expose the apply config to policy step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps apply config to policy behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set apply_config_to_policy_inputs = received_context
    - return apply_config_to_policy_inputs

    Wraps
    -----
    - none
    """
    section_names = config.names_for_dependency_sections
    syntax = config.dependency_syntax
    module_dependencies = replace(
        base_policy.module_dependencies,
        calls_section=section_names.calls,
        instantiates_section=section_names.instantiations,
        dispatches_section=section_names.dispatches,
        allow_legacy_flat=syntax.allow_legacy_flat,
        require_why=syntax.require_why,
        dependency_why=config.dependency_why,
        pseudocode_quality=config.pseudocode_quality,
        repeated_template_detection=config.repeated_template_detection,
        allowed_abs=config.allowed_abs,
    )
    optional_sections = tuple(
        dict.fromkeys(
            (
                *base_policy.callable.optional_sections,
                section_names.calls,
                section_names.instantiations,
                section_names.dispatches,
            )
        )
    )
    callable_policy = replace(
        base_policy.callable,
        optional_sections=optional_sections,
    )
    return replace(
        base_policy,
        callable=callable_policy,
        module_dependencies=module_dependencies,
        config=config,
    )


def _path_matches_profile_pattern(path: Path, pattern: str) -> bool:
    """Return true when a profile glob applies to a module path.

    Intent
    ------
    Expose the path matches profile pattern step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps path matches profile pattern behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set path_matches_profile_pattern_inputs = received_context
    - return path_matches_profile_pattern_inputs

    Wraps
    -----
    - none
    """
    raw_pattern = (pattern or "").strip()
    if not raw_pattern:
        return False

    candidates: set[str] = {path.as_posix().lstrip("/")}
    try:
        candidates.add(path.resolve().relative_to(Path.cwd().resolve()).as_posix())
    except ValueError:
        pass

    parts = path.as_posix().strip("/").split("/")
    for index in range(len(parts)):
        candidates.add("/".join(parts[index:]))

    if raw_pattern.endswith("/**/*.py"):
        prefix = raw_pattern[: -len("/**/*.py")]
        for candidate in candidates:
            if candidate.startswith(f"{prefix}/") and candidate.endswith(".py"):
                return True

    return any(
        candidate == raw_pattern or PurePosixPath(candidate).match(raw_pattern)
        for candidate in candidates
        if candidate
    )


def apply_docstring_profiles(schema_rules: DocstringSchema, path: Path) -> DocstringSchema:
    """Apply ordered repo-configured path profiles to a loaded policy.

    Intent
    ------
    Expose the apply docstring profiles step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps apply docstring profiles behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set apply_docstring_profiles_inputs = received_context
    - set apply_docstring_profiles_effects = local_decisions
    - return apply_docstring_profiles_effects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._path_matches_profile_pattern:
      why:
        computes: "path matches profile pattern supplies repo-local behavior used by apply docstring profiles; this edge is documented from an observed call in the body."
    """
    module_rules = schema_rules.module_dependencies
    callable_rules = schema_rules.callable
    for profile in getattr(schema_rules.config, "profiles", ()):
        if not any(
            _path_matches_profile_pattern(path, pattern)
            for pattern in getattr(profile, "applies_to", ())
        ):
            continue

        if getattr(profile, "callable_require_docstrings", None) is not None:
            callable_rules = replace(
                callable_rules,
                require_docstrings=bool(profile.callable_require_docstrings),
            )
        if getattr(profile, "callable_required_sections", None) is not None:
            callable_rules = replace(
                callable_rules,
                required_sections=tuple(profile.callable_required_sections or ()),
            )
        if getattr(profile, "callable_min_pseudocode_steps", None) is not None:
            min_steps = int(profile.callable_min_pseudocode_steps or 0)
            callable_rules = replace(
                callable_rules,
                min_pseudocode_steps=min_steps,
                pseudocode=replace(
                    callable_rules.pseudocode,
                    min_steps=min_steps,
                ),
            )

        checks = getattr(profile, "checks", {})
        if "repeated_template_detection" in checks:
            module_rules = replace(
                module_rules,
                repeated_template_detection=replace(
                    module_rules.repeated_template_detection,
                    enabled=bool(checks["repeated_template_detection"]),
                ),
            )
        if "pseudocode_output_use" in checks or "pseudocode_dataflow" in checks:
            enabled = bool(
                checks.get(
                    "pseudocode_output_use",
                    checks.get("pseudocode_dataflow", False),
                )
            )
            module_rules = replace(
                module_rules,
                pseudocode_quality=replace(
                    module_rules.pseudocode_quality,
                    require_assigned_dependency_output_use=enabled,
                ),
            )
        if "dependency_why_action" in checks:
            module_rules = replace(
                module_rules,
                dependency_why=replace(
                    module_rules.dependency_why,
                    allow_legacy_string=not bool(checks["dependency_why_action"]),
                ),
            )
        if "instantiation_product_pseudocode" in checks:
            module_rules = replace(
                module_rules,
                enforce_declared_dependency_pseudocode_coverage=bool(
                    checks["instantiation_product_pseudocode"]
                ),
            )

    if module_rules is schema_rules.module_dependencies and callable_rules is schema_rules.callable:
        return schema_rules
    return replace(
        schema_rules,
        callable=callable_rules,
        module_dependencies=module_rules,
    )


def load_docstring_schema(path: str | Path | None = None) -> DocstringSchema:
    """Load the effective docstring policy from the standard and repo config.

    Intent
    ------
    Materialize the policy object used by parser, local syntax checks, and AST-backed
    behavioral validation.

    Rationale
    ---------
    Centralizing policy loading keeps section names, dependency syntax, allowed
    absolute roots, profile checks, and check catalogs synchronized across the
    whole docstring infrastructure.

    Pseudocode
    ----------
    - schema_path = .resolve_docstring_schema_path(optional_policy_path)
    - config_rules = .load_docstring_config()
    - if schema_path is missing:
      - return ._default_docstring_schema()
    - schema_values = ._load_yaml(schema_path)
    - dependency_rules = ModuleDependencyConfig(schema_values)
    - callable_rules = CallableDocstringSchema(schema_values)
    - schema_rules = DocstringSchema(callable_rules, dependency_rules)
    - effective_policy = .apply_config_to_policy(schema_rules, config_rules)
    - return effective_policy

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .ModuleOwnershipConfig:
      why:
        computes: "Supplies nested module ownership defaults while building module policy."
    .OwnershipConfig:
      why:
        computes: "Supplies nested callable ownership defaults while building callable policy."
    .PseudocodeDocstringSchema:
      why:
        computes: "Supplies default pseudocode field values while merging YAML policy."

    InstantiationsFromRepo
    ----------------------
    .resolve_docstring_schema_path:
      why:
        constructs: "Builds the standard policy path used for YAML loading."
    .load_docstring_config:
      why:
        constructs: "Builds repository-local docstring overrides from config.yaml."
    ._load_yaml:
      why:
        constructs: "Builds the raw standard-policy mapping consumed by schema constructors."
    ._default_docstring_schema:
      why:
        constructs: "Builds fallback policy defaults when no readable standard file exists."
    ._safe_str:
      why:
        constructs: "Builds string-valued policy fields from YAML scalars."
    ._safe_bool:
      why:
        constructs: "Builds boolean policy fields from YAML scalars."
    ._safe_int:
      why:
        constructs: "Builds integer policy limits from YAML scalars."
    ._safe_str_tuple:
      why:
        constructs: "Builds immutable policy tuples from YAML lists."
    ._parse_module_ownership_config:
      why:
        constructs: "Builds module ownership-registry policy from the loaded standard mapping."
    ._parse_ownership_config:
      why:
        constructs: "Builds callable ownership policy from the loaded standard mapping."
    .apply_config_to_policy:
      why:
        transforms: "Builds the effective policy by applying repository overrides to standard defaults."
    .CallableDocstringSchema:
      why:
        constructs: "Builds callable-level summary, section, and pseudocode policy."
    .DependencyWhyConfig:
      why:
        constructs: "Builds dependency rationale action-key policy."
    .DocstringSchema:
      why:
        constructs: "Builds the complete policy object returned to callers."
    .ModuleDependencyConfig:
      why:
        constructs: "Builds dependency declaration, scope, and rationale policy."
    .ModuleDocstringSchema:
      why:
        constructs: "Builds module-level documentation policy."
    .PipelineDocstringSchema:
      why:
        constructs: "Builds graph-pipeline documentation policy."
    .PseudocodeDocstringSchema:
      why:
        constructs: "Builds pseudocode section policy from standard YAML values."
    .PseudocodeQualityConfig:
      why:
        constructs: "Builds pseudocode dataflow-quality policy."
    .RepeatedTemplateConfig:
      why:
        constructs: "Builds repeated-template detection policy."
    """
    schema_path = resolve_docstring_schema_path(path)
    config = load_docstring_config()
    if schema_path is None or not schema_path.exists():
        return _default_docstring_schema()

    schema_value = _load_yaml(schema_path)
    if not isinstance(schema_value, dict):
        return _default_docstring_schema()

    callable_values = schema_value.get("callable", {})
    if not isinstance(callable_values, dict):
        callable_values = {}
    module_dependency_values = callable_values.get("module_dependencies", {})
    module_dependency_config = ModuleDependencyConfig()
    if isinstance(module_dependency_values, dict):
        dependency_why_values = module_dependency_values.get("dependency_why", {})
        if not isinstance(dependency_why_values, dict):
            dependency_why_values = {}
        section_action_values = dependency_why_values.get("section_actions", {})
        section_actions = dict(module_dependency_config.dependency_why.section_actions)
        if isinstance(section_action_values, dict):
            for raw_key, raw_actions in section_action_values.items():
                if not isinstance(raw_key, str) or not raw_key.strip():
                    continue
                key = raw_key.strip()
                section_actions[key] = _safe_str_tuple(
                    raw_actions,
                    section_actions.get(key, ()),
                )
        pseudocode_quality_values = module_dependency_values.get("pseudocode_quality", {})
        if not isinstance(pseudocode_quality_values, dict):
            pseudocode_quality_values = {}
        module_dependency_config = ModuleDependencyConfig(
            calls_section=_safe_str(
                module_dependency_values.get("calls_section"),
                module_dependency_config.calls_section,
            ),
            instantiates_section=_safe_str(
                module_dependency_values.get("instantiates_section"),
                module_dependency_config.instantiates_section,
            ),
            dispatches_section=_safe_str(
                module_dependency_values.get("dispatches_section"),
                module_dependency_config.dispatches_section,
            ),
            allow_implicit=_safe_bool(
                module_dependency_values.get("allow_implicit"),
                module_dependency_config.allow_implicit,
            ),
            allow_legacy_flat=_safe_bool(
                module_dependency_values.get("allow_legacy_flat"),
                module_dependency_config.allow_legacy_flat,
            ),
            require_why=_safe_bool(
                module_dependency_values.get("require_why"),
                module_dependency_config.require_why,
            ),
            dependency_why=DependencyWhyConfig(
                actions=_safe_str_tuple(
                    dependency_why_values.get("actions"),
                    module_dependency_config.dependency_why.actions,
                ),
                section_actions=section_actions,
                allow_legacy_string=_safe_bool(
                    dependency_why_values.get("allow_legacy_string"),
                    module_dependency_config.dependency_why.allow_legacy_string,
                ),
                misc_min_chars=_safe_int(
                    dependency_why_values.get("misc_min_chars"),
                    module_dependency_config.dependency_why.misc_min_chars,
                ),
            ),
            pseudocode_quality=PseudocodeQualityConfig(
                forbidden_variables=_safe_str_tuple(
                    pseudocode_quality_values.get("forbidden_variables"),
                    module_dependency_config.pseudocode_quality.forbidden_variables,
                ),
                require_assigned_dependency_output_use=_safe_bool(
                    pseudocode_quality_values.get("require_assigned_dependency_output_use"),
                    module_dependency_config.pseudocode_quality.require_assigned_dependency_output_use,
                ),
            ),
            repeated_template_detection=RepeatedTemplateConfig(
                enabled=_safe_bool(
                    module_dependency_values.get("repeated_template_detection", {}).get("enabled")
                    if isinstance(module_dependency_values.get("repeated_template_detection"), dict)
                    else None,
                    module_dependency_config.repeated_template_detection.enabled,
                ),
                min_repetitions=_safe_int(
                    module_dependency_values.get("repeated_template_detection", {}).get("min_repetitions")
                    if isinstance(module_dependency_values.get("repeated_template_detection"), dict)
                    else None,
                    module_dependency_config.repeated_template_detection.min_repetitions,
                ),
                min_normalized_chars=_safe_int(
                    module_dependency_values.get("repeated_template_detection", {}).get("min_normalized_chars")
                    if isinstance(module_dependency_values.get("repeated_template_detection"), dict)
                    else None,
                    module_dependency_config.repeated_template_detection.min_normalized_chars,
                ),
            ),
            allowed_abs=_safe_str_tuple(
                module_dependency_values.get("allowed_abs"),
                module_dependency_config.allowed_abs,
            ),
            forbidden_why_phrases=_safe_str_tuple(
                module_dependency_values.get("forbidden_why_phrases"),
                module_dependency_config.forbidden_why_phrases,
            ),
            validate_declared_calls=_safe_bool(
                module_dependency_values.get("validate_declared_calls"),
                module_dependency_config.validate_declared_calls,
            ),
            validate_declared_instantiations=_safe_bool(
                module_dependency_values.get("validate_declared_instantiations"),
                module_dependency_config.validate_declared_instantiations,
            ),
            validate_declared_dispatches=_safe_bool(
                module_dependency_values.get("validate_declared_dispatches"),
                module_dependency_config.validate_declared_dispatches,
            ),
            report_unlisted_calls=_safe_bool(
                module_dependency_values.get("report_unlisted_calls"),
                module_dependency_config.report_unlisted_calls,
            ),
            report_unlisted_instantiations=_safe_bool(
                module_dependency_values.get("report_unlisted_instantiations"),
                module_dependency_config.report_unlisted_instantiations,
            ),
            ignore_non_external=_safe_bool(
                module_dependency_values.get("ignore_non_external"),
                module_dependency_config.ignore_non_external,
            ),
            validate_dependency_targets_resolved=_safe_bool(
                module_dependency_values.get("validate_dependency_targets_resolved"),
                module_dependency_config.validate_dependency_targets_resolved,
            ),
            require_repo_dependency_targets=_safe_bool(
                module_dependency_values.get("require_repo_dependency_targets"),
                module_dependency_config.require_repo_dependency_targets,
            ),
            enforce_declared_dependency_pseudocode_coverage=_safe_bool(
                module_dependency_values.get(
                    "enforce_declared_dependency_pseudocode_coverage"
                ),
                module_dependency_config.enforce_declared_dependency_pseudocode_coverage,
            ),
            enforce_observed_dependency_pseudocode_coverage=_safe_bool(
                module_dependency_values.get(
                    "enforce_observed_dependency_pseudocode_coverage"
                ),
                module_dependency_config.enforce_observed_dependency_pseudocode_coverage,
            ),
        )

    pseudocode_values = callable_values.get("pseudocode", {})
    if not isinstance(pseudocode_values, dict):
        pseudocode_values = {}

    pseudocode_config = PseudocodeDocstringSchema(
        section=_safe_str(
            pseudocode_values.get("section"),
            PseudocodeDocstringSchema().section,
        ),
        min_steps=_safe_int(
            pseudocode_values.get("min_steps"),
            PseudocodeDocstringSchema().min_steps,
        ),
        max_steps=_safe_int(
            pseudocode_values.get("max_steps"),
            PseudocodeDocstringSchema().max_steps,
        ),
        max_step_chars=_safe_int(
            pseudocode_values.get("max_step_chars"),
            PseudocodeDocstringSchema().max_step_chars,
        ),
        max_total_chars=_safe_int(
            pseudocode_values.get("max_total_chars"),
            PseudocodeDocstringSchema().max_total_chars,
        ),
    )

    pipeline_values = schema_value.get("pipeline", {})
    module_values = schema_value.get("module", {})
    if not isinstance(pipeline_values, dict):
        pipeline_values = {}
    if not isinstance(module_values, dict):
        module_values = {}
    callable_defaults = CallableDocstringSchema()

    base_policy = DocstringSchema(
        name=_safe_str(schema_value.get("name"), "docstring_format"),
        strict=_safe_bool(schema_value.get("strict"), True),
        callable=CallableDocstringSchema(
            require_docstrings=_safe_bool(
                callable_values.get("require_docstrings"),
                callable_defaults.require_docstrings,
            ),
            required_summary=_safe_bool(
                callable_values.get("required_summary"),
                callable_defaults.required_summary,
            ),
            required_sections=_safe_str_tuple(
                callable_values.get("required_sections"),
                callable_defaults.required_sections,
            ),
            optional_sections=_safe_str_tuple(
                callable_values.get("optional_sections"),
                callable_defaults.optional_sections,
            ),
            dependency_reference_sections=_safe_str_tuple(
                callable_values.get("dependency_reference_sections"),
                callable_defaults.dependency_reference_sections,
            ),
            pseudocode=pseudocode_config,
            forbidden_summary_phrases=_safe_str_tuple(
                callable_values.get("forbidden_summary_phrases"),
                callable_defaults.forbidden_summary_phrases,
            ),
            forbidden_intent_phrases=_safe_str_tuple(
                callable_values.get("forbidden_intent_phrases"),
                callable_defaults.forbidden_intent_phrases,
            ),
            min_pseudocode_steps=_safe_int(
                callable_values.get("min_pseudocode_steps"),
                pseudocode_config.min_steps,
            ),
            summary_min_chars=_safe_int(
                callable_values.get("summary_min_chars"),
                callable_defaults.summary_min_chars,
            ),
            summary_max_chars=_safe_int(
                callable_values.get("summary_max_chars"),
                callable_defaults.summary_max_chars,
            ),
            rationale_min_chars=_safe_int(
                callable_values.get("rationale_min_chars"),
                callable_defaults.rationale_min_chars,
            ),
            rationale_max_chars=_safe_int(
                callable_values.get("rationale_max_chars"),
                callable_defaults.rationale_max_chars,
            ),
            forbidden_rationale_phrases=_safe_str_tuple(
                callable_values.get("forbidden_rationale_phrases"),
                callable_defaults.forbidden_rationale_phrases,
            ),
            forbidden_pseudocode_phrases=_safe_str_tuple(
                callable_values.get("forbidden_pseudocode_phrases"),
                callable_defaults.forbidden_pseudocode_phrases,
            ),
            ownership=_parse_ownership_config(
                callable_values.get("ownership"),
                default=OwnershipConfig(),
            ),
        ),
        module_dependencies=module_dependency_config,
        pipeline=PipelineDocstringSchema(
            required=_safe_bool(pipeline_values.get("required"), False),
            required_sections=_safe_str_tuple(
                pipeline_values.get("required_sections"),
                ("Phases", "PhaseMembers"),
            ),
            optional_sections=_safe_str_tuple(
                pipeline_values.get("optional_sections"),
                ("PhaseEdges", "NonInferableCalls", "Description"),
            ),
        ),
        module=ModuleDocstringSchema(
            required=_safe_bool(module_values.get("required"), False),
            required_summary=_safe_bool(module_values.get("required_summary"), True),
            required_sections=_safe_str_tuple(
                module_values.get("required_sections"),
                ("Includes",),
            ),
            optional_sections=_safe_str_tuple(
                module_values.get("optional_sections"),
                ("Workflow", "Traces", "Description", "Ownable"),
            ),
            ownership_registry=_parse_module_ownership_config(
                module_values.get("ownership_registry"),
                default=ModuleOwnershipConfig(),
            ),
        ),
    )
    return apply_config_to_policy(base_policy, config)


def load_docstring_check_categories(
    path: str | Path | None = None,
) -> dict[str, tuple[str, ...]]:
    """Load named checker groups from the portable standard when present.

    Intent
    ------
    Expose the load docstring check categories step in portable standard loading and repo-profile policy materialization so readers and tools can locate its exact responsibility.

    Rationale
    ---------
    This boundary keeps load docstring check categories behavior separate inside portable standard loading and repo-profile policy materialization; documenting it makes dependency checks and graph extraction reviewable.

    Pseudocode
    ----------
    - set load_docstring_check_categories_inputs = received_context
    - set load_docstring_check_categories_products = carried_outputs
    - return load_docstring_check_categories_products

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._load_yaml:
      why:
        constructs: "load yaml produces a value carried by load docstring check categories; this edge is documented from the observed product position in the body."
    ._safe_check_codes:
      why:
        constructs: "safe check codes produces a value carried by load docstring check categories; this edge is documented from the observed product position in the body."
    .resolve_docstring_schema_path:
      why:
        transforms: "resolve docstring schema path produces a value carried by load docstring check categories; this edge is documented from the observed product position in the body."
    """
    schema_path = resolve_docstring_schema_path(path)
    if schema_path is None or not schema_path.exists():
        return {}

    schema_value = _load_yaml(schema_path)
    if not isinstance(schema_value, dict):
        return {}

    raw_categories = schema_value.get("check_categories")
    if not isinstance(raw_categories, dict):
        return {}

    return {
        str(category).strip().lower(): _safe_check_codes(raw_categories.get(category))
        for category in ("formatting", "behavioral")
        if isinstance(raw_categories.get(category), (list, tuple, dict, set, str))
    }


__all__ = [
    "DOCSTRING_STANDARD_FILE",
    "DOCSTRING_CONFIG_FILE",
    "DependencySectionNames",
    "DependencySyntaxConfig",
    "DependencyWhyConfig",
    "PseudocodeQualityConfig",
    "RepeatedTemplateConfig",
    "DocstringProfileConfig",
    "DocstringRuntimeConfig",
    "OwnershipConfig",
    "ModuleOwnershipConfig",
    "CallableDocstringSchema",
    "CallableDocstringPolicy",
    "PseudocodeDocstringSchema",
    "ModuleDependencyConfig",
    "PipelineDocstringSchema",
    "ModuleDocstringSchema",
    "DocstringSchema",
    "DocstringPolicy",
    "resolve_docstring_schema_path",
    "resolve_docstring_config_path",
    "load_docstring_config",
    "load_docstring_schema",
    "load_docstring_check_categories",
    "apply_config_to_policy",
    "apply_docstring_profiles",
]
