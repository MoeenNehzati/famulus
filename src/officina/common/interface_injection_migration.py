"""Read-only disposition report for replacing legacy skill-wide prompt unions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .blueprint_graph import RepositoryBlueprintGraph


class InterfaceInjectionMigrationError(ValueError):
    pass


_DISPOSITIONS = frozenset({"add-direct-edge", "keep-uninjected", "retire"})


@dataclass(frozen=True)
class InterfaceMigrationEntry:
    interface_id: str
    disposition: str
    authored_consumers: tuple[str, ...]
    target_exists: bool


@dataclass(frozen=True)
class InterfaceInjectionMigrationReport:
    entries: tuple[InterfaceMigrationEntry, ...]

    def as_document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "interfaces": [
                {
                    "interface": entry.interface_id,
                    "disposition": entry.disposition,
                    "authored_consumers": list(entry.authored_consumers),
                    "target_exists": entry.target_exists,
                }
                for entry in self.entries
            ],
        }


def build_interface_injection_migration_report(
    graph: RepositoryBlueprintGraph,
    legacy_union_exports: Iterable[str],
    dispositions: Mapping[str, str],
) -> InterfaceInjectionMigrationReport:
    """Require one explicit disposition for every formerly injected export."""

    legacy = list(legacy_union_exports)
    if len(set(legacy)) != len(legacy):
        raise InterfaceInjectionMigrationError(
            "legacy union contains duplicate interface IDs"
        )
    expected = set(legacy)
    supplied = set(dispositions)
    missing = sorted(expected - supplied)
    extra = sorted(supplied - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing dispositions: {missing}")
        if extra:
            details.append(f"unexpected dispositions: {extra}")
        raise InterfaceInjectionMigrationError("; ".join(details))

    consumers_by_target: dict[str, set[str]] = {}
    for edge in graph.node_edges:
        source = graph.nodes.get(edge.source_id)
        if edge.relation != "uses-interface" or source is None:
            continue
        if source.node_type != "llm-interface":
            continue
        consumers_by_target.setdefault(edge.target_id, set()).add(source.node_id)

    entries = []
    for interface_id in sorted(expected):
        disposition = dispositions[interface_id]
        if disposition not in _DISPOSITIONS:
            raise InterfaceInjectionMigrationError(
                f"{interface_id}: invalid disposition {disposition!r}"
            )
        target_exists = interface_id in graph.machine_exports
        if disposition == "add-direct-edge" and not target_exists:
            raise InterfaceInjectionMigrationError(
                f"{interface_id}: add-direct-edge requires a target export"
            )
        entries.append(
            InterfaceMigrationEntry(
                interface_id=interface_id,
                disposition=disposition,
                authored_consumers=tuple(
                    sorted(consumers_by_target.get(interface_id, ()))
                ),
                target_exists=target_exists,
            )
        )
    return InterfaceInjectionMigrationReport(tuple(entries))
