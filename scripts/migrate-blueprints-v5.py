#!/usr/bin/env python3
"""Render or materialize the deterministic nested-module v5 migration."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from officina.common.nested_module_migration import (  # noqa: E402
    NestedModuleMigrationError,
    build_nested_module_migration,
)


def main(
    argv: list[str] | None = None,
    *,
    repo_root: Path = REPO_ROOT,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plan the v4-to-v5 nested-module migration or materialize its "
            "exact result in a new isolated candidate."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="write the canonical migration manifest to stdout without writes",
    )
    mode.add_argument(
        "--candidate",
        type=Path,
        help="materialize the reviewed plan into this new directory",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=repo_root,
        help="exact Git repository root to inspect",
    )
    parser.add_argument(
        "--expected-source-commit",
        help="reviewed source commit required by --candidate",
    )
    parser.add_argument(
        "--expected-manifest-sha256",
        help="reviewed dry-run manifest SHA-256 required by --candidate",
    )
    args = parser.parse_args(argv)
    review_values = (
        args.expected_source_commit,
        args.expected_manifest_sha256,
    )
    if args.candidate is not None and not all(review_values):
        parser.error(
            "--candidate requires --expected-source-commit and "
            "--expected-manifest-sha256"
        )
    if args.dry_run and any(review_values):
        parser.error("review binding options are valid only with --candidate")
    if args.expected_manifest_sha256 is not None and re.fullmatch(
        r"[0-9a-f]{64}",
        args.expected_manifest_sha256,
    ) is None:
        parser.error("--expected-manifest-sha256 must be 64 lowercase hex digits")

    try:
        plan = build_nested_module_migration(args.repo_root)
        if args.dry_run:
            sys.stdout.buffer.write(plan.render_manifest())
            return 0
        manifest_hash = hashlib.sha256(plan.render_manifest()).hexdigest()
        if plan.source_commit != args.expected_source_commit:
            raise NestedModuleMigrationError(
                "source commit differs from the reviewed dry run"
            )
        if manifest_hash != args.expected_manifest_sha256:
            raise NestedModuleMigrationError(
                "manifest hash differs from the reviewed dry run"
            )
        candidate = plan.materialize(args.candidate)
    except NestedModuleMigrationError as exc:
        print(
            json.dumps({"error": str(exc), "ok": False}, sort_keys=True),
            file=sys.stderr,
        )
        return 2

    print(
        json.dumps(
            {
                "candidate_commit": candidate.commit,
                "candidate_root": str(candidate.root),
                "manifest_sha256": manifest_hash,
                "ok": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
