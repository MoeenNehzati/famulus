#!/usr/bin/env python3
"""Serve or build the bounded Famulus documentation website with MkDocs."""

from __future__ import annotations

from pathlib import Path
import argparse
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPO_ROOT, REPO_ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from docs_tooling.render import generate_all
from docs_tooling.site import assemble_site


def main(argv: list[str] | None = None) -> int:
    """Assemble generated sources, then delegate presentation to MkDocs."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "build"))
    args, mkdocs_args = parser.parse_known_args(argv)

    for changed in generate_all(REPO_ROOT):
        print(f"regenerated {changed.relative_to(REPO_ROOT).as_posix()}")

    source_dir = REPO_ROOT / "_build" / "docs-site" / "source"
    assemble_site(REPO_ROOT, source_dir)

    command = [
        "mkdocs",
        args.command,
        "--config-file",
        str(REPO_ROOT / "mkdocs.yml"),
        *mkdocs_args,
    ]
    try:
        completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
    except FileNotFoundError:
        print(
            "mkdocs is not installed; install requirements-docs.txt first",
            file=sys.stderr,
        )
        return 127
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
