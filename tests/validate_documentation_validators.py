"""Smoke tests for documentation validators."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docs_tooling.render import generate_all  # noqa: E402
from validators.contributor_docs_contract import validate as validate_contributor_docs  # noqa: E402
from validators.generated_skill_docs import validate as validate_skill_docs  # noqa: E402
from validators.readme_user_contract import validate as validate_readme  # noqa: E402
from validators.user_docs_cover_blueprints import validate as validate_user_docs  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_skill(repo_root: Path, name: str, category: str, description: str) -> None:
    skill_dir = repo_root / "skills" / name
    skill_dir.mkdir(parents=True)
    _write(
        skill_dir / "SKILL.md",
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody.\n",
    )
    _write(skill_dir / "blueprint.yaml", f"category: {category}\n")


def _seed_docs(repo_root: Path) -> None:
    _write(
        repo_root / "README.md",
        "\n".join(
            [
                "# Famulus",
                "",
                "Famulus is a cross-host assistant library for personal planning and research work.",
                "",
                "## Quick Start",
                "",
                "### Recommended: workstation install",
                "",
                "### Alternative: plugin install",
                "",
                "## Featured Workflows",
                "",
                "Plan my day",
                "Wrap up today",
                "Build a math dependency graph",
                "",
                "- [docs/user/general.md](docs/user/general.md)",
                "- [docs/user/research.md](docs/user/research.md)",
                "- [docs/user/system.md](docs/user/system.md)",
                "- [docs/skills.md](docs/skills.md)",
                "- [docs/contributors/README.md](docs/contributors/README.md)",
                "",
            ]
        ),
    )
    _write(
        repo_root / "docs/user/general.md",
        "\n".join(
            [
                "# General",
                "## Productivity",
                "<!-- BEGIN AUTO-GENERATED DOCS: productivity-general-assistant -->",
                "<!-- END AUTO-GENERATED DOCS: productivity-general-assistant -->",
                "## Workflow",
                "<!-- BEGIN AUTO-GENERATED DOCS: workflow-general-assistant -->",
                "<!-- END AUTO-GENERATED DOCS: workflow-general-assistant -->",
                "",
            ]
        ),
    )
    _write(
        repo_root / "docs/user/research.md",
        "\n".join(
            [
                "# Research",
                "<!-- BEGIN AUTO-GENERATED DOCS: research-assistant -->",
                "<!-- END AUTO-GENERATED DOCS: research-assistant -->",
                "",
            ]
        ),
    )
    _write(
        repo_root / "docs/user/system.md",
        "\n".join(
            [
                "# System",
                "<!-- BEGIN AUTO-GENERATED DOCS: system-assistant -->",
                "<!-- END AUTO-GENERATED DOCS: system-assistant -->",
                "",
            ]
        ),
    )
    _write(
        repo_root / "docs/contributors/README.md",
        "\n".join(
            [
                "# Contributor Guide",
                "blueprint.yaml",
                "python3 skills/skill-maker/scripts/sync_skill_blueprints.py",
                "dispatcher --caller-skill <caller> <callee> <interface-id> [args...]",
                "python3 validators/runner.py",
                ".githooks/pre-commit",
                "references/blueprint/guide.md",
                "references/blueprint/schema.json",
                "references/blueprint/template.yaml",
                "docs/scaffolding/README.md",
                "docs/contributors/documentation-system.md",
                "## Skill Making",
                "<!-- BEGIN AUTO-GENERATED DOCS: skill-making-development-assistant -->",
                "<!-- END AUTO-GENERATED DOCS: skill-making-development-assistant -->",
                "## Coding",
                "<!-- BEGIN AUTO-GENERATED DOCS: coding-development-assistant -->",
                "<!-- END AUTO-GENERATED DOCS: coding-development-assistant -->",
                "## Development",
                "<!-- BEGIN AUTO-GENERATED DOCS: development-assistant -->",
                "<!-- END AUTO-GENERATED DOCS: development-assistant -->",
                "",
            ]
        ),
    )
    _write(
        repo_root / "docs/contributors/documentation-system.md",
        "\n".join(
            [
                "# Documentation System",
                "docs_tooling/",
                "python3 scripts/generate-doc-artifacts.py",
                "validators/readme_user_contract.py",
                "validators/user_docs_cover_blueprints.py",
                "validators/contributor_docs_contract.py",
                "validators/generated_skill_docs.py",
                "",
            ]
        ),
    )
    _write(repo_root / "docs/scaffolding/README.md", "# Scaffolding\n")
    _write(repo_root / "references/blueprint/README.md", "# Blueprint Reference\n")
    _write(repo_root / "references/blueprint/guide.md", "# Guide\n")
    _write(repo_root / "references/blueprint/schema.json", "{}\n")
    _write(repo_root / "references/blueprint/template.yaml", "category: example\n")


def _make_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path
    _seed_docs(repo_root)
    _make_skill(repo_root, "email-client", "productivity-general-assistant", "Read and send email.")
    _make_skill(repo_root, "daily-plan", "workflow-general-assistant", "Generate today's plan.")
    _make_skill(repo_root, "math-dependency-graph", "research-assistant", "Build a graph for LaTeX results.")
    _make_skill(repo_root, "cloud-files", "system-assistant", "Read and write bounded cloud files.")
    _make_skill(repo_root, "skill-maker", "skill-making-development-assistant", "Create new skills.")
    _make_skill(repo_root, "initialize-tdd", "coding-development-assistant", "Scaffold a coding project.")
    _make_skill(repo_root, "git-workflow", "development-assistant", "Check branch safety.")
    generate_all(repo_root)
    return repo_root


def test_documentation_validators_accept_clean_repo(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    assert validate_readme(repo_root) == []
    assert validate_user_docs(repo_root) == []
    assert validate_contributor_docs(repo_root) == []
    assert validate_skill_docs(repo_root) == []


def test_readme_validator_flags_missing_skill_index_link(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    readme = repo_root / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace("docs/skills.md", "docs/missing.md"), encoding="utf-8")
    errors = validate_readme(repo_root)
    assert any("docs/skills.md" in error for error in errors)
