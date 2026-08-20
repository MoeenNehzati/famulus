#!/usr/bin/env python3
"""Extract project-owned TeX macros reachable from an entrypoint.

The output format is the MathJax ``tex.macros`` object expected by the graph
renderer. Installed packages are read to resolve dependencies, aliases, and
standard symbol slots, but their commands are not promoted as roots because
doing so can replace native MathJax commands with TeX-engine internals. The
extractor is intentionally conservative: it handles common macro forms used in
papers and skips definitions with optional arguments because their TeX semantics
are not directly representable as simple MathJax macros.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
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
    "boldsymbol",
    "caption",
    "cite",
    "citet",
    "citep",
    "arg",
    "dfrac",
    "displaystyle",
    "emph",
    "end",
    "eqref",
    "frac",
    "hspace",
    "in",
    "int",
    "input",
    "include",
    "label",
    "left",
    "lim",
    "liminf",
    "limsup",
    "mapsto",
    "mathbb",
    "mathbf",
    "mathcal",
    "mathrm",
    "min",
    "nonumber",
    "notag",
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

MATHJAX_COMMAND_ARITIES = {
    "boldsymbol": 1,
    "mathbf": 1,
    "mathcal": 1,
    "mathrm": 1,
}


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
MATH_FRAGMENT_RE = re.compile(
    r"\\\[(?P<bracket>.*?)\\\]"
    r"|\\\((?P<paren>.*?)\\\)"
    r"|(?<!\\)\$\$(?P<display>.*?)(?<!\\)\$\$"
    r"|(?<![\\$])\$(?!\$)(?P<inline>.*?)(?<![\\$])\$(?!\$)",
    re.DOTALL,
)
MATH_ENVIRONMENT_RE = re.compile(
    r"\\begin\{(?P<name>equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|"
    r"flalign\*?|displaymath|math)\}(?P<body>.*?)\\end\{(?P=name)\}",
    re.DOTALL,
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


def read_tex_text(path: Path) -> str:
    """Read TeX sources without assuming modern packages are UTF-8."""
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
    suffix = ".tex"
    if command.startswith("documentclass") or command.startswith("LoadClass"):
        suffix = ".cls"
    elif command == "usepackage" or command.startswith("RequirePackage"):
        suffix = ".sty"
    return suffix


def tex_distribution_path(filename: str) -> Path | None:
    """Ask the active TeX distribution to resolve one source dependency."""
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
    """Resolve TeX dependencies locally, then through the TeX distribution."""
    suffix = dependency_suffix(command)

    paths = []
    for name in names.split(","):
        include_name = name.strip()
        if not include_name:
            continue
        path = resolve_tex_path(include_name, current_dir, suffix)
        if path.exists():
            paths.append(path)
            continue
        filename = str(Path(include_name).with_suffix(suffix)) if not Path(include_name).suffix else include_name
        distributed = tex_distribution_path(filename)
        if distributed is not None:
            paths.append(distributed)
    return paths


def local_include_paths(command: str, names: str, current_dir: Path) -> list[Path]:
    """Resolve only TeX dependencies stored beside the document sources."""
    suffix = dependency_suffix(command)
    paths = []
    for name in names.split(","):
        include_name = name.strip()
        if not include_name:
            continue
        path = resolve_tex_path(include_name, current_dir, suffix)
        if path.exists():
            paths.append(path)
    return paths


def path_is_within(path: Path, root: Path) -> bool:
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
) -> list[tuple[str, bool]]:
    """Collect source-order TeX chunks and mark distribution-owned chunks."""
    entrypoint = entrypoint.resolve()
    if seen is None:
        seen = set()
    if entrypoint in seen:
        return []
    seen.add(entrypoint)
    text = strip_comments(read_tex_text(entrypoint))
    external = not path_is_within(entrypoint, project_root)

    chunks: list[tuple[str, bool]] = []
    last = 0
    for match in LOCAL_INCLUDE_RE.finditer(text):
        chunks.append((text[last:match.start()], external))
        children = dependency_paths(match.group("cmd"), match.group("names"), entrypoint.parent)
        for child in children:
            chunks.extend(collect_tex_chunks(child, project_root=project_root, seen=seen))
        last = match.end()
    chunks.append((text[last:], external))
    return chunks


def flatten_tex(entrypoint: Path, seen: set[Path] | None = None) -> str:
    """Return authored TeX content with local input/include files expanded.

    Class and package implementations are intentionally excluded: math inside
    their macro bodies is not evidence that the document uses those commands.
    """
    entrypoint = entrypoint.resolve()
    if seen is None:
        seen = set()
    if entrypoint in seen:
        return ""
    seen.add(entrypoint)
    text = strip_comments(read_tex_text(entrypoint))

    parts = []
    last = 0
    for match in LOCAL_INCLUDE_RE.finditer(text):
        parts.append(text[last:match.start()])
        if match.group("cmd") in {"input", "include"}:
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


def collect_symbol_fonts(text: str) -> dict[str, tuple[str, str, str, str]]:
    """Collect symbol-font declarations keyed by their local font name."""
    return {
        match.group("name").strip(): (
            match.group("encoding").strip(),
            match.group("family").strip(),
            match.group("series").strip(),
            match.group("shape").strip(),
        )
        for match in DECLARE_SYMBOL_FONT_RE.finditer(text)
    }


def math_symbol_slot(code: str) -> int | None:
    r"""Decode the literal, hexadecimal, octal, or decimal TeX symbol slot."""
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
def canonical_math_symbols() -> dict[tuple[str, str, int], str]:
    """Index standard LaTeX symbols by encoding, family, and font slot.

    The active TeX distribution is the authority for slot meanings. Series and
    shape are deliberately excluded from the key so template-specific bold
    fonts can resolve to the corresponding public LaTeX command.
    """
    source = tex_distribution_path("fontmath.ltx")
    if source is None:
        return {}
    text = strip_comments(read_tex_text(source))
    fonts = collect_symbol_fonts(text)
    symbols: dict[tuple[str, str, int], str] = {}
    for match in DECLARE_MATH_SYMBOL_RE.finditer(text):
        font = fonts.get(match.group("font").strip())
        slot = math_symbol_slot(match.group("slot"))
        if font is None or slot is None:
            continue
        encoding, family, _series, _shape = font
        symbols.setdefault((encoding, family, slot), match.group("name"))
    return symbols


def parse_declared_math_symbol_at(
    text: str,
    idx: int,
    symbol_fonts: dict[str, tuple[str, str, str, str]] | None = None,
) -> tuple[str, str, int] | None:
    r"""Translate a ``\DeclareMathSymbol`` into a MathJax-safe macro."""
    match = DECLARE_MATH_SYMBOL_RE.match(text, idx)
    if not match:
        return None
    name = match.group("name")
    code = match.group("slot").strip()
    slot = math_symbol_slot(code)
    if slot is None:
        return None
    font = (symbol_fonts or {}).get(match.group("font").strip())
    is_bold = font is not None and font[2].lower() in {"b", "bx", "bold"}

    if font is not None:
        canonical = canonical_math_symbols().get((font[0], font[1], slot))
        if canonical:
            command = f"\\{canonical}"
            body = f"\\boldsymbol{{{command}}}" if is_bold else command
            return name, body, match.end()

    if code.startswith("`") and len(code) >= 2:
        char = code[1]
        body = f"\\mathbf{{{char}}}" if is_bold or font is None else char
        return name, body, match.end()
    return None


def collect_macro_definitions(
    text: str,
    symbol_fonts: dict[str, tuple[str, str, str, str]] | None = None,
) -> dict[str, object]:
    """Collect supported macro definitions in source order."""
    active_symbol_fonts = dict(symbol_fonts or {})
    active_symbol_fonts.update(collect_symbol_fonts(text))
    macros: dict[str, object] = {}
    idx = 0
    while idx < len(text):
        if text[idx] != "\\":
            idx += 1
            continue

        parsed = (
            parse_newcommand_at(text, idx)
            or parse_declared_operator_at(text, idx)
            or parse_declared_math_symbol_at(text, idx, active_symbol_fonts)
            or parse_def_at(text, idx)
        )
        if parsed:
            name, body, next_idx = parsed
            macros[name] = body
            idx = next_idx
            continue
        idx += 1
    return macros


def collect_aliases(text: str) -> list[tuple[str, str]]:
    """Collect plain ``\\let\\left\\right`` command aliases."""
    return [(match.group("left"), match.group("right")) for match in LET_RE.finditer(text)]


def math_fragments(text: str) -> list[str]:
    """Return TeX fragments that LaTeX places in math mode."""
    fragments = [
        next(value for value in match.groupdict().values() if value is not None)
        for match in MATH_FRAGMENT_RE.finditer(text)
    ]
    fragments.extend(match.group("body") for match in MATH_ENVIRONMENT_RE.finditer(text))
    return fragments


def alias_bridges(aliases: Iterable[tuple[str, str]]) -> dict[str, object]:
    """Translate direct aliases to known MathJax commands without package rules."""
    bridges: dict[str, object] = {}
    for left, right in aliases:
        builtin: str | None = None
        alias: str | None = None
        if left in MATHJAX_COMMAND_ARITIES and right not in BUILTIN_COMMANDS:
            builtin, alias = left, right
        elif right in MATHJAX_COMMAND_ARITIES and left not in BUILTIN_COMMANDS:
            builtin, alias = right, left
        if builtin is None or alias is None:
            continue
        argc = MATHJAX_COMMAND_ARITIES[builtin]
        arguments = "".join(f"{{#{index}}}" for index in range(1, argc + 1))
        body: object = f"\\{builtin}{arguments}"
        if argc:
            body = [argc, body]
        bridges[alias] = body
    return bridges


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


def dependency_closure(
    macros: dict[str, object],
    roots: Iterable[str] | None = None,
) -> dict[str, object]:
    """Keep definitions whose dependencies are local or known MathJax commands."""
    closed: dict[str, object] = {}
    visiting: set[str] = set()

    def visit(name: str) -> bool:
        if name in closed:
            return True
        if name not in macros:
            return name in BUILTIN_COMMANDS
        if macro_body_text(macros[name]).strip() == f"\\{name}":
            # Standard symbol declarations resolve to their own public MathJax
            # command. Treat them as native dependencies; emitting the identity
            # macro would replace that command with infinite self-expansion.
            return True
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

    root_names = list(macros) if roots is None else list(dict.fromkeys(roots))
    for macro_name in root_names:
        visit(macro_name)
    return {name: closed[name] for name in macros if name in closed}


def extract_macros(entrypoint: Path) -> dict[str, object]:
    entrypoint = entrypoint.resolve()
    chunks = collect_tex_chunks(entrypoint, project_root=entrypoint.parent)
    macros: dict[str, object] = {}
    project_macros: list[str] = []
    aliases: list[tuple[str, str]] = []
    symbol_fonts: dict[str, tuple[str, str, str, str]] = {}

    for text, external in chunks:
        symbol_fonts.update(collect_symbol_fonts(text))
        definitions = collect_macro_definitions(text, symbol_fonts)
        macros.update(definitions)
        aliases.extend(collect_aliases(text))
        if not external:
            project_macros.extend(definitions)

    bridges = alias_bridges(aliases)
    macros.update(bridges)
    rendered_math = "\n".join(math_fragments(flatten_tex(entrypoint)))
    referenced = {
        match.group(1)
        for match in COMMAND_RE.finditer(rendered_math)
        if match.group(1).isalpha() or "@" in match.group(1)
    }
    roots = [name for name in project_macros if name in referenced]
    return dependency_closure(macros, roots)


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
