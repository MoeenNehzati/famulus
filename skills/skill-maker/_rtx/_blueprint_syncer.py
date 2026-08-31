#!/usr/bin/env python3
"""Validate and sync skill blueprints into generated artifacts.

Blueprints are hand-authored YAML files under ``skills/<name>/blueprint.yaml``.
This tool never rewrites blueprint files. It only validates them and syncs:

- ``references/blueprint-schema/runtime_dependencies.json``
- the generated contract block near the top of ``SKILL.md``
- the generated owner-facing dispatcher interface block in ``SKILL.md``

The contract block is injected immediately after the YAML frontmatter in
``SKILL.md``. The owner-facing dispatcher interface block is injected
immediately after the contract block. If a generated block already exists, it
is replaced in place.
"""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
from officina.runtime.python_machine_interface import PythonMachineInterface
from officina.runtime.python_machine_interface_runner import run_python_machine_interface
from officina.blueprints.graph import (
    InterfaceExport,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
    setup_order,
)
from officina.common.atomic_files import atomic_replace_bytes
from officina.certification.view import CertificationView
from officina.blueprints.projection import project_consumer_interfaces

SKILLS_ROOT = REPO_ROOT / "skills"
CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"
INTERFACES_START = "<!-- BEGIN BLUEPRINT INTERFACES -->"
INTERFACES_END = "<!-- END BLUEPRINT INTERFACES -->"
USED_INTERFACES_START = "<!-- BEGIN BLUEPRINT USED INTERFACES -->"
USED_INTERFACES_END = "<!-- END BLUEPRINT USED INTERFACES -->"
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


def module_discovery(data: dict[str, Any], context: str) -> dict[str, Any]:
    discovery = data.get("discovery")
    if not isinstance(discovery, dict):
        raise BlueprintError(f"{context}: `discovery` must be a mapping")
    catalog = discovery.get("catalog")
    if not isinstance(catalog, dict):
        raise BlueprintError(f"{context}: `discovery.catalog` must be a mapping")
    domain = catalog.get("domain")
    topics = catalog.get("topics")
    visibility = catalog.get("visibility")
    activated_by = discovery.get("activated_by")
    persistent_modifier = discovery.get("persistent_modifier")
    if not isinstance(domain, str) or not domain:
        raise BlueprintError(f"{context}: catalog domain must be non-empty")
    if not isinstance(topics, list) or not topics or not all(
        isinstance(topic, str) and topic for topic in topics
    ):
        raise BlueprintError(f"{context}: catalog topics must be non-empty strings")
    if not isinstance(visibility, str) or not visibility:
        raise BlueprintError(f"{context}: catalog visibility must be non-empty")
    if not isinstance(activated_by, list) or not activated_by or not all(
        isinstance(source, str) and source for source in activated_by
    ):
        raise BlueprintError(f"{context}: activated_by must be non-empty strings")
    if not isinstance(persistent_modifier, bool):
        raise BlueprintError(f"{context}: persistent_modifier must be boolean")
    return discovery


def load_blueprints(
    *,
    schema_version: int = 6,
    schema_root: Path | None = None,
) -> dict[str, ModuleBlueprint]:
    blueprints: dict[str, ModuleBlueprint] = {}
    paths = sorted(SKILLS_ROOT.glob("*/blueprint.yaml"))
    selected_schema_root = (
        schema_root
        if schema_root is not None
        else (
            BLUEPRINT_SCHEMA_ROOT
            if schema_version == 6
            else BLUEPRINT_SCHEMA_ROOT / "migrations" / f"v{schema_version}"
        )
    )
    try:
        graph = load_repository_blueprint_graph(
            SKILLS_ROOT.parent,
            schema_root=selected_schema_root,
            expected_schema_version=schema_version,
        )
    except (OSError, ValueError) as exc:
        raise BlueprintError(str(exc)) from exc
    modules_by_path = {
        node.blueprint_path.resolve(): node
        for node in graph.nodes.values()
        if node.node_type == "module"
    }
    for path in paths:
        module = modules_by_path.get(path.resolve())
        if module is None:
            raise BlueprintError(f"{path}: repository graph has no matching module")
        discovery = module.declaration.get("discovery")
        if schema_version == 5 and not (
            isinstance(discovery, dict)
            and discovery.get("mechanism") == "skill"
        ):
            continue
        module_id = module.node_id
        blueprints[module_id] = ModuleBlueprint(
            module_id,
            path,
            dict(module.declaration),
            graph,
        )
    return blueprints


def _generated_export_binding(
    repository_graph: RepositoryBlueprintGraph,
    export_id: str,
    export: InterfaceExport,
) -> tuple[Mapping[str, Any], str | None]:
    if getattr(repository_graph, "schema_version", 4) != 5:
        return export.declaration, export.source_node_id
    terminal_id = export.terminal_interface_id or export.interface_id
    terminal = repository_graph.exports.get(terminal_id)
    if terminal is None:
        raise BlueprintError(
            f"{export_id}: generated-view terminal export is unavailable"
        )
    return terminal.declaration, terminal.source_node_id


def generated_contract_block(
    module_id: str,
    data: dict[str, Any],
    repository_graph: RepositoryBlueprintGraph,
) -> str:
    discovery = module_discovery(data, "generated_contract_block")
    catalog = discovery["catalog"]
    uses: list[str] = []
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise BlueprintError(f"{module_id}: module version must be positive")
    for source_id in repository_graph.module_sources.get(module_id, ()):
        source = repository_graph.nodes[source_id]
        for entry in source.declaration.get("uses_interfaces", []) or []:
            if isinstance(entry, dict) and isinstance(entry.get("interface"), str):
                target = repository_graph.exports.get(entry["interface"])
                if (
                    repository_graph.schema_version == 5
                    and target is not None
                    and target.module_node_id == module_id
                ):
                    continue
                if (
                    repository_graph.schema_version == 5
                    and target is not None
                    and repository_graph.module_parents.get(
                        target.module_node_id
                    )
                    == module_id
                    and repository_graph.module_local_segments.get(
                        target.module_node_id
                    )
                    == "_rtx"
                ):
                    continue
                pinned = entry.get("version")
                suffix = f"@{pinned}" if isinstance(pinned, int) else ""
                uses.append(f"{source_id} -> {entry['interface']}{suffix}")
    exports = sorted(
        export_id
        for export_id, export in repository_graph.exports.items()
        if export.module_node_id == module_id
    )

    lines = [
        CONTRACT_START,
        "> Generated from `blueprint.yaml`. Do not edit this block by hand.",
        "",
    ]
    lines.extend(
        [
            "Catalog: "
            f"{catalog['domain']}; topics: {', '.join(catalog['topics'])}; "
            f"visibility: {catalog['visibility']}",
            "Activation: "
            f"{', '.join(discovery['activated_by'])}; persistent modifier: "
            f"{'yes' if discovery['persistent_modifier'] else 'no'}",
            "",
        ]
    )

    lines.append(f"Skill Version: {version}")
    lines.append("")

    if uses:
        lines.append("Uses Interfaces:")
        lines.extend(f"- `{name}`" for name in sorted(set(uses)))
    else:
        lines.append("Uses Interfaces: none")
    lines.append("")

    setup_exports = [
        export_id
        for export_id in exports
        if export_id in getattr(repository_graph, "setup_requirements", {})
    ]
    if setup_exports:
        setup_export = setup_exports[0]
        prerequisites = repository_graph.setup_requirements[setup_export]
        if prerequisites:
            lines.append("Setup Requires Setup Of:")
            lines.extend(
                f"- `{interface_id}@{required_version}`"
                for interface_id, required_version in prerequisites
            )
        else:
            lines.append("Setup Requires Setup Of: none")
        lines.append("Setup Order:")
        lines.extend(
            f"{index}. `{interface_id}`"
            for index, interface_id in enumerate(
                setup_order(repository_graph, setup_export), start=1
            )
        )
        lines.append("")

    if exports:
        lines.append("Public Interfaces:")
        for name in exports:
            lines.append(f"- `{name}`")
    else:
        lines.append("Public Interfaces: none")

    lines.extend([CONTRACT_END, ""])
    return "\n".join(lines)


def generated_interface_block(
    module_id: str,
    repository_graph: RepositoryBlueprintGraph,
) -> str:
    process_exports = []
    instruction_exports = []
    for export_id, export in sorted(repository_graph.exports.items()):
        if module_id not in repository_graph.module_ancestry[export.module_node_id] or any(candidate.interface_id != export_id and candidate.module_node_id == module_id and candidate.terminal_interface_id == export_id for candidate in repository_graph.exports.values()):
            continue
        spec, _source_id = _generated_export_binding(
            repository_graph,
            export_id,
            export,
        )
        description = spec.get("description")
        binding = spec.get("process_binding")
        if isinstance(binding, dict):
            process_exports.append(
                (
                    export_id,
                    description.strip()
                    if isinstance(description, str) and description.strip()
                    else None,
                    export.version,
                    spec.get("usage"),
                    binding,
                )
            )
        elif isinstance(description, str) and description.strip():
            instruction_exports.append((export_id, spec))
    gateway = _host_gateway_source(module_id, repository_graph)
    dependencies = {(edge.target_id, edge.required_version) for edge in repository_graph.node_edges if edge.source_id == gateway.node_id and edge.relation == "uses-source"}
    selected = {entry[0] for entry in process_exports + instruction_exports}
    direct_uses = sorted((edge for edge in repository_graph.node_edges if edge.source_id == gateway.node_id and edge.relation in {"uses-export", "uses-private-interface"}), key=lambda edge: edge.target_id)
    for edge in direct_uses:
        if edge.target_id in selected:
            continue
        export = repository_graph.exports.get(edge.target_id) or repository_graph.source_interfaces.get(edge.target_id)
        if export is None:
            raise BlueprintError(f"{gateway.node_id}: unresolved interface {edge.target_id}")
        spec, source_id = _generated_export_binding(repository_graph, edge.target_id, export)
        binding = spec.get("process_binding")
        if not isinstance(binding, dict):
            continue
        source = repository_graph.nodes.get(source_id) if source_id else None
        if edge.required_version != export.version:
            raise BlueprintError(f"{edge.target_id}: use version does not match export")
        if source is None or (source_id, source.version) not in dependencies:
            continue
        description = spec.get("description")
        if not isinstance(description, str) or not description.strip():
            raise BlueprintError(f"{edge.target_id}: description must be non-empty")
        process_exports.append((edge.target_id, description.strip(), export.version, spec.get("usage"), binding))
        selected.add(edge.target_id)
    if not process_exports and not instruction_exports:
        return ""

    lines = [
        INTERFACES_START,
        "> Generated from `blueprint.yaml`. Do not edit this block by hand.",
        "",
    ]
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
                positionals, options = _usage_projection(usage, pattern, patterns)
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
        for interface_name, spec in instruction_exports:
            lines.append(f"- `{interface_name}` — {spec['description'].strip()}")
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


def validate_gateway_declares_generated_dispatches(
    module_id: str,
    repository_graph: RepositoryBlueprintGraph,
) -> list[str]:
    """Ensure generated host dispatcher commands have matching source uses."""

    if getattr(repository_graph, "schema_version", 4) != 5:
        return []
    gateway = _host_gateway_source(module_id, repository_graph)
    raw_uses = gateway.declaration.get("uses_interfaces", [])
    if not isinstance(raw_uses, list):
        return [f"{gateway.node_id}: uses_interfaces must be a list"]
    declared = {
        (entry.get("interface"), entry.get("version"))
        for entry in raw_uses
        if isinstance(entry, Mapping)
    }
    missing: list[str] = []
    for export_id, export in sorted(repository_graph.exports.items()):
        if export.module_node_id != module_id:
            continue
        spec, _source_id = _generated_export_binding(
            repository_graph,
            export_id,
            export,
        )
        if not isinstance(spec.get("process_binding"), dict):
            continue
        if (export_id, export.version) not in declared:
            missing.append(f"{export_id}@{export.version}")
    if missing:
        return [
            f"{gateway.node_id}: generated dispatcher exports are missing from "
            f"uses_interfaces: {', '.join(missing)}"
        ]
    return []


def sync_contract_block(skill_file: Path, contract_block: str) -> str:
    """Inject or replace the generated blueprint contract block in SKILL.md."""
    text = skill_file.read_text(encoding="utf-8")
    if CONTRACT_START in text and CONTRACT_END in text:
        pattern = re.compile(
            rf"{re.escape(CONTRACT_START)}.*?{re.escape(CONTRACT_END)}\n?",
            re.DOTALL,
        )
        return pattern.sub(contract_block, text, count=1)

    match = re.match(r"(---\n.*?\n---\n+)", text, re.DOTALL)
    if not match:
        raise BlueprintError(f"{skill_file}: missing YAML frontmatter")
    return text[: match.end()] + contract_block + text[match.end() :]


def sync_interface_block(text: str, interface_block: str) -> str:
    """Inject, replace, or remove the generated owner-facing interface block."""
    if INTERFACES_START in text and INTERFACES_END in text:
        pattern = re.compile(
            rf"{re.escape(INTERFACES_START)}.*?{re.escape(INTERFACES_END)}\n?",
            re.DOTALL,
        )
        text = pattern.sub(lambda _: interface_block, text, count=1)
        return re.sub(r"\n{3,}", "\n\n", text)

    if not interface_block:
        return text

    contract_match = re.search(rf"{re.escape(CONTRACT_END)}\n*", text)
    if contract_match:
        updated = text[: contract_match.end()] + interface_block + text[contract_match.end() :]
        return re.sub(r"\n{3,}", "\n\n", updated)

    frontmatter_match = re.match(r"(---\n.*?\n---\n+)", text, re.DOTALL)
    if not frontmatter_match:
        raise BlueprintError("SKILL.md: missing YAML frontmatter for interface injection")
    updated = text[: frontmatter_match.end()] + interface_block + text[frontmatter_match.end() :]
    return re.sub(r"\n{3,}", "\n\n", updated)


def generated_used_interfaces_block(document: Mapping[str, Any]) -> str:
    """Render one canonical consumer-local YAML block, or empty text."""

    selected = any(
        bool(document.get(field))
        for field in ("interfaces", "helper_interfaces")
    )
    if not selected:
        return ""
    payload = yaml.safe_dump(
        dict(document),
        sort_keys=True,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip()
    return "\n".join(
        [
            USED_INTERFACES_START,
            "> Generated consumer-local interface contracts. Do not edit this block by hand.",
            "",
            "```yaml",
            payload,
            "```",
            USED_INTERFACES_END,
            "",
        ]
    )


def sync_used_interfaces_block(
    text: str,
    block: str,
    *,
    root_consumer: bool,
) -> str:
    """Replace/remove one used-interface block with deterministic placement."""

    start_count = text.count(USED_INTERFACES_START)
    end_count = text.count(USED_INTERFACES_END)
    if start_count != end_count or start_count > 1:
        raise BlueprintError("conflicting generated used-interface markers")
    if start_count == 1:
        pattern = re.compile(
            rf"{re.escape(USED_INTERFACES_START)}.*?{re.escape(USED_INTERFACES_END)}\n?",
            re.DOTALL,
        )
        text = pattern.sub(lambda _match: block, text, count=1)
        return re.sub(r"\n{3,}", "\n\n", text)
    if not block:
        return text
    if root_consumer:
        matches = list(re.finditer(re.escape(CONTRACT_END), text))
        if len(matches) != 1:
            raise BlueprintError("SKILL.md must contain exactly one blueprint contract block")
        end = matches[0].end()
        suffix = text[end:]
        suffix = suffix.lstrip("\n")
        return text[:end] + "\n" + block + suffix
    return block + text


def plan_consumer_interface_updates(
    repository_graph: Any,
    projections: Mapping[str, Any],
) -> dict[Path, str]:
    """Plan all consumer gateway contents before any write occurs."""

    planned: dict[Path, str] = {}
    owners: dict[Path, str] = {}
    for consumer_id, projection in sorted(projections.items()):
        node = repository_graph.nodes.get(consumer_id)
        if node is None or node.node_type != "behavioral_source":
            raise BlueprintError(f"unknown behavioral-source consumer {consumer_id!r}")
        gateway = node.gateway_path
        if gateway is None:
            raise BlueprintError(f"{consumer_id}: missing gateway")
        owner = node.module_root.resolve()
        absolute = Path(gateway).resolve(strict=False)
        try:
            absolute.relative_to(owner)
        except ValueError as exc:
            raise BlueprintError(f"{consumer_id}: gateway escapes owner boundary") from exc
        prior = owners.get(absolute)
        if prior is not None and prior != consumer_id:
            raise BlueprintError(
                f"gateway {absolute} is shared by consumers {prior!r} and {consumer_id!r}"
            )
        if not absolute.is_file():
            raise BlueprintError(f"{consumer_id}: gateway is missing: {absolute}")
        owners[absolute] = consumer_id
        document = projection.document if hasattr(projection, "document") else projection
        if not isinstance(document, Mapping):
            raise BlueprintError(f"{consumer_id}: projection document must be a mapping")
        block = generated_used_interfaces_block(document)
        current = absolute.read_text(encoding="utf-8")
        planned[absolute] = sync_used_interfaces_block(
            current,
            block,
            root_consumer=absolute
            == (node.module_root / "SKILL.md").resolve(strict=False),
        )
    return planned


def plan_projected_consumer_interface_updates(
    repository_graph: RepositoryBlueprintGraph,
    certification: CertificationView,
) -> dict[Path, str]:
    """Project every canonical v5 Markdown gateway from the shared graph."""

    projections = {}
    for node_id, node in sorted(repository_graph.nodes.items()):
        if node.node_type != "behavioral_source":
            continue
        gateway = node.declaration.get("gateway")
        language = gateway.get("language") if isinstance(gateway, dict) else None
        if not isinstance(language, str) or not language.startswith("Markdown"):
            continue
        projections[node_id] = project_consumer_interfaces(
            repository_graph,
            node_id,
            certification,
        )
    return plan_consumer_interface_updates(repository_graph, projections)


def apply_consumer_interface_updates(planned: Mapping[Path, str]) -> None:
    """Atomically apply a previously complete consumer update plan."""

    for path, text in sorted(planned.items(), key=lambda item: item[0].as_posix()):
        mode = stat.S_IMODE(path.stat().st_mode)
        atomic_replace_bytes(
            path,
            text.encode("utf-8"),
            allowed_root=path.parent,
            mode=mode,
        )


def sync_module(blueprint: ModuleBlueprint, check_only: bool) -> list[str]:
    data = blueprint.data
    skill_dir = blueprint.path.parent
    errors: list[str] = validate_gateway_declares_generated_dispatches(
        blueprint.name,
        blueprint.repository_graph,
    )
    expected_skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    expected_skill = sync_interface_block(
        expected_skill,
        generated_interface_block(blueprint.name, blueprint.repository_graph),
    )

    skill_path = skill_dir / "SKILL.md"
    current_skill = skill_path.read_text(encoding="utf-8")
    if current_skill != expected_skill:
        if check_only:
            errors.append(f"{skill_path}: generated blueprint blocks are out of sync")
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
            interface_spec, source_node_id = _generated_export_binding(
                graph,
                export_id,
                export,
            )
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
) -> list[str]:
    expected = json.dumps(generated_runtime_dependencies_manifest(blueprints), indent=2) + "\n"
    current = RUNTIME_DEPENDENCIES_PATH.read_text(encoding="utf-8") if RUNTIME_DEPENDENCIES_PATH.exists() else ""
    if current == expected:
        return []
    if check_only:
        return [f"{RUNTIME_DEPENDENCIES_PATH}: out of sync with blueprint.yaml"]
    RUNTIME_DEPENDENCIES_PATH.write_text(expected, encoding="utf-8")
    return []


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
        parser.add_argument(
            "--schema-version",
            type=int,
            choices=(4, 5, 6),
            default=6,
            help="Select the explicit repository blueprint generation.",
        )
        return parser

    def run(self, args: argparse.Namespace) -> int:
        return run_sync(
            check_only=args.check,
            schema_version=args.schema_version,
        )


def run_sync(*, check_only: bool, schema_version: int = 6) -> int:
    try:
        blueprints = load_blueprints(schema_version=schema_version)
    except BlueprintError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    errors: list[str] = []
    for blueprint in blueprints.values():
        errors.extend(sync_module(blueprint, check_only=check_only))
    errors.extend(sync_runtime_dependencies_manifest(blueprints, check_only=check_only))

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
