"""Validate version-4 blueprint source files and generated skill blocks."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.common.blueprint_graph import (  # noqa: E402
    BlueprintGraphError,
    RepositoryBlueprintGraph,
    authored_node_input_paths,
    load_repository_blueprint_graph,
    validate_runtime_file_path,
)
from officina.common.blueprint_inventory import (  # noqa: E402
    BlueprintInventoryError,
)


CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"
INTERFACES_START = "<!-- BEGIN BLUEPRINT INTERFACES -->"
INTERFACES_END = "<!-- END BLUEPRINT INTERFACES -->"
_REGULAR_GIT_MODES = {"100644", "100755"}


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
) -> list[str]:
    errors: list[str] = []
    absolute_root = Path(os.path.abspath(repo_root))
    for node in graph.nodes.values():
        try:
            paths = authored_node_input_paths(node, repo_root)
        except BlueprintGraphError as exc:
            errors.append(str(exc))
            continue
        for path in paths:
            try:
                validate_runtime_file_path(path, node.skill_root, repo_root)
            except BlueprintGraphError as exc:
                errors.append(str(exc))
            lexical_path = Path(os.path.abspath(path))
            try:
                relative_path = lexical_path.relative_to(absolute_root).as_posix()
            except ValueError:
                relative_path = lexical_path.as_posix()
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


def _validate_generated_markers(skill_file: Path) -> list[str]:
    try:
        text = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"{skill_file}: cannot read SKILL.md: {exc}"]
    errors: list[str] = []
    pairs = (
        ("blueprint contract", CONTRACT_START, CONTRACT_END),
        ("blueprint interface", INTERFACES_START, INTERFACES_END),
    )
    for label, start, end in pairs:
        start_count = text.count(start)
        end_count = text.count(end)
        if start_count != end_count:
            errors.append(f"{skill_file}: {label} markers are unbalanced")
        if start_count > 1 or end_count > 1:
            errors.append(f"{skill_file}: {label} block must appear at most once")
    if CONTRACT_START not in text:
        errors.append(
            f"{skill_file}: local skill is missing generated blueprint contract block"
        )
    return errors


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    blueprint_template = repo_root / "references" / "blueprint" / "template.yaml"
    schema_root = repo_root / "references" / "blueprint"

    if not skills_root.is_dir():
        return errors
    if not blueprint_template.is_file():
        errors.append(f"{blueprint_template}: missing blueprint template reference file")

    for skill_dir in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue
        if not (skill_dir / "blueprint.yaml").is_file():
            errors.append(f"{skill_dir}: missing blueprint.yaml")
            continue
        errors.extend(_validate_generated_markers(skill_file))
    if errors:
        return errors

    try:
        graph = load_repository_blueprint_graph(
            repo_root,
            schema_root=schema_root,
        )
    except BlueprintInventoryError as exc:
        errors.extend(
            f"{repo_root / issue.relative_path}: {issue.message}"
            for issue in exc.issues
        )
        return errors
    except (BlueprintGraphError, OSError, UnicodeError, ValueError) as exc:
        errors.append(str(exc))
        return errors

    tracked_files = _git_tracked_files(repo_root)
    if tracked_files is None:
        errors.append("version-4 source validation requires a Git worktree")
    else:
        errors.extend(
            _validate_authored_input_files(graph, repo_root, tracked_files)
        )
    if errors:
        return errors

    sync_script = (
        repo_root / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py"
    )
    if sync_script.is_file():
        result = subprocess.run(
            [sys.executable, str(sync_script), "--check"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
        )
        if result.returncode != 0:
            errors.extend(result.stdout.splitlines())
            errors.extend(result.stderr.splitlines())
    return errors


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[3])
    if errors:
        print("error: invalid blueprint skill layout.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
