#!/usr/bin/env python3
"""Read-only certificate currentness and node-hash reporting."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

RTX_DIR = Path(__file__).resolve().parent
if str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

from _skill_sources import (
    SkillSource,
    SkillSourceDiscoveryError,
    observed_skill_sources,
)
from officina.common.certification_hashing import CertificationHashError, NodeHashState
from officina.common.certificate_records import certificate_public_key_root
from officina.common.blueprint_graph import (
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.common.certification_view import (
    CertificateCurrentnessReport,
    RepositoryCertificationError,
    certificate_log_path,
    derive_repository_certification_state,
)
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = RTX_DIR.parent
BUILD_DIR = SKILL_ROOT / "_build"
OUTPUT_SCHEMA_VERSION = 1


class DriftCheckError(RuntimeError):
    """Raised when certificate state cannot be read for an exact scope."""


@dataclass(frozen=True)
class _V4DerivedState:
    graph: RepositoryBlueprintGraph
    states: dict[str, NodeHashState]
    basis_hash: str
    currentness: CertificateCurrentnessReport


def _v4_repository_state(
    repo_root: Path,
    *,
    allow_non_atomic: bool = False,
) -> tuple[
    RepositoryBlueprintGraph,
    dict[str, NodeHashState],
    str,
    str,
    Path,
    dict[str, object],
]:
    """Return the canonical all-v4 graph, hashes, basis, and certifier identity."""

    root = Path(repo_root).resolve()
    try:
        derived = derive_repository_certification_state(
            root,
            allow_non_atomic=allow_non_atomic,
        )
    except (CertificationHashError, RepositoryCertificationError, OSError, ValueError) as exc:
        raise DriftCheckError(str(exc)) from exc
    return (
        derived.graph,
        dict(derived.states),
        derived.source_commit,
        derived.certification_basis_hash,
        root / "references" / "blueprint",
        dict(derived.certifier_identity),
    )


def _derive_v4_repository_state(
    repo_root: Path,
    target_node_ids: Sequence[str],
    *,
    public_key_root: Path,
    allow_non_atomic: bool = False,
) -> _V4DerivedState:
    root = Path(repo_root).resolve()
    try:
        derived = derive_repository_certification_state(
            root,
            public_key_root=public_key_root,
            allow_non_atomic=allow_non_atomic,
        )
    except (CertificationHashError, RepositoryCertificationError, OSError, ValueError) as exc:
        raise DriftCheckError(str(exc)) from exc
    unknown = sorted(set(target_node_ids) - set(derived.graph.nodes))
    if unknown:
        raise DriftCheckError(
            "unknown exact v4 drift target: " + ", ".join(unknown)
        )
    return _V4DerivedState(
        graph=derived.graph,
        states=dict(derived.states),
        basis_hash=derived.certification_basis_hash,
        currentness=CertificateCurrentnessReport(
            nodes={
                node_id: derived.currentness.nodes[node_id]
                for node_id in target_node_ids
            }
        ),
    )


def _check_v4_repository(
    repo_root: Path,
    target_node_ids: Sequence[str],
    *,
    public_key_root: Path,
    allow_non_atomic: bool = False,
) -> CertificateCurrentnessReport:
    """Return public-key-only currentness for exact v4 node IDs."""

    return _derive_v4_repository_state(
        repo_root,
        target_node_ids,
        public_key_root=public_key_root,
        allow_non_atomic=allow_non_atomic,
    ).currentness


@dataclass(frozen=True)
class NodeDriftStatus:
    node_id: str
    current: bool
    concerns: tuple[str, ...]
    certificate_path: Path

    def as_payload(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": "certificate-current" if self.current else "certificate-stale",
            "concerns": list(self.concerns),
            "certificate_path": self.certificate_path.as_posix(),
        }


@dataclass(frozen=True)
class ModuleDriftReport:
    skill: str
    source: str
    package_root: Path
    skills_root: Path
    nodes: tuple[NodeDriftStatus, ...]

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
        }


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
    if args.repo_root != REPO_ROOT:
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
                schema_root=source.package_root / "references" / "blueprint",
            )
            if not graph.nodes:
                raise DriftCheckError("installed blueprint graph has no v4 nodes")
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
    module_root: Path,
) -> tuple[str, ...]:
    resolved = module_root.resolve()
    return tuple(
        sorted(
            node_id
            for node_id, node in graph.nodes.items()
            if node.skill_root.resolve() == resolved
        )
    )


def _derive_for_source(source: SkillSource) -> _V4DerivedState:
    try:
        derived = derive_repository_certification_state(
            source.package_root,
            public_key_root=certificate_public_key_root(source.package_root),
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
) -> list[ModuleDriftReport]:
    reports: list[ModuleDriftReport] = []
    requested = {name for scope in scopes for name in scope.skill_names}
    found: set[str] = set()
    cache: dict[Path, _V4DerivedState] = {}
    for scope in scopes:
        key = scope.source.package_root.resolve()
        for skill_name in scope.skill_names:
            module_root = scope.source.skills_root / skill_name
            if not (module_root / "SKILL.md").is_file():
                continue
            found.add(skill_name)
            if key not in cache:
                cache[key] = _derive_for_source(scope.source)
            derived = cache[key]
            node_ids = _module_node_ids(derived.graph, module_root)
            if not node_ids:
                raise DriftCheckError(f"{skill_name}: module owns no v4 nodes")
            reports.append(
                ModuleDriftReport(
                    skill=skill_name,
                    source=scope.source.source,
                    package_root=scope.source.package_root,
                    skills_root=scope.source.skills_root,
                    nodes=tuple(
                        NodeDriftStatus(
                            node_id=node_id,
                            current=derived.currentness.nodes[node_id].current,
                            concerns=derived.currentness.nodes[node_id].concerns,
                            certificate_path=certificate_log_path(
                                derived.graph.nodes[node_id]
                            ),
                        )
                        for node_id in node_ids
                    ),
                )
            )
    missing = sorted(requested - found)
    if missing:
        raise DriftCheckError(
            "module(s) not found in installed skill roots: " + ", ".join(missing)
        )
    return reports


def hash_reports_for_scopes(
    scopes: tuple[RequestedScope, ...],
) -> tuple[list[SkillHashReport], list[SkillHashFailure]]:
    reports: list[SkillHashReport] = []
    failures: list[SkillHashFailure] = []
    requested = {name for scope in scopes for name in scope.skill_names}
    found: set[str] = set()
    cache: dict[Path, _V4DerivedState] = {}
    for scope in scopes:
        key = scope.source.package_root.resolve()
        for skill_name in scope.skill_names:
            module_root = scope.source.skills_root / skill_name
            if not (module_root / "SKILL.md").is_file():
                continue
            found.add(skill_name)
            try:
                if key not in cache:
                    cache[key] = _derive_for_source(scope.source)
                derived = cache[key]
                node_ids = _module_node_ids(derived.graph, module_root)
                if not node_ids:
                    raise DriftCheckError(f"{skill_name}: module owns no v4 nodes")
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


def build_payload(reports: Sequence[ModuleDriftReport]) -> dict[str, Any]:
    current = sum(report.current for report in reports)
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "summary": {
            "certificate-current": current,
            "certificate-stale": len(reports) - current,
        },
        "skills": [report.as_payload() for report in reports],
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
    lines = [
        "# Certificate Drift Report",
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
    status.add_argument("--repo-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    status.add_argument("--skill-root", type=Path)
    status.add_argument("--skills-root", type=Path, help=argparse.SUPPRESS)
    hashes = subparsers.add_parser("compute-hashes")
    hashes.add_argument("skills", nargs="*")
    hashes.add_argument("--json", action="store_true")
    hashes.add_argument("--repo-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
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
