"""Validate blueprint presence and contract-block sync rules for local skills."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SYNC_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_skill_blueprints.py"

CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    blueprint_template = repo_root / "references" / "blueprint" / "template.yaml"

    if not skills_root.is_dir():
        return errors

    if not blueprint_template.exists():
        errors.append(f"{blueprint_template}: missing blueprint template reference file")

    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        skill_file = skill_dir / "SKILL.md"
        blueprint_path = skill_dir / "blueprint.yaml"

        if not skill_file.exists():
            continue
        if not blueprint_path.exists():
            errors.append(f"{skill_dir}: missing blueprint.yaml")
            continue

        text = skill_file.read_text(encoding="utf-8")
        start_count = text.count(CONTRACT_START)
        end_count = text.count(CONTRACT_END)
        has_contract = start_count > 0 or end_count > 0

        if start_count != end_count:
            errors.append(f"{skill_file}: blueprint contract markers are unbalanced")
        if start_count > 1 or end_count > 1:
            errors.append(f"{skill_file}: blueprint contract block must appear at most once")
        if not has_contract:
            errors.append(
                f"{skill_file}: local skill is missing generated blueprint contract block"
            )

    if errors:
        return errors

    result = subprocess.run(
        [sys.executable, str(_SYNC_SCRIPT), "--check"],
        cwd=repo_root,
        capture_output=True,
        text=True,
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
