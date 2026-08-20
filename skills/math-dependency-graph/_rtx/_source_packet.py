#!/usr/bin/env python3
"""Prepare an active, source-ordered TeX packet for semantic extraction.

This machine performs document discovery only. It follows local ``\\input`` and
``\\include`` references, removes inactive TeX comments while preserving every
source line's exact file/line label. It also lists the locations and literal
names of base theorem-like ``\\begin`` markers as a syntactic coverage checklist.
It deliberately makes no decision about their statements, graph inclusion,
classification, or dependencies. Those decisions remain owned by the skill's
LLM instruction interfaces.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile
import time
from typing import Iterable

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._tex_macro_reader import read_tex_text, strip_comments
except ImportError:  # pragma: no cover - supports direct script execution
    from _tex_macro_reader import read_tex_text, strip_comments


SOURCE_INCLUDE_RE = re.compile(
    r"\\(?P<command>input|include)\s*"
    r"(?:\{(?P<braced>[^{}]+)\}|(?P<plain>[^\s%{}]+))"
)
VISIBLE_ENVIRONMENT_TYPES = (
    "assumption",
    "definition",
    "notation",
    "lemma",
    "proposition",
    "theorem",
    "corollary",
    "remark",
    "example",
)
VISIBLE_ENVIRONMENT_ALTERNATION = "|".join(VISIBLE_ENVIRONMENT_TYPES)
VISIBLE_ENVIRONMENT_RE = re.compile(
    rf"\\begin\{{(?P<environment>{VISIBLE_ENVIRONMENT_ALTERNATION})\}}"
)
RESTATABLE_ENVIRONMENT_RE = re.compile(
    rf"\\begin\{{restatable\}}(?:\[[^]]*\])?"
    rf"\{{(?P<environment>{VISIBLE_ENVIRONMENT_ALTERNATION})\}}"
)
NEW_THEOREM_RE = re.compile(
    r"\\newtheorem\*?\s*\{(?P<environment>[A-Za-z@][A-Za-z0-9@*:-]*)\}"
)
DECLARE_THEOREM_RE = re.compile(
    r"\\declaretheorem(?:\s*\[[^]]*\])?\s*"
    r"\{(?P<environment>[A-Za-z@][A-Za-z0-9@*:-]*)\}"
)
NEW_ENVIRONMENT_WRAPPER_RE = re.compile(
    r"\\newenvironment\*?\s*\{(?P<environment>[A-Za-z@][A-Za-z0-9@*:-]*)\}"
    r"(?P<body>.{0,1200}?)\\begin\{(?:"
    + VISIBLE_ENVIRONMENT_ALTERNATION
    + r")\}",
    re.DOTALL,
)


def visible_environments_on_line(text: str) -> tuple[tuple[str, str | None], ...]:
    """Return literal base theorem-like begin markers without reading semantics."""
    anchors = [
        (match.group("environment"), None)
        for match in VISIBLE_ENVIRONMENT_RE.finditer(text)
    ]
    anchors.extend(
        (match.group("environment"), "restatable")
        for match in RESTATABLE_ENVIRONMENT_RE.finditer(text)
    )
    return tuple(anchors)


def visible_environment_ranges(
    numbered_lines: Iterable[tuple[int, str]],
    *,
    declaration_text: str | None = None,
) -> tuple[tuple[int, int, str, str | None], ...]:
    """Pair base and project-declared theorem-like blocks with matching ends.

    The declaration scan is syntactic. When ``declaration_text`` is supplied,
    it carries the complete active project packet so declarations in a preamble
    protect matching environments in included files. This prevents chunk
    ownership from bisecting author-visible custom theorem/example-like blocks
    without asking deterministic code to classify their mathematical meaning.
    """
    lines = tuple(numbered_lines)
    joined = (
        declaration_text
        if declaration_text is not None
        else "\n".join(text for _line, text in lines)
    )
    declared = {
        match.group("environment")
        for pattern in (NEW_THEOREM_RE, DECLARE_THEOREM_RE)
        for match in pattern.finditer(joined)
    }
    declared.update(
        match.group("environment")
        for match in NEW_ENVIRONMENT_WRAPPER_RE.finditer(joined)
    )
    environment_names = (*VISIBLE_ENVIRONMENT_TYPES, *sorted(declared))
    alternation = "|".join(re.escape(name) for name in environment_names)
    begin_re = re.compile(rf"\\begin\{{(?P<environment>{alternation})\}}")
    restatable_re = re.compile(
        rf"\\begin\{{restatable\}}(?:\[[^]]*\])?"
        rf"\{{(?P<environment>{alternation})\}}"
    )
    ranges: list[tuple[int, int, str, str | None]] = []
    for start_index, (start_line, text) in enumerate(lines):
        anchors = [
            (match.group("environment"), None) for match in begin_re.finditer(text)
        ]
        anchors.extend(
            (match.group("environment"), "restatable")
            for match in restatable_re.finditer(text)
        )
        for environment, wrapper in anchors:
            closing_environment = wrapper or environment
            end_pattern = re.compile(rf"\\end\{{{re.escape(closing_environment)}\}}")
            for end_line, end_text in lines[start_index:]:
                if end_pattern.search(end_text):
                    ranges.append((start_line, end_line, environment, wrapper))
                    break
    return tuple(ranges)


@dataclass(frozen=True)
class SourcePacket:
    """Carry one prepared source packet and its deterministic discovery report."""

    entrypoint: Path
    text: str
    files: tuple[Path, ...]
    source_lines: int
    visible_environment_anchors: tuple[str, ...]
    unresolved: tuple[str, ...]
    cycles: tuple[str, ...]


def source_label(path: Path, project_root: Path) -> str:
    """Return a stable project-relative label, falling back to an absolute path.

    Intent
    ------
    Give the LLM and downstream evidence fields an unambiguous source name.

    Rationale
    ---------
    Project-relative paths are concise and portable, while an absolute fallback
    preserves accuracy for deliberately included files outside the source root.

    Pseudocode
    ----------
    - try to relativize the resolved path to the project root
    - return a POSIX relative label when possible
    - otherwise return the resolved absolute POSIX path
    """
    try:
        return path.resolve().relative_to(project_root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def resolve_source_include(name: str, current_file: Path, project_root: Path) -> Path | None:
    """Resolve one local TeX source include without consulting packages or classes.

    Intent
    ------
    Locate authored document inputs while excluding distribution-owned TeX code.

    Rationale
    ---------
    TeX projects use both including-file-relative and main-root-relative paths.
    Trying those two explicit roots covers both conventions without turning
    source preparation into package discovery or semantic extraction.

    Pseudocode
    ----------
    - preserve an explicit suffix or add ``.tex`` when absent
    - try the including file's directory
    - try the entrypoint's project root
    - return the first existing regular file, otherwise ``None``
    """
    candidate = Path(name.strip())
    if not candidate.suffix:
        candidate = candidate.with_suffix(".tex")
    roots = (current_file.parent, project_root)
    for root in roots:
        path = candidate if candidate.is_absolute() else root / candidate
        resolved = path.resolve()
        if resolved.is_file():
            return resolved
    return None


def collect_source_packet(entrypoint: Path) -> SourcePacket:
    """Expand authored TeX inputs into one active, line-addressable source packet.

    Intent
    ------
    Give the semantic LLM one complete ordered source artifact so it need not
    discover and repeatedly open the document tree itself.

    Rationale
    ---------
    File discovery and provenance labeling are deterministic. Keeping them here
    reduces tool round trips while preserving LLM ownership of every semantic
    choice. TeX comments are removed deterministically so inactive drafts cannot
    be mistaken for paper content. Blank placeholders retain the original line
    numbering, and comment stripping also prevents traversal of commented-out
    include commands.

    Pseudocode
    ----------
    - resolve and verify the TeX entrypoint
    - walk each source line in TeX expansion order
    - emit a source marker when the active file changes
    - emit ``line | active source text`` for every original source line
    - recurse after active input/include lines
    - record unresolved includes and active-stack cycles without guessing
    - append a syntactic checklist of base theorem-like begin markers
    - return immutable packet text and discovery metadata

    Wraps
    -----
    - read_tex_text
    - strip_comments
    - resolve_source_include
    """
    entrypoint = entrypoint.resolve()
    if not entrypoint.is_file():
        raise FileNotFoundError(f"TeX entrypoint not found: {entrypoint}")
    project_root = entrypoint.parent
    emitted_lines: list[str] = [
        "# math-dependency-graph semantic source packet",
        f"# entrypoint: {entrypoint.as_posix()}",
        "# format: @@ source selects a path; line | active TeX source follows",
        "# semantics: none; comment removal, input/include expansion, and provenance only",
        "",
    ]
    discovered: list[Path] = []
    discovered_set: set[Path] = set()
    unresolved: list[str] = []
    cycles: list[str] = []
    source_line_count = 0
    visible_environment_anchors: list[str] = []
    current_label: str | None = None

    def walk(path: Path, active: tuple[Path, ...]) -> None:
        nonlocal current_label, source_line_count
        newly_discovered = path not in discovered_set
        if newly_discovered:
            discovered.append(path)
            discovered_set.add(path)
        label = source_label(path, project_root)
        raw_lines = read_tex_text(path).splitlines()
        searchable_lines = strip_comments("\n".join(raw_lines)).splitlines()
        if len(searchable_lines) < len(raw_lines):
            searchable_lines.extend([""] * (len(raw_lines) - len(searchable_lines)))
        if newly_discovered:
            for start_line, end_line, environment, wrapper in visible_environment_ranges(
                enumerate(searchable_lines, start=1)
            ):
                suffix = f" environment={environment}"
                if wrapper is not None:
                    suffix += f" wrapper={wrapper}"
                visible_environment_anchors.append(
                    f"{label}:{start_line}-{end_line}{suffix}"
                )
        for line_number, raw_line in enumerate(raw_lines, start=1):
            if current_label != label:
                emitted_lines.append(f"@@ source: {label}")
                current_label = label
            source_line_count += 1
            searchable = searchable_lines[line_number - 1]
            emitted_lines.append(f"{line_number:04d} | {searchable}")
            for match in SOURCE_INCLUDE_RE.finditer(searchable):
                include_name = match.group("braced") or match.group("plain") or ""
                child = resolve_source_include(include_name, path, project_root)
                location = f"{label}:{line_number}"
                if child is None:
                    unresolved.append(f"{location} -> {include_name}")
                    continue
                child_label = source_label(child, project_root)
                if child in active or child == path:
                    cycles.append(f"{location} -> {child_label}")
                    continue
                walk(child, (*active, path))

    walk(entrypoint, ())
    emitted_lines.extend(
        (
            "",
            f"# visible-environment-anchor-count: {len(visible_environment_anchors)}",
            *(f"# visible-environment-anchor: {anchor}" for anchor in visible_environment_anchors),
        )
    )
    return SourcePacket(
        entrypoint=entrypoint,
        text="\n".join(emitted_lines) + "\n",
        files=tuple(discovered),
        source_lines=source_line_count,
        visible_environment_anchors=tuple(visible_environment_anchors),
        unresolved=tuple(unresolved),
        cycles=tuple(cycles),
    )


def default_output_path(entrypoint: Path) -> Path:
    """Return the stable build path for an entrypoint's semantic source packet."""
    entrypoint = entrypoint.resolve()
    return entrypoint.parent / "_build" / f"{entrypoint.stem}-semantic-source.txt"


def write_source_packet(packet: SourcePacket, out_path: Path) -> None:
    """Atomically write a complete packet after rejecting unresolved inputs.

    Intent
    ------
    Prevent semantic extraction from silently operating on a partial document.

    Rationale
    ---------
    Accuracy takes priority over best-effort speed. A missing authored include is
    an explicit failure, while reported cycles are safe because traversal already
    preserved the reachable noncyclic source once.

    Pseudocode
    ----------
    - reject a packet containing unresolved source inputs
    - create the destination directory
    - write a temporary sibling using UTF-8
    - atomically replace the selected output path
    """
    if packet.unresolved:
        joined = "; ".join(packet.unresolved)
        raise ValueError(f"unresolved TeX inputs: {joined}")
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=out_path.parent,
            prefix=f".{out_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(packet.text)
            temporary_path = Path(handle.name)
        temporary_path.replace(out_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


class Interface(PythonArgvMachineInterface):
    """Expose source packet preparation through the repository machine protocol."""

    prog = "source_packet.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


def main(argv: Iterable[str] | None = None) -> None:
    """Parse CLI arguments, prepare one packet, and print a machine JSON report."""
    parser = argparse.ArgumentParser(
        description="Prepare an active source-ordered TeX packet for LLM semantic extraction."
    )
    parser.add_argument("entrypoint", help="Root TeX document, for example main.tex")
    parser.add_argument(
        "--out",
        help="Output text path. Defaults to _build/<entry>-semantic-source.txt",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    started = time.monotonic()
    entrypoint = Path(args.entrypoint).resolve()
    packet = collect_source_packet(entrypoint)
    out_path = Path(args.out).resolve() if args.out else default_output_path(entrypoint)
    try:
        write_source_packet(packet, out_path)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    report = {
        "entrypoint": str(entrypoint),
        "out": str(out_path),
        "files": len(packet.files),
        "source_lines": packet.source_lines,
        "visible_environment_anchors": len(packet.visible_environment_anchors),
        "source_bytes": len(packet.text.encode("utf-8")),
        "unresolved": list(packet.unresolved),
        "cycles": list(packet.cycles),
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
