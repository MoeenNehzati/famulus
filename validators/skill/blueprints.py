"""Validate canonical blueprint source files and the generated interface block."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.blueprints.graph import (  # noqa: E402
    BlueprintGraphError,
    RepositoryBlueprintGraph,
    authored_node_input_paths,
    load_repository_blueprint_graph,
    validate_runtime_file_path,
)
from officina.blueprints.inventory import (  # noqa: E402
    BlueprintInventoryError,
)
from officina.common.repository_paths import (  # noqa: E402
    RepositoryPathError,
    repository_relative_path,
)
from validators.skill_md_body import selected_skill_files


INTERFACES_START = "<!-- BEGIN BLUEPRINT INTERFACES -->"
INTERFACES_END = "<!-- END BLUEPRINT INTERFACES -->"
_REGULAR_GIT_MODES = {"100644", "100755"}
REQUIRES_BLUEPRINT_GRAPH = True


def _git_tracked_files(
    repo_root: Path,
) -> dict[str, tuple[tuple[str, str], ...]] | None:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--stage", "-z"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    entries: dict[str, list[tuple[str, str]]] = {}
    for record in result.stdout.split("\0"):
        if not record:
            continue
        metadata, separator, relative_path = record.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or not relative_path:
            return None
        mode, _object_id, stage = fields
        entries.setdefault(relative_path, []).append((mode, stage))
    return {path: tuple(values) for path, values in entries.items()}


def _validate_authored_input_files(
    graph: RepositoryBlueprintGraph,
    repo_root: Path,
    tracked_files: dict[str, tuple[tuple[str, str], ...]],
    validation_node_ids: tuple[str, ...] | None = None,
) -> list[str]:
    errors: list[str] = []
    nodes = graph.nodes.values() if validation_node_ids is None else (
        graph.nodes[node_id] for node_id in validation_node_ids
    )
    for node in nodes:
        try:
            paths = authored_node_input_paths(node, repo_root)
        except BlueprintGraphError as exc:
            errors.append(str(exc))
            continue
        for path in paths:
            try:
                validate_runtime_file_path(path, node.module_root, repo_root)
            except BlueprintGraphError as exc:
                errors.append(str(exc))
            try:
                relative_path = repository_relative_path(
                    path,
                    repo_root,
                ).as_posix()
            except RepositoryPathError:
                relative_path = path.as_posix()
            index_entries = tracked_files.get(relative_path)
            if not index_entries:
                errors.append(
                    f"{node.blueprint_path}: authored source file is not tracked by git: "
                    f"{relative_path}"
                )
            elif any(stage != "0" for _mode, stage in index_entries):
                errors.append(
                    f"{node.blueprint_path}: authored source file has nonzero Git index "
                    f"stages: {relative_path}"
                )
            elif len(index_entries) != 1:
                errors.append(
                    f"{node.blueprint_path}: authored source file must have exactly one "
                    f"stage-0 Git index entry: {relative_path}"
                )
            elif index_entries[0][0] not in _REGULAR_GIT_MODES:
                errors.append(
                    f"{node.blueprint_path}: authored source file Git index entry is not "
                    f"a regular file: {relative_path}"
                )
    return errors


def _validate_command_file_modes(
    tracked_files: dict[str, tuple[tuple[str, str], ...]],
) -> list[str]:
    errors: list[str] = []
    for relative_path, entries in sorted(tracked_files.items()):
        parts = Path(relative_path).parts
        if len(parts) < 4 or parts[0] != "skills" or parts[2] != "_cx":
            continue
        if entries != (("100755", "0"),):
            errors.append(
                f"{relative_path}: _cx command file must have one stage-0 "
                "executable Git index entry"
            )
    return errors


def _validate_generated_markers(skill_file: Path) -> list[str]:
    try:
        text = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"{skill_file}: cannot read SKILL.md: {exc}"]
    errors: list[str] = []
    pairs = (
        ("blueprint interface", INTERFACES_START, INTERFACES_END),
    )
    for label, start, end in pairs:
        start_count = text.count(start)
        end_count = text.count(end)
        reversed_pair = (
            start_count == end_count == 1
            and text.find(start) > text.find(end)
        )
        if start_count != end_count or reversed_pair:
            errors.append(f"{skill_file}: {label} markers are unbalanced")
        if start_count != 1 or end_count != 1:
            errors.append(f"{skill_file}: {label} block must appear exactly once")
    return errors


def _load_blueprint_syncer(repo_root: Path) -> ModuleType | None:
    """Load the repository-local sync checker without launching its CLI."""

    sync_path = (
        repo_root / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py"
    )
    if not sync_path.is_file():
        return None
    module_name = "_officina_blueprint_syncer_validator"
    spec = importlib.util.spec_from_file_location(module_name, sync_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load blueprint syncer: {sync_path}")
    module = importlib.util.module_from_spec(spec)
    missing = object()
    previous = sys.modules.get(module_name, missing)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is missing:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous


def preflight(
    repo_root: Path,
    *,
    prepared_graph: RepositoryBlueprintGraph | None = None,
    validation_paths: tuple[str, ...] | None = None,
    validation_node_ids: tuple[str, ...] | None = None,
) -> tuple[list[str], RepositoryBlueprintGraph | None]:
    """Own repository graph loading and its canonical diagnostics."""

    errors: list[str] = []
    skills_root = repo_root / "skills"
    blueprint_template = repo_root / "references" / "blueprint-schema" / "template.yaml"
    schema_root = repo_root / "references" / "blueprint-schema"

    if not skills_root.is_dir():
        return errors, None
    if (
        validation_paths is None
        or blueprint_template.relative_to(repo_root).as_posix() in validation_paths
    ) and not blueprint_template.is_file():
        errors.append(f"{blueprint_template}: missing blueprint template reference file")

    try:
        graph = prepared_graph
        if validation_paths is not None and graph is None:
            graph = load_repository_blueprint_graph(repo_root, schema_root=schema_root)
        skill_files = (
            tuple(path / "SKILL.md" for path in sorted(skills_root.iterdir())
                  if path.is_dir() and (path / "SKILL.md").is_file())
            if validation_paths is None else
            selected_skill_files(repo_root, validation_paths, validation_node_ids, graph)
        )
        for skill_file in skill_files:
            if not (skill_file.parent / "blueprint.yaml").is_file():
                errors.append(f"{skill_file.parent}: missing blueprint.yaml")
                continue
            errors.extend(_validate_generated_markers(skill_file))
        if errors:
            return errors, None
        if graph is None:
            graph = load_repository_blueprint_graph(repo_root, schema_root=schema_root)
    except BlueprintInventoryError as exc:
        errors.extend(
            f"{repo_root / issue.relative_path}: {issue.message}"
            for issue in exc.issues
        )
        return errors, None
    except (BlueprintGraphError, OSError, UnicodeError, ValueError) as exc:
        errors.append(str(exc))
        return errors, None
    return errors, graph


def validate_with_graph(
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
    validation_paths: tuple[str, ...] | None = None,
    validation_node_ids: tuple[str, ...] | None = None,
) -> list[str]:
    """Run non-topology blueprint checks against one validated graph."""

    errors: list[str] = []

    tracked_files = _git_tracked_files(repo_root)
    if tracked_files is None:
        errors.append("blueprint source validation requires a Git worktree")
    else:
        errors.extend(
            _validate_authored_input_files(graph, repo_root, tracked_files, validation_node_ids)
        )
        command_files = tracked_files if validation_paths is None else {
            path: entries for path, entries in tracked_files.items() if path in validation_paths
        }
        errors.extend(_validate_command_file_modes(command_files))
    if errors:
        return errors

    skill_files = None if validation_paths is None else selected_skill_files(
        repo_root, validation_paths, validation_node_ids, graph,
    )
    catalog_path = "references/blueprint-schema/runtime_dependencies.json"
    if skill_files == () and catalog_path not in validation_paths:
        return errors
    syncer = _load_blueprint_syncer(repo_root)
    if syncer is not None and graph.schema_version == 6:
        if validation_paths is None:
            errors.extend(syncer.validate_sync_state(
                repository_graph=graph, repository_root=repo_root,
                skills_root=repo_root / "skills",
                runtime_dependencies_path=repo_root / catalog_path,
            ))
        else:
            for node in graph.nodes.values():
                if node.node_type != "module" or node.module_root / "SKILL.md" not in skill_files:
                    continue
                blueprint = syncer.ModuleBlueprint(
                    node.node_id, node.blueprint_path, dict(node.declaration), graph,
                )
                errors.extend(syncer.sync_module(blueprint, check_only=True))
            if catalog_path in validation_paths:
                errors.extend(syncer.sync_runtime_dependencies_manifest(
                    syncer.blueprints_from_graph(graph, skills_root=repo_root / "skills"),
                    check_only=True, runtime_dependencies_path=repo_root / catalog_path,
                ))
    return errors


def test_blueprints(repo_root, graph, validation_paths, validation_node_ids):
    """Check selected authored inputs and generated artifacts after scoped preflight."""
    return validate_with_graph(repo_root, graph, validation_paths, validation_node_ids)


def validate(repo_root: Path) -> list[str]:
    errors, graph = preflight(repo_root)
    if errors or graph is None:
        return errors
    return validate_with_graph(repo_root, graph)


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[2])
    if errors:
        print("error: invalid blueprint skill layout.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
