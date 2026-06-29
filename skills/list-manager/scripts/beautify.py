#!/usr/bin/env python3
"""Pipe-able human-readable formatter for list-manager YAML output.

Reads YAML from stdin, prints indented human-friendly output.
IDs are stripped. Categories are unlabeled headers. Only entries are numbered.
Descriptions hidden by default; use -d / --descriptions to show them.

Usage:
    lists.py read /tmp/todo.yaml | beautify.py
    lists.py read /tmp/todo.yaml state=incomplete | beautify.py
    lists.py read /tmp/todo.yaml | beautify.py --descriptions
"""

import argparse
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


def format_entry(entry: dict, n: int, indent: int, counter: list[int],
                 show_descriptions: bool) -> list[str]:
    """Format a single entry and its children. counter is a mutable [int] for global numbering."""
    lines = []
    prefix = "  " * indent
    state = entry.get("state", "")
    symbol = STATE_SYMBOL.get(state, "•")
    title = entry.get("title", "(untitled)")
    deadline = entry.get("deadline", "")
    location = entry.get("location", "")
    description = entry.get("description", "")

    meta_parts = []
    if deadline:
        meta_parts.append(f"due {deadline}")
    if location:
        meta_parts.append(f"@ {location}")
    meta = f"  [{', '.join(meta_parts)}]" if meta_parts else ""

    num = counter[0]
    counter[0] += 1
    lines.append(f"{prefix}{num}. {symbol} {title}{meta}")

    if show_descriptions and description:
        lines.append(f"{prefix}   {description}")

    children = entry.get("children", [])
    for child in children:
        lines.extend(format_entry(child, num, indent + 1, counter, show_descriptions))

    return lines


def format_category(category: dict, indent: int, counter: list[int],
                    show_descriptions: bool) -> list[str]:
    """Format a category as an unlabeled header, then its entries and subcategories."""
    lines = []
    prefix = "  " * indent
    name = category.get("name", "(unnamed)")
    lines.append(f"{prefix}{name}")

    entries = category.get("entries", [])
    for entry in entries:
        lines.extend(format_entry(entry, counter[0], indent + 1, counter, show_descriptions))

    subcats = category.get("categories", [])
    for subcat in subcats:
        lines.extend(format_category(subcat, indent + 1, counter, show_descriptions))

    return lines


def format_flat_entries(entries: list[dict], show_descriptions: bool) -> list[str]:
    """Format a flat list of entries (output of filtered read)."""
    lines = []
    counter = [1]
    for entry in entries:
        lines.extend(format_entry(entry, counter[0], indent=0, counter=counter,
                                  show_descriptions=show_descriptions))
    return lines


def format_full_doc(doc: dict, show_descriptions: bool) -> list[str]:
    """Format a full list document."""
    lines = []
    name = doc.get("name", "")
    schema = doc.get("schema", "")
    if name:
        header = name
        if schema:
            header += f" ({schema})"
        lines.append(header)
        lines.append("=" * len(header))

    counter = [1]
    categories = doc.get("categories", [])
    for i, cat in enumerate(categories):
        lines.extend(format_category(cat, indent=0, counter=counter,
                                     show_descriptions=show_descriptions))
        if i < len(categories) - 1:
            lines.append("")

    return lines


def main() -> None:
    parser = argparse.ArgumentParser(prog="beautify.py")
    parser.add_argument("-d", "--descriptions", action="store_true",
                        help="Show entry descriptions")
    args = parser.parse_args()

    text = sys.stdin.read()
    if not text.strip():
        return

    data = yaml.safe_load(text)

    if isinstance(data, list):
        lines = format_flat_entries(data, args.descriptions)
    elif isinstance(data, dict):
        lines = format_full_doc(data, args.descriptions)
    else:
        print(text, end="")
        return

    print("\n".join(lines))


if __name__ == "__main__":
    main()
