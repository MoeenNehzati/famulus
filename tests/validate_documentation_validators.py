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


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_skill(repo_root: Path, name: str, domain: str, description: str) -> None:
    skill_dir = repo_root / "skills" / name
    skill_dir.mkdir(parents=True)
    _write(
        skill_dir / "SKILL.md",
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody.\n",
    )
    _write(
        skill_dir / "blueprint.yaml",
        "\n".join(
            [
                "schema_version: 5",
                "node_type: module",
                f"id: {name}",
                "version: 1",
                f"description: {description}",
                "gateway:",
                "  path: SKILL.md",
                "  language: Markdown",
                "content:",
                "- SKILL\\.md",
                "discovery:",
                "  mechanism: skill",
                "  catalog:",
                f"    domain: {domain}",
                "    topics:",
                "    - planning",
                "    visibility: featured",
                "  activated_by:",
                "  - user-request",
                "  persistent_modifier: false",
                "authority:",
                "  owns_filesystem: []",
                "sources: {}",
                "children: {}",
                "namespace_exports: {}",
                "exports: {}",
                "",
            ]
        ),
    )


def _seed_docs(repo_root: Path) -> None:
    _write(
        repo_root / "references/blueprint/config.yaml",
        (REPO_ROOT / "references/blueprint/config.yaml").read_text(
            encoding="utf-8"
        ),
    )
    for schema_name in ("common.schema.json", "module.schema.json"):
        _write(
            repo_root / "references/blueprint" / schema_name,
            (REPO_ROOT / "references/blueprint" / schema_name).read_text(
                encoding="utf-8"
            ),
        )
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
                "### Recommended: plugin install",
                "",
                "[docs/officina/installation.md](docs/officina/installation.md)",
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
                "## Personal Assistance",
                "<!-- BEGIN AUTO-GENERATED DOCS: personal-assistance -->",
                "<!-- END AUTO-GENERATED DOCS: personal-assistance -->",
                "## Assistant Interaction",
                "<!-- BEGIN AUTO-GENERATED DOCS: assistant-interaction -->",
                "<!-- END AUTO-GENERATED DOCS: assistant-interaction -->",
                "",
            ]
        ),
    )
    _write(
        repo_root / "docs/user/research.md",
        "\n".join(
            [
                "# Research",
                "<!-- BEGIN AUTO-GENERATED DOCS: research -->",
                "<!-- END AUTO-GENERATED DOCS: research -->",
                "",
            ]
        ),
    )
    _write(
        repo_root / "docs/user/system.md",
        "\n".join(
            [
                "# System",
                "<!-- BEGIN AUTO-GENERATED DOCS: assistant-operations -->",
                "<!-- END AUTO-GENERATED DOCS: assistant-operations -->",
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
                "python3 skills/skill-maker/_rtx/_blueprint_syncer.py",
                "dispatcher --caller-skill <caller> <callee>.interface.<name> [args...]",
                "python3 validators/runner.py",
                ".githooks/pre-commit",
                "docs/officina/skill-blueprints.md",
                "references/blueprint/schema.json",
                "references/blueprint/template.yaml",
                "docs/officina/scaffolding/README.md",
                "docs/contributors/documentation-system.md",
                "## Software Development",
                "<!-- BEGIN AUTO-GENERATED DOCS: software-development -->",
                "<!-- END AUTO-GENERATED DOCS: software-development -->",
                "## Assistant Development",
                "<!-- BEGIN AUTO-GENERATED DOCS: assistant-development -->",
                "<!-- END AUTO-GENERATED DOCS: assistant-development -->",
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
    _write(repo_root / "docs/officina/scaffolding/README.md", "# Scaffolding\n")
    _write(repo_root / "references/blueprint/README.md", "# Blueprint Reference\n")
    _write(repo_root / "docs/officina/skill-blueprints.md", "# Skill Blueprints\n")
    _write(repo_root / "references/blueprint/schema.json", "{}\n")
    _write(repo_root / "references/blueprint/template.yaml", "discovery: {}\n")


def _make_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path
    _seed_docs(repo_root)
    _make_skill(repo_root, "email-client", "personal-assistance", "Read and send email.")
    _make_skill(repo_root, "daily-plan", "assistant-interaction", "Generate today's plan.")
    _make_skill(repo_root, "math-dependency-graph", "research", "Build a graph for LaTeX results.")
    _make_skill(repo_root, "cloud-files", "assistant-operations", "Read and write bounded cloud files.")
    _make_skill(repo_root, "skill-maker", "assistant-development", "Create new skills.")
    _make_skill(repo_root, "initialize-tdd", "software-development", "Scaffold a coding project.")
    _make_skill(repo_root, "git-workflow", "software-development", "Check branch safety.")
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


def test_readme_validator_rejects_contributor_blueprint_doc(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    readme = repo_root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\n[Blueprints](docs/officina/skill-blueprints.md)\n",
        encoding="utf-8",
    )

    errors = validate_readme(repo_root)

    assert any("docs/officina/skill-blueprints.md" in error for error in errors)
