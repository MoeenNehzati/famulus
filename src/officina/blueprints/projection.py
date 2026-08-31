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

from .authorization import (
    AuthorizationRequest,
    AuthorizationResult,
    resolve_interface_authorization,
)
from .graph import (
    BlueprintGraphError,
    BlueprintNode,
    HelperEdge,
    InterfaceExport,
    RepositoryBlueprintGraph,
    resolve_export,
)
from .inventory import JsonValue
from ..certification.view import CertificationView


class InterfaceProjectionError(ValueError):
    """Raised when selected contracts cannot form a safe bounded projection."""


@dataclass(frozen=True)
class InterfaceProjection:
    consumer_id: str
    document: Mapping[str, JsonValue]
    vocabulary: frozenset[str]


_STANDALONE_EXPORT_LIMIT = 12_288
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


def _enforce_standalone_export_size(
    interface_id: str,
    export_projection: Mapping[str, JsonValue],
) -> None:
    size = standalone_export_size(export_projection)
    if size > _STANDALONE_EXPORT_LIMIT:
        raise InterfaceProjectionError(
            f"{interface_id}: standalone interface projection is {size} bytes; "
            f"limit is {_STANDALONE_EXPORT_LIMIT}"
        )


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
    def __init__(
        self,
        provider_root: Path,
        provider_skill: str,
        *,
        source_field: str = "source_skill",
    ) -> None:
        self.provider_root = provider_root
        self.provider_skill = provider_skill
        self.source_field = source_field
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
            self.source_field: self.provider_skill,
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


def _validate_helper_target(edge: HelperEdge, target: InterfaceExport) -> None:
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
    maximum = cardinality.get("maximum") if isinstance(cardinality, Mapping) else None
    if not isinstance(maximum, int) or isinstance(maximum, bool):
        raise InterfaceProjectionError(
            f"{edge.source_export_id}: enum helper {edge.local_helper_id!r} "
            "result must have finite output cardinality"
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


def _collect_interface_vocabulary(
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
        decision = certification.check_export(
            module.node_id,
            interface_id,
            version,
            source.node_id,
        )
        if not decision.certified:
            raise InterfaceProjectionError(
                f"{interface_id}: certification rejected [{decision.code}]: {decision.message}"
            )
        resolver = resolvers.setdefault(
            module.node_id,
            _DefinitionResolver(
                module.module_root,
                module.node_id,
                source_field="source_module",
            ),
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
        _collect_interface_vocabulary(declaration, vocabulary)
        _enforce_standalone_export_size(interface_id, projection)
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


def _project_consumer_interfaces(
    graph: RepositoryBlueprintGraph,
    consumer_id: str,
    certification: CertificationView,
) -> InterfaceProjection:
    consumer = graph.nodes.get(consumer_id)
    consumer_module_id = graph.source_modules.get(consumer_id)
    if (
        consumer is None
        or consumer.node_type != "behavioral_source"
        or consumer_module_id is None
    ):
        raise InterfaceProjectionError(
            f"unknown behavioral-source consumer {consumer_id!r}"
        )
    raw_uses = consumer.declaration.get("uses_interfaces", [])
    if not isinstance(raw_uses, list):
        raise InterfaceProjectionError(
            f"{consumer_id}: uses_interfaces must be a list"
        )

    interfaces: dict[str, JsonValue] = {}
    helper_interfaces: dict[str, JsonValue] = {}
    resolvers: dict[str, _DefinitionResolver] = {}
    vocabulary: set[str] = set()
    helper_visiting: set[str] = set()

    def resolve_target(
        interface_id: str,
        version: int,
        *,
        caller_module_id: str,
        caller_source_id: str,
    ) -> tuple[
        BlueprintNode,
        BlueprintNode,
        Mapping[str, JsonValue],
        str,
        AuthorizationResult | None,
    ]:
        private = graph.source_interfaces.get(interface_id)
        if private is not None:
            if (
                private.module_node_id != caller_module_id
                or private.version != version
                or private.source_node_id is None
                or private.source_interface_id is None
            ):
                raise InterfaceProjectionError(
                    f"{caller_source_id}: unavailable private interface "
                    f"{interface_id}@{version}"
                )
            source = graph.nodes[private.source_node_id]
            return (
                graph.nodes[private.module_node_id],
                source,
                private.declaration,
                private.source_interface_id,
                None,
            )

        authorization = resolve_interface_authorization(
            graph,
            AuthorizationRequest(
                caller_module_id=caller_module_id,
                caller_source_id=caller_source_id,
                interface_id=interface_id,
                version=version,
            ),
        )
        if not authorization.allowed:
            raise InterfaceProjectionError(
                f"{interface_id}: authorization rejected "
                f"[{authorization.diagnostic}]"
            )
        terminal_module_id = authorization.terminal_module_id
        source_id = authorization.implementing_source_id
        terminal_interface_id = authorization.terminal_interface_id
        if (
            terminal_module_id is None
            or source_id is None
            or terminal_interface_id is None
        ):
            raise InterfaceProjectionError(
                f"{interface_id}: authorization omitted terminal binding"
            )
        terminal_export = graph.exports.get(terminal_interface_id)
        source = graph.nodes.get(source_id)
        module = graph.nodes.get(terminal_module_id)
        if (
            terminal_export is None
            or terminal_export.source_interface_id is None
            or source is None
            or source.node_type != "behavioral_source"
            or module is None
            or module.node_type != "module"
        ):
            raise InterfaceProjectionError(
                f"{interface_id}: authorization terminal binding is unavailable"
            )
        return (
            module,
            source,
            terminal_export.declaration,
            terminal_export.source_interface_id,
            authorization,
        )

    def project(
        interface_id: str,
        version: int,
        *,
        caller_module_id: str,
        caller_source_id: str,
    ) -> dict[str, JsonValue]:
        module, source, declaration, source_interface_id, authorization = (
            resolve_target(
                interface_id,
                version,
                caller_module_id=caller_module_id,
                caller_source_id=caller_source_id,
            )
        )
        certification_owner = (
            authorization.requested_owner_module_id
            if authorization is not None
            else module.node_id
        )
        assert certification_owner is not None
        decision = certification.check_export(
            certification_owner,
            interface_id,
            version,
            source.node_id,
        )
        if not decision.certified:
            raise InterfaceProjectionError(
                f"{interface_id}: certification rejected "
                f"[{decision.code}]: {decision.message}"
            )
        resolver = resolvers.setdefault(
            module.node_id,
            _DefinitionResolver(
                module.module_root,
                module.node_id,
                source_field="source_module",
            ),
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
        _collect_interface_vocabulary(declaration, vocabulary)
        _enforce_standalone_export_size(interface_id, projection)
        return projection

    def include_helper_closure(interface_id: str) -> None:
        if interface_id in helper_visiting:
            raise InterfaceProjectionError(
                f"helper cycle includes {interface_id}"
            )
        source_interface = graph.exports.get(interface_id)
        if source_interface is None:
            source_interface = graph.source_interfaces.get(interface_id)
        if source_interface is None or source_interface.source_node_id is None:
            raise InterfaceProjectionError(
                f"{interface_id}: helper source binding is unavailable"
            )
        helper_caller_source_id = source_interface.source_node_id
        helper_caller_module_id = graph.source_modules.get(
            helper_caller_source_id
        )
        if helper_caller_module_id is None:
            raise InterfaceProjectionError(
                f"{interface_id}: helper caller module is unavailable"
            )
        helper_visiting.add(interface_id)
        for edge in graph.helper_edges:
            if edge.source_export_id != interface_id:
                continue
            module, source, declaration, source_interface_id, _authorization = (
                resolve_target(
                    edge.target_interface_id,
                    edge.target_version,
                    caller_module_id=helper_caller_module_id,
                    caller_source_id=helper_caller_source_id,
                )
            )
            target = InterfaceExport(
                interface_id=edge.target_interface_id,
                version=edge.target_version,
                local_name=edge.target_interface_id.rsplit(
                    ".interface.", 1
                )[-1],
                module_node_id=module.node_id,
                declaration=declaration,
                source_node_id=source.node_id,
                source_interface_id=source_interface_id,
            )
            _validate_helper_target(edge, target)
            if (
                edge.target_interface_id not in helper_interfaces
                and edge.target_interface_id not in interfaces
            ):
                helper_interfaces[edge.target_interface_id] = project(
                    edge.target_interface_id,
                    edge.target_version,
                    caller_module_id=helper_caller_module_id,
                    caller_source_id=helper_caller_source_id,
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
                f"{consumer_id}.uses_interfaces[{index}] requires "
                "interface and version"
            )
        interfaces[target_id] = project(
            target_id,
            version,
            caller_module_id=consumer_module_id,
            caller_source_id=consumer_id,
        )
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
            f"{consumer_id}: combined interface projection is {size} bytes; "
            f"limit is {_COMBINED_LIMIT}"
        )
    return InterfaceProjection(
        consumer_id,
        document,
        frozenset(vocabulary),
    )


def project_consumer_interfaces(
    repository_graph: RepositoryBlueprintGraph,
    consumer_id: str,
    certification: CertificationView,
) -> InterfaceProjection:
    """Select one behavioral source's direct interface uses and bounded helpers."""

    if repository_graph.schema_version != 6:
        raise InterfaceProjectionError(
            f"unsupported graph version {repository_graph.schema_version}"
        )
    return _project_consumer_interfaces(
        repository_graph,
        consumer_id,
        certification,
    )
