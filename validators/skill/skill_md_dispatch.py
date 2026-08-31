"""Validate the generated owner-facing SKILL.md interface block uses dispatcher."""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (REPO_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from officina.blueprints.graph import (  # noqa: E402
    BlueprintGraphError,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.blueprints.inventory import BlueprintInventoryError  # noqa: E402
from validators.skill_md_body import (  # noqa: E402
    generated_interface_block,
    hand_authored_skill_body,
    strip_fenced_code_blocks,
)
REQUIRES_BLUEPRINT_GRAPH = True


def _body_for_invocation_check(text: str) -> str:
    """Strip generated blocks and code fences before checking for invocation violations.

    Code fences are excluded because architecture diagrams and directory listings
    may reference runtime paths structurally without being executable invocations.
    Absolute paths under unrelated repo tooling are also excluded via the caller's
    regex.
    """
    return strip_fenced_code_blocks(hand_authored_skill_body(text))


def _validate_skill_text(
    skill_md: Path,
    skill_name: str,
    text: str,
    *,
    all_ids: list[str],
    visible_ids: list[str],
    dispatcher_targets: list[str],
) -> list[str]:
    errors: list[str] = []
    if not all_ids:
        return errors

    body = hand_authored_skill_body(text)
    invocation_body = _body_for_invocation_check(text)
    raw_runtime_pattern = r"(?<!/)(?:scripts|_rtx)/[\w.-]+\.(?:py|sh)"
    if re.search(raw_runtime_pattern, invocation_body):
        errors.append(
            f"{skill_md}: skill body must not invoke runtime files directly; "
            "reference dispatcher interface names instead"
        )
    if "dispatcher --caller-skill" in body:
        errors.append(
            f"{skill_md}: skill body must not invoke dispatcher directly; "
            "interface invocations belong in the generated block (blueprint.yaml owns them)"
        )

    if not visible_ids:
        return errors
    block = generated_interface_block(text)
    if block is None:
        errors.append(f"{skill_md}: missing generated blueprint interface block")
        return errors
    if re.search(raw_runtime_pattern, block):
        errors.append(
            f"{skill_md}: generated interface block must not expose raw runtime files"
        )
    for interface_id in dispatcher_targets:
        expected = f"dispatcher --caller-skill {skill_name} {interface_id}"
        if expected not in block:
            errors.append(
                f"{skill_md}: generated interface block is missing dispatcher command "
                f"for `{interface_id}`"
            )
    return errors


def _validate_graph(
    graph: RepositoryBlueprintGraph,
    repo_root: Path,
) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    for module in sorted(
        (
            node
            for node in graph.nodes.values()
            if node.node_type == "module"
            and node.module_root.parent == skills_root
        ),
        key=lambda node: node.node_id,
    ):
        skill_md = module.module_root / "SKILL.md"
        if not skill_md.is_file():
            continue
        exports = [
            (interface_id, export)
            for interface_id, export in sorted(graph.exports.items())
            if export.module_node_id == module.node_id
        ]
        all_ids = [interface_id for interface_id, _export in exports]
        dispatcher_targets = [
            interface_id
            for interface_id, export in exports
            if isinstance(export.declaration.get("process_binding"), dict)
        ]
        visible_ids = [
            interface_id
            for interface_id, export in exports
            if isinstance(export.declaration.get("process_binding"), dict)
            or (
                isinstance(export.declaration.get("description"), str)
                and export.declaration["description"].strip()
            )
        ]
        errors.extend(
            _validate_skill_text(
                skill_md,
                module.node_id,
                skill_md.read_text(encoding="utf-8"),
                all_ids=all_ids,
                visible_ids=visible_ids,
                dispatcher_targets=dispatcher_targets,
            )
        )
    return errors


def validate(repo_root: Path) -> list[str]:
    skills_root = repo_root / "skills"
    if (
        not skills_root.is_dir()
        or not any(skills_root.glob("*/blueprint.yaml"))
    ):
        return []

    schema_root = repo_root / "references" / "blueprint-schema"
    try:
        repository_graph = load_repository_blueprint_graph(
            repo_root,
            schema_root=schema_root if (schema_root / "module.schema.json").is_file() else None,
        )
    except (BlueprintGraphError, BlueprintInventoryError, OSError, UnicodeError) as exc:
        return [str(exc)]
    return validate_with_graph(repo_root, repository_graph)


def validate_with_graph(
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
) -> list[str]:
    return _validate_graph(graph, repo_root)


def main() -> int:
    errors = validate(REPO_ROOT)
    if errors:
        print("error: invalid SKILL.md dispatcher exposure.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
