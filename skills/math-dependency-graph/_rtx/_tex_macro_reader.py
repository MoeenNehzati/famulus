#!/usr/bin/env python3
"""Extract the graph-relevant TeX macro closure in MathJax-native form."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable, Iterator

from officina.runtime.python_machine_interface import PythonArgvMachineInterface


MacroValue = str | list[object]


LOCAL_INCLUDE_RE = re.compile(
    r"\\(?P<cmd>input|include|usepackage|RequirePackage(?:WithOptions)?|"
    r"documentclass|LoadClass(?:WithOptions)?)"
    r"(?:\s*\[[^\]]*\])?"
    r"\s*\{(?P<names>[^{}]+)\}"
)
MATHJAX_ADAPTER_DEPENDENCY_RE = re.compile(
    r"\\LWR@origRequirePackage(?:\s*\[[^\]]*\])?\s*\{(?P<names>[^{}]+)\}"
)
TEX_CONDITIONAL_COMMANDS = {
    "if",
    "ifcase",
    "ifcat",
    "ifcsname",
    "ifdefined",
    "ifdim",
    "ifeof",
    "iffalse",
    "iffontchar",
    "ifhbox",
    "ifhmode",
    "ifincsname",
    "ifinner",
    "ifmmode",
    "ifnum",
    "ifodd",
    "iftrue",
    "ifvbox",
    "ifvmode",
    "ifvoid",
    "ifx",
}
COMMAND_RE = re.compile(r"\\([A-Za-z@]+|.)")
LET_RE = re.compile(
    r"\\let\s*\\(?P<left>[A-Za-z@]+)\s*(?:=\s*)?\\(?P<right>[A-Za-z@]+)"
)
DECLARE_OP_RE = re.compile(r"\\DeclareMathOperator\*?\s*\{\\([A-Za-z@]+)\}")
DECLARE_MATH_SYMBOL_RE = re.compile(
    r"\\DeclareMathSymbol\s*\{\\(?P<name>[A-Za-z@]+)\}\s*"
    r"\{(?P<math_class>[^{}]*)\}\s*\{(?P<font>[^{}]*)\}\s*"
    r"\{(?P<slot>[^{}]+)\}"
)
DECLARE_SYMBOL_FONT_RE = re.compile(
    r"\\DeclareSymbolFont\s*\{(?P<name>[^{}]+)\}\s*"
    r"\{(?P<encoding>[^{}]+)\}\s*\{(?P<family>[^{}]+)\}\s*"
    r"\{(?P<series>[^{}]+)\}\s*\{(?P<shape>[^{}]+)\}"
)
DEF_RE = re.compile(r"\\(?P<kind>gdef|def)\s*\\(?P<name>[A-Za-z@]+)")
NEWCOMMAND_RE = re.compile(
    r"\\(?P<kind>(?:re)?newcommand|providecommand)\s*\*?\s*"
)
UNSUPPORTED_NAMED_DECLARATION_RE = re.compile(
    r"\\(?P<kind>DeclareRobustCommand)\s*\*?\s*"
)


@dataclass(frozen=True)
class MacroDefinition:
    """Store one normalized macro definition and its source provenance.

    Intent
    ------
    Carry a renderable value or declaration error with exact source provenance.

    Rationale
    ---------
    Conflict and cycle errors need provenance that the public macro map omits.

    Pseudocode
    ----------
    - set macro_definition = name, value or error, directive, ownership, and location
    - return macro_definition

    Wraps
    -----
    - none
    """

    name: str
    value: MacroValue
    source_path: Path
    line: int
    column: int
    directive: str
    project_owned: bool
    adapter_owned: bool = False
    native_identity: bool = False
    let_target: str | None = None
    external_snapshot_of: str | None = None
    declaration_error: str | None = None

    @property
    def location(self) -> str:
        """Format the definition's path, line, and column for diagnostics.

        Intent
        ------
        Expose one stable human-readable location string.

        Rationale
        ---------
        Every conflict message should identify the precise defining token.

        Pseudocode
        ----------
        - set source_location = source path, line, and column
        - return source_location

        Wraps
        -----
        - none
        """
        return f"{self.source_path}:{self.line}:{self.column}"


@dataclass(frozen=True)
class RenderableMacroExtraction:
    """Expose schema values with optional source records to trusted callers.

    Intent
    ------
    Preserve exact definition provenance without changing the ordinary value-map API.

    Rationale
    ---------
    Finalization needs source locations until embedded-definition conflicts are checked.

    Pseudocode
    ----------
    - set macro_extraction = schema values paired with source definition records
    - return macro_extraction

    Wraps
    -----
    - none
    """

    values: dict[str, MacroValue]
    records: dict[str, MacroDefinition]


@dataclass(frozen=True)
class TexChunk:
    """Represent one source-ordered TeX fragment with file provenance.

    Intent
    ------
    Preserve file ownership and line offset while dependencies are expanded.

    Rationale
    ---------
    Flattened text alone cannot locate definitions in their originating files.

    Pseudocode
    ----------
    - set tex_chunk = source path, text, line and column offsets, and ownership
    - return tex_chunk

    Wraps
    -----
    - none
    """

    source_path: Path
    text: str
    line_offset: int
    column_offset: int
    project_owned: bool
    adapter_owned: bool = False
    conditional_uncertain: bool = False


@dataclass
class TexScanState:
    """Carry bounded TeX scope state across source-ordered chunks.

    Intent
    ------
    Preserve group nesting and conditional activity while includes are expanded.

    Rationale
    ---------
    A group or conditional may begin before an included chunk and end afterward.

    Pseudocode
    ----------
    - set group_depth = zero
    - set conditionals = empty branch stack

    Wraps
    -----
    - none
    """

    group_depth: int = 0
    conditionals: list[bool | None] = field(default_factory=list)
    named_conditionals: dict[str, bool | None] = field(default_factory=dict)
    named_conditional_scopes: list[dict[str, bool | None]] = field(
        default_factory=list
    )
    uncertain_symbol_fonts: set[str] = field(default_factory=set)
    global_prefix: bool = False


def _enter_scan_group(state: TexScanState) -> None:
    """Enter one live TeX group and snapshot locally scoped boolean state.

    Intent
    ------
    Preserve named-conditional values for restoration at the matching group end.

    Rationale
    ---------
    Assignments made by ``newif`` switches are local unless explicitly global.

    CallsFromRepo
    -------------
      ._active_branch:
        why:
          reads: "Limits grouping changes to source TeX that actually executes."

    Pseudocode
    ----------
    - if the current branch executes:
      - set scope_stack = scope stack plus the current named-condition mapping
      - set group_depth = group depth plus one

    Wraps
    -----
    - none
    """
    if _active_branch(state.conditionals) is True:
        state.named_conditional_scopes.append(dict(state.named_conditionals))
        state.group_depth += 1


def _exit_scan_group(state: TexScanState) -> None:
    """Exit one live TeX group and restore locally scoped boolean state.

    Intent
    ------
    Restore the named-condition mapping saved for the matching live group.

    Rationale
    ---------
    Local switch assignments must not leak into source following a TeX group.

    CallsFromRepo
    -------------
      ._active_branch:
        why:
          reads: "Limits grouping changes to source TeX that actually executes."

    Pseudocode
    ----------
    - if a live group is open:
      - set named_conditions = the most recently saved condition mapping
      - set group_depth = group depth minus one

    Wraps
    -----
    - none
    """
    if _active_branch(state.conditionals) is True and state.group_depth:
        state.named_conditionals = state.named_conditional_scopes.pop()
        state.group_depth -= 1


def _active_branch(conditionals: list[bool | None]) -> bool | None:
    """Return true, false, or unknown for the current conditional branch.

    Intent
    ------
    Collapse nested bounded-condition results into one declaration activity state.

    Rationale
    ---------
    Known false parents suppress definitions, while unknown parents require errors.

    Pseudocode
    ----------
    - if any enclosing branch is false:
      - return false
    - if any enclosing branch is unknown:
      - return unknown
    - return true

    Wraps
    -----
    - none
    """
    if False in conditionals:
        return False
    if None in conditionals:
        return None
    return True


def _advance_scan_control_command(
    text: str,
    idx: int,
    state: TexScanState,
    *,
    unknown_internal_conditionals: bool = False,
) -> int | None:
    """Apply one bounded control-flow or scope command to scan state.

    Intent
    ------
    Share the same conservative execution model between declarations and adapters.

    Rationale
    ---------
    Adapter discovery and ordinary definition parsing must agree about live branches.

    InstantiationsFromRepo
    ----------------------
      ._active_branch:
        why:
          constructs: "Classifies whether state changes execute in the current branch."
      .skip_space:
        why:
          transforms: "Advances from newif to its declared conditional command."

    CallsFromRepo
    -------------
      ._enter_scan_group:
        why:
          transforms: "Starts a scoped named-conditional frame for begingroup."
      ._exit_scan_group:
        why:
          transforms: "Restores a scoped named-conditional frame for endgroup."

    Pseudocode
    ----------
    - if command closes, flips, or opens a known conditional:
      - set scan_state = scan state with the updated conditional stack
      - return command ending offset
    - if command declares or changes a newif boolean:
      - set scan_state = scan state with the updated named condition
      - return command ending offset
    - if command changes group depth or prefixes a global assignment:
      - set scan_state = scan state with the updated scope
      - return command ending offset
    - return missing ending offset

    Wraps
    -----
    - none
    """
    command_match = COMMAND_RE.match(text, idx)
    if command_match is None:
        return None
    command = command_match.group(1)
    end = command_match.end()
    if command == "fi":
        if state.conditionals:
            state.conditionals.pop()
        state.global_prefix = False
        return end
    if command == "else":
        if state.conditionals:
            current = state.conditionals[-1]
            state.conditionals[-1] = None if current is None else not current
        state.global_prefix = False
        return end
    if command == "newif":
        name_start = skip_space(text, end)
        name_match = COMMAND_RE.match(text, name_start)
        if name_match is not None and name_match.group(1).startswith("if"):
            branch = _active_branch(state.conditionals)
            if branch is not False:
                state.named_conditionals[name_match.group(1)] = (
                    False if branch is True else None
                )
            state.global_prefix = False
            return name_match.end()
        state.global_prefix = False
        return end
    if command in state.named_conditionals:
        state.conditionals.append(state.named_conditionals[command])
        state.global_prefix = False
        return end
    for conditional_name in tuple(state.named_conditionals):
        stem = conditional_name[2:]
        if command not in {f"{stem}true", f"{stem}false"}:
            continue
        branch = _active_branch(state.conditionals)
        if branch is True:
            value = command.endswith("true")
            state.named_conditionals[conditional_name] = value
            if state.global_prefix:
                for outer_scope in state.named_conditional_scopes:
                    outer_scope[conditional_name] = value
        elif branch is None:
            state.named_conditionals[conditional_name] = None
        state.global_prefix = False
        return end
    if command in TEX_CONDITIONAL_COMMANDS or (
        unknown_internal_conditionals and command.startswith("if@")
    ):
        state.conditionals.append(
            True if command == "iftrue" else False if command == "iffalse" else None
        )
        state.global_prefix = False
        return end
    branch = _active_branch(state.conditionals)
    if command == "begingroup":
        _enter_scan_group(state)
        state.global_prefix = False
        return end
    if command == "endgroup":
        _exit_scan_group(state)
        state.global_prefix = False
        return end
    if command == "global":
        state.global_prefix = branch is not False
        return end
    if command == "long":
        return end
    return None


def strip_comments(text: str) -> str:
    """Remove TeX comments while preserving escaped percent signs and lines.

    Intent
    ------
    Produce parseable source without changing line numbering.

    Rationale
    ---------
    Source locations remain useful only when comment removal preserves lines.

    Pseudocode
    ----------
    - for source_line in text:
      - set cleaned_line = text before the first unescaped percent sign
    - return cleaned lines joined with newlines

    Wraps
    -----
    - none
    """
    cleaned_lines = []
    for line in text.splitlines():
        idx = 0
        cut = len(line)
        while True:
            pos = line.find("%", idx)
            if pos == -1:
                break
            backslashes = 0
            cursor = pos - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                cut = pos
                break
            idx = pos + 1
        cleaned_lines.append(line[:cut])
    return "\n".join(cleaned_lines)


def read_tex_text(path: Path) -> str:
    """Read modern and legacy TeX sources without losing byte values.

    Intent
    ------
    Decode TeX source as UTF-8 with BOM support or fall back to Latin-1.

    Rationale
    ---------
    Distribution packages and older projects are not uniformly UTF-8 encoded.

    Pseudocode
    ----------
    - set source_bytes = bytes read from path
    - if source_bytes decode as UTF-8:
      - return decoded UTF-8 text
    - return decoded Latin-1 text

    Wraps
    -----
    - none
    """
    payload = path.read_bytes()
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return payload.decode("latin-1")


def read_balanced_group(text: str, start: int) -> tuple[str, int]:
    """Read a balanced ``{...}`` group beginning at ``start``.

    Intent
    ------
    Return one brace group's body and first offset after its closing brace.

    Rationale
    ---------
    TeX replacements and adapter blocks may contain nested brace groups.

    Pseudocode
    ----------
    - if start does not point to an opening brace:
      - raise balanced group syntax error
    - while the matching closing brace has not been found:
      - set nesting_depth = nesting depth after the current source character
    - return group body and ending offset

    Wraps
    -----
    - none
    """
    if start >= len(text) or text[start] != "{":
        raise ValueError("balanced group must start with '{'")
    depth = 0
    body_start = start + 1
    idx = start
    while idx < len(text):
        char = text[idx]
        if char == "\\":
            idx += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[body_start:idx], idx + 1
        idx += 1
    raise ValueError("unclosed balanced group")


def _read_optional_group(text: str, start: int) -> tuple[str, int]:
    """Read one balanced optional argument beginning at an opening bracket.

    Intent
    ------
    Parse optional macro arity and default groups without splitting nested braces.

    Rationale
    ---------
    Defaults may contain brace groups even though their outer delimiter is square.

    Pseudocode
    ----------
    - if start is not an opening bracket:
      - raise optional group syntax error
    - set group_end = first unnested closing bracket
    - return optional group text and group_end

    Wraps
    -----
    - none
    """
    if start >= len(text) or text[start] != "[":
        raise ValueError("optional group must start with '['")
    depth = 0
    idx = start + 1
    while idx < len(text):
        char = text[idx]
        if char == "\\":
            idx += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "]" and depth == 0:
            return text[start + 1 : idx], idx + 1
        idx += 1
    raise ValueError("unclosed optional group")


def skip_space(text: str, idx: int) -> int:
    """Return the first offset at or after ``idx`` that is not whitespace.

    Intent
    ------
    Advance source parsers across insignificant whitespace.

    Rationale
    ---------
    TeX declaration arguments may be separated by arbitrary whitespace.

    Pseudocode
    ----------
    - while current character is whitespace:
      - set current_offset = current offset plus one
    - return current offset

    Wraps
    -----
    - none
    """
    while idx < len(text) and text[idx].isspace():
        idx += 1
    return idx


def resolve_tex_path(include_name: str, current_dir: Path, suffix: str = ".tex") -> Path:
    """Resolve one possibly unqualified local TeX dependency path.

    Intent
    ------
    Build an absolute local candidate for an include, package, or class.

    Rationale
    ---------
    TeX load commands commonly omit both suffixes and absolute paths.

    Pseudocode
    ----------
    - set candidate = include name with default suffix when absent
    - if candidate is relative:
      - set candidate = current directory joined with candidate
    - return resolved candidate

    Wraps
    -----
    - none
    """
    path = Path(include_name)
    if not path.suffix:
        path = path.with_suffix(suffix)
    if not path.is_absolute():
        path = current_dir / path
    return path.resolve()


def dependency_suffix(command: str) -> str:
    """Map one TeX dependency command to its conventional source suffix.

    Intent
    ------
    Distinguish package, class, and ordinary TeX dependency filenames.

    Rationale
    ---------
    Unqualified include names require command-specific resolution.

    Pseudocode
    ----------
    - if command loads a class:
      - return class suffix
    - if command loads a package:
      - return package suffix
    - return TeX suffix

    Wraps
    -----
    - none
    """
    if command.startswith("documentclass") or command.startswith("LoadClass"):
        return ".cls"
    if command == "usepackage" or command.startswith("RequirePackage"):
        return ".sty"
    return ".tex"


@lru_cache(maxsize=512)
def tex_distribution_path(filename: str) -> Path | None:
    """Ask the active TeX distribution to resolve one dependency.

    Intent
    ------
    Resolve a package or class through the available ``kpsewhich`` executable.

    Rationale
    ---------
    Relevant macros can originate in installed TeX sources outside the project.

    Pseudocode
    ----------
    - if kpsewhich is unavailable:
      - return missing path
    - set resolved_path = bounded kpsewhich result for filename
    - if resolved_path is a file:
      - return resolved_path
    - return missing path

    Wraps
    -----
    - none
    """
    kpsewhich = shutil.which("kpsewhich")
    if not kpsewhich:
        return None
    try:
        result = subprocess.run(
            [kpsewhich, filename],
            check=False,
            capture_output=True,
            timeout=5,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    resolved = os.fsdecode(result.stdout).strip().splitlines()
    if not resolved:
        return None
    path = Path(resolved[0]).resolve()
    return path if path.is_file() else None


def dependency_paths(command: str, names: str, current_dir: Path) -> list[Path]:
    """Resolve source dependencies locally, then through the TeX distribution.

    Intent
    ------
    Locate every named include, package, or class in declared source order.

    Rationale
    ---------
    Project-local definitions take precedence before distribution lookup.

    InstantiationsFromRepo
    ----------------------
      .dependency_suffix:
        why:
          constructs: "Selects the command-specific filename suffix."
      .resolve_tex_path:
        why:
          constructs: "Builds each candidate project-local source path."
      .tex_distribution_path:
        why:
          constructs: "Provides an installed source path when no local file exists."

    Pseudocode
    ----------
    - suffix = dependency_suffix(command)
    - for dependency_name in names:
      - local_path = resolve_tex_path(dependency_name and suffix)
      - if local_path exists:
        - set resolved_paths = resolved_paths with local_path
      - else:
        - distribution_path = tex_distribution_path(dependency_name)
        - set resolved_paths = resolved_paths with distribution_path
    - return resolved_paths

    Wraps
    -----
    - none
    """
    suffix = dependency_suffix(command)
    paths: list[Path] = []
    for raw_name in names.split(","):
        include_name = raw_name.strip()
        if not include_name:
            continue
        local = resolve_tex_path(include_name, current_dir, suffix)
        if local.is_file():
            paths.append(local)
            continue
        name_path = Path(include_name)
        filename = str(name_path if name_path.suffix else name_path.with_suffix(suffix))
        distributed = tex_distribution_path(filename)
        if distributed is not None:
            paths.append(distributed)
    return paths


def local_include_paths(command: str, names: str, current_dir: Path) -> list[Path]:
    """Return only local dependency paths for compatibility callers.

    Intent
    ------
    Preserve the previous project-only dependency resolver API.

    Rationale
    ---------
    Older callers should not unexpectedly begin traversing distribution files.

    InstantiationsFromRepo
    ----------------------
      .dependency_suffix:
        why:
          constructs: "Selects the suffix used for local candidates."
      .resolve_tex_path:
        why:
          constructs: "Builds each project-local dependency candidate."

    Pseudocode
    ----------
    - suffix = dependency_suffix(command)
    - set local_paths = existing project candidates for names and suffix
    - return local_paths

    Wraps
    -----
    - none
    """
    suffix = dependency_suffix(command)
    paths: list[Path] = []
    for raw_name in names.split(","):
        include_name = raw_name.strip()
        if not include_name:
            continue
        path = resolve_tex_path(include_name, current_dir, suffix)
        if path.is_file():
            paths.append(path)
    return paths


def _path_is_within(path: Path, root: Path) -> bool:
    """Return whether one resolved path is contained by the project root.

    Intent
    ------
    Classify source ownership without depending on string-prefix comparisons.

    Rationale
    ---------
    Project definitions and distribution definitions have different closure roles.

    Pseudocode
    ----------
    - if path is relative to root:
      - return true
    - return false

    Wraps
    -----
    - none
    """
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _mathjax_adapter_file_chunks(
    adapter_path: Path,
    *,
    seen: set[tuple[Path, bool]],
    conditional_uncertain: bool = False,
    source_text: str | None = None,
    line_offset: int = 0,
    column_offset: int = 0,
    inherited_named_conditionals: dict[str, bool | None] | None = None,
) -> list[TexChunk]:
    """Read balanced customization bodies and adapter-only dependencies.

    Intent
    ------
    Collect renderer-facing definitions from one lwarp adapter dependency tree.

    Rationale
    ---------
    Only balanced ``CustomizeMathJax`` bodies describe browser definitions;
    other adapter code configures TeX execution and must not enter the payload.

    CallsFromRepo
    -------------
      ._enter_scan_group:
        why:
          transforms: "Starts scoped named-condition tracking for a live brace group."
      ._exit_scan_group:
        why:
          transforms: "Restores named-condition state after a live brace group."
      .parse_declared_math_symbol_at:
        why:
          reads: "Consumes complete symbol declarations without scanning their bodies."
      .parse_declared_operator_at:
        why:
          reads: "Consumes complete operator declarations without scanning their bodies."
      .parse_def_at:
        why:
          reads: "Consumes complete primitive definitions without scanning their bodies."
      .parse_newcommand_at:
        why:
          reads: "Consumes complete command declarations without scanning their bodies."
      .read_tex_text:
        why:
          reads: "Loads each resolved adapter source."

    InstantiationsFromRepo
    ----------------------
      .TexScanState:
        why:
          constructs: "Carries conditional and scope state across the adapter source."
      .TexChunk:
        why:
          constructs: "Carries each balanced customization body with provenance."
      ._active_branch:
        why:
          constructs: "Derives adapter command activity from nested conditionals."
      ._advance_scan_control_command:
        why:
          constructs: "Applies the shared bounded TeX execution model."
      ._delimited_def_end:
        why:
          constructs: "Skips unsupported definition replacement bodies as a unit."
      ._mathjax_adapter_file_chunks:
        why:
          constructs: "Traverses explicit adapter dependencies with cycle control."
      .read_balanced_group:
        why:
          constructs: "Extracts one complete customization body."
      .skip_space:
        why:
          transforms: "Finds the opening brace after a customization command."
      .strip_comments:
        why:
          transforms: "Removes inactive comments while preserving source lines."
      .tex_distribution_path:
        why:
          constructs: "Resolves explicitly named lwarp adapter dependencies."

    Pseudocode
    ----------
    - if adapter path was already visited:
      - return no chunks
    - for marker in source ordered adapter markers:
      - set adapter_chunks = adapter chunks plus dependency chunks or customization body
    - return adapter chunks

    Wraps
    -----
    - none
    """
    adapter_path = adapter_path.resolve()
    if source_text is None:
        seen_key = (adapter_path, conditional_uncertain)
        if seen_key in seen:
            return []
        seen.add(seen_key)
        text = strip_comments(read_tex_text(adapter_path))
    else:
        text = source_text
    chunks: list[TexChunk] = []
    state = TexScanState(
        named_conditionals=dict(inherited_named_conditionals or {})
    )
    cursor = 0
    while cursor < len(text):
        if text[cursor] == "{":
            _enter_scan_group(state)
            cursor += 1
            continue
        if text[cursor] == "}":
            _exit_scan_group(state)
            cursor += 1
            continue
        if text[cursor] != "\\":
            cursor += 1
            continue

        control_end = _advance_scan_control_command(
            text,
            cursor,
            state,
            unknown_internal_conditionals=True,
        )
        if control_end is not None:
            cursor = control_end
            continue

        command_match = COMMAND_RE.match(text, cursor)
        command = command_match.group(1) if command_match else ""
        branch = _active_branch(state.conditionals)
        active = branch is not False and (state.group_depth == 0 or state.global_prefix)

        if command == "AtBeginDocument":
            group_start = skip_space(text, command_match.end())
            if group_start >= len(text) or text[group_start] != "{":
                cursor = command_match.end()
                state.global_prefix = False
                continue
            try:
                body, group_end = read_balanced_group(text, group_start)
            except ValueError as error:
                line = line_offset + text.count("\n", 0, group_start) + 1
                raise ValueError(
                    f"Unclosed \\AtBeginDocument body at {adapter_path}:{line}"
                ) from error
            if active:
                body_start = group_start + 1
                body_lines = text.count("\n", 0, body_start)
                prior_newline = text.rfind("\n", 0, body_start)
                body_column = (
                    body_start - prior_newline - 1
                    if prior_newline >= 0
                    else column_offset + body_start
                )
                chunks.extend(
                    _mathjax_adapter_file_chunks(
                        adapter_path,
                        seen=seen,
                        conditional_uncertain=(
                            conditional_uncertain or branch is None
                        ),
                        source_text=body,
                        line_offset=line_offset + body_lines,
                        column_offset=body_column,
                        inherited_named_conditionals=state.named_conditionals,
                    )
                )
            cursor = group_end
            state.global_prefix = False
            continue

        dependency = MATHJAX_ADAPTER_DEPENDENCY_RE.match(text, cursor)
        if dependency is not None:
            if not active:
                cursor = dependency.end()
                state.global_prefix = False
                continue
            for raw_name in dependency.group("names").split(","):
                dependency_name = raw_name.strip()
                if not dependency_name.startswith("lwarp-"):
                    continue
                filename = (
                    dependency_name
                    if dependency_name.endswith(".sty")
                    else f"{dependency_name}.sty"
                )
                dependency_path = tex_distribution_path(filename)
                if dependency_path is not None:
                    chunks.extend(
                        _mathjax_adapter_file_chunks(
                            dependency_path,
                            seen=seen,
                            conditional_uncertain=(
                                conditional_uncertain or branch is None
                            ),
                        )
                    )
            cursor = dependency.end()
            state.global_prefix = False
            continue

        if command == "CustomizeMathJax":
            group_start = skip_space(text, command_match.end())
            if group_start >= len(text) or text[group_start] != "{":
                cursor = command_match.end()
                state.global_prefix = False
                continue
            try:
                body, group_end = read_balanced_group(text, group_start)
            except ValueError as error:
                line = text.count("\n", 0, group_start) + 1
                raise ValueError(
                    f"Unclosed \\CustomizeMathJax body at {adapter_path}:{line}"
                ) from error
            if active:
                body_start = group_start + 1
                prior_newline = text.rfind("\n", 0, body_start)
                body_lines = text.count("\n", 0, body_start)
                chunks.append(
                    TexChunk(
                        source_path=adapter_path,
                        text=body,
                        line_offset=line_offset + body_lines,
                        column_offset=(
                            body_start - prior_newline - 1
                            if prior_newline >= 0
                            else column_offset + body_start
                        ),
                        project_owned=False,
                        adapter_owned=True,
                        conditional_uncertain=(
                            conditional_uncertain or branch is None
                        ),
                    )
                )
            cursor = group_end
            state.global_prefix = False
            continue

        parsed = (
            parse_newcommand_at(text, cursor)
            or parse_declared_operator_at(text, cursor)
            or parse_declared_math_symbol_at(text, cursor)
            or parse_def_at(text, cursor)
        )
        if parsed is not None:
            cursor = parsed[2]
            state.global_prefix = False
            continue
        def_end = _delimited_def_end(text, cursor)
        if def_end is not None:
            cursor = def_end
            state.global_prefix = False
            continue
        alias = LET_RE.match(text, cursor)
        if alias is not None:
            cursor = alias.end()
            state.global_prefix = False
            continue
        cursor = command_match.end() if command_match else cursor + 1
        state.global_prefix = False
    return chunks


def _executed_load_commands(
    text: str,
    scan_state: TexScanState | None = None,
) -> Iterator[tuple[re.Match[str], bool]]:
    """Yield source loads as bounded TeX execution reaches each command.

    Intent
    ------
    Discover dependency loads according to the bounded source execution model.

    Rationale
    ---------
    Loads stored in uninvoked replacements or false branches do not execute.

    CallsFromRepo
    -------------
      .TexScanState:
        why:
          reads: "Accepts caller state for source-ordered cross-file scanning."
      ._enter_scan_group:
        why:
          transforms: "Starts scoped condition tracking for a live brace group."
      ._exit_scan_group:
        why:
          transforms: "Restores condition state after a live brace group."
      .parse_declared_math_symbol_at:
        why:
          reads: "Consumes complete symbol declarations without scanning their bodies."
      .parse_declared_operator_at:
        why:
          reads: "Consumes complete operator declarations without scanning their bodies."
      .parse_def_at:
        why:
          reads: "Consumes complete primitive definitions without scanning their bodies."
      .parse_newcommand_at:
        why:
          reads: "Consumes complete command declarations without scanning their bodies."

    InstantiationsFromRepo
    ----------------------
      ._active_branch:
        why:
          constructs: "Classifies each load as live, false, or uncertain."
      ._advance_scan_control_command:
        why:
          constructs: "Applies condition and scope transitions in source order."
      ._delimited_def_end:
        why:
          constructs: "Skips unsupported definition replacement bodies as a unit."

    Pseudocode
    ----------
    - set scan_state = supplied scan state or empty grouping and conditional state
    - for command_token in source text:
      - if command token begins a complete macro declaration:
        - set cursor = first offset after the declaration replacement
      - if command token begins a complete let alias:
        - set cursor = first offset after the alias right side
      - if command token is a load in a live or uncertain branch:
        - return the next command token and its branch uncertainty

    Wraps
    -----
    - none
    """
    state = scan_state if scan_state is not None else TexScanState()
    cursor = 0
    while cursor < len(text):
        if text[cursor] == "{":
            _enter_scan_group(state)
            cursor += 1
            continue
        if text[cursor] == "}":
            _exit_scan_group(state)
            cursor += 1
            continue
        if text[cursor] != "\\":
            cursor += 1
            continue

        control_end = _advance_scan_control_command(
            text,
            cursor,
            state,
            unknown_internal_conditionals=True,
        )
        if control_end is not None:
            cursor = control_end
            continue

        load = LOCAL_INCLUDE_RE.match(text, cursor)
        if load is not None:
            branch = _active_branch(state.conditionals)
            if branch is not False:
                yield load, branch is None
            cursor = load.end()
            state.global_prefix = False
            continue

        parsed = (
            parse_newcommand_at(text, cursor)
            or parse_declared_operator_at(text, cursor)
            or parse_declared_math_symbol_at(text, cursor)
            or parse_def_at(text, cursor)
        )
        if parsed is not None:
            cursor = parsed[2]
            state.global_prefix = False
            continue
        definition_end = _delimited_def_end(text, cursor)
        if definition_end is not None:
            cursor = definition_end
            state.global_prefix = False
            continue
        alias = LET_RE.match(text, cursor)
        if alias is not None:
            cursor = alias.end()
            state.global_prefix = False
            continue
        command = COMMAND_RE.match(text, cursor)
        cursor = command.end() if command is not None else cursor + 1
        state.global_prefix = False


def collect_tex_chunks(
    entrypoint: Path,
    *,
    project_root: Path,
    seen: set[Path] | None = None,
    _loaded_once: set[Path] | None = None,
    _dependency_stack: tuple[Path, ...] = (),
    _scan_state: TexScanState | None = None,
) -> list[TexChunk]:
    """Collect the dependency tree in TeX source order with file provenance.

    Intent
    ------
    Expand executed dependencies while retaining each fragment's exact origin.

    Rationale
    ---------
    Repeated inputs execute in source order, while packages and classes load once.

    CallsFromRepo
    -------------
      .TexScanState:
        why:
          reads: "Accepts the source-order state shared across recursive loads."
      ._executed_load_commands:
        why:
          reads: "Finds dependency commands that execute outside stored replacements."
      .dependency_paths:
        why:
          reads: "Finds local or installed source dependencies at each load command."
      .read_tex_text:
        why:
          reads: "Decodes the current TeX source file."
      ._path_is_within:
        why:
          reads: "Classifies dependency ownership against the project root."
      .tex_distribution_path:
        why:
          reads: "Finds an optional lwarp adapter for one distribution package."

    InstantiationsFromRepo
    ----------------------
      .TexChunk:
        why:
          constructs: "Carries each source fragment with its location metadata."
      ._mathjax_adapter_file_chunks:
        why:
          constructs: "Adds only renderer-facing adapter bodies and dependencies."
      ._path_is_within:
        why:
          constructs: "Classifies whether the current source is project-owned."
      .collect_tex_chunks:
        why:
          constructs: "Produces the recursively expanded child fragment sequence."
      .strip_comments:
        why:
          transforms: "Produces comment-free source while preserving line structure."

    Pseudocode
    ----------
    - if entrypoint is on the active dependency stack:
      - raise a dependency-cycle error
    - source_text = strip_comments(@.read_tex_text(entrypoint))
    - project_owned = _path_is_within(entrypoint, project_root)
    - for load_command in @._executed_load_commands(source_text and scan_state):
      - if load command is load-once and already loaded:
        - continue
      - dependency_sources = @.dependency_paths(load_command)
      - child_chunks = collect_tex_chunks(dependency_sources and scan_state)
      - parent_chunk = TexChunk(parent fragment and project_owned)
      - set ordered_chunks = parent_chunk, child_chunks, and remaining fragment
    - return ordered_chunks

    Wraps
    -----
    - none
    """
    source_path = entrypoint.resolve()
    active_seen = seen if seen is not None else set()
    if source_path in active_seen:
        cycle = _dependency_stack + (source_path,)
        raise ValueError(
            "Cyclic TeX dependency: " + " -> ".join(str(path) for path in cycle)
        )
    active_seen.add(source_path)
    loaded_once = _loaded_once if _loaded_once is not None else set()
    scan_state = _scan_state if _scan_state is not None else TexScanState()
    text = strip_comments(read_tex_text(source_path))
    project_owned = _path_is_within(source_path, project_root)

    chunks: list[TexChunk] = []
    try:
        last = 0
        for match, conditionally_uncertain in _executed_load_commands(
            text, scan_state
        ):
            chunks.append(
                TexChunk(
                    source_path,
                    text[last : match.start()],
                    text.count("\n", 0, last),
                    last - text.rfind("\n", 0, last) - 1,
                    project_owned,
                )
            )
            command = match.group("cmd")
            load_once = (
                command in {"usepackage", "documentclass"}
                or command.startswith("RequirePackage")
                or command.startswith("LoadClass")
            )
            for child in dependency_paths(
                command, match.group("names"), source_path.parent
            ):
                if load_once and child in loaded_once:
                    continue
                if load_once:
                    loaded_once.add(child)
                is_package = command == "usepackage" or command.startswith(
                    "RequirePackage"
                )
                distribution_package = is_package and not _path_is_within(
                    child, project_root
                )
                adapter_path = (
                    tex_distribution_path(f"lwarp-{child.stem}.sty")
                    if distribution_package
                    else None
                )
                child_chunks = collect_tex_chunks(
                    child,
                    project_root=project_root,
                    seen=active_seen,
                    _loaded_once=loaded_once,
                    _dependency_stack=_dependency_stack + (source_path,),
                    _scan_state=scan_state,
                )
                if conditionally_uncertain:
                    child_chunks = [
                        replace(child_chunk, conditional_uncertain=True)
                        for child_chunk in child_chunks
                    ]
                chunks.extend(child_chunks)
                if adapter_path is not None:
                    chunks.extend(
                        _mathjax_adapter_file_chunks(adapter_path, seen=set())
                    )
            last = match.end()
        chunks.append(
            TexChunk(
                source_path,
                text[last:],
                text.count("\n", 0, last),
                last - text.rfind("\n", 0, last) - 1,
                project_owned,
            )
        )
    finally:
        active_seen.remove(source_path)
    return chunks


def flatten_tex(entrypoint: Path, seen: set[Path] | None = None) -> str:
    """Return the reachable source tree flattened in dependency order.

    Intent
    ------
    Adapt provenance-bearing source chunks to the legacy flattened-text contract.

    Rationale
    ---------
    Compatibility reporting needs text but not source records.

    CallsFromRepo
    -------------
      .collect_tex_chunks:
        why:
          transforms: "Collects ordered fragments before their provenance is dropped."

    Pseudocode
    ----------
    - source_chunks = @.collect_tex_chunks(entrypoint)
    - set flattened_text = joined text from source_chunks
    - return flattened_text

    Wraps
    -----
    - none
    """
    entrypoint = entrypoint.resolve()
    return "\n".join(
        chunk.text
        for chunk in collect_tex_chunks(
            entrypoint,
            project_root=entrypoint.parent,
            seen=seen,
        )
    )


def parse_newcommand_at(text: str, idx: int) -> tuple[str, MacroValue, int] | None:
    """Parse a new/provide/renew command into MathJax-native tuple order.

    Intent
    ------
    Decode command names, arity, optional defaults, and balanced replacement text.

    Rationale
    ---------
    New extractor output must always use replacement-first native tuples.

    InstantiationsFromRepo
    ----------------------
      ._read_optional_group:
        why:
          constructs: "Parses bracketed arity and optional-default groups."
      .read_balanced_group:
        why:
          constructs: "Parses command names and replacement groups."
      .skip_space:
        why:
          transforms: "Advances between declaration components."

    Pseudocode
    ----------
    - if text at idx is not a supported command definition:
      - return missing definition
    - optional_group = _read_optional_group(text after macro name)
    - set native_definition = name, optional_group, replacement, and ending offset
    - return native_definition

    Wraps
    -----
    - none
    """
    match = NEWCOMMAND_RE.match(text, idx)
    if not match:
        return None
    pos = skip_space(text, match.end())
    if pos >= len(text):
        return None

    if text[pos] == "{":
        try:
            name_group, pos = read_balanced_group(text, pos)
        except ValueError:
            return None
        name_match = re.fullmatch(r"\\([A-Za-z@]+)", name_group.strip())
        if not name_match:
            return None
        name = name_match.group(1)
    elif text[pos] == "\\":
        name_match = re.match(r"\\([A-Za-z@]+)", text[pos:])
        if not name_match:
            return None
        name = name_match.group(1)
        pos += len(name_match.group(0))
    else:
        return None

    pos = skip_space(text, pos)
    argc = 0
    if pos < len(text) and text[pos] == "[":
        try:
            argc_text, pos = _read_optional_group(text, pos)
        except ValueError:
            return None
        if not argc_text.strip().isdigit():
            return None
        argc = int(argc_text.strip())
        pos = skip_space(text, pos)

    default: str | None = None
    if pos < len(text) and text[pos] == "[":
        try:
            default, pos = _read_optional_group(text, pos)
        except ValueError:
            return None
        pos = skip_space(text, pos)

    if pos >= len(text) or text[pos] != "{":
        return None
    try:
        body, end_pos = read_balanced_group(text, pos)
    except ValueError:
        return None

    if default is not None:
        return name, [body, argc, default], end_pos
    if argc:
        return name, [body, argc], end_pos
    return name, body, end_pos


def parse_def_at(text: str, idx: int) -> tuple[str, str, int] | None:
    """Parse one simple brace-bodied TeX ``def`` declaration.

    Intent
    ------
    Recover zero-argument primitive definitions that can be represented in MathJax.

    Rationale
    ---------
    Package sources commonly use ``def`` even when project sources use ``newcommand``.

    InstantiationsFromRepo
    ----------------------
      .read_balanced_group:
        why:
          constructs: "Parses the primitive definition replacement group."
      .skip_space:
        why:
          transforms: "Advances from the macro name to its replacement."

    Pseudocode
    ----------
    - if text at idx is not a simple def declaration:
      - return missing definition
    - replacement_group = read_balanced_group(text after command name)
    - return command name, replacement_group, and ending offset

    Wraps
    -----
    - none
    """
    match = DEF_RE.match(text, idx)
    if not match:
        return None
    name = match.group("name")
    pos = skip_space(text, match.end())
    if pos >= len(text) or text[pos] != "{":
        return None
    try:
        body, end_pos = read_balanced_group(text, pos)
    except ValueError:
        return None
    return name, body, end_pos


def parse_declared_operator_at(text: str, idx: int) -> tuple[str, str, int] | None:
    """Parse one ``DeclareMathOperator`` into a portable MathJax definition.

    Intent
    ------
    Preserve an operator's literal label using MathJax's operator command.

    Rationale
    ---------
    The source declaration syntax is not itself a macro configuration value.

    InstantiationsFromRepo
    ----------------------
      .read_balanced_group:
        why:
          constructs: "Extracts the complete operator label group."
      .skip_space:
        why:
          transforms: "Finds the operator label after the command name."

    Pseudocode
    ----------
    - if source at offset is not an operator declaration:
      - return no definition
    - set operator_text = balanced label group
    - return macro name, portable operator body, and ending offset

    Wraps
    -----
    - none
    """
    match = DECLARE_OP_RE.match(text, idx)
    if not match:
        return None
    pos = skip_space(text, match.end())
    if pos >= len(text) or text[pos] != "{":
        return None
    try:
        operator_text, end_pos = read_balanced_group(text, pos)
    except ValueError:
        return None
    return match.group(1), f"\\operatorname{{{operator_text}}}", end_pos


def collect_symbol_fonts(text: str) -> dict[str, tuple[str, str, str, str]]:
    """Index declared symbol-font metadata by TeX font name.

    Intent
    ------
    Extract encoding, family, series, and shape for declared symbol fonts.

    Rationale
    ---------
    Math-symbol slots are meaningful only in the context of their declared font.

    Pseudocode
    ----------
    - set symbol_fonts = declared font metadata indexed by name
    - return symbol_fonts

    Wraps
    -----
    - none
    """
    return {
        match.group("name").strip(): (
            match.group("encoding").strip(),
            match.group("family").strip(),
            match.group("series").strip(),
            match.group("shape").strip(),
        )
        for match in DECLARE_SYMBOL_FONT_RE.finditer(text)
    }


def _math_symbol_slot(code: str) -> int | None:
    """Parse a TeX character-slot spelling into an integer code point.

    Intent
    ------
    Accept character, hexadecimal, octal, and decimal slot forms.

    Rationale
    ---------
    Distribution symbol declarations use several equivalent numeric syntaxes.

    Pseudocode
    ----------
    - if code names a character:
      - return character code point
    - if code is numeric:
      - return integer in the indicated base
    - return missing slot

    Wraps
    -----
    - none
    """
    code = code.strip()
    if code.startswith("`") and len(code) >= 2:
        return ord(code[1])
    try:
        if code.startswith('"'):
            return int(code[1:], 16)
        if code.startswith("'"):
            return int(code[1:], 8)
        return int(code, 10)
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _canonical_math_symbols() -> dict[tuple[str, str, int], str]:
    """Load the TeX distribution's canonical math-symbol slot index.

    Intent
    ------
    Map encoding, family, and slot triples to standard MathJax command names.

    Rationale
    ---------
    Reusing canonical command names avoids embedding engine-specific glyph slots.

    CallsFromRepo
    -------------
      .read_tex_text:
        why:
          reads: "Decodes the distribution symbol declaration source."

    InstantiationsFromRepo
    ----------------------
      ._math_symbol_slot:
        why:
          constructs: "Converts each declared TeX slot to an integer key."
      .collect_symbol_fonts:
        why:
          constructs: "Indexes font metadata used by symbol declarations."
      .strip_comments:
        why:
          transforms: "Produces parseable symbol source without TeX comments."
      .tex_distribution_path:
        why:
          constructs: "Locates the canonical distribution declaration file."

    Pseudocode
    ----------
    - source_path = tex_distribution_path(`fontmath.ltx`)
    - source_text = @.read_tex_text(source_path)
    - clean_text = strip_comments(source_text)
    - symbol_fonts = collect_symbol_fonts(clean_text)
    - for symbol_declaration in clean_text:
      - slot = _math_symbol_slot(symbol_declaration and symbol_fonts)
      - set symbol_index = symbol_index with font and slot command
    - return symbol_index

    Wraps
    -----
    - none
    """
    source = tex_distribution_path("fontmath.ltx")
    if source is None:
        return {}
    text = strip_comments(read_tex_text(source))
    fonts = collect_symbol_fonts(text)
    symbols: dict[tuple[str, str, int], str] = {}
    for match in DECLARE_MATH_SYMBOL_RE.finditer(text):
        font = fonts.get(match.group("font").strip())
        slot = _math_symbol_slot(match.group("slot"))
        if font is not None and slot is not None:
            symbols.setdefault((font[0], font[1], slot), match.group("name"))
    return symbols


def parse_declared_math_symbol_at(
    text: str,
    idx: int,
    symbol_fonts: dict[str, tuple[str, str, str, str]] | None = None,
) -> tuple[str, str, int] | None:
    """Translate one literal math-symbol declaration to a MathJax command.

    Intent
    ------
    Resolve a declared glyph to a canonical command or representable literal.

    Rationale
    ---------
    Raw TeX font slots are not portable renderer macro bodies.

    CallsFromRepo
    -------------
      ._canonical_math_symbols:
        why:
          reads: "Looks up the portable command for a declared font slot."

    InstantiationsFromRepo
    ----------------------
      ._math_symbol_slot:
        why:
          constructs: "Parses the declaration's slot spelling."

    Pseudocode
    ----------
    - slot = _math_symbol_slot(symbol declaration)
    - set font_metadata = declared font details for the symbol
    - set canonical_command = @._canonical_math_symbols(font_metadata and slot)
    - if canonical_command exists:
      - return symbol name, portable body, and ending offset
    - return a literal character mapping or missing definition

    Wraps
    -----
    - none
    """
    match = DECLARE_MATH_SYMBOL_RE.match(text, idx)
    if not match:
        return None
    slot = _math_symbol_slot(match.group("slot"))
    if slot is None:
        return None
    font = (symbol_fonts or {}).get(match.group("font").strip())
    is_bold = font is not None and font[2].lower() in {"b", "bx", "bold"}

    if font is not None:
        canonical = _canonical_math_symbols().get((font[0], font[1], slot))
        if canonical:
            command = f"\\{canonical}"
            body = f"\\boldsymbol{{{command}}}" if is_bold else command
            return match.group("name"), body, match.end()

    code = match.group("slot").strip()
    if code.startswith("`") and len(code) >= 2:
        char = code[1]
        body = f"\\mathbf{{{char}}}" if is_bold or font is None else char
        return match.group("name"), body, match.end()
    return None


def _line_column(chunk: TexChunk, index: int) -> tuple[int, int]:
    """Convert a fragment-relative offset to its source line and column.

    Intent
    ------
    Recover human-readable coordinates after include expansion split a source file.

    Rationale
    ---------
    Diagnostics must point to original files rather than flattened offsets.

    Pseudocode
    ----------
    - set source_line = chunk offset plus preceding fragment lines
    - set source_column = characters since the preceding newline
    - return source_line and source_column

    Wraps
    -----
    - none
    """
    line = chunk.line_offset + chunk.text.count("\n", 0, index) + 1
    prior_newline = chunk.text.rfind("\n", 0, index)
    column_offset = chunk.column_offset if prior_newline < 0 else 0
    return line, column_offset + index - prior_newline


def _delimited_def_end(text: str, idx: int) -> int | None:
    """Return the full span of a brace-bodied ``def``, including parameters.

    Intent
    ------
    Consume unsupported parameterized or delimited definitions as one declaration.

    Rationale
    ---------
    Nested aliases in an unsupported replacement body are not file-level exports.

    InstantiationsFromRepo
    ----------------------
      .read_balanced_group:
        why:
          constructs: "Finds the end of the replacement after local parameter scanning."

    Pseudocode
    ----------
    - if source at offset is not a primitive definition:
      - return no ending offset
    - set replacement_start = first opening brace after local parameter tokens
    - return the offset after the balanced replacement group

    Wraps
    -----
    - none
    """
    match = DEF_RE.match(text, idx)
    if not match:
        return None
    cursor = match.end()
    while cursor < len(text):
        if text[cursor] == "\\":
            command = COMMAND_RE.match(text, cursor)
            cursor = command.end() if command else cursor + 1
            continue
        if text[cursor] == "{":
            try:
                _, end = read_balanced_group(text, cursor)
            except ValueError:
                return len(text)
            return end
        cursor += 1
    return len(text)


def _unsupported_named_declaration_at(
    text: str,
    idx: int,
) -> tuple[str, str, int] | None:
    """Return a named unsupported declaration and the span to skip.

    Intent
    ------
    Detect project commands declared by a narrow known declaration grammar.

    Rationale
    ---------
    A graph-visible source declaration must fail closed instead of looking undeclared.

    InstantiationsFromRepo
    ----------------------
      ._read_optional_group:
        why:
          constructs: "Consumes optional declaration arguments."
      .read_balanced_group:
        why:
          constructs: "Consumes braced command names and replacement text."
      .skip_space:
        why:
          constructs: "Advances between declaration grammar elements."

    Pseudocode
    ----------
    - if the token is not a known unsupported named declaration:
      - return no declaration
    - set command_name = command name parsed from braced or direct syntax
    - for optional_group in at most two declaration option groups:
      - set ending_offset = offset after optional_group
    - set ending_offset = offset after the balanced replacement group
    - return directive, name, and ending offset

    Wraps
    -----
    - none
    """
    match = UNSUPPORTED_NAMED_DECLARATION_RE.match(text, idx)
    if match is None:
        return None
    cursor = skip_space(text, match.end())
    name: str | None = None
    if cursor < len(text) and text[cursor] == "{":
        try:
            name_group, cursor = read_balanced_group(text, cursor)
        except ValueError:
            return None
        name_match = re.fullmatch(r"\\([A-Za-z@]+)", name_group.strip())
        if name_match is not None:
            name = name_match.group(1)
    elif cursor < len(text) and text[cursor] == "\\":
        name_match = COMMAND_RE.match(text, cursor)
        if name_match is not None and (
            name_match.group(1).isalpha() or "@" in name_match.group(1)
        ):
            name = name_match.group(1)
            cursor = name_match.end()
    if name is None:
        return None

    cursor = skip_space(text, cursor)
    for _ in range(2):
        if cursor >= len(text) or text[cursor] != "[":
            break
        try:
            _, cursor = _read_optional_group(text, cursor)
        except ValueError:
            return match.group("kind"), name, len(text)
        cursor = skip_space(text, cursor)
    if cursor < len(text) and text[cursor] == "{":
        try:
            _, cursor = read_balanced_group(text, cursor)
        except ValueError:
            cursor = len(text)
    return match.group("kind"), name, cursor


def _definition_records_from_chunk(
    chunk: TexChunk,
    symbol_fonts: dict[str, tuple[str, str, str, str]],
    scan_state: TexScanState | None = None,
) -> list[MacroDefinition]:
    """Parse one TeX chunk into source-located macro definition records.

    Intent
    ------
    Recognize representable declarations, malformed named declarations, and aliases.

    Rationale
    ---------
    A single record stream lets later merge logic apply TeX redefinition semantics.

    CallsFromRepo
    -------------
      .TexScanState:
        why:
          reads: "Continues bounded scope state across source chunks."
      ._enter_scan_group:
        why:
          transforms: "Starts scoped named-condition tracking for a live brace group."
      ._exit_scan_group:
        why:
          transforms: "Restores named-condition state after a live brace group."

    InstantiationsFromRepo
    ----------------------
      ._active_branch:
        why:
          constructs: "Derives nested branch activity."
      ._advance_scan_control_command:
        why:
          constructs: "Applies shared conditional, named-boolean, and scope transitions."
      .MacroDefinition:
        why:
          constructs: "Carries each parsed value with its directive and location."
      ._line_column:
        why:
          constructs: "Provides original coordinates for each declaration token."
      .parse_declared_math_symbol_at:
        why:
          constructs: "Translates portable literal symbol declarations."
      .parse_declared_operator_at:
        why:
          constructs: "Parses declared operator definitions."
      .parse_def_at:
        why:
          constructs: "Parses primitive zero-argument definitions."
      .parse_newcommand_at:
        why:
          constructs: "Parses new, provide, and renew command definitions."
      ._delimited_def_end:
        why:
          constructs: "Consumes unsupported definitions without exposing nested aliases."
      ._unsupported_named_declaration_at:
        why:
          constructs: "Consumes named declarations that cannot be rendered safely."

    Pseudocode
    ----------
    - set active_fonts = previously active font declarations
    - for command_token in chunk text:
      - set scan_state = delimiter-adjusted scope state
      - if command_token is a live symbol-font declaration:
        - set active_fonts = active fonts with that declaration
      - if declaration is inactive or local:
        - continue
      - set parsed_definition = declaration parsed using active_fonts
      - if conditional truth is unknown:
        - set records = records with source-located conditional error
      - else:
        - set records = records with parsed source-located definition
    - return definition_records

    Wraps
    -----
    - none
    """
    active_fonts = dict(symbol_fonts)
    state = scan_state if scan_state is not None else TexScanState()
    records: list[MacroDefinition] = []
    idx = 0
    while idx < len(chunk.text):
        if chunk.text[idx] == "{":
            _enter_scan_group(state)
            idx += 1
            continue
        if chunk.text[idx] == "}":
            _exit_scan_group(state)
            idx += 1
            continue
        if chunk.text[idx] != "\\":
            idx += 1
            continue

        control_end = _advance_scan_control_command(
            chunk.text,
            idx,
            state,
            unknown_internal_conditionals=True,
        )
        if control_end is not None:
            idx = control_end
            continue
        branch = _active_branch(state.conditionals)
        uncertain = branch is None or chunk.conditional_uncertain
        globally_defined = state.global_prefix

        symbol_font = DECLARE_SYMBOL_FONT_RE.match(chunk.text, idx)
        if symbol_font is not None:
            font_name = symbol_font.group("name").strip()
            if not (
                branch is False
                or (branch is True and state.group_depth and not globally_defined)
            ):
                if uncertain:
                    state.uncertain_symbol_fonts.add(font_name)
                else:
                    active_fonts[font_name] = (
                        symbol_font.group("encoding").strip(),
                        symbol_font.group("family").strip(),
                        symbol_font.group("series").strip(),
                        symbol_font.group("shape").strip(),
                    )
                    state.uncertain_symbol_fonts.discard(font_name)
            idx = symbol_font.end()
            state.global_prefix = False
            continue

        parsed: tuple[str, MacroValue, int] | None = None
        directive = ""
        native_identity = False
        newcommand = NEWCOMMAND_RE.match(chunk.text, idx)
        if newcommand:
            parsed = parse_newcommand_at(chunk.text, idx)
            directive = newcommand.group("kind")
        if parsed is None:
            parsed = parse_declared_operator_at(chunk.text, idx)
            directive = "DeclareMathOperator"
        if parsed is None:
            parsed = parse_declared_math_symbol_at(chunk.text, idx, active_fonts)
            directive = "DeclareMathSymbol"
            native_identity = bool(parsed and parsed[1].strip() == f"\\{parsed[0]}")
        if parsed is None:
            parsed = parse_def_at(chunk.text, idx)
            primitive_def = DEF_RE.match(chunk.text, idx)
            directive = primitive_def.group("kind") if primitive_def else "def"
            globally_defined = globally_defined or directive == "gdef"
        if parsed is not None:
            name, value, next_idx = parsed
            if branch is False or (
                branch is True and state.group_depth and not globally_defined
            ):
                idx = next_idx
                state.global_prefix = False
                continue
            line, column = _line_column(chunk, idx)
            math_symbol = DECLARE_MATH_SYMBOL_RE.match(chunk.text, idx)
            uncertain_font = (
                math_symbol.group("font").strip()
                if math_symbol is not None
                and math_symbol.group("font").strip()
                in state.uncertain_symbol_fonts
                else None
            )
            records.append(
                MacroDefinition(
                    name=name,
                    value=value,
                    source_path=chunk.source_path,
                    line=line,
                    column=column,
                    directive=directive,
                    project_owned=chunk.project_owned,
                    adapter_owned=chunk.adapter_owned,
                    native_identity=native_identity,
                    declaration_error=(
                        (
                            f"a math symbol using font {uncertain_font!r} whose "
                            "active declaration cannot be determined statically"
                        )
                        if uncertain_font is not None
                        else (
                            "a definition in a conditional branch whose truth "
                            "cannot be determined statically"
                            if uncertain
                            else None
                        )
                    ),
                )
            )
            idx = next_idx
            state.global_prefix = False
            continue

        unsupported_named = _unsupported_named_declaration_at(chunk.text, idx)
        if unsupported_named is not None:
            unsupported_directive, unsupported_name, next_idx = unsupported_named
            if not (
                branch is False
                or (branch is True and state.group_depth and not globally_defined)
            ):
                line, column = _line_column(chunk, idx)
                records.append(
                    MacroDefinition(
                        name=unsupported_name,
                        value="",
                        source_path=chunk.source_path,
                        line=line,
                        column=column,
                        directive=unsupported_directive,
                        project_owned=chunk.project_owned,
                        adapter_owned=chunk.adapter_owned,
                        native_identity=not chunk.project_owned,
                        declaration_error=(
                            "a definition in a conditional branch whose truth "
                            "cannot be determined statically"
                            if uncertain
                            else f"an unsupported {unsupported_directive} declaration; "
                            "supported renderable declarations are newcommand, "
                            "renewcommand, source-resolved providecommand, "
                            "zero-argument def/gdef, let, DeclareMathOperator, and "
                            "representable DeclareMathSymbol"
                        ),
                    )
                )
            idx = next_idx
            state.global_prefix = False
            continue

        if newcommand:
            name_match = re.match(
                r"\s*(?:\{\s*)?\\([A-Za-z@]+)",
                chunk.text[newcommand.end() :],
            )
            if name_match:
                if branch is False or (
                    branch is True and state.group_depth and not globally_defined
                ):
                    idx = newcommand.end() + name_match.end()
                    state.global_prefix = False
                    continue
                line, column = _line_column(chunk, idx)
                records.append(
                    MacroDefinition(
                        name=name_match.group(1),
                        value="",
                        source_path=chunk.source_path,
                        line=line,
                        column=column,
                        directive=directive,
                        project_owned=chunk.project_owned,
                        adapter_owned=chunk.adapter_owned,
                        declaration_error=(
                            "a definition in a conditional branch whose truth "
                            "cannot be determined statically"
                            if uncertain
                            else "a malformed declaration; expected an optional numeric "
                            "arity from 0 through 9 followed by a balanced replacement body"
                        ),
                    )
                )
                idx = newcommand.end() + name_match.end()
                state.global_prefix = False
                continue

        def_end = _delimited_def_end(chunk.text, idx)
        if def_end is not None:
            unsupported_def = DEF_RE.match(chunk.text, idx)
            globally_defined = globally_defined or bool(
                unsupported_def and unsupported_def.group("kind") == "gdef"
            )
            if unsupported_def and not (
                branch is False
                or (branch is True and state.group_depth and not globally_defined)
            ):
                line, column = _line_column(chunk, idx)
                records.append(
                    MacroDefinition(
                        name=unsupported_def.group("name"),
                        value="",
                        source_path=chunk.source_path,
                        line=line,
                        column=column,
                        directive="def",
                        project_owned=chunk.project_owned,
                        adapter_owned=chunk.adapter_owned,
                        declaration_error=(
                            "a definition in a conditional branch whose truth "
                            "cannot be determined statically"
                            if uncertain
                            else "an unsupported parameterized or delimited def declaration"
                        ),
                    )
                )
            idx = def_end
            state.global_prefix = False
            continue

        alias = LET_RE.match(chunk.text, idx)
        if alias:
            left = alias.group("left")
            right = alias.group("right")
            if left != right and not (
                branch is False
                or (branch is True and state.group_depth and not globally_defined)
            ):
                line, column = _line_column(chunk, idx)
                records.append(
                    MacroDefinition(
                        name=left,
                        value=f"\\{right}",
                        source_path=chunk.source_path,
                        line=line,
                        column=column,
                        directive="let",
                        project_owned=chunk.project_owned,
                        adapter_owned=chunk.adapter_owned,
                        let_target=right,
                        declaration_error=(
                            "a definition in a conditional branch whose truth "
                            "cannot be determined statically"
                            if uncertain
                            else None
                        ),
                    )
                )
            idx = alias.end()
            state.global_prefix = False
            continue
        state.global_prefix = False
        idx += 1
    symbol_fonts.update(active_fonts)
    return records


def collect_macro_definitions(text: str) -> dict[str, MacroValue]:
    """Parse flattened text for compatibility callers without source diagnostics.

    Intent
    ------
    Preserve the earlier in-memory macro parser contract.

    Rationale
    ---------
    Existing callers can retain value-only parsing while new paths keep provenance.

    CallsFromRepo
    -------------
      .parse_declared_math_symbol_at:
        why:
          reads: "Recognizes portable math-symbol declarations."
      .parse_declared_operator_at:
        why:
          reads: "Recognizes declared operator definitions."
      .parse_def_at:
        why:
          reads: "Recognizes simple primitive definitions."
      .parse_newcommand_at:
        why:
          reads: "Recognizes new-command family declarations."

    InstantiationsFromRepo
    ----------------------
      .collect_symbol_fonts:
        why:
          constructs: "Builds font context for literal symbol declarations."

    Pseudocode
    ----------
    - symbol_fonts = collect_symbol_fonts(text)
    - set macro_values = representable definitions parsed from text using symbol_fonts
    - return macro_values

    Wraps
    -----
    - none
    """
    macros: dict[str, MacroValue] = {}
    active_fonts = collect_symbol_fonts(text)
    idx = 0
    while idx < len(text):
        parsed = (
            parse_newcommand_at(text, idx)
            or parse_declared_operator_at(text, idx)
            or parse_declared_math_symbol_at(text, idx, active_fonts)
            or parse_def_at(text, idx)
        )
        if parsed is None:
            idx += 1
            continue
        name, value, idx = parsed
        macros[name] = value
    return macros


def macro_body_text(body: object) -> str:
    """Project one schema macro value to text used for dependency discovery.

    Intent
    ------
    Expose replacement and optional-default text from either tuple encoding.

    Rationale
    ---------
    Recursive closure must inspect all text that can reference another command.

    Pseudocode
    ----------
    - if body is a supported parameterized tuple:
      - set text = replacement plus optional default when present
      - return text
    - return body converted to text

    Wraps
    -----
    - none
    """
    if isinstance(body, list) and len(body) in {2, 3}:
        if isinstance(body[0], str) and isinstance(body[1], int):
            replacement = body[0]
        elif isinstance(body[0], int) and isinstance(body[1], str):
            replacement = body[1]
        else:
            return str(body)
        if len(body) == 3 and isinstance(body[2], str):
            return f"{replacement}\n{body[2]}"
        return replacement
    return str(body)


def referenced_macros(body: object) -> set[str]:
    """Return control-sequence names referenced by one macro value.

    Intent
    ------
    Identify direct dependency names in replacement and default text.

    Rationale
    ---------
    Closure traversal operates on macro names rather than raw source strings.

    CallsFromRepo
    -------------
      .macro_body_text:
        why:
          reads: "Projects schema tuple encodings to searchable source text."

    Pseudocode
    ----------
    - set body_text = macro body projected to text
    - return alphabetic and at-sign control-sequence names from body text

    Wraps
    -----
    - none
    """
    return {
        match.group(1)
        for match in COMMAND_RE.finditer(macro_body_text(body))
        if match.group(1).isalpha() or "@" in match.group(1)
    }


def _normalize_macro_value(value: MacroValue) -> tuple[MacroValue, dict[str, int]]:
    """Normalize one macro tuple to MathJax-native semantics.

    Intent
    ------
    Enforce replacement-first tuple order without changing source commands.

    Rationale
    ---------
    Tuple normalization supports semantic comparison while preserving TeX meaning.

    Pseudocode
    ----------
    - if macro value is parameterized:
      - set normalized_value = replacement-first tuple
    - return normalized_value and an empty compatibility count

    Wraps
    -----
    - none
    """
    if isinstance(value, str):
        return value, {}
    if len(value) not in {2, 3}:
        return value, {}
    if isinstance(value[0], int) and isinstance(value[1], str):
        normalized: list[object] = [value[1], value[0]]
    elif isinstance(value[0], str) and isinstance(value[1], int):
        normalized = [value[0], value[1]]
    else:
        return value, {}
    if len(value) == 3:
        normalized.append(value[2])
    return normalized, {}


def normalize_package_commands(
    macros: dict[str, object],
) -> tuple[dict[str, MacroValue], dict[str, int]]:
    """Normalize tuple order for MathJax configuration.

    Intent
    ------
    Preserve the compatibility API without rewriting source command semantics.

    Rationale
    ---------
    The compatibility API must share the same normalizer as the source-aware path.

    InstantiationsFromRepo
    ----------------------
      ._normalize_macro_value:
        why:
          transforms: "Produces each canonical macro value and rewrite count."

    Pseudocode
    ----------
    - for macro_definition in macros:
      - normalized_value = _normalize_macro_value(macro_definition)
      - set normalized_macros = normalized_macros with normalized_value
    - return normalized_macros and an empty rewrite-count mapping

    Wraps
    -----
    - none
    """
    normalized: dict[str, MacroValue] = {}
    for name, value in macros.items():
        if not isinstance(value, (str, list)):
            continue
        normalized_value, _ = _normalize_macro_value(value)
        normalized[name] = normalized_value
    return normalized, {}


def _definition_catalog(
    tex_entrypoint: Path,
) -> tuple[dict[str, MacroDefinition], dict[str, list[MacroDefinition]]]:
    """Build effective definitions and unresolved duplicate candidates.

    Intent
    ------
    Apply source-order provide, renew, primitive, and alias override semantics.

    Rationale
    ---------
    Declaration semantics distinguish invalid duplicates from intentional overrides.

    CallsFromRepo
    -------------
      ._definition_records_from_chunk:
        why:
          parses: "Yields source-located declarations from each ordered fragment."

    InstantiationsFromRepo
    ----------------------
      .TexScanState:
        why:
          constructs: "Carries bounded scope state across expanded source chunks."
      ._normalize_macro_value:
        why:
          transforms: "Canonicalizes each record before semantic comparison."
      .collect_tex_chunks:
        why:
          constructs: "Produces the source-ordered reachable fragment sequence."

    Pseudocode
    ----------
    - source_chunks = collect_tex_chunks(tex_entrypoint)
    - for source_chunk in source_chunks:
      - definition_records = @._definition_records_from_chunk(source_chunk)
      - normalized_record = _normalize_macro_value(definition_records)
      - if normalized record is semantically identical or an intentional override:
        - set effective_definitions = effective_definitions with normalized_record
      - if differing new declarations remain incompatible:
        - set conflicts = conflicts with differing duplicate records
    - return effective_definitions and conflicts

    Wraps
    -----
    - none
    """
    entrypoint = tex_entrypoint.resolve()
    chunks = collect_tex_chunks(entrypoint, project_root=entrypoint.parent)
    definitions: dict[str, MacroDefinition] = {}
    conflicts: dict[str, list[MacroDefinition]] = {}
    symbol_fonts: dict[str, tuple[str, str, str, str]] = {}
    scan_state = TexScanState()

    for chunk in chunks:
        for record in _definition_records_from_chunk(chunk, symbol_fonts, scan_state):
            if record.directive == "let" and record.let_target is not None:
                target = definitions.get(record.let_target)
                if target is None:
                    record = replace(
                        record,
                        external_snapshot_of=record.let_target,
                    )
                elif target.external_snapshot_of is not None:
                    record = replace(
                        record,
                        value=target.value,
                        external_snapshot_of=target.external_snapshot_of,
                        declaration_error=target.declaration_error,
                    )
                else:
                    record = replace(
                        record,
                        value=target.value,
                        declaration_error=target.declaration_error,
                    )
            normalized, _ = _normalize_macro_value(record.value)
            record = replace(record, value=normalized)
            previous = definitions.get(record.name)
            if record.directive == "providecommand":
                if previous is None:
                    if record.project_owned:
                        record = replace(
                            record,
                            declaration_error=(
                                "an ambiguous providecommand with no earlier source "
                                "binding; an existing external or renderer-native "
                                "binding cannot be determined statically"
                            ),
                        )
                    else:
                        record = replace(
                            record,
                            native_identity=True,
                            declaration_error=None,
                        )
                    definitions[record.name] = record
                continue
            if previous is None:
                definitions[record.name] = record
                continue
            if previous.adapter_owned and record.adapter_owned:
                if previous.value == record.value:
                    continue
                conflicts.setdefault(record.name, [previous]).append(record)
                definitions[record.name] = record
                continue
            if previous.value == record.value:
                if (record.project_owned or record.adapter_owned) and not (
                    previous.project_owned
                ):
                    definitions[record.name] = record
                continue
            if record.adapter_owned and not previous.project_owned:
                definitions[record.name] = record
                continue
            if previous.adapter_owned and not record.project_owned:
                continue
            if record.directive in {"renewcommand", "def", "gdef", "let"}:
                definitions[record.name] = record
                conflicts.pop(record.name, None)
                continue
            conflicts.setdefault(record.name, [previous]).append(record)
            definitions[record.name] = record
    return definitions, conflicts


def _command_names(texts: Iterable[str]) -> list[str]:
    """Return graph-visible TeX command names in first-seen order.

    Intent
    ------
    Extract and deduplicate control-sequence names across rendered graph strings.

    Rationale
    ---------
    Stable root order makes closure selection and diagnostics deterministic.

    Pseudocode
    ----------
    - set command_names = first-seen control sequences from texts
    - return command_names

    Wraps
    -----
    - none
    """
    return list(
        dict.fromkeys(
            match.group(1)
            for text in texts
            for match in COMMAND_RE.finditer(text)
            if match.group(1).isalpha() or "@" in match.group(1)
        )
    )


def _raw_definition_wraps_external_snapshot(
    name: str,
    definition: MacroDefinition,
    definitions: dict[str, MacroDefinition],
) -> bool:
    """Return whether a raw definition wraps its pre-existing external binding.

    Intent
    ------
    Recognize distribution redefinitions that save then wrap a native command.

    Rationale
    ---------
    Serializing such TeX-engine wrappers would replace a renderer-native command
    with implementation machinery whose saved binding does not exist in MathJax.

    InstantiationsFromRepo
    ----------------------
      .referenced_macros:
        why:
          constructs: "Finds the source-known dependency path to an external snapshot."

    Pseudocode
    ----------
    - if definition is project-owned or adapter-owned:
      - return false
    - set pending_names = @.referenced_macros(definition value)
    - while pending_names is not empty:
      - set current_definition = definition for next pending name
      - if current_definition snapshots the external binding of name:
        - return true
      - if current_definition is raw and source-known:
        - set pending_names = pending names plus its referenced macros
    - return false

    Wraps
    -----
    - none
    """
    if definition.project_owned or definition.adapter_owned:
        return False
    pending = list(referenced_macros(definition.value))
    visited: set[str] = set()
    while pending:
        dependency = pending.pop()
        if dependency in visited:
            continue
        visited.add(dependency)
        dependency_definition = definitions.get(dependency)
        if dependency_definition is None:
            continue
        if dependency_definition.external_snapshot_of == name:
            return True
        if (
            not dependency_definition.project_owned
            and not dependency_definition.adapter_owned
            and dependency_definition.external_snapshot_of is None
        ):
            pending.extend(referenced_macros(dependency_definition.value))
    return False


def _extract_renderable_macro_definitions(
    *,
    tex_entrypoint: Path,
    graph_text: Iterable[str],
) -> dict[str, MacroDefinition]:
    """Select the source-aware recursive macro closure used by graph text.

    Intent
    ------
    Traverse source-defined roots and reject relevant conflicts or cycles.

    Rationale
    ---------
    The finalizer needs provenance until merge diagnostics have been completed.

    CallsFromRepo
    -------------
      ._command_names:
        why:
          reads: "Identifies the graph-visible root command sequence."
      .referenced_macros:
        why:
          reads: "Finds direct dependencies while walking relevant definitions."
      ._raw_definition_wraps_external_snapshot:
        why:
          reads: "Classifies raw native-command wrappers before serialization."

    InstantiationsFromRepo
    ----------------------
      ._definition_catalog:
        why:
          constructs: "Provides effective definitions and conflict candidates."

    Pseudocode
    ----------
    - definition_catalog = _definition_catalog(tex_entrypoint)
    - graph_commands = @._command_names(graph_text)
    - set roots = graph_commands present in definition_catalog
    - set direct_dependencies = @.referenced_macros(definition_catalog replacements)
    - for root_command in graph_commands:
      - if root command is a raw wrapper of its external snapshot:
        - continue
      - set selected_definitions = direct_dependencies selected with cycle checks
    - return selected_definitions in source order

    Wraps
    -----
    - none
    """
    entrypoint = tex_entrypoint.resolve()
    if not entrypoint.is_file():
        raise ValueError(f"TeX entrypoint not found: {entrypoint}")
    definitions, conflicts = _definition_catalog(entrypoint)

    roots = [
        name
        for name in _command_names(graph_text)
        if name in definitions and not definitions[name].native_identity
    ]

    selected: dict[str, MacroDefinition] = {}
    visiting: list[str] = []

    def describe_chain(names: list[str]) -> str:
        """Format one dependency chain with source locations.

        Intent
        ------
        Attach exact provenance to cycle and unsupported-definition diagnostics.

        Rationale
        ---------
        A macro name alone does not identify which reachable source caused failure.

        Pseudocode
        ----------
        - return macro names paired with definition locations in dependency order

        Wraps
        -----
        - none
        """
        return " -> ".join(
            f"\\{item} ({definitions[item].location})" for item in names
        )

    def visit(name: str) -> None:
        """Add one definition after recursively selecting its dependencies.

        Intent
        ------
        Perform depth-first closure while detecting relevant cycles and conflicts.

        Rationale
        ---------
        Postorder insertion ensures dependencies precede macros that reference them.

        CallsFromRepo
        -------------
          ._raw_definition_wraps_external_snapshot:
            why:
              reads: "Stops at a raw wrapper around its pre-existing binding."
          .referenced_macros:
            why:
              reads: "Finds direct command dependencies in the current replacement."

        Pseudocode
        ----------
        - if name is already selected:
          - return
        - if name is currently visiting or has conflicting records:
          - raise source-located macro error
        - dependencies = @.referenced_macros(current definition)
        - if the current definition is an explicit MathJax adapter:
          - set dependencies = project and adapter definitions only
        - for dependency_name in dependencies:
          - set selected_definitions = selected_definitions with visited dependency
        - set selected_definitions = selected_definitions with current definition

        Wraps
        -----
        - none
        """
        if name in selected:
            return
        if name in visiting:
            cycle = visiting + [name]
            raise ValueError(
                f"Cyclic relevant macro definitions: {describe_chain(cycle)}"
            )
        definition = definitions.get(name)
        if definition is None or definition.native_identity:
            return
        if _raw_definition_wraps_external_snapshot(name, definition, definitions):
            return
        snapshot_target = definition.external_snapshot_of
        snapshot_target_definition = (
            definitions.get(snapshot_target) if snapshot_target is not None else None
        )
        if (
            snapshot_target_definition is not None
            and not snapshot_target_definition.native_identity
            and not _raw_definition_wraps_external_snapshot(
                snapshot_target,
                snapshot_target_definition,
                definitions,
            )
        ):
            raise ValueError(
                f"Relevant macro \\{name} is an external \\let snapshot of "
                f"\\{snapshot_target} at {definition.location}, but that target was "
                "defined later and would be emitted; a self-contained MathJax macro "
                "map cannot preserve the earlier binding as a live alias."
            )
        if definition.declaration_error is not None:
            raise ValueError(
                f"Relevant macro \\{name} has {definition.declaration_error} "
                f"at {definition.location}. Dependency chain: "
                f"{describe_chain(visiting + [name])}"
            )
        if (
            isinstance(definition.value, list)
            and len(definition.value) in {2, 3}
            and isinstance(definition.value[1], int)
            and not isinstance(definition.value[1], bool)
            and not 0 <= definition.value[1] <= 9
        ):
            raise ValueError(
                f"Relevant macro \\{name} has unsupported MathJax arity "
                f"{definition.value[1]} at {definition.location}; expected 0 through 9."
            )
        duplicates = conflicts.get(name)
        if duplicates:
            locations = ", ".join(item.location for item in duplicates)
            raise ValueError(
                f"Conflicting definitions for relevant macro \\{name}: {locations}"
            )
        visiting.append(name)
        for dependency in referenced_macros(definition.value):
            if dependency == name:
                described = describe_chain(visiting) + f" -> \\{name}"
                raise ValueError(f"Cyclic relevant macro definitions: {described}")
            dependency_definition = definitions.get(dependency)
            if dependency_definition is not None:
                visit(dependency)
        visiting.pop()
        selected[name] = definition

    for root in roots:
        visit(root)
    return {name: selected[name] for name in definitions if name in selected}


def extract_renderable_macros(
    *,
    tex_entrypoint: Path,
    graph_text: Iterable[str],
    include_records: bool = False,
) -> dict[str, MacroValue] | RenderableMacroExtraction:
    """Return the normalized recursive closure of macros used by graph text.

    Intent
    ------
    Expose value-only MathJax definitions for the relevant source-aware closure.

    Rationale
    ---------
    Public callers get schema values by default; finalization can retain internal records.

    InstantiationsFromRepo
    ----------------------
      ._extract_renderable_macro_definitions:
        why:
          transforms: "Produces the validated source-aware macro closure."
      .RenderableMacroExtraction:
        why:
          constructs: "Pairs projected schema values with exact source records on request."

    Pseudocode
    ----------
    - macro_records = _extract_renderable_macro_definitions(tex_entrypoint and graph_text)
    - set macro_values = normalized values projected from macro_records
    - if source records were requested:
      - return macro values paired with source records
    - return macro_values

    Wraps
    -----
    - none
    """
    definitions = _extract_renderable_macro_definitions(
        tex_entrypoint=tex_entrypoint,
        graph_text=graph_text,
    )
    values = {name: definition.value for name, definition in definitions.items()}
    if include_records:
        return RenderableMacroExtraction(values=values, records=definitions)
    return values


def dependency_closure(
    macros: dict[str, object],
    roots: Iterable[str] | None = None,
) -> dict[str, object]:
    """Return the acyclic recursive closure of an in-memory macro map.

    Intent
    ------
    Preserve the value-only closure API used by compatibility callers.

    Rationale
    ---------
    Callers without source records still need deterministic cycle-safe selection.

    CallsFromRepo
    -------------
      .referenced_macros:
        why:
          reads: "Finds direct dependencies in each value-only definition."

    Pseudocode
    ----------
    - set requested_roots = requested roots or every macro when roots are absent
    - for root in requested_roots:
      - set closed_macros = closed macros plus the acyclic recursive visit result
    - return selected definitions in input order

    Wraps
    -----
    - none
    """
    closed: dict[str, object] = {}
    visiting: set[str] = set()

    def visit(name: str) -> bool:
        """Select one in-memory macro after its acyclic dependencies.

        Intent
        ------
        Build the compatibility closure using depth-first traversal.

        Rationale
        ---------
        A separate visiting set distinguishes cycles from completed definitions.

        CallsFromRepo
        -------------
          .referenced_macros:
            why:
              reads: "Finds child macro names in the current definition."

        Pseudocode
        ----------
        - if macro is selected or external:
          - return true
        - if macro is currently visiting:
          - return false
        - set valid = every in-map dependency has a valid recursive visit
        - if valid:
          - set closed_macros = closed macros plus current macro
        - return whether the macro is acyclic

        Wraps
        -----
        - none
        """
        if name in closed:
            return True
        if name not in macros:
            return True
        if name in visiting:
            return False
        visiting.add(name)
        valid = all(
            dependency == name
            or dependency not in macros
            or visit(dependency)
            for dependency in referenced_macros(macros[name])
        )
        visiting.remove(name)
        if valid:
            closed[name] = macros[name]
        return valid

    for root in macros if roots is None else dict.fromkeys(roots):
        visit(root)
    return {name: closed[name] for name in macros if name in closed}


def extract_macros(entrypoint: Path) -> dict[str, MacroValue]:
    """Return every project-owned macro through the public normalized extractor.

    Intent
    ------
    Preserve CLI-wide extraction while sharing the graph-relevant implementation.

    Rationale
    ---------
    One extractor prevents package normalization from diverging between skill paths.

    InstantiationsFromRepo
    ----------------------
      ._definition_catalog:
        why:
          constructs: "Identifies all project-owned command roots for compatibility."
      .extract_renderable_macros:
        why:
          transforms: "Produces the normalized recursive closure for those roots."

    Pseudocode
    ----------
    - definition_catalog = _definition_catalog(entrypoint)
    - set project_commands = project-owned names from definition_catalog
    - return extract_renderable_macros(entrypoint and project_commands)

    Wraps
    -----
    - none
    """
    entrypoint = entrypoint.resolve()
    definitions, _ = _definition_catalog(entrypoint)
    project_commands = [
        f"\\{name}" for name, definition in definitions.items() if definition.project_owned
    ]
    return extract_renderable_macros(
        tex_entrypoint=entrypoint,
        graph_text=project_commands,
    )


def default_output_path(entrypoint: Path) -> Path:
    """Return the conventional build path for extracted macro JSON.

    Intent
    ------
    Keep CLI output beside the source project under its build directory.

    Rationale
    ---------
    Compatibility callers rely on a deterministic default location.

    Pseudocode
    ----------
    - return entrypoint build directory joined with a stem-derived filename

    Wraps
    -----
    - none
    """
    return entrypoint.resolve().parent / "_build" / f"{entrypoint.stem}-mathjax-macros.json"


def write_macros(macros: dict[str, object], out_path: Path) -> None:
    """Write a stable indented macro mapping to one JSON path.

    Intent
    ------
    Persist compatibility extractor output for command-line callers.

    Rationale
    ---------
    Stable ordering makes generated macro maps reviewable and reproducible.

    Pseudocode
    ----------
    - set output_parent = parent directory of output path
    - set persisted_output = output path containing sorted indented macro JSON

    Wraps
    -----
    - none
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(macros, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class Interface(PythonArgvMachineInterface):
    """Expose macro extraction through the registered Python argv gateway.

    Intent
    ------
    Adapt dispatcher argv to the module's command-line entrypoint.

    Rationale
    ---------
    Registered interfaces require a machine-readable process gateway.

    Pseudocode
    ----------
    - set interface_contract = dispatcher argv adapted by the run method
    - return interface contract

    Wraps
    -----
    - none
    """
    prog = "tex_macro_reader.py"

    def run(self, argv: list[str]) -> int:
        """Run macro extraction with dispatcher-supplied arguments.

        Intent
        ------
        Implement the machine interface's argv contract.

        Rationale
        ---------
        The gateway reports process success after the shared entrypoint returns.

        CallsFromRepo
        -------------
          .main:
            why:
              dispatches: "Executes the registered macro-extraction command before adapting its void result to zero."

        Pseudocode
        ----------
        - set ignored_result = @.main(argv)
        - return success

        Wraps
        -----
        - none
        """
        main(argv)
        return 0


def main(argv: Iterable[str] | None = None) -> None:
    """Parse CLI arguments, extract macros, and report the output artifact.

    Intent
    ------
    Provide the compatibility command-line surface for macro extraction.

    Rationale
    ---------
    The registered gateway and direct CLI share one validated execution path.

    CallsFromRepo
    -------------
      .default_output_path:
        why:
          reads: "Selects the conventional destination when none is supplied."
      .write_macros:
        why:
          writes: "Persists the extracted macro mapping."

    InstantiationsFromRepo
    ----------------------
      .extract_macros:
        why:
          constructs: "Builds the normalized project macro closure."

    Pseudocode
    ----------
    - set arguments = parsed entrypoint and optional output arguments
    - if TeX entrypoint is missing:
      - raise missing entrypoint error
    - set macros = extracted macros from the entrypoint
    - @.write_macros(macros, output_path)
    - set standard_output = machine-readable output metadata

    Wraps
    -----
    - none
    """
    parser = argparse.ArgumentParser(
        description="Extract MathJax macro definitions from a TeX entrypoint."
    )
    parser.add_argument("entrypoint", help="TeX entrypoint, e.g. main.tex or or.tex")
    parser.add_argument(
        "--out",
        help="Output JSON path. Defaults to _build/<entry>-mathjax-macros.json",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    entrypoint = Path(args.entrypoint).resolve()
    if not entrypoint.exists():
        raise SystemExit(f"TeX entrypoint not found: {entrypoint}")

    macros = extract_macros(entrypoint)
    out_path = Path(args.out).resolve() if args.out else default_output_path(entrypoint)
    write_macros(macros, out_path)
    print(
        json.dumps(
            {
                "entrypoint": str(entrypoint),
                "out": str(out_path),
                "macros": len(macros),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
