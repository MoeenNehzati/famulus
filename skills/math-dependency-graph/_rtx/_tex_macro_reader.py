#!/usr/bin/env python3
"""Extract user-defined TeX macros reachable from an entrypoint.

The output format is the MathJax ``tex.macros`` object expected by the graph
renderer. The extractor is intentionally conservative: it handles common macro
forms used in papers and skips definitions with optional arguments because their
TeX semantics are not directly representable as simple MathJax macros.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable

from officina.runtime.python_machine_interface import PythonArgvMachineInterface


BUILTIN_COMMANDS = {
    "begin",
    "big",
    "Big",
    "bigg",
    "Bigg",
    "bigl",
    "Bigl",
    "biggl",
    "Biggl",
    "bigr",
    "Bigr",
    "biggr",
    "Biggr",
    "bm",
    "boldsymbol",
    "caption",
    "cite",
    "citet",
    "citep",
    "dfrac",
    "displaystyle",
    "emph",
    "end",
    "eqref",
    "frac",
    "hspace",
    "in",
    "input",
    "include",
    "label",
    "left",
    "mathbb",
    "mathbf",
    "mathcal",
    "mathrm",
    "operatorname",
    "overline",
    "paragraph",
    "partial",
    "qquad",
    "quad",
    "ref",
    "renewcommand",
    "right",
    "section",
    "subsection",
    "subsubsection",
    "text",
    "textbf",
    "textit",
    "tilde",
    "to",
    "usepackage",
    "widehat",
}


LOCAL_INCLUDE_RE = re.compile(
    r"\\(?P<cmd>input|include|usepackage|documentclass)"
    r"(?:\s*\[[^\]]*\])?"
    r"\s*\{(?P<names>[^{}]+)\}"
)
COMMAND_RE = re.compile(r"\\([A-Za-z@]+|.)")
DECLARE_OP_RE = re.compile(r"\\DeclareMathOperator\*?\s*\{\\([A-Za-z@]+)\}")
DECLARE_MATH_SYMBOL_RE = re.compile(
    r"\\DeclareMathSymbol\s*\{\\([A-Za-z@]+)\}\s*"
    r"\{[^{}]*\}\s*\{[^{}]*\}\s*\{([^{}]+)\}"
)
DEF_RE = re.compile(r"\\def\s*\\([A-Za-z@]+)")
NEWCOMMAND_RE = re.compile(r"\\(?:re)?newcommand\s*\*?\s*|\\providecommand\s*\*?\s*")


def strip_comments(text: str) -> str:
    """Remove TeX comments while preserving escaped percent signs."""
    cleaned_lines = []
    for line in text.splitlines():
        idx = 0
        cut = len(line)
        while True:
            pos = line.find("%", idx)
            if pos == -1:
                break
            backslashes = 0
            j = pos - 1
            while j >= 0 and line[j] == "\\":
                backslashes += 1
                j -= 1
            if backslashes % 2 == 0:
                cut = pos
                break
            idx = pos + 1
        cleaned_lines.append(line[:cut])
    return "\n".join(cleaned_lines)


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


def local_include_paths(command: str, names: str, current_dir: Path) -> list[Path]:
    """Resolve local TeX source dependencies in document order.

    System packages/classes are ignored unless a matching local file exists.
    """
    suffix = ".tex"
    if command == "documentclass":
        suffix = ".cls"
    elif command == "usepackage":
        suffix = ".sty"

    paths = []
    for name in names.split(","):
        include_name = name.strip()
        if not include_name:
            continue
        path = resolve_tex_path(include_name, current_dir, suffix)
        if path.exists():
            paths.append(path)
    return paths


def flatten_tex(entrypoint: Path, seen: set[Path] | None = None) -> str:
    """Return entrypoint text with reachable ``\\input``/``\\include`` expanded."""
    entrypoint = entrypoint.resolve()
    if seen is None:
        seen = set()
    if entrypoint in seen:
        return ""
    seen.add(entrypoint)
    text = strip_comments(entrypoint.read_text(encoding="utf-8"))

    parts = []
    last = 0
    for match in LOCAL_INCLUDE_RE.finditer(text):
        parts.append(text[last:match.start()])
        children = local_include_paths(match.group("cmd"), match.group("names"), entrypoint.parent)
        for child in children:
            parts.append(flatten_tex(child, seen))
        last = match.end()
    parts.append(text[last:])
    return "\n".join(parts)


def parse_newcommand_at(text: str, idx: int) -> tuple[str, object, int] | None:
    """Parse a ``\\newcommand``/``\\renewcommand``/``\\providecommand`` definition."""
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
        end = text.find("]", pos + 1)
        if end == -1:
            return None
        argc_text = text[pos + 1 : end].strip()
        if not argc_text.isdigit():
            return None
        argc = int(argc_text)
        pos = skip_space(text, end + 1)

    if pos < len(text) and text[pos] == "[":
        # Optional-argument macros need a richer conversion than MathJax's
        # simple [argc, body] form. Skip them rather than emit wrong macros.
        end = text.find("]", pos + 1)
        if end == -1:
            return None
        pos = skip_space(text, end + 1)
        if pos < len(text) and text[pos] == "{":
            try:
                _, pos = read_balanced_group(text, pos)
            except ValueError:
                return None
        return None

    if pos >= len(text) or text[pos] != "{":
        return None
    try:
        body, end_pos = read_balanced_group(text, pos)
    except ValueError:
        return None

    if argc:
        return name, [argc, body], end_pos
    return name, body, end_pos


def parse_def_at(text: str, idx: int) -> tuple[str, str, int] | None:
    """Parse simple ``\\def\\foo{...}`` definitions."""
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
    name = match.group(1)
    pos = skip_space(text, match.end())
    if pos >= len(text) or text[pos] != "{":
        return None
    try:
        operator_text, end_pos = read_balanced_group(text, pos)
    except ValueError:
        return None
    return name, f"\\operatorname{{{operator_text}}}", end_pos


def parse_declared_math_symbol_at(text: str, idx: int) -> tuple[str, str, int] | None:
    r"""Parse simple literal ``\DeclareMathSymbol`` declarations.

    INFORMS defines bold roman letters as, for example,
    ``\DeclareMathSymbol{\BFn}{\mathalpha}{boperators}{`n}``.
    MathJax does not know that template-specific symbol font, so represent the
    literal character as a bold MathJax symbol.
    """
    match = DECLARE_MATH_SYMBOL_RE.match(text, idx)
    if not match:
        return None
    name = match.group(1)
    code = match.group(2).strip()
    if not code.startswith("`") or len(code) < 2:
        return None
    char = code[1]
    return name, f"\\mathbf{{{char}}}", match.end()


def collect_macro_definitions(text: str) -> dict[str, object]:
    """Collect supported macro definitions in source order."""
    macros: dict[str, object] = {}
    idx = 0
    while idx < len(text):
        if text[idx] != "\\":
            idx += 1
            continue

        parsed = (
            parse_newcommand_at(text, idx)
            or parse_declared_operator_at(text, idx)
            or parse_declared_math_symbol_at(text, idx)
            or parse_def_at(text, idx)
        )
        if parsed:
            name, body, next_idx = parsed
            macros[name] = body
            idx = next_idx
            continue
        idx += 1
    return macros


def macro_body_text(body: object) -> str:
    if isinstance(body, list) and len(body) == 2:
        return str(body[1])
    return str(body)


def referenced_macros(body: object) -> set[str]:
    return {
        match.group(1)
        for match in COMMAND_RE.finditer(macro_body_text(body))
        if match.group(1).isalpha() or "@" in match.group(1)
    }


def dependency_closure(macros: dict[str, object]) -> dict[str, object]:
    """Keep definitions whose dependencies are local or known MathJax commands."""
    closed: dict[str, object] = {}
    visiting: set[str] = set()

    def visit(name: str) -> bool:
        if name in closed:
            return True
        if name not in macros:
            return name in BUILTIN_COMMANDS
        if name in visiting:
            # Cyclic macros are rare and unsafe for MathJax expansion.
            return False
        visiting.add(name)
        deps_ok = True
        for dep in referenced_macros(macros[name]):
            if dep == name or dep in BUILTIN_COMMANDS:
                continue
            if dep in macros and not visit(dep):
                deps_ok = False
            elif dep not in macros:
                # Assume package/MathJax commands are available. The renderer
                # cannot fully know every MathJax command without MathJax.
                continue
        visiting.remove(name)
        if deps_ok:
            closed[name] = macros[name]
        return deps_ok

    for macro_name in macros:
        visit(macro_name)
    return {name: closed[name] for name in macros if name in closed}


def extract_macros(entrypoint: Path) -> dict[str, object]:
    flattened = flatten_tex(entrypoint)
    return dependency_closure(collect_macro_definitions(flattened))


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
    parser = argparse.ArgumentParser(description="Extract MathJax macro definitions from a TeX entrypoint.")
    parser.add_argument("entrypoint", help="TeX entrypoint, e.g. main.tex or or.tex")
    parser.add_argument("--out", help="Output JSON path. Defaults to _build/<entry>-mathjax-macros.json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    entrypoint = Path(args.entrypoint).resolve()
    if not entrypoint.exists():
        raise SystemExit(f"TeX entrypoint not found: {entrypoint}")

    macros = extract_macros(entrypoint)
    out_path = Path(args.out).resolve() if args.out else default_output_path(entrypoint)
    write_macros(macros, out_path)
    print(json.dumps({"entrypoint": str(entrypoint), "out": str(out_path), "macros": len(macros)}, indent=2))


if __name__ == "__main__":
    main()
