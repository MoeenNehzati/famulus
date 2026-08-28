#!/usr/bin/env python3
"""Resolve TeX label numbering by asking LaTeX, not by simulating it.

A document's numbering is not written in its source: ``\\label{key}`` records a
key, and TeX assigns ``Lemma A.7`` while typesetting. Counter schemes are also
easy to get wrong by inspection, because environments may share a counter,
``\\setcounter`` and ``\\numberwithin`` may rewrite the scheme, and ``\\appendix``
switches sections to letters.

A draft-mode compile settles all of that: LaTeX writes ``\\newlabel`` entries to
the ``.aux`` file carrying both the number and the kind of each labelled object.
The compile is optional. Without a TeX toolchain, or when the document does not
compile, this reports why and returns an empty map so extraction proceeds with
no numbering rather than with guessed numbering.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from officina.runtime.python_machine_interface import PythonArgvMachineInterface


# \newlabel{key}{{number}{page}{title}{anchor}{}} — field 1 is the number and
# the anchor's prefix names the kind, as in `lemma.A.7` or `assumption.4.3`.
NEWLABEL_RE = re.compile(
    r"\\newlabel\{(?P<key>[^}]+)\}\{\{(?P<ref>[^{}]*)\}\{[^{}]*\}"
    r"(?:\{(?P<title>(?:[^{}]|\{[^{}]*\})*)\}\{(?P<anchor>[^{}]*)\})?"
)
COMPILERS = ("pdflatex", "lualatex", "xelatex")


def available_compiler() -> str | None:
    """Return the first available LaTeX compiler, or None when none is installed."""
    for name in COMPILERS:
        if shutil.which(name):
            return name
    return None


def parse_aux(text: str) -> dict[str, dict[str, str]]:
    """Return {label: {ref, kind}} for every numbered label in an aux file."""
    labels: dict[str, dict[str, str]] = {}
    for match in NEWLABEL_RE.finditer(text):
        ref = (match.group("ref") or "").strip()
        if not ref:
            continue
        entry: dict[str, str] = {"ref": ref}
        anchor = match.group("anchor") or ""
        kind = anchor.split(".", 1)[0].strip()
        if kind and kind.isalpha():
            entry["kind"] = kind
        labels[match.group("key")] = entry
    return labels


def extract_labels(entrypoint: Path) -> tuple[dict[str, dict[str, str]], dict[str, object]]:
    """Draft-compile the document and read its label numbering."""
    compiler = available_compiler()
    if compiler is None:
        return {}, {"resolved": False, "reason": "no LaTeX compiler on PATH", "compiler": None}

    project = entrypoint.resolve().parent
    with tempfile.TemporaryDirectory(prefix="texlabels-") as scratch:
        work = Path(scratch) / "project"
        shutil.copytree(project, work, symlinks=True)
        try:
            completed = subprocess.run(
                [compiler, "-draftmode", "-interaction=nonstopmode", entrypoint.name],
                cwd=work, capture_output=True, timeout=300, check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return {}, {"resolved": False, "reason": f"{compiler} failed to run: {error}", "compiler": compiler}
        aux = work / f"{entrypoint.stem}.aux"
        if not aux.exists():
            return {}, {
                "resolved": False,
                "compiler": compiler,
                "reason": f"{compiler} produced no aux file (exit {completed.returncode})",
            }
        labels = parse_aux(aux.read_text(encoding="utf-8", errors="replace"))
    return labels, {"resolved": True, "compiler": compiler, "exit_code": completed.returncode}


def default_output_path(entrypoint: Path) -> Path:
    return entrypoint.resolve().parent / "_build" / f"{entrypoint.stem}-tex-labels.json"


def write_labels(labels: dict[str, dict[str, str]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(labels, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class Interface(PythonArgvMachineInterface):
    prog = "tex_label_reader.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Resolve TeX label numbering through an optional draft-mode compile."
    )
    parser.add_argument("entrypoint", help="TeX entrypoint, e.g. main.tex")
    parser.add_argument("--out", help="Output JSON path. Defaults to _build/<entry>-tex-labels.json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    entrypoint = Path(args.entrypoint).resolve()
    if not entrypoint.exists():
        raise SystemExit(f"TeX entrypoint not found: {entrypoint}")

    labels, status = extract_labels(entrypoint)
    out_path = Path(args.out).resolve() if args.out else default_output_path(entrypoint)
    write_labels(labels, out_path)
    print(json.dumps({"entrypoint": str(entrypoint), "out": str(out_path), "labels": len(labels), **status}, indent=2))


if __name__ == "__main__":
    main()
