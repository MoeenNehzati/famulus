#!/usr/bin/env python3
"""One-time migration tool: Markdown checklist → YAML list.

Separate from lists.py because migration is a one-time operation that
requires dateparser and handles legacy state markers ([+], [-]) not
present in the production format.

Handles:
  - Indented child entries (deeper checkbox indent → children[])
  - Deadline continuation lines: `      deadline: this week`
  - Description continuation lines: any other non-blank text after an entry

Usage:
    python3 migrate_md.py <src.md> <dst.yaml> --schema <name> [--name <list-name>]
"""

import argparse
import datetime
import os
import re
import sys
import warnings
from pathlib import Path

import yaml
import jsonschema

import get_schema

warnings.filterwarnings("ignore", category=DeprecationWarning, module="jsonschema")

today = datetime.date.today().isoformat()


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def validate_list(data: dict) -> None:
    schema_name = data.get("schema")
    if not get_schema.list_schema_exists(schema_name):
        die(f"unknown schema '{schema_name}'")
    try:
        get_schema.validate_document(data, schema_name)
    except jsonschema.ValidationError as e:
        die(f"validation failed: {e.message}")


def gen_id(existing: set[str]) -> str:
    while True:
        candidate = os.urandom(3).hex()
        if candidate not in existing:
            existing.add(candidate)
            return candidate


def parse_deadline(raw: str, relative_to: str | None = None) -> str | None:
    """Parse a deadline string. relative_to is an ISO date used as the base for
    natural-language expressions like 'this week' or 'next monday'."""
    raw = raw.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    # MM/DD/YY (old creation date format)
    m = re.match(r"^(\d{2})/(\d{2})/(\d{2})$", raw)
    if m:
        month, day, year = m.groups()
        return f"20{year}-{month}-{day}"
    try:
        import dateparser
        base = datetime.datetime.fromisoformat(relative_to) if relative_to else datetime.datetime.today()
        parsed = dateparser.parse(raw, settings={
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": base,
        })
        if parsed:
            return parsed.date().isoformat()
    except ImportError:
        pass
    return None


def map_state(marker: str, schema: str) -> str:
    """Map a Markdown checkbox marker to the appropriate schema state."""
    if schema == "potential-actions":
        return {"x": "accepted", "X": "accepted", "+": "accepted",
                "-": "rejected"}.get(marker, "undecided")
    else:
        return "complete" if marker.lower() == "x" else "incomplete"


def parse_entry_line(line: str, schema_name: str, existing_ids: set[str]) -> tuple[int, dict] | None:
    """Parse a checkbox line. Returns (indent, entry_dict) or None."""
    item_m = re.match(r"^(\s*)-\s+\[([xX+\- ]?)\]\s+(.+)$", line)
    if not item_m:
        return None

    indent = len(item_m.group(1))
    marker = item_m.group(2)
    raw_title = item_m.group(3).strip()

    # Strip HTML comment ID: <!-- #xxxx -->
    raw_title = re.sub(r"\s*<!--\s*#[0-9a-f]+\s*-->\s*$", "", raw_title)

    # Extract creation date: (MM/DD/YY) title
    created_m = re.match(r"^\((\d{2}/\d{2}/\d{2})\)\s+(.+)$", raw_title)
    if created_m:
        created = parse_deadline(created_m.group(1)) or today
        raw_title = created_m.group(2).strip()
    else:
        created = today

    # Extract inline deadline tag: (due: ...) or (deadline: ...)
    deadline = None
    due_m = re.search(r"\((?:due|deadline):\s*([^)]+)\)", raw_title, re.IGNORECASE)
    if due_m:
        deadline = parse_deadline(due_m.group(1).strip(), relative_to=created)
        raw_title = (raw_title[:due_m.start()] + raw_title[due_m.end():]).strip()

    state = map_state(marker, schema_name)
    entry: dict = {
        "id": gen_id(existing_ids),
        "title": raw_title,
        "created": created,
        "state": state,
    }
    if deadline:
        entry["deadline"] = deadline
    # deadline may be filled in later from a continuation line

    return indent, entry


def migrate(src: Path, dst: Path, schema_name: str, name: str) -> None:
    try:
        import dateparser  # noqa: F401
    except ImportError:
        die("dateparser is required: pip install dateparser")

    if not src.exists():
        die(f"source file not found: {src}")
    if dst.exists():
        die(f"destination file already exists: {dst}")

    lines = src.read_text(encoding="utf-8").splitlines()
    categories: list[dict] = []
    active_cats: list[dict | None] = [None] * 6
    existing_ids: set[str] = set()
    no_deadline: list[str] = []

    # Stack of (indent, entry_dict) for tracking nesting and continuations.
    # Reset whenever we enter a new category.
    entry_stack: list[tuple[int, dict]] = []

    def reset_entry_stack() -> None:
        entry_stack.clear()

    def current_category() -> dict | None:
        for i in range(5, -1, -1):
            if active_cats[i] is not None:
                return active_cats[i]
        return None

    for line in lines:
        # Skip file header lines (old format: [listname] [state] meaning · ...)
        if line.startswith("[") and "]" in line:
            continue

        # Heading → category
        heading_m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_m:
            level = len(heading_m.group(1))
            cat = {"name": heading_m.group(2).strip()}
            if level == 1:
                categories.append(cat)
                active_cats[0] = cat
                for i in range(1, 6):
                    active_cats[i] = None
            else:
                parent = active_cats[level - 2] if level >= 2 else None
                if parent is not None:
                    parent.setdefault("categories", []).append(cat)
                else:
                    categories.append(cat)
                active_cats[level - 1] = cat
                for i in range(level, 6):
                    active_cats[i] = None
            reset_entry_stack()
            continue

        # Persistent title lines (no checkbox): structural category headers
        title_only_m = re.match(r"^(\s*)-\s+(?!\[)(.+)$", line)
        if title_only_m:
            indent = len(title_only_m.group(1))
            cat_name = title_only_m.group(2).strip()
            cat = {"name": cat_name}
            level = indent // 2 + 1
            parent = None
            for i in range(min(level - 1, 5), -1, -1):
                if active_cats[i] is not None:
                    parent = active_cats[i]
                    break
            if parent is not None:
                parent.setdefault("categories", []).append(cat)
            else:
                categories.append(cat)
            active_cats[min(level, 5)] = cat
            reset_entry_stack()
            continue

        # Checkbox item: may be a top-level entry or a child of a previous entry
        parsed = parse_entry_line(line, schema_name, existing_ids)
        if parsed is not None:
            indent, entry = parsed

            # Pop entries from stack that are at same or deeper indent
            while entry_stack and entry_stack[-1][0] >= indent:
                entry_stack.pop()

            if entry_stack:
                # Child of the entry at the top of the stack
                parent_entry = entry_stack[-1][1]
                parent_entry.setdefault("children", []).append(entry)
            else:
                # Top-level entry in current category
                cat = current_category()
                if cat is not None:
                    cat.setdefault("entries", []).append(entry)
                else:
                    fallback: dict = {"name": "General", "entries": [entry]}
                    categories.append(fallback)
                    active_cats[0] = fallback

            entry_stack.append((indent, entry))
            continue

        # Continuation lines: deadline or description text after an entry
        cont_m = re.match(r"^(\s+)(\S.*)$", line)
        if cont_m and entry_stack:
            cont_indent = len(cont_m.group(1))
            content = cont_m.group(2).strip()

            # Find the entry this continuation belongs to (last entry with less indent)
            target_entry = None
            for ei, ed in reversed(entry_stack):
                if ei < cont_indent:
                    target_entry = ed
                    break

            if target_entry is not None:
                dl_m = re.match(r"^deadline:\s*(.+)$", content, re.IGNORECASE)
                if dl_m:
                    dl = parse_deadline(dl_m.group(1).strip(),
                                        relative_to=target_entry.get("created"))
                    if dl and "deadline" not in target_entry:
                        target_entry["deadline"] = dl
                else:
                    # Append to description (multiple lines joined with space)
                    existing = target_entry.get("description", "")
                    target_entry["description"] = (existing + " " + content).strip() if existing else content

    # Assign deadline placeholders for entries that still have none
    def assign_missing_deadlines(node) -> None:
        if isinstance(node, dict):
            if "title" in node and "state" in node and "deadline" not in node:
                if schema_name in ("todo", "potential-actions"):
                    node["deadline"] = node.get("created", today)
                    no_deadline.append(node["title"])
            for v in node.values():
                assign_missing_deadlines(v)
        elif isinstance(node, list):
            for item in node:
                assign_missing_deadlines(item)

    doc = {"schema": schema_name, "name": name, "categories": categories}
    assign_missing_deadlines(doc)

    if no_deadline:
        print(f"warning: {len(no_deadline)} entries have no deadline (used created date):",
              file=sys.stderr)
        for t in no_deadline[:5]:
            print(f"  - {t}", file=sys.stderr)
        if len(no_deadline) > 5:
            print(f"  ... and {len(no_deadline) - 5} more", file=sys.stderr)

    validate_list(doc)
    dst.write_text(
        yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    print(f"migrated {src} → {dst}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="migrate_md.py",
                                     description="Migrate Markdown list to YAML")
    parser.add_argument("src", help="Source Markdown file")
    parser.add_argument("dst", help="Destination YAML file")
    parser.add_argument("--schema", required=True,
                        help="Target schema (todo, potential-actions, default)")
    parser.add_argument("--name", help="List name (defaults to dst stem)")
    args = parser.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)
    name = args.name or dst.stem
    migrate(src, dst, args.schema, name)


if __name__ == "__main__":
    main()
