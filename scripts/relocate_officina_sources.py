#!/usr/bin/env python3
"""Preflight or apply a manifest-driven Officina source relocation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_ROOT = SCRIPT_REPOSITORY / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from officina.refactor.relocation import (  # noqa: E402
    RelocationError,
    apply_change_set,
    load_manifest,
    plan_relocation,
    render_report,
)


def main(argv: list[str] | None = None) -> int:
    """Run read-only preflight by default and publish only with ``--apply``."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=SCRIPT_REPOSITORY)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=SCRIPT_REPOSITORY / "refactors/officina-source-relocation.yaml",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest.resolve())
        changes = plan_relocation(args.root, manifest)
        report = render_report(changes)
        if args.report is not None:
            args.report.write_text(report, encoding="utf-8")
        if args.apply:
            apply_change_set(changes)
        sys.stdout.write(report)
    except (OSError, RelocationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
