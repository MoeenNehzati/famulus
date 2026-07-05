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

# Skills with pre-existing body violations that predate this check.
# Each entry needs a follow-up pass to move raw-script invocations into
# blueprint.yaml (description + usage) and strip them from the skill body.
# Do not add new skills here — fix them at the source instead.
_LEGACY_BODY_VIOLATIONS: frozenset[str] = frozenset({
    "bib-audit",
    "cloud-files",
    "daily-plan",
    "email-client",
    "email-triage",
    "g-calendar",
    "get-weather",
    "install-assistant-tools",
    "math-dependency-graph",
    "pdf-to-markdown",
    "recurring-tasks",
})


def _expect_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        return {}
    return value


def _interface_ids(blueprint: dict[str, Any]) -> list[str]:
    interfaces = _expect_mapping(blueprint.get("script_interfaces"))
    result: list[str] = []
    for _name, spec in sorted(interfaces.items()):
        if not isinstance(spec, dict):
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
        interface_ids = _interface_ids(blueprint)
        if not interface_ids:
            continue

        text = skill_md.read_text(encoding="utf-8")

        body = _body_text(text)
        if skill_name not in _LEGACY_BODY_VIOLATIONS:
            if "scripts/" in body:
                errors.append(
                    f"{skill_md}: skill body must not invoke scripts directly; "
                    "reference dispatcher interface names instead"
                )
            if "dispatcher --caller-skill" in body:
                errors.append(
                    f"{skill_md}: skill body must not invoke dispatcher directly; "
                    "interface invocations belong in the generated block (blueprint.yaml owns them)"
                )

        block = _interface_block(text)
        if block is None:
            errors.append(f"{skill_md}: missing generated blueprint interface block")
            continue

        if "scripts/" in block or "python3 scripts/" in block or "python scripts/" in block:
            errors.append(f"{skill_md}: generated interface block must not expose raw scripts")

        for interface_id in interface_ids:
            expected = f"dispatcher --caller-skill {skill_name} {skill_name} {interface_id}"
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
