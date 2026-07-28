#!/usr/bin/env python3
"""Docstring standard loading and typed adaptation for the common layer.

This module owns the *runtime interpretation* of declarative docstring policy.
It is intentionally narrow:

1. discover a format file under ``references`` or accept explicit path input,
2. load the policy values as a typed object that parser and validation layers
   can consume,
3. coerce values into frozen dataclasses consumed by parser/validation code.

The policy artifacts live in ``references/standards`` and should remain stable,
documented, and changeable without requiring caller updates whenever possible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Canonical policy filename.
DOCSTRING_STANDARD_FILE = "docstring.standard.yaml"


@dataclass(frozen=True)
class OwnershipConfig:
    """Rules for parsing and validating callable ``Owns`` metadata.

    ``Owns`` maps a callable to a logical owner or capability. This is separate
    from module-level ownership definitions and only needs to express the
    expectations for how ownership annotations are written and validated.
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
    """Rules for module-level ``Ownable`` registry entries.

    ``Ownable`` is where modules declare canonical owner identities. Other
    sections (e.g., callable ``Owns``) can point to these declarations.
    """

    section: str = "Ownable"
    allows_multiple: bool = True


@dataclass(frozen=True)
class PseudocodeDocstringSchema:
    """Rules for parsing callable pseudocode documentation sections."""

    section: str = "Pseudocode"
    max_steps: int = 8
    max_step_chars: int = 140
    max_total_chars: int = 640

    def section_names(self) -> tuple[str, ...]:
        """Return pseudocode section names used by callable parsing."""
        return (self.section,)


@dataclass(frozen=True)
class CallableDocstringSchema:
    """Rules for callable/class docstring block validation.

    The callable rule set is the most active contract and directly drives
    what parser output is available to downstream tooling.
    """

    required_summary: bool = True
    required_sections: tuple[str, ...] = ("Graph", "Role")
    optional_sections: tuple[str, ...] = ("Phase", "NonInferableCalls", "Wraps", "Owns")
    pseudocode: PseudocodeDocstringSchema = field(default_factory=PseudocodeDocstringSchema)
    ownership: OwnershipConfig = field(default_factory=OwnershipConfig)
    forbidden_summary_phrases: tuple[str, ...] = (
        "what does this do",
        "todo",
        "tbd",
        "placeholder",
        "implement me",
        "to be implemented",
    )

    def section_names(self) -> tuple[str, ...]:
        """Return all sections that are meaningful for callable parsing/validation."""
        return self.required_sections + self.optional_sections + self.pseudocode.section_names()


@dataclass(frozen=True)
class ModuleDependencyConfig:
    """Configuration for module dependency block sections."""

    calls_section: str = "CallsFromModule"
    instantiates_section: str = "InstantiationsFromModule"
    allow_implicit: bool = True
    validate_declared_calls: bool = True
    validate_declared_instantiations: bool = True
    report_unlisted_calls: bool = False
    report_unlisted_instantiations: bool = False
    ignore_non_external: bool = True
    validate_dependency_targets_resolved: bool = False
    enforce_declared_dependency_pseudocode_coverage: bool = False
    enforce_observed_dependency_pseudocode_coverage: bool = False

    def section_names(self) -> tuple[str, ...]:
        """Return callable section names for module dependency capture."""
        return (self.calls_section, self.instantiates_section)


@dataclass(frozen=True)
class PipelineDocstringSchema:
    """Rules for module-level pipeline docstring block validation.

    A pipeline is parsed from the ``GraphPipeline`` block and used by graph
    tooling to preserve module execution order and major phase structure.
    """

    required: bool = False
    required_sections: tuple[str, ...] = ("Phases", "PhaseMembers")
    optional_sections: tuple[str, ...] = ("PhaseEdges", "NonInferableCalls", "Description")

    def section_names(self) -> tuple[str, ...]:
        """Return all sections expected inside ``GraphPipeline``."""
        return self.required_sections + self.optional_sections


@dataclass(frozen=True)
class ModuleDocstringSchema:
    """Rules for module-level metadata expectations.

    Module rules describe high-level metadata required to interpret a module as an
    orchestration unit before diving into per-callable behavior.
    """

    required: bool = False
    required_summary: bool = True
    required_sections: tuple[str, ...] = ("Includes",)
    optional_sections: tuple[str, ...] = ("Workflow", "Traces", "Description", "Ownable")
    ownership_registry: ModuleOwnershipConfig = field(default_factory=ModuleOwnershipConfig)

    def section_names(self) -> tuple[str, ...]:
        """Return all module-level section names used by tooling."""
        return self.required_sections + self.optional_sections


@dataclass(frozen=True)
class DocstringSchema:
    """Aggregate rule set for docstring parsing and validation.

    This object is intentionally a frozen dataclass so callers can safely share
    it as a snapshot of the resolved policy without accidental mutation.
    """

    name: str = "docstring_schema"
    strict: bool = True
    callable: CallableDocstringSchema = field(default_factory=CallableDocstringSchema)
    module_dependencies: ModuleDependencyConfig = field(default_factory=ModuleDependencyConfig)
    pipeline: PipelineDocstringSchema = field(default_factory=PipelineDocstringSchema)
    module: ModuleDocstringSchema = field(default_factory=ModuleDocstringSchema)

    def section_names(self) -> tuple[str, ...]:
        """Return canonical section names across all docstring modes."""
        return (
            self.callable.section_names()
            + self.module_dependencies.section_names()
            + self.pipeline.section_names()
            + self.module.section_names()
        )


def _safe_bool(value: object, fallback: bool = False) -> bool:
    """Best-effort bool normalizer used when reading YAML policy.

    The parser accepts true/false booleans, and common string aliases like
    ``"true"``/``"1"`` for robustness across hand-authored files.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes", "on"}
    return fallback


def _safe_str(value: object, fallback: str) -> str:
    """Best-effort string normalizer with fallback for malformed YAML values."""
    if isinstance(value, str):
        return value
    return fallback


def _safe_str_tuple(values: object, fallback: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Normalize list/tuple-like scalar values into ``tuple[str, ...]``.

    YAML consumers often drift between list and tuple forms. This helper keeps
    loaders tolerant while preserving order for deterministic behavior.
    """
    if values is None:
        return fallback
    if isinstance(values, tuple):
        return tuple(item for item in values if isinstance(item, str))
    if isinstance(values, list):
        return tuple(item for item in values if isinstance(item, str))
    return fallback


def _safe_int(value: object, fallback: int = 0) -> int:
    """Normalize an integer policy value with graceful fallback."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value.strip()))
    return fallback


def _safe_check_codes(values: object) -> tuple[str, ...]:
    """Normalize check-category entries into stable check-code tuples.

    Supports both historical list-of-strings and object entries like
    ``{code: "...", intent: "..."}``.
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


def _load_yaml(path: Path) -> object:
    """Load YAML from disk with hard-fail semantics handled by caller."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return data


def _parse_ownership_config(
    value: object,
    default: OwnershipConfig,
) -> OwnershipConfig:
    """Materialize ownership section configuration into a typed object."""
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
    """Materialize module-level ownable registry configuration."""
    if not isinstance(value, dict):
        return default
    return ModuleOwnershipConfig(
        section=_safe_str(value.get("section"), default.section),
        allows_multiple=_safe_bool(value.get("allows_multiple"), default.allows_multiple),
    )


def _default_docstring_schema() -> DocstringSchema:
    """Return conservative fallback when policy lookup/validation fails."""
    return DocstringSchema()


def resolve_docstring_schema_path(path: str | Path | None = None) -> Path | None:
    """Resolve a docstring format file path.

    Priority:

    - explicit ``path`` argument when provided,
    - ``references/docstring.standard.yaml``
    - ``references/standards/docstring.standard.yaml``
    """
    if path is not None:
        return Path(path)

    def _resolve_candidates(base: Path) -> list[Path]:
        return [
            base / DOCSTRING_STANDARD_FILE,
            base / "standards" / DOCSTRING_STANDARD_FILE,
        ]

    module_path = Path(__file__).resolve()
    for parent in (module_path, *module_path.parents):
        for candidate in _resolve_candidates(parent / "references"):
            if candidate.exists():
                return candidate
        for candidate in _resolve_candidates(parent / "references" / "standards"):
            if candidate.exists():
                return candidate
    return None


def load_docstring_schema(path: str | Path | None = None) -> DocstringSchema:
    """Load and map docstring format rules from ``references/.../docstring.standard.yaml``.

    On malformed files or missing schema validation support, this returns the
    conservative default dataclass values so callers can continue operating in a
    safe mode.
    """
    schema_path = resolve_docstring_schema_path(path)
    if schema_path is None or not schema_path.exists():
        return _default_docstring_schema()

    schema_value = _load_yaml(schema_path)
    if not isinstance(schema_value, dict):
        return _default_docstring_schema()

    callable_values = schema_value.get("callable", {})
    module_dependency_values = callable_values.get("module_dependencies", {}) if isinstance(callable_values, dict) else {}
    module_dependency_config = ModuleDependencyConfig()
    if isinstance(module_dependency_values, dict):
        module_dependency_config = ModuleDependencyConfig(
            calls_section=_safe_str(
                module_dependency_values.get("calls_section"),
                module_dependency_config.calls_section,
            ),
            instantiates_section=_safe_str(
                module_dependency_values.get("instantiates_section"),
                module_dependency_config.instantiates_section,
            ),
            allow_implicit=_safe_bool(
                module_dependency_values.get("allow_implicit"), module_dependency_config.allow_implicit
            ),
            validate_declared_calls=_safe_bool(
                module_dependency_values.get("validate_declared_calls"),
                module_dependency_config.validate_declared_calls,
            ),
            validate_declared_instantiations=_safe_bool(
                module_dependency_values.get("validate_declared_instantiations"),
                module_dependency_config.validate_declared_instantiations,
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

    pseudocode_config = PseudocodeDocstringSchema()
    if isinstance(pseudocode_values, dict):
        pseudocode_config = PseudocodeDocstringSchema(
            section=_safe_str(pseudocode_values.get("section"), pseudocode_config.section),
            max_steps=_safe_int(
                pseudocode_values.get("max_steps"), pseudocode_config.max_steps
            ),
            max_step_chars=_safe_int(
                pseudocode_values.get("max_step_chars"),
                pseudocode_config.max_step_chars,
            ),
            max_total_chars=_safe_int(
                pseudocode_values.get("max_total_chars"),
                pseudocode_config.max_total_chars,
            ),
        )

    pipeline_values = schema_value.get("pipeline", {})
    module_values = schema_value.get("module", {})

    return DocstringSchema(
        name=_safe_str(schema_value.get("name"), "docstring_format"),
        strict=_safe_bool(schema_value.get("strict"), True),
        callable=CallableDocstringSchema(
            required_summary=_safe_bool(callable_values.get("required_summary"), True),
            required_sections=_safe_str_tuple(
                callable_values.get("required_sections"),
                ("Graph", "Role"),
            ),
            optional_sections=_safe_str_tuple(
                callable_values.get("optional_sections"),
                ("Phase", "NonInferableCalls", "Wraps", "Owns", "Pseudocode"),
            ),
            pseudocode=pseudocode_config,
            forbidden_summary_phrases=_safe_str_tuple(
                callable_values.get("forbidden_summary_phrases"),
                (
                    "what does this do",
                    "todo",
                    "tbd",
                    "placeholder",
                    "implement me",
                    "to be implemented",
                ),
            ),
            ownership=_parse_ownership_config(callable_values.get("ownership"), default=OwnershipConfig()),
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

def load_docstring_check_categories(path: str | Path | None = None) -> dict[str, tuple[str, ...]]:
    """Load the optional check-category manifest from the policy file.

    Returns mapping keys such as ``formatting`` and ``behavioral`` to ordered,
    deduplicated check-code tuples. Unknown categories or malformed entries are
    ignored and return an empty tuple.
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
    "OwnershipConfig",
    "ModuleOwnershipConfig",
    "CallableDocstringSchema",
    "PseudocodeDocstringSchema",
    "ModuleDependencyConfig",
    "PipelineDocstringSchema",
    "ModuleDocstringSchema",
    "DocstringSchema",
    "resolve_docstring_schema_path",
    "load_docstring_schema",
    "load_docstring_check_categories",
]
