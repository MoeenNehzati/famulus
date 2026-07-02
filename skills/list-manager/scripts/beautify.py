#!/usr/bin/env python3
"""Pipe-able human-readable formatter for list-manager YAML output.

Reads YAML from stdin and prints a formatted view.

Default behaviour:
  - todo / potential-actions schemas → diff renderer (for LLM diff code blocks)
  - all other schemas               → rich terminal renderer

Usage:
    lists.py read /tmp/todo.yaml | beautify.py
    lists.py read /tmp/todo.yaml | beautify.py --diff      # force diff
    lists.py read /tmp/todo.yaml | beautify.py --markdown  # for LLM markdown
    lists.py read /tmp/todo.yaml | beautify.py -D          # hide descriptions
"""

import argparse
import datetime
import sys
import yaml

# ── Shared constants ──────────────────────────────────────────────────────────

STATE_SYMBOL = {
    "incomplete": "☐",
    "inprogress": "▷",
    "done":       "✓",
    "undecided":  "?",
    "accepted":   "✓",
    "rejected":   "✗",
}

DIFF_SCHEMAS = {"todo", "potential-actions", "default"}


def _deadline_label(ds: str, relative: bool) -> str:
    if not relative:
        return f"due {ds}"
    try:
        due = datetime.date.fromisoformat(ds)
        delta = (due - datetime.date.today()).days
    except ValueError:
        return ds
    if delta < 0:
        return f"{abs(delta)}d overdue"
    if delta == 0:
        return "due today"
    return f"in {delta}d"

# ── Rich renderer (fallback for non-diff schemas) ─────────────────────────────

def _rich_render(data, show_desc: bool, relative_deadlines: bool) -> None:
    from rich.console import Console
    from rich.text import Text
    from rich.tree import Tree
    from rich.rule import Rule

    console = Console(force_terminal=True)
    counter = [1]

    def deadline_style(ds: str) -> str:
        try:
            due = datetime.date.fromisoformat(ds)
            delta = (due - datetime.date.today()).days
            if delta < 0:
                return "bold red"
            if delta <= 7:
                return "yellow"
            return "dim"
        except ValueError:
            return "dim"

    STATE_RICH_STYLE = {
        "incomplete": "",
        "inprogress": "bold blue",
        "done":       "dim",
        "undecided":  "yellow",
        "accepted":   "green",
        "rejected":   "dim red",
    }

    def entry_label(entry: dict) -> Text:
        state = entry.get("state", "")
        symbol = STATE_SYMBOL.get(state, "•")
        title = entry.get("title", "(untitled)")
        deadline = entry.get("deadline", "")
        location = entry.get("location", "")
        base_style = STATE_RICH_STYLE.get(state, "")
        is_finished = state in ("done", "accepted", "rejected")

        t = Text()
        t.append(f"{counter[0]}. ", style="dim")
        counter[0] += 1
        t.append(f"{symbol} ", style=base_style or ("dim" if is_finished else ""))
        t.append(title, style="dim" if is_finished else base_style)
        if deadline:
            t.append(f"  [{_deadline_label(deadline, relative_deadlines)}]", style=deadline_style(deadline))
        if location:
            t.append(f"  @ {location}", style="dim")
        return t

    def add_entries(node, entries: list, show_desc: bool) -> None:
        for entry in entries:
            label = entry_label(entry)
            branch = node.add(label)
            if show_desc and entry.get("description"):
                branch.add(Text(entry["description"], style="dim italic"))
            for child in entry.get("children", []):
                add_entries(branch, [child], show_desc)

    def add_category(node, cat: dict, show_desc: bool) -> None:
        cat_node = node.add(Text(cat.get("name", "(unnamed)"), style="bold"))
        add_entries(cat_node, cat.get("entries", []), show_desc)
        for subcat in cat.get("categories", []):
            add_category(cat_node, subcat, show_desc)

    if isinstance(data, dict):
        name = data.get("name", "")
        schema = data.get("schema", "")
        title = f"{name}  [dim]({schema})[/dim]" if schema else name
        if name:
            console.print(Rule(title, style="bold"))
        root = Tree("")
        for cat in data.get("categories", []):
            add_category(root, cat, show_desc)
        for branch in root.children:
            console.print(branch)
    elif isinstance(data, list):
        root = Tree("")
        add_entries(root, data, show_desc)
        for branch in root.children:
            console.print(branch)


# ── Diff renderer (todo / potential-actions) ──────────────────────────────────
# Green: +, lines containing =
# Red:   - (single dash prefix)
# White: space prefix

def _diff_marker(state):
    if state in ("accepted", "inprogress", "done"):
        return "+"
    if state == "rejected":
        return "-"
    return " "


def _format_entry_diff(entry, indent, counter, show_desc, relative_deadlines):
    lines = []
    state = entry.get("state", "")
    title = entry.get("title", "(untitled)")
    deadline = entry.get("deadline", "")
    location = entry.get("location", "")
    description = entry.get("description", "")
    marker = _diff_marker(state)
    pad = "    " * indent
    meta_parts = []
    if deadline:
        meta_parts.append(_deadline_label(deadline, relative_deadlines))
    if location:
        meta_parts.append(f"@ {location}")
    meta = f"  [{', '.join(meta_parts)}]" if meta_parts else ""
    num = counter[0]
    counter[0] += 1
    lines.append(f"{marker}{pad}{num}. {title}{meta}")
    if show_desc and description:
        lines.append(f" {pad}   {description}")
    for child in entry.get("children", []):
        lines.extend(_format_entry_diff(child, indent + 1, counter, show_desc, relative_deadlines))
    return lines


def _format_category_diff(cat, indent, counter, show_desc, relative_deadlines, depth=0):
    lines = []
    pad = "    " * indent
    name = cat.get("name", "(unnamed)")
    # depth=0 → red top-level header ("-=== Name ===")
    # depth>0 → green subcategory header (" pad=== Name ===")
    if depth == 0:
        lines.append(f"+{pad}=== {name} ===")
    else:
        lines.append(f"-{pad}=== {name} ===")
    for e in cat.get("entries", []):
        lines.extend(_format_entry_diff(e, indent + 1, counter, show_desc, relative_deadlines))
    for sub in cat.get("categories", []):
        lines.extend(_format_category_diff(sub, indent + 1, counter, show_desc, relative_deadlines, depth + 1))
    return lines


def _diff_render(data, show_desc: bool, relative_deadlines: bool) -> None:
    counter = [1]
    lines = []
    if isinstance(data, dict):
        name = data.get("name", "")
        schema = data.get("schema", "")
        if name:
            lines.append(f" {name}" + (f" ({schema})" if schema else ""))
        for i, cat in enumerate(data.get("categories", [])):
            lines.extend(_format_category_diff(cat, 1, counter, show_desc, relative_deadlines))
            if i < len(data.get("categories", [])) - 1:
                lines.append(" ")
    elif isinstance(data, list):
        for e in data:
            lines.extend(_format_entry_diff(e, 0, counter, show_desc, relative_deadlines))
    print("\n".join(lines))


# ── Markdown renderer (--markdown) ────────────────────────────────────────────

def _format_entry_md(entry, indent, counter, show_desc, relative_deadlines):
    lines = []
    prefix = "  " * indent
    state = entry.get("state", "")
    symbol = STATE_SYMBOL.get(state, "•")
    title = entry.get("title", "(untitled)")
    deadline = entry.get("deadline", "")
    location = entry.get("location", "")
    description = entry.get("description", "")
    is_done = state in ("done", "accepted", "rejected")
    meta_parts = []
    if deadline:
        meta_parts.append(_deadline_label(deadline, relative_deadlines))
    if location:
        meta_parts.append(f"@ {location}")
    meta = f" *[{', '.join(meta_parts)}]*" if meta_parts else ""
    num = counter[0]
    counter[0] += 1
    title_str = f"~~{title}~~" if is_done else f"**{title}**" if state == "inprogress" else title
    lines.append(f"{prefix}- {num}. {symbol} {title_str}{meta}")
    if show_desc and description:
        lines.append(f"{prefix}  *{description}*")
    for child in entry.get("children", []):
        lines.extend(_format_entry_md(child, indent + 1, counter, show_desc, relative_deadlines))
    return lines


def _format_category_md(cat, indent, counter, show_desc, relative_deadlines):
    lines = []
    hashes = "#" * (indent + 3)
    lines.append(f"{hashes} {cat.get('name', '(unnamed)')}")
    for e in cat.get("entries", []):
        lines.extend(_format_entry_md(e, 0, counter, show_desc, relative_deadlines))
    for sub in cat.get("categories", []):
        lines.extend(_format_category_md(sub, indent + 1, counter, show_desc, relative_deadlines))
    return lines


def _markdown_render(data, show_desc: bool, relative_deadlines: bool) -> None:
    counter = [1]
    lines = []
    if isinstance(data, dict):
        name = data.get("name", "")
        schema = data.get("schema", "")
        if name:
            lines.append(f"# {name}" + (f" *({schema})*" if schema else ""))
        for i, cat in enumerate(data.get("categories", [])):
            lines.extend(_format_category_md(cat, 0, counter, show_desc, relative_deadlines))
            if i < len(data.get("categories", [])) - 1:
                lines.append("")
    elif isinstance(data, list):
        for e in data:
            lines.extend(_format_entry_md(e, 0, [1], show_desc, relative_deadlines))
    print("\n".join(lines))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(prog="beautify.py")
    parser.add_argument("-D", "--no-descriptions", action="store_true",
                        help="Hide entry descriptions (shown by default)")
    parser.add_argument("--diff", action="store_true",
                        help="Force diff renderer (auto for todo/potential-actions)")
    parser.add_argument("--markdown", action="store_true",
                        help="Output markdown (for LLM markdown blocks)")
    parser.add_argument("--relative-deadlines", action="store_true",
                        help="Show relative deadline labels like [in 3d] or [2d overdue]")
    args = parser.parse_args()

    text = sys.stdin.read()
    if not text.strip():
        return

    data = yaml.safe_load(text)
    show_desc = not args.no_descriptions
    schema = data.get("schema", "") if isinstance(data, dict) else ""

    if args.markdown:
        _markdown_render(data, show_desc, args.relative_deadlines)
    elif args.diff or schema in DIFF_SCHEMAS:
        _diff_render(data, show_desc, args.relative_deadlines)
    else:
        _rich_render(data, show_desc, args.relative_deadlines)


if __name__ == "__main__":
    main()
