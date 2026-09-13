#!/usr/bin/env python3
"""Populate missing schema-v6 interface content and use subsets."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import stat
import sys
from typing import Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from officina.blueprints.inventory import BlueprintInventoryError, collect_blueprints
from officina.common.atomic_files import atomic_replace_bytes, read_regular_file_bytes


def _missing_interface_facets(
    declaration: dict[str, object],
) -> dict[str, dict[str, object]]:
    """Return source-envelope values for each missing interface facet field."""

    if (
        declaration.get("schema_version") != 6
        or declaration.get("node_type") != "behavioral_source"
    ):
        return {}
    interfaces = declaration.get("interfaces")
    if not isinstance(interfaces, dict) or not interfaces:
        return {}
    content = declaration.get("content")
    uses_interfaces = declaration.get("uses_interfaces")
    if not isinstance(content, list) or not isinstance(uses_interfaces, list):
        raise ValueError("source content and uses_interfaces must be lists")

    missing: dict[str, dict[str, object]] = {}
    for interface_id, interface in interfaces.items():
        if not isinstance(interface_id, str) or not isinstance(interface, dict):
            raise ValueError("interfaces must map string ids to mappings")
        fields: dict[str, object] = {}
        if "content" not in interface:
            fields["content"] = deepcopy(content)
        if "uses_interfaces" not in interface:
            fields["uses_interfaces"] = deepcopy(uses_interfaces)
        if fields:
            missing[interface_id] = fields
    return missing


def _mapping_value_node(
    mapping: yaml.MappingNode,
    key: str,
) -> yaml.Node | None:
    for key_node, value_node in mapping.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return value_node
    return None


def _migrate_text(
    text: str,
    declaration: dict[str, object],
) -> str | None:
    """Insert missing fields into block mappings without reformatting YAML."""

    missing = _missing_interface_facets(declaration)
    if not missing:
        return None
    root_node = yaml.compose(text, Loader=yaml.SafeLoader)
    if not isinstance(root_node, yaml.MappingNode):
        raise ValueError("blueprint root must be a YAML mapping")
    interfaces_node = _mapping_value_node(root_node, "interfaces")
    if not isinstance(interfaces_node, yaml.MappingNode):
        raise ValueError("interfaces must be a YAML mapping")

    line_offsets = [0]
    for line in text.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))
    insertions: list[tuple[int, str]] = []
    for key_node, value_node in interfaces_node.value:
        if not isinstance(key_node, yaml.ScalarNode) or key_node.value not in missing:
            continue
        if not isinstance(value_node, yaml.MappingNode):
            raise ValueError(f"interface {key_node.value!r} must be a YAML mapping")
        if value_node.start_mark.line <= key_node.start_mark.line:
            raise ValueError(
                f"interface {key_node.value!r} must use a block mapping for migration"
            )
        rendered = yaml.safe_dump(
            missing[key_node.value],
            sort_keys=False,
            allow_unicode=True,
        )
        indent = " " * value_node.start_mark.column
        block = "".join(
            indent + line for line in rendered.splitlines(keepends=True)
        )
        insertions.append((line_offsets[value_node.start_mark.line], block))

    if len(insertions) != len(missing):
        raise ValueError("could not locate every interface requiring migration")
    migrated = text
    for offset, block in sorted(insertions, reverse=True):
        migrated = migrated[:offset] + block + migrated[offset:]
    return migrated


def main(argv: Sequence[str] | None = None) -> int:
    """Check or mechanically migrate every discovered schema-v6 source blueprint."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Atomically write missing interface fields; default is check-only.",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    try:
        inventory = collect_blueprints(repo_root)
    except BlueprintInventoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    pending: list[tuple[Path, bytes]] = []
    try:
        for document in inventory.documents:
            declaration = dict(document.declaration)
            raw = read_regular_file_bytes(
                document.path,
                allowed_root=repo_root,
            )
            migrated = _migrate_text(raw.decode("utf-8"), declaration)
            if migrated is not None:
                pending.append((document.path, migrated.encode("utf-8")))
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.write:
        for path, _rendered in pending:
            print(path.relative_to(repo_root).as_posix())
        return 1 if pending else 0

    for path, rendered in pending:
        mode = stat.S_IMODE(path.stat().st_mode)
        atomic_replace_bytes(
            path,
            rendered,
            allowed_root=repo_root,
            mode=mode,
        )
        print(f"updated {path.relative_to(repo_root).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
