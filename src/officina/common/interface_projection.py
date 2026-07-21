"""Consumer-local, non-granting projection of selected interface contracts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Mapping

import yaml

from .blueprint_graph import (
    BlueprintGraphError,
    HelperEdge,
    InterfaceExport,
    MachineInterfaceExport,
    RepositoryBlueprintGraph,
    resolve_export,
    resolve_machine_export,
)
from .blueprint_inventory import JsonValue
from .certification_view import CertificationView


class InterfaceProjectionError(ValueError):
    """Raised when selected contracts cannot form a safe bounded projection."""


@dataclass(frozen=True)
class InterfaceProjection:
    consumer_id: str
    document: Mapping[str, JsonValue]
    vocabulary: frozenset[str]


_COMBINED_LIMIT = 16_384


def _canonical_yaml_bytes(value: object) -> bytes:
    return yaml.safe_dump(
        value,
        sort_keys=True,
        allow_unicode=True,
        default_flow_style=False,
    ).encode("utf-8")


def standalone_export_size(export_projection: Mapping[str, JsonValue]) -> int:
    """Return the canonical UTF-8 size used by Phase 4's 12,288-byte rule."""

    return len(_canonical_yaml_bytes(dict(export_projection)))


def _provider_skill(export: MachineInterfaceExport) -> str:
    return export.module_node_id.split(".machine-module.", 1)[0]


def _checked_file(owner_root: Path, relative_text: str, *, base: Path | None = None) -> Path:
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise InterfaceProjectionError(f"reference path escapes provider root: {relative_text}")
    owner = Path(os.path.abspath(owner_root))
    path = Path(os.path.abspath((base or owner) / relative))
    try:
        path.relative_to(owner)
    except ValueError as exc:
        raise InterfaceProjectionError(f"reference path escapes provider root: {relative_text}") from exc
    current = owner
    try:
        for component in path.relative_to(owner).parts:
            current = current / component
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise InterfaceProjectionError(f"referenced path contains symlink: {path}")
        if not stat.S_ISREG(metadata.st_mode):
            raise InterfaceProjectionError(f"referenced path is not a regular file: {path}")
    except FileNotFoundError as exc:
        raise InterfaceProjectionError(f"referenced path does not exist: {path}") from exc
    return path


def _fragment_value(document: object, fragment: str, path: Path) -> object:
    if fragment in {"", "#"}:
        return deepcopy(document)
    if not fragment.startswith("#/"):
        raise InterfaceProjectionError(f"{path}: unsupported fragment {fragment!r}")
    current = document
    try:
        for raw in fragment[2:].split("/"):
            part = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, Mapping):
                current = current[part]
            else:
                raise KeyError(part)
    except (KeyError, IndexError, ValueError) as exc:
        raise InterfaceProjectionError(f"{path}: unresolved fragment {fragment!r}") from exc
    return deepcopy(current)


class _DefinitionResolver:
    def __init__(self, provider_root: Path, provider_skill: str) -> None:
        self.provider_root = provider_root
        self.provider_skill = provider_skill
        self.definitions: dict[str, dict[str, JsonValue]] = {}
        self.identities: dict[tuple[str, str], str] = {}

    def ensure(
        self,
        relative_path: str,
        fragment: str,
        *,
        base: Path | None = None,
    ) -> str:
        path = _checked_file(self.provider_root, relative_path, base=base)
        owner_relative = path.relative_to(self.provider_root).as_posix()
        identity = (owner_relative, fragment)
        existing = self.identities.get(identity)
        if existing is not None:
            return existing
        key = "definition-" + hashlib.sha256(
            f"{self.provider_skill}:{owner_relative}:{fragment}".encode("utf-8")
        ).hexdigest()[:16]
        self.identities[identity] = key
        payload = path.read_bytes()
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        try:
            text = payload.decode("utf-8")
            document = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
        except (UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise InterfaceProjectionError(f"{path}: invalid referenced definition: {exc}") from exc
        self.definitions[key] = {
            "source_module": self.provider_skill,
            "path": owner_relative,
            "fragment": fragment,
            "digest": digest,
            "value": None,
        }
        value = _fragment_value(document, fragment, path)
        self.definitions[key]["value"] = self.transform(value, current_file=path)
        return key

    def transform(self, value: object, *, current_file: Path | None = None) -> JsonValue:
        if isinstance(value, Mapping):
            ref = value.get("$ref")
            if isinstance(ref, str):
                path_text, separator, fragment_text = ref.partition("#")
                if "://" in path_text:
                    raise InterfaceProjectionError(f"external reference URI is unsupported: {ref}")
                target_path = path_text or (
                    current_file.relative_to(self.provider_root).as_posix()
                    if current_file is not None
                    else ""
                )
                if not target_path:
                    raise InterfaceProjectionError(f"unscoped internal reference: {ref}")
                base = (
                    current_file.parent
                    if current_file is not None and path_text
                    else self.provider_root
                )
                key = self.ensure(
                    target_path,
                    f"#{fragment_text}" if separator else "#",
                    base=base,
                )
                siblings = {
                    str(child_key): self.transform(child, current_file=current_file)
                    for child_key, child in value.items()
                    if child_key != "$ref"
                }
                return {"definition_ref": key, **siblings}
            path = value.get("path")
            fragment = value.get("fragment")
            if isinstance(path, str) and isinstance(fragment, str):
                self.ensure(path, fragment)
                return {
                    str(key): self.transform(child, current_file=current_file)
                    for key, child in value.items()
                }
            return {
                str(key): self.transform(child, current_file=current_file)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [self.transform(child, current_file=current_file) for child in value]
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        raise InterfaceProjectionError(f"non-JSON projected value: {type(value).__name__}")


def _certify(
    certification: CertificationView,
    module_id: str,
    export: MachineInterfaceExport,
) -> None:
    decision = certification.check_export(module_id, export.interface_id, export.version)
    if not decision.certified:
        raise InterfaceProjectionError(
            f"{export.interface_id}: certification rejected [{decision.code}]: {decision.message}"
        )


def _project_export(
    graph: RepositoryBlueprintGraph,
    export: MachineInterfaceExport,
    certification: CertificationView,
    resolvers: dict[str, _DefinitionResolver],
    vocabulary: set[str],
) -> dict[str, JsonValue]:
    module, resolved = resolve_machine_export(graph, export.interface_id, export.version)
    _certify(certification, module.node_id, resolved)
    resolver = resolvers.setdefault(
        module.node_id,
        _DefinitionResolver(module.skill_root, _provider_skill(export)),
    )
    declaration = export.declaration
    projection = {
        "id": export.interface_id,
        "version": export.version,
        "description": deepcopy(declaration.get("description")),
        "invocation_binding": resolver.transform(
            deepcopy(declaration.get("invocation_binding", {"fixed": []}))
        ),
        "direct_io": resolver.transform(
            deepcopy(declaration.get("direct_io", {"reads": [], "writes": [], "network": []}))
        ),
        "helpers": resolver.transform(deepcopy(declaration.get("helpers", []))),
        "contract": resolver.transform(deepcopy(declaration.get("contract", {}))),
    }
    contract = declaration.get("contract", {})
    arguments = contract.get("arguments", {}) if isinstance(contract, Mapping) else {}
    if isinstance(arguments, Mapping):
        for argument in arguments.values():
            if not isinstance(argument, Mapping):
                continue
            binding = argument.get("invocation_binding")
            type_spec = argument.get("type")
            if isinstance(binding, Mapping) and isinstance(binding.get("kind"), str):
                vocabulary.add(f"binding:{binding['kind']}")
                arity = binding.get("arity")
                if isinstance(arity, Mapping):
                    minimum = arity.get("minimum")
                    maximum = arity.get("maximum")
                    if maximum is None:
                        vocabulary.add(
                            "arity:one-or-more" if minimum else "arity:zero-or-more"
                        )
                    elif argument.get("required") is True:
                        vocabulary.add("arity:required")
                    else:
                        vocabulary.add("arity:optional")
                elif binding.get("kind") == "switch":
                    vocabulary.add("binding:switch")
                    vocabulary.add(
                        "arity:required"
                        if argument.get("required") is True
                        else "arity:optional"
                    )
                elif binding.get("kind") == "stdin":
                    vocabulary.add(
                        "arity:required"
                        if argument.get("required") is True
                        else "arity:optional"
                    )
            if isinstance(type_spec, Mapping) and isinstance(type_spec.get("kind"), str):
                vocabulary.add(f"type:{type_spec['kind']}")
    if declaration.get("helpers"):
        vocabulary.add("helpers")
    if isinstance(contract, Mapping) and contract.get("outcomes"):
        vocabulary.add("outcomes")
    if isinstance(contract, Mapping) and contract.get("execution"):
        vocabulary.add("execution")
    return projection


def _validate_helper_target(edge: HelperEdge, target: MachineInterfaceExport) -> None:
    binding = edge.binding
    route = binding.get("route") if isinstance(binding, Mapping) else None
    if not isinstance(route, Mapping) or route.get("kind") != "argument-enum":
        return
    contract = target.declaration.get("contract")
    execution = contract.get("execution") if isinstance(contract, Mapping) else None
    if not isinstance(execution, Mapping) or execution.get("state_effect") != "read-only":
        raise InterfaceProjectionError(
            f"{edge.source_export_id}: enum helper {edge.local_helper_id!r} "
            f"target {target.interface_id} must be read-only"
        )


def _v4_source_interface(
    graph: RepositoryBlueprintGraph,
    interface_id: str,
) -> tuple[object, object, Mapping[str, JsonValue]]:
    source_id, marker, _local_name = interface_id.rpartition(".interface.")
    if not marker:
        raise InterfaceProjectionError(f"invalid source interface {interface_id!r}")
    source = graph.nodes.get(source_id)
    if source is None or source.node_type != "behavioral_source":
        raise InterfaceProjectionError(f"unresolved source interface {interface_id!r}")
    raw_interfaces = source.declaration.get("interfaces")
    declaration = raw_interfaces.get(interface_id) if isinstance(raw_interfaces, Mapping) else None
    if not isinstance(declaration, Mapping):
        raise InterfaceProjectionError(f"unresolved source interface {interface_id!r}")
    module_id = next(
        (
            candidate
            for candidate, source_ids in graph.module_sources.items()
            if source_id in source_ids
        ),
        None,
    )
    module = graph.nodes.get(module_id) if module_id is not None else None
    if module is None or module.node_type != "module":
        raise InterfaceProjectionError(
            f"{interface_id}: owning module is unavailable"
        )
    return module, source, declaration


def _collect_v4_vocabulary(
    declaration: Mapping[str, JsonValue], vocabulary: set[str]
) -> None:
    contract = declaration.get("contract")
    arguments = contract.get("arguments", {}) if isinstance(contract, Mapping) else {}
    if isinstance(arguments, Mapping):
        for argument in arguments.values():
            if not isinstance(argument, Mapping):
                continue
            type_spec = argument.get("type")
            if isinstance(type_spec, Mapping) and isinstance(type_spec.get("kind"), str):
                vocabulary.add(f"type:{type_spec['kind']}")
            vocabulary.add(
                "arity:required"
                if argument.get("required") is True
                else "arity:optional"
            )
    process_binding = declaration.get("process_binding")
    raw_bindings = (
        process_binding.get("arguments", {})
        if isinstance(process_binding, Mapping)
        else {}
    )
    if isinstance(raw_bindings, Mapping):
        for binding in raw_bindings.values():
            if isinstance(binding, Mapping) and isinstance(binding.get("kind"), str):
                vocabulary.add(f"binding:{binding['kind']}")
    if isinstance(contract, Mapping) and contract.get("helpers"):
        vocabulary.add("helpers")
    if isinstance(contract, Mapping) and contract.get("outcomes"):
        vocabulary.add("outcomes")
    if isinstance(contract, Mapping) and contract.get("execution"):
        vocabulary.add("execution")


def _project_v4_consumer_interfaces(
    graph: RepositoryBlueprintGraph,
    consumer_id: str,
    certification: CertificationView,
) -> InterfaceProjection:
    consumer = graph.nodes.get(consumer_id)
    if consumer is None or consumer.node_type != "behavioral_source":
        raise InterfaceProjectionError(f"unknown behavioral-source consumer {consumer_id!r}")
    raw_uses = consumer.declaration.get("uses_interfaces", [])
    if not isinstance(raw_uses, list):
        raise InterfaceProjectionError(f"{consumer_id}: uses_interfaces must be a list")

    interfaces: dict[str, JsonValue] = {}
    helper_interfaces: dict[str, JsonValue] = {}
    resolvers: dict[str, _DefinitionResolver] = {}
    vocabulary: set[str] = set()
    helper_visiting: set[str] = set()

    def resolved_parts(
        interface_id: str, version: int
    ) -> tuple[object, object, Mapping[str, JsonValue], str]:
        export = graph.exports.get(interface_id)
        if export is not None:
            if export.version != version:
                raise InterfaceProjectionError(
                    f"{consumer_id}: pins {interface_id} version {version}, but target "
                    f"version is {export.version}"
                )
            module, source, resolved = resolve_export(graph, interface_id, version)
            declaration = resolved.declaration
            source_interface_id = resolved.source_interface_id
            assert source_interface_id is not None
        else:
            module, source, declaration = _v4_source_interface(graph, interface_id)
            actual_version = declaration.get("version")
            if actual_version != version:
                raise InterfaceProjectionError(
                    f"{consumer_id}: pins {interface_id} version {version}, but target "
                    f"version is {actual_version}"
                )
            source_interface_id = interface_id
        return module, source, declaration, source_interface_id

    def project(interface_id: str, version: int) -> dict[str, JsonValue]:
        module, source, declaration, source_interface_id = resolved_parts(
            interface_id, version
        )
        decision = certification.check_export(module.node_id, interface_id, version)
        if not decision.certified:
            raise InterfaceProjectionError(
                f"{interface_id}: certification rejected [{decision.code}]: {decision.message}"
            )
        resolver = resolvers.setdefault(
            module.node_id,
            _DefinitionResolver(module.skill_root, module.node_id),
        )
        gateway = source.declaration.get("gateway")
        contract = declaration.get("contract")
        if not isinstance(gateway, Mapping) or not isinstance(contract, Mapping):
            raise InterfaceProjectionError(
                f"{interface_id}: source gateway and contract are required"
            )
        projection: dict[str, JsonValue] = {
            "id": interface_id,
            "version": version,
            "description": str(declaration.get("description", "")),
            "source_module": module.node_id,
            "source_interface": source_interface_id,
            "gateway": resolver.transform(deepcopy(gateway)),
            "contract": resolver.transform(deepcopy(contract)),
        }
        process_binding = declaration.get("process_binding")
        if isinstance(process_binding, Mapping):
            projection["process_binding"] = resolver.transform(
                deepcopy(process_binding)
            )
        _collect_v4_vocabulary(declaration, vocabulary)
        return projection

    def include_helper_closure(interface_id: str) -> None:
        if interface_id in helper_visiting:
            raise InterfaceProjectionError(f"helper cycle includes {interface_id}")
        helper_visiting.add(interface_id)
        for edge in graph.helper_edges:
            if edge.source_export_id != interface_id:
                continue
            _module, _source, declaration, _source_interface = resolved_parts(
                edge.target_interface_id, edge.target_version
            )
            target = InterfaceExport(
                interface_id=edge.target_interface_id,
                version=edge.target_version,
                local_name=edge.target_interface_id.rsplit(".interface.", 1)[-1],
                module_node_id=_module.node_id,
                declaration=declaration,
                source_node_id=_source.node_id,
                source_interface_id=_source_interface,
            )
            _validate_helper_target(edge, target)
            if (
                edge.target_interface_id not in helper_interfaces
                and edge.target_interface_id not in interfaces
            ):
                helper_interfaces[edge.target_interface_id] = project(
                    edge.target_interface_id, edge.target_version
                )
                include_helper_closure(edge.target_interface_id)
        helper_visiting.remove(interface_id)

    for index, entry in enumerate(raw_uses):
        if not isinstance(entry, Mapping):
            raise InterfaceProjectionError(
                f"{consumer_id}.uses_interfaces[{index}] must be a mapping"
            )
        target_id = entry.get("interface")
        version = entry.get("version")
        if not isinstance(target_id, str) or not isinstance(version, int):
            raise InterfaceProjectionError(
                f"{consumer_id}.uses_interfaces[{index}] requires interface and version"
            )
        interfaces[target_id] = project(target_id, version)
        include_helper_closure(target_id)

    definitions = {
        key: value
        for resolver in resolvers.values()
        for key, value in resolver.definitions.items()
    }
    document: dict[str, JsonValue] = {
        "schema_version": 2,
        "consumer": consumer_id,
        "interfaces": dict(sorted(interfaces.items())),
        "helper_interfaces": dict(sorted(helper_interfaces.items())),
        "definitions": dict(sorted(definitions.items())),
    }
    size = len(_canonical_yaml_bytes(document))
    if size > _COMBINED_LIMIT:
        raise InterfaceProjectionError(
            f"{consumer_id}: combined interface projection is {size} bytes; limit is {_COMBINED_LIMIT}"
        )
    return InterfaceProjection(consumer_id, document, frozenset(vocabulary))
    result = binding.get("result")
    output_ref = result.get("output_ref") if isinstance(result, Mapping) else None
    outputs = contract.get("outputs", []) if isinstance(contract, Mapping) else []
    output = next(
        (
            item
            for item in outputs
            if isinstance(item, Mapping) and item.get("id") == output_ref
        ),
        None,
    )
    cardinality = output.get("cardinality") if isinstance(output, Mapping) else None
    if not isinstance(cardinality, Mapping) or not isinstance(cardinality.get("maximum"), int):
        raise InterfaceProjectionError(
            f"{edge.source_export_id}: enum helper {edge.local_helper_id!r} "
            "result must have finite output cardinality"
        )


def project_consumer_interfaces(
    repository_graph: RepositoryBlueprintGraph,
    consumer_id: str,
    certification: CertificationView,
) -> InterfaceProjection:
    """Select only one LLM consumer's direct grants and bounded helpers."""

    candidate = repository_graph.nodes.get(consumer_id)
    if candidate is not None and candidate.node_type == "behavioral_source":
        return _project_v4_consumer_interfaces(
            repository_graph, consumer_id, certification
        )

    consumer = repository_graph.nodes.get(consumer_id)
    if consumer is None or consumer.node_type != "llm-interface":
        raise InterfaceProjectionError(f"unknown LLM consumer {consumer_id!r}")
    raw_uses = consumer.declaration.get("uses_interfaces", [])
    if not isinstance(raw_uses, list):
        raise InterfaceProjectionError(f"{consumer_id}: uses_interfaces must be a list")
    interfaces: dict[str, JsonValue] = {}
    helper_interfaces: dict[str, JsonValue] = {}
    llm_interfaces: dict[str, JsonValue] = {}
    resolvers: dict[str, _DefinitionResolver] = {}
    vocabulary: set[str] = set()
    helper_visiting: set[str] = set()

    def include_helper_closure(export_id: str) -> None:
        if export_id in helper_visiting:
            raise InterfaceProjectionError(f"helper cycle includes {export_id}")
        helper_visiting.add(export_id)
        for edge in repository_graph.helper_edges:
            if edge.source_export_id != export_id:
                continue
            target_module, target = resolve_machine_export(
                repository_graph, edge.target_interface_id, edge.target_version
            )
            _validate_helper_target(edge, target)
            _certify(certification, target_module.node_id, target)
            if target.interface_id not in helper_interfaces and target.interface_id not in interfaces:
                helper_interfaces[target.interface_id] = _project_export(
                    repository_graph, target, certification, resolvers, vocabulary
                )
                include_helper_closure(target.interface_id)
        helper_visiting.remove(export_id)

    for index, entry in enumerate(raw_uses):
        if not isinstance(entry, Mapping):
            raise InterfaceProjectionError(f"{consumer_id}.uses_interfaces[{index}] must be a mapping")
        target_id = entry.get("interface")
        version = entry.get("version")
        if not isinstance(target_id, str) or not isinstance(version, int):
            raise InterfaceProjectionError(f"{consumer_id}.uses_interfaces[{index}] requires interface and version")
        export = repository_graph.machine_exports.get(target_id)
        if export is not None:
            if export.version != version:
                raise InterfaceProjectionError(
                    f"{consumer_id}: pins {target_id} version {version}, but target version is {export.version}"
                )
            interfaces[target_id] = _project_export(
                repository_graph, export, certification, resolvers, vocabulary
            )
            include_helper_closure(target_id)
            continue
        target = repository_graph.nodes.get(target_id)
        if target is None or target.node_type != "llm-interface":
            raise InterfaceProjectionError(f"{consumer_id}: unresolved interface {target_id!r}")
        if target.version != version:
            raise InterfaceProjectionError(
                f"{consumer_id}: pins {target_id} version {version}, but target version is {target.version}"
            )
        projected_llm: dict[str, JsonValue] = {
            "id": target.node_id,
            "version": target.version,
            "description": str(target.declaration.get("description", "")),
        }
        if target.skill_root == consumer.skill_root:
            if target.gateway_path is None:
                raise InterfaceProjectionError(f"{target.node_id}: missing instruction gateway")
            try:
                projected_llm["gateway"] = target.gateway_path.relative_to(
                    target.skill_root
                ).as_posix()
            except ValueError as exc:
                raise InterfaceProjectionError(
                    f"{target.node_id}: instruction gateway escapes owner root"
                ) from exc
        else:
            projected_llm["route"] = {
                "kind": "provider-skill",
                "skill": target.skill_root.name,
            }
            vocabulary.add("provider-skill-route")
        llm_interfaces[target_id] = projected_llm
        vocabulary.add("llm-interface")

    definitions = {
        key: value
        for resolver in resolvers.values()
        for key, value in resolver.definitions.items()
    }
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "consumer": consumer_id,
        "interfaces": dict(sorted(interfaces.items())),
        "helper_interfaces": dict(sorted(helper_interfaces.items())),
        "llm_interfaces": dict(sorted(llm_interfaces.items())),
        "definitions": dict(sorted(definitions.items())),
    }
    size = len(_canonical_yaml_bytes(document))
    if size > _COMBINED_LIMIT:
        raise InterfaceProjectionError(
            f"{consumer_id}: combined interface projection is {size} bytes; limit is {_COMBINED_LIMIT}"
        )
    return InterfaceProjection(consumer_id, document, frozenset(vocabulary))
