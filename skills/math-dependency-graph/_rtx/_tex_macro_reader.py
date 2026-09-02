#!/usr/bin/env python3
"""Extract the graph-relevant TeX macro closure in MathJax-native form."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable

from officina.runtime.python_machine_interface import PythonArgvMachineInterface


MacroValue = str | list[object]


LOCAL_INCLUDE_RE = re.compile(
    r"\\(?P<cmd>input|include|usepackage|RequirePackage(?:WithOptions)?|"
    r"documentclass|LoadClass(?:WithOptions)?)"
    r"(?:\s*\[[^\]]*\])?"
    r"\s*\{(?P<names>[^{}]+)\}"
)
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
DEF_RE = re.compile(r"\\def\s*\\([A-Za-z@]+)")
NEWCOMMAND_RE = re.compile(
    r"\\(?P<kind>(?:re)?newcommand|providecommand)\s*\*?\s*"
)
@dataclass(frozen=True)
class MacroDefinition:
    """Store one normalized macro definition and its source provenance.

    Intent
    ------
    Carry the renderable value together with directive and exact source location.

    Rationale
    ---------
    Conflict and cycle errors need provenance that the public macro map omits.

    Pseudocode
    ----------
    - set macro_definition = name, value, directive, ownership, and location
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
    native_identity: bool = False

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


def strip_comments(text: str) -> str:
    """Remove TeX comments while preserving escaped percent signs and lines."""
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
    """Read a balanced ``{...}`` group beginning at ``start``."""
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
    while idx < len(text) and text[idx].isspace():
        idx += 1
    return idx


def resolve_tex_path(include_name: str, current_dir: Path, suffix: str = ".tex") -> Path:
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


def collect_tex_chunks(
    entrypoint: Path,
    *,
    project_root: Path,
    seen: set[Path] | None = None,
) -> list[TexChunk]:
    """Collect the dependency tree in TeX source order with file provenance.

    Intent
    ------
    Expand reachable includes while retaining the exact origin of each fragment.

    Rationale
    ---------
    Source order governs TeX redefinitions and provenance governs diagnostics.

    CallsFromRepo
    -------------
      .dependency_paths:
        why:
          reads: "Finds local or installed source dependencies at each load command."
      .read_tex_text:
        why:
          reads: "Decodes the current TeX source file."

    InstantiationsFromRepo
    ----------------------
      .TexChunk:
        why:
          constructs: "Carries each source fragment with its location metadata."
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
    - decoded_text = @.read_tex_text(entrypoint)
    - source_text = strip_comments(decoded_text)
    - project_owned = _path_is_within(entrypoint, project_root)
    - for load_command in source_text:
      - dependency_sources = @.dependency_paths(load_command)
      - child_chunks = collect_tex_chunks(dependency_sources)
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
        return []
    active_seen.add(source_path)
    text = strip_comments(read_tex_text(source_path))
    project_owned = _path_is_within(source_path, project_root)

    chunks: list[TexChunk] = []
    last = 0
    for match in LOCAL_INCLUDE_RE.finditer(text):
        chunks.append(
            TexChunk(
                source_path,
                text[last : match.start()],
                text.count("\n", 0, last),
                last - text.rfind("\n", 0, last) - 1,
                project_owned,
            )
        )
        for child in dependency_paths(match.group("cmd"), match.group("names"), source_path.parent):
            chunks.extend(
                collect_tex_chunks(child, project_root=project_root, seen=active_seen)
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
    name = match.group(1)
    pos = skip_space(text, match.end())
    if pos >= len(text) or text[pos] != "{":
        return None
    try:
        body, end_pos = read_balanced_group(text, pos)
    except ValueError:
        return None
    return name, body, end_pos


def parse_declared_operator_at(text: str, idx: int) -> tuple[str, str, int] | None:
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


def _definition_records_from_chunk(
    chunk: TexChunk,
    symbol_fonts: dict[str, tuple[str, str, str, str]],
) -> list[MacroDefinition]:
    """Parse one TeX chunk into source-located macro definition records.

    Intent
    ------
    Recognize representable declarations and supported aliases in source order.

    Rationale
    ---------
    A single record stream lets later merge logic apply TeX redefinition semantics.

    InstantiationsFromRepo
    ----------------------
      .MacroDefinition:
        why:
          constructs: "Carries each parsed value with its directive and location."
      ._line_column:
        why:
          constructs: "Provides original coordinates for each declaration token."
      .collect_symbol_fonts:
        why:
          constructs: "Builds the font declarations active in this source chunk."
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

    Pseudocode
    ----------
    - active_fonts = collect_symbol_fonts(chunk text)
    - for command_token in chunk text:
      - parsed_definition = parse_newcommand_at(command_token)
      - parsed_definition = parse_declared_operator_at(command_token)
      - parsed_definition = parse_declared_math_symbol_at(command_token and active_fonts)
      - parsed_definition = parse_def_at(command_token)
      - source_position = _line_column(chunk, command_token)
      - macro_record = MacroDefinition(parsed_definition and source_position)
      - set definition_records = definition_records with macro_record
    - return definition_records

    Wraps
    -----
    - none
    """
    active_fonts = dict(symbol_fonts)
    active_fonts.update(collect_symbol_fonts(chunk.text))
    records: list[MacroDefinition] = []
    idx = 0
    while idx < len(chunk.text):
        if chunk.text[idx] != "\\":
            idx += 1
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
            directive = "def"
        if parsed is not None:
            name, value, next_idx = parsed
            line, column = _line_column(chunk, idx)
            records.append(
                MacroDefinition(
                    name=name,
                    value=value,
                    source_path=chunk.source_path,
                    line=line,
                    column=column,
                    directive=directive,
                    project_owned=chunk.project_owned,
                    native_identity=native_identity,
                )
            )
            idx = next_idx
            continue

        alias = LET_RE.match(chunk.text, idx)
        if alias:
            left = alias.group("left")
            right = alias.group("right")
            if left != right:
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
                    )
                )
            idx = alias.end()
            continue
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
    Only duplicate new definitions remain conflicts; intentional overrides are valid.

    CallsFromRepo
    -------------
      ._definition_records_from_chunk:
        why:
          parses: "Yields source-located declarations from each ordered fragment."

    InstantiationsFromRepo
    ----------------------
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
      - if normalized_record is an intentional override:
        - set effective_definitions = effective_definitions with normalized_record
      - else:
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

    for chunk in chunks:
        for record in _definition_records_from_chunk(chunk, symbol_fonts):
            normalized, _ = _normalize_macro_value(record.value)
            record = replace(record, value=normalized)
            previous = definitions.get(record.name)
            if previous is None:
                definitions[record.name] = record
                continue
            if previous.value == record.value:
                continue
            if record.directive == "providecommand":
                continue
            if record.directive in {"renewcommand", "def", "let"}:
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
    roots: list[str] = []
    for name in _command_names(graph_text):
        definition = definitions.get(name)
        if definition is not None and not definition.native_identity:
            roots.append(name)

    selected: dict[str, MacroDefinition] = {}
    visiting: list[str] = []

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
            cycle = visiting[visiting.index(name) :] + [name]
            described = " -> ".join(
                f"\\{item} ({definitions[item].location})" for item in cycle
            )
            raise ValueError(f"Cyclic relevant macro definitions: {described}")
        definition = definitions.get(name)
        if definition is None or definition.native_identity:
            return
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
                described = f"\\{name} ({definition.location}) -> \\{name}"
                raise ValueError(f"Cyclic relevant macro definitions: {described}")
            if dependency in definitions:
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
) -> dict[str, MacroValue]:
    """Return the normalized recursive closure of macros used by graph text.

    Intent
    ------
    Expose value-only MathJax definitions for the relevant source-aware closure.

    Rationale
    ---------
    Public callers need schema values while conflict handling retains internal records.

    InstantiationsFromRepo
    ----------------------
      ._extract_renderable_macro_definitions:
        why:
          transforms: "Produces the validated source-aware macro closure."

    Pseudocode
    ----------
    - macro_records = _extract_renderable_macro_definitions(tex_entrypoint and graph_text)
    - set macro_values = normalized values projected from macro_records
    - return macro_values

    Wraps
    -----
    - none
    """
    definitions = _extract_renderable_macro_definitions(
        tex_entrypoint=tex_entrypoint,
        graph_text=graph_text,
    )
    return {name: definition.value for name, definition in definitions.items()}


def dependency_closure(
    macros: dict[str, object],
    roots: Iterable[str] | None = None,
) -> dict[str, object]:
    """Compatibility closure for in-memory macro maps."""
    closed: dict[str, object] = {}
    visiting: set[str] = set()

    def visit(name: str) -> bool:
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
    return entrypoint.resolve().parent / "_build" / f"{entrypoint.stem}-mathjax-macros.json"


def write_macros(macros: dict[str, object], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(macros, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class Interface(PythonArgvMachineInterface):
    prog = "tex_macro_reader.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


def main(argv: Iterable[str] | None = None) -> None:
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
