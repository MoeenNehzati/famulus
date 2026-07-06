"""get_schema: sole owner of on-disk JSON Schema access for list-manager.

Every other script in this skill goes through get_schema() instead of
reading schemas/*.json directly -- this keeps schema storage swappable (e.g.
moved to cloud storage, fetched alongside the list it describes) later
without touching any call site, and keeps ref-resolution logic in one place.

Uniform interface: one call, one `field` argument.
  get_schema(schema_name, "*")      -> whole resolved entry schema
  get_schema(schema_name, "state")  -> just that field's spec

This module only *extracts* schema data. Anything that derives domain
meaning from it (which fields are required, which values an enum allows) is
the caller's job.
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    import jsonschema
    from jsonschema import FormatChecker
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False
    FormatChecker = None

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"

# Maps a list schema name to the type schema file that defines its entries.
# Falls back to entry.json (no state/enum fields) for anything not listed.
_ENTRY_TYPE_SCHEMA_BY_LIST_SCHEMA = {
    "todo": "action.json",
    "triage": "triage_action.json",
}


def list_schema_path(schema_name: str) -> Path:
    return SCHEMAS_DIR / "lists" / f"{schema_name}.json"


def list_schema_exists(schema_name: str) -> bool:
    return list_schema_path(schema_name).exists()


def validate_document(data: dict, schema_name: str) -> None:
    """Validate data against schema_name's list schema.

    Raises jsonschema.ValidationError on failure; callers decide how to
    report it (lists.py builds a rich id/title-annotated message, migrate_md
    just relays err.message). Raises RuntimeError if the jsonschema package
    isn't installed.
    """
    if not HAS_JSONSCHEMA:
        raise RuntimeError("jsonschema package is not installed")

    schema_path = list_schema_path(schema_name)
    with open(schema_path) as f:
        schema = json.load(f)
    resolver = jsonschema.RefResolver(
        base_uri=schema_path.resolve().as_uri(), referrer=schema
    )
    jsonschema.validate(data, schema, resolver=resolver, format_checker=FormatChecker())


def _resolve_type_schema(type_filename: str, _seen: set[str] | None = None) -> dict:
    """Recursively resolve a type schema's allOf/$ref chain into one flat dict
    with merged 'properties' and 'required'.
    """
    _seen = _seen if _seen is not None else set()
    if type_filename in _seen:
        return {"properties": {}, "required": []}
    _seen.add(type_filename)

    path = SCHEMAS_DIR / "types" / type_filename
    if not path.exists():
        return {"properties": {}, "required": []}
    with open(path) as f:
        raw = json.load(f)

    merged_properties: dict = {}
    merged_required: set[str] = set()
    for parent in raw.get("allOf", []):
        ref = parent.get("$ref")
        if ref:
            resolved_parent = _resolve_type_schema(ref, _seen)
            merged_properties.update(resolved_parent["properties"])
            merged_required |= set(resolved_parent["required"])

    merged_properties.update(raw.get("properties", {}))
    merged_required |= set(raw.get("required", []))
    return {"properties": merged_properties, "required": sorted(merged_required)}


def _describe_field(spec: dict) -> dict:
    """Replace a raw, unresolved `$ref` with a plain-English note.

    Only `children` is ever recursive (an entry's children are entries of the
    same type), so a full generic $ref resolver isn't worth the complexity --
    but leaving `{"$ref": "action.json"}` verbatim in output would break this
    module's whole point (callers never seeing raw JSON Schema).
    """
    items = spec.get("items")
    if isinstance(items, dict) and "$ref" in items:
        spec = dict(spec)
        spec["items"] = "same schema, recursively (entries can have child entries)"
    return spec


def get_schema(schema_name: str, field: str = "*") -> dict | None:
    """Uniform schema-extraction entry point.

    get_schema(schema_name, "*")      -> whole resolved entry-level schema:
                                          {"properties": {...}, "required": [...]}
    get_schema(schema_name, "state")  -> just that field's spec, e.g.
                                          {"enum": ["incomplete", "inprogress", "complete"]}
                                          (or None if the field is unconstrained/unknown)

    Resolves the type schema's allOf/$ref chain (e.g. action.json ->
    task_entry.json -> entry.json) so callers never deal with raw,
    unresolved JSON Schema.
    """
    type_filename = _ENTRY_TYPE_SCHEMA_BY_LIST_SCHEMA.get(schema_name, "entry.json")
    resolved = _resolve_type_schema(type_filename)
    if field == "*":
        resolved = dict(resolved)
        resolved["properties"] = {
            name: (_describe_field(spec) if isinstance(spec, dict) else spec)
            for name, spec in resolved["properties"].items()
        }
        return resolved
    spec = resolved["properties"].get(field)
    return _describe_field(spec) if isinstance(spec, dict) else spec
