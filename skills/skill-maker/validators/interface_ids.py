"""Validate blueprint interface ids and nested interface-id layout."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


class BlueprintError(Exception):
    """Raised when a blueprint is structurally invalid for interface ids."""


def _load_blueprint(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BlueprintError(f"{path}: failed to parse YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise BlueprintError(f"{path}: top level must be a mapping")
    return raw


def _expect_mapping(value: Any, context: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BlueprintError(f"{context}: expected mapping")
    return value


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors

    for blueprint_path in sorted(skills_root.glob("*/blueprint.yaml")):
        try:
            blueprint = _load_blueprint(blueprint_path)
        except BlueprintError as exc:
            errors.append(str(exc))
            continue

        interfaces = blueprint.get("interfaces")
        if interfaces is None:
            continue
        if not isinstance(interfaces, dict):
            errors.append(f"{blueprint_path}: interfaces: expected mapping")
            continue
        for kind in ("machine", "llm"):
            namespace = interfaces.get(kind)
            if namespace is None:
                continue
            if not isinstance(namespace, dict):
                errors.append(f"{blueprint_path}: interfaces.{kind}: expected mapping")
                continue
            for interface_name, interface_spec in namespace.items():
                context = f"{blueprint_path}: interfaces.{kind}.{interface_name}"
                if "." in str(interface_name):
                    errors.append(f"{context}: interface names must not contain `.`")
                if not isinstance(interface_spec, dict):
                    errors.append(f"{context}: expected mapping")

    return errors


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[3])
    if errors:
        print("error: invalid blueprint interface ids.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
