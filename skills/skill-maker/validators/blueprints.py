"""Validate blueprint presence, schema correctness, and contract-block sync rules."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only in misconfigured envs
    jsonschema = None

_SYNC_SCRIPT = Path(__file__).resolve().parents[1] / "_rtx" / "_blueprint_syncer.py"
_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "references" / "blueprint" / "schema.json"

CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"
INTERFACES_START = "<!-- BEGIN BLUEPRINT INTERFACES -->"
INTERFACES_END = "<!-- END BLUEPRINT INTERFACES -->"


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False):
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.YAMLError(f"duplicate key `{key}`")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_schema() -> dict[str, Any] | None:
    if not _SCHEMA_PATH.exists():
        return None
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate_blueprint_schema(
    blueprint_path: Path,
    blueprint: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
    """Run jsonschema validation; return error strings."""
    errors: list[str] = []
    if jsonschema is None:
        return [
            f"{blueprint_path}: cannot validate blueprint schema because required "
            "Python package `jsonschema` is not installed"
        ]
    validator = jsonschema.Draft7Validator(schema)
    for error in sorted(validator.iter_errors(blueprint), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in error.absolute_path) or "(root)"
        errors.append(f"{blueprint_path}: schema error at {path}: {error.message}")
    return errors


# All valid category nodes (excluding structural root 'assistant').
# Must stay in sync with references/blueprint/schema.json enum.
_CATEGORY_NODES: frozenset[str] = frozenset({
    "research-assistant",
    "general-assistant",
    "productivity-general-assistant",
    "workflow-general-assistant",
    "development-assistant",
    "skill-making-development-assistant",
    "coding-development-assistant",
    "system-assistant",
})
_CATEGORY_ROOT = "assistant"


def _validate_category_hierarchy(
    blueprint_path: Path,
    blueprint: dict[str, Any],
) -> list[str]:
    """Enforce the postfix hierarchy rule: every non-root category must end with
    '-{parent}' where parent is a known category node or the structural root.

    Uses longest-suffix match to find the immediate parent.
    """
    errors: list[str] = []
    cat = blueprint.get("category")
    if not isinstance(cat, str):
        return errors  # schema validation handles type/enum errors
    all_nodes = _CATEGORY_NODES | {_CATEGORY_ROOT}
    # Find longest known suffix of the form '-<node>'
    parent = max(
        (node for node in all_nodes if cat != node and cat.endswith(f"-{node}")),
        key=len,
        default=None,
    )
    if parent is None:
        errors.append(
            f"{blueprint_path}: category '{cat}' has no valid parent in the taxonomy tree "
            f"(expected name ending with '-assistant' or '-<parent-node>')"
        )
    return errors


def _validate_interface_cross_fields(
    blueprint_path: Path,
    blueprint: dict[str, Any],
) -> list[str]:
    """Python-only checks that jsonschema cannot express.

    Currently enforces: if an interface has description, it must also have usage.
    (jsonschema marks both as optional individually; the pairing rule requires Python.)
    """
    errors: list[str] = []
    interfaces = blueprint.get("interfaces") or {}
    if not isinstance(interfaces, dict):
        return errors
    machine = interfaces.get("machine") or {}
    if not isinstance(machine, dict):
        return errors
    for iface_name, spec in machine.items():
        if not isinstance(spec, dict):
            continue
        has_desc = bool((spec.get("description") or "").strip())
        has_usage = spec.get("usage") is not None
        if has_desc and not has_usage:
            errors.append(
                f"{blueprint_path}: machine interface '{iface_name}' has description but no usage field "
                "(add usage: \"\" for no-arg interfaces, or the full arg template)"
            )
    return errors


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    blueprint_template = repo_root / "references" / "blueprint" / "template.yaml"

    if not skills_root.is_dir():
        return errors

    if not blueprint_template.exists():
        errors.append(f"{blueprint_template}: missing blueprint template reference file")

    schema = _load_schema()
    if schema is None:
        errors.append(f"{_SCHEMA_PATH}: missing blueprint schema file")

    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        skill_file = skill_dir / "SKILL.md"
        blueprint_path = skill_dir / "blueprint.yaml"

        if not skill_file.exists():
            continue
        if not blueprint_path.exists():
            errors.append(f"{skill_dir}: missing blueprint.yaml")
            continue

        # ── Schema validation (jsonschema) ───────────────────────────────────
        try:
            blueprint = yaml.load(blueprint_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
        except yaml.YAMLError as exc:
            errors.append(f"{blueprint_path}: YAML parse error: {exc}")
            continue

        if schema is not None and isinstance(blueprint, dict):
            errors.extend(_validate_blueprint_schema(blueprint_path, blueprint, schema))

        # ── Cross-field checks (Python only) ─────────────────────────────────
        if isinstance(blueprint, dict):
            errors.extend(_validate_category_hierarchy(blueprint_path, blueprint))
            errors.extend(_validate_interface_cross_fields(blueprint_path, blueprint))

        # ── SKILL.md marker checks ────────────────────────────────────────────
        text = skill_file.read_text(encoding="utf-8")
        start_count = text.count(CONTRACT_START)
        end_count = text.count(CONTRACT_END)
        has_contract = start_count > 0 or end_count > 0
        interface_start_count = text.count(INTERFACES_START)
        interface_end_count = text.count(INTERFACES_END)

        if start_count != end_count:
            errors.append(f"{skill_file}: blueprint contract markers are unbalanced")
        if start_count > 1 or end_count > 1:
            errors.append(f"{skill_file}: blueprint contract block must appear at most once")
        if interface_start_count != interface_end_count:
            errors.append(f"{skill_file}: blueprint interface markers are unbalanced")
        if interface_start_count > 1 or interface_end_count > 1:
            errors.append(f"{skill_file}: blueprint interface block must appear at most once")
        if not has_contract:
            errors.append(
                f"{skill_file}: local skill is missing generated blueprint contract block"
            )

    if errors:
        return errors

    # ── Sync drift check ─────────────────────────────────────────────────────
    result = subprocess.run(
        [sys.executable, str(_SYNC_SCRIPT), "--check"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    if result.returncode != 0:
        if result.stdout:
            errors.extend(result.stdout.splitlines())
        if result.stderr:
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
