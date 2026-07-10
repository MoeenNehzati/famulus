"""Validate the generated owner-facing SKILL.md interface block uses dispatcher."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml


INTERFACES_START = "<!-- BEGIN BLUEPRINT INTERFACES -->"
INTERFACES_END = "<!-- END BLUEPRINT INTERFACES -->"
CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"


def _expect_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        return {}
    return value


def _uses_new_interface_model(blueprint: dict[str, Any]) -> bool:
    return isinstance(blueprint.get("interfaces"), dict)


def _interface_ids(blueprint: dict[str, Any], only_visible: bool = False) -> list[str]:
    """Return sorted list of interface IDs from blueprint.

    If only_visible=True, return only interfaces that have a description (those that
    the sync tool will expose in the generated block). Internal interfaces omit description.
    """
    if _uses_new_interface_model(blueprint):
        interfaces = _expect_mapping(blueprint.get("interfaces"))
        machine = _expect_mapping(interfaces.get("machine"))
        result: list[str] = []
        for name, spec in sorted(machine.items()):
            if not isinstance(spec, dict):
                continue
            if only_visible:
                desc = spec.get("description")
                if not (isinstance(desc, str) and desc.strip()):
                    continue
            result.append(str(name))
        return result

    interfaces = _expect_mapping(blueprint.get("script_interfaces"))
    result: list[str] = []
    for _name, spec in sorted(interfaces.items()):
        if not isinstance(spec, dict):
            continue
        if only_visible:
            desc = spec.get("description")
            if not (isinstance(desc, str) and desc.strip()):
                continue
        interface_id = spec.get("id")
        if isinstance(interface_id, str) and interface_id.strip():
            result.append(interface_id.strip())
    return result


def _body_text(text: str) -> str:
    """Return SKILL.md content with all generated blueprint blocks stripped out."""
    body = re.sub(
        rf"{re.escape(CONTRACT_START)}.*?{re.escape(CONTRACT_END)}",
        "",
        text,
        flags=re.DOTALL,
    )
    body = re.sub(
        rf"{re.escape(INTERFACES_START)}.*?{re.escape(INTERFACES_END)}",
        "",
        body,
        flags=re.DOTALL,
    )
    return body


def _body_for_invocation_check(text: str) -> str:
    """Strip generated blocks and code fences before checking for invocation violations.

    Code fences are excluded because architecture diagrams and directory listings
    may reference scripts/ paths structurally (e.g. showing what systemd calls)
    without being executable invocations. Absolute paths (e.g. $HOME/.../scripts/)
    are also excluded via the caller's regex.
    """
    body = _body_text(text)
    # Remove all fenced code blocks (```...```)
    return re.sub(r"```.*?```", "", body, flags=re.DOTALL)


def _interface_block(text: str) -> str | None:
    match = re.search(
        rf"{re.escape(INTERFACES_START)}(.*?){re.escape(INTERFACES_END)}",
        text,
        re.DOTALL,
    )
    if not match:
        return None
    return match.group(1)


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors

    for blueprint_path in sorted(skills_root.glob("*/blueprint.yaml")):
        skill_name = blueprint_path.parent.name
        skill_md = blueprint_path.parent / "SKILL.md"
        if not skill_md.exists():
            continue

        blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8")) or {}
        if not isinstance(blueprint, dict):
            continue
        all_ids = _interface_ids(blueprint)
        if not all_ids:
            continue

        # visible_ids: interfaces with a description (sync tool exposes these in the generated block)
        visible_ids = _interface_ids(blueprint, only_visible=True)

        text = skill_md.read_text(encoding="utf-8")

        # Body checks apply regardless of visibility — any skill with interfaces must not
        # re-invoke them in the hand-authored body
        body = _body_text(text)
        invocation_body = _body_for_invocation_check(text)
        # Match `scripts/` not preceded by `/` (to allow absolute paths like $HOME/.../scripts/foo)
        if re.search(r"(?<!/)scripts/", invocation_body):
            errors.append(
                f"{skill_md}: skill body must not invoke scripts directly; "
                "reference dispatcher interface names instead"
            )
        if "dispatcher --caller-skill" in body:
            errors.append(
                f"{skill_md}: skill body must not invoke dispatcher directly; "
                "interface invocations belong in the generated block (blueprint.yaml owns them)"
            )

        # Generated-block checks only apply when there are visible interfaces
        if not visible_ids:
            continue

        block = _interface_block(text)
        if block is None:
            errors.append(f"{skill_md}: missing generated blueprint interface block")
            continue

        if "scripts/" in block or "python3 scripts/" in block or "python scripts/" in block:
            errors.append(f"{skill_md}: generated interface block must not expose raw scripts")

        for interface_id in visible_ids:
            expected = (
                f"dispatcher --caller-skill {skill_name} {skill_name}.machine.{interface_id}"
                if _uses_new_interface_model(blueprint)
                else f"dispatcher --caller-skill {skill_name} {skill_name} {interface_id}"
            )
            if expected not in block:
                errors.append(
                    f"{skill_md}: generated interface block is missing dispatcher command for `{interface_id}`"
                )

    return errors


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[3])
    if errors:
        print("error: invalid SKILL.md dispatcher exposure.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
