"""Probe the exact checked-out public Rutter and Compass contracts."""

from __future__ import annotations

from copy import deepcopy
import importlib
import importlib.util
import re
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import pytest
import yaml

import officina.rutter as public_rutter
from officina.rutter import (
    BaseRutter,
    Charter,
    InputValidatorContract,
    JsonValue,
    RutterRegistry,
    State,
    TerminalState,
    ValidationIssue,
    ValidationReport,
)


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SKILL_ROOT.parents[1]
RUTTER_ROOT = REPOSITORY_ROOT / "src/officina/rutter"
COMPASS_ROOT = REPOSITORY_ROOT / "skills/using-compass"
PACKAGE_NAME = "_distill_to_rutters_rtx"


def _load_production_runtime():
    package_dir = SKILL_ROOT / "_rtx"
    runtime_path = package_dir / "_runtime_compatibility.py"
    assert runtime_path.is_file(), (
        "runtime compatibility must be implemented in the owned production module"
    )
    if PACKAGE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PACKAGE_NAME,
            package_dir / "__init__.py",
            submodule_search_locations=[str(package_dir)],
        )
        assert spec is not None and spec.loader is not None
        package = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE_NAME] = package
        spec.loader.exec_module(package)
    return importlib.import_module(f"{PACKAGE_NAME}._runtime_compatibility")

def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _exported_interfaces(module_root: Path) -> dict[str, dict[str, Any]]:
    root = _load_yaml(module_root / "blueprint.yaml")
    exports = root.get("exports")
    sources = root.get("sources")
    resolved: dict[str, dict[str, Any]] = {}
    if not isinstance(exports, Mapping) or not isinstance(sources, Mapping):
        return resolved
    for export_id, export in exports.items():
        if not isinstance(export_id, str) or not isinstance(export, Mapping):
            continue
        source_interface = export.get("source_interface")
        if not isinstance(source_interface, str) or ".interface." not in source_interface:
            continue
        source_id = source_interface.split(".interface.", 1)[0]
        source_entry = sources.get(source_id)
        locator = (
            source_entry.get("blueprint")
            if isinstance(source_entry, Mapping)
            else None
        )
        if (
            not isinstance(locator, Mapping)
            or locator.get("base") != "module-root"
            or not isinstance(locator.get("path"), str)
        ):
            continue
        source = _load_yaml(module_root / str(locator["path"]))
        interfaces = source.get("interfaces")
        interface = (
            interfaces.get(source_interface)
            if isinstance(interfaces, Mapping)
            else None
        )
        if not isinstance(interface, Mapping):
            continue
        contract = interface.get("contract")
        resolved[export_id] = {
            "export_id": export_id,
            "source_interface": source_interface,
            "version": interface.get("version"),
            "contract": contract if isinstance(contract, Mapping) else {},
            "definition": interface,
        }
    return resolved


def _exported_interface(module_root: Path, export_id: str) -> dict[str, Any] | None:
    exported = _exported_interfaces(module_root).get(export_id)
    if exported is None:
        return None
    definition = exported.get("definition")
    return dict(definition) if isinstance(definition, Mapping) else None


def _exported_contract(module_root: Path, export_id: str) -> dict[str, Any] | None:
    interface = _exported_interface(module_root, export_id)
    if interface is None:
        return None
    contract = interface.get("contract")
    return contract if isinstance(contract, dict) else None


def _operation_values(contract: Mapping[str, Any] | None) -> tuple[str, ...]:
    if contract is None:
        return ()
    values = (
        contract.get("arguments", {})
        .get("operation", {})
        .get("type", {})
        .get("values", ())
    )
    return tuple(
        item["value"]
        for item in values
        if isinstance(item, Mapping) and isinstance(item.get("value"), str)
    )


def _nominal_python_type_errors(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ("structured nominal Python type is missing",)
    if value.get("language") != "python":
        return ("nominal type language is not python",)
    if not isinstance(value.get("qualified_class"), str) or not value.get(
        "qualified_class"
    ):
        return ("nominal Python qualified class is missing",)
    schema = value.get("schema")
    if not isinstance(schema, Mapping) or not schema:
        return ("nominal Python schema is missing",)
    return ()


def _semantic_capabilities(export: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    contract = export.get("contract")
    rows = (
        contract.get("semantic_capabilities")
        if isinstance(contract, Mapping)
        else None
    )
    return tuple(row for row in rows if isinstance(row, Mapping)) if isinstance(
        rows, list
    ) else ()


def _contract_output(
    export: Mapping[str, Any],
    output_id: Any,
) -> Mapping[str, Any] | None:
    contract = export.get("contract")
    outputs = contract.get("outputs") if isinstance(contract, Mapping) else None
    if not isinstance(outputs, list):
        return None
    return next(
        (
            output
            for output in outputs
            if isinstance(output, Mapping) and output.get("id") == output_id
        ),
        None,
    )


def _operation_capability_errors(
    export: Mapping[str, Any],
    capability: Mapping[str, Any],
) -> tuple[str, ...]:
    errors: list[str] = []
    contract = export.get("contract")
    if not isinstance(contract, Mapping):
        return ("source interface contract is missing",)
    operation = capability.get("operation")
    if not isinstance(operation, str) or operation not in _operation_values(contract):
        errors.append("declared semantic operation does not exist")
    arguments = contract.get("arguments")
    argument_ids = (
        set(arguments) - {"operation"} if isinstance(arguments, Mapping) else set()
    )
    required = capability.get("required_arguments")
    optional = capability.get("optional_arguments")
    exact_partition = (
        isinstance(required, list)
        and isinstance(optional, list)
        and all(isinstance(item, str) for item in (*required, *optional))
        and len(required) == len(set(required))
        and len(optional) == len(set(optional))
        and not set(required).intersection(optional)
        and set(required).union(optional) == argument_ids
    )
    if not exact_partition:
        errors.append("semantic capability argument partition is not exact")
    elif isinstance(arguments, Mapping):
        actual_required = {
            argument_id
            for argument_id, argument in arguments.items()
            if argument_id != "operation"
            and isinstance(argument, Mapping)
            and argument.get("required") is True
        }
        actual_optional = {
            argument_id
            for argument_id, argument in arguments.items()
            if argument_id != "operation"
            and isinstance(argument, Mapping)
            and argument.get("required") is False
        }
        if set(required) != actual_required or set(optional) != actual_optional:
            errors.append(
                "semantic capability required/optional declarations do not "
                "match argument required flags"
            )
    declared_outcomes = capability.get("outcomes")
    outcomes = contract.get("outcomes")
    indexed_outcomes = {
        row.get("id"): row
        for row in outcomes
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    } if isinstance(outcomes, list) else {}
    if not isinstance(declared_outcomes, Mapping) or not declared_outcomes:
        errors.append("semantic capability outcomes are missing")
    else:
        if set(declared_outcomes) != set(indexed_outcomes):
            errors.append(
                "semantic capability outcomes do not equal complete interface "
                "outcome set"
            )
        for outcome_id, declared in declared_outcomes.items():
            actual = indexed_outcomes.get(outcome_id)
            if not (
                isinstance(declared, Mapping)
                and isinstance(actual, Mapping)
                and declared.get("class") == actual.get("class")
                and tuple(declared.get("outputs", ()))
                == tuple(actual.get("outputs", ()))
            ):
                errors.append(
                    f"semantic capability outcome {outcome_id} does not match"
                )
        if capability.get("role") == "construction" and not any(
            isinstance(declared, Mapping)
            and declared.get("class") == "success"
            and capability.get("output") in declared.get("outputs", ())
            for declared in declared_outcomes.values()
        ):
            errors.append(
                "nominal construction output is not emitted by a declared "
                "success outcome"
            )
    output = _contract_output(export, capability.get("output"))
    if output is None:
        errors.append("semantic capability output does not exist")
    elif _nominal_python_type_errors(output.get("nominal_type")):
        errors.append("producer output lacks a structured nominal Python type")
    if capability.get("role") == "execution":
        binding_id = capability.get("binding_argument")
        binding = (
            arguments.get(binding_id)
            if isinstance(arguments, Mapping) and isinstance(binding_id, str)
            else None
        )
        if not isinstance(binding, Mapping):
            errors.append("execution binding argument does not exist")
        elif _nominal_python_type_errors(binding.get("nominal_type")):
            errors.append("execution binding lacks a structured nominal Python type")
    return tuple(errors)


def _discover_nominal_profiles(
    exported_interfaces: Mapping[str, Mapping[str, Any]],
    qualified_class: str,
    role: str,
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    expected_semantic = "construct" if role == "construction" else "advance"
    candidates: list[dict[str, Any]] = []
    candidate_errors: list[str] = []
    for export_id, export in exported_interfaces.items():
        for capability in _semantic_capabilities(export):
            if capability.get("role") != role or capability.get(
                "semantic_operation"
            ) != expected_semantic:
                continue
            if role == "construction" and capability.get(
                "id"
            ) != "rutter-binding-construction":
                continue
            output = _contract_output(export, capability.get("output"))
            contract = export.get("contract")
            arguments = (
                contract.get("arguments") if isinstance(contract, Mapping) else None
            )
            binding = (
                arguments.get(capability.get("binding_argument"))
                if role == "execution" and isinstance(arguments, Mapping)
                else None
            )
            nominal = (
                output.get("nominal_type")
                if role == "construction" and isinstance(output, Mapping)
                else binding.get("nominal_type")
                if isinstance(binding, Mapping)
                else None
            )
            if not isinstance(nominal, Mapping) or nominal.get(
                "qualified_class"
            ) != qualified_class:
                continue
            errors = _operation_capability_errors(export, capability)
            if errors:
                candidate_errors.extend(
                    f"{export_id}: {error}" for error in errors
                )
                continue
            assert isinstance(output, Mapping)
            candidates.append(
                {
                    "interface": export_id,
                    "source_interface": export.get("source_interface"),
                    "version": export.get("version"),
                    "capability": capability.get("id"),
                    "operation": capability.get("operation"),
                    "output": capability.get("output"),
                    "nominal_type": dict(nominal),
                    "output_nominal_type": dict(output["nominal_type"]),
                }
            )
    if candidates:
        return tuple(sorted(candidates, key=lambda row: str(row["interface"]))), ()
    if candidate_errors:
        return (), tuple(candidate_errors)
    if role == "construction":
        return (), (
            "no exported source interface declares an operation-specific "
            "rutter-binding-construction capability with a structured nominal "
            f"Python type for {qualified_class}",
        )
    return (), (
        "no exported source interface declares an advance semantic capability "
        f"bound to structured nominal Python type {qualified_class}",
    )


def _resolved_compass_handoff_errors(
    compass_interface: Mapping[str, Any] | None,
    producer_interfaces: Mapping[str, Mapping[str, Any]],
    ready_constructions: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    if not isinstance(compass_interface, Mapping):
        return ("using-compass public interface is missing",)
    contract = compass_interface.get("contract")
    arguments = contract.get("arguments") if isinstance(contract, Mapping) else None
    binding = arguments.get("binding") if isinstance(arguments, Mapping) else None
    if not isinstance(binding, Mapping) or binding.get("required") is not True:
        return ("required Compass binding argument is missing",)
    accepts = binding.get("accepts")
    required_fields = {
        "interface",
        "version",
        "operation",
        "output",
        "capability",
        "nominal_type",
    }
    if not isinstance(accepts, Mapping) or set(accepts) != required_fields:
        return (
            "binding.accepts is missing exact interface/version/operation/output/"
            "capability/nominal_type fields",
        )
    errors: list[str] = []
    nominal_errors = _nominal_python_type_errors(accepts.get("nominal_type"))
    if nominal_errors:
        errors.append("binding.accepts lacks a structured nominal Python type")
    interface_id = accepts.get("interface")
    producer = (
        producer_interfaces.get(interface_id)
        if isinstance(interface_id, str)
        else None
    )
    if producer is None:
        return tuple([*errors, "accepted producer interface does not exist"])
    if not isinstance(producer.get("source_interface"), str) or not producer.get(
        "source_interface"
    ):
        errors.append("accepted producer source interface does not exist")
    if producer.get("version") != accepts.get("version"):
        errors.append("accepted producer version does not exist")
    output = _contract_output(producer, accepts.get("output"))
    if output is None:
        errors.append("accepted producer output does not exist")
    else:
        nominal = output.get("nominal_type")
        if _nominal_python_type_errors(nominal):
            errors.append("producer output lacks a structured nominal Python type")
        elif nominal != accepts.get("nominal_type"):
            errors.append("accepted nominal type does not match producer output")
    capability = next(
        (
            row
            for row in _semantic_capabilities(producer)
            if row.get("id") == "rutter-binding-construction"
            and row.get("role") == "construction"
            and row.get("semantic_operation") == "construct"
            and row.get("operation") == accepts.get("operation")
            and row.get("output") == accepts.get("output")
        ),
        None,
    )
    if accepts.get("capability") != "rutter-binding-construction" or not isinstance(
        capability, Mapping
    ):
        errors.append(
            "accepted operation lacks rutter-binding-construction capability"
        )
    else:
        errors.extend(_operation_capability_errors(producer, capability))
    selected = tuple(
        ready
        for ready in ready_constructions
        if isinstance(ready, Mapping)
        and all(
            ready.get(field) == accepts.get(field)
            for field in (
                "interface",
                "version",
                "operation",
                "output",
                "capability",
                "nominal_type",
            )
        )
    )
    if not selected:
        errors.append("accepted producer is not a discovered ready construction profile")
    elif len(selected) > 1:
        errors.append("accepted construction tuple is ambiguous")
    return tuple(dict.fromkeys(errors))


class _PublicTransitionProbeRutter(BaseRutter):
    """Small direct definition used only to exercise the live public boundary."""

    rutter_id = "public-transition-probe"
    definition_version = 1
    start_state = "inspect"

    @staticmethod
    def _validate(value: Mapping[str, JsonValue]) -> ValidationReport:
        if value.get("outcome") == "accepted":
            return ValidationReport(valid=True)
        return ValidationReport(
            valid=False,
            issues=(
                ValidationIssue(
                    path="outcome",
                    code="invalid-outcome",
                    message="outcome must be accepted",
                ),
            ),
        )

    @staticmethod
    def _next(value: Mapping[str, JsonValue]) -> str:
        assert value["outcome"] == "accepted"
        return "complete"

    def define_states(self) -> Mapping[str, State | TerminalState]:
        return {
            "inspect": State(
                instruction="Inspect the public transition probe.",
                input_validator=InputValidatorContract(
                    self._validate,
                    ("accepted",),
                ),
                next_state=self._next,
            ),
            "complete": TerminalState(description="Probe completed."),
        }


def _probe_runtime(tmp_path: Path) -> dict[str, Any]:
    exported_interfaces = _exported_interfaces(RUTTER_ROOT)
    construction_interface = _exported_interface(
        RUTTER_ROOT,
        "rutter.interface.binding",
    )
    construction = (
        construction_interface.get("contract")
        if isinstance(construction_interface, Mapping)
        else None
    )
    bound_operations_interface = _exported_interface(
        RUTTER_ROOT,
        "rutter.interface.bound-operations",
    )
    bound_operations = (
        bound_operations_interface.get("contract")
        if isinstance(bound_operations_interface, Mapping)
        else None
    )
    compass_interface = _exported_interface(
        COMPASS_ROOT,
        "using-compass.interface.default",
    )
    compass = (
        compass_interface.get("contract")
        if isinstance(compass_interface, Mapping)
        else None
    )

    missing_evidence: dict[str, tuple[str, ...]] = {}
    public_names = set(public_rutter.__all__)
    for name in (
        "BaseRutter",
        "Charter",
        "InputValidatorContract",
        "RutterRegistry",
        "State",
        "TerminalState",
        "ValidationIssue",
        "ValidationReport",
    ):
        if name not in public_names or not hasattr(public_rutter, name):
            missing_evidence[f"python-export:officina.rutter.{name}"] = (
                "public Python export is missing",
            )
    construction_operations = _operation_values(construction)
    public_bound_operations = _operation_values(bound_operations)
    for name in ("Voyage", "VoyageDispenser"):
        if name not in public_names or not hasattr(public_rutter, name):
            missing_evidence[f"python-export:officina.rutter.{name}"] = (
                "public Python export is missing",
            )

    runtime_profiles: dict[str, dict[str, Any]] = {}
    ready_constructions: list[Mapping[str, Any]] = []
    for profile_id, qualified_class, role in (
        ("base-rutter-construction", "officina.rutter.BaseRutter", "construction"),
        ("voyage-construction", "officina.rutter.Voyage", "construction"),
        ("voyage-execution", "officina.rutter.Voyage", "execution"),
        (
            "voyage-dispenser-construction",
            "officina.rutter.VoyageDispenser",
            "construction",
        ),
        (
            "voyage-dispenser-execution",
            "officina.rutter.VoyageDispenser",
            "execution",
        ),
    ):
        profiles, errors = _discover_nominal_profiles(
            exported_interfaces,
            qualified_class,
            role,
        )
        runtime_profiles[profile_id] = {
            "qualified_class": qualified_class,
            "role": role,
            "ready": bool(profiles) and not errors,
            "discovered": profiles,
            "errors": errors,
        }
        if errors:
            missing_evidence[
                f"rutter-root-export:{profile_id}:contract.semantic_capabilities"
            ] = errors
        elif role == "construction":
            ready_constructions.extend(profiles)

    binding = compass.get("arguments", {}).get("binding", {}) if compass else {}
    handoff_errors = _resolved_compass_handoff_errors(
        compass_interface,
        exported_interfaces,
        ready_constructions,
    )
    if handoff_errors:
        missing_evidence[
            "using-compass.source.gateway.interface.default.contract.arguments."
            "binding.accepts"
        ] = handoff_errors

    binding_type = binding.get("type", {}) if isinstance(binding, Mapping) else {}
    accepted_type = (
        binding_type.get("description")
        if isinstance(binding_type, Mapping)
        else None
    )

    reckoning_path = Path("probe.reckoning.json")
    registry = RutterRegistry(
        {"probe": _PublicTransitionProbeRutter},
        tmp_path,
    )
    bound = registry.create("probe", reckoning_path, {})
    successor = bound.advance(
        {"revision": 0, "outcome": "accepted", "evidence": {}},
    )
    transition = {
        "successor": successor,
        "state": bound.fix.current_state_id,
        "revision": bound.fix.revision,
        "lifecycle": bound.fix.lifecycle,
    }
    bound_type = type(bound).__name__
    del bound
    reopened_registry = RutterRegistry(
        {"probe": _PublicTransitionProbeRutter},
        tmp_path,
    )
    reopened = reopened_registry.open(reckoning_path)

    return {
        "outcome": "design-ready" if not missing_evidence else "design-blocked",
        "missing_evidence": missing_evidence,
        "concrete_rutter_construction": {
            "interface": "rutter.interface.binding",
            "version": (
                construction_interface.get("version")
                if isinstance(construction_interface, Mapping)
                else None
            ),
            "operations": construction_operations,
            "bound_type": bound_type,
        },
        "bound_operations": {
            "interface": "rutter.interface.bound-operations",
            "version": (
                bound_operations_interface.get("version")
                if isinstance(bound_operations_interface, Mapping)
                else None
            ),
            "operations": public_bound_operations,
        },
        "runtime_profiles": runtime_profiles,
        "compass_binding": {
            "interface": "using-compass.interface.default",
            "version": (
                compass_interface.get("version")
                if isinstance(compass_interface, Mapping)
                else None
            ),
            "required": binding.get("required") if isinstance(binding, Mapping) else None,
            "accepted_type": accepted_type,
            "exact_construction_handoff": not handoff_errors,
        },
        "real_transition": transition,
        "reopened_transition": {
            "bound_type": type(reopened).__name__,
            "state": reopened.fix.current_state_id,
            "revision": reopened.fix.revision,
            "lifecycle": reopened.fix.lifecycle,
        },
    }


def test_checked_out_runtime_probe_is_honestly_design_blocked(tmp_path: Path) -> None:
    """The current checkout must not turn direct-Rutter support into Voyage support."""
    runtime = _load_production_runtime()
    result = runtime.probe_runtime_compatibility(REPOSITORY_ROOT, tmp_path)

    assert result["outcome"] == "design-blocked"
    assert result["missing_evidence"] == {
        "python-export:officina.rutter.Voyage": (
            "public Python export is missing",
        ),
        "python-export:officina.rutter.VoyageDispenser": (
            "public Python export is missing",
        ),
        (
            "rutter-root-export:base-rutter-construction:"
            "contract.semantic_capabilities"
        ): (
            "no exported source interface declares an operation-specific "
            "rutter-binding-construction capability with a structured nominal "
            "Python type for officina.rutter.BaseRutter",
        ),
        (
            "rutter-root-export:voyage-construction:"
            "contract.semantic_capabilities"
        ): (
            "no exported source interface declares an operation-specific "
            "rutter-binding-construction capability with a structured nominal "
            "Python type for officina.rutter.Voyage",
        ),
        "rutter-root-export:voyage-execution:contract.semantic_capabilities": (
            "no exported source interface declares an advance semantic capability "
            "bound to structured nominal Python type officina.rutter.Voyage",
        ),
        (
            "rutter-root-export:voyage-dispenser-construction:"
            "contract.semantic_capabilities"
        ): (
            "no exported source interface declares an operation-specific "
            "rutter-binding-construction capability with a structured nominal "
            "Python type for officina.rutter.VoyageDispenser",
        ),
        (
            "rutter-root-export:voyage-dispenser-execution:"
            "contract.semantic_capabilities"
        ): (
            "no exported source interface declares an advance semantic capability "
            "bound to structured nominal Python type "
            "officina.rutter.VoyageDispenser",
        ),
        (
            "using-compass.source.gateway.interface.default.contract.arguments."
            "binding.accepts"
        ): (
            "binding.accepts is missing exact interface/version/operation/output/"
            "capability/nominal_type fields",
        ),
    }
    assert result["concrete_rutter_construction"] == {
        "interface": "rutter.interface.binding",
        "version": 1,
        "operations": ("registry-create", "registry-open"),
        "bound_type": "_PublicTransitionProbeRutter",
    }
    assert result["bound_operations"] == {
        "interface": "rutter.interface.bound-operations",
        "version": 1,
        "operations": ("get-instruction", "validate", "advance"),
    }
    assert all(
        profile["ready"] is False
        for profile in result["runtime_profiles"].values()
    )
    assert result["compass_binding"] == {
        "interface": "using-compass.interface.default",
        "version": 5,
        "required": True,
        "accepted_type": "Bound Python Rutter value supplied by the authorized invoker.",
        "exact_construction_handoff": False,
    }
    assert result["real_transition"] == {
        "successor": "complete",
        "state": "complete",
        "revision": 1,
        "lifecycle": "complete",
    }
    assert result["reopened_transition"] == {
        "bound_type": "_PublicTransitionProbeRutter",
        "state": "complete",
        "revision": 1,
        "lifecycle": "complete",
    }


def test_semantic_capability_requires_exact_arguments_and_outcomes() -> None:
    export = _declared_export(
        "rutter.interface.constructor",
        101,
        "officina.rutter.Voyage",
        role="construction",
        operation="construct-voyage",
    )
    wrong_arguments = deepcopy(export)
    wrong_arguments["contract"]["semantic_capabilities"][0][
        "optional_arguments"
    ] = []
    wrong_outcomes = deepcopy(export)
    wrong_outcomes["contract"]["semantic_capabilities"][0]["outcomes"][
        "completed"
    ]["class"] = "partial"

    assert "semantic capability argument partition is not exact" in (
        _operation_capability_errors(
            wrong_arguments,
            wrong_arguments["contract"]["semantic_capabilities"][0],
        )
    )
    assert "semantic capability outcome completed does not match" in (
        _operation_capability_errors(
            wrong_outcomes,
            wrong_outcomes["contract"]["semantic_capabilities"][0],
        )
    )


def test_semantic_capability_rejects_swapped_argument_required_flags() -> None:
    export = _declared_export(
        "rutter.interface.constructor",
        102,
        "officina.rutter.Voyage",
        role="construction",
        operation="construct-voyage",
    )
    capability = export["contract"]["semantic_capabilities"][0]
    capability["required_arguments"] = ["options"]
    capability["optional_arguments"] = ["context"]

    errors = _operation_capability_errors(export, capability)

    assert (
        "semantic capability required/optional declarations do not match "
        "argument required flags"
    ) in errors


@pytest.mark.parametrize("mutation", ("missing", "extra"))
def test_semantic_capability_requires_complete_interface_outcomes(
    mutation: str,
) -> None:
    export = _declared_export(
        "rutter.interface.constructor",
        103,
        "officina.rutter.Voyage",
        role="construction",
        operation="construct-voyage",
    )
    capability = export["contract"]["semantic_capabilities"][0]
    if mutation == "missing":
        capability["outcomes"].pop("refused")
    else:
        capability["outcomes"]["invented"] = {
            "class": "error",
            "outputs": [],
        }

    errors = _operation_capability_errors(export, capability)

    assert "semantic capability outcomes do not equal complete interface outcome set" in (
        errors
    )


def test_construction_output_must_be_emitted_by_a_success_outcome() -> None:
    export = _declared_export(
        "rutter.interface.constructor",
        104,
        "officina.rutter.Voyage",
        role="construction",
        operation="construct-voyage",
    )
    capability = export["contract"]["semantic_capabilities"][0]
    capability["outcomes"]["completed"]["outputs"] = []
    export["contract"]["outcomes"][0]["outputs"] = []

    errors = _operation_capability_errors(export, capability)

    assert (
        "nominal construction output is not emitted by a declared success outcome"
    ) in errors


def test_design_stage_declares_the_same_live_probe_boundary() -> None:
    """Removing a required probe or naming only an interface must block design."""
    instruction = (SKILL_ROOT / "instructions/design-implementation.md").read_text(
        encoding="utf-8"
    )
    normalized_instruction = " ".join(instruction.split())
    source = _load_yaml(
        SKILL_ROOT / "blueprints/instructions-design-implementation.yaml"
    )
    contract = source["interfaces"][
        "distill-to-rutters.source.design-implementation.interface.design-implementation"
    ]["contract"]

    for requirement in (
        "every production compatibility probe passes",
        "one real bound instance",
        "dispatcher dry-run is insufficient",
        "Do not add core exports, adapters, or shims",
        "hardening-complete; runtime-blocked",
        "discover each public interface version from the checked-out root export",
        "rutter-binding-construction",
        "required_arguments",
        "optional_arguments",
        "semantic capability outcomes",
        "structured nominal Python type",
        "qualified_class",
        "complete nominal type and schema",
        "actual `required` flag",
        "complete interface outcome set",
        "successful construction outcome",
        "preserve every valid constructor candidate",
        "exact accepted tuple",
        "reject unresolved ambiguity",
        "interface`, `version`, `operation`, `output`, `capability`, and `nominal_type",
        "No interface version is predicted",
        "rutter-root-export:base-rutter-construction:contract.semantic_capabilities",
        "rutter-root-export:voyage-construction:contract.semantic_capabilities",
        "rutter-root-export:voyage-execution:contract.semantic_capabilities",
        "rutter-root-export:voyage-dispenser-construction:contract.semantic_capabilities",
        "rutter-root-export:voyage-dispenser-execution:contract.semantic_capabilities",
    ):
        assert requirement in normalized_instruction
    runtime_read = next(
        item
        for item in contract["direct_io"]["reads"]
        if item["id"] == "runtime-contracts"
    )
    for requirement in (
        "rutter-binding-construction",
        "structured nominal Python type",
        "officina.rutter.BaseRutter",
        "officina.rutter.Voyage",
        "officina.rutter.VoyageDispenser",
        "binding.accepts",
    ):
        assert requirement in runtime_read["content"]
    for guessed in (
        "rutter.interface.binding@3",
        "rutter.interface.bound-operations@6",
        "rutter.interface.dispenser@5",
    ):
        assert guessed not in normalized_instruction
        assert guessed not in runtime_read["content"]
    outcomes = {item["id"]: item for item in contract["outcomes"]}
    assert outcomes["design-ready"]["class"] == "success"
    assert outcomes["design-blocked"]["class"] == "refusal"
    assert isinstance(outcomes["design-blocked"]["caller_action"], str)
    assert set(outcomes["design-blocked"]) == {
        "id",
        "class",
        "outputs",
        "effects",
        "caller_action",
    }


def test_module_owns_the_deterministic_probe_without_claiming_a_live_artifact() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    runtime_root = _load_yaml(SKILL_ROOT / "_rtx/blueprint.yaml")
    source = _load_yaml(
        SKILL_ROOT / "_rtx/blueprints/rtx-artifact-contract.yaml"
    )

    assert re.fullmatch(
        root["content"][0],
        "tests/test_runtime_compatibility.py",
    )
    assert any(
        re.fullmatch(pattern, "_runtime_compatibility.py")
        for pattern in runtime_root["content"]
    )
    assert any(
        re.fullmatch(pattern, "_runtime_compatibility.py")
        for pattern in source["content"]
    )
    assert not list(SKILL_ROOT.rglob("05_implementation_design.md"))
    assert root["maturity"] == "experimental"


def _nominal(qualified_class: str, schema_version: int = 1) -> dict[str, Any]:
    return {
        "language": "python",
        "qualified_class": qualified_class,
        "schema": {
            "id": f"python:{qualified_class}",
            "version": schema_version,
        },
    }


def _declared_export(
    export_id: str,
    version: int,
    qualified_class: str,
    *,
    role: str,
    operation: str,
) -> dict[str, Any]:
    output_id = "bound-result" if role == "construction" else "advance-result"
    arguments: dict[str, Any] = {
        "operation": {
            "required": True,
            "type": {
                "kind": "enum",
                "values": [{"value": operation}],
            },
        },
        "context": {"required": True, "type": {"kind": "string"}},
        "options": {"required": False, "type": {"kind": "string"}},
    }
    capability: dict[str, Any] = {
        "id": (
            "rutter-binding-construction"
            if role == "construction"
            else f"{export_id.rsplit('.', 1)[-1]}-advance"
        ),
        "role": role,
        "semantic_operation": "construct" if role == "construction" else "advance",
        "operation": operation,
        "required_arguments": ["context"],
        "optional_arguments": ["options"],
        "outcomes": {
            "completed": {"class": "success", "outputs": [output_id]},
            "refused": {"class": "refusal", "outputs": []},
        },
        "output": output_id,
    }
    if role == "execution":
        arguments["binding"] = {
            "required": True,
            "type": {"kind": "string"},
            "nominal_type": _nominal(qualified_class),
        }
        capability["required_arguments"].append("binding")
        capability["binding_argument"] = "binding"
        output_nominal = _nominal("officina.rutter.EvolutionView")
    else:
        output_nominal = _nominal(qualified_class)
    return {
        "export_id": export_id,
        "source_interface": f"synthetic.source.interface.{role}",
        "version": version,
        "contract": {
            "arguments": arguments,
            "outputs": [
                {
                    "id": output_id,
                    "type": {"kind": "string"},
                    "nominal_type": output_nominal,
                }
            ],
            "outcomes": [
                {
                    "id": "completed",
                    "class": "success",
                    "outputs": [output_id],
                },
                {"id": "refused", "class": "refusal", "outputs": []},
            ],
            "semantic_capabilities": [capability],
        },
    }


def test_declared_profiles_discover_legitimate_alternate_versions() -> None:
    exports = {
        "rutter.interface.base-constructor": _declared_export(
            "rutter.interface.base-constructor",
            17,
            "officina.rutter.BaseRutter",
            role="construction",
            operation="make-rutter",
        ),
        "rutter.interface.voyage-constructor": _declared_export(
            "rutter.interface.voyage-constructor",
            23,
            "officina.rutter.Voyage",
            role="construction",
            operation="launch-voyage",
        ),
        "rutter.interface.voyage-runtime": _declared_export(
            "rutter.interface.voyage-runtime",
            31,
            "officina.rutter.Voyage",
            role="execution",
            operation="continue-voyage",
        ),
        "rutter.interface.dispenser-constructor": _declared_export(
            "rutter.interface.dispenser-constructor",
            41,
            "officina.rutter.VoyageDispenser",
            role="construction",
            operation="build-dispenser",
        ),
        "rutter.interface.dispenser-runtime": _declared_export(
            "rutter.interface.dispenser-runtime",
            43,
            "officina.rutter.VoyageDispenser",
            role="execution",
            operation="advance-selected-voyage",
        ),
    }

    profiles = {
        name: _discover_nominal_profiles(exports, qualified_class, role)[0][0]
        for name, qualified_class, role in (
            ("base", "officina.rutter.BaseRutter", "construction"),
            ("voyage-construction", "officina.rutter.Voyage", "construction"),
            ("voyage-execution", "officina.rutter.Voyage", "execution"),
            (
                "dispenser-construction",
                "officina.rutter.VoyageDispenser",
                "construction",
            ),
            (
                "dispenser-execution",
                "officina.rutter.VoyageDispenser",
                "execution",
            ),
        )
    }

    assert {name: profile["version"] for name, profile in profiles.items()} == {
        "base": 17,
        "voyage-construction": 23,
        "voyage-execution": 31,
        "dispenser-construction": 41,
        "dispenser-execution": 43,
    }
    dispenser = profiles["dispenser-construction"]
    nominal_type = dispenser["nominal_type"]
    compass = {
        "version": 88,
        "contract": {
            "arguments": {
                "binding": {
                    "required": True,
                    "type": {"kind": "string"},
                    "accepts": {
                        "interface": dispenser["interface"],
                        "version": dispenser["version"],
                        "operation": dispenser["operation"],
                        "output": dispenser["output"],
                        "capability": "rutter-binding-construction",
                        "nominal_type": nominal_type,
                    },
                }
            }
        },
    }

    assert _resolved_compass_handoff_errors(
        compass,
        exports,
        tuple(profiles.values()),
    ) == ()


def test_compass_selects_exact_nonfirst_valid_constructor() -> None:
    exports = {
        "rutter.interface.alpha-constructor": _declared_export(
            "rutter.interface.alpha-constructor",
            11,
            "officina.rutter.VoyageDispenser",
            role="construction",
            operation="construct-alpha",
        ),
        "rutter.interface.zeta-constructor": _declared_export(
            "rutter.interface.zeta-constructor",
            29,
            "officina.rutter.VoyageDispenser",
            role="construction",
            operation="construct-zeta",
        ),
    }
    candidates, errors = _discover_nominal_profiles(
        exports,
        "officina.rutter.VoyageDispenser",
        "construction",
    )
    selected = candidates[1]
    compass = {
        "contract": {
            "arguments": {
                "binding": {
                    "required": True,
                    "type": {"kind": "string"},
                    "accepts": {
                        "interface": selected["interface"],
                        "version": selected["version"],
                        "operation": selected["operation"],
                        "output": selected["output"],
                        "capability": selected["capability"],
                        "nominal_type": selected["nominal_type"],
                    },
                }
            }
        },
    }

    assert errors == ()
    assert [candidate["interface"] for candidate in candidates] == [
        "rutter.interface.alpha-constructor",
        "rutter.interface.zeta-constructor",
    ]
    assert _resolved_compass_handoff_errors(compass, exports, candidates) == ()


def test_compass_rejects_duplicate_exact_constructor_ambiguity() -> None:
    export_id = "rutter.interface.constructor"
    producer = _declared_export(
        export_id,
        37,
        "officina.rutter.VoyageDispenser",
        role="construction",
        operation="construct",
    )
    candidate, errors = _discover_nominal_profiles(
        {export_id: producer},
        "officina.rutter.VoyageDispenser",
        "construction",
    )
    selected = candidate[0]
    compass = {
        "contract": {
            "arguments": {
                "binding": {
                    "required": True,
                    "type": {"kind": "string"},
                    "accepts": {
                        "interface": selected["interface"],
                        "version": selected["version"],
                        "operation": selected["operation"],
                        "output": selected["output"],
                        "capability": selected["capability"],
                        "nominal_type": selected["nominal_type"],
                    },
                }
            }
        },
    }

    assert errors == ()
    assert "accepted construction tuple is ambiguous" in (
        _resolved_compass_handoff_errors(
            compass,
            {export_id: producer},
            (selected, deepcopy(selected)),
        )
    )


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("nonexistent-output", "accepted producer output does not exist"),
        (
            "wrong-existing-output",
            "accepted operation lacks rutter-binding-construction capability",
        ),
        ("wrong-version", "accepted producer version does not exist"),
        ("shallow-string", "producer output lacks a structured nominal Python type"),
        ("wrong-class", "accepted nominal type does not match producer output"),
        ("wrong-schema", "accepted nominal type does not match producer output"),
        (
            "missing-capability",
            "accepted operation lacks rutter-binding-construction capability",
        ),
    ),
)
def test_compass_handoff_rejects_non_nominal_or_unconstructed_outputs(
    mutation: str,
    expected_error: str,
) -> None:
    export_id = "rutter.interface.constructor"
    producer = _declared_export(
        export_id,
        73,
        "officina.rutter.VoyageDispenser",
        role="construction",
        operation="construct",
    )
    capability = producer["contract"]["semantic_capabilities"][0]
    output = producer["contract"]["outputs"][0]
    nominal_type = deepcopy(output["nominal_type"])
    accepts = {
        "interface": export_id,
        "version": 73,
        "operation": "construct",
        "output": "bound-result",
        "capability": "rutter-binding-construction",
        "nominal_type": nominal_type,
    }
    if mutation == "nonexistent-output":
        accepts["output"] = "missing-result"
    elif mutation == "wrong-existing-output":
        producer["contract"]["outputs"].append(
            {
                "id": "other-result",
                "type": {"kind": "string"},
                "nominal_type": deepcopy(nominal_type),
            }
        )
        accepts["output"] = "other-result"
    elif mutation == "wrong-version":
        accepts["version"] = 72
    elif mutation == "shallow-string":
        output.pop("nominal_type")
    elif mutation == "wrong-class":
        accepts["nominal_type"]["qualified_class"] = "officina.rutter.Voyage"
    elif mutation == "wrong-schema":
        accepts["nominal_type"]["schema"]["version"] = 2
    else:
        capability["id"] = "unrelated-construction"
    compass = {
        "contract": {
            "arguments": {
                "binding": {
                    "required": True,
                    "type": {"kind": "string"},
                    "accepts": accepts,
                }
            }
        }
    }

    errors = _resolved_compass_handoff_errors(
        compass,
        {export_id: producer},
        {},
    )

    assert expected_error in errors


def test_root_export_resolution_discovers_source_declared_version(
    tmp_path: Path,
) -> None:
    module_root = tmp_path / "runtime"
    blueprint_root = module_root / "blueprints"
    blueprint_root.mkdir(parents=True)
    root = {
        "exports": {
            "rutter.interface.alternate": {
                "source_interface": "rutter.source.alternate.interface.default"
            }
        },
        "sources": {
            "rutter.source.alternate": {
                "blueprint": {
                    "base": "module-root",
                    "path": "blueprints/alternate.yaml",
                }
            }
        },
    }
    source = {
        "interfaces": {
            "rutter.source.alternate.interface.default": {
                "version": 907,
                "contract": {"semantic_capabilities": []},
            }
        }
    }
    (module_root / "blueprint.yaml").write_text(
        yaml.safe_dump(root, sort_keys=False),
        encoding="utf-8",
    )
    (blueprint_root / "alternate.yaml").write_text(
        yaml.safe_dump(source, sort_keys=False),
        encoding="utf-8",
    )

    exported = _exported_interfaces(module_root)

    assert exported["rutter.interface.alternate"]["version"] == 907
    assert exported["rutter.interface.alternate"]["source_interface"] == (
        "rutter.source.alternate.interface.default"
    )


def test_current_missing_evidence_contains_no_guessed_interface_versions(
    tmp_path: Path,
) -> None:
    runtime = _load_production_runtime()
    result = runtime.probe_runtime_compatibility(REPOSITORY_ROOT, tmp_path)
    evidence = repr(result["missing_evidence"])

    assert result["outcome"] == "design-blocked"
    assert "@3" not in evidence
    assert "@5" not in evidence
    assert "@6" not in evidence
    assert "rutter-binding-construction" in evidence
    assert "structured nominal Python type" in evidence


@pytest.mark.parametrize("mutation", ("absolute", "parent", "symlink"))
def test_production_runtime_discovery_rejects_uncontained_source_locators(
    tmp_path: Path,
    mutation: str,
) -> None:
    runtime = _load_production_runtime()
    repository = tmp_path / "repo"
    module_root = repository / "runtime"
    module_root.mkdir(parents=True)
    (repository / ".git").mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("interfaces: {}\n", encoding="utf-8")
    inside_repository = repository / "outside-module.yaml"
    inside_repository.write_text("interfaces: {}\n", encoding="utf-8")
    if mutation == "absolute":
        locator = str(outside)
    elif mutation == "parent":
        locator = "../outside-module.yaml"
    else:
        link = module_root / "linked.yaml"
        link.symlink_to(outside)
        locator = "linked.yaml"
    root = {
        "exports": {
            "runtime.interface.default": {
                "source_interface": "runtime.source.default.interface.default"
            }
        },
        "sources": {
            "runtime.source.default": {
                "blueprint": {"base": "module-root", "path": locator}
            }
        },
    }
    (module_root / "blueprint.yaml").write_text(
        yaml.safe_dump(root, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(runtime.RuntimeCompatibilityError, match="locator|escape"):
        runtime.exported_interfaces(module_root, repository)


def test_production_runtime_discovery_rejects_a_symlinked_module_root(
    tmp_path: Path,
) -> None:
    runtime = _load_production_runtime()
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    outside_module = tmp_path / "outside-runtime"
    outside_module.mkdir()
    (outside_module / "blueprint.yaml").write_text(
        "exports: {}\nsources: {}\n",
        encoding="utf-8",
    )
    linked_module = repository / "runtime"
    linked_module.symlink_to(outside_module, target_is_directory=True)

    with pytest.raises(runtime.RuntimeCompatibilityError, match="escape"):
        runtime.exported_interfaces(linked_module, repository)
