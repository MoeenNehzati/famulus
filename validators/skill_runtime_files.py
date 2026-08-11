"""Validate private skill runtime file layout and names."""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

RTX_DIR_NAME = "_rtx"
ALLOWED_RTX_SUFFIXES = {".py"}
EXEMPT_RTX_FILENAMES = {"__init__.py"}
EXEMPT_RTX_DIRNAMES = {"__pycache__"}
RUNTIME_STEM_RE = re.compile(r"^_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+$")

_SKIP_SKILLS = {".system"}
_CHILD_ARTIFACT_DIRS = {
    "assets",
    "blueprints",
    "schemas",
    "state",
    # Generated at run time and gitignored, exactly like `state`. Without this,
    # a skill that writes logs beneath its module root fails the check whenever
    # one of its jobs happens to have written recently -- which made this
    # validator's result depend on the clock rather than on the commit.
    "logs",
    "tests",
    ".certificates",
    ".certificate-history",
}
_CHILD_ARTIFACT_FILENAMES = {
    "blueprint.yaml",
    ".pooled-blueprint-review.yaml",
    ".pooled-blueprint-review.health.json",
}
REQUIRES_BLUEPRINT_GRAPH = True
BLUEPRINT_GRAPH_OPTIONAL = True


def _iter_skill_files(repo_root: Path):
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return
    for path in skills_root.rglob("*"):
        if not path.is_file():
            continue
        rel_path = path.relative_to(repo_root)
        if len(rel_path.parts) < 3:
            continue
        if rel_path.parts[1] in _SKIP_SKILLS:
            continue
        # Interpreter byte-cache, at any depth. Any import of a skill module
        # leaves one behind, so without this the check reports on whether
        # something happened to run rather than on what is being committed.
        if "__pycache__" in rel_path.parts:
            continue
        yield path, rel_path


def _validate_private_component(component: str, rel_path: Path, kind: str) -> list[str]:
    if RUNTIME_STEM_RE.fullmatch(component):
        return []
    return [
        f"{rel_path.as_posix()}: runtime {kind} must match "
        f"`^_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+$`; got `{component}`"
    ]


def _validate_rtx_path(path: Path, rel_path: Path) -> list[str]:
    errors: list[str] = []

    private_parts = rel_path.parts[3:-1]
    for dirname in private_parts:
        if dirname in EXEMPT_RTX_DIRNAMES:
            continue
        errors.extend(_validate_private_component(dirname, rel_path, "directory name"))

    hidden_health_sidecar = (
        path.name.startswith(".")
        and path.name != ".health.json"
        and path.name.endswith(".health.json")
    )
    if (
        path.name in EXEMPT_RTX_FILENAMES
        or path.name.endswith(".blueprint.yaml")
        or hidden_health_sidecar
    ):
        return errors

    if path.suffix not in ALLOWED_RTX_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_RTX_SUFFIXES))
        errors.append(
            f"{rel_path.as_posix()}: unsupported runtime suffix `{path.suffix}`; "
            f"allowed suffixes: {allowed}"
        )
    errors.extend(_validate_private_component(path.stem, rel_path, "filename stem"))
    return errors


def _registered_child_artifact(path: Path, graph: object | None) -> bool:
    if graph is None:
        return False
    nodes = getattr(graph, "nodes", {})
    module_parents = getattr(graph, "module_parents", {})
    matching_roots: list[Path] = []
    for module_id, parent_id in module_parents.items():
        if parent_id is None:
            continue
        node = nodes.get(module_id)
        module_root = getattr(node, "module_root", None)
        if isinstance(module_root, Path) and path.is_relative_to(module_root):
            matching_roots.append(module_root)
    if not matching_roots:
        return False
    relative = path.relative_to(max(matching_roots, key=lambda root: len(root.parts)))
    fixed_artifact = (
        relative.parent == Path(".")
        and relative.name in _CHILD_ARTIFACT_FILENAMES
        or bool(relative.parts)
        and relative.parts[0] in _CHILD_ARTIFACT_DIRS
    )
    if fixed_artifact:
        return True

    direct_file_owners = getattr(graph, "direct_file_owners", {})
    owner_id = direct_file_owners.get(path)
    owner = nodes.get(owner_id)
    if getattr(owner, "node_type", None) != "behavioral_source":
        return False
    if getattr(owner, "gateway_path", None) != path:
        return False
    declaration = getattr(owner, "declaration", {})
    gateway = declaration.get("gateway") if isinstance(declaration, dict) else None
    language = gateway.get("language") if isinstance(gateway, dict) else None
    return isinstance(language, str) and not language.startswith("Python")


def _validate(repo_root: Path, graph: object | None) -> list[str]:
    errors: list[str] = []
    seen_by_parent: dict[tuple[str, ...], dict[str, tuple[str, ...]]] = defaultdict(dict)

    for path, rel_path in _iter_skill_files(repo_root):
        parts = rel_path.parts
        if len(parts) >= 4 and parts[2] == "scripts" and path.suffix in ALLOWED_RTX_SUFFIXES:
            errors.append(
                f"{rel_path.as_posix()}: skill runtime files must live under "
                f"`skills/<skill>/{RTX_DIR_NAME}/`, not `scripts/`"
            )
            continue

        if len(parts) >= 4 and parts[2] == RTX_DIR_NAME:
            if _registered_child_artifact(path, graph):
                continue
            errors.extend(_validate_rtx_path(path, rel_path))

            for depth in range(3, len(parts)):
                component_parts = parts[: depth + 1]
                component_name = parts[depth]
                if component_name in EXEMPT_RTX_DIRNAMES:
                    continue
                if depth == len(parts) - 1 and component_name in EXEMPT_RTX_FILENAMES:
                    continue
                parent = parts[:depth]
                folded = component_name.casefold()
                previous = seen_by_parent[parent].get(folded)
                if previous is not None and previous != component_parts:
                    errors.append(
                        f"{Path(*component_parts).as_posix()}: case-insensitive "
                        "runtime path collision with "
                        f"{Path(*previous).as_posix()}"
                    )
                else:
                    seen_by_parent[parent][folded] = component_parts
    return errors


def validate_with_graph(repo_root: Path, graph: object) -> list[str]:
    return _validate(repo_root, graph)


def validate(repo_root: Path) -> list[str]:
    return _validate(repo_root, None)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Skill runtime file violations found:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
