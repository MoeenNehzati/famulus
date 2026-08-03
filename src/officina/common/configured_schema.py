"""Load validated configuration and monotonically tighten JSON Schema bundles.

Schemas opt into composition through ``x-officina-config`` annotations governed
by ``configuration.schema.json``. Configuration supplies values, never schema
fragments. Every configuration document is validated by a domain companion
schema before all annotated documents in the target schema bundle are composed.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

import jsonschema
import yaml


ANNOTATION_KEY = "x-officina-config"
DocumentReader = Callable[[Path], str]

_MAPPING_SUBSCHEMAS = frozenset(
    {
        "$defs",
        "definitions",
        "dependentSchemas",
        "patternProperties",
        "properties",
    }
)
_SINGLE_SUBSCHEMAS = frozenset(
    {
        "additionalItems",
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
_ARRAY_SUBSCHEMAS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})


class ConfiguredSchemaError(ValueError):
    """Report invalid input, references, annotations, or composition."""


def schema_requires_configuration(document: object) -> bool:
    """Return whether a schema tree contains a configuration annotation."""

    if isinstance(document, Mapping):
        return ANNOTATION_KEY in document or any(
            schema_requires_configuration(value) for value in document.values()
        )
    if isinstance(document, Sequence) and not isinstance(
        document, (str, bytes, bytearray)
    ):
        return any(schema_requires_configuration(value) for value in document)
    return False


class _UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that accepts only unique string mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "JSON-compatible configuration keys must be strings",
                key_node.start_mark,
            )
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfiguredSchemaError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ConfiguredSchemaError(f"nonstandard JSON numeric constant {value!r}")


def _default_reader(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_mapping(text: str, *, name: str, suffix: str) -> dict[str, Any]:
    try:
        if suffix.lower() == ".json":
            value = json.loads(
                text,
                object_pairs_hook=_json_object,
                parse_constant=_reject_json_constant,
            )
        else:
            value = yaml.load(text, Loader=_UniqueKeyLoader)
    except (json.JSONDecodeError, yaml.YAMLError, ConfiguredSchemaError) as exc:
        raise ConfiguredSchemaError(f"cannot load {name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfiguredSchemaError(f"{name}: document root must be an object")
    _assert_json_compatible(value, name=name)
    return value


def _assert_json_compatible(value: object, *, name: str, path: str = "/") -> None:
    """Reject values that YAML accepts but JSON configuration cannot represent."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfiguredSchemaError(f"{name}: configuration number at {path} must be finite")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _assert_json_compatible(child, name=name, path=f"{path}{key}/")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_json_compatible(child, name=name, path=f"{path}{index}/")


def _load_mapping(path: Path, reader: DocumentReader) -> dict[str, Any]:
    try:
        text = reader(path)
    except FileNotFoundError as exc:
        raise ConfiguredSchemaError(f"missing document: {path}") from exc
    except (OSError, UnicodeError) as exc:
        raise ConfiguredSchemaError(f"cannot read {path}: {exc}") from exc
    return _parse_mapping(text, name=str(path), suffix=path.suffix)


def _validator_class(schema: Mapping[str, Any]) -> type[jsonschema.protocols.Validator]:
    if "$schema" not in schema:
        return jsonschema.Draft7Validator
    return jsonschema.validators.validator_for(schema)


def _check_schema(schema: Mapping[str, Any], *, name: str) -> None:
    validator_class = _validator_class(schema)
    try:
        validator_class.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise ConfiguredSchemaError(f"{name}: invalid schema: {exc.message}") from exc


def _subschemas(
    schema: Mapping[str, Any],
    parts: tuple[object, ...],
) -> Iterator[tuple[dict[str, Any], tuple[object, ...]]]:
    for keyword in _MAPPING_SUBSCHEMAS:
        value = schema.get(keyword)
        if isinstance(value, Mapping):
            for name, child in value.items():
                if isinstance(child, dict):
                    yield child, (*parts, keyword, name)
    for keyword in _SINGLE_SUBSCHEMAS:
        child = schema.get(keyword)
        if isinstance(child, dict):
            yield child, (*parts, keyword)
    for keyword in _ARRAY_SUBSCHEMAS:
        value = schema.get(keyword)
        if isinstance(value, list):
            for index, child in enumerate(value):
                if isinstance(child, dict):
                    yield child, (*parts, keyword, index)
    items = schema.get("items")
    if isinstance(items, dict):
        yield items, (*parts, "items")
    elif isinstance(items, list):
        for index, child in enumerate(items):
            if isinstance(child, dict):
                yield child, (*parts, "items", index)
    dependencies = schema.get("dependencies")
    if isinstance(dependencies, Mapping):
        for name, child in dependencies.items():
            if isinstance(child, dict):
                yield child, (*parts, "dependencies", name)


def _schema_nodes(schema: Mapping[str, Any]) -> Iterator[tuple[dict[str, Any], tuple[object, ...]]]:
    if not isinstance(schema, dict):
        return
    stack: list[tuple[dict[str, Any], tuple[object, ...]]] = [(schema, ())]
    while stack:
        node, parts = stack.pop()
        yield node, parts
        stack.extend(reversed(list(_subschemas(node, parts))))


def _schema_nodes_with_base(
    schema: Mapping[str, Any],
    document_uri: str,
) -> Iterator[tuple[dict[str, Any], tuple[object, ...], str]]:
    """Yield schema locations with the RFC 3986 base established by nested ids."""
    if not isinstance(schema, dict):
        return
    stack: list[tuple[dict[str, Any], tuple[object, ...], str]] = [
        (schema, (), document_uri)
    ]
    while stack:
        node, parts, inherited_base = stack.pop()
        schema_id = node.get("$id")
        base_uri = (
            urljoin(inherited_base, schema_id)
            if isinstance(schema_id, str) and schema_id
            else inherited_base
        )
        yield node, parts, base_uri
        children = list(_subschemas(node, parts))
        stack.extend(
            (child, child_parts, base_uri)
            for child, child_parts in reversed(children)
        )


def _schema_has_annotation(schema: Mapping[str, Any]) -> bool:
    return any(ANNOTATION_KEY in node for node, _ in _schema_nodes(schema))


def _local_reference_path(
    ref: str,
    *,
    current_path: Path,
    allowed_root: Path,
) -> Path | None:
    parsed = urlsplit(ref)
    if not parsed.path:
        return None
    if parsed.scheme not in {"", "file"}:
        return None
    if parsed.scheme == "file":
        candidate = Path(unquote(parsed.path)).resolve()
    else:
        candidate = (current_path.parent / unquote(parsed.path)).resolve()
    try:
        candidate.relative_to(allowed_root)
    except ValueError as exc:
        raise ConfiguredSchemaError(
            f"{current_path}: schema reference {ref!r} resolves outside allowed schema root {allowed_root}"
        ) from exc
    return candidate


def _load_schema_documents(
    root_path: Path,
    *,
    reader: DocumentReader,
    allowed_root: Path,
    referenced_schema_paths: Sequence[str | Path] = (),
) -> dict[Path, dict[str, Any]]:
    root = root_path.resolve()
    allowed = allowed_root.resolve()
    try:
        root.relative_to(allowed)
    except ValueError as exc:
        raise ConfiguredSchemaError(
            f"root schema {root} is outside allowed schema root {allowed}"
        ) from exc
    seeds = [root, *(Path(item).resolve() for item in referenced_schema_paths)]
    for seed in seeds:
        try:
            seed.relative_to(allowed)
        except ValueError as exc:
            raise ConfiguredSchemaError(
                f"schema document {seed} is outside allowed schema root {allowed}"
            ) from exc
    documents: dict[Path, dict[str, Any]] = {}
    pending: deque[Path] = deque(seeds)
    while pending:
        path = pending.popleft()
        if path in documents:
            continue
        schema = _load_mapping(path, reader)
        _check_schema(schema, name=str(path))
        documents[path] = schema
        for node, _, base_uri in _schema_nodes_with_base(schema, path.as_uri()):
            ref = node.get("$ref")
            if not isinstance(ref, str):
                continue
            resolved_ref = urljoin(base_uri, ref)
            target = _local_reference_path(
                resolved_ref,
                current_path=path,
                allowed_root=allowed,
            )
            if target is not None and target not in documents:
                pending.append(target)
    return documents


def _schema_store(
    documents: Mapping[Path, Mapping[str, Any]],
) -> Mapping[str, Mapping[str, Any]]:
    store: dict[str, Mapping[str, Any]] = {}

    def register(alias: str, node: Mapping[str, Any]) -> None:
        existing = store.get(alias)
        if existing is not None and existing is not node:
            raise ConfiguredSchemaError(
                f"duplicate schema identifier or canonical URI {alias!r}"
            )
        store[alias] = node

    for path, document in documents.items():
        register(path.as_uri(), document)
        for node, _, base_uri in _schema_nodes_with_base(document, path.as_uri()):
            schema_id = node.get("$id")
            if isinstance(schema_id, str) and schema_id:
                if urlsplit(schema_id).scheme:
                    register(schema_id, node)
                register(base_uri, node)
    return MappingProxyType(store)


def _document_base_uri(path: Path, document: Mapping[str, Any]) -> str:
    schema_id = document.get("$id")
    if isinstance(schema_id, str) and schema_id:
        return urljoin(path.as_uri(), schema_id)
    return path.as_uri()


def _assert_references_resolve_locally(
    documents: Mapping[Path, Mapping[str, Any]],
    store: Mapping[str, Mapping[str, Any]],
) -> None:
    """Reject references that could escape the loaded bundle through retrieval."""
    aliases = set(store)
    for path, document in documents.items():
        for node, parts, base_uri in _schema_nodes_with_base(document, path.as_uri()):
            ref = node.get("$ref")
            if not isinstance(ref, str):
                continue
            resolved = urljoin(base_uri, ref)
            document_uri, _ = urldefrag(resolved)
            if document_uri not in aliases:
                raise ConfiguredSchemaError(
                    f"{path} at {_schema_location(parts)}: reference {ref!r} "
                    "is not present in the local schema bundle; supply its path "
                    "through referenced_schema_paths"
                )


def _assert_reference_fragments_exist(
    documents: Mapping[Path, Mapping[str, Any]],
    store: Mapping[str, Mapping[str, Any]],
) -> None:
    """Resolve every reference eagerly so malformed fragments fail at loading."""
    for path, document in documents.items():
        resolver = jsonschema.RefResolver(
            base_uri=_document_base_uri(path, document),
            referrer=document,
            store=dict(store),
        )
        for node, parts, _ in _schema_nodes_with_base(document, path.as_uri()):
            ref = node.get("$ref")
            if not isinstance(ref, str):
                continue
            try:
                resolver.resolve(ref)
            except Exception as exc:
                raise ConfiguredSchemaError(
                    f"{path} at {_schema_location(parts)}: cannot resolve "
                    f"reference {ref!r}: {exc}"
                ) from exc


def _canonicalize_references(
    document: Mapping[str, Any],
    document_uri: str,
) -> None:
    """Make nested-id reference scope explicit for legacy resolver compatibility."""
    for node, _, base_uri in _schema_nodes_with_base(document, document_uri):
        ref = node.get("$ref")
        if isinstance(ref, str):
            node["$ref"] = urljoin(base_uri, ref)


@dataclass(frozen=True)
class ConfiguredSchemaBundle:
    """Resolved schema documents plus the registry needed to validate them."""

    root_path: Path
    _documents: Mapping[Path, Mapping[str, Any]]
    _store: Mapping[str, Mapping[str, Any]]
    format_checker: jsonschema.FormatChecker | None = None

    @property
    def documents(self) -> Mapping[Path, Mapping[str, Any]]:
        """Return a defensive snapshot of all composed documents."""
        return MappingProxyType(deepcopy(dict(self._documents)))

    @property
    def store(self) -> Mapping[str, Mapping[str, Any]]:
        """Return a defensive snapshot of the resolver aliases."""
        return MappingProxyType(deepcopy(dict(self._store)))

    @property
    def document_count(self) -> int:
        """Return the number of physical schema documents in the bundle."""
        return len(self._documents)

    @property
    def root_schema(self) -> Mapping[str, Any]:
        """Return a defensive copy of the composed root schema document."""
        return deepcopy(self._documents[self.root_path])

    def validator(
        self,
        schema_path: str | Path | None = None,
        *,
        format_checker: jsonschema.FormatChecker | None = None,
    ) -> jsonschema.protocols.Validator:
        """Construct the dialect-appropriate validator for one bundled document."""
        path = self.root_path if schema_path is None else Path(schema_path).resolve()
        try:
            schema = deepcopy(self._documents[path])
        except KeyError as exc:
            raise ConfiguredSchemaError(f"schema is not present in bundle: {path}") from exc
        validator_class = _validator_class(schema)
        resolver = jsonschema.RefResolver(
            base_uri=_document_base_uri(path, schema),
            referrer=schema,
            store=deepcopy(dict(self._store)),
        )
        return validator_class(
            schema,
            resolver=resolver,
            format_checker=(format_checker or self.format_checker),
        )


def _instance_path(parts: Sequence[object]) -> str:
    if not parts:
        return "/"
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    )


def _validate_instance(
    document: Mapping[str, Any],
    bundle: ConfiguredSchemaBundle,
    *,
    document_name: str,
    format_checker: jsonschema.FormatChecker | None,
) -> None:
    try:
        errors = list(
            bundle.validator(format_checker=format_checker).iter_errors(document)
        )
    except Exception as exc:
        raise ConfiguredSchemaError(
            f"cannot validate {document_name} against companion schema: {exc}"
        ) from exc
    if errors:
        error = jsonschema.exceptions.best_match(errors)
        raise ConfiguredSchemaError(
            f"{document_name}: invalid configuration at "
            f"{_instance_path(error.absolute_path)}: {error.message}"
        )


def load_configuration(
    config_path: str | Path,
    *,
    config_schema_path: str | Path | None = None,
    format_checker: jsonschema.FormatChecker | None = None,
    reader: DocumentReader | None = None,
    allowed_schema_root: str | Path | None = None,
    referenced_schema_paths: Sequence[str | Path] = (),
) -> dict[str, Any]:
    """Load YAML/JSON config through the central or an external schema."""
    path = Path(config_path)
    read = reader or _default_reader
    document = _load_mapping(path, read)
    validate_configuration(
        document,
        config_schema_path=config_schema_path,
        format_checker=format_checker,
        reader=read,
        allowed_schema_root=allowed_schema_root,
        referenced_schema_paths=referenced_schema_paths,
        document_name=str(path),
    )
    return document


def validate_configuration(
    document: Mapping[str, Any],
    *,
    config_schema_path: str | Path | None = None,
    format_checker: jsonschema.FormatChecker | None = None,
    reader: DocumentReader | None = None,
    allowed_schema_root: str | Path | None = None,
    referenced_schema_paths: Sequence[str | Path] = (),
    document_name: str = "<configuration>",
) -> None:
    """Validate an in-memory configuration without mutating it."""
    if not isinstance(document, Mapping):
        raise ConfiguredSchemaError(f"{document_name}: configuration must be a mapping")
    _assert_json_compatible(document, name=document_name)
    read = reader or _default_reader
    companion_path = (
        Path(config_schema_path).resolve()
        if config_schema_path is not None
        else _configuration_schema_path()
    )
    allowed_root = (
        Path(allowed_schema_root).resolve()
        if allowed_schema_root is not None
        else companion_path.parent
    )
    raw_documents = _load_schema_documents(
        companion_path,
        reader=read,
        allowed_root=allowed_root,
        referenced_schema_paths=referenced_schema_paths,
    )
    raw_store = _schema_store(raw_documents)
    _assert_references_resolve_locally(raw_documents, raw_store)
    if any(_schema_has_annotation(schema) for schema in raw_documents.values()):
        raise ConfiguredSchemaError(
            f"{companion_path}: companion schemas cannot themselves require configuration"
        )
    resolved_documents = {
        path: deepcopy(schema) for path, schema in raw_documents.items()
    }
    for path, schema_document in resolved_documents.items():
        _canonicalize_references(schema_document, path.as_uri())
    resolved_store = _schema_store(resolved_documents)
    _assert_references_resolve_locally(resolved_documents, resolved_store)
    _assert_reference_fragments_exist(resolved_documents, resolved_store)
    frozen = MappingProxyType(resolved_documents)
    bundle = ConfiguredSchemaBundle(
        root_path=companion_path,
        _documents=frozen,
        _store=resolved_store,
        format_checker=format_checker,
    )
    _validate_instance(
        document,
        bundle,
        document_name=document_name,
        format_checker=format_checker,
    )


@lru_cache(maxsize=1)
def _configuration_schema_path() -> Path:
    return Path(resources.files("officina.common").joinpath("configuration.schema.json"))


@lru_cache(maxsize=1)
def _annotation_schema() -> dict[str, Any]:
    text = _configuration_schema_path().read_text(encoding="utf-8")
    schema = _parse_mapping(
        text,
        name="officina.common/configuration.schema.json",
        suffix=".json",
    )
    _check_schema(schema, name="configuration annotation protocol")
    definitions = schema.get("definitions", {})
    annotation = definitions.get("configAnnotation")
    if not isinstance(annotation, dict):
        raise ConfiguredSchemaError(
            "configuration schema is missing definitions.configAnnotation"
        )
    resolved = deepcopy(annotation)
    resolved["definitions"] = {
        "nonRootJsonPointer": deepcopy(definitions["nonRootJsonPointer"])
    }
    return resolved


def _schema_location(parts: Sequence[object]) -> str:
    return "#" + _instance_path(parts)


def _composition_error(
    detail: str,
    *,
    schema_name: str,
    schema_parts: Sequence[object],
    config_name: str,
    pointer: str,
) -> ConfiguredSchemaError:
    return ConfiguredSchemaError(
        f"{schema_name} at {_schema_location(schema_parts)} using "
        f"{config_name}{pointer}: {detail}"
    )


def _resolve_pointer(
    document: object,
    pointer: str,
    *,
    schema_name: str,
    schema_parts: Sequence[object],
    config_name: str,
) -> object:
    current = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                raise _composition_error(
                    "configuration pointer does not exist",
                    schema_name=schema_name,
                    schema_parts=schema_parts,
                    config_name=config_name,
                    pointer=pointer,
                )
            current = current[token]
        elif isinstance(current, list):
            canonical_index = token == "0" or (
                bool(token) and token[0] != "0" and token.isdigit()
            )
            if not canonical_index or int(token) >= len(current):
                raise _composition_error(
                    "configuration pointer does not identify a canonical array index",
                    schema_name=schema_name,
                    schema_parts=schema_parts,
                    config_name=config_name,
                    pointer=pointer,
                )
            current = current[int(token)]
        else:
            raise _composition_error(
                "configuration pointer traverses a scalar value",
                schema_name=schema_name,
                schema_parts=schema_parts,
                config_name=config_name,
                pointer=pointer,
            )
    return current


def _nonempty_unique_strings(
    value: object,
    *,
    schema_name: str,
    schema_parts: Sequence[object],
    config_name: str,
    pointer: str,
) -> list[str]:
    if not isinstance(value, list):
        raise _composition_error(
            "configuration source must be an array",
            schema_name=schema_name,
            schema_parts=schema_parts,
            config_name=config_name,
            pointer=pointer,
        )
    if not value or any(not isinstance(item, str) or not item for item in value):
        raise _composition_error(
            "configuration source must be a nonempty array of nonempty strings",
            schema_name=schema_name,
            schema_parts=schema_parts,
            config_name=config_name,
            pointer=pointer,
        )
    if len(set(value)) != len(value):
        raise _composition_error(
            "configuration source must contain unique strings",
            schema_name=schema_name,
            schema_parts=schema_parts,
            config_name=config_name,
            pointer=pointer,
        )
    return value


def _enum_values(
    operation: str,
    source: object,
    *,
    schema_name: str,
    schema_parts: Sequence[object],
    config_name: str,
    pointer: str,
) -> list[str]:
    if operation == "keys-to-enum":
        if not isinstance(source, Mapping) or not source:
            raise _composition_error(
                "configuration source must be a nonempty object",
                schema_name=schema_name,
                schema_parts=schema_parts,
                config_name=config_name,
                pointer=pointer,
            )
        values = list(source)
        if any(not isinstance(value, str) or not value for value in values):
            raise _composition_error(
                "configuration source keys must be nonempty strings",
                schema_name=schema_name,
                schema_parts=schema_parts,
                config_name=config_name,
                pointer=pointer,
            )
        return values
    return _nonempty_unique_strings(
        source,
        schema_name=schema_name,
        schema_parts=schema_parts,
        config_name=config_name,
        pointer=pointer,
    )


def _validate_annotation(
    annotation: object,
    *,
    schema_name: str,
    schema_parts: Sequence[object],
) -> dict[str, str]:
    errors = list(
        jsonschema.Draft7Validator(_annotation_schema()).iter_errors(annotation)
    )
    if errors:
        error = jsonschema.exceptions.best_match(errors)
        raise ConfiguredSchemaError(
            f"{schema_name} at {_schema_location(schema_parts)}: invalid "
            f"{ANNOTATION_KEY} annotation: {error.message}"
        )
    assert isinstance(annotation, dict)
    return annotation


def _required_names_allowed(
    schema_node: Mapping[str, Any],
    names: Sequence[str],
    *,
    document_schema: Mapping[str, Any],
    base_uri: str,
    store: Mapping[str, Mapping[str, Any]],
    format_checker: jsonschema.FormatChecker | None,
) -> bool:
    if schema_node.get("additionalProperties") is False:
        properties = schema_node.get("properties")
        patterns = schema_node.get("patternProperties")
        explicit = properties if isinstance(properties, Mapping) else {}
        regexes = list(patterns) if isinstance(patterns, Mapping) else []
        for name in names:
            if name not in explicit and not any(re.search(pattern, name) for pattern in regexes):
                return False
    property_names = schema_node.get("propertyNames")
    if property_names is False and names:
        return False
    if isinstance(property_names, Mapping):
        validator_class = _validator_class(document_schema)
        resolver = jsonschema.RefResolver(
            base_uri=base_uri,
            referrer=property_names,
            store=dict(store),
        )
        validator = validator_class(
            property_names,
            resolver=resolver,
            format_checker=format_checker,
        )
        if any(not validator.is_valid(name) for name in names):
            return False
    return True


def _apply_annotation(
    schema_node: dict[str, Any],
    annotation_value: object,
    config: Mapping[str, Any],
    *,
    schema_name: str,
    schema_parts: Sequence[object],
    config_name: str,
) -> list[str] | None:
    annotation = _validate_annotation(
        annotation_value,
        schema_name=schema_name,
        schema_parts=schema_parts,
    )
    operation = annotation["operation"]
    pointer = annotation["source"]
    source = _resolve_pointer(
        config,
        pointer,
        schema_name=schema_name,
        schema_parts=schema_parts,
        config_name=config_name,
    )
    if operation in {"keys-to-enum", "values-to-enum"}:
        configured = _enum_values(
            operation,
            source,
            schema_name=schema_name,
            schema_parts=schema_parts,
            config_name=config_name,
            pointer=pointer,
        )
        existing = schema_node.get("enum")
        resolved = configured if existing is None else [
            value for value in existing if value in configured
        ]
        if not resolved:
            raise _composition_error(
                "configured enum has no values compatible with the base schema",
                schema_name=schema_name,
                schema_parts=schema_parts,
                config_name=config_name,
                pointer=pointer,
            )
        schema_node["enum"] = resolved
        return None

    required = _nonempty_unique_strings(
        source,
        schema_name=schema_name,
        schema_parts=schema_parts,
        config_name=config_name,
        pointer=pointer,
    )
    if schema_node.get("type") != "object":
        raise _composition_error(
            "extend-required annotation must target a schema with type object",
            schema_name=schema_name,
            schema_parts=schema_parts,
            config_name=config_name,
            pointer=pointer,
        )
    existing = list(schema_node.get("required", []))
    added = [name for name in required if name not in existing]
    schema_node["required"] = existing + added
    return added


def _compose_schema(
    base_schema: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    schema_name: str,
    config_name: str,
    document_uri: str,
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], list[str], str, tuple[object, ...]]]]:
    resolved = deepcopy(dict(base_schema))
    required_checks: list[
        tuple[dict[str, Any], list[str], str, tuple[object, ...]]
    ] = []
    nodes = list(_schema_nodes_with_base(resolved, document_uri))
    for node, parts, base_uri in reversed(nodes):
        if ANNOTATION_KEY not in node:
            continue
        annotation = node.pop(ANNOTATION_KEY)
        added = _apply_annotation(
            node,
            annotation,
            config,
            schema_name=schema_name,
            schema_parts=parts,
            config_name=config_name,
        )
        if added:
            required_checks.append((node, added, base_uri, parts))
    _check_schema(resolved, name=schema_name)
    return resolved, required_checks


def load_configured_schema_bundle(
    schema_path: str | Path,
    *,
    config_path: str | Path | None = None,
    config_schema_path: str | Path | None = None,
    format_checker: jsonschema.FormatChecker | None = None,
    reader: DocumentReader | None = None,
    allowed_schema_root: str | Path | None = None,
    referenced_schema_paths: Sequence[str | Path] = (),
    config_schema_allowed_root: str | Path | None = None,
    config_schema_referenced_paths: Sequence[str | Path] = (),
) -> ConfiguredSchemaBundle:
    """Load, locally resolve, compose, and register one schema bundle.

    ``referenced_schema_paths`` catalogs documents addressed by absolute ``$id``
    values. All catalog entries and relative references remain confined to
    ``allowed_schema_root``; unresolved references never fall back to a network.
    """
    read = reader or _default_reader
    root_path = Path(schema_path).resolve()
    allowed_root = (
        Path(allowed_schema_root).resolve()
        if allowed_schema_root is not None
        else root_path.parent
    )
    raw_documents = _load_schema_documents(
        root_path,
        reader=read,
        allowed_root=allowed_root,
        referenced_schema_paths=referenced_schema_paths,
    )
    raw_store = _schema_store(raw_documents)
    _assert_references_resolve_locally(raw_documents, raw_store)
    annotated = any(_schema_has_annotation(schema) for schema in raw_documents.values())
    if config_path is None:
        if annotated:
            raise ConfiguredSchemaError(
                f"{root_path}: annotated schema bundle requires configuration"
            )
        resolved_documents = {
            path: deepcopy(schema) for path, schema in raw_documents.items()
        }
    else:
        config = load_configuration(
            config_path,
            config_schema_path=config_schema_path,
            format_checker=format_checker,
            reader=read,
            allowed_schema_root=config_schema_allowed_root,
            referenced_schema_paths=config_schema_referenced_paths,
        )
        required_checks: list[
            tuple[Path, dict[str, Any], list[str], str, tuple[object, ...]]
        ] = []
        resolved_documents = {}
        for path, schema in raw_documents.items():
            resolved, checks = _compose_schema(
                schema,
                config,
                schema_name=str(path),
                config_name=str(config_path),
                document_uri=path.as_uri(),
            )
            resolved_documents[path] = resolved
            required_checks.extend(
                (path, node, names, base_uri, parts)
                for node, names, base_uri, parts in checks
            )
    if config_path is None:
        required_checks = []
    for path, document in resolved_documents.items():
        _canonicalize_references(document, path.as_uri())
    resolved_store = _schema_store(resolved_documents)
    _assert_references_resolve_locally(resolved_documents, resolved_store)
    _assert_reference_fragments_exist(resolved_documents, resolved_store)
    for path, node, names, base_uri, parts in required_checks:
        if not _required_names_allowed(
            node,
            names,
            document_schema=resolved_documents[path],
            base_uri=base_uri,
            store=resolved_store,
            format_checker=format_checker,
        ):
            raise _composition_error(
                "configured required fields are forbidden by the composed object schema",
                schema_name=str(path),
                schema_parts=parts,
                config_name=str(config_path),
                pointer="",
            )
    frozen = MappingProxyType(resolved_documents)
    return ConfiguredSchemaBundle(
        root_path=root_path,
        _documents=frozen,
        _store=resolved_store,
        format_checker=format_checker,
    )


def load_configured_schema(
    schema_path: str | Path,
    *,
    config_path: str | Path | None = None,
    config_schema_path: str | Path | None = None,
    format_checker: jsonschema.FormatChecker | None = None,
    reader: DocumentReader | None = None,
    allowed_schema_root: str | Path | None = None,
    referenced_schema_paths: Sequence[str | Path] = (),
    config_schema_allowed_root: str | Path | None = None,
    config_schema_referenced_paths: Sequence[str | Path] = (),
) -> dict[str, Any]:
    """Return a standalone root schema, rejecting reference-bearing bundles."""
    bundle = load_configured_schema_bundle(
        schema_path,
        config_path=config_path,
        config_schema_path=config_schema_path,
        format_checker=format_checker,
        reader=reader,
        allowed_schema_root=allowed_schema_root,
        referenced_schema_paths=referenced_schema_paths,
        config_schema_allowed_root=config_schema_allowed_root,
        config_schema_referenced_paths=config_schema_referenced_paths,
    )
    if bundle.document_count != 1:
        raise ConfiguredSchemaError(
            "load_configured_schema cannot discard a multi-document bundle; "
            "use load_configured_schema_bundle or configured_validator"
        )
    return dict(bundle.root_schema)


def configured_validator(
    schema_path: str | Path,
    *,
    config_path: str | Path | None = None,
    config_schema_path: str | Path | None = None,
    format_checker: jsonschema.FormatChecker | None = None,
    reader: DocumentReader | None = None,
    allowed_schema_root: str | Path | None = None,
    referenced_schema_paths: Sequence[str | Path] = (),
    config_schema_allowed_root: str | Path | None = None,
    config_schema_referenced_paths: Sequence[str | Path] = (),
) -> jsonschema.protocols.Validator:
    """Construct a dialect-aware validator backed by the composed bundle registry."""
    return load_configured_schema_bundle(
        schema_path,
        config_path=config_path,
        config_schema_path=config_schema_path,
        format_checker=format_checker,
        reader=reader,
        allowed_schema_root=allowed_schema_root,
        referenced_schema_paths=referenced_schema_paths,
        config_schema_allowed_root=config_schema_allowed_root,
        config_schema_referenced_paths=config_schema_referenced_paths,
    ).validator()


def validator_for_schema(
    schema: Mapping[str, Any],
    *,
    format_checker: jsonschema.FormatChecker | None = None,
) -> jsonschema.protocols.Validator:
    """Build a validator for a self-contained in-memory schema mapping."""
    resolved = deepcopy(dict(schema))
    _check_schema(resolved, name="<in-memory schema>")
    if _schema_has_annotation(resolved):
        raise ConfiguredSchemaError(
            "in-memory configured schemas are unsupported; load a schema bundle with configuration"
        )
    for node, parts in _schema_nodes(resolved):
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            raise ConfiguredSchemaError(
                f"<in-memory schema> at {_schema_location(parts)}: external reference "
                f"{ref!r} requires a configured schema bundle"
            )
    validator_class = _validator_class(resolved)
    return validator_class(resolved, format_checker=format_checker)


__all__ = [
    "ConfiguredSchemaBundle",
    "ConfiguredSchemaError",
    "configured_validator",
    "load_configuration",
    "load_configured_schema",
    "load_configured_schema_bundle",
    "validate_configuration",
    "validator_for_schema",
]
