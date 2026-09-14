"""Enforce canonical Python-side use of the shared dispatcher package."""
from __future__ import annotations

import re
import sys
from pathlib import Path


_DISPATCHER_CLI_RE = re.compile(r"\bdispatcher\b[^\n]*\s--caller-skill\b")


def _python_files(skill_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for subdir in ("_rtx", "bin"):
        root = skill_dir / subdir
        if not root.is_dir():
            continue
        tests_root = root / "tests"
        paths.extend(
            path
            for path in root.rglob("*.py")
            if path.is_file() and not path.is_relative_to(tests_root)
        )
    return paths


def validate(repo_root: Path, validation_paths: tuple[str, ...] | None = None) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors

    blueprint_paths = (sorted(skills_root.glob("*/blueprint.yaml")) if validation_paths is None else
                       sorted({repo_root / Path(path).parts[0] / Path(path).parts[1] / "blueprint.yaml"
                               for path in validation_paths if len(Path(path).parts) >= 4
                               and Path(path).parts[0] == "skills"}))
    for blueprint_path in blueprint_paths:
        if not blueprint_path.is_file():
            continue
        skill_dir = blueprint_path.parent
        paths = (_python_files(skill_dir) if validation_paths is None else
                 [repo_root / path for path in validation_paths
                  if (repo_root / path).is_relative_to(skill_dir)
                  and len(Path(path).parts) >= 4 and Path(path).parts[2] in {"_rtx", "bin"}
                  and Path(path).suffix == ".py" and Path(path).parts[3] != "tests"])
        for path in paths:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue

            for lineno, line in enumerate(lines, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                rel = path.relative_to(repo_root)

                if (
                    "invoke_skill_export.py" in line
                    or "scripts/dispatcher.py" in line
                    or _DISPATCHER_CLI_RE.search(line)
                    or '"dispatcher"' in line
                    or "'dispatcher'" in line
                ):
                    errors.append(
                        f"{rel}:{lineno}: Python skill code must use declared DispatchCall entries "
                        "and PythonMachineInterface.dispatch(), not the dispatcher CLI"
                    )

                if "from officina.dispatcher" in line or "import officina.dispatcher" in line:
                    errors.append(
                        f"{rel}:{lineno}: Python skill code must use declared DispatchCall entries "
                        "and PythonMachineInterface.dispatch(), not raw officina.dispatcher"
                    )

                if "sys.path" in line and ("officina" in line or "/src" in line):
                    errors.append(
                        f"{rel}:{lineno}: do not modify sys.path to reach officina.dispatcher; "
                        "import it normally"
                    )

    return errors


def test_dispatcher_usage(repo_root, validation_paths):
    """Validate selected Python subjects without scanning sibling implementations."""
    return validate(repo_root, validation_paths)


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[2])
    if errors:
        print("error: invalid Python dispatcher usage.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
