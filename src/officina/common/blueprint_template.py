"""Render blueprint YAML from JSON Schema annotations.

Blueprint values are user-owned.  Blueprint comments are schema-owned generated
documentation, so refreshes intentionally discard existing YAML comments and
emit fresh documentation tags from the schema.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from textwrap import wrap
from typing import Any, Mapping

import jsonschema
import yaml


JsonMapping = Mapping[str, Any]
DocMode = str

_ANNOTATION_KEYS = {"description", "$comment", "examples", "x-famulus"}
_HEADER_LINES = [
    "Generated documentation comments.",
    "Blueprint values are editable; comments are regenerated from the schema.",
    "Do not store durable notes in this file's comments.",
]


def load_schema(path: str | Path) -> dict[str, Any]:
    """Load a JSON-compatible schema file."""
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_regenerated_skill_blueprint(
    skill_name: str,
    *,
    repo_root: str | Path = ".",
    output_dir: str | Path = "/tmp",
    schema_path: str | Path | None = None,
    doc_mode: DocMode = "compact",
) -> Path:
    """Write a refreshed blueprint for ``skill_name`` under ``output_dir``.

    Existing blueprint values are preserved exactly as parsed YAML; generated
    comments are refreshed from the schema.
    """
    if not skill_name or "/" in skill_name or "\\" in skill_name:
        raise ValueError(f"invalid skill name: {skill_name!r}")

    root = Path(repo_root)
    blueprint_path = root / "skills" / skill_name / "blueprint.yaml"
    if not blueprint_path.exists():
        raise FileNotFoundError(f"missing blueprint: {blueprint_path}")

    resolved_schema_path = Path(schema_path) if schema_path is not None else _default_schema_path(root)
    if not resolved_schema_path.is_absolute():
        resolved_schema_path = root / resolved_schema_path
    schema = load_schema(resolved_schema_path)

    original = blueprint_path.read_text(encoding="utf-8")
    rendered = refresh_blueprint_documentation(schema, original, doc_mode=doc_mode)
    data = yaml.safe_load(rendered)
    jsonschema.Draft7Validator(schema).validate(data)
    if yaml.safe_load(original) != data:
        raise ValueError(f"refreshed blueprint changed parsed values for {skill_name!r}")

    destination = Path(output_dir) / f"{skill_name}_blueprint.yaml"
    destination.write_text(rendered, encoding="utf-8")
    return destination


def render_blueprint_template(schema: JsonMapping, *, doc_mode: DocMode = "full") -> str:
    """Render a documented blueprint template from schema examples/defaults."""
    values = _value_from_schema(schema, schema)
    return render_blueprint_from_schema(schema, values, doc_mode=doc_mode, include_missing_template_fields=True)


def _default_schema_path(repo_root: Path) -> Path:
    annotated = repo_root / "references" / "blueprint" / "schema.annotated-draft.json"
    if annotated.exists():
        return annotated
    return repo_root / "references" / "blueprint" / "schema.json"


def refresh_blueprint_documentation(
    schema: JsonMapping,
    blueprint_yaml: str,
    *,
    doc_mode: DocMode = "full",
) -> str:
    """Preserve blueprint values while replacing all YAML comments."""
    loaded = yaml.safe_load(blueprint_yaml) or {}
    if not isinstance(loaded, dict):
        raise ValueError("blueprint YAML must contain a mapping at the top level")
    return render_blueprint_from_schema(schema, loaded, doc_mode=doc_mode, include_missing_template_fields=False)


def render_blueprint_from_schema(
    schema: JsonMapping,
    values: Mapping[str, Any] | None = None,
    *,
    doc_mode: DocMode = "full",
    include_missing_template_fields: bool | None = None,
) -> str:
    """Render ``values`` as YAML with comments derived from ``schema``.

    If ``values`` is omitted, template values are synthesized from
    ``x-famulus.template.example``, ``examples``, ``default``, ``const``,
    ``enum``, and required object properties.
    """
    _validate_doc_mode(doc_mode)
    if include_missing_template_fields is None:
        include_missing_template_fields = values is None
    concrete_values = deepcopy(dict(values)) if values is not None else _value_from_schema(schema, schema)
    lines = [f"# {line}" for line in _HEADER_LINES]
    root_description = schema.get("description")
    if isinstance(root_description, str) and root_description.strip():
        lines.extend(_tagged_comment_lines([("summary", root_description.strip())], 0, path="$", doc_mode=doc_mode))
    lines.extend(
        _render_mapping(
            schema,
            concrete_values,
            schema,
            path=(),
            indent=0,
            doc_mode=doc_mode,
            include_missing_template_fields=include_missing_template_fields,
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_mapping(
    schema: JsonMapping,
    values: Mapping[str, Any],
    root: JsonMapping,
    *,
    path: tuple[str, ...],
    indent: int,
    doc_mode: DocMode,
    include_missing_template_fields: bool,
) -> list[str]:
    resolved = _resolve_schema(schema, root)
    properties = resolved.get("properties")
    if not isinstance(properties, dict):
        return _render_dynamic_mapping(
            resolved,
            values,
            root,
            path=path,
            indent=indent,
            doc_mode=doc_mode,
            include_missing_template_fields=include_missing_template_fields,
        )

    lines: list[str] = []
    emitted: set[str] = set()
    for key, child_schema in properties.items():
        should_emit = key in values or _should_include_missing_property(
            resolved,
            key,
            child_schema,
            root,
            include_missing_template_fields=include_missing_template_fields,
        )
        if not should_emit:
            continue
        child_value = deepcopy(values[key]) if key in values else _value_from_schema(child_schema, root)
        lines.extend(
            _render_property(
                key,
                child_schema,
                child_value,
                root,
                path=path + (key,),
                indent=indent,
                doc_mode=doc_mode,
                include_missing_template_fields=include_missing_template_fields,
            )
        )
        emitted.add(key)

    for key, child_value in values.items():
        if key in emitted:
            continue
        lines.extend(
            _render_property(
                key,
                {},
                child_value,
                root,
                path=path + (key,),
                indent=indent,
                doc_mode=doc_mode,
                include_missing_template_fields=include_missing_template_fields,
            )
        )
    return lines


def _render_dynamic_mapping(
    schema: JsonMapping,
    values: Mapping[str, Any],
    root: JsonMapping,
    *,
    path: tuple[str, ...],
    indent: int,
    doc_mode: DocMode,
    include_missing_template_fields: bool,
) -> list[str]:
    additional = schema.get("additionalProperties")
    child_schema = additional if isinstance(additional, dict) else {}
    lines: list[str] = []
    for key, child_value in values.items():
        lines.extend(
            _render_property(
                key,
                child_schema,
                child_value,
                root,
                path=path + (str(key),),
                indent=indent,
                doc_mode=doc_mode,
                include_missing_template_fields=include_missing_template_fields,
            )
        )
    return lines


def _render_property(
    key: Any,
    schema: JsonMapping,
    value: Any,
    root: JsonMapping,
    *,
    path: tuple[str, ...],
    indent: int,
    doc_mode: DocMode,
    include_missing_template_fields: bool,
) -> list[str]:
    resolved = _resolve_schema(schema, root, value)
    lines = _schema_comment_lines(resolved, path, indent, doc_mode)
    key_text = _plain_key(key)

    if isinstance(value, Mapping):
        if value:
            lines.append(f"{' ' * indent}{key_text}:")
            lines.extend(
                _render_mapping(
                    resolved,
                    value,
                    root,
                    path=path,
                    indent=indent + 2,
                    doc_mode=doc_mode,
                    include_missing_template_fields=include_missing_template_fields,
                )
            )
        else:
            lines.append(f"{' ' * indent}{key_text}: {{}}")
        return lines

    if isinstance(value, list):
        if value:
            lines.append(f"{' ' * indent}{key_text}:")
            lines.extend(
                _render_sequence(
                    resolved,
                    value,
                    root,
                    path=path,
                    indent=indent + 2,
                    doc_mode=doc_mode,
                    include_missing_template_fields=include_missing_template_fields,
                )
            )
        else:
            lines.append(f"{' ' * indent}{key_text}: []")
        return lines

    block_lines = _format_block_scalar(value, indent + 2)
    if block_lines is not None:
        lines.append(f"{' ' * indent}{key_text}: >-")
        lines.extend(block_lines)
    else:
        lines.append(f"{' ' * indent}{key_text}: {_format_scalar(value)}")
    return lines


def _render_sequence(
    schema: JsonMapping,
    values: list[Any],
    root: JsonMapping,
    *,
    path: tuple[str, ...],
    indent: int,
    doc_mode: DocMode,
    include_missing_template_fields: bool,
) -> list[str]:
    item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
    lines: list[str] = []
    for value in values:
        resolved_item = _resolve_schema(item_schema, root, value)
        if isinstance(value, Mapping):
            if value:
                lines.append(f"{' ' * indent}-")
                lines.extend(
                    _render_mapping(
                        resolved_item,
                        value,
                        root,
                        path=path + ("[]",),
                        indent=indent + 2,
                        doc_mode=doc_mode,
                        include_missing_template_fields=include_missing_template_fields,
                    )
                )
            else:
                lines.append(f"{' ' * indent}- {{}}")
        elif isinstance(value, list):
            if value:
                lines.append(f"{' ' * indent}-")
                lines.extend(
                    _render_sequence(
                        resolved_item,
                        value,
                        root,
                        path=path + ("[]",),
                        indent=indent + 2,
                        doc_mode=doc_mode,
                        include_missing_template_fields=include_missing_template_fields,
                    )
                )
            else:
                lines.append(f"{' ' * indent}- []")
        else:
            lines.append(f"{' ' * indent}- {_format_scalar(value)}")
    return lines


def _value_from_schema(schema: JsonMapping, root: JsonMapping) -> Any:
    resolved = _resolve_schema(schema, root)
    template = _template_metadata(resolved)
    if "example" in template:
        return deepcopy(template["example"])
    if "examples" in resolved and isinstance(resolved["examples"], list) and resolved["examples"]:
        return deepcopy(resolved["examples"][0])
    if "default" in resolved:
        return deepcopy(resolved["default"])
    if "const" in resolved:
        return deepcopy(resolved["const"])
    if "enum" in resolved and isinstance(resolved["enum"], list) and resolved["enum"]:
        return deepcopy(resolved["enum"][0])

    one_of = resolved.get("oneOf")
    if isinstance(one_of, list) and one_of:
        return _value_from_schema(one_of[0], root)

    schema_type = _schema_type(resolved)
    if schema_type == "object":
        return _object_value_from_schema(resolved, root)
    if schema_type == "array":
        return []
    if schema_type == "boolean":
        return False
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1
    return "TODO"


def _object_value_from_schema(schema: JsonMapping, root: JsonMapping) -> dict[str, Any]:
    properties = schema.get("properties")
    result: dict[str, Any] = {}
    if isinstance(properties, dict):
        required = _required_keys(schema)
        for key, child_schema in properties.items():
            if key in required or _template_metadata(_resolve_schema(child_schema, root)).get("include") is True:
                result[key] = _value_from_schema(child_schema, root)
        return result

    required_keys = list(_required_keys(schema))
    additional = schema.get("additionalProperties")
    if required_keys and isinstance(additional, dict):
        for key in required_keys:
            result[str(key)] = _value_from_schema(additional, root)
    return result


def _should_include_missing_property(
    parent_schema: JsonMapping,
    key: str,
    child_schema: JsonMapping,
    root: JsonMapping,
    *,
    include_missing_template_fields: bool,
) -> bool:
    if key in _required_keys(parent_schema):
        return True
    if not include_missing_template_fields:
        return False
    resolved_child = _resolve_schema(child_schema, root)
    return _template_metadata(resolved_child).get("include") is True


def _required_keys(schema: JsonMapping) -> set[str]:
    required = {str(key) for key in schema.get("required", [])}
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for item in all_of:
            if not isinstance(item, dict):
                continue
            one_of = item.get("oneOf")
            if not isinstance(one_of, list) or not one_of:
                continue
            first_branch = one_of[0]
            if isinstance(first_branch, dict):
                required.update(str(key) for key in first_branch.get("required", []))
    return required


def _resolve_schema(schema: JsonMapping, root: JsonMapping, value: Any | None = None) -> dict[str, Any]:
    resolved = dict(schema)
    if "$ref" in resolved:
        ref_target = _resolve_ref(str(resolved["$ref"]), root)
        local = {key: val for key, val in resolved.items() if key != "$ref"}
        resolved = {**ref_target, **local}

    one_of = resolved.get("oneOf")
    if isinstance(one_of, list) and one_of and not _schema_has_renderable_shape(resolved):
        branch = _select_one_of_branch(one_of, value, root)
        branch_resolved = _resolve_schema(branch, root, value)
        overlays = {key: val for key, val in resolved.items() if key not in {"oneOf"}}
        resolved = {**branch_resolved, **overlays}
    return resolved


def _resolve_ref(ref: str, root: JsonMapping) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported schema ref: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    if not isinstance(node, dict):
        raise ValueError(f"schema ref does not point to an object: {ref}")
    return dict(node)


def _select_one_of_branch(branches: list[Any], value: Any, root: JsonMapping) -> JsonMapping:
    dict_branches = [branch for branch in branches if isinstance(branch, dict)]
    if isinstance(value, Mapping):
        for branch in dict_branches:
            resolved = _resolve_schema(branch, root)
            properties = resolved.get("properties")
            if not isinstance(properties, dict):
                continue
            required = set(resolved.get("required", []))
            if not required.issubset(value.keys()):
                continue
            consts_match = True
            for key, child_schema in properties.items():
                child_resolved = _resolve_schema(child_schema, root)
                if "const" in child_resolved and key in value and value[key] != child_resolved["const"]:
                    consts_match = False
                    break
            if consts_match:
                return branch
    if dict_branches:
        return dict_branches[0]
    return {}


def _schema_has_renderable_shape(schema: JsonMapping) -> bool:
    return any(key in schema for key in ("type", "properties", "items", "additionalProperties", "enum", "const"))


def _schema_type(schema: JsonMapping) -> str | None:
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return schema_type
    if "properties" in schema or "additionalProperties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return None


def _template_metadata(schema: JsonMapping) -> dict[str, Any]:
    extension = schema.get("x-famulus")
    if not isinstance(extension, dict):
        return {}
    template = extension.get("template")
    return template if isinstance(template, dict) else {}


def _schema_comment_lines(schema: JsonMapping, path: tuple[str, ...], indent: int, doc_mode: DocMode) -> list[str]:
    tags: list[tuple[str, str]] = []
    description = schema.get("description")
    if isinstance(description, str) and description.strip():
        tags.append(("summary", description.strip()))

    extension = schema.get("x-famulus")
    if isinstance(extension, dict):
        field_status = extension.get("field_status")
        if isinstance(field_status, str) and field_status.strip():
            tags.append(("status", field_status.strip()))

        doc = extension.get("doc")
        if isinstance(doc, dict):
            for item in doc.get("authoring", []):
                if isinstance(item, str) and item.strip():
                    tags.append(("authoring", item.strip()))
            for item in doc.get("red_flags", []):
                if isinstance(item, str) and item.strip():
                    tags.append(("red-flag", item.strip()))
        rule_ids = extension.get("related_validation_rules")
        if isinstance(rule_ids, list):
            for rule_id in rule_ids:
                if str(rule_id).strip():
                    tags.append(("validator", str(rule_id).strip()))

    if not tags:
        return []
    return _tagged_comment_lines(tags, indent, path=_path_text(path), doc_mode=doc_mode)


def _tagged_comment_lines(tags: list[tuple[str, str]], indent: int, *, path: str, doc_mode: DocMode) -> list[str]:
    prefix = " " * indent
    lines = [f"{prefix}# @schema-doc path={path}"]
    allowed_tags = _allowed_doc_tags(doc_mode)
    for tag, value in tags:
        if tag not in allowed_tags:
            continue
        wrapped = wrap(value, width=max(40, 88 - indent - len(tag) - 4))
        if not wrapped:
            continue
        lines.append(f"{prefix}# @{tag} {wrapped[0]}")
        for continuation in wrapped[1:]:
            lines.append(f"{prefix}#   {continuation}")
    return lines


def _validate_doc_mode(doc_mode: DocMode) -> None:
    if doc_mode not in {"full", "compact"}:
        raise ValueError(f"unsupported doc mode: {doc_mode!r}")


def _allowed_doc_tags(doc_mode: DocMode) -> set[str]:
    if doc_mode == "compact":
        return {"summary", "status", "validator"}
    return {"summary", "status", "authoring", "red-flag", "validator"}


def _path_text(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _plain_key(key: Any) -> str:
    if isinstance(key, str) and key.replace("-", "").replace("_", "").isalnum() and not key[:1].isdigit():
        return key
    return _format_scalar(key)


def _format_scalar(value: Any) -> str:
    dumped = yaml.safe_dump(value, default_flow_style=True, sort_keys=False, allow_unicode=False)
    lines = [line for line in dumped.splitlines() if line != "..."]
    return " ".join(line.strip() for line in lines)


def _format_block_scalar(value: Any, indent: int) -> list[str] | None:
    if not isinstance(value, str):
        return None
    if "\n" in value or value != value.strip():
        return None
    width = max(40, 88 - indent)
    if len(value) <= width:
        return None
    prefix = " " * indent
    lines = wrap(value, width=width, break_long_words=False, break_on_hyphens=False)
    rendered = "value: >-\n" + "\n".join(f"  {line}" for line in lines) + "\n"
    if yaml.safe_load(rendered)["value"] != value:
        return None
    return [f"{prefix}{line}" for line in lines]
