#!/usr/bin/env python3
"""Validate blueprint presence and sync rules for local skills."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"
BLUEPRINT_TEMPLATE = REPO_ROOT / "references" / "blueprint" / "template.yaml"
CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"


def main() -> int:
    errors: list[str] = []

    if not BLUEPRINT_TEMPLATE.exists():
        errors.append(f"{BLUEPRINT_TEMPLATE}: missing blueprint template reference file")

    for skill_dir in sorted(path for path in SKILLS_ROOT.iterdir() if path.is_dir()):
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
        if blueprint_path.exists() and not has_contract:
            errors.append(f"{skill_file}: local skill is missing generated blueprint contract block")

    if errors:
        print("error: invalid blueprint skill layout.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "sync_skill_blueprints.py"), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
