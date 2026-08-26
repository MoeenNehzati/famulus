"""Validate canonical module export and behavioral-source interface IDs."""
from __future__ import annotations

import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.blueprints.graph import (  # noqa: E402
    BlueprintGraphError,
    load_repository_blueprint_graph,
)
from officina.blueprints.inventory import BlueprintInventoryError  # noqa: E402

REQUIRES_BLUEPRINT_GRAPH = True


def validate_with_graph(
    repo_root: Path,
    graph: object,
) -> list[str]:
    """Interface-ID diagnostics are owned by the shared graph preflight."""

    del repo_root, graph
    return []


def validate(repo_root: Path) -> list[str]:
    skills_root = repo_root / "skills"
    if (
        not skills_root.is_dir()
        or not any(skills_root.glob("*/blueprint.yaml"))
    ):
        return []
    schema_root = repo_root / "references" / "blueprint-schema"
    try:
        load_repository_blueprint_graph(
            repo_root,
            schema_root=(
                schema_root
                if (schema_root / "module.schema.json").is_file()
                else None
            ),
            expected_schema_version=6,
        )
    except (
        BlueprintGraphError,
        BlueprintInventoryError,
        OSError,
        UnicodeError,
    ) as exc:
        return [str(exc)]
    return []


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[2])
    if errors:
        print("error: invalid blueprint interface ids.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
