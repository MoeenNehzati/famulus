#!/usr/bin/env python3
"""Parse Task-4 migration arguments and call the canonical engine."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from officina.common.interface_injection_migration import (  # noqa: E402
    check_active_migration_references,
    InterfaceInjectionMigrationError,
    finalize_candidate_v4,
    inspect_candidate_v4,
    load_blueprint_migration_map,
    materialize_blueprint_v4_candidate,
    validate_post_adoption_migration_map,
)


def main(
    argv: list[str] | None = None,
    *,
    repo_root: Path = REPO_ROOT,
) -> int:
    root = Path(repo_root).resolve()
    parser = argparse.ArgumentParser(
        description="Validate the migration map and build an isolated v4 candidate."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-map", action="store_true")
    mode.add_argument("--inspect-candidate", type=Path)
    mode.add_argument("--finalize-candidate", type=Path)
    parser.add_argument("--check-active-references", action="store_true")
    parser.add_argument("--temporary-output", action="store_true")
    parser.add_argument(
        "--diagnostic-allow-non-atomic",
        action="store_true",
        help="build a visibly non-certifiable diagnostic candidate",
    )
    parser.add_argument("--reviewed-commit")
    parser.add_argument("--certified-at")
    args = parser.parse_args(argv)
    if args.inspect_candidate is not None:
        if (
            args.check_active_references
            or args.temporary_output
            or args.diagnostic_allow_non_atomic
            or args.reviewed_commit
            or args.certified_at
        ):
            parser.error("--inspect-candidate accepts no migration/finalization flags")
        try:
            inspection = inspect_candidate_v4(args.inspect_candidate)
        except InterfaceInjectionMigrationError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps(inspection, indent=2, sort_keys=True))
        return 3 if inspection["findings"] else 0
    if args.finalize_candidate is not None:
        if (
            args.check_active_references
            or args.temporary_output
            or args.diagnostic_allow_non_atomic
        ):
            parser.error(
                "--finalize-candidate does not accept candidate-creation flags"
            )
        if (
            not args.reviewed_commit
            or not args.certified_at
        ):
            parser.error(
                "--finalize-candidate requires --reviewed-commit and --certified-at"
            )
        try:
            result = finalize_candidate_v4(
                args.finalize_candidate,
                reviewed_commit=args.reviewed_commit,
                certified_at=args.certified_at,
            )
        except InterfaceInjectionMigrationError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.check_active_references:
        if (
            args.temporary_output
            or args.diagnostic_allow_non_atomic
            or args.reviewed_commit
            or args.certified_at
        ):
            parser.error(
                "--check-map --check-active-references accepts no candidate "
                "or finalization flags"
            )
        try:
            migration_map = load_blueprint_migration_map(
                root / "docs/plans/unified-architecture-migration-map.yaml"
            )
            validate_post_adoption_migration_map(migration_map)
            findings = check_active_migration_references(root, migration_map)
        except InterfaceInjectionMigrationError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
            return 2
        print("migration_map_status=valid-post-adoption")
        print(f"active_reference_findings={len(findings)}")
        for finding in findings:
            print(
                "active_reference_finding="
                + json.dumps(finding.as_document(), sort_keys=True)
            )
        return 3 if findings else 0
    if (
        not args.temporary_output
        or args.reviewed_commit
        or args.certified_at
    ):
        parser.error("--check-map requires only --temporary-output")

    migration_map = load_blueprint_migration_map(
        root / "docs/plans/unified-architecture-migration-map.yaml"
    )
    if args.diagnostic_allow_non_atomic:
        candidate = materialize_blueprint_v4_candidate(
            root,
            migration_map,
            diagnostic_allow_non_atomic=True,
        )
    else:
        candidate = materialize_blueprint_v4_candidate(root, migration_map)
    print(f"candidate_root={candidate.root}")
    print(f"candidate_source_commit={candidate.source_commit}")
    print(f"candidate_legacy_commit={candidate.legacy_commit}")
    print(f"candidate_source_overlay_commit={candidate.source_overlay_commit}")
    print(f"candidate_commit={candidate.commit}")
    print(f"candidate_nodes={len(candidate.graph.nodes) if candidate.graph else 0}")
    inspection = candidate.inspection or {"findings": []}
    findings = inspection.get("findings", [])
    review_context = inspection.get("review_context", [])
    print(f"candidate_atomic_guarantee={str(candidate.atomic_guarantee).lower()}")
    print(
        "candidate_status="
        + (
            "mechanical-review-required"
            if candidate.atomic_guarantee
            else "diagnostic-noncertifiable"
        )
    )
    print("certified_nodes=0")
    print(f"candidate_findings={len(findings)}")
    print(f"candidate_review_context={len(review_context)}")
    for finding in findings:
        print("candidate_finding=" + json.dumps(finding, sort_keys=True))
    for item in review_context:
        print("candidate_review_item=" + json.dumps(item, sort_keys=True))
    print(
        "candidate_review_command="
        f"scripts/{Path(__file__).name} --inspect-candidate {candidate.root}"
    )
    print(f"cutover_paths={len(candidate.cutover_paths)}")
    for path in candidate.cutover_paths:
        print(f"cutover_path={path.as_posix()}")
    for change in candidate.cutover_manifest:
        payload = {"status": change.status, "path": change.path.as_posix()}
        if change.source_path is not None:
            payload["source_path"] = change.source_path.as_posix()
        print("cutover_change=" + json.dumps(payload, sort_keys=True))
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
