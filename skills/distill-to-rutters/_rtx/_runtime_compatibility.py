"""Deterministic probe of the checked-out public Rutter and Compass contracts."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

import officina.rutter as public_rutter
from officina.rutter import (
    EvolutionView,
    LLMStep,
    Message,
    Rutter,
    RutterRegistry,
    Terminal,
    VoyageResult,
    VoyageStatus,
)


ReadBytes = Callable[[Path], bytes]


class RuntimeCompatibilityError(ValueError):
    """Raised when public-runtime discovery cannot be contained or interpreted."""


def _resolved_directory(path: Path, repository_root: Path, *, label: str) -> Path:
    try:
        repository = repository_root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeCompatibilityError(f"cannot resolve {label}: {exc}") from exc
    try:
        resolved.relative_to(repository)
    except ValueError as exc:
        raise RuntimeCompatibilityError(f"{label} escapes repository") from exc
    if not resolved.is_dir():
        raise RuntimeCompatibilityError(f"{label} is not a directory")
    return resolved


def _resolved_locator(
    module_root: Path,
    repository_root: Path,
    locator: str,
    *,
    label: str,
) -> Path:
    candidate = Path(locator)
    if candidate.is_absolute() or not locator or ".." in candidate.parts:
        raise RuntimeCompatibilityError(
            f"{label} locator must be nonempty, module-root-relative, and traversal-free"
        )
    module = _resolved_directory(module_root, repository_root, label="module root")
    repository = repository_root.resolve(strict=True)
    try:
        resolved = (module / candidate).resolve(strict=True)
    except OSError as exc:
        raise RuntimeCompatibilityError(
            f"cannot resolve {label} locator {locator}: {exc}"
        ) from exc
    for boundary, boundary_label in (
        (repository, "repository"),
        (module, "module root"),
    ):
        try:
            resolved.relative_to(boundary)
        except ValueError as exc:
            raise RuntimeCompatibilityError(
                f"{label} locator escapes {boundary_label}: {locator}"
            ) from exc
    if not resolved.is_file():
        raise RuntimeCompatibilityError(
            f"{label} locator is not a regular file: {locator}"
        )
    return resolved


def _load_yaml(path: Path, read_bytes: ReadBytes) -> Mapping[str, Any]:
    try:
        text = read_bytes(path).decode("utf-8")
        loaded = yaml.safe_load(text)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RuntimeCompatibilityError(f"cannot load public blueprint {path}: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise RuntimeCompatibilityError(f"public blueprint is not a mapping: {path}")
    return loaded


def exported_interfaces(
    module_root: Path,
    repository_root: Path,
    read_bytes: ReadBytes | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve every public export through a strictly contained source locator."""
    reader = read_bytes or Path.read_bytes
    module = _resolved_directory(module_root, repository_root, label="module root")
    root_path = _resolved_locator(
        module,
        repository_root,
        "blueprint.yaml",
        label="root blueprint",
    )
    root = _load_yaml(root_path, reader)
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
        locator = source_entry.get("blueprint") if isinstance(source_entry, Mapping) else None
        if (
            not isinstance(locator, Mapping)
            or locator.get("base") != "module-root"
            or not isinstance(locator.get("path"), str)
        ):
            raise RuntimeCompatibilityError(
                f"export {export_id} has an invalid module-root source locator"
            )
        source_path = _resolved_locator(
            module,
            repository_root,
            str(locator["path"]),
            label=f"export {export_id}",
        )
        source = _load_yaml(source_path, reader)
        interfaces = source.get("interfaces")
        interface = interfaces.get(source_interface) if isinstance(interfaces, Mapping) else None
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


def _exported_interface(
    exported: Mapping[str, Mapping[str, Any]],
    export_id: str,
) -> Mapping[str, Any] | None:
    export = exported.get(export_id)
    definition = export.get("definition") if isinstance(export, Mapping) else None
    return definition if isinstance(definition, Mapping) else None


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
    if not isinstance(value.get("qualified_class"), str) or not value.get("qualified_class"):
        return ("nominal Python qualified class is missing",)
    schema = value.get("schema")
    if not isinstance(schema, Mapping) or not schema:
        return ("nominal Python schema is missing",)
    return ()


def _semantic_capabilities(export: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    contract = export.get("contract")
    rows = contract.get("semantic_capabilities") if isinstance(contract, Mapping) else None
    return (
        tuple(row for row in rows if isinstance(row, Mapping))
        if isinstance(rows, list)
        else ()
    )


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


def operation_capability_errors(
    export: Mapping[str, Any],
    capability: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return structural mismatches for one operation-specific capability."""
    errors: list[str] = []
    contract = export.get("contract")
    if not isinstance(contract, Mapping):
        return ("source interface contract is missing",)
    operation = capability.get("operation")
    if not isinstance(operation, str) or operation not in _operation_values(contract):
        errors.append("declared semantic operation does not exist")
    arguments = contract.get("arguments")
    argument_ids = set(arguments) - {"operation"} if isinstance(arguments, Mapping) else set()
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
                "semantic capability required/optional declarations do not match argument required flags"
            )
    declared_outcomes = capability.get("outcomes")
    outcomes = contract.get("outcomes")
    indexed_outcomes = (
        {
            row.get("id"): row
            for row in outcomes
            if isinstance(row, Mapping) and isinstance(row.get("id"), str)
        }
        if isinstance(outcomes, list)
        else {}
    )
    if not isinstance(declared_outcomes, Mapping) or not declared_outcomes:
        errors.append("semantic capability outcomes are missing")
    else:
        if set(declared_outcomes) != set(indexed_outcomes):
            errors.append(
                "semantic capability outcomes do not equal complete interface outcome set"
            )
        for outcome_id, declared in declared_outcomes.items():
            actual = indexed_outcomes.get(outcome_id)
            if not (
                isinstance(declared, Mapping)
                and isinstance(actual, Mapping)
                and declared.get("class") == actual.get("class")
                and tuple(declared.get("outputs", ())) == tuple(actual.get("outputs", ()))
            ):
                errors.append(f"semantic capability outcome {outcome_id} does not match")
        if capability.get("role") == "construction" and not any(
            isinstance(declared, Mapping)
            and declared.get("class") == "success"
            and capability.get("output") in declared.get("outputs", ())
            for declared in declared_outcomes.values()
        ):
            errors.append(
                "nominal construction output is not emitted by a declared success outcome"
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


def discover_nominal_profiles(
    exported: Mapping[str, Mapping[str, Any]],
    qualified_class: str,
    role: str,
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    """Discover every exact nominal construction or execution profile."""
    expected_semantic = "construct" if role == "construction" else "advance"
    candidates: list[dict[str, Any]] = []
    candidate_errors: list[str] = []
    for export_id, export in exported.items():
        for capability in _semantic_capabilities(export):
            if (
                capability.get("role") != role
                or capability.get("semantic_operation") != expected_semantic
            ):
                continue
            if role == "construction" and capability.get("id") != "rutter-binding-construction":
                continue
            output = _contract_output(export, capability.get("output"))
            contract = export.get("contract")
            arguments = contract.get("arguments") if isinstance(contract, Mapping) else None
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
            if not isinstance(nominal, Mapping) or nominal.get("qualified_class") != qualified_class:
                continue
            errors = operation_capability_errors(export, capability)
            if errors:
                candidate_errors.extend(f"{export_id}: {error}" for error in errors)
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


def resolved_compass_handoff_errors(
    compass_interface: Mapping[str, Any] | None,
    producer_interfaces: Mapping[str, Mapping[str, Any]],
    ready_constructions: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Validate Compass's exact accepted constructor tuple."""
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
    if _nominal_python_type_errors(accepts.get("nominal_type")):
        errors.append("binding.accepts lacks a structured nominal Python type")
    interface_id = accepts.get("interface")
    producer = producer_interfaces.get(interface_id) if isinstance(interface_id, str) else None
    if producer is None:
        return tuple([*errors, "accepted producer interface does not exist"])
    if not isinstance(producer.get("source_interface"), str) or not producer.get("source_interface"):
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
    if accepts.get("capability") != "rutter-binding-construction" or not isinstance(capability, Mapping):
        errors.append("accepted operation lacks rutter-binding-construction capability")
    else:
        errors.extend(operation_capability_errors(producer, capability))
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


def _public_transition_probe() -> Rutter:
    """Return one direct definition using only the live public Rutter API."""

    return Rutter(
        id="public-transition-probe",
        version=1,
        start="inspect",
        evolutions={
            "inspect": LLMStep(
                "Inspect the public transition probe.",
                response_schema={
                    "type": "object",
                    "properties": {"outcome": {"const": "accepted"}},
                    "required": ["outcome"],
                    "additionalProperties": False,
                },
                next_on_outcome="complete",
            ),
            "complete": Terminal(result=VoyageResult("completed", {})),
        },
    )


def probe_runtime_compatibility(
    repository_root: Path,
    scratch_root: Path,
    read_bytes: ReadBytes | None = None,
) -> dict[str, Any]:
    """Probe live public declarations and one real bound transition."""
    repository = repository_root.resolve(strict=True)
    reader = read_bytes or Path.read_bytes
    rutter_root = repository / "src/officina/rutter"
    compass_root = repository / "skills/using-compass"
    exported = exported_interfaces(rutter_root, repository, reader)
    compass_exports = exported_interfaces(compass_root, repository, reader)
    construction_interface = _exported_interface(exported, "rutter.interface.binding")
    construction = (
        construction_interface.get("contract")
        if isinstance(construction_interface, Mapping)
        else None
    )
    bound_operations_interface = _exported_interface(
        exported,
        "rutter.interface.bound-operations",
    )
    bound_operations = (
        bound_operations_interface.get("contract")
        if isinstance(bound_operations_interface, Mapping)
        else None
    )
    compass_interface = _exported_interface(
        compass_exports,
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
        "Rutter",
        "Charter",
        "EvolutionView",
        "LLMStep",
        "Message",
        "RutterRegistry",
        "Terminal",
        "ValidationIssue",
        "ValidationReport",
        "Voyage",
        "VoyageDispenser",
        "VoyageResult",
        "VoyageStatus",
    ):
        if name not in public_names or not hasattr(public_rutter, name):
            missing_evidence[f"python-export:officina.rutter.{name}"] = (
                "public Python export is missing",
            )
    construction_operations = _operation_values(construction)
    public_bound_operations = _operation_values(bound_operations)

    runtime_profiles: dict[str, dict[str, Any]] = {}
    ready_constructions: list[Mapping[str, Any]] = []
    for profile_id, qualified_class, role in (
        ("rutter-construction", "officina.rutter.Rutter", "construction"),
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
        profiles, errors = discover_nominal_profiles(exported, qualified_class, role)
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
    handoff_errors = resolved_compass_handoff_errors(
        compass_interface,
        exported,
        ready_constructions,
    )
    if handoff_errors:
        missing_evidence[
            "using-compass.source.gateway.interface.default.contract.arguments.binding.accepts"
        ] = handoff_errors
    binding_type = binding.get("type", {}) if isinstance(binding, Mapping) else {}
    accepted_type = (
        binding_type.get("description") if isinstance(binding_type, Mapping) else None
    )

    scratch_root.mkdir(parents=True, exist_ok=True)
    reckoning_path = Path("probe.reckoning.json")
    registry = RutterRegistry({"probe": _public_transition_probe()}, scratch_root)
    bound = registry.create("probe", reckoning_path, {})
    initial_status = bound.get_status()
    assert isinstance(initial_status, VoyageStatus)
    message = initial_status.instruction
    assert isinstance(message, Message)
    response = {"outcome": "accepted"}
    validation = bound.validate(
        response,
        responding_to=message.evolution_entry_id,
    )
    assert validation.valid
    successor = bound.advance(
        response,
        responding_to=message.evolution_entry_id,
    )
    assert isinstance(successor, EvolutionView)
    status = bound.get_status()
    transition = {
        "successor_type": type(successor).__name__,
        "evolution": successor.evolution_id,
        "condition": successor.condition,
        "status_type": type(status).__name__,
        "status_evolution": status.current_evolution.evolution_id,
        "status_condition": status.current_evolution.condition,
    }
    bound_type = type(bound).__name__
    del bound
    reopened_registry = RutterRegistry(
        {"probe": _public_transition_probe()},
        scratch_root,
    )
    reopened = reopened_registry.open(reckoning_path)
    reopened_status = reopened.get_status()

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
            "status_type": type(reopened_status).__name__,
            "evolution": reopened_status.current_evolution.evolution_id,
            "condition": reopened_status.current_evolution.condition,
        },
    }
