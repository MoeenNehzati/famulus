#!/usr/bin/env python3
"""Scaffold blueprint.yaml for every local skill that does not have one yet."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"

CATEGORY_OVERRIDES: dict[str, list[str]] = {
    "prepare-handoff": ["workflow"],
    "initialize-tdd": ["automation"],
    "my-writing-skills": ["workflow"],
    "proof-audit": ["mathematical-analysis"],
    "refactor-skills": ["workflow"],
}


def skill_names() -> list[str]:
    return sorted(path.name for path in SKILLS_ROOT.iterdir() if path.is_dir())


def parse_categories(skill_name: str, text: str) -> list[str]:
    categories: list[str] = []
    for match in re.finditer(r"^Category:\s*(.+?)\s*$", text, re.MULTILINE):
        value = match.group(1)
        if value not in categories:
            categories.append(value)
    if categories:
        return categories
    if skill_name in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[skill_name]
    raise ValueError(f"{skill_name}: could not infer category")


def parse_dependencies(skill_dir: Path, local_skills: set[str]) -> list[tuple[str, bool]]:
    path = skill_dir / "depends_on_skills"
    if not path.exists():
        return []
    deps: list[tuple[str, bool]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = re.sub(r"\s+#.*$", "", raw_line).strip()
        if not line:
            continue
        deps.append((line, line in local_skills))
    return deps


def yaml_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return json.dumps(key)


def parse_permissions(skill_dir: Path) -> tuple[list[list[str]], list[tuple[str, list[str] | None]]]:
    path = skill_dir / "permissions.json"
    if not path.exists():
        return [], []
    data = json.loads(path.read_text(encoding="utf-8"))

    bash_commands: list[list[str]] = []
    for entry in data.get("bash", []):
        match = re.fullmatch(r"Bash\((.*):\*\)", entry)
        if not match:
            continue
        bash_commands.append(shlex.split(match.group(1)))

    network_entries: list[tuple[str, list[str] | None]] = []
    for entry in data.get("network", []):
        if entry == "WebSearch":
            network_entries.append(("web_search", None))
            continue
        match = re.fullmatch(r"WebFetch\(https://([^/]+)/\*\)", entry)
        if match:
            network_entries.append(("web_fetch", [match.group(1)]))
    return bash_commands, network_entries


def collect_scripts(skill_dir: Path) -> list[Path]:
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.exists():
        return []
    return sorted(
        path
        for path in scripts_dir.rglob("*")
        if path.is_file() and path.suffix in {".py", ".sh"}
    )


def interface_name_for_script(script: Path, used: set[str]) -> str:
    rel = script.relative_to(script.parents[1])
    stem = rel.with_suffix("").as_posix()
    name = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()
    if not name:
        name = "script"
    candidate = name
    suffix = 2
    while candidate in used:
        candidate = f"{name}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def format_categories(categories: list[str]) -> str:
    if len(categories) == 1:
        return f"category: {categories[0]}\n"
    lines = ["category:"]
    lines.extend(f"  - {category}" for category in categories)
    return "\n".join(lines) + "\n"


def build_blueprint(skill_name: str, skill_dir: Path, local_skills: set[str]) -> str:
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    categories = parse_categories(skill_name, skill_text)
    deps = parse_dependencies(skill_dir, local_skills)
    bash_permissions, network_permissions = parse_permissions(skill_dir)
    scripts = collect_scripts(skill_dir)

    lines: list[str] = [
        f"# Canonical contract for {skill_name}.",
        "# This file was migrated from legacy skill metadata.",
        "# Tighten the placeholder script interfaces as each script contract becomes clearer.",
        "",
        format_categories(categories).rstrip(),
        "",
        "# Bump this major version whenever the exported contract changes in a",
        "# breaking way for dependent skills.",
        "interface_version: 1",
        "",
        "depends_on:",
    ]

    if deps:
        for dep_name, is_local in deps:
            if is_local:
                lines.append(f"  {yaml_key(dep_name)}:")
                lines.append("    major_version: 1")
            else:
                lines.append(f"  {yaml_key(dep_name)}: {{}}")
    else:
        lines.append("  {}")

    lines.extend(
        [
            "",
            "suggested_permissions:",
            "  bash:",
        ]
    )
    if bash_permissions:
        for command in bash_permissions:
            command_json = json.dumps(command)
            lines.append(f"    - command: {command_json}")
            lines.append('      reason: "Migrated from legacy permissions.json."')
    else:
        lines.append("    []")

    lines.append("  network:")
    if network_permissions:
        for kind, domains in network_permissions:
            lines.append(f"    - kind: {kind}")
            if domains is not None:
                domains_json = json.dumps(domains)
                lines.append(f"      domains: {domains_json}")
            lines.append('      reason: "Migrated from legacy permissions.json."')
    else:
        lines.append("    []")

    lines.extend(
        [
            "",
            "skill_interface:",
            "  inputs:",
            '    - "User requests that match the SKILL.md description for this skill."',
            "  outputs:",
            '    - "Responses, artifacts, or side effects described by this skill\'s workflow."',
            "  side_effects:",
            '    - "May invoke local scripts or dependencies as described in SKILL.md."',
            "",
            "script_interfaces:",
        ]
    )

    if scripts:
        used_names: set[str] = set()
        for script in scripts:
            rel = script.relative_to(skill_dir).as_posix()
            interface_name = interface_name_for_script(script, used_names)
            lines.extend(
                [
                    f"  {interface_name}:",
                    "    cwd: skill_root",
                    f'    command: {json.dumps(["python3", rel] if script.suffix == ".py" else [rel])}',
                    "    interface:",
                    "      default:",
                    "        min_positionals: 0",
                    "        allow_extra_positionals: true",
                    "        allow_stdin: false",
                    f'        notes: "Auto-migrated placeholder interface for {rel}; tighten this contract when needed."',
                    "    exported_interface: []",
                ]
            )
    else:
        lines.append("  {}")

    return "\n".join(lines) + "\n"


def main() -> int:
    local_skills = set(skill_names())
    created: list[str] = []
    for skill_name in sorted(local_skills):
        skill_dir = SKILLS_ROOT / skill_name
        blueprint_path = skill_dir / "blueprint.yaml"
        if blueprint_path.exists():
            continue
        blueprint_path.write_text(build_blueprint(skill_name, skill_dir, local_skills), encoding="utf-8")
        created.append(skill_name)

    if created:
        print("Created blueprints:")
        for skill_name in created:
            print(f"- {skill_name}")
    else:
        print("No new blueprints created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
