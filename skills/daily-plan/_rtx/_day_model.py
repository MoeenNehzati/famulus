#!/usr/bin/env python3
"""Runtime helpers for generating, rendering, and mutating the daily plan.

Persisted artifacts per day:
- `plans/<date>.md`: human-readable rendered plan shown to the user
- `plans/<date>.meta.json`: JSON object with two lists,
  `{"actions": [[id, situation], ...], "triage": [[id, situation], ...]}`

The metadata is the source of truth for which master-list items belong to the
plan and whether each one is currently `shown` or `hidden`. The rendered plan
contains HTML marker pairs `<!-- BEGIN ACTIONS --> ... <!-- END ACTIONS -->`
and `<!-- BEGIN TRIAGE --> ... <!-- END TRIAGE -->`; every refresh replaces the
contents of those blocks from the current master-list state.

Mutation commands fall into two groups:
- plan-local only: `hide`, `show`, `keep`, `remove`, `add`
- master-list backed: `mark-done`, `reject`, `set-deadline`

After every mutation or refresh, this module rewrites both the metadata file
and the rendered plan so the stored plan remains human-readable and current.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
try:
    from officina.common.dates import get_today_date_key, normalize_date_key
    from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface
except ImportError:  # pragma: no cover - local checkout fallback
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from officina.common.dates import get_today_date_key, normalize_date_key
    from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface

MAX_ITEMS = 5
SECTION_SPECS = {
    "actions": {
        "list": "todo",
        "marker": "ACTIONS",
        "header": "## Actions (suggestions)",
        "none": "(nothing selected for actions)",
    },
    "triage": {
        "list": "triage",
        "marker": "TRIAGE",
        "header": "## Triage",
        "none": "(nothing selected for triage)",
    },
}


class PlanError(Exception):
    pass


DISPATCHES = {
    "cloud-plans-read": DispatchCall(
        caller_skill="daily-plan",
        target_skill="cloud-files",
        interface="plans-read",
    ),
    "cloud-plans-write": DispatchCall(
        caller_skill="daily-plan",
        target_skill="cloud-files",
        interface="plans-write",
    ),
    "cloud-lists-read": DispatchCall(
        caller_skill="daily-plan",
        target_skill="cloud-files",
        interface="lists-read",
    ),
    "cloud-lists-write": DispatchCall(
        caller_skill="daily-plan",
        target_skill="cloud-files",
        interface="lists-write",
    ),
    "calendar-agenda": DispatchCall(
        caller_skill="daily-plan",
        target_skill="g-calendar",
        interface="scripts-gcal",
    ),
    "weather": DispatchCall(
        caller_skill="daily-plan",
        target_skill="get-weather",
        interface="scripts-weather",
        smoke_args=(),
    ),
    "list-read-beautify": DispatchCall(
        caller_skill="daily-plan",
        target_skill="list-manager",
        interface="read-beautify",
    ),
    "list-update": DispatchCall(
        caller_skill="daily-plan",
        target_skill="list-manager",
        interface="update-list",
    ),
}

_DISPATCH_KEYS = {
    ("cloud-files", "plans-read"): "cloud-plans-read",
    ("cloud-files", "plans-write"): "cloud-plans-write",
    ("cloud-files", "lists-read"): "cloud-lists-read",
    ("cloud-files", "lists-write"): "cloud-lists-write",
    ("g-calendar", "scripts-gcal"): "calendar-agenda",
    ("get-weather", "scripts-weather"): "weather",
    ("list-manager", "read-beautify"): "list-read-beautify",
    ("list-manager", "update-list"): "list-update",
}


class _DefaultDispatchInterface(PythonMachineInterface):
    dispatches = DISPATCHES


_dispatch_interface: PythonMachineInterface = _DefaultDispatchInterface()


def set_dispatch_interface(interface: PythonMachineInterface) -> None:
    global _dispatch_interface
    _dispatch_interface = interface


def run_dispatcher(target_skill: str, script_interface: str, *args: str, stdin: str | None = None) -> str:
    try:
        key = _DISPATCH_KEYS[(target_skill, script_interface)]
    except KeyError as exc:
        raise PlanError(f"unknown declared dispatch for {target_skill}:{script_interface}") from exc
    try:
        result = _dispatch_interface.dispatch(
            key,
            args=list(args),
            stdin=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:  # pragma: no cover
        raise PlanError(f"Failed to invoke {target_skill}:{script_interface}: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise PlanError(f"dispatcher failed for {target_skill}:{script_interface}: {stderr}")
    return result.stdout


def get_today_date() -> str:
    return get_today_date_key()


def normalize_plan_date(value: str | None) -> str:
    if value is None or not value.strip():
        return get_today_date()
    try:
        return normalize_date_key(value)
    except ValueError as exc:
        raise PlanError("date must be M-D-YY or YYYY-MM-DD") from exc


def plan_path(date_key: str) -> str:
    return f"plans/{date_key}.md"


def meta_path(date_key: str) -> str:
    return f"plans/{date_key}.meta.json"


def plan_exists(date_key: str) -> bool:
    try:
        run_dispatcher("cloud-files", "plans-read", plan_path(date_key))
        return True
    except PlanError:
        return False


def read_plan_text(date_key: str) -> str:
    return run_dispatcher("cloud-files", "plans-read", plan_path(date_key))


def write_plan_text(date_key: str, content: str) -> None:
    run_dispatcher("cloud-files", "plans-write", plan_path(date_key), stdin=content)


def read_meta(date_key: str) -> dict[str, list[list[str]]]:
    try:
        raw = run_dispatcher("cloud-files", "plans-read", meta_path(date_key))
    except PlanError:
        return {"actions": [], "triage": []}
    data = json.loads(raw)
    return {
        "actions": [list(row) for row in data.get("actions", [])],
        "triage": [list(row) for row in data.get("triage", [])],
    }


def write_meta(date_key: str, meta: dict[str, list[list[str]]]) -> None:
    payload = json.dumps(meta, indent=2) + "\n"
    run_dispatcher("cloud-files", "plans-write", meta_path(date_key), stdin=payload)


def load_list_text(list_name: str) -> str:
    return run_dispatcher("cloud-files", "lists-read", f"lists/{list_name}.yaml")


def load_list_doc(list_name: str) -> dict[str, Any]:
    return yaml.safe_load(load_list_text(list_name)) or {}


def write_list_text(list_name: str, content: str) -> None:
    run_dispatcher("cloud-files", "lists-write", f"lists/{list_name}.yaml", stdin=content)


def collect_entries(node: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if "id" in node and "title" in node:
            out.append(node)
            for child in node.get("children", []):
                out.extend(collect_entries(child))
        else:
            for value in node.values():
                out.extend(collect_entries(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(collect_entries(item))
    return out


def entry_map(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in collect_entries(doc)}


def sort_key(entry: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(entry.get("deadline", "9999-12-31")),
        str(entry.get("created", "9999-12-31")),
        str(entry.get("title", "")),
    )


def initial_meta_for_section(section: str, doc: dict[str, Any]) -> list[list[str]]:
    entries = collect_entries(doc)
    if section == "actions":
        entries = [entry for entry in entries if entry.get("state") in {"incomplete", "inprogress"}]
    elif section == "triage":
        entries = [entry for entry in entries if entry.get("state") == "undecided"]
    else:
        raise PlanError(f"unknown section: {section}")
    entries.sort(key=sort_key)
    return [[entry["id"], "shown"] for entry in entries[:MAX_ITEMS]]


def parse_calendar_line(line: str) -> dict[str, str] | None:
    if not line.strip() or line.startswith("No"):
        return None
    calendar_match = re.search(r"\[calendar: ([^,]+),", line)
    calendar = calendar_match.group(1) if calendar_match else "Unknown"
    bracket_pos = line.rfind("[")
    if bracket_pos <= 0:
        return None
    parts = line[:bracket_pos].split()
    if len(parts) < 4:
        return None
    return {
        "time": parts[0],
        "title": " ".join(parts[3:]),
        "calendar": calendar,
        "line": line,
    }


def get_calendar_today() -> list[str]:
    output = run_dispatcher("g-calendar", "scripts-gcal", "agenda", "--all-calendars")
    return output.strip().splitlines() if output.strip() else []


def get_calendar_week() -> list[str]:
    output = run_dispatcher("g-calendar", "scripts-gcal", "agenda", "--all-calendars", "--days", "7")
    return output.strip().splitlines() if output.strip() else []


def get_weather() -> str:
    return run_dispatcher("get-weather", "scripts-weather").strip()


def calculate_free_time(events: list[dict[str, str]]) -> tuple[int, str]:
    total_hours = 10
    busy_hours = 1.5 * len(events)
    commute_hours = 1
    free_hours = max(0, total_hours - busy_hours - commute_hours)
    return int(free_hours), f"{int(busy_hours)}h activities + {commute_hours}h commute"


def build_base_plan(today_date: str, calendar_today: list[str], calendar_week: list[str], weather: str) -> str:
    lines = [f"# Plan: {today_date}", "", "## Calendar"]

    parsed_today = [parsed for parsed in (parse_calendar_line(line) for line in calendar_today) if parsed]
    if parsed_today:
        for item in parsed_today:
            lines.append(f"- {item['time']}: {item['title']}")
    else:
        lines.append("(no events today)")

    free_hours, breakdown = calculate_free_time(parsed_today)
    lines.extend(["", f"Free time: ~{free_hours}h (10h budget - {breakdown})", ""])

    lines.append("## The Day")
    if weather:
        sentences = weather.split(". ")
        text = ". ".join(sentences[:2]).strip()
        if text and not text.endswith("."):
            text += "."
        lines.append(text or "(weather unavailable)")
    else:
        lines.append("(weather unavailable)")
    lines.append("")

    lines.append("## Upcoming")
    upcoming: list[str] = []
    for line in calendar_week:
        parsed = parse_calendar_line(line)
        if parsed and "birthday" in parsed.get("calendar", "").lower():
            upcoming.append(f"- {parsed['title']}")
    lines.extend(upcoming or ["(none this week)"])
    lines.append("")

    for section in ("actions", "triage"):
        spec = SECTION_SPECS[section]
        lines.append(spec["header"])
        lines.append(f"<!-- BEGIN {spec['marker']} -->")
        lines.append(f"(refreshing {section})")
        lines.append(f"<!-- END {spec['marker']} -->")
        lines.append("")

    lines.append("→ Tell me which items you're keeping and I'll update the rendered plan.")
    lines.append("")
    return "\n".join(lines)


def resolve_section(
    section: str,
    section_meta: list[list[str]],
    doc: dict[str, Any],
) -> tuple[list[list[str]], list[tuple[int, int, str, dict[str, Any]]]]:
    if section not in SECTION_SPECS:
        raise PlanError(f"unknown section: {section}")
    by_id = entry_map(doc)
    new_meta: list[list[str]] = []
    visible: list[tuple[int, int, str, dict[str, Any]]] = []
    visible_index = 0
    for item_id, situation in section_meta:
        entry = by_id.get(item_id)
        if not entry:
            continue
        stored_index = len(new_meta)
        new_meta.append([item_id, situation])
        if situation == "shown":
            visible_index += 1
            visible.append((visible_index, stored_index, item_id, entry))
    return new_meta, visible


def render_entries(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as tmp:
        yaml.safe_dump(entries, tmp, allow_unicode=True, default_flow_style=False, sort_keys=False)
        tmp_path = tmp.name
    try:
        rendered = run_dispatcher("list-manager", "read-beautify", tmp_path, "--no-ids")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return rendered.rstrip()


def inject_block(plan_text: str, marker: str, content: str) -> str:
    pattern = re.compile(rf"<!-- BEGIN {marker} -->.*?<!-- END {marker} -->", re.DOTALL)
    replacement = f"<!-- BEGIN {marker} -->\n{content}\n<!-- END {marker} -->"
    return pattern.sub(replacement, plan_text)


def refresh_rendered_plan(
    date_key: str,
    plan_text: str | None = None,
    meta: dict[str, list[list[str]]] | None = None,
) -> str:
    current_plan = read_plan_text(date_key) if plan_text is None else plan_text
    current_meta = read_meta(date_key) if meta is None else meta
    docs = {spec["list"]: load_list_doc(spec["list"]) for spec in SECTION_SPECS.values()}
    for section, spec in SECTION_SPECS.items():
        new_meta, visible = resolve_section(section, current_meta.get(section, []), docs[spec["list"]])
        current_meta[section] = new_meta
        rendered = render_entries([entry for _, _, _, entry in visible])
        current_plan = inject_block(current_plan, spec["marker"], rendered or spec["none"])
    write_meta(date_key, current_meta)
    write_plan_text(date_key, current_plan)
    return current_plan


def generate_plan(date_key: str, forced_today: str | None = None) -> str:
    tasks = {
        "calendar_today": get_calendar_today,
        "calendar_week": get_calendar_week,
        "weather": get_weather,
        "todo": lambda: load_list_doc("todo"),
        "triage": lambda: load_list_doc("triage"),
    }
    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {name: executor.submit(func) for name, func in tasks.items()}
        for name, future in futures.items():
            try:
                results[name] = future.result()
            except PlanError:
                if name.startswith("calendar"):
                    results[name] = []
                elif name == "weather":
                    results[name] = ""
                else:
                    results[name] = {}

    today_label = forced_today or datetime.now().strftime("%B %d, %Y")
    meta = {
        "actions": initial_meta_for_section("actions", results.get("todo") or {}),
        "triage": initial_meta_for_section("triage", results.get("triage") or {}),
    }
    base_plan = build_base_plan(
        today_label,
        results.get("calendar_today") or [],
        results.get("calendar_week") or [],
        results.get("weather") or "",
    )
    write_meta(date_key, meta)
    return refresh_rendered_plan(date_key, base_plan, meta)


def parse_indices(spec: str) -> list[int]:
    values: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        idx = int(part)
        if idx < 1:
            raise PlanError("indices must be 1-based positive integers")
        values.append(idx)
    if not values:
        raise PlanError("no indices provided")
    return values


def apply_local_mutation(
    section_meta: list[list[str]],
    visible: list[tuple[int, int, str, dict[str, Any]]],
    operation: str,
    indices: list[int],
) -> list[list[str]]:
    index_map = {visible_idx: stored_idx for visible_idx, stored_idx, _, _ in visible}
    for idx in indices:
        if idx not in index_map:
            raise PlanError(f"visible index {idx} is not present")

    if operation == "hide":
        for idx in indices:
            section_meta[index_map[idx]][1] = "hidden"
    elif operation == "show":
        for idx in indices:
            section_meta[index_map[idx]][1] = "shown"
    elif operation == "keep":
        keep_set = set(indices)
        for visible_idx, stored_idx, _, _ in visible:
            section_meta[stored_idx][1] = "shown" if visible_idx in keep_set else "hidden"
    elif operation == "remove":
        for idx in sorted(indices, reverse=True):
            del section_meta[index_map[idx]]
    else:
        raise PlanError(f"unsupported local operation: {operation}")
    return section_meta


def visible_id_map(visible: list[tuple[int, int, str, dict[str, Any]]], indices: list[int]) -> dict[int, str]:
    mapping = {visible_idx: item_id for visible_idx, _, item_id, _ in visible}
    for idx in indices:
        if idx not in mapping:
            raise PlanError(f"visible index {idx} is not present")
    return mapping


def update_master_list(list_name: str, patches: list[dict[str, Any]]) -> None:
    raw = load_list_text(list_name)
    with tempfile.NamedTemporaryFile("w+", suffix=".yaml", delete=False, encoding="utf-8") as tmp:
        tmp.write(raw)
        tmp.flush()
        tmp_path = tmp.name
    try:
        patch_text = yaml.safe_dump(patches, allow_unicode=True, default_flow_style=False, sort_keys=False)
        run_dispatcher("list-manager", "update-list", tmp_path, stdin=patch_text)
        updated = Path(tmp_path).read_text(encoding="utf-8")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    write_list_text(list_name, updated)


def mutate_plan(
    date_key: str,
    command: str,
    section: str | None = None,
    indices: list[int] | None = None,
    value: str | None = None,
    item_id: str | None = None,
) -> str:
    if not plan_exists(date_key):
        raise PlanError(f"plan {date_key} does not exist")

    meta = read_meta(date_key)
    docs = {spec["list"]: load_list_doc(spec["list"]) for spec in SECTION_SPECS.values()}

    if command in {"hide", "show", "keep", "remove", "mark-done", "reject", "set-deadline"}:
        if section is None or indices is None:
            raise PlanError("section and indices are required")
        if section not in SECTION_SPECS:
            raise PlanError(f"unknown section: {section}")

        section_meta = meta.get(section, [])
        section_meta, visible = resolve_section(section, section_meta, docs[SECTION_SPECS[section]["list"]])
        meta[section] = section_meta

        if command in {"hide", "show", "keep", "remove"}:
            meta[section] = apply_local_mutation(section_meta, visible, command, indices)
        elif command == "mark-done":
            if section != "actions":
                raise PlanError("mark-done only applies to actions")
            id_map = visible_id_map(visible, indices)
            update_master_list("todo", [{"id": id_map[idx], "state": "complete"} for idx in indices])
            meta[section] = apply_local_mutation(section_meta, visible, "hide", indices)
        elif command == "reject":
            if section != "triage":
                raise PlanError("reject only applies to triage")
            id_map = visible_id_map(visible, indices)
            update_master_list("triage", [{"id": id_map[idx], "state": "rejected"} for idx in indices])
            meta[section] = apply_local_mutation(section_meta, visible, "hide", indices)
        elif command == "set-deadline":
            if not value:
                raise PlanError("set-deadline requires a YYYY-MM-DD value")
            id_map = visible_id_map(visible, indices)
            list_name = SECTION_SPECS[section]["list"]
            update_master_list(list_name, [{"id": id_map[idx], "deadline": value} for idx in indices])
    elif command == "add":
        if section is None or not item_id:
            raise PlanError("add requires section and item id")
        if section not in SECTION_SPECS:
            raise PlanError(f"unknown section: {section}")
        list_name = SECTION_SPECS[section]["list"]
        if item_id not in entry_map(docs[list_name]):
            raise PlanError(f"item id {item_id} not found in {list_name}")
        section_meta = meta.get(section, [])
        for row in section_meta:
            if row[0] == item_id:
                row[1] = "shown"
                break
        else:
            section_meta.append([item_id, "shown"])
        meta[section] = section_meta
    else:
        raise PlanError(f"unknown mutation command: {command}")

    return refresh_rendered_plan(date_key, meta=meta)
