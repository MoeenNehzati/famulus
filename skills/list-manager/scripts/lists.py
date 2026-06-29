#!/usr/bin/env python3
"""list-manager: pure local YAML file operator.

Subcommands:
  init          <file> --schema <name> [--name <list-name>]
  read          <file> [key=value | key~=value ...]
  create-entry  <file> <target> [--entries <file>]
  update        <file> [--file <file>]
  gen-id        <file> [--count <n>]
  migrate-md    <src.md> <dst.yaml> --schema <name> [--name <list-name>]
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
from jsonschema import Draft7Validator, FormatChecker

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


# ── Filter helpers (for `read`) ────────────────────────────────────────────────

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
    # Group by key for OR semantics within same key
    from collections import defaultdict
    by_key: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, op, val in filters:
        by_key[key].append((op, val))

    for key, conditions in by_key.items():
        field_val = str(entry.get(key, ""))
        # At least one condition for this key must match (OR)
        matched_any = False
        for op, val in conditions:
            if op == "=":
                # comma-separated OR values
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
        # Is this an entry (has 'id' and 'title')?
        if "id" in node and "title" in node:
            if entry_matches(node, filters):
                results.append(node)
            # Recurse into children
            for child in node.get("children", []):
                results.extend(collect_matching_entries(child, filters))
        else:
            # Category or document root — recurse
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

    validate_list(data)  # ensures schema name is valid before writing
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
        # Unfiltered: print full document
        print(yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False), end="")
        return

    filters = parse_filters(args.filters)
    matches = collect_matching_entries(data, filters)
    print(yaml.dump(matches, allow_unicode=True, default_flow_style=False, sort_keys=False), end="")


def cmd_create_entry(args: argparse.Namespace) -> None:
    file = Path(args.file)
    data = load_yaml(file)
    target = args.target

    # Determine where to add entries
    if HEX6_RE.match(target):
        # Target is an entry ID — add as children
        parent_entry = find_entry_by_id(data, target)
        if parent_entry is None:
            die(f"no entry with id '{target}' found in {file}")
        dest_list = parent_entry.setdefault("children", [])
    else:
        # Target is a category path
        parts = [p.strip() for p in target.split("/") if p.strip()]
        category = find_category_by_path(data.get("categories", []), parts)
        if category is None:
            available = all_category_paths(data.get("categories", []))
            die(
                f"category '{target}' not found. Available: "
                + (", ".join(available) if available else "(none)")
            )
        dest_list = category.setdefault("entries", [])

    # Load new entries
    if args.entries:
        with open(args.entries, encoding="utf-8") as f:
            new_entries = yaml.safe_load(f)
    else:
        new_entries = yaml.safe_load(sys.stdin.read())

    if not isinstance(new_entries, list):
        die("entries input must be a YAML list")

    # Assign IDs and validate
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

        # Check for immutable field violations
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


def cmd_migrate_md(args: argparse.Namespace) -> None:
    try:
        import dateparser
    except ImportError:
        die("dateparser is required for migrate-md: pip install dateparser")

    src = Path(args.src)
    dst = Path(args.dst)
    schema_name = args.schema
    name = args.name if hasattr(args, "name") and args.name else dst.stem

    if not src.exists():
        die(f"source file not found: {src}")
    if dst.exists():
        die(f"destination file already exists: {dst}")

    text = src.read_text(encoding="utf-8")
    lines = text.splitlines()

    categories: list[dict] = []
    # Stack of (indent_level, category_dict)
    cat_stack: list[tuple[int, dict]] = []
    # Current leaf entries list
    current_entries: list[dict] = []
    # Current category at each level
    current_cat: dict | None = None

    import datetime
    today = datetime.date.today().isoformat()

    def parse_deadline(raw: str) -> str | None:
        """Parse free-form deadline string to YYYY-MM-DD or return None."""
        raw = raw.strip()
        # Already ISO format
        if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
            return raw
        parsed = dateparser.parse(raw, settings={"PREFER_DATES_FROM": "future"})
        if parsed:
            return parsed.date().isoformat()
        return None

    unresolvable: list[str] = []
    existing_ids: set[str] = set()

    def fresh_id() -> str:
        nid = gen_ids(existing_ids, 1)[0]
        existing_ids.add(nid)
        return nid

    # Simple markdown parser: headings = categories, list items = entries
    # Supports `- [ ]` (incomplete) and `- [x]` (done)
    # Deadline parsed from `(due: ...)` or `(deadline: ...)`
    current_level_cats: list[list[dict]] = [categories]  # index = heading level - 1

    active_cats: list[dict | None] = [None, None, None, None, None, None]  # by heading level

    for line in lines:
        # Heading?
        heading_m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_m:
            level = len(heading_m.group(1))
            cat_name = heading_m.group(2).strip()
            cat = {"name": cat_name}
            # Find parent category
            if level == 1:
                categories.append(cat)
                active_cats[0] = cat
                for i in range(1, 6):
                    active_cats[i] = None
            else:
                parent_level = level - 2  # 0-indexed
                parent = active_cats[parent_level] if parent_level >= 0 else None
                if parent is not None:
                    parent.setdefault("categories", []).append(cat)
                else:
                    # Fall back to top level
                    categories.append(cat)
                active_cats[level - 1] = cat
                for i in range(level, 6):
                    active_cats[i] = None
            continue

        # List item?
        item_m = re.match(r"^(\s*)-\s+\[([xX ]?)\]\s+(.+)$", line)
        if item_m:
            indent = len(item_m.group(1))
            checked = item_m.group(2).lower() == "x"
            raw_title = item_m.group(3).strip()

            # Extract deadline from title: (due: ...) or (deadline: ...)
            deadline = None
            title = raw_title
            due_m = re.search(r"\((?:due|deadline):\s*([^)]+)\)", raw_title, re.IGNORECASE)
            if due_m:
                deadline_raw = due_m.group(1).strip()
                deadline = parse_deadline(deadline_raw)
                if deadline is None:
                    unresolvable.append(f"'{deadline_raw}' in: {raw_title}")
                title = raw_title[:due_m.start()].strip() + raw_title[due_m.end():].strip()
                title = title.strip()

            state = "done" if checked else "incomplete"

            entry: dict = {
                "id": fresh_id(),
                "title": title,
                "created": today,
                "state": state,
            }
            if deadline:
                entry["deadline"] = deadline
            elif schema_name in ("todo", "potential-actions"):
                # Required field — flag but still include placeholder
                entry["deadline"] = today  # placeholder
                unresolvable.append(f"no deadline found for: {raw_title}")

            # Find the right category to put this entry under
            # The last active category at any level receives the entry
            target_cat = None
            for i in range(5, -1, -1):
                if active_cats[i] is not None:
                    target_cat = active_cats[i]
                    break
            if target_cat is not None:
                target_cat.setdefault("entries", []).append(entry)
            else:
                # No category — create a default one
                default_cat = {"name": "General", "entries": [entry]}
                categories.append(default_cat)
                active_cats[0] = default_cat

    if unresolvable:
        print("warning: unresolvable deadlines:", file=sys.stderr)
        for item in unresolvable:
            print(f"  - {item}", file=sys.stderr)
        # Still proceed — caller can decide whether to use the output

    doc = {"schema": schema_name, "name": name, "categories": categories}
    validate_list(doc)
    save_yaml(dst, doc)
    print(f"migrated {src} → {dst}")


# ── Argument parsing + dispatch ───────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lists.py")
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="Create a new empty list file")
    p_init.add_argument("file", help="Path to create")
    p_init.add_argument("--schema", required=True, help="Schema name (todo, potential-actions, default)")
    p_init.add_argument("--name", help="List name (defaults to filename stem)")

    # read
    p_read = sub.add_parser("read", help="Read list, optionally filtered")
    p_read.add_argument("file", help="Path to list YAML")
    p_read.add_argument("filters", nargs="*", help="key=value or key~=value filters")

    # create-entry
    p_create = sub.add_parser("create-entry", help="Add entries to a category or entry")
    p_create.add_argument("file", help="Path to list YAML")
    p_create.add_argument("target", help="Category path (Work/Writing) or 6-char entry ID")
    p_create.add_argument("--entries", dest="entries", help="YAML file of entries (default: stdin)")

    # update
    p_update = sub.add_parser("update", help="Update fields on entries")
    p_update.add_argument("file", help="Path to list YAML")
    p_update.add_argument("--file", dest="file_input", help="YAML file of updates (default: stdin)")

    # gen-id
    p_genid = sub.add_parser("gen-id", help="Generate collision-free IDs")
    p_genid.add_argument("file", help="Path to list YAML")
    p_genid.add_argument("--count", type=int, default=1, help="Number of IDs to generate")

    # migrate-md
    p_migrate = sub.add_parser("migrate-md", help="Migrate Markdown list to YAML")
    p_migrate.add_argument("src", help="Source Markdown file")
    p_migrate.add_argument("dst", help="Destination YAML file")
    p_migrate.add_argument("--schema", required=True, help="Target schema name")
    p_migrate.add_argument("--name", help="List name (defaults to dst stem)")

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
        "migrate-md": cmd_migrate_md,
    }

    fn = dispatch[args.command]
    fn(args)


if __name__ == "__main__":
    main()
