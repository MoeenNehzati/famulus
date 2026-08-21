#!/usr/bin/env python3
"""Deterministically add v6 maturity and discoverable-install metadata.

This migration intentionally uses the repository's ``yaml.safe_load`` and
``yaml.safe_dump`` convention.  It changes only authored blueprints beneath
``references``, ``skills``, and ``src/officina``; fixtures are excluded.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT_ROOTS = ("references", "skills", "src/officina")
EXPERIMENTAL_NODE_IDS = frozenset({"rutter", "using-compass"})
OPTIONAL_MODULE_IDS = frozenset({"pdf-to-markdown"})


@dataclass(frozen=True)
class MigrationResult:
    """Summarize changed blueprints and named overrides absent from a checkout."""

    changed_paths: tuple[Path, ...]
    absent_experimental_overrides: tuple[str, ...]


def iter_authored_blueprints(repository_root: Path) -> Iterable[Path]:
    """Yield the repository's authored module and behavioral-source blueprints."""

    paths: set[Path] = set()
    for relative_root in BLUEPRINT_ROOTS:
        root = repository_root / relative_root
        if not root.is_dir():
            continue
        paths.update(root.rglob("blueprint.yaml"))
        paths.update(root.glob("**/blueprints/*.yaml"))
    yield from sorted(paths)


def migrate_document(document: dict[str, Any]) -> None:
    """Apply the metadata defaults and explicit node-ID overrides in place."""

    if document.get("schema_version") != 6:
        return
    node_type = document.get("node_type")
    if node_type not in {"module", "behavioral_source"}:
        return

    node_id = document.get("id")
    document.setdefault("maturity", "stable")
    if node_id in EXPERIMENTAL_NODE_IDS:
        document["maturity"] = "experimental"

    if node_type != "module":
        return
    if "discovery" not in document:
        document.pop("installation_tier", None)
        document.pop("personal_preference", None)
        return

    document.setdefault("installation_tier", "core")
    document.setdefault("personal_preference", {"applies": False})
    if node_id in OPTIONAL_MODULE_IDS:
        document["installation_tier"] = "optional"


def migrate_repository(repository_root: Path, *, write: bool = True) -> MigrationResult:
    """Migrate authored v6 blueprints and report absent explicit overrides."""

    changed_paths: list[Path] = []
    present_ids: set[str] = set()
    for path in iter_authored_blueprints(repository_root):
        original = path.read_text(encoding="utf-8")
        document = yaml.safe_load(original)
        if not isinstance(document, dict):
            continue
        original_document = deepcopy(document)
        node_id = document.get("id")
        if isinstance(node_id, str):
            present_ids.add(node_id)
        migrate_document(document)
        if document == original_document:
            continue
        changed_paths.append(path)
        if write:
            path.write_text(
                yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )

    return MigrationResult(
        changed_paths=tuple(changed_paths),
        absent_experimental_overrides=tuple(sorted(EXPERIMENTAL_NODE_IDS - present_ids)),
    )


def main() -> int:
    """Run the migration or report whether its deterministic output is current."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--check", action="store_true", help="fail if migration would edit files")
    args = parser.parse_args()
    result = migrate_repository(args.root.resolve(), write=not args.check)
    for path in result.changed_paths:
        print(f"{'would update' if args.check else 'updated'} {path.relative_to(args.root)}")
    if result.absent_experimental_overrides:
        print(
            "absent explicit experimental overrides: "
            + ", ".join(result.absent_experimental_overrides)
        )
    return 1 if args.check and result.changed_paths else 0


if __name__ == "__main__":
    raise SystemExit(main())
