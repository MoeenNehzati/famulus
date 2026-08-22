"""Controlled TOML file access for project-owned runtime code.

Python's stdlib can parse TOML with ``tomllib`` but does not provide a TOML
writer. This module centralizes the text-level TOML access that the project
still needs, so callers do not hand-roll TOML filenames, encodings, or scalar
string escaping at each call site.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
import errno
import hashlib
import json
import os
import re
import stat
import tempfile
import tomllib
from pathlib import Path
from types import TracebackType
from typing import Mapping, TextIO

from .atomic_files import (
    AtomicWriteError,
    atomic_compare_and_delete,
    atomic_compare_and_replace_bytes,
    read_regular_file_bytes,
)


class TomlManagedArrayError(ValueError):
    """A managed TOML array is malformed, ambiguous, or concurrently edited."""


@dataclass(frozen=True)
class TomlFileState:
    """Content identity and permission state without exposing user configuration."""

    path: Path
    sha256: str | None
    mode: int | None


@dataclass(frozen=True)
class ManagedArrayPlan:
    """A non-mutating, compare-before-replace managed-array transition."""

    path: Path
    current_sha256: str | None
    replacement_sha256: str | None
    mode: int
    introduced: tuple[str, ...]
    block_sha256: str | None
    created_file: bool
    created_table: bool
    created_key: bool
    _expected: bytes | None = field(repr=False)
    _replacement: bytes | None = field(repr=False)


@dataclass(frozen=True)
class ManagedArrayInspection:
    """Read-only structural evidence for one managed string-array block."""

    path: Path
    roots: tuple[str, ...]
    marker_values: tuple[str, ...]
    marker_within_array: bool
    block_sha256: str


class TomlFile:
    """Context manager for UTF-8 TOML files with parse validation on writes."""

    def __init__(self, path: Path, mode: str) -> None:
        self.path = path
        self.mode = mode
        self._file: TextIO | None = None

    def __enter__(self) -> TextIO:
        if _mode_may_write(self.mode):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = builtins.open(self.path, self.mode, encoding="utf-8")
        return self._file

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        assert self._file is not None
        self._file.close()
        if exc_type is None and _mode_may_write(self.mode):
            validate_toml_file(self.path)
        return False


def open(base: Path | str, name: str, mode: str = "r") -> TomlFile:
    """Open a TOML file under ``base`` using UTF-8 text mode.

    ``name`` must be a single TOML filename, not a path. This keeps all TOML
    filename construction inside the controlled TOML boundary.
    """
    if "b" in mode:
        raise ValueError("toml_io.open only supports text modes")
    return TomlFile(Path(base) / _validate_toml_filename(name), mode)


def validate_toml_file(path: Path) -> None:
    """Parse ``path`` as TOML, raising if the file is invalid."""
    tomllib.loads(path.read_text(encoding="utf-8"))


def toml_string(value: str | Path) -> str:
    """Serialize a scalar string value as a TOML-compatible basic string."""
    return json.dumps(str(value), ensure_ascii=False)


def key_value(key: str, value: str | Path) -> str:
    """Return one TOML ``key = value`` line for a scalar string value."""
    if not key.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise ValueError(f"unsupported TOML key: {key!r}")
    return f"{key} = {toml_string(value)}\n"


def profile_config_filename(agent: str) -> str:
    """Return the profile config filename for an agent name."""
    if not agent or "/" in agent or "\\" in agent:
        raise ValueError(f"invalid agent name: {agent!r}")
    return f"{agent}.config.toml"


def repository_config_filename() -> str:
    """Return the single repository configuration filename."""

    return "officina.toml"


def iter_profile_configs(directory: Path | str):
    """Yield tracked profile TOML files in a directory."""
    for path in sorted(Path(directory).iterdir()):
        if path.is_file() and path.name.endswith(".config.toml"):
            yield path


def _validate_toml_filename(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("TOML filename must be a string")
    if not name.endswith(".toml"):
        raise ValueError(f"expected a .toml filename: {name!r}")
    if not name or name in {".toml", ".config.toml"}:
        raise ValueError(f"invalid TOML filename: {name!r}")
    if "/" in name or "\\" in name or os.sep in name:
        raise ValueError(f"TOML filename must not contain path separators: {name!r}")
    if os.altsep and os.altsep in name:
        raise ValueError(f"TOML filename must not contain path separators: {name!r}")
    return name


def _mode_may_write(mode: str) -> bool:
    return any(flag in mode for flag in ("w", "a", "x", "+"))


_TABLE_RE = re.compile(
    r"(?m)^[ \t]*(?:\[\[[^\]\r\n]+\]\]|\[[^\]\r\n]+\])"
    r"[ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)"
)
_UNSUPPORTED_DIRECTORY_SYNC = {errno.EINVAL, errno.ENOTSUP, errno.EBADF}


def _identity(raw: bytes | None) -> str | None:
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def _read_optional_managed(path: Path) -> bytes | None:
    try:
        parent = path.parent.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise TomlManagedArrayError(
            f"TOML configuration parent is not a real directory: {path.parent}"
        )
    try:
        return read_regular_file_bytes(path, allowed_root=path.parent)
    except FileNotFoundError:
        return None
    except (OSError, AtomicWriteError) as exc:
        raise TomlManagedArrayError(f"cannot read TOML configuration {path}: {exc}") from exc


def managed_file_state(base: Path | str, name: str) -> TomlFileState:
    """Return exact content and permission identity for one managed TOML file."""
    path = Path(base) / _validate_toml_filename(name)
    raw = _read_optional_managed(path)
    if raw is None:
        return TomlFileState(path, None, None)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise TomlManagedArrayError(f"cannot stat TOML configuration {path}: {exc}") from exc
    return TomlFileState(path, _identity(raw), mode)


def _decode_toml(raw: bytes | None, *, path: Path) -> tuple[str, dict[str, object]]:
    try:
        text = "" if raw is None else raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TomlManagedArrayError(f"TOML configuration is not UTF-8: {path}") from exc
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TomlManagedArrayError(f"cannot parse TOML configuration {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TomlManagedArrayError(f"TOML configuration must be a table: {path}")
    return text, payload


def _managed_newline(text: str) -> str:
    without_crlf = text.replace("\r\n", "")
    return "\r\n" if "\r\n" in text and "\n" not in without_crlf else "\n"


def _syntax_mask(text: str) -> str:
    masked = list(text)
    state: str | None = None
    index = 0

    def blank(start: int, finish: int) -> None:
        for position in range(start, min(finish, len(masked))):
            if masked[position] not in "\r\n":
                masked[position] = " "

    while index < len(text):
        if state is None:
            if text.startswith('"""', index):
                blank(index, index + 3); state = "multiline-basic"; index += 3
            elif text.startswith("'''", index):
                blank(index, index + 3); state = "multiline-literal"; index += 3
            elif text[index] == '"':
                blank(index, index + 1); state = "basic"; index += 1
            elif text[index] == "'":
                blank(index, index + 1); state = "literal"; index += 1
            elif text[index] == "#":
                newline = text.find("\n", index)
                index = len(text) if newline < 0 else newline + 1
            else:
                index += 1
            continue
        if state == "multiline-basic" and text.startswith('"""', index):
            blank(index, index + 3); state = None; index += 3
        elif state == "multiline-literal" and text.startswith("'''", index):
            blank(index, index + 3); state = None; index += 3
        elif state == "basic" and text[index] == '"':
            blank(index, index + 1); state = None; index += 1
        elif state == "literal" and text[index] == "'":
            blank(index, index + 1); state = None; index += 1
        elif state in {"basic", "multiline-basic"} and text[index] == "\\":
            blank(index, index + 2); index += 2
        else:
            blank(index, index + 1); index += 1
    return "".join(masked)


def _marker_spans(text: str, marker: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.rstrip("\r\n").strip() == marker:
            spans.append((offset, offset + len(line)))
        offset += len(line)
    if offset < len(text) and text[offset:].strip() == marker:
        spans.append((offset, len(text)))
    return spans


def _array_bounds(text: str, start: int, *, path: Path) -> tuple[int, int]:
    index = start
    while index < len(text):
        if text[index] in " \t\r\n":
            index += 1; continue
        if text[index] == "#":
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline + 1
            continue
        break
    if index >= len(text) or text[index] != "[":
        raise TomlManagedArrayError(f"managed TOML value must be an array: {path}")
    opening = index
    depth = 0
    quote: str | None = None
    triple = escaped = in_comment = False
    while index < len(text):
        character = text[index]
        if in_comment:
            if character == "\n": in_comment = False
            index += 1; continue
        if quote is not None:
            if triple and text.startswith(quote * 3, index):
                quote = None; triple = False; index += 3; continue
            if not triple and character == quote and (quote == "'" or not escaped):
                quote = None; escaped = False; index += 1; continue
            escaped = quote == '"' and character == "\\" and not escaped
            if character != "\\": escaped = False
            index += 1; continue
        if character == "#": in_comment = True
        elif character in {'"', "'"}:
            quote = character; triple = text.startswith(character * 3, index)
            index += 3 if triple else 1; continue
        elif character == "[": depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0: return opening, index
        index += 1
    raise TomlManagedArrayError(f"managed TOML array is incomplete: {path}")


def _parse_string_fragment(fragment: str, *, path: Path) -> list[str]:
    try:
        values = tomllib.loads("values = [\n" + fragment + "\n]")["values"]
    except tomllib.TOMLDecodeError as exc:
        raise TomlManagedArrayError(f"managed TOML marker content is malformed in {path}: {exc}") from exc
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise TomlManagedArrayError(f"managed TOML marker must contain only strings: {path}")
    return values


def _body_end(text: str, body_start: int, next_table_start: int | None) -> int:
    if next_table_start is None: return len(text)
    last = body_start
    offset = body_start
    for line in text[body_start:next_table_start].splitlines(keepends=True):
        if line.strip() and not line.strip().startswith("#"): last = offset + len(line)
        offset += len(line)
    return last


def _patterns(table: str, key: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    table_re = re.compile(rf"(?m)^[ \t]*\[{re.escape(table)}\][ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)")
    key_re = re.compile(rf"(?m)^(?P<indent>[ \t]*){re.escape(key)}[ \t]*=")
    return table_re, key_re


def _marker_lines(values: list[str], *, begin: str, end: str, indent: str) -> list[str]:
    return [indent + begin, *(indent + toml_string(value) + "," for value in values), indent + end]


def _render_array(foreign: list[str], introduced: list[str], *, key: str, begin: str, end: str, indent: str, newline: str) -> str:
    child = indent + "  "
    lines = [indent + key + " = ["]
    lines.extend(child + toml_string(value) + "," for value in foreign)
    lines.extend(_marker_lines(introduced, begin=begin, end=end, indent=child))
    lines.append(indent + "]")
    return newline.join(lines)


def _can_append(fragment: str, *, newline: str) -> bool:
    separator = "" if not fragment or fragment.endswith(("\n", "\r")) else newline
    probe = "__famulus_append_probe__"
    try:
        values = tomllib.loads("values = [\n" + fragment + separator + toml_string(probe) + ",\n]")["values"]
    except tomllib.TOMLDecodeError:
        return False
    return isinstance(values, list) and values[-1:] == [probe]


def _appendable_multiline_fragment(fragment: str, *, newline: str) -> str | None:
    if _can_append(fragment, newline=newline):
        return fragment
    syntax = _syntax_mask(fragment)
    offset = 0
    candidates: list[tuple[int, str, str]] = []
    for raw_line, masked_line in zip(
        fragment.splitlines(keepends=True), syntax.splitlines(keepends=True), strict=True
    ):
        if raw_line.strip() and not raw_line.lstrip().startswith("#"):
            candidates.append((offset, raw_line, masked_line))
        offset += len(raw_line)
    if not candidates:
        return None
    line_offset, raw_line, masked_line = candidates[-1]
    comment = masked_line.find("#")
    value_end = len(raw_line.rstrip("\r\n")) if comment < 0 else comment
    while value_end > 0 and raw_line[value_end - 1] in " \t":
        value_end -= 1
    if value_end == 0 or raw_line[value_end - 1] == ",":
        return None
    insertion = line_offset + value_end
    candidate = fragment[:insertion] + "," + fragment[insertion:]
    return candidate if _can_append(candidate, newline=newline) else None


def _transform_managed_array(
    raw: bytes | None,
    *,
    path: Path,
    table_name: str,
    key_name: str,
    required: list[str],
    prior: Mapping[str, object] | None,
    begin: str,
    end: str,
) -> tuple[bytes, dict[str, object]]:
    text, payload = _decode_toml(raw, path=path)
    syntax = _syntax_mask(text)
    newline = _managed_newline(text)
    begin_spans = _marker_spans(syntax, begin)
    end_spans = _marker_spans(syntax, end)
    if syntax.count(begin) != len(begin_spans) or syntax.count(end) != len(end_spans):
        raise TomlManagedArrayError(f"managed TOML marker must occupy its own line: {path}")
    if len(begin_spans) != len(end_spans) or len(begin_spans) > 1:
        raise TomlManagedArrayError(f"managed TOML marker arrangement is ambiguous: {path}")
    pending_pre = bool(
        prior and prior.get("transaction") == "pending" and _identity(raw) == prior.get("pre_sha256")
    )
    if begin_spans and prior is None:
        raise TomlManagedArrayError(f"managed TOML marker has no matching ownership: {path}")
    if prior is not None and not begin_spans and not pending_pre:
        raise TomlManagedArrayError(f"managed TOML marker content was modified: {path}")
    if begin_spans:
        current_block = text[begin_spans[0][0] : end_spans[0][1]].encode("utf-8")
        if not isinstance(prior.get("block_sha256"), str) or hashlib.sha256(current_block).hexdigest() != prior["block_sha256"]:
            raise TomlManagedArrayError(f"managed TOML marker content was modified: {path}")

    table_re, key_re = _patterns(table_name, key_name)
    table_matches = list(table_re.finditer(syntax))
    target = payload.get(table_name)
    if target is not None and not isinstance(target, dict):
        raise TomlManagedArrayError(f"TOML {table_name} must be a table: {path}")
    if len(table_matches) > 1:
        raise TomlManagedArrayError(f"TOML {table_name} table is duplicated: {path}")
    if not table_matches:
        if target is not None or begin_spans or end_spans:
            raise TomlManagedArrayError(f"managed TOML target is ambiguous: {path}")
        introduced = list(required)
        block = _render_array([], introduced, key=key_name, begin=begin, end=end, indent="", newline=newline)
        prefix = text
        if prefix and not prefix.endswith(("\n", "\r")): prefix += newline
        if prefix and prefix.strip(): prefix += newline
        result = prefix + f"[{table_name}]" + newline + block + newline
        return result.encode("utf-8"), {
            "introduced": introduced, "created_table": True, "created_key": True,
        }

    table = table_matches[0]
    next_table = next(_TABLE_RE.finditer(syntax, table.end()), None)
    table_end = _body_end(text, table.end(), None if next_table is None else next_table.start())
    key_matches = list(key_re.finditer(syntax, table.end(), table_end))
    target_table = target if isinstance(target, dict) else {}
    target_value = target_table.get(key_name)
    if len(key_matches) > 1:
        raise TomlManagedArrayError(f"TOML {key_name} key is duplicated: {path}")
    if not key_matches:
        if target_value is not None:
            raise TomlManagedArrayError(f"TOML {key_name} uses an ambiguous key form: {path}")
        if begin_spans or end_spans:
            raise TomlManagedArrayError(f"managed TOML marker is outside {key_name}: {path}")
        introduced = list(required)
        block = _render_array([], introduced, key=key_name, begin=begin, end=end, indent="", newline=newline) + newline
        insertion = table_end
        if text[:insertion] and not text[:insertion].endswith(("\n", "\r")): block = newline + block
        result = text[:insertion] + block + text[insertion:]
        return result.encode("utf-8"), {
            "introduced": introduced,
            "created_table": bool(prior and prior.get("created_table")),
            "created_key": True,
        }

    if not isinstance(target_value, list) or any(not isinstance(value, str) for value in target_value):
        raise TomlManagedArrayError(f"TOML {table_name}.{key_name} must contain only strings: {path}")
    key = key_matches[0]
    opening, closing = _array_bounds(text, key.end(), path=path)
    if closing >= table_end:
        raise TomlManagedArrayError(f"TOML {key_name} escapes its selected table: {path}")
    content_start, content_end = opening + 1, closing
    if begin_spans:
        begin_start, begin_end = begin_spans[0]
        end_start, end_end = end_spans[0]
        if not (content_start <= begin_start < begin_end <= end_start < end_end <= content_end):
            raise TomlManagedArrayError(f"managed TOML marker is outside or malformed in {key_name}: {path}")
        foreign = _parse_string_fragment(text[content_start:begin_start], path=path)
        owned = _parse_string_fragment(text[begin_end:end_start], path=path)
        foreign.extend(_parse_string_fragment(text[end_end:content_end], path=path))
        if len(owned) != len(set(owned)):
            raise TomlManagedArrayError(f"managed TOML marker content was modified: {path}")
    else:
        foreign = list(target_value)
    introduced = [value for value in required if value not in set(foreign)]
    marker_lines = _marker_lines(introduced, begin=begin, end=end, indent=key.group("indent") + "  ")
    if begin_spans:
        replacement = newline.join(marker_lines)
        if text[end_start:end_end].endswith(("\n", "\r")): replacement += newline
        result = text[:begin_start] + replacement + text[end_end:]
    else:
        content = text[content_start:content_end]
        appendable = (
            _appendable_multiline_fragment(content, newline=newline)
            if "\n" in text[opening : closing + 1]
            else None
        )
        if appendable is not None:
            content = appendable
            separator = "" if not content or content.endswith(("\n", "\r")) else newline
            replacement = content + separator + newline.join(marker_lines) + newline
            result = text[:content_start] + replacement + text[content_end:]
        else:
            replacement = _render_array(foreign, introduced, key=key_name, begin=begin, end=end, indent=key.group("indent"), newline=newline)
            result = text[:key.start()] + replacement + text[closing + 1:]
    return result.encode("utf-8"), {
        "introduced": introduced,
        "created_table": bool(prior and prior.get("created_table")),
        "created_key": bool(prior and prior.get("created_key")),
    }


def _locate_owned_block(raw: bytes, *, path: Path, table_name: str, key_name: str, begin: str, end: str) -> tuple[int, int, bytes, tuple[str, ...], tuple[str, ...]]:
    text, payload = _decode_toml(raw, path=path)
    syntax = _syntax_mask(text)
    begins, ends = _marker_spans(syntax, begin), _marker_spans(syntax, end)
    if syntax.count(begin) != len(begins) or syntax.count(end) != len(ends) or len(begins) != 1 or len(ends) != 1 or begins[0][0] >= ends[0][0]:
        raise TomlManagedArrayError(f"managed TOML marker arrangement is ambiguous: {path}")
    table_re, key_re = _patterns(table_name, key_name)
    tables = list(table_re.finditer(syntax))
    target = payload.get(table_name)
    if len(tables) != 1 or not isinstance(target, dict):
        raise TomlManagedArrayError(f"TOML {table_name} table is missing or ambiguous: {path}")
    table = tables[0]
    next_table = next(_TABLE_RE.finditer(syntax, table.end()), None)
    table_end = len(text) if next_table is None else next_table.start()
    keys = list(key_re.finditer(syntax, table.end(), table_end))
    values = target.get(key_name)
    if len(keys) != 1 or not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise TomlManagedArrayError(f"TOML {table_name}.{key_name} is missing or ambiguous: {path}")
    opening, closing = _array_bounds(text, keys[0].end(), path=path)
    begin_start, begin_end = begins[0]; end_start, end_end = ends[0]
    if not (closing < table_end and opening < begin_start < begin_end <= end_start < end_end <= closing):
        raise TomlManagedArrayError(f"managed TOML marker is outside or malformed in {key_name}: {path}")
    start = len(text[:begin_start].encode("utf-8")); finish = len(text[:end_end].encode("utf-8"))
    marker_values = tuple(_parse_string_fragment(text[begin_end:end_start], path=path))
    return start, finish, raw[start:finish], tuple(values), marker_values


def plan_managed_string_array_update(
    base: Path | str,
    name: str,
    *,
    table_name: str,
    key_name: str,
    required: list[str],
    prior: Mapping[str, object] | None,
    begin: str,
    end: str,
) -> ManagedArrayPlan:
    """Plan a syntax-preserving managed string-array update without mutation."""
    path = Path(base) / _validate_toml_filename(name)
    current = _read_optional_managed(path)
    replacement, ownership = _transform_managed_array(
        current, path=path, table_name=table_name, key_name=key_name,
        required=required, prior=prior, begin=begin, end=end,
    )
    try:
        _text, _payload = _decode_toml(replacement, path=path)
        _start, _finish, block, _roots, _marker_values = _locate_owned_block(
            replacement, path=path, table_name=table_name, key_name=key_name,
            begin=begin, end=end,
        )
    except TomlManagedArrayError:
        raise
    created_file = current is None or bool(prior and prior.get("created_file"))
    if current is None:
        mode = 0o600
    else:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError as exc:
            raise TomlManagedArrayError(f"cannot stat TOML configuration {path}: {exc}") from exc
    return ManagedArrayPlan(
        path=path,
        current_sha256=_identity(current),
        replacement_sha256=_identity(replacement),
        mode=mode,
        introduced=tuple(ownership["introduced"]),
        block_sha256=hashlib.sha256(block).hexdigest(),
        created_file=created_file,
        created_table=bool(ownership["created_table"]),
        created_key=bool(ownership["created_key"]),
        _expected=current,
        _replacement=replacement,
    )


def _remove_empty_scaffolding(raw: bytes, *, path: Path, table_name: str, key_name: str, created_key: bool, created_table: bool) -> bytes:
    if not created_key: return raw
    text, payload = _decode_toml(raw, path=path)
    target = payload.get(table_name)
    if not isinstance(target, dict) or target.get(key_name) != []: return raw
    table_re, key_re = _patterns(table_name, key_name)
    syntax = _syntax_mask(text)
    tables = list(table_re.finditer(syntax))
    if len(tables) != 1: return raw
    table = tables[0]
    next_table = next(_TABLE_RE.finditer(syntax, table.end()), None)
    table_end = len(text) if next_table is None else next_table.start()
    keys = list(key_re.finditer(syntax, table.end(), table_end))
    if len(keys) != 1: return raw
    opening, closing = _array_bounds(text, keys[0].end(), path=path)
    if text[opening + 1:closing].strip(): return raw
    key_end = text.find("\n", closing)
    key_end = len(text) if key_end < 0 else key_end + 1
    text = text[:keys[0].start()] + text[key_end:]
    if not created_table: return text.encode("utf-8")
    syntax = _syntax_mask(text); tables = list(table_re.finditer(syntax))
    if len(tables) != 1: return text.encode("utf-8")
    table = tables[0]; next_table = next(_TABLE_RE.finditer(syntax, table.end()), None)
    table_end = len(text) if next_table is None else next_table.start()
    if text[table.end():table_end].strip(): return text.encode("utf-8")
    prefix = text[:table.start()]
    if prefix.endswith("\r\n"):
        prefix = prefix[:-2]
    elif prefix.endswith("\n"):
        prefix = prefix[:-1]
    return (prefix + text[table_end:]).encode("utf-8")


def plan_managed_string_array_removal(
    base: Path | str,
    name: str,
    *,
    table_name: str,
    key_name: str,
    ownership: Mapping[str, object],
) -> ManagedArrayPlan:
    """Plan removal of one identity-proven managed block without mutation."""
    path = Path(base) / _validate_toml_filename(name)
    current = _read_optional_managed(path)
    if current is None:
        raise TomlManagedArrayError(f"managed TOML configuration does not exist: {path}")
    begin, end = ownership.get("begin"), ownership.get("end")
    if not isinstance(begin, str) or not isinstance(end, str):
        raise TomlManagedArrayError(f"managed TOML ownership markers are malformed: {path}")
    start, finish, block, _roots, marker_values = _locate_owned_block(
        current, path=path, table_name=table_name, key_name=key_name, begin=begin, end=end,
    )
    if hashlib.sha256(block).hexdigest() != ownership.get("block_sha256"):
        raise TomlManagedArrayError(f"managed TOML block was modified: {path}")
    replacement = _remove_empty_scaffolding(
        current[:start] + current[finish:], path=path,
        table_name=table_name, key_name=key_name,
        created_key=bool(ownership.get("created_key")),
        created_table=bool(ownership.get("created_table")),
    )
    if ownership.get("created_file") and not replacement.strip(): replacement_or_none = None
    else: replacement_or_none = replacement
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise TomlManagedArrayError(f"cannot stat TOML configuration {path}: {exc}") from exc
    return ManagedArrayPlan(
        path=path, current_sha256=_identity(current), replacement_sha256=_identity(replacement_or_none),
        mode=mode, introduced=marker_values, block_sha256=hashlib.sha256(block).hexdigest(),
        created_file=bool(ownership.get("created_file")),
        created_table=bool(ownership.get("created_table")),
        created_key=bool(ownership.get("created_key")),
        _expected=current, _replacement=replacement_or_none,
    )


def inspect_managed_string_array(base: Path | str, name: str, *, table_name: str, key_name: str, begin: str, end: str) -> ManagedArrayInspection:
    """Inspect one managed block without exposing or mutating unrelated TOML content."""
    path = Path(base) / _validate_toml_filename(name)
    raw = _read_optional_managed(path)
    if raw is None: raise TomlManagedArrayError(f"managed TOML configuration does not exist: {path}")
    _start, _finish, block, roots, marker_values = _locate_owned_block(
        raw, path=path, table_name=table_name, key_name=key_name, begin=begin, end=end,
    )
    return ManagedArrayInspection(path, roots, marker_values, True, hashlib.sha256(block).hexdigest())


def _sync_managed_directory(path: Path) -> None:
    if os.name != "posix": return
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        if exc.errno in _UNSUPPORTED_DIRECTORY_SYNC: return
        raise
    try:
        try: os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED_DIRECTORY_SYNC: raise
    finally: os.close(descriptor)


def apply_managed_array_plan(plan: ManagedArrayPlan) -> None:
    """Apply a planned TOML transition only if bytes and mode are unchanged."""
    path, expected, replacement, mode = plan.path, plan._expected, plan._replacement, plan.mode
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if replacement is None:
            if expected is None:
                raise TomlManagedArrayError(f"TOML removal has no predecessor: {path}")
            atomic_compare_and_delete(
                path,
                expected_previous_bytes=expected,
                expected_previous_mode=mode,
                allowed_root=path.parent,
            )
        else:
            atomic_compare_and_replace_bytes(
                path,
                replacement,
                expected_previous_bytes=expected,
                expected_previous_mode=None if expected is None else mode,
                allowed_root=path.parent,
                mode=mode,
            )
    except AtomicWriteError as exc:
        action = "atomic removal" if replacement is None else "atomic replacement"
        raise TomlManagedArrayError(
            f"TOML configuration changed before {action}: {path}: {exc}"
        ) from exc
