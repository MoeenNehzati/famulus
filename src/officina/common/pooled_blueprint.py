"""Generated pooled blueprint reviews and their downstream-only health."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping
from urllib.parse import unquote, urljoin, urlsplit

import jsonschema
import yaml

from .artifact_health import (
    GraphHealthReport,
    health_node_ids,
    health_owner_node_id,
)
from .audit_records import attach_record_authentication, record_authentication_matches
from .blueprint_graph import SkillBlueprintGraph


_DEFAULT_CERTIFIER = {"interface": "skill-audit.machine.certify", "version": 1}
_SCHEMA_BASE_URI = "https://famulus-schema.invalid/"
_POOL_SCHEMA_BUNDLE = frozenset({"pooled-review.schema.json", "health.schema.json"})
_HEALTH_SCHEMA_BUNDLE = frozenset({"health.schema.json"})


class PooledReviewValidationError(ValueError):
    pass


def _require_descriptor_access() -> None:
    if (
        os.name != "posix"
        or not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or os.open not in getattr(os, "supports_dir_fd", set())
    ):
        raise PooledReviewValidationError(
            "descriptor-safe no-follow file access is unavailable"
        )


def _directory_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _open_directory_without_symlinks(
    path: Path,
) -> tuple[int, tuple[tuple[int, int], ...]]:
    _require_descriptor_access()
    absolute = Path(os.path.abspath(path))
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(absolute.anchor, flags)
    identities = [_directory_identity(os.fstat(descriptor))]
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            identities.append(_directory_identity(os.fstat(descriptor)))
        return descriptor, tuple(identities)
    except BaseException:
        os.close(descriptor)
        raise


def _open_confined_regular_file(
    path: Path,
    root: Path,
) -> tuple[int, tuple[tuple[int, int], ...]]:
    absolute_path = Path(os.path.abspath(path))
    absolute_root = Path(os.path.abspath(root))
    try:
        relative = absolute_path.relative_to(absolute_root)
    except ValueError as exc:
        raise PooledReviewValidationError(f"file is outside allowed root: {path}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise PooledReviewValidationError(f"invalid contained file path: {path}")

    directory_fd, directory_identities = _open_directory_without_symlinks(
        absolute_root
    )
    identity_list = list(directory_identities)
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY
        | os.O_NONBLOCK
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        for component in relative.parts[:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_descriptor
            identity_list.append(_directory_identity(os.fstat(directory_fd)))
        descriptor = os.open(relative.parts[-1], file_flags, dir_fd=directory_fd)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise PooledReviewValidationError(f"file is not a regular file: {path}")
        return descriptor, tuple(identity_list)
    except PooledReviewValidationError:
        raise
    except FileNotFoundError:
        raise
    except OSError as exc:
        detail = "contains a symlink component" if exc.errno == errno.ELOOP else str(exc)
        raise PooledReviewValidationError(f"cannot securely open {path}: {detail}") from exc
    finally:
        os.close(directory_fd)


def _read_descriptor(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 1024 * 1024):
        chunks.append(chunk)
    return b"".join(chunks)


def _read_confined_regular_file(path: Path, root: Path) -> bytes:
    descriptor, _directory_identities = _open_confined_regular_file(path, root)
    try:
        before = os.fstat(descriptor)
        content = _read_descriptor(descriptor)
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after):
            raise PooledReviewValidationError(f"file changed while being read: {path}")
        return content
    finally:
        os.close(descriptor)


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


@dataclass
class _StableFileSnapshot:
    path: Path
    root: Path
    descriptor: int
    directory_identities: tuple[tuple[int, int], ...]
    metadata_identity: tuple[int, int, int, int, int]
    content: bytes

    @classmethod
    def open(cls, path: Path, root: Path) -> _StableFileSnapshot:
        descriptor, directory_identities = _open_confined_regular_file(path, root)
        try:
            before = os.fstat(descriptor)
            content = _read_descriptor(descriptor)
            after = os.fstat(descriptor)
            if _stat_identity(before) != _stat_identity(after):
                raise PooledReviewValidationError(f"file changed while being read: {path}")
            return cls(
                Path(path),
                Path(root),
                descriptor,
                directory_identities,
                _stat_identity(after),
                content,
            )
        except BaseException:
            os.close(descriptor)
            raise

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1

    def verify_current(self) -> bool:
        if self.descriptor < 0:
            return False
        if _stat_identity(os.fstat(self.descriptor)) != self.metadata_identity:
            return False
        current_descriptor = -1
        try:
            current_descriptor, current_directories = _open_confined_regular_file(
                self.path,
                self.root,
            )
            before = os.fstat(current_descriptor)
            current_content = _read_descriptor(current_descriptor)
            after = os.fstat(current_descriptor)
            return (
                current_directories == self.directory_identities
                and _stat_identity(before) == self.metadata_identity
                and _stat_identity(after) == self.metadata_identity
                and current_content == self.content
            )
        except (OSError, PooledReviewValidationError):
            return False
        finally:
            if current_descriptor >= 0:
                os.close(current_descriptor)


_DRAFT7_SINGLE_SUBSCHEMA_KEYWORDS = frozenset(
    {
        "additionalItems",
        "additionalProperties",
        "contains",
        "else",
        "if",
        "not",
        "propertyNames",
        "then",
    }
)
_DRAFT7_ARRAY_SUBSCHEMA_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf"})
_DRAFT7_MAPPING_SUBSCHEMA_KEYWORDS = frozenset(
    {"definitions", "patternProperties", "properties"}
)
_SchemaLocation = tuple[str, str]


def _pointer_append(pointer: str, *tokens: object) -> str:
    encoded = [str(token).replace("~", "~0").replace("/", "~1") for token in tokens]
    return pointer + "/" + "/".join(encoded)


def _draft7_subschemas(
    schema: dict[str, Any],
    pointer: str,
) -> tuple[tuple[str, object], ...]:
    children: list[tuple[str, object]] = []
    for keyword in _DRAFT7_SINGLE_SUBSCHEMA_KEYWORDS:
        child = schema.get(keyword)
        if isinstance(child, (dict, bool)):
            children.append((_pointer_append(pointer, keyword), child))
    for keyword in _DRAFT7_ARRAY_SUBSCHEMA_KEYWORDS:
        values = schema.get(keyword)
        if isinstance(values, list):
            for index, child in enumerate(values):
                if isinstance(child, (dict, bool)):
                    children.append((_pointer_append(pointer, keyword, index), child))
    for keyword in _DRAFT7_MAPPING_SUBSCHEMA_KEYWORDS:
        values = schema.get(keyword)
        if isinstance(values, dict):
            for name, child in values.items():
                if isinstance(child, (dict, bool)):
                    children.append((_pointer_append(pointer, keyword, name), child))

    items = schema.get("items")
    if isinstance(items, (dict, bool)):
        children.append((_pointer_append(pointer, "items"), items))
    elif isinstance(items, list):
        for index, child in enumerate(items):
            if isinstance(child, (dict, bool)):
                children.append((_pointer_append(pointer, "items", index), child))

    dependencies = schema.get("dependencies")
    if isinstance(dependencies, dict):
        for name, child in dependencies.items():
            if isinstance(child, (dict, bool)):
                children.append((_pointer_append(pointer, "dependencies", name), child))
    return tuple(children)


def _schema_nodes(value: object, pointer: str) -> dict[str, object]:
    if not isinstance(value, (dict, bool)):
        raise PooledReviewValidationError("schema reference target is not a schema")
    nodes: dict[str, object] = {}
    pending = [(pointer, value)]
    while pending:
        current_pointer, schema = pending.pop()
        if current_pointer in nodes:
            continue
        nodes[current_pointer] = schema
        if not isinstance(schema, dict):
            continue
        schema_id = schema.get("$id")
        if schema_id is not None and (
            current_pointer != "" or not isinstance(schema_id, str)
        ):
            raise PooledReviewValidationError(
                "nested or non-string schema $id is forbidden"
            )
        pending.extend(_draft7_subschemas(schema, current_pointer))
    return nodes


def _decode_pointer_token(token: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        if token[index] != "~":
            decoded.append(token[index])
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise PooledReviewValidationError("invalid schema JSON pointer")
        decoded.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(decoded)


def _canonical_json_pointer(fragment: str) -> str:
    if not fragment:
        return ""
    if not fragment.startswith("/"):
        raise PooledReviewValidationError("schema fragment is not a JSON pointer")
    tokens = [_decode_pointer_token(token) for token in fragment[1:].split("/")]
    return _pointer_append("", *tokens)


def _local_schema_ref(
    ref: str,
    current_name: str,
    allowed_names: frozenset[str],
) -> _SchemaLocation:
    parsed = urlsplit(ref)
    path = unquote(parsed.path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.username
        or parsed.password
    ):
        raise PooledReviewValidationError(f"schema reference is not bundled: {ref}")
    if path:
        relative = Path(path)
        if (
            "\\" in path
            or relative.is_absolute()
            or len(relative.parts) != 1
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.name not in allowed_names
        ):
            raise PooledReviewValidationError(
                f"schema reference is not bundled: {ref}"
            )
        target_name = relative.name
    else:
        target_name = current_name
    pointer = _canonical_json_pointer(unquote(parsed.fragment))
    return target_name, pointer


def _resolve_json_pointer(document: object, pointer: str) -> object:
    current = document
    if not pointer:
        return current
    for encoded_token in pointer[1:].split("/"):
        token = _decode_pointer_token(encoded_token)
        try:
            if isinstance(current, dict):
                current = current[token]
            elif isinstance(current, list):
                current = current[int(token)]
            else:
                raise KeyError(token)
        except (IndexError, KeyError, ValueError) as exc:
            raise PooledReviewValidationError(
                f"schema JSON pointer does not resolve: {pointer}"
            ) from exc
    return current


def _deny_schema_resolution(uri: str) -> object:
    raise PooledReviewValidationError(f"external schema resolution is forbidden: {uri}")


def _reject_schema_reference_cycles(
    references: Mapping[_SchemaLocation, frozenset[_SchemaLocation]],
) -> None:
    colors: dict[_SchemaLocation, int] = {}
    for start in references:
        if colors.get(start, 0) != 0:
            continue
        stack = [(start, False)]
        while stack:
            location, expanded = stack.pop()
            if expanded:
                colors[location] = 2
                continue
            if colors.get(location, 0) == 2:
                continue
            colors[location] = 1
            stack.append((location, True))
            for referenced_location in references.get(location, frozenset()):
                referenced_color = colors.get(referenced_location, 0)
                if referenced_color == 1:
                    raise PooledReviewValidationError(
                        "bundled schema reference cycle"
                    )
                if referenced_color == 0:
                    stack.append((referenced_location, False))


def _confined_schema_validator(
    schema_root: Path,
    selected_name: str,
    allowed_names: frozenset[str],
) -> jsonschema.Draft7Validator:
    documents: dict[str, dict[str, Any]] = {}
    schemas: dict[_SchemaLocation, object] = {}
    references: dict[_SchemaLocation, frozenset[_SchemaLocation]] = {}
    pending_documents = [selected_name]
    pending_schema_roots: list[_SchemaLocation] = []
    pending_scans: list[_SchemaLocation] = []
    while pending_documents or pending_schema_roots or pending_scans:
        if pending_documents:
            name = pending_documents.pop()
            if name in documents:
                continue
            raw = _read_confined_regular_file(
                Path(schema_root) / name,
                Path(schema_root),
            )
            document = json.loads(raw.decode("utf-8"))
            if not isinstance(document, dict) or document.get("$id") != name:
                raise PooledReviewValidationError(
                    f"invalid bundled schema identity: {name}"
                )
            jsonschema.Draft7Validator.check_schema(document)
            documents[name] = document
            for pointer, schema in _schema_nodes(document, "").items():
                location = (name, pointer)
                if location not in schemas:
                    schemas[location] = schema
                    pending_scans.append(location)
            continue

        if pending_schema_roots:
            location = pending_schema_roots.pop()
            name, pointer = location
            if name not in documents:
                pending_schema_roots.append(location)
                pending_documents.append(name)
                continue
            if location in schemas:
                continue
            target = _resolve_json_pointer(documents[name], pointer)
            for child_pointer, schema in _schema_nodes(target, pointer).items():
                child_location = (name, child_pointer)
                if child_location not in schemas:
                    schemas[child_location] = schema
                    pending_scans.append(child_location)
            continue

        location = pending_scans.pop()
        schema = schemas[location]
        if not isinstance(schema, dict) or "$ref" not in schema:
            references[location] = frozenset()
            continue
        ref = schema["$ref"]
        if not isinstance(ref, str):
            raise PooledReviewValidationError("schema $ref must be a string")
        referenced_location = _local_schema_ref(
            ref,
            location[0],
            allowed_names,
        )
        references[location] = frozenset({referenced_location})
        pending_schema_roots.append(referenced_location)

    if selected_name not in documents:
        raise PooledReviewValidationError(f"missing selected schema: {selected_name}")
    _reject_schema_reference_cycles(references)
    store: dict[str, object] = {}
    for name, document in documents.items():
        store[name] = document
        store[urljoin(_SCHEMA_BASE_URI, name)] = document
    resolver = jsonschema.RefResolver(
        base_uri=_SCHEMA_BASE_URI,
        referrer=documents[selected_name],
        store=store,
        handlers={
            scheme: _deny_schema_resolution
            for scheme in ("file", "ftp", "http", "https")
        },
        cache_remote=False,
    )
    return jsonschema.Draft7Validator(documents[selected_name], resolver=resolver)


@dataclass(frozen=True)
class PooledReviewHealth:
    healthy: bool
    concerns: tuple[str, ...]


def pooled_review_path(skill_root: Path) -> Path:
    return Path(skill_root) / ".pooled-blueprint-review.yaml"


def pooled_review_health_path(skill_root: Path) -> Path:
    return Path(skill_root) / ".pooled-blueprint-review.health.json"


def _review_path(path: Path, graph: SkillBlueprintGraph) -> str:
    try:
        return path.relative_to(graph.skill_root).as_posix()
    except ValueError:
        repository_root = graph.skill_root.parent.parent
        try:
            return "$repo/" + path.relative_to(repository_root).as_posix()
        except ValueError:
            return path.as_posix()


def render_pooled_review(
    graph: SkillBlueprintGraph,
    records: Mapping[str, dict[str, Any]],
) -> str:
    """Render a deterministic expanded review without creating graph authority."""

    root_record = records[graph.root.node_id]
    certification = root_record.get("certification", {})
    nodes: list[dict[str, Any]] = []
    for node_id in sorted(graph.nodes):
        node = graph.nodes[node_id]
        record = records.get(health_owner_node_id(graph, node_id), {})
        hashes = record.get("hashes", {}) if isinstance(record, dict) else {}
        nodes.append(
            {
                "id": node.node_id,
                "blueprint_type": node.blueprint_type,
                "version": node.version,
                "blueprint_path": _review_path(node.blueprint_path, graph),
                "binding_path": (
                    _review_path(node.binding_path, graph)
                    if node.binding_path is not None
                    else None
                ),
                "declaration": deepcopy(node.declaration),
                "health": {
                    "result": record.get("certification", {}).get("result")
                    if isinstance(record, dict)
                    else None,
                    "artifact_graph_hash": hashes.get("artifact_graph_hash")
                    if isinstance(hashes, dict)
                    else None,
                    "certified_health_hash": hashes.get("certified_health_hash")
                    if isinstance(hashes, dict)
                    else None,
                },
            }
        )
    root_hashes = root_record.get("hashes", {})
    document = {
        "document_type": "pooled-blueprint-review",
        "generated_at": certification.get("certified_at"),
        "root": {
            "id": graph.root.node_id,
            "blueprint_path": "blueprint.yaml",
            "artifact_graph_hash": root_hashes.get("artifact_graph_hash"),
            "certified_health_hash": root_hashes.get("certified_health_hash"),
        },
        "nodes": nodes,
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=False)


def certify_pooled_review(
    path: Path,
    root_record: dict[str, Any],
    *,
    key: bytes,
    certified_at: str,
) -> dict[str, Any]:
    """Create authenticated health for a generated pooled review file."""

    snapshot = _StableFileSnapshot.open(path, Path(path).parent)
    try:
        root_subject = root_record.get("subject", {})
        root_hashes = root_record.get("hashes", {})
        root_id = root_subject.get("id")
        root_health_hash = root_hashes.get("certified_health_hash")
        record = {
            "health_schema_version": 1,
            "record_type": "pooled-review-health",
            "subject": {"root_id": root_id, "path": path.name},
            "certification": {"result": "passed", "certified_at": certified_at},
            "certifier": deepcopy(root_record.get("certifier")),
            "hashes": {
                "pooled_file_hash": "sha256:"
                + hashlib.sha256(snapshot.content).hexdigest(),
                "root_certified_health_hash": root_health_hash,
            },
            "dependencies": [
                {
                    "relation": "reviews-root",
                    "target": root_id,
                    "certified_health_hash": root_health_hash,
                }
            ],
            "checks": [],
            "coverage": {},
        }
        authenticated = attach_record_authentication(record, key)
        if not snapshot.verify_current():
            raise PooledReviewValidationError(
                f"pooled review path changed during certification: {path}"
            )
        return authenticated
    finally:
        snapshot.close()


def _node_health_path(path: Path, owner_root: Path) -> str:
    try:
        return path.relative_to(owner_root).as_posix()
    except ValueError:
        return str(path)


def _records_are_admitted(
    graph: SkillBlueprintGraph,
    records: Mapping[str, dict[str, object]],
    root_report: GraphHealthReport,
    key: bytes,
    health_validator: jsonschema.Draft7Validator,
) -> bool:
    node_ids = set(health_node_ids(graph))
    if (
        not root_report.healthy
        or graph.root.node_id != root_report.root_id
        or set(records) != node_ids
        or set(root_report.nodes) != node_ids
    ):
        return False
    for node_id in node_ids:
        record = records[node_id]
        node = graph.nodes[node_id]
        status = root_report.nodes[node_id]
        if not isinstance(record, dict) or not status.healthy:
            return False
        if not record_authentication_matches(record, key):
            return False
        health_validator.validate(record)
        subject = record.get("subject")
        hashes = record.get("hashes")
        expected_subject = {
            "id": node.node_id,
            "blueprint_type": node.blueprint_type,
            "version": node.version,
            "blueprint_path": _node_health_path(node.blueprint_path, node.skill_root),
            "binding_path": (
                _node_health_path(node.binding_path, node.skill_root)
                if node.binding_path is not None
                else None
            ),
        }
        if (
            status.node_id != node_id
            or subject != expected_subject
            or record.get("record_hash") != status.admitted_record_hash
            or status.admitted_record_hash is None
            or not isinstance(hashes, dict)
            or hashes.get("certified_health_hash")
            != status.recorded_certified_health_hash
            or status.recorded_certified_health_hash
            != status.expected_certified_health_hash
        ):
            return False
    return True


def _check_pooled_review_snapshots(
    path: Path,
    root_report: GraphHealthReport,
    key: bytes,
    *,
    graph: SkillBlueprintGraph,
    records: Mapping[str, dict[str, object]],
    schema_root: Path,
    pool_snapshot: _StableFileSnapshot,
    health_snapshot: _StableFileSnapshot,
    certifier: Mapping[str, Any] = _DEFAULT_CERTIFIER,
) -> PooledReviewHealth:
    concerns: list[str] = []
    if not root_report.healthy:
        concerns.append("root-unhealthy")

    try:
        pooled_bytes = pool_snapshot.content
        document = yaml.safe_load(pooled_bytes.decode("utf-8"))
        _confined_schema_validator(
            Path(schema_root),
            "pooled-review.schema.json",
            _POOL_SCHEMA_BUNDLE,
        ).validate(document)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        yaml.YAMLError,
        jsonschema.ValidationError,
        jsonschema.SchemaError,
        jsonschema.RefResolutionError,
        OverflowError,
        TypeError,
        ValueError,
    ):
        concerns.append("invalid-pooled-review")
        return PooledReviewHealth(False, tuple(dict.fromkeys(concerns)))

    try:
        health_validator = _confined_schema_validator(
            Path(schema_root),
            "health.schema.json",
            _HEALTH_SCHEMA_BUNDLE,
        )
        if root_report.healthy and not _records_are_admitted(
            graph,
            records,
            root_report,
            key,
            health_validator,
        ):
            concerns.append("invalid-pooled-review")
            return PooledReviewHealth(False, tuple(dict.fromkeys(concerns)))
        expected = render_pooled_review(graph, records).encode("utf-8")
    except (
        AttributeError,
        KeyError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        jsonschema.ValidationError,
        jsonschema.SchemaError,
        jsonschema.RefResolutionError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ):
        concerns.append("invalid-pooled-review")
        return PooledReviewHealth(False, tuple(dict.fromkeys(concerns)))
    if pooled_bytes != expected:
        concerns.append("noncanonical-pooled-review")

    try:
        record = json.loads(health_snapshot.content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        concerns.append("invalid-pooled-review-health")
        return PooledReviewHealth(False, tuple(dict.fromkeys(concerns)))
    if not isinstance(record, dict):
        concerns.append("invalid-pooled-review-health")
        return PooledReviewHealth(False, tuple(dict.fromkeys(concerns)))
    try:
        authenticated = record_authentication_matches(record, key)
    except (AttributeError, OverflowError, TypeError, ValueError):
        authenticated = False
    if not authenticated:
        concerns.append("pooled-review-authentication-failed")
        return PooledReviewHealth(False, tuple(dict.fromkeys(concerns)))

    try:
        health_validator.validate(record)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        jsonschema.ValidationError,
        jsonschema.SchemaError,
        jsonschema.RefResolutionError,
        TypeError,
        ValueError,
    ):
        concerns.append("invalid-pooled-review-health")
        return PooledReviewHealth(False, tuple(dict.fromkeys(concerns)))

    try:
        root_status = root_report.nodes[root_report.root_id]
    except (KeyError, TypeError):
        concerns.append("invalid-pooled-review-health")
        return PooledReviewHealth(False, tuple(dict.fromkeys(concerns)))
    expected_dependency = [
        {
            "relation": "reviews-root",
            "target": root_report.root_id,
            "certified_health_hash": root_status.expected_certified_health_hash,
        }
    ]
    if (
        record.get("record_type") != "pooled-review-health"
        or record.get("subject")
        != {"root_id": root_report.root_id, "path": path.name}
        or record.get("certifier") != dict(certifier)
        or record.get("dependencies") != expected_dependency
    ):
        concerns.append("invalid-pooled-review-health")
        return PooledReviewHealth(False, tuple(dict.fromkeys(concerns)))

    hashes = record.get("hashes", {}) if isinstance(record, dict) else {}
    pooled_file_hash = "sha256:" + hashlib.sha256(pooled_bytes).hexdigest()
    if not isinstance(hashes, dict) or hashes.get("pooled_file_hash") != pooled_file_hash:
        concerns.append("pooled-review-stale")
    if (
        not isinstance(hashes, dict)
        or hashes.get("root_certified_health_hash")
        != root_status.expected_certified_health_hash
    ):
        concerns.append("pooled-review-root-stale")
    if not concerns:
        if not pool_snapshot.verify_current():
            concerns.append("invalid-pooled-review")
        if not health_snapshot.verify_current():
            concerns.append("invalid-pooled-review-health")
    return PooledReviewHealth(not concerns, tuple(dict.fromkeys(concerns)))


def check_pooled_review(
    path: Path,
    health_path: Path,
    root_report: GraphHealthReport,
    key: bytes,
    *,
    graph: SkillBlueprintGraph,
    records: Mapping[str, dict[str, object]],
    schema_root: Path,
    certifier: Mapping[str, Any] = _DEFAULT_CERTIFIER,
) -> PooledReviewHealth:
    """Check exact generated pool health without creating graph authority."""

    artifact_root = Path(os.path.abspath(path)).parent
    if Path(os.path.abspath(health_path)).parent != artifact_root:
        return PooledReviewHealth(False, ("invalid-pooled-review-health",))
    try:
        pool_snapshot = _StableFileSnapshot.open(Path(path), artifact_root)
    except FileNotFoundError:
        concerns = ["missing-pooled-review"]
        if not root_report.healthy:
            concerns.insert(0, "root-unhealthy")
        return PooledReviewHealth(False, tuple(concerns))
    except (OSError, PooledReviewValidationError):
        concerns = ["invalid-pooled-review"]
        if not root_report.healthy:
            concerns.insert(0, "root-unhealthy")
        return PooledReviewHealth(False, tuple(concerns))
    try:
        try:
            health_snapshot = _StableFileSnapshot.open(Path(health_path), artifact_root)
        except FileNotFoundError:
            concerns = ["missing-pooled-review-health"]
            if not root_report.healthy:
                concerns.insert(0, "root-unhealthy")
            return PooledReviewHealth(False, tuple(concerns))
        except (OSError, PooledReviewValidationError):
            concerns = ["invalid-pooled-review-health"]
            if not root_report.healthy:
                concerns.insert(0, "root-unhealthy")
            return PooledReviewHealth(False, tuple(concerns))
        try:
            return _check_pooled_review_snapshots(
                Path(path),
                root_report,
                key,
                graph=graph,
                records=records,
                schema_root=Path(schema_root),
                pool_snapshot=pool_snapshot,
                health_snapshot=health_snapshot,
                certifier=certifier,
            )
        finally:
            health_snapshot.close()
    finally:
        pool_snapshot.close()
