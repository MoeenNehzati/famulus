#!/usr/bin/env python3
"""Pipe-able human-readable formatter for list-manager YAML output.

Reads YAML from stdin and prints a formatted view.

Default behaviour:
  - todo / triage schemas → nested bullet-list markdown renderer (checkboxes,
                            bold/strikethrough, blockquoted descriptions,
                            deadline-urgency emoji -- renders consistently
                            across chat surfaces, unlike ```diff fences, which
                            some clients render inconsistently)
  - all other schemas     → rich terminal renderer

Two other renderers are preserved as explicit opt-ins for callers that want
them specifically: the old diff-fenced renderer (`--diff`), and a flat GFM
table (`--table`).

Usage:
    lists.py read /tmp/todo.yaml | beautify.py
    lists.py read /tmp/todo.yaml | beautify.py --diff          # old diff-fence renderer
    lists.py read /tmp/todo.yaml | beautify.py --table         # GFM table renderer
    lists.py read /tmp/todo.yaml | beautify.py -D              # hide descriptions
"""

import argparse
import datetime
import sys
import yaml

# ── Shared constants ──────────────────────────────────────────────────────────

STATE_SYMBOL = {
    "incomplete": "☐",
    "inprogress": "▷",
    "complete":   "✓",
    "undecided":  "?",
    "accepted":   "✓",
    "rejected":   "✗",
}

BULLET_SCHEMAS = {"todo", "triage", "default"}

# An entry in one of these states is done -- its `deadline` is no longer
# meaningful (it can only ever read as "overdue"), so renderers show
# `completed` instead, when available. `modified` is never rendered here; it
# exists purely as a debugging aid (see lists.py's cmd_update).
FINISHED_STATES = {"complete", "accepted", "rejected"}

# Render-scoped toggle: when true, entry lines carry a trailing `#<id>` so a
# reader (human or LLM) can act on a specific row by id without counting.
_SHOW_IDS = False


def _id_suffix(entry: dict) -> str:
    eid = entry.get("id")
    return f"  #{eid}" if (_SHOW_IDS and eid) else ""


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


def _completed_label(ds: str, relative: bool) -> str:
    if not relative:
        return f"completed {ds}"
    try:
        done = datetime.date.fromisoformat(ds)
        delta = (datetime.date.today() - done).days
    except ValueError:
        return f"completed {ds}"
    if delta <= 0:
        return "completed today"
    return f"completed {delta}d ago"


def _date_badge(entry: dict, relative_deadlines: bool) -> str:
    """Return the date-ish label to show for one entry: `completed` for a
    finished entry that has one, its `deadline` otherwise (finished entries
    with no recorded completion date show nothing, rather than a misleading
    "Nd overdue" for something that's actually done)."""
    if entry.get("state", "") in FINISHED_STATES:
        completed = entry.get("completed", "")
        return _completed_label(completed, relative_deadlines) if completed else ""
    deadline = entry.get("deadline", "")
    return _deadline_label(deadline, relative_deadlines) if deadline else ""

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
        "complete":   "dim",
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
        is_finished = state in ("complete", "accepted", "rejected")

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


# ── Diff renderer (todo / triage) ─────────────────────────────────────────────
# Green: +, lines containing =
# Red:   - (single dash prefix)
# White: space prefix

def _diff_marker(state):
    if state in ("accepted", "inprogress", "complete"):
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
    lines.append(f"{marker}{pad}{num}. {title}{meta}{_id_suffix(entry)}")
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
    # Wrap in a ```diff fence so the output renders with diff highlighting
    # when relayed into a chat reply, without relying on the caller to add it.
    print("```diff")
    print("\n".join(lines))
    print("```")


# ── Table renderer (--table opt-in) ───────────────────────────────────────────
# A real GFM table renders consistently across chat clients, unlike ```diff
# fences (which some clients highlight inconsistently or not at all).

def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _collect_rows_table(entries, indent, counter, show_desc, relative_deadlines, rows):
    for entry in entries:
        state = entry.get("state", "")
        symbol = STATE_SYMBOL.get(state, "•")
        title = entry.get("title", "(untitled)")
        location = entry.get("location", "")
        description = entry.get("description", "")
        is_done = state in FINISHED_STATES
        num = counter[0]
        counter[0] += 1

        title_cell = _md_escape(title)
        if is_done:
            title_cell = f"~~{title_cell}~~"
        elif state == "inprogress":
            title_cell = f"**{title_cell}**"
        if indent:
            title_cell = ("&nbsp;&nbsp;" * indent) + "↳ " + title_cell
        if show_desc and description:
            title_cell += f"<br>*{_md_escape(description)}*"
        id_suffix = _id_suffix(entry)
        if id_suffix:
            title_cell += " " + id_suffix.strip()

        date_label = _date_badge(entry, relative_deadlines)
        deadline_cell = _md_escape(date_label) if date_label else ""
        location_cell = _md_escape(location) if location else ""

        rows.append([str(num), symbol, title_cell, deadline_cell, location_cell])
        children = entry.get("children", [])
        if children:
            _collect_rows_table(children, indent + 1, counter, show_desc, relative_deadlines, rows)


def _entries_table_block(entries, counter, show_desc, relative_deadlines):
    rows = []
    _collect_rows_table(entries, 0, counter, show_desc, relative_deadlines, rows)
    if not rows:
        return []
    header = ["#", "", "Title", "Deadline", "Location"]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return lines


def _format_category_table(cat, indent, counter, show_desc, relative_deadlines):
    lines = []
    hashes = "#" * (indent + 3)
    entries = cat.get("entries", [])
    sub_blocks = []
    for sub in cat.get("categories", []):
        sub_lines = _format_category_table(sub, indent + 1, counter, show_desc, relative_deadlines)
        if sub_lines:
            sub_blocks.append(sub_lines)
    if not entries and not sub_blocks:
        return []  # skip empty categories/subcategories entirely
    lines.append(f"{hashes} {cat.get('name', '(unnamed)')}")
    if entries:
        lines.append("")
        lines.extend(_entries_table_block(entries, counter, show_desc, relative_deadlines))
    for sub_lines in sub_blocks:
        lines.append("")
        lines.extend(sub_lines)
    return lines


def _table_render(data, show_desc: bool, relative_deadlines: bool) -> None:
    counter = [1]
    lines = []
    if isinstance(data, dict):
        name = data.get("name", "")
        schema = data.get("schema", "")
        if name:
            lines.append(f"# {name}" + (f" *({schema})*" if schema else ""))
        cats = data.get("categories", [])
        for cat in cats:
            cat_lines = _format_category_table(cat, 0, counter, show_desc, relative_deadlines)
            if cat_lines:
                lines.append("")
                lines.extend(cat_lines)
    elif isinstance(data, list):
        lines.extend(_entries_table_block(data, counter, show_desc, relative_deadlines))
    print("\n".join(lines))


# ── Bullet-list markdown renderer (default for todo/triage; --markdown) ──────
# Nested GFM bullets read more reliably across chat clients than a flat table
# with HTML-indent hacks, and let us lean on real markdown features: task-list
# checkboxes, bold/strikethrough, blockquoted descriptions, inline-code ids,
# and emoji badges for deadline urgency -- each a light, real signal rather
# than a giant wall of table cells.

STATE_BULLET = {
    "incomplete": "[ ]",
    "inprogress": "[ ]",
    "complete":   "[x]",
    "undecided":  "[ ]",
    "accepted":   "[x]",
    "rejected":   "[x]",
}


def _date_emoji_badge(entry: dict, relative_deadlines: bool) -> str:
    """Emoji + label for an entry's most relevant date: `completed` (✅) for a
    finished entry that has one, urgency-colored `deadline` otherwise."""
    if entry.get("state", "") in FINISHED_STATES:
        completed = entry.get("completed", "")
        return f"✅ {_completed_label(completed, relative_deadlines)}" if completed else ""
    deadline = entry.get("deadline", "")
    if not deadline:
        return ""
    try:
        due = datetime.date.fromisoformat(deadline)
        delta = (due - datetime.date.today()).days
        emoji = "🔴" if delta < 0 else "🟡" if delta <= 7 else "⚪"
    except ValueError:
        emoji = "⚪"
    return f"{emoji} {_deadline_label(deadline, relative_deadlines)}"


def _format_entry_md(entry, indent, show_desc, relative_deadlines):
    lines = []
    prefix = "  " * indent
    state = entry.get("state", "")
    title = entry.get("title", "(untitled)")
    location = entry.get("location", "")
    description = entry.get("description", "")
    is_finished = state in FINISHED_STATES
    box = STATE_BULLET.get(state, "[ ]")

    if state == "inprogress":
        title_str = f"⏳ **{title}**"
    elif is_finished:
        title_str = f"~~{title}~~"
    else:
        title_str = title

    meta_parts = []
    date_badge = _date_emoji_badge(entry, relative_deadlines)
    if date_badge:
        meta_parts.append(date_badge)
    if location:
        meta_parts.append(f"📍 {location}")
    meta = "  " + "  ".join(meta_parts) if meta_parts else ""

    eid = entry.get("id", "")
    id_part = f"  `#{eid}`" if (_SHOW_IDS and eid) else ""

    lines.append(f"{prefix}- {box} {title_str}{meta}{id_part}")
    if show_desc and description:
        # Blockquote every line of a multi-line description -- a bare "> "
        # on only the first line lets embedded newlines break out of the
        # quote and render as literal top-level markdown (e.g. a "- " list
        # line in the description text would become its own bullet).
        for desc_line in description.splitlines() or [""]:
            lines.append(f"{prefix}  > {desc_line}".rstrip())
    for child in entry.get("children", []):
        lines.extend(_format_entry_md(child, indent + 1, show_desc, relative_deadlines))
    return lines


def _format_category_md(cat, indent, show_desc, relative_deadlines):
    entries = cat.get("entries", [])
    sub_blocks = []
    for sub in cat.get("categories", []):
        sub_lines = _format_category_md(sub, indent + 1, show_desc, relative_deadlines)
        if sub_lines:
            sub_blocks.append(sub_lines)
    if not entries and not sub_blocks:
        return []  # skip empty categories/subcategories entirely

    # Categories are bullets themselves now, not headers: top-level category
    # bold, nested subcategories italic, each indented one level deeper than
    # its parent -- entries then nest one level deeper still, under whichever
    # category/subcategory bullet they actually belong to.
    prefix = "  " * indent
    name = cat.get("name", "(unnamed)")
    label = f"**{name}**" if indent == 0 else f"*{name}*"
    lines = [f"{prefix}- {label}"]
    for e in entries:
        lines.extend(_format_entry_md(e, indent + 1, show_desc, relative_deadlines))
    for sub_lines in sub_blocks:
        lines.extend(sub_lines)
    return lines


def _markdown_render(data, show_desc: bool, relative_deadlines: bool) -> None:
    lines = []
    if isinstance(data, dict):
        name = data.get("name", "")
        schema = data.get("schema", "")
        if name:
            lines.append(f"# {name}" + (f" *({schema})*" if schema else ""))
            lines.append("")
        for cat in data.get("categories", []):
            lines.extend(_format_category_md(cat, 0, show_desc, relative_deadlines))
    elif isinstance(data, list):
        for e in data:
            lines.extend(_format_entry_md(e, 0, show_desc, relative_deadlines))
    print("\n".join(lines))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(prog="beautify.py")
    parser.add_argument("-D", "--no-descriptions", action="store_true",
                        help="Hide entry descriptions (shown by default)")
    parser.add_argument("--diff", action="store_true",
                        help="Force the legacy diff-fenced renderer")
    parser.add_argument("--table", action="store_true",
                        help="Force the flat GFM table renderer (opt-in; default is nested bullet-list markdown)")
    parser.add_argument("--markdown", action="store_true",
                        help="Force the bullet-list markdown renderer (auto for todo/triage schemas; "
                             "use explicitly when schema info may be stripped, e.g. filtered list input)")
    parser.add_argument("--relative-deadlines", action="store_true",
                        help="Show relative deadline labels like [in 3d] or [2d overdue]")
    parser.add_argument("--ids", action="store_true",
                        help="Append each entry's #id so rows can be acted on by id")
    args = parser.parse_args()

    global _SHOW_IDS
    _SHOW_IDS = args.ids

    text = sys.stdin.read()
    if not text.strip():
        return

    data = yaml.safe_load(text)
    show_desc = not args.no_descriptions
    schema = data.get("schema", "") if isinstance(data, dict) else ""

    if args.diff:
        _diff_render(data, show_desc, args.relative_deadlines)
    elif args.table:
        _table_render(data, show_desc, args.relative_deadlines)
    elif args.markdown or schema in BULLET_SCHEMAS:
        _markdown_render(data, show_desc, args.relative_deadlines)
    else:
        _rich_render(data, show_desc, args.relative_deadlines)


if __name__ == "__main__":
    main()
