"""Search skill blueprint YAML files with structured filters and projections.

The public entry point is :func:`search_blueprints`. It is intentionally
JSON-oriented: callers provide a plain Python query mapping and receive a list
of JSON-ready dictionaries. The CLI wrapper in ``scripts/search_blueprints.py``
only handles argument parsing, query-file loading, and JSON emission.

Query shape::

    {
        "filter": {
            "all": [
                {"path": "cross_platform", "op": "eq", "value": True},
                {
                    "any": [
                        {"path": "category", "op": "regex", "pattern": "development"},
                        {
                            "path": "interfaces.machine.*.invocation.kind",
                            "op": "regex",
                            "pattern": "python",
                            "flags": "i",
                        },
                    ]
                },
            ]
        },
        "select": [
            "skill",
            "path",
            "category",
            {"as": "runtimes", "path": "interfaces.machine.*.invocation.kind"},
        ],
        "comments": "drop",
        "explain": True,
    }

Filters support ``all`` (AND), ``any`` (OR), ``not``, and predicate nodes.
Selectors use dotted paths with ``*`` wildcards over mapping values or list
items. PyYAML drops comments when parsing; ``comments: raw`` includes the
original source text under ``raw`` instead of attempting comment-preserving
structured fragments.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import yaml


class BlueprintSearchError(ValueError):
    """Raised when blueprint discovery, parsing, or query evaluation fails."""


@dataclass(frozen=True)
class BlueprintRecord:
    """One parsed ``skills/<skill>/blueprint.yaml`` file."""

    skill: str
    path: str
    data: dict[str, Any]
    raw: str


@dataclass(frozen=True)
class MatchEvidence:
    """The concrete selected value that made a predicate true."""

    selector: str
    op: str
    path: str
    value: Any


def iter_blueprints(
    repo_root: Path | str,
    *,
    include_hidden: bool = False,
) -> Iterator[BlueprintRecord]:
    """Yield parsed blueprint records sorted by skill name.

    Discovery is intentionally limited to ``skills/*/blueprint.yaml``. Hidden
    skill directories are skipped by default because repo-local skills are the
    intended search surface.
    """

    root = Path(repo_root)
    skills_dir = root / "skills"
    if not skills_dir.exists():
        raise BlueprintSearchError(f"{skills_dir}: missing skills directory")

    for blueprint_path in sorted(skills_dir.glob("*/blueprint.yaml"), key=_skill_sort_key):
        skill = blueprint_path.parent.name
        if skill.startswith(".") and not include_hidden:
            continue
        raw = blueprint_path.read_text(encoding="utf-8")
        try:
            loaded = yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:
            rel = _relative_path(blueprint_path, root)
            raise BlueprintSearchError(f"{rel}: invalid YAML: {exc}") from exc
        if not isinstance(loaded, dict):
            rel = _relative_path(blueprint_path, root)
            raise BlueprintSearchError(f"{rel}: top-level YAML value must be a mapping")
        yield BlueprintRecord(
            skill=skill,
            path=_relative_path(blueprint_path, root),
            data=loaded,
            raw=raw,
        )


def select_values(data: Any, selector: str) -> list[tuple[str, Any]]:
    """Resolve a dotted selector against parsed YAML data.

    ``*`` expands all mapping values or list items. Numeric path segments select
    list indexes. Missing selectors return an empty list.
    """

    if not selector:
        raise BlueprintSearchError("selector must not be empty")
    if selector.startswith("$"):
        raise BlueprintSearchError(f"{selector}: built-in selectors are handled at transform time")

    values: list[tuple[str, Any]] = [("", data)]
    for part in selector.split("."):
        next_values: list[tuple[str, Any]] = []
        for current_path, current_value in values:
            next_values.extend(_select_child(current_path, current_value, part))
        values = next_values
        if not values:
            break
    return values


def matches_filter(
    record: BlueprintRecord,
    filter_spec: Mapping[str, Any] | Sequence[Any] | None,
) -> tuple[bool, list[MatchEvidence]]:
    """Evaluate a filter spec against one record.

    ``None`` and empty mappings match every record. A list is treated as an
    ``all`` group for convenience in import callers.
    """

    if filter_spec is None or filter_spec == {}:
        return True, []
    if isinstance(filter_spec, Sequence) and not isinstance(filter_spec, (str, bytes)):
        return _match_all(record, filter_spec)
    if not isinstance(filter_spec, Mapping):
        raise BlueprintSearchError("filter must be a mapping, list, or null")

    if "all" in filter_spec:
        children = filter_spec["all"]
        if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
            raise BlueprintSearchError("filter.all must be a list")
        return _match_all(record, children)

    if "any" in filter_spec:
        children = filter_spec["any"]
        if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
            raise BlueprintSearchError("filter.any must be a list")
        for child in children:
            matched, evidence = matches_filter(record, child)
            if matched:
                return True, evidence
        return False, []

    if "not" in filter_spec:
        matched, _evidence = matches_filter(record, filter_spec["not"])
        if matched:
            return False, []
        return True, [MatchEvidence(selector="$not", op="not", path="", value=True)]

    return _match_predicate(record, filter_spec)


def transform_record(
    record: BlueprintRecord,
    select_spec: str | Sequence[Any] | None,
    *,
    comments: str = "drop",
    matches: Sequence[MatchEvidence] = (),
    explain: bool = False,
) -> dict[str, Any]:
    """Project a record into a JSON-ready result row."""

    if comments not in {"drop", "raw"}:
        raise BlueprintSearchError("comments must be one of: drop, raw")

    if select_spec is None:
        select_spec = ["skill", "path"]

    if select_spec == "all":
        row: dict[str, Any] = {
            "skill": record.skill,
            "path": record.path,
            "data": record.data,
        }
    else:
        if not isinstance(select_spec, Sequence) or isinstance(select_spec, (str, bytes)):
            raise BlueprintSearchError("select must be 'all', a list, or null")
        row = {"skill": record.skill, "path": record.path, "values": {}}
        values = row["values"]
        for item in select_spec:
            _apply_selector(record, item, values, row)
        if not values:
            del row["values"]

    if comments == "raw":
        row["raw"] = record.raw
    if explain:
        row["matches"] = [asdict(match) for match in matches]
    return row


def search_blueprints(
    repo_root: Path | str,
    query: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Discover, filter, and transform skill blueprints.

    Args:
        repo_root: Repository root containing ``skills/``.
        query: Plain mapping with optional ``filter``, ``select``, ``comments``,
            ``explain``, and ``include_hidden`` keys.

    Returns:
        A JSON-ready list of result dictionaries.
    """

    query = query or {}
    if not isinstance(query, Mapping):
        raise BlueprintSearchError("query must be a mapping")

    filter_spec = query.get("filter")
    select_spec = query.get("select")
    comments = query.get("comments", "drop")
    explain = bool(query.get("explain", False))
    include_hidden = bool(query.get("include_hidden", False))

    rows: list[dict[str, Any]] = []
    for record in iter_blueprints(repo_root, include_hidden=include_hidden):
        matched, evidence = matches_filter(record, filter_spec)
        if not matched:
            continue
        rows.append(
            transform_record(
                record,
                select_spec,
                comments=comments,
                matches=evidence,
                explain=explain,
            )
        )
    return rows


def load_query_file(path: Path | str) -> dict[str, Any]:
    """Load a YAML or JSON query file as a mapping."""

    query_path = Path(path)
    try:
        loaded = yaml.safe_load(query_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BlueprintSearchError(f"{query_path}: invalid query YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise BlueprintSearchError(f"{query_path}: query file must contain a mapping")
    return loaded


def _skill_sort_key(path: Path) -> str:
    return path.parent.name


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _select_child(path: str, value: Any, part: str) -> list[tuple[str, Any]]:
    if part == "*":
        if isinstance(value, Mapping):
            return [(_join_path(path, str(key)), child) for key, child in value.items()]
        if isinstance(value, list):
            return [(_join_path(path, str(index)), child) for index, child in enumerate(value)]
        return []

    if isinstance(value, Mapping):
        if part in value:
            return [(_join_path(path, part), value[part])]
        return []

    if isinstance(value, list) and part.isdigit():
        index = int(part)
        if 0 <= index < len(value):
            return [(_join_path(path, part), value[index])]
    return []


def _join_path(parent: str, child: str) -> str:
    return child if not parent else f"{parent}.{child}"


def _match_all(record: BlueprintRecord, children: Sequence[Any]) -> tuple[bool, list[MatchEvidence]]:
    evidence: list[MatchEvidence] = []
    for child in children:
        matched, child_evidence = matches_filter(record, child)
        if not matched:
            return False, []
        evidence.extend(child_evidence)
    return True, evidence


def _match_predicate(
    record: BlueprintRecord,
    predicate: Mapping[str, Any],
) -> tuple[bool, list[MatchEvidence]]:
    selector = predicate.get("path")
    op = predicate.get("op", "exists")
    if not isinstance(selector, str) or not selector:
        raise BlueprintSearchError("predicate requires non-empty string path")
    if not isinstance(op, str):
        raise BlueprintSearchError("predicate op must be a string")

    values = select_values(record.data, selector)

    if op == "exists":
        if values:
            return True, [
                MatchEvidence(selector=selector, op=op, path=path, value=value)
                for path, value in values
            ]
        return False, []

    if op == "missing":
        if not values:
            return True, [MatchEvidence(selector=selector, op=op, path=selector, value=None)]
        return False, []

    if op in {"eq", "neq", "contains"}:
        expected = predicate.get("value")
        return _match_value_predicate(selector, op, expected, values)

    if op in {"regex", "not_regex"}:
        pattern = predicate.get("pattern")
        if not isinstance(pattern, str):
            raise BlueprintSearchError(f"{selector}: regex predicate requires string pattern")
        flags = _regex_flags(predicate.get("flags", ""))
        regex = re.compile(pattern, flags)
        return _match_regex_predicate(selector, op, regex, values)

    raise BlueprintSearchError(f"{selector}: unsupported filter op {op!r}")


def _match_value_predicate(
    selector: str,
    op: str,
    expected: Any,
    values: Sequence[tuple[str, Any]],
) -> tuple[bool, list[MatchEvidence]]:
    evidence: list[MatchEvidence] = []
    for path, value in values:
        if op == "eq" and value == expected:
            evidence.append(MatchEvidence(selector=selector, op=op, path=path, value=value))
        elif op == "neq" and value != expected:
            evidence.append(MatchEvidence(selector=selector, op=op, path=path, value=value))
        elif op == "contains" and _contains(value, expected):
            evidence.append(MatchEvidence(selector=selector, op=op, path=path, value=value))
    return bool(evidence), evidence


def _match_regex_predicate(
    selector: str,
    op: str,
    regex: re.Pattern[str],
    values: Sequence[tuple[str, Any]],
) -> tuple[bool, list[MatchEvidence]]:
    evidence: list[MatchEvidence] = []
    for path, value in values:
        matched = regex.search(_stringify_for_regex(value)) is not None
        if (op == "regex" and matched) or (op == "not_regex" and not matched):
            evidence.append(MatchEvidence(selector=selector, op=op, path=path, value=value))
    return bool(evidence), evidence


def _regex_flags(raw_flags: Any) -> int:
    if raw_flags in (None, ""):
        return 0
    if not isinstance(raw_flags, str):
        raise BlueprintSearchError("regex flags must be a string")
    flags = 0
    for flag in raw_flags:
        if flag == "i":
            flags |= re.IGNORECASE
        elif flag == "m":
            flags |= re.MULTILINE
        elif flag == "s":
            flags |= re.DOTALL
        else:
            raise BlueprintSearchError(f"unsupported regex flag {flag!r}")
    return flags


def _contains(value: Any, expected: Any) -> bool:
    if isinstance(value, Mapping):
        return expected in value
    if isinstance(value, (list, tuple, set)):
        return expected in value
    if isinstance(value, str):
        return str(expected) in value
    return False


def _stringify_for_regex(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _apply_selector(
    record: BlueprintRecord,
    item: Any,
    values: dict[str, Any],
    row: dict[str, Any],
) -> None:
    if isinstance(item, str):
        if item == "skill":
            row["skill"] = record.skill
        elif item == "path":
            row["path"] = record.path
        elif item == "$data":
            row["data"] = record.data
        elif item == "$raw":
            row["raw"] = record.raw
        else:
            selected = select_values(record.data, item)
            values[item] = _collapse_selected_values(selected, force_list="*" in item)
        return

    if isinstance(item, Mapping):
        alias = item.get("as")
        selector = item.get("path")
        if not isinstance(alias, str) or not alias:
            raise BlueprintSearchError("select mapping requires non-empty string 'as'")
        if not isinstance(selector, str) or not selector:
            raise BlueprintSearchError(f"select mapping {alias!r} requires non-empty string 'path'")
        if selector == "$data":
            values[alias] = record.data
        elif selector == "$raw":
            values[alias] = record.raw
        else:
            values[alias] = _collapse_selected_values(
                select_values(record.data, selector),
                force_list="*" in selector,
            )
        return

    raise BlueprintSearchError("select entries must be strings or mappings")


def _collapse_selected_values(
    selected: Sequence[tuple[str, Any]],
    *,
    force_list: bool = False,
) -> Any:
    if not selected:
        return []
    if len(selected) == 1 and not force_list:
        return selected[0][1]
    return [value for _path, value in selected]
