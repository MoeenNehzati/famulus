#!/usr/bin/env python3
"""Pipe-able human-readable formatter for list-manager YAML output.

Reads YAML from stdin, prints indented, numbered, human-friendly output.
IDs are stripped. State shown as symbol. Children indented.

Usage:
    lists.py read /tmp/todo.yaml | beautify.py
    lists.py read /tmp/todo.yaml state=incomplete | beautify.py
"""

import sys
import yaml

STATE_SYMBOL = {
    "incomplete": "☐",
    "inprogress": "▷",
    "done": "✓",
    "undecided": "?",
    "accepted": "✓",
    "rejected": "✗",
}


def format_entry(entry: dict, number: str, indent: int = 0) -> list[str]:
    """Format a single entry and its children into lines."""
    lines = []
    prefix = "  " * indent
    state = entry.get("state", "")
    symbol = STATE_SYMBOL.get(state, "•")
    title = entry.get("title", "(untitled)")
    deadline = entry.get("deadline", "")
    location = entry.get("location", "")

    meta_parts = []
    if deadline:
        meta_parts.append(f"due {deadline}")
    if location:
        meta_parts.append(f"@ {location}")
    meta = f"  [{', '.join(meta_parts)}]" if meta_parts else ""

    lines.append(f"{prefix}{number}. {symbol} {title}{meta}")

    description = entry.get("description", "")
    if description:
        lines.append(f"{prefix}   {description}")

    children = entry.get("children", [])
    for i, child in enumerate(children, 1):
        child_number = f"{number}.{i}"
        lines.extend(format_entry(child, child_number, indent + 1))

    return lines


def format_category(category: dict, number: str, indent: int = 0) -> list[str]:
    """Format a category (heading + entries + subcategories)."""
    lines = []
    prefix = "  " * indent
    name = category.get("name", "(unnamed)")
    lines.append(f"{prefix}{number}. {name}")

    entries = category.get("entries", [])
    for i, entry in enumerate(entries, 1):
        lines.extend(format_entry(entry, f"{number}.{i}", indent + 1))

    subcats = category.get("categories", [])
    for i, subcat in enumerate(subcats, len(entries) + 1):
        lines.extend(format_category(subcat, f"{number}.{i}", indent + 1))

    return lines


def format_flat_entries(entries: list[dict]) -> list[str]:
    """Format a flat list of entries (output of filtered read)."""
    lines = []
    for i, entry in enumerate(entries, 1):
        lines.extend(format_entry(entry, str(i), indent=0))
    return lines


def format_full_doc(doc: dict) -> list[str]:
    """Format a full list document with categories."""
    lines = []
    name = doc.get("name", "")
    schema = doc.get("schema", "")
    if name:
        header = name
        if schema:
            header += f" ({schema})"
        lines.append(header)
        lines.append("=" * len(header))

    categories = doc.get("categories", [])
    for i, cat in enumerate(categories, 1):
        lines.extend(format_category(cat, str(i), indent=0))
        if i < len(categories):
            lines.append("")

    return lines


def main() -> None:
    text = sys.stdin.read()
    if not text.strip():
        return

    data = yaml.safe_load(text)

    if isinstance(data, list):
        # Filtered read output: flat list of entries
        lines = format_flat_entries(data)
    elif isinstance(data, dict):
        # Full document
        lines = format_full_doc(data)
    else:
        print(text, end="")
        return

    print("\n".join(lines))


if __name__ == "__main__":
    main()
