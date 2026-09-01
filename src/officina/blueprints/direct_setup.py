"""Build one sparse canonical setup graph for an authorized direct route."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from officina.blueprints.graph import (
    BlueprintGraphError,
    InterfaceExport,
    ManagedSetup,
    RepositoryBlueprintGraph,
    _managed_setup_for_export,
    _setup_requirement,
    managed_setup_order,
)
from officina.configuration.repository import RepositoryConfiguration
from officina.dispatcher.direct_authorization import (
    resolve_direct_export_from_module,
)
from officina.dispatcher.direct_blueprints import (
    DirectBlueprintError,
    DirectBlueprintRepository,
    DirectModule,
    parse_interface_id,
)
from officina.dispatcher.direct_models import DirectInterfaceExport


@dataclass(frozen=True)
class DirectSetupProjection:
    """One existing graph plus exact lifecycle classification for a target."""

    graph: RepositoryBlueprintGraph
    lifecycle: tuple[str, Literal["setup", "teardown"]] | None


def _interface_export(export: DirectInterfaceExport) -> InterfaceExport:
    """Convert one already validated direct export to the canonical graph value."""

    return InterfaceExport(
        interface_id=export.interface_id,
        version=export.version,
        local_name=export.interface_id.rsplit(".interface.", 1)[-1],
        module_node_id=export.module_node_id,
        declaration=export.declaration,
        source_node_id=export.source_node_id,
        source_interface_id=export.source_interface_id,
        export_declaration=export.export_declaration,
        terminal_interface_id=export.terminal_interface_id,
        terminal_module_node_id=export.terminal_module_node_id,
    )


def _module_exports(module: DirectModule) -> dict[str, InterfaceExport]:
    """Resolve all exports of one already selected module without deriving paths."""

    exports: dict[str, InterfaceExport] = {}
    for export_id in module.declaration["exports"]:
        _source, direct_export = resolve_direct_export_from_module(
            module,
            export_id,
            None,
        )
        exports[export_id] = _interface_export(direct_export)
    return dict(sorted(exports.items()))


def _managed_declarations(module: DirectModule) -> tuple[str, ...]:
    """Return public export IDs that explicitly mention setup management."""

    declarations = module.declaration["exports"]
    assert isinstance(declarations, Mapping)
    managed = []
    for export_id, declaration in declarations.items():
        if not isinstance(export_id, str) or not isinstance(declaration, Mapping):
            raise BlueprintGraphError(
                f"{module.blueprint_path}: invalid export declaration"
            )
        if declaration.get("setup_management") is not None:
            managed.append(export_id)
    return tuple(sorted(managed))


def _sole_managed_declaration(module: DirectModule) -> str | None:
    """Return one owner declaration while enforcing the module-local limit."""

    declared = _managed_declarations(module)
    if len(declared) > 1:
        raise BlueprintGraphError(
            f"{declared[1]}: module {module.module_id!r} may declare at most "
            f"one managed setup (already declared by {declared[0]})"
        )
    return declared[0] if declared else None


def _record_ancestry(
    module_parents: dict[str, str | None],
    ancestry: tuple[DirectModule, ...],
) -> None:
    """Merge one repository-provided ancestry into canonical parent metadata."""

    for index, module in enumerate(ancestry):
        parent_id = ancestry[index - 1].module_id if index else None
        existing = module_parents.get(module.module_id)
        if existing is not None and existing != parent_id:
            raise BlueprintGraphError(
                f"conflicting parents for module {module.module_id!r}"
            )
        module_parents[module.module_id] = parent_id


def _sparse_graph(
    *,
    exports: Mapping[str, InterfaceExport],
    module_parents: Mapping[str, str | None],
    setup_requirements: Mapping[str, tuple[tuple[str, int], ...]] | None = None,
    managed_setups: Mapping[str, ManagedSetup] | None = None,
) -> RepositoryBlueprintGraph:
    """Construct the existing graph type with only setup-relevant fields."""

    return RepositoryBlueprintGraph(
        nodes={},
        node_edges=(),
        exports=dict(sorted(exports.items())),
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        module_parents=dict(sorted(module_parents.items())),
        setup_requirements=dict(sorted((setup_requirements or {}).items())),
        managed_setups=dict(sorted((managed_setups or {}).items())),
    )


def _merge_exports(
    exports: dict[str, InterfaceExport],
    additions: Mapping[str, InterfaceExport],
) -> None:
    """Merge selected-module exports while rejecting contradictory identities."""

    for export_id, export in additions.items():
        previous = exports.get(export_id)
        if previous is not None and previous != export:
            raise BlueprintGraphError(f"duplicate export id {export_id!r}")
        exports[export_id] = export


def _managed_for_setup(
    module: DirectModule,
    setup_id: str,
    exports: Mapping[str, InterfaceExport],
) -> ManagedSetup | None:
    """Validate the sole managed declaration in one relevant module."""

    _sole_managed_declaration(module)
    export = exports.get(setup_id)
    if export is None:
        raise BlueprintGraphError(
            f"setup prerequisite {setup_id!r} is not a public setup interface"
        )
    return _managed_setup_for_export(setup_id, export, exports)


def load_direct_setup_projection(
    repository: DirectBlueprintRepository,
    target_modules: tuple[DirectModule, ...],
    target_export: DirectInterfaceExport,
) -> DirectSetupProjection:
    """Load only the managed setup closure relevant to one authorized target."""

    if not target_modules or target_modules[-1].module_id != target_export.module_node_id:
        raise DirectBlueprintError(
            f"target ancestry does not own {target_export.interface_id}",
            code="dispatcher.interface_not_found",
            target_module_id=target_export.module_node_id,
        )

    module_parents: dict[str, str | None] = {}
    _record_ancestry(module_parents, target_modules)
    ancestry_declarations: dict[str, str | None] = {}
    for module in target_modules:
        ancestry_declarations[module.module_id] = _sole_managed_declaration(module)
    if all(setup_id is None for setup_id in ancestry_declarations.values()):
        graph = _sparse_graph(
            exports={target_export.interface_id: _interface_export(target_export)},
            module_parents=module_parents,
        )
        return DirectSetupProjection(graph=graph, lifecycle=None)

    exports: dict[str, InterfaceExport] = {}
    module_exports: dict[str, dict[str, InterfaceExport]] = {}
    modules_by_id = {module.module_id: module for module in target_modules}
    for module in target_modules:
        selected = _module_exports(module)
        module_exports[module.module_id] = selected
        _merge_exports(exports, selected)

    for module in target_modules:
        setup_id = ancestry_declarations[module.module_id]
        if setup_id is not None:
            _managed_setup_for_export(
                setup_id,
                module_exports[module.module_id][setup_id],
                exports,
            )

    owner = next(
        module
        for module in reversed(target_modules)
        if ancestry_declarations[module.module_id] is not None
    )
    root_setup_id = ancestry_declarations[owner.module_id]
    assert root_setup_id is not None

    setup_requirements: dict[str, tuple[tuple[str, int], ...]] = {}

    def load_requirements(setup_id: str) -> None:
        if setup_id in setup_requirements:
            return
        export = exports.get(setup_id)
        if export is None:
            raise BlueprintGraphError(
                f"setup prerequisite {setup_id!r} is not a public setup interface"
            )
        requirement = _setup_requirement(setup_id, export)
        if requirement is None:
            raise BlueprintGraphError(
                f"setup prerequisite {setup_id!r} is not a public setup interface"
            )
        setup_requirements[setup_id] = requirement
        for prerequisite_id, version in requirement:
            module_id, _local_name = parse_interface_id(prerequisite_id)
            module = repository.load_module(module_id)
            ancestry = repository.load_ancestry(module_id)
            _record_ancestry(module_parents, ancestry)
            modules_by_id[module_id] = module
            selected = module_exports.get(module_id)
            if selected is None:
                selected = _module_exports(module)
                module_exports[module_id] = selected
                _merge_exports(exports, selected)
            prerequisite = selected.get(prerequisite_id)
            if prerequisite is None:
                raise BlueprintGraphError(
                    f"{setup_id}: setup prerequisite {prerequisite_id!r} is not a "
                    "public setup interface"
                )
            if prerequisite.version != version:
                raise BlueprintGraphError(
                    f"{setup_id}: setup prerequisite {prerequisite_id!r} pins version "
                    f"{version}, but target version is {prerequisite.version}"
                )
            load_requirements(prerequisite_id)

    load_requirements(root_setup_id)

    managed_setups: dict[str, ManagedSetup] = {}
    for setup_id in sorted(setup_requirements):
        module_id, _local_name = parse_interface_id(setup_id)
        metadata = _managed_for_setup(modules_by_id[module_id], setup_id, exports)
        if metadata is not None:
            managed_setups[setup_id] = metadata

    graph = _sparse_graph(
        exports=exports,
        module_parents=module_parents,
        setup_requirements=setup_requirements,
        managed_setups=managed_setups,
    )
    managed_setup_order(graph, root_setup_id)
    root_metadata = graph.managed_setups[root_setup_id]
    lifecycle: tuple[str, Literal["setup", "teardown"]] | None = None
    if target_export.interface_id == root_metadata.setup_interface:
        lifecycle = (root_setup_id, "setup")
    elif target_export.interface_id == root_metadata.teardown_interface:
        lifecycle = (root_setup_id, "teardown")
    return DirectSetupProjection(graph=graph, lifecycle=lifecycle)


def resolve_direct_export(
    repository: DirectBlueprintRepository,
    target_modules: tuple[DirectModule, ...],
    target_interface: str,
) -> DirectInterfaceExport:
    """Reuse direct export validation for one repository-provided target ancestry."""

    target_module_id, _local_name = parse_interface_id(target_interface)
    terminal = repository.load_module(target_module_id)
    if not target_modules or terminal != target_modules[-1]:
        raise DirectBlueprintError(
            f"target ancestry does not match {target_interface}",
            code="dispatcher.interface_not_found",
            target_module_id=target_module_id,
        )
    _source, export = resolve_direct_export_from_module(
        terminal,
        target_interface,
        None,
    )
    return export


def load_direct_setup_graph(
    configuration: RepositoryConfiguration,
    target_interface: str,
) -> RepositoryBlueprintGraph:
    """Load a manager subprocess graph through one invocation-local repository."""

    repository = DirectBlueprintRepository(configuration)
    target_module_id, _local_name = parse_interface_id(target_interface)
    target_modules = repository.load_ancestry(target_module_id)
    target_export = resolve_direct_export(
        repository,
        target_modules,
        target_interface,
    )
    return load_direct_setup_projection(
        repository,
        target_modules,
        target_export,
    ).graph


__all__ = [
    "DirectSetupProjection",
    "load_direct_setup_graph",
    "load_direct_setup_projection",
    "resolve_direct_export",
]
