"""Validate canonical behavioral-source dependencies named in instruction text."""
from __future__ import annotations

import re
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.blueprints.graph import (  # noqa: E402
    BlueprintGraphError,
    BlueprintNode,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.blueprints.inventory import BlueprintInventoryError  # noqa: E402


_PARENT_PATH_RE = re.compile(
    r"(?:^|(?<=[^A-Za-z0-9_./~\-]))(?:\.\.?/)*\.\./"
)
_ALLOWED_DIRS_BASE = re.compile(
    r"(?:^|(?<=[^A-Za-z0-9_./~\-]))"
    r"(?:\.\.?/)*\.\./(?:references|tools|scripts)"
    r"(?:/|[ \t`'\"]|$)"
)
_DEPRECATED_MARKERS_RE = re.compile(
    r"^(Sub-skills to invoke:|Depends on:)",
    re.MULTILINE,
)
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_BLUEPRINT_BLOCK_RE = re.compile(
    r"<!-- BEGIN BLUEPRINT (?:CONTRACT|INTERFACES) -->.*?"
    r"<!-- END BLUEPRINT (?:CONTRACT|INTERFACES) -->",
    re.DOTALL,
)
_MODULE_ID = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:\.[a-z_][a-z0-9_-]*)*"
_CANONICAL_INTERFACE_RE = re.compile(
    rf"\b{_MODULE_ID}\."
    r"(?:"
    r"interface\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
    r"|source\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
    r"\.interface\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
    r")\b"
)
_OPAQUE_RUNTIME_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./~-])"
    r"(?:[A-Za-z0-9_.~+-]+/)*_(?:rtx|cx)/[A-Za-z0-9_.~+/-]+"
)
REQUIRES_BLUEPRINT_GRAPH = True


def _strip_generated_prose(text: str) -> str:
    frontmatter = _FRONTMATTER_RE.match(text)
    if frontmatter:
        text = text[frontmatter.end() :]
    return _BLUEPRINT_BLOCK_RE.sub("", text)


def _word_boundary_mentions(text: str, module_id: str) -> bool:
    escaped = re.escape(module_id)
    return bool(
        re.search(
            r"(?:^|(?<=[^A-Za-z0-9:_\-]))"
            + escaped
            + r"(?=[^A-Za-z0-9:_\-]|$)",
            text,
        )
    )


def _validate_parent_paths(skill_file: Path, module_id: str) -> list[str]:
    allowed = _ALLOWED_DIRS_BASE
    errors: list[str] = []
    for lineno, line in enumerate(
        skill_file.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if _PARENT_PATH_RE.search(line) and not allowed.search(line):
            errors.append(
                f"{skill_file}:{lineno}: parent paths in SKILL.md may only point "
                f"to ../references or ../../tools: {line.strip()}"
            )
    return errors


def _declared_interface_ids(source: BlueprintNode) -> set[str]:
    return {
        entry["interface"]
        for entry in source.declaration.get("uses_interfaces", [])
        if isinstance(entry, dict) and isinstance(entry.get("interface"), str)
    }


def _top_level_module_id(
    graph: RepositoryBlueprintGraph,
    module_id: str,
) -> str:
    """Return the public skill module that owns ``module_id``."""

    return graph.module_ancestry.get(module_id, (module_id,))[0]


def _used_module_ids(
    graph: RepositoryBlueprintGraph,
    module_id: str,
) -> set[str]:
    used: set[str] = set()
    local_module_ids = {
        owner_id
        for owner_id in graph.module_sources
        if module_id
        in graph.module_ancestry.get(owner_id, (owner_id,))
    }
    local_sources = {
        source_id
        for owner_id in local_module_ids
        for source_id in graph.module_sources.get(owner_id, ())
    }
    source_modules = {
        source_id: owner_id
        for owner_id, source_ids in graph.module_sources.items()
        for source_id in source_ids
    }
    for source_id in local_sources:
        source = graph.nodes[source_id]
        for interface_id in _declared_interface_ids(source):
            export = graph.exports.get(interface_id)
            if export is not None and export.module_node_id != module_id:
                used.add(_top_level_module_id(graph, export.module_node_id))
    for edge in graph.node_edges:
        if edge.relation != "uses-source" or edge.source_id not in local_sources:
            continue
        target_module = source_modules.get(edge.target_id)
        if target_module is not None and target_module != module_id:
            used.add(_top_level_module_id(graph, target_module))
    return used


def _effective_declared_interface_ids(
    graph: RepositoryBlueprintGraph,
    source: BlueprintNode,
) -> set[str]:
    declared = _declared_interface_ids(source)
    effective = set(declared)
    for export_id, export in graph.exports.items():
        if export.source_interface_id in declared:
            effective.add(export_id)
        if export_id in declared and export.source_interface_id is not None:
            effective.add(export.source_interface_id)
    return effective


def _validate_markdown_source(
    graph: RepositoryBlueprintGraph,
    source: BlueprintNode,
) -> list[str]:
    gateway = source.declaration.get("gateway")
    if (
        not isinstance(gateway, dict)
        or str(gateway.get("language", "")).casefold() != "markdown"
        or source.gateway_path is None
    ):
        return []
    try:
        body = _strip_generated_prose(
            source.gateway_path.read_text(encoding="utf-8")
        )
    except UnicodeDecodeError:
        return []

    errors: list[str] = []
    for match in _OPAQUE_RUNTIME_PATH_RE.finditer(body):
        errors.append(
            f"{source.gateway_path}: behavioral source body names opaque runtime "
            f"path `{match.group(0)}`; use a declared canonical interface"
        )
    declared = _effective_declared_interface_ids(graph, source)
    mentioned = set(_CANONICAL_INTERFACE_RE.findall(body))
    for interface_id in sorted(mentioned - declared):
        errors.append(
            f"{source.gateway_path}: canonical interface `{interface_id}` is not "
            f"declared in {source.node_id}.uses_interfaces"
        )
    return errors


def _has_module_blueprints(repo_root: Path) -> bool:
    skills_root = repo_root / "skills"
    return skills_root.is_dir() and any(skills_root.glob("*/blueprint.yaml"))


def _validate_graph(
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
) -> list[str]:
    errors: list[str] = []
    modules = {
        node.node_id: node
        for node in graph.nodes.values()
        if node.node_type == "module"
        and node.module_root.parent == repo_root / "skills"
    }
    for source in sorted(
        (
            node
            for node in graph.nodes.values()
            if node.node_type == "behavioral_source"
        ),
        key=lambda node: node.node_id,
    ):
        try:
            relative_root = source.module_root.relative_to(repo_root / "skills")
        except ValueError:
            continue
        if len(relative_root.parts) >= 1:
            errors.extend(_validate_markdown_source(graph, source))

    for module_id, module in sorted(modules.items()):
        skill_file = module.module_root / "SKILL.md"
        if not skill_file.is_file():
            continue
        errors.extend(_validate_parent_paths(skill_file, module_id))
        text = skill_file.read_text(encoding="utf-8")
        for match in _DEPRECATED_MARKERS_RE.finditer(text):
            lineno = text[: match.start()].count("\n") + 1
            errors.append(
                f"{skill_file}:{lineno}: {match.group(0)} — declare cross-module "
                "use through the gateway behavioral source's uses_interfaces"
            )

        declared_modules = _used_module_ids(graph, module_id)
        body = _strip_generated_prose(text)
        mentioned_modules = {
            other_id
            for other_id in modules
            if other_id != module_id
            and _word_boundary_mentions(body, other_id)
        }
        undeclared = sorted(mentioned_modules - declared_modules)
        if undeclared:
            errors.append(
                f"{skill_file}: exact module-name mentions in SKILL.md body are not "
                "declared through the gateway behavioral source's uses_interfaces: "
                f"{undeclared}"
            )
    return errors


def validate_with_graph(
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
) -> list[str]:
    return _validate_graph(repo_root, graph)


def validate(repo_root: Path) -> list[str]:
    if not _has_module_blueprints(repo_root):
        return []

    schema_root = repo_root / "references" / "blueprint-schema"
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
        return [str(exc)]
    return _validate_graph(repo_root, graph)


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[2])
    if errors:
        print("error: invalid skill dependencies.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
