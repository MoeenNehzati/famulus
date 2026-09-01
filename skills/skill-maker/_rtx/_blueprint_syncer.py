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
LEGACY_CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
LEGACY_CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"
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
USAGE_TOKEN = re.compile(r"<[^>]+>(?:\s*\.\.\.)?|\{[^}]+\}|--?[\w-]+|(?!\|--?)[^\s\[\](){}]+(?:\[[^\]\s]*\])?(?:\s*\.\.\.)?|[\[\](){}|]")


def _usage_label(value: str) -> str:
    value = re.sub(r"\s+\.\.\.$", "...", value)
    suffix = "..." if value.endswith("...") else ""
    value = value.removesuffix(suffix)
    if len(value) > 1 and (value[0], value[-1]) in {("<", ">"), ("{", "}")}:
        value = value[1:-1].replace(",", "|")
    return value + suffix

def _usage_projection(usage: object, pattern: dict, patterns: list[dict]) -> tuple[list[str], dict]:
    note = pattern.get("notes")
    quoted = re.search(r"`([^`]+)`", note) if isinstance(note, str) else None
    source = quoted.group(1) if quoted else usage.strip() if isinstance(usage, str) else ""
    required = set(pattern.get("required_flags", ()))
    allowed = set(pattern.get("allowed_flags", ())) | required
    known = set().union(*(set(item.get("allowed_flags", ())) | set(item.get("required_flags", ())) for item in patterns))
    value_flags = set().union(*(set(item.get("flag_patterns", {})) for item in patterns))
    argument_options = {item["name"] for item in pattern.get("arguments", {}).values() if item.get("kind") == "option"}
    known |= argument_options
    allowed |= argument_options
    value_flags |= argument_options
    tokens = USAGE_TOKEN.findall(source)
    if "".join("".join(tokens).split()) != "".join(source.split()):
        raise ValueError("usage cannot be projected unambiguously: incomplete tokenization")
    present = set(tokens) & known
    aliases = {}
    for missing in required - present:
        candidates = {old for old in present - allowed if any(
            allowed ^ set(other.get("allowed_flags", ())) == {old, missing}
            and required ^ set(other.get("required_flags", ())) == {old, missing}
            and missing in set(other.get("forbidden_flags", ())) for other in patterns
        )}
        if len(candidates) != 1:
            raise ValueError("usage cannot be projected unambiguously: ambiguous option alias")
        aliases[candidates.pop()] = missing
    option_indexes = [index for index, token in enumerate(tokens) if token in known]
    options = {
        aliases.get(tokens[index], tokens[index]): (
            _usage_label(tokens[index + 1]) if tokens[index] in value_flags else True
        )
        for index in option_indexes
        if aliases.get(tokens[index], tokens[index]) in allowed
    }
    if required - set(options):
        raise ValueError(f"usage cannot be projected unambiguously: missing option label {sorted(required - set(options))} in {source!r}")
    consumed = set(option_indexes) | {index + 1 for index in option_indexes if tokens[index] in value_flags}
    atoms = [token for index, token in enumerate(tokens) if index not in consumed and token not in "[](){}|"]
    if pattern.get("allow_stdin") and atoms[-2:-1] == ["<"]:
        atoms = atoms[:-2]
    positionals = [_usage_label(atom) for atom in atoms]
    positional_names = {
        item["position"]: name
        for name, item in pattern.get("arguments", {}).items()
        if item.get("kind") == "positional" and isinstance(item.get("position"), int)
    }
    maximum = pattern.get("max_positionals", len(positionals))
    selected = positionals if pattern.get("allow_extra_positionals") else positionals[:maximum]
    selected = [positional_names.get(index, value) if "|" in value else value for index, value in enumerate(selected)]
    for index, validator in pattern.get("positional_patterns", {}).items():
        choices = selected[int(index)].split("|") if int(index) < len(selected) else []
        matching = [choice for choice in choices if re.fullmatch(validator, choice)]
        selected[int(index)] = "|".join(matching) if matching else selected[int(index)]
    too_many = "max_positionals" in pattern and not pattern.get("allow_extra_positionals") and len(positionals) > max(item.get("max_positionals", 0) for item in patterns)
    if len(selected) < pattern.get("min_positionals", 0) or too_many:
        raise ValueError("usage cannot be projected unambiguously: positional labels")
    return selected, options


@dataclass(frozen=True)
class ModuleBlueprint:
    name: str
    path: Path
    data: dict[str, Any]
    repository_graph: RepositoryBlueprintGraph


class BlueprintError(Exception):
    """Raised when a blueprint is invalid."""


def load_blueprints(
    *,
    schema_root: Path | None = None,
) -> dict[str, ModuleBlueprint]:
    selected_schema_root = (
        schema_root
        if schema_root is not None
        else BLUEPRINT_SCHEMA_ROOT
    )
    try:
        graph = load_repository_blueprint_graph(
            SKILLS_ROOT.parent,
            schema_root=selected_schema_root,
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
            "provided repository graph must use schema version 6: "
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


def generated_setup_gate(
    module_id: str,
    repository_graph: RepositoryBlueprintGraph,
) -> list[str]:
    """Render the managed-setup protocol for one opted-in Markdown gateway."""

    gateway = _host_gateway_source(module_id, repository_graph)
    gateway_declaration = getattr(gateway, "declaration", None)
    if not isinstance(gateway_declaration, Mapping):
        return []
    gateway_spec = gateway_declaration.get("gateway")
    if not isinstance(gateway_spec, Mapping) or gateway_spec.get("language") != "Markdown":
        return []

    managed_setups = getattr(repository_graph, "managed_setups", {})
    if not isinstance(managed_setups, Mapping):
        return []
    managed_entries = []
    for setup_interface, managed in sorted(managed_setups.items()):
        setup_export = repository_graph.exports.get(setup_interface)
        if (
            setup_export is not None
            and setup_export.module_node_id == module_id
            and managed.kind == "markdown"
        ):
            managed_entries.append(managed)
    if not managed_entries:
        return []

    lines = [
        "### Managed setup gate",
        "",
        "Activate this gate only for an invocation of this skill's interfaces or an exact managed lifecycle entry below. Generic setup prose does not activate this gate.",
        "Keep the original caller, interface, version, arguments, and stdin outside the ledger; the manager receives only its public continuation identity.",
        "",
        "Managed lifecycle entries:",
    ]
    for managed in managed_entries:
        lines.append(
            f"- Setup `{managed.setup_interface}@{managed.setup_version}` routes to "
            f"`begin(setup, {managed.setup_interface}, ORIGINAL_CALLER, "
            f"ORIGINAL_INTERFACE, ORIGINAL_VERSION)`; teardown "
            f"`{managed.teardown_interface}@{managed.teardown_version}` routes to "
            f"`begin(teardown, {managed.setup_interface}, ORIGINAL_CALLER, "
            f"ORIGINAL_INTERFACE, ORIGINAL_VERSION)`."
        )
    lines.extend([
        "",
        "For an ordinary invocation, use this exact sequence:",
        "",
        f"1. Call `{_MANAGER_STATUS}` for the original target interface. If it is `unmanaged`, run the original request normally. If it is `setup_busy`, follow only its recovery result.",
        "2. If it is `setup_required`, obtain permission, then call "
        f"`{_MANAGER_BEGIN}` as `begin(setup, ROOT_SETUP_INTERFACE, ORIGINAL_CALLER, "
        "ORIGINAL_INTERFACE, ORIGINAL_VERSION)`, where `ROOT_SETUP_INTERFACE` is the returned root setup interface.",
        f"3. Follow only the returned exact structured current step: call `{_MANAGER_RUN_MARKDOWN}` for a Markdown step, follow its returned instructions, then call `{_MANAGER_SETTLE}`; call `{_MANAGER_RUN_PYTHON}` for a Python step. Repeat until the flow is ready.",
        f"4. Perform the ready recheck with `{_MANAGER_STATUS}` for the original target and require `ready`; then call `{_MANAGER_AUTHORIZE}` with the original target plus caller, interface, and version.",
        "5. Retry the original request exactly once, with its original arguments and stdin, only when `authorize` returns `resume_original: true`.",
        "",
        "For an exact managed setup or teardown invocation, do not launch it directly; use its "
        f"listed `{_MANAGER_BEGIN}` route. "
        "A manager result that names an exact structured current step is the only bypass of this gate.",
    ])
    return lines


_MANAGER_STATUS = "setup-interface-manager._rtx.interface.status@1"
_MANAGER_AUTHORIZE = "setup-interface-manager._rtx.interface.authorize@1"
_MANAGER_BEGIN = "setup-interface-manager._rtx.interface.begin@1"
_MANAGER_RUN_MARKDOWN = "setup-interface-manager._rtx.interface.run-markdown@1"
_MANAGER_RUN_PYTHON = "setup-interface-manager._rtx.interface.run-python@1"
_MANAGER_SETTLE = "setup-interface-manager._rtx.interface.settle@1"


def generated_interface_block(
    module_id: str,
    repository_graph: RepositoryBlueprintGraph,
) -> str:
    process_exports = []
    instruction_exports = []
    gateway = _host_gateway_source(module_id, repository_graph)
    used_versions = {
        edge.target_id: edge.required_version
        for edge in repository_graph.node_edges
        if edge.source_id == gateway.node_id
        and edge.relation in {"uses-export", "uses-private-interface"}
    }
    for export_id, required_version in sorted(used_versions.items()):
        export = repository_graph.exports.get(export_id) or repository_graph.source_interfaces.get(export_id)
        if export is None:
            raise BlueprintError(f"{gateway.node_id}: unresolved interface {export_id}")
        spec = export.declaration
        description = spec.get("description")
        if not isinstance(description, str) or not description.strip():
            raise BlueprintError(f"{export_id}: description must be non-empty")
        if required_version != export.version:
            raise BlueprintError(f"{export_id}: use version does not match export")
        binding = spec.get("process_binding")
        if isinstance(binding, dict):
            process_exports.append(
                (
                    export_id,
                    description.strip()
                    if isinstance(description, str) and description.strip()
                    else None,
                    required_version,
                    spec.get("usage"),
                    binding,
                )
            )
        elif isinstance(description, str) and description.strip():
            instruction_exports.append((export_id, required_version, spec))

    lines = [
        INTERFACES_START,
        "> Generated from `blueprint.yaml`. Do not edit this block by hand.",
        "",
    ]
    setup_gate = generated_setup_gate(module_id, repository_graph)
    if setup_gate:
        lines.extend(setup_gate)
        lines.append("")
    if process_exports:
        lines.extend([
            "Executable Interfaces:",
            "",
            "Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.",
        ])
        for interface_name, description, version, usage, binding in process_exports:
            lines.extend([
                f"- `{interface_name}` — {description}",
                f"  - Caller: `{module_id}`",
                f"  - Version: {version}",
            ])
            patterns = binding.get("patterns", [binding])
            for pattern in patterns:
                required = set(pattern.get("required_flags", ()))
                try:
                    positionals, options = _usage_projection(
                        usage, pattern, patterns
                    )
                except ValueError as exc:
                    raise BlueprintError(f"{interface_name}: {exc}") from exc
                arguments = {"positionals": positionals, "options": options, "stdin": None}
                minimum = pattern.get("min_positionals", sum(item["arity"]["minimum"] for item in pattern.get("arguments", {}).values() if item.get("kind") == "positional"))
                maximum = "unbounded" if pattern.get("allow_extra_positionals") else pattern.get("max_positionals", sum(item["arity"]["maximum"] for item in pattern.get("arguments", {}).values() if item.get("kind") == "positional"))
                lines.extend([
                    f"  - Alternative: `{pattern.get('name', 'default')}`",
                    "    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.",
                    f"    {json.dumps(arguments, sort_keys=True)}",
                    f"    Required options: {json.dumps(sorted(required))}; positional arity: {minimum}..{maximum}; stdin: {'permitted' if pattern.get('allow_stdin') else 'forbidden'}",
                ])
        lines.append("")
    if instruction_exports:
        lines.extend([
            "Instruction Interfaces:",
            "",
            "These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.",
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
    if LEGACY_CONTRACT_START in text and LEGACY_CONTRACT_END in text:
        legacy_pattern = re.compile(
            rf"{re.escape(LEGACY_CONTRACT_START)}.*?{re.escape(LEGACY_CONTRACT_END)}\n?",
            re.DOTALL,
        )
        text = legacy_pattern.sub("", text, count=1)

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
