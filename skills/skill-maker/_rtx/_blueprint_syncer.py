#!/usr/bin/env python3
"""Validate and sync skill blueprints into generated artifacts.

Blueprints are hand-authored YAML files under ``skills/<name>/blueprint.yaml``.
This tool never rewrites blueprint files. It only validates them and syncs:

- ``references/blueprint-schema/runtime_dependencies.json``
- the generated owner-facing dispatcher interface block in ``SKILL.md``

The owner-facing dispatcher interface block is injected immediately after the
YAML frontmatter. If it already exists, it is replaced in place.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
from officina.runtime.python_machine_interface import PythonMachineInterface
from officina.runtime.python_machine_interface_runner import run_python_machine_interface
from officina.blueprints.graph import (
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)

SKILLS_ROOT = REPO_ROOT / "skills"
INTERFACES_START = "<!-- BEGIN BLUEPRINT INTERFACES -->"
INTERFACES_END = "<!-- END BLUEPRINT INTERFACES -->"
RUNTIME_DEPENDENCIES_PATH = REPO_ROOT / "references" / "blueprint-schema" / "runtime_dependencies.json"
BLUEPRINT_SCHEMA_ROOT = REPO_ROOT / "references" / "blueprint-schema"
PLATFORM_NAMES = ("linux", "macos", "windows")
RUNTIME_DEPENDENCY_KINDS = (
    "python-package",
    "binary",
    "system-service",
    "system-library",
    "external-application",
    "runtime",
    "model-data",
)
@dataclass(frozen=True)
class ModuleBlueprint:
    name: str
    path: Path
    data: dict[str, Any]
    repository_graph: RepositoryBlueprintGraph


class BlueprintError(Exception):
    """Raised when a blueprint is invalid."""


def load_blueprints() -> dict[str, ModuleBlueprint]:
    try:
        graph = load_repository_blueprint_graph(
            SKILLS_ROOT.parent,
            schema_root=BLUEPRINT_SCHEMA_ROOT,
            expected_schema_version=6,
        )
    except (OSError, ValueError) as exc:
        raise BlueprintError(str(exc)) from exc
    return blueprints_from_graph(
        graph,
        skills_root=SKILLS_ROOT,
    )


def blueprints_from_graph(
    repository_graph: RepositoryBlueprintGraph,
    *,
    skills_root: Path,
) -> dict[str, ModuleBlueprint]:
    """Select managed skill modules from an already validated graph."""

    if repository_graph.schema_version != 6:
        raise BlueprintError(
            "syncer requires schema version 6, got "
            f"{repository_graph.schema_version}"
        )
    blueprints: dict[str, ModuleBlueprint] = {}
    paths = sorted(skills_root.glob("*/blueprint.yaml"))
    modules_by_path = {
        node.blueprint_path.resolve(): node
        for node in repository_graph.nodes.values()
        if node.node_type == "module"
    }
    for path in paths:
        module = modules_by_path.get(path.resolve())
        if module is None:
            raise BlueprintError(f"{path}: repository graph has no matching module")
        module_id = module.node_id
        blueprints[module_id] = ModuleBlueprint(
            module_id,
            path,
            dict(module.declaration),
            repository_graph,
        )
    return blueprints


def generated_interface_block(
    module_id: str,
    repository_graph: RepositoryBlueprintGraph,
) -> str:
    process_exports = []
    instruction_exports = []
    gateway = _host_gateway_source(module_id, repository_graph)
    used_versions = {edge.target_id: edge.required_version for edge in repository_graph.node_edges if edge.source_id == gateway.node_id and edge.relation in {"uses-export", "uses-private-interface"}}
    for export_id, export in sorted((target_id, repository_graph.exports.get(target_id) or repository_graph.source_interfaces.get(target_id)) for target_id in used_versions):
        if export is None:
            raise BlueprintError(f"{gateway.node_id}: unresolved interface {export_id}")
        spec = export.declaration
        description = spec.get("description")
        if not isinstance(description, str) or not description.strip(): raise BlueprintError(f"{export_id}: description must be non-empty")
        required_version = used_versions[export_id]
        binding = spec.get("process_binding")
        if isinstance(binding, dict):
            patterns = binding.get("patterns") or []
            notes = [
                (pattern.get("name"), pattern.get("notes"))
                for pattern in patterns
                if isinstance(pattern, dict)
                and (pattern.get("name") or pattern.get("notes"))
            ]
            process_exports.append(
                (
                    export_id,
                    required_version,
                    description.strip()
                    if isinstance(description, str) and description.strip()
                    else None,
                    spec.get("usage") if isinstance(spec.get("usage"), str) else None,
                    notes,
                )
            )
        elif isinstance(description, str) and description.strip():
            instruction_exports.append((export_id, required_version, spec))

    lines = [
        INTERFACES_START,
        "> Generated from `blueprint.yaml`. Do not edit this block by hand.",
        "",
    ]
    if process_exports:
        lines.extend([
            "Dispatcher Interfaces:",
            "",
            "Use the installed `dispatcher` command for these process-bound interfaces:",
        ])
        for interface_name, required_version, description, usage, pattern_notes in process_exports:
            lines.append(f"- `{interface_name}@{required_version}` — {description}")
            args = f" {usage}" if usage else ("" if usage == "" else " ...")
            lines.append(
                f"  - `dispatcher --caller-skill {module_id} {interface_name}{args}`"
            )
            for pat_name, pat_notes in pattern_notes:
                if pat_name and pat_notes:
                    lines.append(f"  - {pat_name}: {pat_notes}")
                elif pat_notes:
                    lines.append(f"  - {pat_notes}")
        lines.append("")
    if instruction_exports:
        lines.extend([
            "Instruction Interfaces:",
            "",
            "These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:",
        ])
        for interface_name, required_version, spec in instruction_exports:
            lines.append(f"- `{interface_name}@{required_version}` — {spec['description'].strip()}")
    if not process_exports and not instruction_exports:
        lines.append("Used Interfaces: none")
    lines.extend([INTERFACES_END, ""])
    return "\n".join(lines)


def _host_gateway_source(
    module_id: str,
    repository_graph: RepositoryBlueprintGraph,
) -> Any:
    module = repository_graph.nodes[module_id]
    matches = tuple(
        repository_graph.nodes[source_id]
        for source_id in repository_graph.module_sources.get(module_id, ())
        if repository_graph.nodes[source_id].gateway_path == module.gateway_path
    )
    if len(matches) != 1:
        raise BlueprintError(
            f"{module_id}: expected exactly one host gateway source, found "
            f"{len(matches)}"
        )
    return matches[0]


def sync_interface_block(text: str, interface_block: str) -> str:
    """Inject, replace, or remove the generated owner-facing interface block."""
    if INTERFACES_START in text and INTERFACES_END in text:
        pattern = re.compile(
            rf"{re.escape(INTERFACES_START)}.*?{re.escape(INTERFACES_END)}\n?",
            re.DOTALL,
        )
        text = pattern.sub(lambda _: interface_block, text, count=1)
        return text

    if not interface_block:
        return text

    frontmatter_match = re.match(r"(---\n.*?\n---\n+)", text, re.DOTALL)
    if not frontmatter_match:
        raise BlueprintError("SKILL.md: missing YAML frontmatter for interface injection")
    updated = text[: frontmatter_match.end()] + interface_block + text[frontmatter_match.end() :]
    return re.sub(r"\n{3,}", "\n\n", updated)
def sync_module(blueprint: ModuleBlueprint, check_only: bool) -> list[str]:
    skill_dir = blueprint.path.parent
    errors: list[str] = []
    expected_skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    expected_skill = sync_interface_block(
        expected_skill,
        generated_interface_block(blueprint.name, blueprint.repository_graph),
    )

    skill_path = skill_dir / "SKILL.md"
    current_skill = skill_path.read_text(encoding="utf-8")
    if current_skill != expected_skill:
        if check_only:
            errors.append(f"{skill_path}: generated blueprint interface block is out of sync")
        else:
            skill_path.write_text(expected_skill, encoding="utf-8")

    return errors


def generated_runtime_dependencies_manifest(
    blueprints: dict[str, ModuleBlueprint],
) -> dict[str, Any]:
    """Build dependency manifest v2 from all executable owned interfaces.

    Canonical interface IDs are the keys.  Ownership, including descendant
    ownership, determines aggregation; namespace exposure is intentionally
    irrelevant because a private child process still needs its runtime
    dependencies installed.
    """
    skills: dict[str, Any] = {}
    all_dependencies: dict[str, set[str]] = {kind: set() for kind in RUNTIME_DEPENDENCY_KINDS}

    def module_installation_metadata(
        module_id: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Project the validated v6 selection metadata for one module."""
        if data.get("schema_version") != 6:
            return {}
        maturity = data.get("maturity")
        installation_tier = data.get("installation_tier")
        personal_preference = data.get("personal_preference")
        if maturity not in {"stable", "experimental"}:
            raise BlueprintError(f"{module_id}: invalid module maturity")
        if installation_tier not in {"core", "optional"}:
            raise BlueprintError(f"{module_id}: invalid installation tier")
        if not isinstance(personal_preference, dict):
            raise BlueprintError(f"{module_id}: personal preference must be a mapping")
        applies = personal_preference.get("applies")
        if not isinstance(applies, bool):
            raise BlueprintError(f"{module_id}: personal preference applies must be boolean")
        metadata: dict[str, Any] = {
            "maturity": maturity,
            "installation_tier": installation_tier,
            "personal_preference": {"applies": applies},
        }
        if applies:
            description = personal_preference.get("description")
            if not isinstance(description, str) or not description.strip():
                raise BlueprintError(
                    f"{module_id}: applicable personal preference needs a description"
                )
            metadata["personal_preference"]["description"] = description
        return metadata

    def reachable_runtime_dependencies(
        graph: RepositoryBlueprintGraph,
        source_node_id: str,
    ) -> list[dict[str, Any]]:
        runtime_relations = {
            "uses-source",
            "uses-private-interface",
            "uses-export",
        }
        interface_owners = {
            interface_id: node_id
            for node_id, node in graph.nodes.items()
            for interface_id in (
                node.declaration.get("interfaces", {})
                if isinstance(node.declaration.get("interfaces"), dict)
                else {}
            )
        }
        adjacency: dict[str, set[str]] = {}
        for edge in graph.node_edges:
            if edge.relation not in runtime_relations:
                continue
            target_node_id = edge.target_id
            if edge.relation == "uses-export":
                export = graph.exports.get(edge.target_id)
                target_node_id = (
                    export.source_node_id if export is not None else None
                )
            elif edge.relation == "uses-private-interface":
                target_node_id = interface_owners.get(edge.target_id)
            if not isinstance(target_node_id, str):
                raise BlueprintError(
                    f"{edge.source_id}: unresolved runtime dependency {edge.target_id}"
                )
            adjacency.setdefault(edge.source_id, set()).add(target_node_id)
        pending = [source_node_id]
        visited: set[str] = set()
        records: dict[str, dict[str, Any]] = {}
        while pending:
            node_id = pending.pop()
            if node_id in visited:
                continue
            visited.add(node_id)
            node = graph.nodes.get(node_id)
            if node is None:
                raise BlueprintError(
                    f"{source_node_id}: missing runtime dependency node {node_id}"
                )
            raw_dependencies = node.declaration.get("runtime_dependencies", [])
            if isinstance(raw_dependencies, list):
                for dependency in raw_dependencies:
                    if not isinstance(dependency, dict):
                        continue
                    key = json.dumps(
                        dependency,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    records[key] = dict(dependency)
            pending.extend(sorted(adjacency.get(node_id, ()), reverse=True))
        return [records[key] for key in sorted(records)]

    for skill_name, blueprint in sorted(blueprints.items()):
        generated_interfaces: dict[str, Any] = {}
        graph = blueprint.repository_graph
        interface_items = []
        for export_id, export in sorted(graph.exports.items()):
            ancestry = getattr(graph, "module_ancestry", {}).get(
                export.module_node_id, ()
            )
            if export.module_node_id != skill_name and (
                not ancestry or ancestry[0] != skill_name
            ):
                continue
            interface_spec = export.declaration
            source_node_id = export.source_node_id
            if not isinstance(interface_spec.get("process_binding"), dict):
                continue
            source = (
                graph.nodes.get(source_node_id)
                if source_node_id is not None
                else None
            )
            enriched = {
                **interface_spec,
                "dependencies": (
                    reachable_runtime_dependencies(graph, source.node_id)
                    if source is not None
                    else []
                ),
            }
            interface_items.append((export_id, enriched))

        for interface_id_value, interface_spec in interface_items:
            if not isinstance(interface_spec, dict):
                continue
            raw_dependencies = interface_spec.get("dependencies", [])
            dependencies: list[dict[str, str]] = []
            if isinstance(raw_dependencies, list):
                for entry in raw_dependencies:
                    if not isinstance(entry, dict):
                        continue
                    kind = entry.get("kind")
                    name = entry.get("name")
                    version = entry.get("version")
                    platforms = entry.get("platforms")
                    reason = entry.get("reason")
                    if (
                        kind not in all_dependencies
                        or not isinstance(name, str)
                        or not isinstance(version, str)
                        or not isinstance(platforms, dict)
                        or not isinstance(reason, str)
                    ):
                        continue
                    clean_platforms = {
                        platform: bool(platforms.get(platform))
                        for platform in PLATFORM_NAMES
                    }
                    dependencies.append(
                        {
                            "kind": kind,
                            "name": name,
                            "version": version,
                            "platforms": clean_platforms,
                            "reason": reason,
                        }
                    )
                    all_dependencies[kind].add(name)

            generated_interfaces[interface_id_value] = {"dependencies": dependencies}

        skills[skill_name] = {
            **module_installation_metadata(skill_name, blueprint.data),
            "interfaces": generated_interfaces,
        }

    return {
        "version": 2,
        "skills": skills,
        "all": {kind: sorted(all_dependencies[kind]) for kind in RUNTIME_DEPENDENCY_KINDS},
    }


def sync_runtime_dependencies_manifest(
    blueprints: dict[str, ModuleBlueprint],
    check_only: bool,
    *,
    runtime_dependencies_path: Path | None = None,
) -> list[str]:
    if runtime_dependencies_path is None:
        runtime_dependencies_path = RUNTIME_DEPENDENCIES_PATH
    expected = json.dumps(generated_runtime_dependencies_manifest(blueprints), indent=2) + "\n"
    current = (
        runtime_dependencies_path.read_text(encoding="utf-8")
        if runtime_dependencies_path.exists()
        else ""
    )
    if current == expected:
        return []
    if check_only:
        return [f"{runtime_dependencies_path}: out of sync with blueprint.yaml"]
    runtime_dependencies_path.write_text(expected, encoding="utf-8")
    return []


def validate_sync_state(
    *,
    repository_graph: RepositoryBlueprintGraph,
    repository_root: Path,
    skills_root: Path,
    runtime_dependencies_path: Path,
) -> list[str]:
    """Check generated blueprint state using one caller-prepared graph.

    This is intentionally read-only and does not load a graph.  The canonical
    validator supplies its per-item defensive graph after owning topology
    preflight; the standalone sync interface continues to load its own graph.
    """

    expected_skills_root = repository_root / "skills"
    if skills_root.resolve(strict=False) != expected_skills_root.resolve(strict=False):
        raise BlueprintError(
            f"skills root must be {expected_skills_root}, got {skills_root}"
        )
    errors: list[str] = []
    try:
        blueprints = blueprints_from_graph(
            repository_graph,
            skills_root=skills_root,
        )
        for blueprint in blueprints.values():
            errors.extend(sync_module(blueprint, check_only=True))
        errors.extend(
            sync_runtime_dependencies_manifest(
                blueprints,
                check_only=True,
                runtime_dependencies_path=runtime_dependencies_path,
            )
        )
    except BlueprintError as exc:
        errors.append(str(exc))
    return errors


class Interface(PythonMachineInterface):
    description = "Validate and sync skill blueprints."
    prog = "_blueprint_syncer.py"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument(
            "--check",
            action="store_true",
            help="Validate blueprints and fail if generated artifacts are out of sync.",
        )
        return parser

    def run(self, args: argparse.Namespace) -> int:
        return run_sync(check_only=args.check)


def run_sync(*, check_only: bool) -> int:
    try:
        blueprints = load_blueprints()
        errors: list[str] = []
        for blueprint in blueprints.values():
            errors.extend(sync_module(blueprint, check_only=check_only))
        errors.extend(
            sync_runtime_dependencies_manifest(
                blueprints,
                check_only=check_only,
            )
        )
    except BlueprintError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if errors:
        print("error: invalid or out-of-sync skill blueprints.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        if check_only:
            print(
                "Rerun this interface without `--check` to refresh generated artifacts.",
                file=sys.stderr,
            )
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run_python_machine_interface(Interface(), sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
