"""Enforce canonical Python-side use of the shared dispatcher package."""
from __future__ import annotations

import sys
from pathlib import Path


def _python_files(skill_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for subdir in ("_rtx", "bin"):
        root = skill_dir / subdir
        if not root.is_dir():
            continue
        paths.extend(path for path in root.rglob("*.py") if path.is_file())
    return paths


# Skills exempt from these rules (see skill-guidelines.md, installer-bootstrap
# exception): install-assistant-tools generates and removes the dispatcher
# launcher itself, and must bootstrap officina.dispatcher imports from the repo
# before any launcher exists.
_EXCLUDED_SKILLS = {"install-assistant-tools"}


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors

    for blueprint_path in sorted(skills_root.glob("*/blueprint.yaml")):
        skill_dir = blueprint_path.parent
        if skill_dir.name in _EXCLUDED_SKILLS:
            continue
        for path in _python_files(skill_dir):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue

            for lineno, line in enumerate(lines, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                rel = path.relative_to(repo_root)

                if "invoke_skill_export.py" in line or "scripts/dispatcher.py" in line or '"dispatcher"' in line or "'dispatcher'" in line:
                    errors.append(
                        f"{rel}:{lineno}: Python skill code must use officina.dispatcher.dispatch(), "
                        "not the dispatcher CLI"
                    )

                if "sys.path" in line and ("script_dispatcher" in line or "officina" in line or "/src" in line):
                    errors.append(
                        f"{rel}:{lineno}: do not modify sys.path to reach officina.dispatcher; "
                        "import it normally"
                    )

    return errors


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[3])
    if errors:
        print("error: invalid Python dispatcher usage.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
