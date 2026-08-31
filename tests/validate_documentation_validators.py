"""Smoke tests for documentation validators."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docs_tooling import render as docs_render  # noqa: E402
from docs_tooling.render import generate_all  # noqa: E402
from validators.contributor_docs_contract import validate as validate_contributor_docs  # noqa: E402
from validators.generated_skill_docs import validate as validate_skill_docs  # noqa: E402
from validators.readme_user_contract import validate as validate_readme  # noqa: E402
from validators import domain_docs_cover_blueprints as domain_docs_validator  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
CLEAN_README = "\n".join(
    [
        "# Famulus",
        "",
        "Famulus is a cross-host assistant library for personal planning and research work.",
        "",
        "## Quick Start",
        "",
        "### Step 1: install the plugin",
        "",
        "### Step 2: Install the assistant tools",
        "",
        "",
        "https://moeennehzati.github.io/famulus/",
        "",
        "https://github.com/MoeenNehzati/famulus/issues",
        "",
        "[docs/officina/installation.md](docs/officina/installation.md)",
        "",
        "## Featured Workflows",
        "",
        "Plan my day",
        "Wrap up today",
        "Build a math dependency graph",
        "",
        "- [docs/quickstarts/personal-assistance.md](docs/quickstarts/personal-assistance.md)",
        "- [docs/quickstarts/research.md](docs/quickstarts/research.md)",
        "- [docs/quickstarts/development.md](docs/quickstarts/development.md)",
        "- [docs/quickstarts/automation.md](docs/quickstarts/automation.md)",
        "- [docs/quickstarts/skill-development.md](docs/quickstarts/skill-development.md)",
        "- [docs/domains/assistant-interaction.md](docs/domains/assistant-interaction.md)",
        "- [docs/domains/assistant-operations.md](docs/domains/assistant-operations.md)",
        "- [docs/skills.md](docs/skills.md)",
        "- [docs/contributors/README.md](docs/contributors/README.md)",
        "",
    ]
)


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
                "schema_version: 6",
                "node_type: module",
                f"id: {name}",
                "version: 1",
                "maturity: stable",
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
                "installation_tier: core",
                "personal_preference:",
                "  applies: false",
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
        repo_root / "references/blueprint-schema/config.yaml",
        (REPO_ROOT / "references/blueprint-schema/config.yaml").read_text(
            encoding="utf-8"
        ),
    )
    for schema_name in ("common.schema.json", "module.schema.json"):
        _write(
            repo_root / "references/blueprint-schema" / schema_name,
            (REPO_ROOT / "references/blueprint-schema" / schema_name).read_text(
                encoding="utf-8"
            ),
        )
    _write(repo_root / "README.md", CLEAN_README)
    _write(
        repo_root / "docs/quickstarts/personal-assistance.md",
        "# Personal Assistance Quickstart\n",
    )
    _write(repo_root / "docs/quickstarts/research.md", "# Research Quickstart\n")
    _write(
        repo_root / "docs/quickstarts/development.md",
        "# Software Development Quickstart\n",
    )
    _write(repo_root / "docs/quickstarts/automation.md", "# Automation Quickstart\n")
    _write(
        repo_root / "docs/quickstarts/skill-development.md",
        "# Skill Development Quickstart\n",
    )
    _write(
        repo_root / "docs/domains/personal-assistance.md",
        "\n".join(
            [
                "# Personal Assistance",
                "<!-- BEGIN AUTO-GENERATED DOCS: personal-assistance -->",
                "<!-- END AUTO-GENERATED DOCS: personal-assistance -->",
                "",
            ]
        ),
    )
    _write(
        repo_root / "docs/domains/assistant-interaction.md",
        "\n".join(
            [
                "# Assistant Interaction",
                "<!-- BEGIN AUTO-GENERATED DOCS: assistant-interaction -->",
                "<!-- END AUTO-GENERATED DOCS: assistant-interaction -->",
                "",
            ]
        ),
    )
    _write(
        repo_root / "docs/domains/research.md",
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
        repo_root / "docs/domains/assistant-operations.md",
        "\n".join(
            [
                "# Assistant Operations",
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
                '"interface":"skill-maker._rtx.interface.sync-blueprints","version":1',
                '"--check":true',
                '"caller":"<caller>","interface":"<callee>.interface.<name>"',
                "python3 repo_checks.py --suite validators",
                ".githooks/pre-commit",
                "[Blueprints](../officina/blueprints.md)",
                "references/blueprint-schema/schema.json",
                "references/blueprint-schema/template.yaml",
                "[Maintainer Scaffolding](../officina/scaffolding/README.md)",
                "[Documentation System](documentation-system.md)",
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
                "validators/domain_docs_cover_blueprints.py",
                "validators/contributor_docs_contract.py",
                "validators/generated_skill_docs.py",
                "",
            ]
        ),
    )
    _write(repo_root / "docs/officina/scaffolding/README.md", "# Scaffolding\n")
    _write(repo_root / "references/blueprint-schema/README.md", "# Blueprint Reference\n")
    _write(repo_root / "docs/officina/blueprints.md", "# Blueprints\n")
    _write(repo_root / "references/blueprint-schema/schema.json", "{}\n")
    _write(repo_root / "references/blueprint-schema/template.yaml", "discovery: {}\n")


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


def test_documentation_validators_accept_clean_repo_and_reuse_catalog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = _make_repo(tmp_path)
    calls = 0
    real_load_catalog = domain_docs_validator.load_catalog

    def counted_load_catalog(root: Path):
        nonlocal calls
        calls += 1
        return real_load_catalog(root)

    monkeypatch.setattr(
        domain_docs_validator,
        "load_catalog",
        counted_load_catalog,
    )
    monkeypatch.setattr(docs_render, "load_catalog", counted_load_catalog)

    assert validate_readme(repo_root) == []
    assert domain_docs_validator.validate(repo_root) == []
    assert calls == 1
    assert validate_contributor_docs(repo_root) == []
    assert validate_skill_docs(repo_root) == []


def test_readme_validator_reports_distinct_user_contract_violations(
    tmp_path: Path,
) -> None:
    readme = tmp_path / "README.md"
    cases = (
        (
            CLEAN_README.replace("docs/skills.md", "docs/missing.md"),
            ("docs/skills.md",),
        ),
        (
            CLEAN_README
            + "\npython3 <FAMULUS_DIR>/skills/install-assistant-tools/_rtx/_phase_entry.py\n",
            ("_phase_entry.py", "_rtx"),
        ),
        (
            CLEAN_README
            + "\n[Blueprints](docs/officina/blueprints.md)\n",
            ("docs/officina/blueprints.md",),
        ),
        (
            CLEAN_README
            + "\n[Blueprints](docs/officina/skill-blueprints.md)\n",
            ("docs/officina/skill-blueprints.md",),
        ),
    )

    for text, expected_snippets in cases:
        _write(readme, text)
        errors = validate_readme(tmp_path)
        for snippet in expected_snippets:
            assert any(snippet in error for error in errors)
