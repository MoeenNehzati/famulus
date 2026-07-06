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

        script_interfaces = blueprint.get("script_interfaces")
        if script_interfaces is None:
            continue
        if not isinstance(script_interfaces, dict):
            errors.append(f"{blueprint_path}: script_interfaces: expected mapping")
            continue

        seen_ids: dict[str, str] = {}
        for interface_name, interface_spec in script_interfaces.items():
            context = f"{blueprint_path}: script_interfaces.{interface_name}"
            if not isinstance(interface_spec, dict):
                errors.append(f"{context}: expected mapping")
                continue

            interface_id = interface_spec.get("id")
            if not isinstance(interface_id, str) or not interface_id.strip():
                errors.append(f"{context}: missing non-empty string `id`")
            else:
                previous = seen_ids.get(interface_id)
                if previous is not None:
                    errors.append(
                        f"{context}: id `{interface_id}` duplicates {previous}; "
                        f"interface ids must be unique within a skill"
                    )
                else:
                    seen_ids[interface_id] = context

            if "default" in interface_spec:
                default_spec = interface_spec["default"]
                if not isinstance(default_spec, dict):
                    errors.append(f"{context}.default: expected mapping")
                elif "id" in default_spec:
                    errors.append(
                        f"{context}.default: must not define `id`; the default subinterface shares the parent interface id"
                    )

            subinterfaces = interface_spec.get("subinterfaces")
            if subinterfaces is None:
                continue
            if not isinstance(subinterfaces, dict):
                errors.append(f"{context}.subinterfaces: expected mapping")
                continue

            for sub_name, sub_spec in subinterfaces.items():
                sub_context = f"{context}.subinterfaces.{sub_name}"
                if not isinstance(sub_spec, dict):
                    errors.append(f"{sub_context}: expected mapping")
                    continue
                sub_id = sub_spec.get("id")
                if not isinstance(sub_id, str) or not sub_id.strip():
                    errors.append(f"{sub_context}: missing non-empty string `id`")
                    continue
                previous = seen_ids.get(sub_id)
                if previous is not None:
                    errors.append(
                        f"{sub_context}: id `{sub_id}` duplicates {previous}; "
                        f"interface ids must be unique within a skill"
                    )
                else:
                    seen_ids[sub_id] = sub_context

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
