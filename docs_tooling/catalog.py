"""Load live skill metadata and documentation coverage contracts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml

from officina.configuration.configured_schema import load_configuration
from officina.blueprints.graph import prepare_module_blueprint_loader

SKILL_INDEX_PATH = Path("docs/skills.md")

PERSONAL_ASSISTANCE_DOC = Path("docs/domains/personal-assistance.md")
ASSISTANT_INTERACTION_DOC = Path("docs/domains/assistant-interaction.md")
RESEARCH_DOC = Path("docs/domains/research.md")
ASSISTANT_OPERATIONS_DOC = Path("docs/domains/assistant-operations.md")
CONTRIBUTOR_DOC = Path("docs/contributors/README.md")
DOC_SYSTEM_DOC = Path("docs/contributors/documentation-system.md")


@dataclass(frozen=True)
class SkillInfo:
    """Store skillinfo state.

    Intent
    ------
    Keep name, domain, topics, visibility, activated_by together as a SkillInfo contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry name, domain, topics, visibility, activated_by across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set skillinfo_contract = declared fields and invariants

    Wraps
    -----
    - none
    """
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
    """Store catalogvocabulary state.

    Intent
    ------
    Keep domains, topics, visibility, activated_by together as a CatalogVocabulary contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry domains, topics, visibility, activated_by across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set catalogvocabulary_contract = declared fields and invariants

    Wraps
    -----
    - none
    """
    domains: tuple[str, ...]
    topics: tuple[str, ...]
    visibility: tuple[str, ...]
    activated_by: tuple[str, ...]


@dataclass(frozen=True)
class CoverageBlock:
    """Store coverageblock state.

    Intent
    ------
    Keep doc_path, domain, heading together as a CoverageBlock contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry doc_path, domain, heading across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set coverageblock_contract = declared fields and invariants

    Wraps
    -----
    - none
    """
    doc_path: Path
    domain: str
    heading: str

    @property
    def marker_id(self) -> str:
        """Transform declared fields into the marker id result used by the blueprint graph.

        Intent
        ------
        Use declared fields to transform declared fields into the marker id result used by the blueprint graph.

        Rationale
        ---------
        The operation combines declared fields through local state and an explicit return value, making the resulting marker id behavior explicit across 0 conditional branches.

        Pseudocode
        ----------
        - set marker_id_inputs = declared fields
        - return marker id value

        Wraps
        -----
        - none
        """
        return self.domain


COVERAGE_BLOCKS = (
    CoverageBlock(
        PERSONAL_ASSISTANCE_DOC,
        "personal-assistance",
        "Personal Assistance",
    ),
    CoverageBlock(
        ASSISTANT_INTERACTION_DOC,
        "assistant-interaction",
        "Assistant Interaction",
    ),
    CoverageBlock(RESEARCH_DOC, "research", "Research"),
    CoverageBlock(
        ASSISTANT_OPERATIONS_DOC,
        "assistant-operations",
        "Assistant Operations",
    ),
    CoverageBlock(CONTRIBUTOR_DOC, "software-development", "Software Development"),
    CoverageBlock(CONTRIBUTOR_DOC, "assistant-development", "Assistant Development"),
)

DOMAIN_DOCS = (
    PERSONAL_ASSISTANCE_DOC,
    ASSISTANT_INTERACTION_DOC,
    RESEARCH_DOC,
    ASSISTANT_OPERATIONS_DOC,
)
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
    "wrap-up": "Review the day, update plans and lists, and find handoff candidates via find-handoff-candidates",
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
    """Transform skill md into the frontmatter result used by the blueprint graph.

    Intent
    ------
    Use skill md to transform skill md into the frontmatter result used by the blueprint graph.

    Rationale
    ---------
    The operation combines skill md through read_text, match, safe_load and an explicit return value, making the resulting frontmatter behavior explicit across 1 conditional branches.

    Pseudocode
    ----------
    - set frontmatter_inputs = skill md
    - return frontmatter value

    Wraps
    -----
    - none
    """
    text = skill_md.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}
    data = yaml.safe_load(match.group(1))
    return data if isinstance(data, dict) else {}


def _summary(description: str) -> str:
    """Transform description into the summary result used by the blueprint graph.

    Intent
    ------
    Use description to transform description into the summary result used by the blueprint graph.

    Rationale
    ---------
    The operation combines description through join, lower, rstrip and ordered iteration, an explicit return value, making the resulting summary behavior explicit across 2 conditional branches.

    Pseudocode
    ----------
    - set summary_inputs = description
    - for item in summary_inputs:
      - set validated_item = item
    - return summary value

    Wraps
    -----
    - none
    """
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
    """Load the configured, centrally validated discovery vocabulary.

    Intent
    ------
    Use repo root to load the configured, centrally validated discovery vocabulary.

    Rationale
    ---------
    The operation combines repo root through CatalogVocabulary, is_file, ValueError and bounded failure checks, an explicit return value, making the resulting load catalog vocabulary behavior explicit across 1 conditional branches.

    Pseudocode
    ----------
    - set load_catalog_vocabulary_inputs = repo root
    - if load_catalog_vocabulary_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return load catalog vocabulary value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .officina.configuration.configured_schema.load_configuration:
      why:
        computes: "Supplies dependency position 1, load configuration, while transforming repo root into the load catalog vocabulary value."

    InstantiationsFromRepo
    ----------------------
    .CatalogVocabulary:
      why:
        constructs: "Supplies dependency position 1, CatalogVocabulary, while transforming repo root into the load catalog vocabulary value."
    """
    config_path = repo_root / "references" / "blueprint-schema" / "config.yaml"
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
    """Transform value, field, allowed, blueprint path into the configured value result used by the blueprint graph.

    Intent
    ------
    Use value, field, allowed, blueprint path to transform value, field, allowed, blueprint path into the configured value result used by the blueprint graph.

    Rationale
    ---------
    The operation combines value, field, allowed, blueprint path through join, ValueError, isinstance and bounded failure checks, an explicit return value, making the resulting configured value behavior explicit across 1 conditional branches.

    Pseudocode
    ----------
    - set configured_value_inputs = value, field, allowed, blueprint path
    - if configured_value_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return configured value value

    Wraps
    -----
    - none
    """
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
    """Transform value, field, allowed, blueprint path into the configured values result used by the blueprint graph.

    Intent
    ------
    Use value, field, allowed, blueprint path to transform value, field, allowed, blueprint path into the configured values result used by the blueprint graph.

    Rationale
    ---------
    The operation combines value, field, allowed, blueprint path through tuple, any, join and bounded failure checks, an explicit return value, making the resulting configured values behavior explicit across 1 conditional branches.

    Pseudocode
    ----------
    - set configured_values_inputs = value, field, allowed, blueprint path
    - if configured_values_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return configured values value

    Wraps
    -----
    - none
    """
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
    """Return live skills from blueprints and SKILL.md frontmatter.

    Intent
    ------
    Use repo root to return live skills from blueprints and skill.md frontmatter.

    Rationale
    ---------
    The operation combines repo root through load_catalog_vocabulary, prepare_module_blueprint_loader, sorted and ordered iteration, bounded failure checks, an explicit return value, making the resulting load catalog behavior explicit across 4 conditional branches.

    Pseudocode
    ----------
    - set load_catalog_inputs = repo root
    - if load_catalog_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in load_catalog_inputs:
      - set validated_item = item
    - return load catalog value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._summary:
      why:
        computes: "Supplies dependency position 1,  summary, while transforming repo root into the load catalog value."
    ._frontmatter:
      why:
        computes: "Supplies dependency position 2,  frontmatter, while transforming repo root into the load catalog value."

    InstantiationsFromRepo
    ----------------------
    .officina.blueprints.graph.prepare_module_blueprint_loader:
      why:
        constructs: "Supplies dependency position 1, prepare module blueprint loader, while transforming repo root into the load catalog value."
    ._configured_value:
      why:
        constructs: "Supplies dependency position 2,  configured value, while transforming repo root into the load catalog value."
    .SkillInfo:
      why:
        constructs: "Supplies dependency position 3, SkillInfo, while transforming repo root into the load catalog value."
    .load_catalog_vocabulary:
      why:
        constructs: "Supplies dependency position 4, load catalog vocabulary, while transforming repo root into the load catalog value."
    ._configured_values:
      why:
        constructs: "Supplies dependency position 5,  configured values, while transforming repo root into the load catalog value."
    """
    vocabulary = load_catalog_vocabulary(repo_root)
    validate_blueprint = prepare_module_blueprint_loader(
        repo_root,
        schema_root=repo_root / "references" / "blueprint-schema",
        expected_schema_version=6,
    )
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
        validate_blueprint(skill_dir)
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
    """Transform catalog, include hidden into the skills by domain result used by the blueprint graph.

    Intent
    ------
    Use catalog, include hidden to transform catalog, include hidden into the skills by domain result used by the blueprint graph.

    Rationale
    ---------
    The operation combines catalog, include hidden through append, setdefault and ordered iteration, an explicit return value, making the resulting skills by domain behavior explicit across 1 conditional branches.

    Pseudocode
    ----------
    - set skills_by_domain_inputs = catalog, include hidden
    - for item in skills_by_domain_inputs:
      - set validated_item = item
    - return skills by domain value

    Wraps
    -----
    - none
    """
    grouped: dict[str, list[SkillInfo]] = {}
    for skill in catalog:
        if skill.visibility == "hidden" and not include_hidden:
            continue
        grouped.setdefault(skill.domain, []).append(skill)
    return grouped


def configured_domains(repo_root: Path, catalog: list[SkillInfo]) -> tuple[str, ...]:
    """Return domains in their configured documentation order.

    Intent
    ------
    Use repo root, catalog to return domains in their configured documentation order.

    Rationale
    ---------
    The operation combines repo root, catalog through load_catalog_vocabulary and an explicit return value, making the resulting configured domains behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set configured_domains_inputs = repo root, catalog
    - return configured domains value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .load_catalog_vocabulary:
      why:
        computes: "Supplies dependency position 1, load catalog vocabulary, while transforming repo root, catalog into the configured domains value."
    """
    del catalog
    return load_catalog_vocabulary(repo_root).domains


def configured_visibilities(repo_root: Path) -> tuple[str, ...]:
    """Return visibility classes in their configured documentation order.

    Intent
    ------
    Use repo root to return visibility classes in their configured documentation order.

    Rationale
    ---------
    The operation combines repo root through load_catalog_vocabulary and an explicit return value, making the resulting configured visibilities behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set configured_visibilities_inputs = repo root
    - return configured visibilities value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .load_catalog_vocabulary:
      why:
        computes: "Supplies dependency position 1, load catalog vocabulary, while transforming repo root into the configured visibilities value."
    """
    return load_catalog_vocabulary(repo_root).visibility
