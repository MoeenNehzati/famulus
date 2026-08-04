"""Load live skill metadata and documentation coverage contracts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml

from officina.common.configured_schema import load_configuration
from officina.common.blueprint_graph import load_module_blueprint

SKILL_INDEX_PATH = Path("docs/skills.md")

GENERAL_DOC = Path("docs/user/general.md")
RESEARCH_DOC = Path("docs/user/research.md")
SYSTEM_DOC = Path("docs/user/system.md")
CONTRIBUTOR_DOC = Path("docs/contributors/README.md")
DOC_SYSTEM_DOC = Path("docs/contributors/documentation-system.md")


@dataclass(frozen=True)
class SkillInfo:
    name: str
    domain: str
    topics: tuple[str, ...]
    visibility: str
    activated_by: tuple[str, ...]
    persistent_modifier: bool
    summary: str
    description: str


@dataclass(frozen=True)
class CatalogVocabulary:
    domains: tuple[str, ...]
    topics: tuple[str, ...]
    visibility: tuple[str, ...]
    activated_by: tuple[str, ...]


@dataclass(frozen=True)
class CoverageBlock:
    doc_path: Path
    domain: str
    heading: str

    @property
    def marker_id(self) -> str:
        return self.domain


COVERAGE_BLOCKS = (
    CoverageBlock(GENERAL_DOC, "personal-assistance", "Personal Assistance"),
    CoverageBlock(GENERAL_DOC, "assistant-interaction", "Assistant Interaction"),
    CoverageBlock(RESEARCH_DOC, "research", "Research"),
    CoverageBlock(SYSTEM_DOC, "assistant-operations", "Assistant Operations"),
    CoverageBlock(CONTRIBUTOR_DOC, "software-development", "Software Development"),
    CoverageBlock(CONTRIBUTOR_DOC, "assistant-development", "Assistant Development"),
)

USER_DOCS = (GENERAL_DOC, RESEARCH_DOC, SYSTEM_DOC)
CONTRIBUTOR_DOCS = (CONTRIBUTOR_DOC, DOC_SYSTEM_DOC)

SUMMARY_OVERRIDES = {
    "bib-audit": "Audit a `.bib` file for validity, style, external metadata, and duplicates",
    "cloud-files": "Bounded read/write of plain files under a configured Google Drive root",
    "daily-plan": "Generate today's plan from calendar, todos, and weather",
    "email-client": "Read, search, and send email across configured accounts",
    "email-triage": "Triage the inbox into todo and triage lists since the last run",
    "fix-bisync": "Diagnose and repair rclone bisync failures",
    "formal-prose-review": "Polish grammar, tone, and concision in technical prose without touching the math",
    "proof-audit": "Audit a proof for soundness, coherence, hidden assumptions, and redundancy",
    "g-calendar": "Read and modify Google Calendar via a local OAuth CLI",
    "get-weather": "Fetch weather for a location, day, or date range",
    "git-workflow": "Branch-safety checks and commit hygiene for any repo",
    "hook-maker": "Design cross-host assistant hooks with one purpose and per-host bindings",
    "initialize-tdd": "Scaffold a staged, approval-gated TDD project",
    "install-assistant-tools": "Install or update launchers, wiring, hooks, and environment on a machine",
    "list-manager": "Manage personal YAML lists in cloud storage",
    "loose-mode": "Broad, fast exploration mode with breadth over certainty",
    "math-dependency-graph": "Extract an assumptions-to-results dependency graph from a LaTeX document",
    "skill-maker": "Author new skills that conform to the repo's skill-writing guideline",
    "pdf-to-markdown": "Convert a research-paper PDF into LLM-readable text",
    "prepare-handoff": "Prepare a clean handoff with workflow and documentation updates",
    "recurring-tasks": "Manage recurring AI jobs through the host's native per-user scheduler",
    "refactor-node": "Refactor whole repository nodes or owned sub-scopes by gateway language",
    "tight-mode": "Rigorous, verified output mode with certainty over speed",
    "tool-applicability": "Check whether a theorem or framework achieves a target in the current setting",
    "update-standards": "Change canonical standards and keep their pinned closures aligned",
    "wrap-up": "Review the day, record completions, and capture follow-up items",
}

_TRIGGER_PREFIXES = [
    r"use whenever the user\s",
    r"use when the user (?:asks|wants|invokes|refers)\s(?:to\s|about\s)?",
    r"use when asked to\s",
    r"use when\s",
    r"use this skill to\s",
    r"use\s",
]


def _frontmatter(skill_md: Path) -> dict[str, object]:
    text = skill_md.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}
    data = yaml.safe_load(match.group(1))
    return data if isinstance(data, dict) else {}


def _summary(description: str) -> str:
    flat = " ".join(description.split())
    sentence = re.split(r"(?<=[.!?])\s", flat, maxsplit=1)[0]
    lowered = sentence.lower()
    for prefix in _TRIGGER_PREFIXES:
        match = re.match(prefix, lowered)
        if match:
            sentence = sentence[match.end():]
            break
    sentence = sentence.strip().rstrip(".")
    if sentence:
        sentence = sentence[0].upper() + sentence[1:]
    return sentence or "No summary available"


def load_catalog_vocabulary(repo_root: Path) -> CatalogVocabulary:
    """Load the configured, centrally validated discovery vocabulary."""
    config_path = repo_root / "references" / "blueprint" / "config.yaml"
    if not config_path.is_file():
        raise ValueError(f"{config_path}: blueprint catalog configuration is missing")
    config = load_configuration(config_path)["blueprint_catalog"]
    return CatalogVocabulary(
        domains=tuple(config["domains"]),
        topics=tuple(config["topics"]),
        visibility=tuple(config["visibility"]),
        activated_by=tuple(config["activated_by"]),
    )


def _configured_value(
    value: object,
    *,
    field: str,
    allowed: tuple[str, ...],
    blueprint_path: Path,
) -> str:
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(allowed)
        raise ValueError(
            f"{blueprint_path}: {field} must identify one configured value; "
            f"choose one of: {choices}"
        )
    return value


def _configured_values(
    value: object,
    *,
    field: str,
    allowed: tuple[str, ...],
    blueprint_path: Path,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or len(set(item for item in value if isinstance(item, str))) != len(value)
        or any(not isinstance(item, str) or item not in allowed for item in value)
    ):
        choices = ", ".join(allowed)
        raise ValueError(
            f"{blueprint_path}: {field} must contain one or more unique configured "
            f"values; choose from: {choices}"
        )
    return tuple(value)


def load_catalog(repo_root: Path) -> list[SkillInfo]:
    """Return live skills from blueprints and SKILL.md frontmatter."""
    vocabulary = load_catalog_vocabulary(repo_root)
    skills: list[SkillInfo] = []
    for blueprint_path in sorted((repo_root / "skills").glob("*/blueprint.yaml")):
        skill_dir = blueprint_path.parent
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8")) or {}
        discovery = blueprint.get("discovery")
        catalog = discovery.get("catalog") if isinstance(discovery, dict) else None
        if not isinstance(catalog, dict):
            raise ValueError(f"{blueprint_path}: missing discovery.catalog")
        domain = _configured_value(
            catalog.get("domain"),
            field="discovery.catalog.domain",
            allowed=vocabulary.domains,
            blueprint_path=blueprint_path,
        )
        topics = _configured_values(
            catalog.get("topics"),
            field="discovery.catalog.topics",
            allowed=vocabulary.topics,
            blueprint_path=blueprint_path,
        )
        visibility = _configured_value(
            catalog.get("visibility"),
            field="discovery.catalog.visibility",
            allowed=vocabulary.visibility,
            blueprint_path=blueprint_path,
        )
        activated_by = _configured_values(
            discovery.get("activated_by"),
            field="discovery.activated_by",
            allowed=vocabulary.activated_by,
            blueprint_path=blueprint_path,
        )
        persistent_modifier = discovery.get("persistent_modifier")
        if not isinstance(persistent_modifier, bool):
            raise ValueError(
                f"{blueprint_path}: discovery.persistent_modifier must be boolean"
            )
        if persistent_modifier and "reasoning-control" not in topics:
            raise ValueError(
                f"{blueprint_path}: persistent modifiers must include the "
                "reasoning-control topic"
            )
        load_module_blueprint(
            repo_root,
            skill_dir,
            schema_root=repo_root / "references" / "blueprint",
            expected_schema_version=5,
        )
        description = str(_frontmatter(skill_md).get("description", "")).strip()
        summary = SUMMARY_OVERRIDES.get(skill_dir.name) or _summary(description)
        skills.append(
            SkillInfo(
                name=skill_dir.name,
                domain=domain,
                topics=topics,
                visibility=visibility,
                activated_by=activated_by,
                persistent_modifier=persistent_modifier,
                summary=summary,
                description=description,
            )
        )
    return skills


def skills_by_domain(
    catalog: list[SkillInfo],
    *,
    include_hidden: bool = False,
) -> dict[str, list[SkillInfo]]:
    grouped: dict[str, list[SkillInfo]] = {}
    for skill in catalog:
        if skill.visibility == "hidden" and not include_hidden:
            continue
        grouped.setdefault(skill.domain, []).append(skill)
    return grouped


def configured_domains(repo_root: Path, catalog: list[SkillInfo]) -> tuple[str, ...]:
    """Return domains in their configured documentation order."""
    del catalog
    return load_catalog_vocabulary(repo_root).domains


def configured_visibilities(repo_root: Path) -> tuple[str, ...]:
    """Return visibility classes in their configured documentation order."""
    return load_catalog_vocabulary(repo_root).visibility
