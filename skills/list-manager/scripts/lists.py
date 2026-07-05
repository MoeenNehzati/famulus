#!/usr/bin/env python3
"""list-manager: pure local YAML file operator.

Subcommands:
  init          <file> --schema <name> [--name <list-name>]
  read          <file> [key=value | key~=value ...]
  create-entry  <file> <target> [--entries <file>]
  update        <file> [--file <file>]
  delete        <file> <id> [<id>...]
  gen-id        <file> [--count <n>]
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

import yaml

try:
    import jsonschema
    from jsonschema import FormatChecker
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False
    FormatChecker = None

if HAS_JSONSCHEMA:
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="jsonschema")

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"
IMMUTABLE_FIELDS = frozenset({"id", "created"})
HEX6_RE = re.compile(r"^[0-9a-f]{6}$")


# ── I/O helpers ──────────────────────────────────────────────────────────────

def normalize_dates(node) -> None:
    """Coerce any date/datetime values to ISO strings, in place, recursively.

    YAML parses an unquoted `deadline: 2026-07-05` into a datetime.date, which
    then fails the schema's `type: string, format: date`. Normalizing on load
    and before validation makes the store robust to writers that emit unquoted
    dates, without changing the schema.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, datetime.date):  # also matches datetime.datetime
                node[k] = v.isoformat()
            else:
                normalize_dates(v)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, datetime.date):
                node[i] = v.isoformat()
            else:
                normalize_dates(v)


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    normalize_dates(data)
    return data


def save_yaml(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ── Cloud transport ───────────────────────────────────────────────────────────

def run_cloud_dispatch(interface_id: str, remote_path: str, *, stdin: str | None = None) -> tuple[int, str, str]:
    try:
        from script_dispatcher import InvocationError, dispatch
    except ImportError:
        die(
            "script_dispatcher is not installed. Re-run install-assistant-tools to install the shared dispatcher package."
        )

    try:
        result = dispatch(
            caller_skill="list-manager",
            target_skill="cloud-files",
            script_interface=interface_id,
            args=[remote_path],
            stdin=stdin,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except InvocationError as exc:
        die(f"invalid dispatcher request for cloud-files:{interface_id}: {exc}")
    except subprocess.TimeoutExpired:
        die(f"{interface_id} timed out")
    except Exception as exc:
        die(f"{interface_id} failed: {exc}")

    return result.returncode, result.stdout, result.stderr


def download_list(list_name: str, dest_path: Path) -> None:
    """Download list from cloud storage via cloud-files lists-read interface."""
    remote_path = f"lists/{list_name}.yaml"
    returncode, stdout, stderr = run_cloud_dispatch("lists-read", remote_path)
    if returncode != 0:
        die(f"failed to download {remote_path}: {stderr}")
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(stdout)


def upload_list(list_name: str, src_path: Path) -> None:
    """Upload list to cloud storage via cloud-files lists-write interface."""
    remote_path = f"lists/{list_name}.yaml"
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()
    returncode, _stdout, stderr = run_cloud_dispatch("lists-write", remote_path, stdin=content)
    if returncode != 0:
        die(f"failed to upload {remote_path}: {stderr}")


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

def validate_entries_before_insert(entries: list, schema_name: str) -> None:
    """Check that each entry has all required fields before insertion.

    This prevents the mistake of inventing missing required fields. If any entry
    is missing a required field, fail loudly so the caller is forced to ask for
    the value instead of guessing.

    Auto-generated fields (id, created, state) are not required in the input.
    """
    if not HAS_JSONSCHEMA:
        return  # Skip if jsonschema not available; full validation will happen later

    # Determine which fields are required and which are auto-generated
    schema_path = SCHEMAS_DIR / "lists" / f"{schema_name}.json"
    if not schema_path.exists():
        return  # Schema unknown; let full validation handle it

    auto_generated = {"id", "created", "state"}

    # For todo/potential-actions, load the action schema to find required fields
    user_required = set()
    if schema_name in ("todo", "potential-actions"):
        action_schema_path = SCHEMAS_DIR / "types" / "action.json"
        if action_schema_path.exists():
            with open(action_schema_path) as f:
                action_schema = json.load(f)
            if "required" in action_schema:
                user_required = set(action_schema["required"]) - auto_generated

    # Check each entry for missing user-provided required fields
    for entry in entries:
        if not isinstance(entry, dict):
            continue  # Let full validation handle type errors

        missing = user_required - set(entry.keys())
        if missing:
            title = entry.get("title", "(no title)")
            die(
                f"entry '{title}' is missing required field(s): {', '.join(sorted(missing))}. "
                f"Provide {missing.pop() if len(missing) == 1 else 'these fields'} instead of inventing them."
            )


def validate_list(data: dict) -> None:
    """Validate data against its declared schema. Calls die() on failure."""
    # Patch inputs (create-entry/update) may carry date objects from YAML; coerce
    # them so what we validate matches what we save.
    normalize_dates(data)
    schema_name = data.get("schema")
    if not schema_name:
        die("list file missing 'schema' field")

    schema_path = SCHEMAS_DIR / "lists" / f"{schema_name}.json"
    if not schema_path.exists():
        die(f"unknown schema '{schema_name}' (no file at {schema_path})")

    if not HAS_JSONSCHEMA:
        print(
            "warning: jsonschema is not installed — schema validation skipped. "
            "Install it (`pip install jsonschema`) to validate entries before saving.",
            file=sys.stderr,
        )
        die("cannot write: jsonschema is required for mutating operations but is not installed")

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
        die(f"validation failed: {describe_validation_error(data, e)}")


def describe_validation_error(data: dict, err) -> str:
    """Turn a jsonschema error into an actionable message: the specific problem,
    the location, and the offending entry's id/title when there is one."""
    path = list(err.absolute_path)
    # Walk the document along the error path, remembering the nearest enclosing
    # entry (a dict with id + title) so we can name the row that is wrong.
    node, entry = data, None
    for key in path:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            break
        if isinstance(node, dict) and "id" in node and "title" in node:
            entry = node
    loc = "/".join(str(p) for p in path) or "(document root)"
    where = f"\n  at: {loc}"
    who = ""
    if entry is not None:
        who = f"\n  entry: id={entry.get('id')} title={entry.get('title')!r}"
    # `err.message` already names the field for required/type/format failures.
    return f"{err.message}{where}{who}"


# ── Filter helpers (for `read`) ───────────────────────────────────────────────

def parse_filters(filter_args: list[str]) -> list[tuple[str, str, str]]:
    """Parse filter strings into (key, op, value) tuples.

    Supported ops:
      key=value    exact match (comma-separated = OR)
      key~=value   regex search on the field (case-insensitive; substring is a
                   plain-text regex, so old substring filters keep working)
    """
    filters = []
    for f in filter_args:
        m = re.match(r"^([^~=]+)(~=|=)(.+)$", f)
        if not m:
            die(f"invalid filter '{f}': expected key=value or key~=value")
        filters.append((m.group(1), m.group(2), m.group(3)))
    return filters


def entry_matches(entry: dict, filters: list[tuple[str, str, str]]) -> bool:
    """Return True if entry satisfies all filters (AND across keys, OR within a key)."""
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
                # Regex search; fall back to literal substring on a bad pattern
                # so filters containing regex metacharacters never crash.
                try:
                    if re.search(val, field_val, re.IGNORECASE):
                        matched_any = True
                        break
                except re.error:
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

    def emit(content: str) -> None:
        if getattr(args, "output", None):
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(content)
        else:
            print(content, end="")

    if not args.filters:
        emit(yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False))
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

    emit(yaml.dump(matches, allow_unicode=True, default_flow_style=False, sort_keys=False))


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

    # Validate required fields before adding to list. This fails fast and forces
    # the caller to ask for missing values instead of inventing them.
    schema_name = data.get("schema")
    validate_entries_before_insert(new_entries, schema_name)

    existing_ids = collect_ids(data)
    today = datetime.date.today().isoformat()
    for entry in new_entries:
        if "id" not in entry:
            new_id = gen_ids(existing_ids, 1)[0]
            entry["id"] = new_id
            existing_ids.add(new_id)
        # Default state and created so callers (e.g. email-triage) don't need
        # to supply them; these are only required by todo/potential-actions schemas
        # but are harmless on others.
        if "state" not in entry:
            entry["state"] = "incomplete"
        if "created" not in entry:
            entry["created"] = today

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


# ── Deletion helpers ─────────────────────────────────────────────────────────

def remove_entries_by_ids(node, ids_to_remove: set[str]) -> None:
    """Remove entries with the given IDs from the tree, in place.

    Operates on any list-bearing node: top-level category entries AND nested
    children lists. Removing a parent removes the whole subtree naturally
    (the node is never visited after removal).
    """
    if isinstance(node, dict):
        for val in node.values():
            if isinstance(val, list):
                # Filter out matching entries at this level
                val[:] = [
                    item for item in val
                    if not (isinstance(item, dict) and item.get("id") in ids_to_remove)
                ]
                # Recurse into survivors
                for item in val:
                    remove_entries_by_ids(item, ids_to_remove)
            else:
                remove_entries_by_ids(val, ids_to_remove)
    elif isinstance(node, list):
        node[:] = [
            item for item in node
            if not (isinstance(item, dict) and item.get("id") in ids_to_remove)
        ]
        for item in node:
            remove_entries_by_ids(item, ids_to_remove)


def cmd_delete(args: argparse.Namespace) -> None:
    file = Path(args.file)
    data = load_yaml(file)

    ids_to_delete = set(args.ids)

    # Detect missing ids before touching data
    all_ids = collect_ids(data)
    missing = ids_to_delete - all_ids
    if missing:
        for mid in sorted(missing):
            print(f"error: id '{mid}' not found", file=sys.stderr)
        sys.exit(1)

    remove_entries_by_ids(data, ids_to_delete)
    validate_list(data)
    save_yaml(file, data)

    for id_ in sorted(ids_to_delete):
        print(f"deleted: {id_}")


# ── Argument parsing + dispatch ───────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lists.py")
    sub = parser.add_subparsers(dest="command", required=True)

    # Helper to add --cloud to any subcommand. When set, the source positional
    # is treated as a cloud list NAME (download → operate → upload) instead of a
    # local file PATH. It is a plain boolean, so it never consumes a positional
    # and filters keep their own slot.
    def add_cloud_arg(subparser):
        subparser.add_argument(
            "--cloud",
            action="store_true",
            help="Treat the source as a cloud list name; download, operate, and upload",
        )

    p_init = sub.add_parser("init", help="Create a new empty list file")
    p_init.add_argument("file", help="Path to create, or cloud list name with --cloud")
    p_init.add_argument("--schema", required=True, help="Schema name (todo, potential-actions, default)")
    p_init.add_argument("--name", help="List name (defaults to filename stem)")
    add_cloud_arg(p_init)

    p_read = sub.add_parser("read", help="Read list, optionally filtered")
    p_read.add_argument("file", help="Path to list YAML, or cloud list name with --cloud")
    p_read.add_argument("filters", nargs="*", help="key=value (exact/OR) or key~=value (regex) filters")
    p_read.add_argument("--sort", metavar="FIELD", help="Sort results by field (e.g., deadline, created). Dates sorted ascending (earliest first)")
    p_read.add_argument("-o", "--output", metavar="FILE", help="Write output to file instead of stdout")
    add_cloud_arg(p_read)

    p_create = sub.add_parser("create-entry", help="Add entries to a category or entry")
    p_create.add_argument("file", help="Path to list YAML, or cloud list name with --cloud")
    p_create.add_argument("target", help="Category path (Work/Writing) or 6-char entry ID")
    p_create.add_argument("--entries", dest="entries", help="YAML file of entries (default: stdin)")
    add_cloud_arg(p_create)

    p_update = sub.add_parser("update", help="Update fields on entries")
    p_update.add_argument("file", help="Path to list YAML, or cloud list name with --cloud")
    p_update.add_argument("--file", dest="file_input", help="YAML file of updates (default: stdin)")
    add_cloud_arg(p_update)

    p_genid = sub.add_parser("gen-id", help="Generate collision-free IDs")
    p_genid.add_argument("file", help="Path to list YAML, or cloud list name with --cloud")
    p_genid.add_argument("--count", type=int, default=1, help="Number of IDs to generate")
    add_cloud_arg(p_genid)

    p_delete = sub.add_parser("delete", help="Delete entries by ID (removes whole subtree)")
    p_delete.add_argument("file", help="Path to list YAML, or cloud list name with --cloud")
    p_delete.add_argument("ids", nargs="+", help="One or more 6-char entry IDs to delete")
    add_cloud_arg(p_delete)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "init": cmd_init,
        "read": cmd_read,
        "create-entry": cmd_create_entry,
        "update": cmd_update,
        "delete": cmd_delete,
        "gen-id": cmd_gen_id,
    }

    # Cloud mode: the source positional is a list NAME. For reads we download →
    # operate; for mutations we download → operate → upload; for init we create
    # → upload (nothing to download). Local mode operates on the file in place.
    if getattr(args, "cloud", False):
        list_name = args.file
        mutating = args.command in ("init", "create-entry", "update", "delete")
        tmp_dir = Path(tempfile.mkdtemp())
        temp_path = tmp_dir / f"{list_name}.yaml"
        try:
            if args.command == "init":
                # New list: nothing to download; default display name to the
                # cloud list name unless the caller set one explicitly.
                if not getattr(args, "name", None):
                    args.name = list_name
            else:
                download_list(list_name, temp_path)
            args.file = str(temp_path)
            dispatch[args.command](args)
            if mutating:
                upload_list(list_name, temp_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        dispatch[args.command](args)


if __name__ == "__main__":
    main()
