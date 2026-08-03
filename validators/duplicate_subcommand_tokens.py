"""Detect interfaces within one behavioral source whose fixed args_prefix
collides, so the dispatcher cannot route a caller's tokens unambiguously.

Each interface is invoked as
``python3 -m officina.runtime.python_machine_interface_runner <source-file>
Interface <args_prefix...> ...`` — a fresh process per behavioral-source
file. Two interfaces in *different* source files can never collide at
runtime even when they share a module and even when they share a fixed
prefix, because they are never routed by the same argv parse. Only
interfaces declared within the *same* source file's own ``interfaces``
mapping share one dispatch and can genuinely collide, so this validator
scopes the check to one behavioral source at a time, not to the module.

Within one source file, sharing an args_prefix token is not automatically a
bug: this repository has a real, intentional pattern (see
``skills/list-manager/blueprints/rtx-yaml-store.yaml``'s ``create-entry`` /
``cloud-create-entry`` pair) where one literal CLI subcommand is documented
as two separate interface contracts that branch internally on a flag (e.g.
``--cloud``). The structurally sound, directly verifiable invariant that
makes such a pair safe is that both interfaces declare the exact same
``process_binding.entry`` — the same Python dispatch target actually
receives and handles every invocation for that token, so nothing is ever
routed ambiguously between two different handlers. (An earlier version of
this validator tried to infer safety from ``required_flags`` disjointness;
that reasoning was unsound — it degraded to a pairwise check that misses
3+-way collisions, treated `issubset`-style flag matching as if it proved
mutual exclusivity, and never consulted ``forbidden_flags``. Same-entry
equality is trivially transitive and needs none of that.) This validator
only flags a same-source args_prefix collision when at least two interfaces
sharing the token declare *different* ``process_binding.entry`` values.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.common.blueprint_graph import (  # noqa: E402
    BlueprintGraphError,
    load_repository_blueprint_graph,
)
from officina.common.blueprint_inventory import BlueprintInventoryError  # noqa: E402


def _extract_args_prefix(value: Any) -> tuple[str, ...] | None:
    """Return the fixed subcommand tokens declared by one interface value."""

    if not isinstance(value, dict):
        return None

    prefix = value.get("args_prefix")
    if (
        isinstance(prefix, list)
        and prefix
        and all(isinstance(token, str) for token in prefix)
    ):
        return tuple(prefix)

    process_binding = value.get("process_binding")
    if isinstance(process_binding, dict):
        prefix = process_binding.get("args_prefix")
        if (
            isinstance(prefix, list)
            and prefix
            and all(isinstance(token, str) for token in prefix)
        ):
            return tuple(prefix)

    return None


def _process_entry(value: Any) -> str | None:
    """Return the ``process_binding.entry`` dispatch target, if declared."""

    if not isinstance(value, dict):
        return None
    process_binding = value.get("process_binding")
    if not isinstance(process_binding, dict):
        return None
    entry = process_binding.get("entry")
    return entry if isinstance(entry, str) and entry else None


def _same_entry_for_all(ids: list[str], interfaces: dict) -> bool:
    """True when every interface in ``ids`` shares one ``process_binding.entry``.

    Same-entry equality means the exact same Python dispatch target handles
    every invocation carrying this token, so nothing is ever routed
    ambiguously between two different handlers. Equality is transitive, so
    a single set-size check covers any number of participants — no pairwise
    reasoning required.
    """

    entries = {_process_entry(interfaces[interface_id]) for interface_id in ids}
    if len(entries) != 1:
        return False
    (only_entry,) = entries
    return only_entry is not None


def find_duplicate_fixed_subcommands(
    interfaces: dict,
) -> list[tuple[str, list[str]]]:
    """Return fixed subcommand tokens declared by more than one interface.

    ``interfaces`` maps interface identifiers to their declared contract
    (either the flat ``{"args_prefix": [...]}`` shape or the real
    ``{"process_binding": {"args_prefix": [...]}}`` shape). The result lists
    each colliding token alongside the interface IDs that declare it, in
    first-seen order, but only when the sharing interfaces do not all
    declare the same ``process_binding.entry`` (see ``_same_entry_for_all``).
    """

    by_token: dict[tuple[str, ...], list[str]] = {}
    for interface_id, value in interfaces.items():
        prefix = _extract_args_prefix(value)
        if prefix is None:
            continue
        by_token.setdefault(prefix, []).append(interface_id)
    return [
        (" ".join(prefix), interface_ids)
        for prefix, interface_ids in by_token.items()
        if len(interface_ids) > 1
        and not _same_entry_for_all(interface_ids, interfaces)
    ]


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir() or not any(skills_root.glob("*/blueprint.yaml")):
        return errors

    schema_root = repo_root / "references" / "blueprint"
    try:
        graph = load_repository_blueprint_graph(
            repo_root,
            schema_root=(
                schema_root
                if (schema_root / "module.schema.json").is_file()
                else None
            ),
        )
    except (
        BlueprintGraphError,
        BlueprintInventoryError,
        OSError,
        UnicodeError,
    ) as exc:
        errors.append(str(exc))
        return errors

    for source_id, source_node in sorted(graph.nodes.items()):
        if source_node.node_type != "behavioral_source":
            continue
        raw_interfaces = source_node.declaration.get("interfaces")
        if not isinstance(raw_interfaces, dict):
            continue

        for token, interface_ids in find_duplicate_fixed_subcommands(
            raw_interfaces
        ):
            errors.append(
                f"source {source_id!r}: duplicate fixed dispatcher subcommand "
                f"token `{token}` used by interfaces: "
                f"{', '.join(sorted(interface_ids))}"
            )

    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Duplicate dispatcher subcommand tokens found:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
