#!/usr/bin/env python3
"""Read a local structured list file and immediately render it for display."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
LISTS_PY = SKILL_ROOT / "scripts" / "lists.py"
BEAUTIFY_PY = SKILL_ROOT / "scripts" / "beautify.py"


def main() -> int:
    parser = argparse.ArgumentParser(prog="read_beautify.py")
    parser.add_argument("file", help="Path to local YAML list file")
    parser.add_argument("filters", nargs="*", help="key=value or key~=value filters")
    parser.add_argument("--sort", metavar="FIELD", help="Sort results by field before rendering")
    parser.add_argument("-D", "--no-descriptions", action="store_true", help="Hide entry descriptions")
    parser.add_argument("--markdown", action="store_true", help="Render markdown instead of diff")
    args = parser.parse_args()

    read_cmd = [sys.executable, str(LISTS_PY), "read", args.file]
    if args.sort:
        read_cmd.extend(["--sort", args.sort])
    read_cmd.extend(args.filters)

    read_result = subprocess.run(read_cmd, capture_output=True, text=True, check=False)
    if read_result.returncode != 0:
        if read_result.stdout:
            print(read_result.stdout, end="")
        if read_result.stderr:
            print(read_result.stderr, end="", file=sys.stderr)
        return read_result.returncode

    beautify_cmd = [sys.executable, str(BEAUTIFY_PY), "--relative-deadlines"]
    beautify_cmd.append("--markdown" if args.markdown else "--diff")
    if args.no_descriptions:
        beautify_cmd.append("--no-descriptions")

    pretty = subprocess.run(
        beautify_cmd,
        input=read_result.stdout,
        capture_output=True,
        text=True,
        check=False,
    )
    if pretty.stdout:
        print(pretty.stdout, end="")
    if pretty.stderr:
        print(pretty.stderr, end="", file=sys.stderr)
    return pretty.returncode


if __name__ == "__main__":
    raise SystemExit(main())
