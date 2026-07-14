"""Validate private skill runtime file layout and names."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

RTX_DIR_NAME = "_rtx"
CX_DIR_NAME = "_cx"
ALLOWED_RTX_SUFFIXES = {".py", ".sh"}
EXEMPT_RTX_FILENAMES = {"__init__.py"}
EXEMPT_RTX_DIRNAMES = {"__pycache__"}
RUNTIME_STEM_RE = re.compile(r"^_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+$")

_SKIP_SKILLS = {".system"}


def _tracked_files(repo_root: Path) -> set[Path] | None:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return {repo_root / path for path in result.stdout.splitlines()}


def _iter_skill_files(repo_root: Path):
    tracked = _tracked_files(repo_root)
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return
    for path in skills_root.rglob("*"):
        if not path.is_file():
            continue
        if tracked is not None and path not in tracked:
            continue
        rel_path = path.relative_to(repo_root)
        if len(rel_path.parts) < 3:
            continue
        if rel_path.parts[1] in _SKIP_SKILLS:
            continue
        yield path, rel_path


def _validate_private_component(component: str, rel_path: Path, kind: str) -> list[str]:
    if RUNTIME_STEM_RE.fullmatch(component):
        return []
    return [
        f"{rel_path}: runtime {kind} must match "
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
        errors.append(f"{rel_path}: unsupported runtime suffix `{path.suffix}`; allowed suffixes: {allowed}")
    errors.extend(_validate_private_component(path.stem, rel_path, "filename stem"))
    return errors


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    seen_by_parent: dict[tuple[str, ...], dict[str, tuple[str, ...]]] = defaultdict(dict)

    for path, rel_path in _iter_skill_files(repo_root):
        parts = rel_path.parts
        if len(parts) >= 4 and parts[2] == "scripts" and path.suffix in ALLOWED_RTX_SUFFIXES:
            errors.append(
                f"{rel_path}: skill runtime files must live under "
                f"`skills/<skill>/{RTX_DIR_NAME}/`, not `scripts/`"
            )
            continue

        if len(parts) >= 4 and parts[2] == RTX_DIR_NAME:
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
                        f"{Path(*component_parts)}: case-insensitive runtime path collision with {Path(*previous)}"
                    )
                else:
                    seen_by_parent[parent][folded] = component_parts
        elif len(parts) >= 4 and parts[2] == CX_DIR_NAME:
            if path.name.endswith(".blueprint.yaml") or path.name.endswith(".health.json"):
                continue
            if not os.access(path, os.X_OK):
                errors.append(f"{rel_path}: _cx command file must be executable")

    return errors


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
