"""Reject direct cross-skill script-path reach-through for blueprint skills."""
from __future__ import annotations

import re
import sys
from pathlib import Path

RUNTIME_SUFFIXES = {".py", ".sh"}


def _is_text_runtime_file(path: Path) -> bool:
    """Return whether a path is a readable runtime-text candidate.

    Intent
    ------
    Restrict boundary scanning to regular Python, shell, and `_cx` runtime files.

    Rationale
    ---------
    Skill documentation and data files cannot execute cross-skill path reach-through,
    so excluding them keeps the validator focused and avoids unnecessary reads.

    Pseudocode
    ----------
    - set is_runtime_file = path is regular and has a runtime suffix or `_cx` ancestor
    - return is_runtime_file

    Wraps
    -----
    - none
    """
    return path.is_file() and (path.suffix in RUNTIME_SUFFIXES or "_cx" in path.parts)


def _compile_direct_runtime_patterns(
    skill_names: list[str],
) -> tuple[re.Pattern[str], ...]:
    """Compile repository-wide matchers for direct private-runtime paths.

    Intent
    ------
    Prepare the three supported cross-skill path forms once for a validation scan.

    Rationale
    ---------
    One alternation over escaped skill names replaces per-line, per-skill pattern
    reconstruction while retaining exact skill-name capture for ordered findings.

    Pseudocode
    ----------
    - set escaped_names = skill names ordered by descending length then name
    - set target_pattern = named capture over escaped_names
    - set direct_patterns = compiled relative and repository-root path forms
    - return direct_patterns

    Wraps
    -----
    - none
    """
    alternatives = "|".join(
        re.escape(name)
        for name in sorted(skill_names, key=lambda name: (-len(name), name))
    )
    target = rf"(?P<skill>{alternatives})"
    return (
        re.compile(rf"(?:^|[^A-Za-z0-9_-])(?:\.\./)+{target}/_(?:rtx|cx)/"),
        re.compile(rf"(?:^|[^A-Za-z0-9_-])skills/{target}/_(?:rtx|cx)/"),
        re.compile(rf"/skills/{target}/_(?:rtx|cx)/"),
    )


def validate(repo_root: Path) -> list[str]:
    """Return direct cross-skill private-runtime path findings.

    Intent
    ------
    Scan blueprint skills for executable text that reaches through another skill's
    `_rtx` or `_cx` boundary instead of using a declared interface.

    Rationale
    ---------
    Repository-wide matcher preparation keeps the scan proportional to runtime text
    rather than multiplying every line by every other skill and every path form.

    Pseudocode
    ----------
    - if skills root is absent:
      - return no findings
    - set direct_patterns = compiled boundary matchers for skill_names
    - for blueprint_path in blueprint skills:
      - for path in runtime files beneath blueprint_path parent:
        - set is_runtime_file = runtime eligibility of path
        - if is_runtime_file:
          - set violations = captured direct paths and guarded sys path mentions
          - set findings = findings plus first alphabetical violation per line
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_text_runtime_file:
      why:
        computes: "Selects regular executable-text candidates beneath each blueprint skill."

    InstantiationsFromRepo
    ----------------------
    ._compile_direct_runtime_patterns:
      why:
        constructs: "Builds the three matchers reused throughout the repository scan."
    """
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors

    skill_names = sorted(path.name for path in skills_root.iterdir() if path.is_dir())
    blueprint_skills = sorted(skills_root.glob("*/blueprint.yaml"))
    direct_patterns = _compile_direct_runtime_patterns(skill_names)

    for blueprint_path in blueprint_skills:
        skill_dir = blueprint_path.parent
        skill_name = skill_dir.name
        other_skills = [name for name in skill_names if name != skill_name]
        script_files = [path for path in skill_dir.rglob("*") if _is_text_runtime_file(path)]

        for path in script_files:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue

            for lineno, line in enumerate(lines, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                direct_targets = {
                    match.group("skill")
                    for pattern in direct_patterns
                    for match in pattern.finditer(line)
                    if match.group("skill") != skill_name
                }
                sys_path_targets = (
                    {
                        other_skill
                        for other_skill in other_skills
                        if other_skill in line
                    }
                    if "skills" in line
                    and "_rtx" in line
                    and "sys.path.insert" in line
                    else set()
                )
                for other_skill in other_skills:
                    if other_skill in direct_targets:
                        rel = path.relative_to(repo_root)
                        errors.append(
                            f"{rel}:{lineno}: direct cross-skill runtime path to "
                            f"{other_skill} is forbidden"
                        )
                        break

                    if other_skill in sys_path_targets:
                        rel = path.relative_to(repo_root)
                        errors.append(
                            f"{rel}:{lineno}: cross-skill sys.path insertion to "
                            f"{other_skill} is forbidden"
                        )
                        break

    return errors


def main() -> int:
    """Run boundary validation from the repository-oriented command line.

    Intent
    ------
    Convert repository boundary findings into the validator's stderr and exit-code
    protocol for direct script invocation.

    Rationale
    ---------
    Keeping CLI rendering outside `validate` preserves its deterministic list-return
    contract for pytest and consolidated repository-check callers.

    Pseudocode
    ----------
    - set errors = repository boundary findings
    - if errors are present:
      - return failure status
    - return success status

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .validate:
      why:
        constructs: "Builds the ordered findings rendered by the standalone command."
    """
    errors = validate(Path(__file__).resolve().parents[2])
    if errors:
        print("error: invalid cross-skill boundary usage.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
