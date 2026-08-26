#!/usr/bin/env python3
"""Read-only certificate currentness and node-hash reporting."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

RTX_DIR = Path(__file__).resolve().parent
if not __package__ and str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

if __package__:
    from ._skill_sources import (
        SkillSource,
        SkillSourceDiscoveryError,
        dedupe_skill_sources,
        observed_skill_sources,
    )
else:
    from _skill_sources import (
        SkillSource,
        SkillSourceDiscoveryError,
        dedupe_skill_sources,
        observed_skill_sources,
    )
from officina.certification.hashing import CertificationHashError, NodeHashState
from officina.certification.records import certificate_public_key_root
from officina.blueprints.graph import (
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.certification.view import (
    CertificateDependencyDelta,
    CertificateFacetDrift,
    CertificateInputDelta,
    CertificateNodeCurrentness,
    CertificateCurrentnessReport,
    RepositoryCertificationError,
    certificate_log_path,
    certificate_stale_worklist,
    derive_repository_certification_state,
)
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = RTX_DIR.parent
BUILD_DIR = SKILL_ROOT / "_build"
OUTPUT_SCHEMA_VERSION = 2


class DriftCheckError(RuntimeError):
    """Raised when certificate state cannot be read for an exact scope."""


@dataclass(frozen=True)
class _V4DerivedState:
    graph: RepositoryBlueprintGraph
    states: dict[str, NodeHashState]
    basis_hash: str
    currentness: CertificateCurrentnessReport


def _schema_root_for_version(root: Path, schema_version: int) -> Path | None:
    """Return the live schema root unless v5 selects its retired owner."""

    return root / "references" / "blueprint-schema" if schema_version in {4, 6} else None


def _v4_repository_state(
    repo_root: Path,
    *,
    expected_schema_version: int = 6,
    allow_non_atomic: bool = False,
) -> tuple[
    RepositoryBlueprintGraph,
    dict[str, NodeHashState],
    str,
    str,
    Path,
    dict[str, object],
]:
    """Return the canonical graph, hashes, basis, and certifier identity."""

    root = Path(repo_root).resolve()
    try:
        derived = derive_repository_certification_state(
            root,
            expected_schema_version=expected_schema_version,
            schema_root=_schema_root_for_version(root, expected_schema_version),
            allow_non_atomic=allow_non_atomic,
        )
    except (CertificationHashError, RepositoryCertificationError, OSError, ValueError) as exc:
        raise DriftCheckError(str(exc)) from exc
    return (
        derived.graph,
        dict(derived.states),
        derived.source_commit,
        derived.certification_basis_hash,
        root / "references" / "blueprint-schema",
        dict(derived.certifier_identity),
    )


def _derive_v4_repository_state(
    repo_root: Path,
    target_node_ids: Sequence[str],
    *,
    public_key_root: Path,
    expected_schema_version: int = 6,
    allow_non_atomic: bool = False,
) -> _V4DerivedState:
    root = Path(repo_root).resolve()
    try:
        derived = derive_repository_certification_state(
            root,
            public_key_root=public_key_root,
            expected_schema_version=expected_schema_version,
            schema_root=_schema_root_for_version(root, expected_schema_version),
            allow_non_atomic=allow_non_atomic,
        )
    except (CertificationHashError, RepositoryCertificationError, OSError, ValueError) as exc:
        raise DriftCheckError(str(exc)) from exc
    unknown = sorted(set(target_node_ids) - set(derived.graph.nodes))
    if unknown:
        raise DriftCheckError(
            "unknown exact drift target: " + ", ".join(unknown)
        )
    stale_worklist = certificate_stale_worklist(
        derived.graph,
        derived.states,
        derived.currentness,
        target_node_ids,
    )
    target_set = set(target_node_ids)
    return _V4DerivedState(
        graph=derived.graph,
        states=dict(derived.states),
        basis_hash=derived.certification_basis_hash,
        currentness=CertificateCurrentnessReport(
            nodes={
                node_id: derived.currentness.nodes[node_id]
                for node_id in target_node_ids
            },
            stale_worklist=stale_worklist,
            dependency_nodes={
                node_id: derived.currentness.nodes[node_id]
                for node_id in stale_worklist
                if node_id not in target_set
            },
        ),
    )


def _check_v4_repository(
    repo_root: Path,
    target_node_ids: Sequence[str],
    *,
    public_key_root: Path,
    expected_schema_version: int = 6,
    allow_non_atomic: bool = False,
) -> CertificateCurrentnessReport:
    """Return public-key-only currentness for exact node IDs."""

    return _derive_v4_repository_state(
        repo_root,
        target_node_ids,
        public_key_root=public_key_root,
        expected_schema_version=expected_schema_version,
        allow_non_atomic=allow_non_atomic,
    ).currentness


@dataclass(frozen=True)
class NodeDriftStatus:
    """Expose one node's compatible verdict and structured drift evidence.

    Intent
    ------
    Adapt shared certification currentness into the skill-drift JSON and text
    contract without removing existing status fields.

    Rationale
    ---------
    Operators need exact file, dependency, declaration, and facet
    causes to choose the smallest audit boundary.

    Pseudocode
    ----------
    - retain node identity, verdict, concerns, and certificate path
    - serialize node- and facet-level structured deltas

    Wraps
    -----
    - ``CertificateNodeCurrentness`` from the shared certification view
    """

    node_id: str
    current: bool
    concerns: tuple[str, ...]
    certificate_path: Path
    local_hash_changed: bool = False
    declaration_changed: bool = False
    blueprint_path: str | None = None
    input_files: tuple[CertificateInputDelta, ...] = ()
    dependencies: tuple[CertificateDependencyDelta, ...] = ()
    facet_drift: tuple[CertificateFacetDrift, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": "certificate-current" if self.current else "certificate-stale",
            "concerns": list(self.concerns),
            "certificate_path": self.certificate_path.as_posix(),
            "local_hash_changed": self.local_hash_changed,
            "declaration_changed": self.declaration_changed,
            "blueprint_path": self.blueprint_path,
            "input_files": [
                {
                    "change": delta.change,
                    "path": delta.path,
                    "certified": delta.certified,
                    "current": delta.current,
                }
                for delta in self.input_files
            ],
            "dependencies": [
                {
                    "change": delta.change,
                    "relation": delta.relation,
                    "target": delta.target,
                    "interface": delta.interface,
                    "certified": delta.certified,
                    "current": delta.current,
                }
                for delta in self.dependencies
            ],
            "facet_drift": [
                {
                    "facet_id": facet.facet_id,
                    "facet_type": facet.facet_type,
                    "local_hash_changed": facet.local_hash_changed,
                    "declaration_changed": facet.declaration_changed,
                    "blueprint_path": facet.blueprint_path,
                    "input_files": [
                        {
                            "change": delta.change,
                            "path": delta.path,
                            "certified": delta.certified,
                            "current": delta.current,
                        }
                        for delta in facet.input_files
                    ],
                    "dependencies": [
                        {
                            "change": delta.change,
                            "relation": delta.relation,
                            "target": delta.target,
                            "interface": delta.interface,
                            "certified": delta.certified,
                            "current": delta.current,
                        }
                        for delta in facet.dependencies
                    ],
                }
                for facet in self.facet_drift
            ],
        }


@dataclass(frozen=True)
class ModuleDriftReport:
    """Report one requested module scope and its stale dependency closure.

    Intent
    ------
    Preserve scoped module results while exposing the canonical bottom-up
    worklist and structured status of external stale dependencies.

    Rationale
    ---------
    A consumer audit cannot safely precede an unaudited stale provider, even
    when the provider falls outside the requested module's display scope.

    Pseudocode
    ----------
    - store requested node statuses
    - store dependency-first stale node IDs
    - store external dependency statuses separately

    Wraps
    -----
    - ``NodeDriftStatus`` records derived from shared currentness
    """

    skill: str
    source: str
    package_root: Path
    skills_root: Path
    nodes: tuple[NodeDriftStatus, ...]
    stale_worklist: tuple[str, ...] = ()
    dependency_nodes: tuple[NodeDriftStatus, ...] = ()
    repository_stale_worklist: tuple[str, ...] = ()

    @property
    def current(self) -> bool:
        return bool(self.nodes) and all(node.current for node in self.nodes)

    def as_payload(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "source": self.source,
            "package_root": self.package_root.as_posix(),
            "skills_root": self.skills_root.as_posix(),
            "status": "certificate-current" if self.current else "certificate-stale",
            "nodes": [node.as_payload() for node in self.nodes],
            "stale_worklist": list(self.stale_worklist),
            "dependency_nodes": [
                node.as_payload() for node in self.dependency_nodes
            ],
        }


def _node_drift_status(
    derived: _V4DerivedState,
    node_id: str,
) -> NodeDriftStatus:
    """Adapt one shared currentness result without dropping structured drift.

    Intent
    ------
    Translate shared view records to the drift runtime's public status shape.

    Rationale
    ---------
    Keeping this conversion in one place prevents scoped and dependency-node
    reports from disagreeing about structured causes.

    Pseudocode
    ----------
    - read one node from the derived currentness report
    - copy legacy fields and all available structured evidence

    Wraps
    -----
    - ``CertificateNodeCurrentness``
    """

    currentness = derived.currentness.nodes[node_id]
    return NodeDriftStatus(
        node_id=node_id,
        current=currentness.current,
        concerns=currentness.concerns,
        certificate_path=certificate_log_path(derived.graph.nodes[node_id]),
        local_hash_changed=getattr(currentness, "local_hash_changed", False),
        declaration_changed=getattr(currentness, "declaration_changed", False),
        blueprint_path=getattr(currentness, "blueprint_path", None),
        input_files=getattr(currentness, "input_files", ()),
        dependencies=getattr(
            currentness,
            "dependencies",
            (),
        ),
        facet_drift=getattr(currentness, "facet_drift", ()),
    )


@dataclass(frozen=True)
class SkillHashReport:
    skill: str
    source: str
    package_root: Path
    skills_root: Path
    hashes: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "source": self.source,
            "package_root": self.package_root.as_posix(),
            "skills_root": self.skills_root.as_posix(),
            "hashes": self.hashes,
        }


@dataclass(frozen=True)
class SkillHashFailure:
    skill: str
    source: str
    package_root: Path
    skills_root: Path
    message: str

    def as_payload(self) -> dict[str, str]:
        return {
            "skill": self.skill,
            "source": self.source,
            "package_root": self.package_root.as_posix(),
            "skills_root": self.skills_root.as_posix(),
            "message": self.message,
        }


@dataclass(frozen=True)
class RequestedScope:
    source: SkillSource
    skill_names: tuple[str, ...]


def observed_skill_names(skills_root: Path) -> list[str]:
    if not skills_root.is_dir():
        return []
    return sorted(
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )


def blueprint_skill_names(skills_root: Path) -> list[str]:
    return sorted(
        name
        for name in observed_skill_names(skills_root)
        if (skills_root / name / "blueprint.yaml").is_file()
    )


def source_for_skill_root(
    skill_root: Path,
    *,
    source: str = "path",
) -> SkillSource:
    root = Path(skill_root).resolve()
    if root.parent.name != "skills":
        raise DriftCheckError(
            f"exact module root must be a direct child of a skills directory: {root}"
        )
    return SkillSource(
        source=source,
        package_root=root.parent.parent,
        skills_root=root.parent,
    )


def requested_skill_sources(args: argparse.Namespace) -> list[SkillSource]:
    if args.skills_root is not None:
        skills_root = args.skills_root.resolve()
        return [
            SkillSource(
                source="override",
                package_root=skills_root.parent,
                skills_root=skills_root,
            )
        ]
    if args.repo_root is not None:
        root = args.repo_root.resolve()
        return [
            SkillSource(
                source="override",
                package_root=root,
                skills_root=root / "skills",
            )
        ]
    try:
        sources = observed_skill_sources()
    except SkillSourceDiscoveryError as exc:
        raise DriftCheckError(str(exc)) from exc
    for source in sources:
        if source.plugin_id is None:
            continue
        try:
            graph = load_repository_blueprint_graph(
                source.package_root,
                schema_root=source.package_root / "references" / "blueprint-schema",
            )
            if not graph.nodes:
                raise DriftCheckError(
                    "installed blueprint graph has no registered nodes"
                )
        except (OSError, TypeError, ValueError, DriftCheckError) as exc:
            raise DriftCheckError(
                "unsupported active plugin "
                f"{json.dumps(source.plugin_id)} version "
                f"{json.dumps(source.plugin_version)} at "
                f"{source.package_root}: {exc}; repair installed_plugins.json or "
                "pass --skill-root, --skills-root, or --repo-root for the exact "
                "intended installation"
            ) from exc
    return sources


def requested_scopes(args: argparse.Namespace) -> tuple[RequestedScope, ...]:
    if args.skill_root is not None:
        root = args.skill_root.expanduser().resolve()
        return (RequestedScope(source_for_skill_root(root, source="override"), (root.name,)),)

    path_scopes: list[RequestedScope] = []
    names: list[str] = []
    for request in args.skills:
        if "/" in request or "\\" in request or request.startswith((".", "~")):
            root = Path(request).expanduser().resolve()
            path_scopes.append(RequestedScope(source_for_skill_root(root), (root.name,)))
        else:
            names.append(request)
    if args.skills and not names:
        return tuple(path_scopes)

    scopes = list(path_scopes)
    for source in requested_skill_sources(args):
        selected = (
            tuple(names)
            if names
            else tuple(
                observed_skill_names(source.skills_root)
                if args.command == "status"
                else blueprint_skill_names(source.skills_root)
            )
        )
        scopes.append(RequestedScope(source, selected))
    return tuple(scopes)


def _module_node_ids(
    graph: RepositoryBlueprintGraph,
    module_id: str,
) -> tuple[str, ...]:
    node = graph.nodes.get(module_id)
    if node is None or getattr(node, "node_type", "module") != "module":
        return ()
    return tuple(
        sorted(
            {
                module_id,
                *graph.module_sources.get(module_id, ()),
            }
        )
    )


def _derive_for_source(
    source: SkillSource,
    *,
    expected_schema_version: int = 6,
) -> _V4DerivedState:
    try:
        derived = derive_repository_certification_state(
            source.package_root,
            public_key_root=certificate_public_key_root(source.package_root),
            expected_schema_version=expected_schema_version,
            schema_root=_schema_root_for_version(
                source.package_root,
                expected_schema_version,
            ),
        )
    except (CertificationHashError, RepositoryCertificationError, OSError, ValueError) as exc:
        raise DriftCheckError(str(exc)) from exc
    return _V4DerivedState(
        graph=derived.graph,
        states=dict(derived.states),
        basis_hash=derived.certification_basis_hash,
        currentness=derived.currentness,
    )


def reports_for_scopes(
    scopes: tuple[RequestedScope, ...],
    *,
    expected_schema_version: int = 6,
) -> list[ModuleDriftReport]:
    reports: list[ModuleDriftReport] = []
    requested = {name for scope in scopes for name in scope.skill_names}
    found: set[str] = set()
    cache: dict[Path, _V4DerivedState] = {}
    requested_node_ids: dict[Path, set[str]] = {}
    for scope in scopes:
        key = scope.source.package_root.resolve()
        for skill_name in scope.skill_names:
            if expected_schema_version == 4 and not (
                scope.source.skills_root / skill_name / "SKILL.md"
            ).is_file():
                continue
            if key not in cache:
                cache[key] = _derive_for_source(
                    scope.source,
                    expected_schema_version=expected_schema_version,
                )
            derived = cache[key]
            node_ids = _module_node_ids(derived.graph, skill_name)
            if expected_schema_version in {5, 6} and not node_ids:
                continue
            found.add(skill_name)
            if not node_ids:
                raise DriftCheckError(f"{skill_name}: module owns no nodes")
            requested_node_ids.setdefault(key, set()).update(node_ids)
            stale_worklist = certificate_stale_worklist(
                derived.graph,
                derived.states,
                derived.currentness,
                node_ids,
            )
            node_id_set = set(node_ids)
            reports.append(
                ModuleDriftReport(
                    skill=skill_name,
                    source=scope.source.source,
                    package_root=scope.source.package_root,
                    skills_root=scope.source.skills_root,
                    nodes=tuple(
                        _node_drift_status(derived, node_id)
                        for node_id in node_ids
                    ),
                    stale_worklist=stale_worklist,
                    dependency_nodes=tuple(
                        _node_drift_status(derived, node_id)
                        for node_id in stale_worklist
                        if node_id not in node_id_set
                    ),
                )
            )
    missing = sorted(requested - found)
    if missing:
        raise DriftCheckError(
            "module(s) not found in installed skill roots: " + ", ".join(missing)
        )
    repository_worklists = {
        key: certificate_stale_worklist(
            cache[key].graph,
            cache[key].states,
            cache[key].currentness,
            tuple(node_ids),
        )
        for key, node_ids in requested_node_ids.items()
    }
    return [
        replace(
            report,
            repository_stale_worklist=repository_worklists[
                report.package_root.resolve()
            ],
        )
        for report in reports
    ]


def hash_reports_for_scopes(
    scopes: tuple[RequestedScope, ...],
    *,
    expected_schema_version: int = 6,
) -> tuple[list[SkillHashReport], list[SkillHashFailure]]:
    reports: list[SkillHashReport] = []
    failures: list[SkillHashFailure] = []
    requested = {name for scope in scopes for name in scope.skill_names}
    found: set[str] = set()
    cache: dict[Path, _V4DerivedState] = {}
    for scope in scopes:
        key = scope.source.package_root.resolve()
        for skill_name in scope.skill_names:
            if expected_schema_version == 4 and not (
                scope.source.skills_root / skill_name / "SKILL.md"
            ).is_file():
                continue
            try:
                if key not in cache:
                    cache[key] = _derive_for_source(
                        scope.source,
                        expected_schema_version=expected_schema_version,
                    )
                derived = cache[key]
                node_ids = _module_node_ids(derived.graph, skill_name)
                if expected_schema_version in {5, 6} and not node_ids:
                    continue
                found.add(skill_name)
                if not node_ids:
                    raise DriftCheckError(f"{skill_name}: module owns no nodes")
                reports.append(
                    SkillHashReport(
                        skill=skill_name,
                        source=scope.source.source,
                        package_root=scope.source.package_root,
                        skills_root=scope.source.skills_root,
                        hashes={
                            "certification_basis": derived.basis_hash,
                            "nodes": {
                                node_id: {
                                    "node_type": derived.graph.nodes[node_id].node_type,
                                    "node_hash": derived.states[node_id].node_hash,
                                    "dependencies": list(
                                        derived.states[node_id].dependency_hashes
                                    ),
                                }
                                for node_id in node_ids
                            },
                        },
                    )
                )
            except DriftCheckError as exc:
                failures.append(
                    SkillHashFailure(
                        skill=skill_name,
                        source=scope.source.source,
                        package_root=scope.source.package_root,
                        skills_root=scope.source.skills_root,
                        message=str(exc),
                    )
                )
    missing = sorted(requested - found)
    if missing:
        raise DriftCheckError(
            "module(s) not found in installed skill roots: " + ", ".join(missing)
        )
    return reports, failures


def _repository_worklists(
    reports: Sequence[ModuleDriftReport],
) -> tuple[tuple[Path, tuple[str, ...]], ...]:
    """Return one stable stale worklist for each distinct repository root."""

    grouped: dict[Path, list[ModuleDriftReport]] = {}
    for report in reports:
        grouped.setdefault(report.package_root.resolve(), []).append(report)
    result: list[tuple[Path, tuple[str, ...]]] = []
    for package_root in sorted(grouped):
        repository_reports = grouped[package_root]
        canonical = next(
            (
                report.repository_stale_worklist
                for report in repository_reports
                if report.repository_stale_worklist
            ),
            (),
        )
        if not canonical:
            canonical = tuple(
                dict.fromkeys(
                    node_id
                    for report in repository_reports
                    for node_id in report.stale_worklist
                )
            )
        result.append((package_root, canonical))
    return tuple(result)


def _display_worklist(
    worklists: Sequence[tuple[Path, tuple[str, ...]]],
) -> tuple[str, ...]:
    """Keep single-repository IDs short and qualify cross-repository IDs."""

    if len(worklists) <= 1:
        return worklists[0][1] if worklists else ()
    return tuple(
        f"{package_root.as_posix()}::{node_id}"
        for package_root, node_ids in worklists
        for node_id in node_ids
    )


def build_payload(reports: Sequence[ModuleDriftReport]) -> dict[str, Any]:
    current = sum(report.current for report in reports)
    repository_worklists = _repository_worklists(reports)
    stale_worklist = _display_worklist(repository_worklists)
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "summary": {
            "certificate-current": current,
            "certificate-stale": len(reports) - current,
        },
        "skills": [report.as_payload() for report in reports],
        "stale_worklist": list(stale_worklist),
        "repository_stale_worklists": [
            {
                "package_root": package_root.as_posix(),
                "stale_worklist": list(node_ids),
            }
            for package_root, node_ids in repository_worklists
        ],
    }


def build_hash_payload(
    reports: Sequence[SkillHashReport],
    failures: Sequence[SkillHashFailure],
) -> dict[str, Any]:
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "skills": [report.as_payload() for report in reports],
        "failures": [failure.as_payload() for failure in failures],
    }


def render_text(reports: Sequence[ModuleDriftReport]) -> str:
    repository_worklists = _repository_worklists(reports)
    stale_worklist = _display_worklist(repository_worklists)
    lines = [
        "# Certificate Drift Report",
        "",
        "## Dependency-first stale worklist",
        "",
        *(
            [f"{index}. {node_id}" for index, node_id in enumerate(stale_worklist, 1)]
            if stale_worklist
            else ["No stale nodes."]
        ),
        "",
        "| Source | Module | Status | Concerns |",
        "|---|---|---|---|",
    ]
    for report in reports:
        concerns = "; ".join(
            f"{node.node_id}: {', '.join(node.concerns)}"
            for node in report.nodes
            if node.concerns
        )
        status = "certificate-current" if report.current else "certificate-stale"
        lines.append(
            f"| {report.source} | {report.skill} | {status} | {concerns} |"
        )
    structured_nodes = [
        (report.package_root.resolve(), node)
        for report in reports
        for node in (*report.nodes, *report.dependency_nodes)
    ]
    worklist_order = {
        (package_root, node_id): index
        for index, (package_root, node_id) in enumerate(
            (package_root, node_id)
            for package_root, node_ids in repository_worklists
            for node_id in node_ids
        )
    }
    structured_nodes.sort(
        key=lambda item: worklist_order.get(
            (item[0], item[1].node_id),
            len(worklist_order),
        )
    )
    structured: list[
        tuple[Path, NodeDriftStatus, CertificateFacetDrift]
    ] = []
    seen_structured: set[tuple[Path, str, str, str]] = set()
    for package_root, node in structured_nodes:
        facets = (
            *(
                (
                    CertificateFacetDrift(
                        facet_id=node.node_id,
                        facet_type="node",
                        local_hash_changed=node.local_hash_changed,
                        declaration_changed=node.declaration_changed,
                        blueprint_path=node.blueprint_path,
                        input_files=node.input_files,
                        dependencies=node.dependencies,
                    ),
                )
                if node.local_hash_changed
                or node.input_files
                or node.dependencies
                else ()
            ),
            *node.facet_drift,
        )
        for facet in facets:
            key = (
                package_root,
                node.node_id,
                facet.facet_type,
                facet.facet_id,
            )
            if key in seen_structured:
                continue
            seen_structured.add(key)
            structured.append((package_root, node, facet))
    if structured:
        lines.extend(["", "## Structured drift", ""])
    for package_root, node, facet in structured:
        displayed_node_id = (
            f"{package_root.as_posix()}::{node.node_id}"
            if len(repository_worklists) > 1
            else node.node_id
        )
        lines.append(
            f"### {displayed_node_id} / {facet.facet_type} {facet.facet_id}"
        )
        if facet.declaration_changed and facet.blueprint_path is not None:
            lines.append(f"- modified declaration {facet.blueprint_path}")
        lines.extend(
            f"- {delta.change} input {delta.path}"
            for delta in facet.input_files
        )
        lines.extend(
            (
                f"- {delta.change} interface dependency {delta.interface}"
                if delta.interface is not None
                else f"- {delta.change} dependency {delta.relation} {delta.target}"
            )
            for delta in facet.dependencies
        )
        lines.append("")
    return "\n".join(lines) + "\n"


def render_hash_text(
    reports: Sequence[SkillHashReport],
    failures: Sequence[SkillHashFailure],
) -> str:
    lines = ["# Node Hash Report", ""]
    for report in reports:
        lines.append(f"## {report.skill}")
        lines.append("")
        lines.append(json.dumps(report.hashes, indent=2, sort_keys=True))
        lines.append("")
    for failure in failures:
        lines.append(f"error [{failure.skill}]: {failure.message}")
    return "\n".join(lines) + "\n"


def write_markdown_report(
    markdown: str,
    now: datetime | None = None,
) -> Path:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = BUILD_DIR / f"certificate-drift-{timestamp}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only certificate drift checker.")
    subparsers = parser.add_subparsers(dest="command")
    status = subparsers.add_parser("status")
    status.add_argument("skills", nargs="*")
    status.add_argument("--all", action="store_true")
    status.add_argument("--json", action="store_true")
    status.add_argument("--repo-root", type=Path, help=argparse.SUPPRESS)
    status.add_argument("--skill-root", type=Path)
    status.add_argument("--skills-root", type=Path, help=argparse.SUPPRESS)
    hashes = subparsers.add_parser("compute-hashes")
    hashes.add_argument("skills", nargs="*")
    hashes.add_argument("--json", action="store_true")
    hashes.add_argument("--repo-root", type=Path, help=argparse.SUPPRESS)
    hashes.add_argument("--skill-root", type=Path)
    hashes.add_argument("--skills-root", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command is None:
        print("error: command is required", file=sys.stderr)
        return 2
    if args.command == "status" and args.skills and args.all:
        print("error: status accepts names or --all, not both", file=sys.stderr)
        return 2
    try:
        scopes = requested_scopes(args)
        if not scopes:
            raise DriftCheckError("no installed skill roots were found")
        if args.command == "status":
            reports = reports_for_scopes(scopes)
            if args.json:
                print(json.dumps(build_payload(reports), indent=2, sort_keys=True))
            else:
                rendered = render_text(reports)
                report_path = write_markdown_report(rendered)
                print(rendered, end="")
                print(f"\nSaved report: {report_path.as_posix()}")
            return 0
        reports, failures = hash_reports_for_scopes(scopes)
        if args.json:
            print(
                json.dumps(
                    build_hash_payload(reports, failures),
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(render_hash_text(reports, failures), end="")
        return 2 if failures else 0
    except DriftCheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


class Interface(PythonArgvMachineInterface):
    """Dispatcher adapter for read-only certificate drift."""

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
