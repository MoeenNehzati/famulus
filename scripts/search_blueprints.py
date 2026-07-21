#!/usr/bin/env python3
"""Search module and behavioral-source blueprint YAML files and emit JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from officina.blueprint_search import BlueprintSearchError, load_query_file, search_blueprints


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Search module blueprint.yaml files and direct blueprints/*.yaml "
            "behavioral sources with structured filters and projections."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root containing skills/ (default: inferred from this script).",
    )
    parser.add_argument(
        "--query-file",
        type=Path,
        help="YAML or JSON query file. If omitted, generic v4 node metadata is returned.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    args = parser.parse_args(argv)

    try:
        query = load_query_file(args.query_file) if args.query_file else {}
        rows = search_blueprints(args.repo_root, query)
    except BlueprintSearchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    json.dump(
        rows,
        sys.stdout,
        indent=2 if args.pretty else None,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
