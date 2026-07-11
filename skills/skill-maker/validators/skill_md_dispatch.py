"""Validate the generated owner-facing SKILL.md interface block uses dispatcher."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from validators.skill_md_body import (  # noqa: E402
    generated_interface_block,
    hand_authored_skill_body,
    strip_fenced_code_blocks,
)


def _expect_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        return {}
    return value


def _interface_ids(blueprint: dict[str, Any], only_visible: bool = False) -> list[str]:
    """Return sorted list of interface IDs from blueprint.

    If only_visible=True, return only interfaces that have a description (those that
    the sync tool will expose in the generated block). Internal interfaces omit description.
    """
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


def _body_for_invocation_check(text: str) -> str:
    """Strip generated blocks and code fences before checking for invocation violations.

    Code fences are excluded because architecture diagrams and directory listings
    may reference runtime paths structurally without being executable invocations.
    Absolute paths under unrelated repo tooling are also excluded via the caller's
    regex.
    """
    return strip_fenced_code_blocks(hand_authored_skill_body(text))


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

        # Generated-block checks only apply when there are visible interfaces
        if not visible_ids:
            continue

        block = generated_interface_block(text)
        if block is None:
            errors.append(f"{skill_md}: missing generated blueprint interface block")
            continue

        if re.search(raw_runtime_pattern, block):
            errors.append(f"{skill_md}: generated interface block must not expose raw runtime files")

        for interface_id in visible_ids:
            expected = f"dispatcher --caller-skill {skill_name} {skill_name}.machine.{interface_id}"
            if expected not in block:
                errors.append(
                    f"{skill_md}: generated interface block is missing dispatcher command for `{interface_id}`"
                )

    return errors


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
