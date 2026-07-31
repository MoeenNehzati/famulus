#!/usr/bin/env python3
"""Docstring policy loading and repo-local config adaptation.

The standard YAML describes the portable docstring contract. The local
``config.yaml`` describes repo-specific choices such as allowed absolute roots
and dependency section names. This module materializes both into typed objects
used by parser and validator code.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

DOCSTRING_STANDARD_FILE = "docstring.standard.yaml"
DOCSTRING_STANDARD_CANDIDATE_FILE = "docstring.standard.candidate.yaml"
DOCSTRING_LEGACY_FORMAT_FILE = "docstring_format.yaml"
DOCSTRING_CONFIG_FILE = "config.yaml"

_ALLOWED_PROFILE_CHECKS: frozenset[str] = frozenset(
    {
        "dependency_why_action",
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
    """Configurable names for dependency declaration sections."""

    calls: str = "CallsFromRepo"
    instantiations: str = "InstantiationsFromRepo"
    dispatches: str = "Dispatches"


@dataclass(frozen=True)
class DependencySyntaxConfig:
    """Configurable dependency syntax switches."""

    allow_legacy_flat: bool = False
    require_why: bool = True


@dataclass(frozen=True)
class DependencyWhyConfig:
    """Configurable dependency rationale action syntax."""

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
    allow_legacy_string: bool = False
    misc_min_chars: int = 80


@dataclass(frozen=True)
class PseudocodeQualityConfig:
    """Configurable pseudocode graphability checks."""

    forbidden_variables: tuple[str, ...] = ("value", "out", "state", "args", "data")
    require_assigned_dependency_output_use: bool = False


@dataclass(frozen=True)
class RepeatedTemplateConfig:
    """Configurable repeated prose-template detection."""

    enabled: bool = False
    min_repetitions: int = 3
    min_normalized_chars: int = 40


@dataclass(frozen=True)
class DocstringProfileConfig:
    """Path-scoped docstring quality switches loaded from repo config."""

    name: str
    applies_to: tuple[str, ...] = ()
    checks: dict[str, bool] = field(default_factory=dict)
    callable_require_docstrings: bool | None = None
    callable_required_sections: tuple[str, ...] | None = None
    callable_min_pseudocode_steps: int | None = None


@dataclass(frozen=True)
class DocstringRuntimeConfig:
    """Repo-local docstring settings loaded from ``config.yaml``."""

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
    """Rules for parsing and validating callable ``Owns`` metadata."""

    section: str = "Owns"
    section_required: bool = False
    allows_multiple: bool = False
    single_owner_per_callable: bool = True
    owner_resolution: str = "module:Ownable"
    owner_section: str = "Ownable"
    cross_file_enabled: bool = False


@dataclass(frozen=True)
class ModuleOwnershipConfig:
    """Rules for module-level ``Ownable`` registry entries."""

    section: str = "Ownable"
    allows_multiple: bool = True


@dataclass(frozen=True)
class PseudocodeDocstringSchema:
    """Rules for callable pseudocode sections."""

    section: str = "Pseudocode"
    min_steps: int = 1
    max_steps: int = 16
    max_step_chars: int = 140
    max_total_chars: int = 640

    def section_names(self) -> tuple[str, ...]:
        return (self.section,)


@dataclass(frozen=True)
class CallableDocstringSchema:
    """Rules for callable/class docstring block validation."""

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
        "compact docstrings whose declared calls and constructors stay aligned",
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
        "compact docstrings whose declared calls and constructors stay aligned",
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
        sections: list[str] = []
        sections.extend(self.required_sections)
        sections.extend(self.optional_sections)
        sections.extend(self.pseudocode.section_names())
        sections.extend(self.dependency_reference_sections)
        return tuple(dict.fromkeys(sections).keys())


@dataclass(frozen=True)
class ModuleDependencyConfig:
    """Configuration for callable dependency declaration sections."""

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
        "observed constructor dependency",
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
        return (
            self.calls_section,
            self.instantiates_section,
            self.dispatches_section,
        )


@dataclass(frozen=True)
class PipelineDocstringSchema:
    """Rules for module-level pipeline docstring block validation."""

    required: bool = False
    required_sections: tuple[str, ...] = ("Phases", "PhaseMembers")
    optional_sections: tuple[str, ...] = ("PhaseEdges", "NonInferableCalls", "Description")

    def section_names(self) -> tuple[str, ...]:
        return self.required_sections + self.optional_sections


@dataclass(frozen=True)
class ModuleDocstringSchema:
    """Rules for module-level metadata expectations."""

    required: bool = False
    required_summary: bool = True
    required_sections: tuple[str, ...] = ("Includes",)
    optional_sections: tuple[str, ...] = ("Workflow", "Traces", "Description", "Ownable")
    ownership_registry: ModuleOwnershipConfig = field(default_factory=ModuleOwnershipConfig)

    def section_names(self) -> tuple[str, ...]:
        return self.required_sections + self.optional_sections


@dataclass(frozen=True)
class DocstringSchema:
    """Effective docstring policy consumed by parser and validators."""

    name: str = "docstring_policy"
    strict: bool = True
    callable: CallableDocstringSchema = field(default_factory=CallableDocstringSchema)
    module_dependencies: ModuleDependencyConfig = field(default_factory=ModuleDependencyConfig)
    config: DocstringRuntimeConfig = field(default_factory=DocstringRuntimeConfig)
    pipeline: PipelineDocstringSchema = field(default_factory=PipelineDocstringSchema)
    module: ModuleDocstringSchema = field(default_factory=ModuleDocstringSchema)

    def section_names(self) -> tuple[str, ...]:
        return (
            self.callable.section_names()
            + self.module_dependencies.section_names()
            + self.pipeline.section_names()
            + self.module.section_names()
        )


DocstringPolicy = DocstringSchema
CallableDocstringPolicy = CallableDocstringSchema


def _safe_bool(value: object, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes", "on"}
    return fallback


def _safe_str(value: object, fallback: str) -> str:
    if isinstance(value, str):
        return value
    return fallback


def _safe_str_tuple(values: object, fallback: tuple[str, ...] = ()) -> tuple[str, ...]:
    if values is None:
        return fallback
    if isinstance(values, tuple):
        return tuple(item for item in values if isinstance(item, str))
    if isinstance(values, list):
        return tuple(item for item in values if isinstance(item, str))
    return fallback


def _safe_int(value: object, fallback: int = 0) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value.strip()))
    return fallback


def _safe_check_codes(values: object) -> tuple[str, ...]:
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
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _parse_ownership_config(
    value: object,
    default: OwnershipConfig,
) -> OwnershipConfig:
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
    if not isinstance(value, dict):
        return default
    return ModuleOwnershipConfig(
        section=_safe_str(value.get("section"), default.section),
        allows_multiple=_safe_bool(value.get("allows_multiple"), default.allows_multiple),
    )


def _default_docstring_schema() -> DocstringSchema:
    return apply_config_to_policy(DocstringSchema(), load_docstring_config())


def resolve_docstring_schema_path(path: str | Path | None = None) -> Path | None:
    if path is not None:
        return Path(path)

    def _resolve_candidates(base: Path) -> list[Path]:
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
    if path is not None:
        return Path(path)
    return Path(__file__).resolve().with_name(DOCSTRING_CONFIG_FILE)


def load_docstring_config(path: str | Path | None = None) -> DocstringRuntimeConfig:
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


def load_docstring_schema(path: str | Path | None = None) -> DocstringSchema:
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
]
