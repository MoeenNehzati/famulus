#!/usr/bin/env python3
"""list-manager: pure local YAML file operator.

Subcommands:
  init          <file> --schema <name> [--name <list-name>]
  read          <file> [key=value | key~=value ...]
  create-entry  <file> <target> [--entries <file>]
  update        <file> [--file <file>]
  gen-id        <file> [--count <n>]
"""

import argparse
import json
import os
import re
import sys
import warnings
from pathlib import Path

import yaml
import jsonschema
from jsonschema import FormatChecker

warnings.filterwarnings("ignore", category=DeprecationWarning, module="jsonschema")

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"
IMMUTABLE_FIELDS = frozenset({"id", "created"})
HEX6_RE = re.compile(r"^[0-9a-f]{6}$")


# ── I/O helpers ──────────────────────────────────────────────────────────────

def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ── ID generation ─────────────────────────────────────────────────────────────

def collect_ids(node) -> set[str]:
    """Recursively collect all entry IDs from a list document."""
    ids: set[str] = set()
    if isinstance(node, dict):
        if "id" in node:
            ids.add(node["id"])
        for v in node.values():
            ids |= collect_ids(v)
    elif isinstance(node, list):
        for item in node:
            ids |= collect_ids(item)
    return ids


def gen_ids(existing_ids: set[str], count: int = 1) -> list[str]:
    """Return `count` collision-free 6-char lowercase hex IDs."""
    ids: list[str] = []
    while len(ids) < count:
        candidate = os.urandom(3).hex()
        if candidate not in existing_ids and candidate not in ids:
            ids.append(candidate)
    return ids


# ── Validation ───────────────────────────────────────────────────────────────

def validate_list(data: dict) -> None:
    """Validate data against its declared schema. Calls die() on failure."""
    schema_name = data.get("schema")
    if not schema_name:
        die("list file missing 'schema' field")

    schema_path = SCHEMAS_DIR / "lists" / f"{schema_name}.json"
    if not schema_path.exists():
        die(f"unknown schema '{schema_name}' (no file at {schema_path})")

    with open(schema_path) as f:
        schema = json.load(f)

    resolver = jsonschema.RefResolver(
        base_uri=schema_path.resolve().as_uri(), referrer=schema
    )

    try:
        jsonschema.validate(
            data, schema, resolver=resolver, format_checker=FormatChecker()
        )
    except jsonschema.ValidationError as e:
        die(f"validation failed: {e.message}")


# ── Filter helpers (for `read`) ───────────────────────────────────────────────

def parse_filters(filter_args: list[str]) -> list[tuple[str, str, str]]:
    """Parse filter strings into (key, op, value) tuples.

    Supported ops:
      key=value    exact match (comma-separated = OR)
      key~=value   substring match
    """
    filters = []
    for f in filter_args:
        m = re.match(r"^([^~=]+)(~=|=)(.+)$", f)
        if not m:
            die(f"invalid filter '{f}': expected key=value or key~=value")
        filters.append((m.group(1), m.group(2), m.group(3)))
    return filters


def entry_matches(entry: dict, filters: list[tuple[str, str, str]]) -> bool:
    """Return True if entry satisfies all filters (AND semantics across keys)."""
    from collections import defaultdict
    by_key: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, op, val in filters:
        by_key[key].append((op, val))

    for key, conditions in by_key.items():
        field_val = str(entry.get(key, ""))
        matched_any = False
        for op, val in conditions:
            if op == "=":
                if field_val in [v.strip() for v in val.split(",")]:
                    matched_any = True
                    break
            elif op == "~=":
                if val in field_val:
                    matched_any = True
                    break
        if not matched_any:
            return False
    return True


def collect_matching_entries(node, filters: list[tuple[str, str, str]]) -> list[dict]:
    """Walk the document tree; return flat list of entries matching all filters."""
    results: list[dict] = []
    if isinstance(node, dict):
        if "id" in node and "title" in node:
            if entry_matches(node, filters):
                results.append(node)
            for child in node.get("children", []):
                results.extend(collect_matching_entries(child, filters))
        else:
            for v in node.values():
                results.extend(collect_matching_entries(v, filters))
    elif isinstance(node, list):
        for item in node:
            results.extend(collect_matching_entries(item, filters))
    return results


# ── Category / entry lookup helpers ──────────────────────────────────────────

def find_category_by_path(categories: list[dict], path_parts: list[str]) -> dict | None:
    """Navigate nested categories by name path. Returns the category dict or None."""
    if not path_parts:
        return None
    name = path_parts[0]
    for cat in categories:
        if cat.get("name") == name:
            if len(path_parts) == 1:
                return cat
            return find_category_by_path(cat.get("categories", []), path_parts[1:])
    return None


def all_category_paths(categories: list[dict], prefix: str = "") -> list[str]:
    """Return all category paths for error messages."""
    paths = []
    for cat in categories:
        path = f"{prefix}/{cat['name']}" if prefix else cat["name"]
        paths.append(path)
        paths.extend(all_category_paths(cat.get("categories", []), path))
    return paths


def find_entry_by_id(node, target_id: str) -> dict | None:
    """Recursively find an entry by ID."""
    if isinstance(node, dict):
        if node.get("id") == target_id:
            return node
        for v in node.values():
            result = find_entry_by_id(v, target_id)
            if result is not None:
                return result
    elif isinstance(node, list):
        for item in node:
            result = find_entry_by_id(item, target_id)
            if result is not None:
                return result
    return None


# ── Subcommands ───────────────────────────────────────────────────────────────

def cmd_init(args: argparse.Namespace) -> None:
    file = Path(args.file)
    if file.exists():
        die(f"file already exists: {file}")

    name = args.name if hasattr(args, "name") and args.name else file.stem
    data: dict = {
        "schema": args.schema,
        "name": name,
        "categories": [],
    }

    validate_list(data)
    save_yaml(file, data)
    print(f"created {file}")


def cmd_gen_id(args: argparse.Namespace) -> None:
    file = Path(args.file)
    data = load_yaml(file)
    existing = collect_ids(data)
    ids = gen_ids(existing, args.count)
    for id_ in ids:
        print(id_)


def cmd_read(args: argparse.Namespace) -> None:
    file = Path(args.file)
    data = load_yaml(file)

    if not args.filters:
        print(yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False), end="")
        return

    filters = parse_filters(args.filters)
    matches = collect_matching_entries(data, filters)

    # Sort if requested
    if hasattr(args, 'sort') and args.sort:
        sort_field = args.sort
        # Try to sort by the field, treating dates as dates
        try:
            matches.sort(key=lambda e: (
                # Missing values sort last
                float('inf') if sort_field not in e else (
                    # Treat YYYY-MM-DD as dates (earlier dates first)
                    e[sort_field] if not isinstance(e[sort_field], str) or len(e[sort_field]) < 10
                    else (e[sort_field], 0)  # Sort dates lexicographically (safe for YYYY-MM-DD)
                )
            ))
        except (TypeError, ValueError) as ex:
            die(f"sort by '{sort_field}' failed: {ex}")

    print(yaml.dump(matches, allow_unicode=True, default_flow_style=False, sort_keys=False), end="")


def cmd_create_entry(args: argparse.Namespace) -> None:
    file = Path(args.file)
    data = load_yaml(file)
    target = args.target

    if HEX6_RE.match(target):
        parent_entry = find_entry_by_id(data, target)
        if parent_entry is None:
            die(f"no entry with id '{target}' found in {file}")
        dest_list = parent_entry.setdefault("children", [])
    else:
        parts = [p.strip() for p in target.split("/") if p.strip()]
        category = find_category_by_path(data.get("categories", []), parts)
        if category is None:
            available = all_category_paths(data.get("categories", []))
            die(
                f"category '{target}' not found. Available: "
                + (", ".join(available) if available else "(none)")
            )
        dest_list = category.setdefault("entries", [])

    if args.entries:
        with open(args.entries, encoding="utf-8") as f:
            new_entries = yaml.safe_load(f)
    else:
        new_entries = yaml.safe_load(sys.stdin.read())

    if not isinstance(new_entries, list):
        die("entries input must be a YAML list")

    existing_ids = collect_ids(data)
    for entry in new_entries:
        if "id" not in entry:
            new_id = gen_ids(existing_ids, 1)[0]
            entry["id"] = new_id
            existing_ids.add(new_id)

    dest_list.extend(new_entries)
    validate_list(data)
    save_yaml(file, data)


def cmd_update(args: argparse.Namespace) -> None:
    file = Path(args.file)
    data = load_yaml(file)

    if args.file_input:
        with open(args.file_input, encoding="utf-8") as f:
            updates = yaml.safe_load(f)
    else:
        updates = yaml.safe_load(sys.stdin.read())

    if not isinstance(updates, list):
        die("update input must be a YAML list")

    for patch in updates:
        if "id" not in patch:
            die("each update must have an 'id' field")

        bad = IMMUTABLE_FIELDS & set(patch.keys()) - {"id"}
        if bad:
            die(f"cannot update immutable field(s): {', '.join(sorted(bad))}")

        target_id = patch["id"]
        entry = find_entry_by_id(data, target_id)
        if entry is None:
            die(f"no entry with id '{target_id}' found in {file}")

        for k, v in patch.items():
            if k == "id":
                continue
            entry[k] = v

    validate_list(data)
    save_yaml(file, data)


# ── Argument parsing + dispatch ───────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lists.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create a new empty list file")
    p_init.add_argument("file", help="Path to create")
    p_init.add_argument("--schema", required=True, help="Schema name (todo, potential-actions, default)")
    p_init.add_argument("--name", help="List name (defaults to filename stem)")

    p_read = sub.add_parser("read", help="Read list, optionally filtered")
    p_read.add_argument("file", help="Path to list YAML")
    p_read.add_argument("filters", nargs="*", help="key=value or key~=value filters")
    p_read.add_argument("--sort", metavar="FIELD", help="Sort results by field (e.g., deadline, created). Dates sorted ascending (earliest first)")

    p_create = sub.add_parser("create-entry", help="Add entries to a category or entry")
    p_create.add_argument("file", help="Path to list YAML")
    p_create.add_argument("target", help="Category path (Work/Writing) or 6-char entry ID")
    p_create.add_argument("--entries", dest="entries", help="YAML file of entries (default: stdin)")

    p_update = sub.add_parser("update", help="Update fields on entries")
    p_update.add_argument("file", help="Path to list YAML")
    p_update.add_argument("--file", dest="file_input", help="YAML file of updates (default: stdin)")

    p_genid = sub.add_parser("gen-id", help="Generate collision-free IDs")
    p_genid.add_argument("file", help="Path to list YAML")
    p_genid.add_argument("--count", type=int, default=1, help="Number of IDs to generate")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "init": cmd_init,
        "read": cmd_read,
        "create-entry": cmd_create_entry,
        "update": cmd_update,
        "gen-id": cmd_gen_id,
    }

    fn = dispatch[args.command]
    fn(args)


if __name__ == "__main__":
    main()
