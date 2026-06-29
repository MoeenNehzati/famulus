#!/usr/bin/env python3
"""One-time migration tool: Markdown checklist → YAML list.

Separate from lists.py because migration is a one-time operation that
requires dateparser and handles legacy state markers ([+], [-]) not
present in the production format.

Usage:
    python3 migrate_md.py <src.md> <dst.yaml> --schema <name> [--name <list-name>]
"""

import argparse
import datetime
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
today = datetime.date.today().isoformat()


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def validate_list(data: dict) -> None:
    schema_name = data.get("schema")
    schema_path = SCHEMAS_DIR / "lists" / f"{schema_name}.json"
    if not schema_path.exists():
        die(f"unknown schema '{schema_name}'")
    with open(schema_path) as f:
        schema = json.load(f)
    resolver = jsonschema.RefResolver(
        base_uri=schema_path.resolve().as_uri(), referrer=schema
    )
    try:
        jsonschema.validate(data, schema, resolver=resolver, format_checker=FormatChecker())
    except jsonschema.ValidationError as e:
        die(f"validation failed: {e.message}")


def gen_id(existing: set[str]) -> str:
    while True:
        candidate = os.urandom(3).hex()
        if candidate not in existing:
            existing.add(candidate)
            return candidate


def parse_deadline(raw: str) -> str | None:
    raw = raw.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    # Try MM/DD/YY (old format)
    m = re.match(r"^(\d{2})/(\d{2})/(\d{2})$", raw)
    if m:
        month, day, year = m.groups()
        return f"20{year}-{month}-{day}"
    try:
        import dateparser
        parsed = dateparser.parse(raw, settings={"PREFER_DATES_FROM": "future"})
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
        return "done" if marker.lower() == "x" else "incomplete"


def migrate(src: Path, dst: Path, schema_name: str, name: str) -> None:
    try:
        import dateparser  # noqa: F401 — just confirm it's available
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
    unresolvable: list[str] = []

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
            continue

        # Persistent title lines (no checkbox): `- Title` or `  - Title`
        title_only_m = re.match(r"^(\s*)-\s+(?!\[)(.+)$", line)
        if title_only_m:
            # These were structural headers in the old format — become categories
            indent = len(title_only_m.group(1))
            cat_name = title_only_m.group(2).strip()
            cat = {"name": cat_name}
            # Find nearest active parent
            level = indent // 2 + 1  # rough level from indent
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
            continue

        # Checkbox item: `- [ ]`, `- [x]`, `- [+]`, `- [-]`
        item_m = re.match(r"^(\s*)-\s+\[([xX+\- ]?)\]\s+(.+)$", line)
        if item_m:
            marker = item_m.group(2)
            raw_title = item_m.group(3).strip()

            # Strip old creation date from title: (MM/DD/YY) title <!-- #xxxx -->
            raw_title = re.sub(r"\s*<!--\s*#[0-9a-f]+\s*-->\s*$", "", raw_title)
            created_m = re.match(r"^\((\d{2}/\d{2}/\d{2})\)\s+(.+)$", raw_title)
            if created_m:
                created = parse_deadline(created_m.group(1)) or today
                raw_title = created_m.group(2).strip()
            else:
                created = today

            # Extract explicit deadline tag if present
            deadline = None
            due_m = re.search(r"\((?:due|deadline):\s*([^)]+)\)", raw_title, re.IGNORECASE)
            if due_m:
                deadline = parse_deadline(due_m.group(1).strip())
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
            elif schema_name in ("todo", "potential-actions"):
                entry["deadline"] = today
                unresolvable.append(raw_title)

            # Add to last active category
            target_cat = None
            for i in range(5, -1, -1):
                if active_cats[i] is not None:
                    target_cat = active_cats[i]
                    break
            if target_cat is not None:
                target_cat.setdefault("entries", []).append(entry)
            else:
                fallback = {"name": "General", "entries": [entry]}
                categories.append(fallback)
                active_cats[0] = fallback
            continue

        # Continuation lines (deadline:, description): skip — already extracted above

    if unresolvable:
        print(f"warning: {len(unresolvable)} entries got today as deadline placeholder "
              f"(no deadline in source):", file=sys.stderr)
        for t in unresolvable[:5]:
            print(f"  - {t}", file=sys.stderr)
        if len(unresolvable) > 5:
            print(f"  ... and {len(unresolvable) - 5} more", file=sys.stderr)

    doc = {"schema": schema_name, "name": name, "categories": categories}
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
