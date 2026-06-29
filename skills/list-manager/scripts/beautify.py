#!/usr/bin/env python3
"""Pipe-able human-readable formatter for list-manager YAML output.

Reads YAML from stdin, prints a rich terminal view (default) or plain-text
variants for LLM consumption.

Usage:
    lists.py read /tmp/todo.yaml | beautify.py
    lists.py read /tmp/todo.yaml state=incomplete | beautify.py
    lists.py read /tmp/todo.yaml | beautify.py --no-color
    lists.py read /tmp/todo.yaml | beautify.py --diff    # for LLM diff blocks
    lists.py read /tmp/todo.yaml | beautify.py --markdown  # for LLM markdown
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

# ── Rich renderer (default) ───────────────────────────────────────────────────

def _rich_render(data, show_desc: bool) -> None:
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

        if is_finished:
            t.append(title, style="dim")
        else:
            t.append(title, style=base_style)

        if deadline:
            t.append(f"  [due {deadline}]", style=deadline_style(deadline))
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
        name = cat.get("name", "(unnamed)")
        cat_node = node.add(Text(name, style="bold"))
        add_entries(cat_node, cat.get("entries", []), show_desc)
        for subcat in cat.get("categories", []):
            add_category(cat_node, subcat, show_desc)

    # Full document
    if isinstance(data, dict):
        name = data.get("name", "")
        schema = data.get("schema", "")
        title = f"{name}  [dim]({schema})[/dim]" if schema else name
        if name:
            console.print(Rule(title, style="bold"))
        root = Tree("")
        for cat in data.get("categories", []):
            add_category(root, cat, show_desc)
        # Don't print the empty root label — print children directly
        for branch in root.children:
            console.print(branch)

    # Flat filtered list
    elif isinstance(data, list):
        root = Tree("")
        add_entries(root, data, show_desc)
        for branch in root.children:
            console.print(branch)


# ── Plain ANSI renderer (--no-color) ─────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
FG_GREEN  = "\033[32m"
FG_RED    = "\033[31m"
FG_YELLOW = "\033[33m"
FG_BLUE   = "\033[34m"

STATE_COLOR = {
    "incomplete": "",
    "inprogress": FG_BLUE,
    "done":       "",
    "undecided":  FG_YELLOW,
    "accepted":   FG_GREEN,
    "rejected":   FG_RED,
}


def c(text: str, *codes: str, color: bool = True) -> str:
    if not color or not codes:
        return text
    return "".join(codes) + text + RESET


def deadline_ansi(ds: str, color: bool) -> str:
    label = f"due {ds}"
    if not color or not ds:
        return label
    try:
        delta = (datetime.date.fromisoformat(ds) - datetime.date.today()).days
        if delta < 0:
            return c(label, FG_RED, BOLD, color=color)
        if delta <= 7:
            return c(label, FG_YELLOW, color=color)
        return c(label, DIM, color=color)
    except ValueError:
        return label


def format_entry_plain(entry, indent, counter, show_desc, color):
    lines = []
    state = entry.get("state", "")
    symbol = STATE_SYMBOL.get(state, "•")
    title = entry.get("title", "(untitled)")
    deadline = entry.get("deadline", "")
    location = entry.get("location", "")
    description = entry.get("description", "")
    is_done = state in ("done", "accepted", "rejected")
    sc = STATE_COLOR.get(state, "")
    pad = "  " * indent

    meta_parts = []
    if deadline:
        meta_parts.append(deadline_ansi(deadline, color))
    if location:
        meta_parts.append(c(f"@ {location}", DIM, color=color))
    meta = f"  [{', '.join(meta_parts)}]" if meta_parts else ""

    num_str = c(f"{counter[0]}.", DIM, color=color)
    sym_str = c(symbol, sc, color=color)
    title_str = c(title, DIM, color=color) if is_done else c(title, sc, color=color) if sc else title
    counter[0] += 1

    lines.append(f"{pad}{num_str} {sym_str} {title_str}{meta}")
    if show_desc and description:
        lines.append(f"{pad}   {c(description, DIM, color=color)}")
    for child in entry.get("children", []):
        lines.extend(format_entry_plain(child, indent + 1, counter, show_desc, color))
    return lines


def format_category_plain(cat, indent, counter, show_desc, color):
    lines = []
    pad = "  " * indent
    lines.append(f"{pad}{c(cat.get('name', '(unnamed)'), BOLD, color=color)}")
    for e in cat.get("entries", []):
        lines.extend(format_entry_plain(e, indent + 1, counter, show_desc, color))
    for sub in cat.get("categories", []):
        lines.extend(format_category_plain(sub, indent + 1, counter, show_desc, color))
    return lines


def _plain_render(data, show_desc: bool, color: bool) -> None:
    counter = [1]
    lines = []
    if isinstance(data, dict):
        name = data.get("name", "")
        schema = data.get("schema", "")
        if name:
            header = name + (f" ({schema})" if schema else "")
            lines.append(c(header, BOLD, color=color))
            lines.append(c("=" * len(header), DIM, color=color))
        for i, cat in enumerate(data.get("categories", [])):
            lines.extend(format_category_plain(cat, 0, counter, show_desc, color))
            if i < len(data.get("categories", [])) - 1:
                lines.append("")
    elif isinstance(data, list):
        for e in data:
            lines.extend(format_entry_plain(e, 0, counter, show_desc, color))
    print("\n".join(lines))


# ── Diff renderer (--diff, for LLM diff code blocks) ─────────────────────────

def diff_marker(state):
    if state in ("done", "accepted"):
        return "+"
    if state in ("rejected",):
        return "-"
    return " "


def format_entry_diff(entry, indent, counter, show_desc):
    lines = []
    state = entry.get("state", "")
    symbol = STATE_SYMBOL.get(state, "•")
    title = entry.get("title", "(untitled)")
    deadline = entry.get("deadline", "")
    location = entry.get("location", "")
    description = entry.get("description", "")
    marker = diff_marker(state)
    pad = "  " * indent
    meta_parts = []
    if deadline:
        meta_parts.append(f"due {deadline}")
    if location:
        meta_parts.append(f"@ {location}")
    meta = f"  [{', '.join(meta_parts)}]" if meta_parts else ""
    num = counter[0]
    counter[0] += 1
    lines.append(f"{marker}{pad}{num}. {symbol} {title}{meta}")
    if show_desc and description:
        lines.append(f" {pad}   {description}")
    for child in entry.get("children", []):
        lines.extend(format_entry_diff(child, indent + 1, counter, show_desc))
    return lines


def format_category_diff(cat, indent, counter, show_desc, depth=0):
    lines = []
    pad = "  " * indent
    name = cat.get("name", "(unnamed)")
    if depth == 0:
        lines.append(f"@@ {name} @@")
    else:
        lines.append(f" {pad}{name}")
    for e in cat.get("entries", []):
        lines.extend(format_entry_diff(e, indent + 1, counter, show_desc))
    for sub in cat.get("categories", []):
        lines.extend(format_category_diff(sub, indent + 1, counter, show_desc, depth + 1))
    return lines


def _diff_render(data, show_desc: bool) -> None:
    counter = [1]
    lines = []
    if isinstance(data, dict):
        name = data.get("name", "")
        schema = data.get("schema", "")
        if name:
            header = name + (f" ({schema})" if schema else "")
            lines += [f" {header}", f" {'=' * len(header)}"]
        for i, cat in enumerate(data.get("categories", [])):
            lines.extend(format_category_diff(cat, 0, counter, show_desc))
            if i < len(data.get("categories", [])) - 1:
                lines.append(" ")
    elif isinstance(data, list):
        for e in data:
            lines.extend(format_entry_diff(e, 0, counter, show_desc))
    print("\n".join(lines))


# ── Markdown renderer (--markdown, for LLM markdown blocks) ──────────────────

def format_entry_md(entry, indent, counter, show_desc):
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
        meta_parts.append(f"due {deadline}")
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
        lines.extend(format_entry_md(child, indent + 1, counter, show_desc))
    return lines


def format_category_md(cat, indent, counter, show_desc):
    lines = []
    prefix = "  " * indent
    hashes = "#" * (indent + 3)
    lines.append(f"{hashes} {cat.get('name', '(unnamed)')}")
    for e in cat.get("entries", []):
        lines.extend(format_entry_md(e, 0, counter, show_desc))
    for sub in cat.get("categories", []):
        lines.extend(format_category_md(sub, indent + 1, counter, show_desc))
    return lines


def _markdown_render(data, show_desc: bool) -> None:
    counter = [1]
    lines = []
    if isinstance(data, dict):
        name = data.get("name", "")
        schema = data.get("schema", "")
        if name:
            lines.append(f"# {name}" + (f" *({schema})*" if schema else ""))
        for i, cat in enumerate(data.get("categories", [])):
            lines.extend(format_category_md(cat, 0, counter, show_desc))
            if i < len(data.get("categories", [])) - 1:
                lines.append("")
    elif isinstance(data, list):
        for e in data:
            lines.extend(format_entry_md(e, 0, [1], show_desc))
    print("\n".join(lines))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(prog="beautify.py")
    parser.add_argument("-D", "--no-descriptions", action="store_true",
                        help="Hide entry descriptions (shown by default)")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable colors (plain ANSI fallback)")
    parser.add_argument("--markdown", action="store_true",
                        help="Output markdown (for LLM rendering)")
    parser.add_argument("--diff", action="store_true",
                        help="Output diff format (for LLM diff code blocks)")
    args = parser.parse_args()

    text = sys.stdin.read()
    if not text.strip():
        return

    data = yaml.safe_load(text)
    show_desc = not args.no_descriptions

    if args.diff:
        _diff_render(data, show_desc)
    elif args.markdown:
        _markdown_render(data, show_desc)
    elif args.no_color:
        _plain_render(data, show_desc, color=False)
    else:
        _rich_render(data, show_desc)


if __name__ == "__main__":
    main()
