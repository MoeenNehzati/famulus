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
    """Carry one repository derivation reused by status and hash reporting."""

    graph: RepositoryBlueprintGraph
    states: dict[str, NodeHashState]
    basis_hash: str
    currentness: CertificateCurrentnessReport


def _v4_repository_state(
    repo_root: Path,
    *,
    expected_schema_version: int = 5,
    allow_non_atomic: bool = False,
) -> tuple[
    RepositoryBlueprintGraph,
    dict[str, NodeHashState],
    str,
    str,
    Path,
    dict[str, object],
]:
    """Derive canonical repository state for legacy exact-node callers.

    Intent
    ------
    Return the graph, node hashes, source revision, certification basis, schema
    root, and certifier identity from one certification-state derivation.

    Rationale
    ---------
    Legacy tests and compatibility callers need the former tuple shape while
    still sharing the canonical derivation path and its atomic-read policy.

    Pseudocode
    ----------
    - set repository_root = resolved requested repository
    - set derived_state = canonical certification state for requested schema
    - if derivation fails:
      - raise DriftCheckError with the original diagnostic
    - return graph hashes revision basis schema root and certifier identity

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.common.certification_view.derive_repository_certification_state:
      why:
        constructs: "Builds the canonical graph, hashes, basis, revision, and certifier identity returned to compatibility callers."
    .DriftCheckError:
      why:
        raises: "Translates certification, repository, and filesystem failures into the drift checker's public error domain."
    """

    root = Path(repo_root).resolve()
    try:
        derived = derive_repository_certification_state(
            root,
            expected_schema_version=expected_schema_version,
            schema_root=(
                root / "references" / "blueprint"
                if expected_schema_version == 4
                else None
            ),
            allow_non_atomic=allow_non_atomic,
        )
    except (CertificationHashError, RepositoryCertificationError, OSError, ValueError) as exc:
        raise DriftCheckError(str(exc)) from exc
    return (
        derived.graph,
        dict(derived.states),
        derived.source_commit,
        derived.certification_basis_hash,
        (
            root / "references" / "blueprint"
            if expected_schema_version == 5
            else root / "references" / "blueprint" / "migrations" / "v4"
        ),
        dict(derived.certifier_identity),
    )


def _derive_v4_repository_state(
    repo_root: Path,
    target_node_ids: Sequence[str],
    *,
    public_key_root: Path,
    expected_schema_version: int = 5,
    allow_non_atomic: bool = False,
) -> _V4DerivedState:
    """Derive and restrict signed currentness to exact requested node IDs.

    Intent
    ------
    Build one canonical repository snapshot and select currentness records for
    every explicitly requested global node identifier.

    Rationale
    ---------
    Exact-node checks must reject unknown identifiers instead of silently
    broadening scope or returning a partial certificate-current result.

    Pseudocode
    ----------
    - set repository_root = resolved requested repository
    - set derived_state = canonical certification state with retained public keys
    - if derivation fails:
      - raise DriftCheckError with the original diagnostic
    - set unknown_ids = requested identifiers absent from graph
    - if unknown_ids is nonempty:
      - raise DriftCheckError listing unknown exact targets
    - return _V4DerivedState with currentness restricted to requested identifiers

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.common.certification_view.derive_repository_certification_state:
      why:
        constructs: "Builds the signed repository snapshot whose graph and hashes define exact-node currentness."
    officina.common.certification_view.CertificateCurrentnessReport:
      why:
        constructs: "Builds the currentness view restricted to the exact requested node identifiers."
    ._V4DerivedState:
      why:
        constructs: "Builds the reusable graph, hash, basis, and currentness bundle for the exact scope."
    .DriftCheckError:
      why:
        raises: "Reports derivation failures and requested identifiers absent from the canonical graph."
    """
    root = Path(repo_root).resolve()
    try:
        derived = derive_repository_certification_state(
            root,
            public_key_root=public_key_root,
            expected_schema_version=expected_schema_version,
            schema_root=(
                root / "references" / "blueprint"
                if expected_schema_version == 4
                else None
            ),
            allow_non_atomic=allow_non_atomic,
        )
    except (CertificationHashError, RepositoryCertificationError, OSError, ValueError) as exc:
        raise DriftCheckError(str(exc)) from exc
    unknown = sorted(set(target_node_ids) - set(derived.graph.nodes))
    if unknown:
        raise DriftCheckError(
            "unknown exact drift target: " + ", ".join(unknown)
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
    expected_schema_version: int = 5,
    allow_non_atomic: bool = False,
) -> CertificateCurrentnessReport:
    """Return public-key-only currentness for exact node IDs.

    Intent
    ------
    Expose only the restricted currentness component of one exact-node
    certification-state derivation.

    Rationale
    ---------
    Compatibility callers need a currentness-only facade without gaining access
    to the broader derived graph and hash state.

    Pseudocode
    ----------
    - set exact_state = _derive_v4_repository_state for requested identifiers
    - return exact_state currentness

    Wraps
    -----
    - ._derive_v4_repository_state -> preprocess: forwards the exact repository, node identifiers, key root, schema, and atomicity choice; postprocess: selects only currentness; fixed_arguments: none
    """

    return _derive_v4_repository_state(
        repo_root,
        target_node_ids,
        public_key_root=public_key_root,
        expected_schema_version=expected_schema_version,
        allow_non_atomic=allow_non_atomic,
    ).currentness


@dataclass(frozen=True)
class NodeDriftStatus:
    """Represent signed currentness and evidence location for one graph node.

    Intent
    ------
    Keep a node's exact identifier, currentness verdict, concerns, and
    certificate-log path together for rendering and serialization.

    Rationale
    ---------
    A typed record prevents module-level aggregation from losing the node that
    owns each concern or the certificate history that supports the verdict.

    Pseudocode
    ----------
    - set node_status = identifier currentness concerns and certificate path

    Wraps
    -----
    - none
    """
    node_id: str
    current: bool
    concerns: tuple[str, ...]
    certificate_path: Path

    def as_payload(self) -> dict[str, Any]:
        """Serialize one node verdict using the stable status vocabulary.

        Intent
        ------
        Convert the immutable node record into JSON-compatible identifiers,
        status text, concerns, and a POSIX-form certificate path.

        Rationale
        ---------
        Centralizing the current-versus-stale label and path conversion keeps
        every machine-readable module report consistent across platforms.

        Pseudocode
        ----------
        - set status_label = currentness label
        - return node identifier status concerns and portable certificate path

        Wraps
        -----
        - none
        """
        return {
            "node_id": self.node_id,
            "status": "certificate-current" if self.current else "certificate-stale",
            "concerns": list(self.concerns),
            "certificate_path": self.certificate_path.as_posix(),
        }


@dataclass(frozen=True)
class ModuleDriftReport:
    """Aggregate supplied node verdicts for one module at one discovered source.

    Intent
    ------
    Bind source and filesystem identity to the supplied signed node verdicts for
    a selected module.

    Rationale
    ---------
    Installed copies can share a module name, so reports must retain their
    package and skills roots rather than collapsing results by name alone.

    Pseudocode
    ----------
    - set module_report = source roots module name and exact node verdicts

    Wraps
    -----
    - none
    """
    skill: str
    source: str
    package_root: Path
    skills_root: Path
    nodes: tuple[NodeDriftStatus, ...]

    @property
    def current(self) -> bool:
        """Require a nonempty supplied node set whose verdicts are all current.

        Intent
        ------
        Compute a module-level verdict that is true only when the supplied node
        tuple is nonempty and every supplied signed verdict is current.

        Rationale
        ---------
        Treating an empty selection as stale prevents vacuous success when graph
        ownership resolution yields no evidence.

        Pseudocode
        ----------
        - return nodes is nonempty and every node is current

        Wraps
        -----
        - none
        """
        return bool(self.nodes) and all(node.current for node in self.nodes)

    def as_payload(self) -> dict[str, Any]:
        """Serialize one module report and its ordered node evidence.

        Intent
        ------
        Emit JSON-compatible source identity, roots, aggregate status, and each
        node's serialized currentness evidence.

        Rationale
        ---------
        The module envelope preserves installed-copy provenance while the node
        payloads retain exact certificate concerns for downstream consumers.

        Pseudocode
        ----------
        - set module_status = aggregate currentness label
        - set node_payloads = serialized node verdicts in stored order
        - return module identity roots status and node_payloads

        Wraps
        -----
        - none
        """
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
    """Carry canonical hashes for one module at one installed source.

    Intent
    ------
    Associate a module's certification-basis and node hashes with the source and
    filesystem roots from which they were derived.

    Rationale
    ---------
    Hash reports from multiple installed copies remain distinguishable only when
    their package and skills roots travel with the canonical hash payload.

    Pseudocode
    ----------
    - set hash_report = module source roots and canonical hashes

    Wraps
    -----
    - none
    """
    skill: str
    source: str
    package_root: Path
    skills_root: Path
    hashes: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        """Serialize one source-qualified module hash report.

        Intent
        ------
        Return JSON-compatible module identity, discovery source, filesystem
        roots, and the already canonical hash mapping.

        Rationale
        ---------
        A single conversion point keeps status and hash CLI envelopes aligned on
        source labels and portable path formatting.

        Pseudocode
        ----------
        - return module source portable roots and canonical hashes

        Wraps
        -----
        - none
        """
        return {
            "skill": self.skill,
            "source": self.source,
            "package_root": self.package_root.as_posix(),
            "skills_root": self.skills_root.as_posix(),
            "hashes": self.hashes,
        }


@dataclass(frozen=True)
class SkillHashFailure:
    """Carry one module-scoped hash failure without aborting sibling targets.

    Intent
    ------
    Preserve the failed module's source identity, roots, and diagnostic for a
    partial compute-hashes response.

    Rationale
    ---------
    Per-target failure records let successful installed modules retain their
    hashes while the process still exits nonzero for incomplete work.

    Pseudocode
    ----------
    - set hash_failure = module source roots and diagnostic

    Wraps
    -----
    - none
    """
    skill: str
    source: str
    package_root: Path
    skills_root: Path
    message: str

    def as_payload(self) -> dict[str, str]:
        """Serialize one source-qualified hash failure.

        Intent
        ------
        Return the failed module, discovery source, portable roots, and exact
        derivation diagnostic as JSON-compatible strings.

        Rationale
        ---------
        Stable failure fields allow callers to pair partial results with precise
        remediation targets without parsing rendered Markdown.

        Pseudocode
        ----------
        - return module source portable roots and diagnostic message

        Wraps
        -----
        - none
        """
        return {
            "skill": self.skill,
            "source": self.source,
            "package_root": self.package_root.as_posix(),
            "skills_root": self.skills_root.as_posix(),
            "message": self.message,
        }


@dataclass(frozen=True)
class RequestedScope:
    """Carry one installed source and the exact module names selected there."""

    source: SkillSource
    skill_names: tuple[str, ...]


def observed_skill_names(skills_root: Path) -> list[str]:
    """List direct child directories that expose a discoverable skill marker.

    Intent
    ------
    Return lexically ordered names for immediate skills-root children containing
    a regular-looking ``SKILL.md`` marker.

    Rationale
    ---------
    Discovery is intentionally shallow so nested caches and implementation
    children cannot become modules merely because they contain skill-like files.

    Pseudocode
    ----------
    - if skills_root is not a directory:
      - return empty list
    - set names = direct directories containing SKILL.md
    - return names sorted lexically

    Wraps
    -----
    - none
    """
    if not skills_root.is_dir():
        return []
    return sorted(
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )


def blueprint_skill_names(skills_root: Path) -> list[str]:
    """Filter observed names to direct roots with a blueprint file marker.

    Intent
    ------
    Return ordered observed module names whose direct skill roots contain a
    ``blueprint.yaml`` file marker.

    Rationale
    ---------
    No-target hash candidate discovery uses this direct file marker as its only
    extra filter; graph loading and validation occur later.

    Pseudocode
    ----------
    - set observed_names = observed_skill_names for skills_root
    - return names whose skill root contains blueprint.yaml sorted lexically

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .observed_skill_names:
      why:
        reads: "Enumerates the shallow discoverable candidates before blueprint-marker filtering."
    """
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
    """Derive source roots from a resolved path shaped as a skills child.

    Intent
    ------
    Require only that the resolved path's parent is named ``skills``, then
    derive the enclosing package and skills roots.

    Rationale
    ---------
    This is a path-shape boundary only; it does not establish path existence,
    directory kind, a skill marker, or membership in a repository graph.

    Pseudocode
    ----------
    - set module_root = resolved requested path
    - if parent directory is not named skills:
      - raise DriftCheckError with the exact-root requirement
    - return SkillSource from package root and skills root

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .DriftCheckError:
      why:
        raises: "Rejects explicit paths that are not direct module roots below a skills directory."
    "._skill_sources.SkillSource [implicit]":
      why:
        constructs: "Builds the package and skills-root identity for the exact module path."
    """
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
    """Resolve override or authoritative installed sources for one invocation.

    Intent
    ------
    Prefer explicit skills or repository roots, otherwise discover active host
    sources and reject plugin installations without a usable canonical graph.

    Rationale
    ---------
    Installed-plugin registries are authoritative; accepting stale cache
    directories or malformed active versions could report hashes for code that
    the host does not actually expose.

    Pseudocode
    ----------
    - if explicit skills root is supplied:
      - return one override SkillSource
    - if explicit repository root differs from default:
      - return one repository override SkillSource
    - set sources = authoritative observed skill sources
    - for source in sources:
      - set graph = canonical blueprint graph for plugin package
      - if graph has no nodes or cannot load:
        - raise DriftCheckError with registry remediation
    - return sources

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.common.blueprint_graph.load_repository_blueprint_graph:
      why:
        constructs: "Builds each active plugin graph whose nonempty canonical node set proves the installation is supported."
    "._skill_sources.SkillSource [implicit]":
      why:
        constructs: "Builds explicit source records when repository or skills-root overrides bypass host discovery."
    "._skill_sources.observed_skill_sources [implicit]":
      why:
        constructs: "Builds the authoritative direct-skill and active-plugin source set used when no override is supplied."
    .DriftCheckError:
      why:
        raises: "Translates discovery errors and unsupported active-plugin graphs into actionable drift-check diagnostics."
    """
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
    """Map explicit paths, names, or no-target discovery into source scopes.

    Intent
    ------
    Resolve every CLI target to exact module names grouped with the installed or
    overridden source against which each name must be checked.

    Rationale
    ---------
    Separating path-like requests from names preserves exact filesystem intent
    while allowing one installed name to resolve independently across sources.

    Pseudocode
    ----------
    - if exact skill root is supplied:
      - return one RequestedScope for that module root
    - set target_groups = path scopes and plain names
    - if every supplied target is path-like:
      - return path scopes
    - for source in requested skill sources:
      - set selected_names = plain names or command-specific discovered names
      - set scopes = scopes plus RequestedScope for selected_names
    - return all scopes as a tuple

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .requested_skill_sources:
      why:
        reads: "Resolves the installed or overridden sources against which plain names and no-target scans are evaluated."

    InstantiationsFromRepo
    ----------------------
    .source_for_skill_root:
      why:
        constructs: "Builds source identity for each exact module-root request."
    .RequestedScope:
      why:
        constructs: "Builds each pairing of one source with its exact selected module names."
    .observed_skill_names:
      why:
        constructs: "Builds the discovered module-name collection carried into a no-target status scope."
    .blueprint_skill_names:
      why:
        constructs: "Builds the blueprint-backed name collection carried into a no-target hash scope."
    """
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
    """Select one module node and only its directly declared source nodes.

    Intent
    ------
    Return the exact global module identifier together with its direct behavioral
    sources, or an empty tuple when the identifier is absent or not a module.

    Rationale
    ---------
    Prefix matching would conflate parent modules, implementation children, and
    similarly named neighbors, so selection follows explicit graph ownership.

    Pseudocode
    ----------
    - set module = graph node at exact module_id
    - if module is missing or is not a module:
      - return empty tuple
    - return sorted unique module_id and direct module source identifiers

    Wraps
    -----
    - none
    """
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
    expected_schema_version: int = 5,
) -> _V4DerivedState:
    """Derive canonical graph, hashes, basis, and currentness for one source.

    Intent
    ------
    Read one package's certification state using its certifier-owned public-key
    root and the requested blueprint schema version.

    Rationale
    ---------
    Status and hash routes must consume the same canonical snapshot so their
    node selection and certification basis cannot disagree for one source.

    Pseudocode
    ----------
    - set public_key_root = certifier-owned key location for source package
    - set derived_state = canonical certification state for source package
    - if derivation fails:
      - raise DriftCheckError with the original diagnostic
    - return _V4DerivedState with graph hashes basis and currentness

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.common.certificate_records.certificate_public_key_root:
      why:
        constructs: "Builds the certifier-owned public-key location used to verify retained certificate histories."
    officina.common.certification_view.derive_repository_certification_state:
      why:
        constructs: "Builds the canonical source snapshot shared by status and hash reporting."
    ._V4DerivedState:
      why:
        constructs: "Builds the reusable source derivation containing graph, hashes, basis, and signed currentness."
    .DriftCheckError:
      why:
        raises: "Translates repository, certificate, hashing, and filesystem failures into the checker error domain."
    """
    try:
        derived = derive_repository_certification_state(
            source.package_root,
            public_key_root=certificate_public_key_root(source.package_root),
            expected_schema_version=expected_schema_version,
            schema_root=(
                source.package_root / "references" / "blueprint"
                if expected_schema_version == 4
                else None
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
    expected_schema_version: int = 5,
) -> list[ModuleDriftReport]:
    """Build signed currentness reports for every exact requested module.

    Intent
    ------
    Derive each source once, select the exact nodes owned by each requested
    module, and attach signed concerns plus certificate-log locations.

    Rationale
    ---------
    Per-source caching preserves one snapshot across sibling modules, while
    fail-closed missing-name checks prevent partial status output from appearing
    complete.

    Pseudocode
    ----------
    - set requested_names = selected module names
    - set reports = empty report list and per-source cache
    - for scope in scopes:
      - for skill_name in scope module names:
        - if schema version is four and SKILL.md marker is absent:
          - continue
        - set derived_state = cached canonical source state
        - set node_ids = exact module and direct sources
        - if node_ids is empty:
          - if schema version is five:
            - continue
          - raise DriftCheckError(no owned nodes)
        - set reports = reports plus module and node currentness records
    - if requested_names contains an unfound name:
      - raise DriftCheckError(missing_names)
    - return reports

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._derive_for_source:
      why:
        constructs: "Builds the one canonical certification snapshot cached for every module at a source."
    ._module_node_ids:
      why:
        constructs: "Builds the exact owned-node selection for each requested global module identifier."
    officina.common.certification_view.certificate_log_path:
      why:
        constructs: "Builds the retained certificate-history path attached to each exact node verdict."
    .NodeDriftStatus:
      why:
        constructs: "Builds each node-level currentness, concern, and certificate-location record."
    .ModuleDriftReport:
      why:
        constructs: "Builds each source-qualified module report from its exact node records."
    .DriftCheckError:
      why:
        raises: "Rejects modules with no owned nodes and names absent from all selected sources."
    """
    reports: list[ModuleDriftReport] = []
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
            if key not in cache:
                cache[key] = _derive_for_source(
                    scope.source,
                    expected_schema_version=expected_schema_version,
                )
            derived = cache[key]
            node_ids = _module_node_ids(derived.graph, skill_name)
            if expected_schema_version == 5 and not node_ids:
                continue
            found.add(skill_name)
            if not node_ids:
                raise DriftCheckError(f"{skill_name}: module owns no nodes")
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
    *,
    expected_schema_version: int = 5,
) -> tuple[list[SkillHashReport], list[SkillHashFailure]]:
    """Build node-hash reports while collecting drift-domain module failures.

    Intent
    ------
    Derive each source once, serialize exact owned-node hashes for successful
    modules, and provisionally record module-level ``DriftCheckError`` failures.

    Rationale
    ---------
    Derivation, node selection, or report assembly can fail for one module while
    siblings proceed. A final missing-name error aborts instead of returning the
    provisional success and failure collections.

    Pseudocode
    ----------
    - set requested_names = selected names
    - set outputs = reports provisional failures and source cache
    - for scope in scopes:
      - for skill_name in scope:
        - if v4 SKILL.md marker is absent:
          - continue
        - set derived_state = cached source state
        - set node_ids = exact module nodes or v4 ownership error
        - if v5 node_ids is empty:
          - continue
        - set reports = reports plus basis and node hashes
        - if module work raises DriftCheckError:
          - set provisional_failures = failures plus diagnostic
    - if a requested name is unfound:
      - raise DriftCheckError(missing_names)
    - return reports and provisional failures

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._derive_for_source:
      why:
        constructs: "Builds the one canonical graph and hash snapshot cached for every module at a source."
    ._module_node_ids:
      why:
        constructs: "Builds the exact owned-node selection serialized for each requested module."
    .SkillHashReport:
      why:
        constructs: "Builds each successful source-qualified certification-basis and node-hash report."
    .SkillHashFailure:
      why:
        constructs: "Records each caught module-level drift diagnostic provisionally before the final missing-name check."
    .DriftCheckError:
      why:
        raises: "Rejects modules with no owned nodes and names absent from all selected sources."
    """
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
                if expected_schema_version == 5 and not node_ids:
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


def build_payload(reports: Sequence[ModuleDriftReport]) -> dict[str, Any]:
    """Build the versioned JSON envelope for module currentness reports.

    Intent
    ------
    Count current and stale modules and serialize every source-qualified report
    in caller-provided order.

    Rationale
    ---------
    A stable schema version and explicit summary let machine consumers validate
    the response without reimplementing aggregate-status rules.

    Pseudocode
    ----------
    - set current_count = number of current module reports
    - set stale_count = report count minus current_count
    - set serialized_reports = each report payload in input order
    - return schema version summary and serialized_reports

    Wraps
    -----
    - none
    """
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
    """Build the versioned JSON envelope for hash results and failures.

    Intent
    ------
    Serialize successful module hashes and recoverable module failures into one
    machine-readable response.

    Rationale
    ---------
    Keeping both collections in a stable envelope lets callers consume partial
    success while still treating the process's nonzero exit as incomplete work.

    Pseudocode
    ----------
    - set serialized_reports = each hash report payload in input order
    - set serialized_failures = each hash failure payload in input order
    - return schema version serialized_reports and serialized_failures

    Wraps
    -----
    - none
    """
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "skills": [report.as_payload() for report in reports],
        "failures": [failure.as_payload() for failure in failures],
    }


def render_text(reports: Sequence[ModuleDriftReport]) -> str:
    """Render module currentness and exact concerns as a Markdown table.

    Intent
    ------
    Produce a human-readable row for each source-qualified module with aggregate
    status and concern text grouped by exact node identifier.

    Rationale
    ---------
    Node-qualified concern strings preserve actionable certificate evidence in a
    compact report that can be printed and saved unchanged.

    Pseudocode
    ----------
    - set lines = report heading and table header
    - for report in reports:
      - set concerns = nonempty node concerns labeled by identifier
      - set status = current or stale from aggregate verdict
      - set lines = lines plus source module status and concerns row
    - return newline-terminated Markdown

    Wraps
    -----
    - none
    """
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
    """Render canonical hashes and per-module failures as Markdown text.

    Intent
    ------
    Emit one JSON-formatted hash section per successful module followed by
    stable error lines for recoverable failures.

    Rationale
    ---------
    Embedding sorted JSON retains canonical hash structure while Markdown
    headings keep multi-module terminal output readable.

    Pseudocode
    ----------
    - set lines = hash report heading
    - for report in reports:
      - set lines = lines plus module heading and sorted indented hashes
    - for failure in failures:
      - set lines = lines plus module-labeled error line
    - return newline-terminated Markdown

    Wraps
    -----
    - none
    """
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
    """Persist a timestamped human-readable drift report under skill build state.

    Intent
    ------
    Ensure the derived-report directory exists, choose a second-resolution local
    timestamp, write UTF-8 Markdown, and return the written path.

    Rationale
    ---------
    Keeping optional human output below the owning skill separates disposable
    derived reports from authoritative certificate state and source files.

    Pseudocode
    ----------
    - set build_directory = existing or newly created report directory
    - set timestamp = supplied time or current local time
    - set report_path = build directory plus timestamped filename
    - set persisted_report = markdown written to report_path as UTF-8
    - return report_path

    Wraps
    -----
    - none
    """
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = BUILD_DIR / f"certificate-drift-{timestamp}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    """Construct the two-route command-line grammar for drift inspection.

    Intent
    ------
    Define status and compute-hashes subcommands with their shared targets,
    machine-output flag, and exact-source overrides.

    Rationale
    ---------
    One parser factory keeps direct execution and dispatcher-bound invocation on
    the same accepted arguments and hidden compatibility options.

    Pseudocode
    ----------
    - set parser = read-only drift checker argument parser
    - set status_parser = status targets all json and source overrides
    - set hash_parser = compute-hashes targets json and source overrides
    - return parser

    Wraps
    -----
    - none
    """
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
    """Execute status or hash reporting with stable output and exit semantics.

    Intent
    ------
    Let argparse raise ``SystemExit(2)`` for parse errors, manually return two
    for the two post-parse argument checks, and return two for caught
    drift-domain failures or retained per-module hash failures.

    Rationale
    ---------
    Argparse owns syntax failures. Manual returns cover only a missing subcommand
    and status names combined with ``--all``; the guarded route maps caught
    ``DriftCheckError`` and returned ``SkillHashFailure`` records. Uncaught output
    and filesystem exceptions keep their normal process behavior.

    Pseudocode
    ----------
    - set arguments = argparse parsing of supplied or process argv
    - if command is missing or status combines names with all:
      - return two
    - set scopes = requested_scopes
    - if scopes is empty:
      - raise DriftCheckError(no installed sources)
    - if command is status:
      - set reports = reports_for_scopes
      - set status_output = JSON or rendered and saved Markdown
      - return zero
    - set hash_results = reports and failures from hash scopes
    - set hash_output = JSON or rendered hash text
    - if a drift operation raises DriftCheckError:
      - return two
    - return two if retained hash failures else zero

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .build_parser:
      why:
        parses: "Creates the shared command grammar and immediately parses direct or dispatcher-provided arguments."
    .build_payload:
      why:
        serializes: "Creates the versioned status envelope immediately serialized for machine output."
    .build_hash_payload:
      why:
        serializes: "Creates the versioned hash envelope immediately serialized for machine output."
    .render_hash_text:
      why:
        serializes: "Formats hash results and failures immediately written to standard output."

    InstantiationsFromRepo
    ----------------------
    .requested_scopes:
      why:
        constructs: "Builds exact source and module selections from validated command arguments."
    .reports_for_scopes:
      why:
        constructs: "Builds signed module currentness reports for the status route."
    .render_text:
      why:
        constructs: "Builds the human-readable status report printed and optionally saved."
    .write_markdown_report:
      why:
        constructs: "Builds the timestamped derived-report path after persisting human status output."
    .hash_reports_for_scopes:
      why:
        constructs: "Builds successful canonical hashes and recoverable target failures for the hash route."
    .DriftCheckError:
      why:
        raises: "Represents an empty resolved scope before the shared error boundary maps it to stderr and exit code two."
    """
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
    """Adapt dispatcher argv execution to the certificate drift command.

    Intent
    ------
    Expose the module's existing argv contract through the shared Python machine
    interface expected by dispatcher process bindings.

    Rationale
    ---------
    A minimal adapter avoids a second parser or route implementation, keeping
    dispatcher execution behavior identical to direct command execution.

    Pseudocode
    ----------
    - set interface = dispatcher-compatible argv adapter

    Wraps
    -----
    - none
    """

    def run(self, argv: list[str]) -> int:
        """Forward dispatcher-provided arguments to the canonical command entry.

        Intent
        ------
        Return the exact exit code produced by the module's shared argv command
        implementation.

        Rationale
        ---------
        Direct delegation prevents the process interface from drifting from CLI
        validation, output, report-writing, and error semantics.

        Pseudocode
        ----------
        - return main for dispatcher-provided argv

        Wraps
        -----
        - .main -> preprocess: forwards dispatcher argv unchanged; postprocess: returns the command exit code unchanged; fixed_arguments: none
        """
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
