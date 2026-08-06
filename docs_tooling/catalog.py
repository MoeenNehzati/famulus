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
    """Immutable discovery metadata for one cataloged skill."""

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
    """Configured discovery values accepted by the documentation catalog."""

    domains: tuple[str, ...]
    topics: tuple[str, ...]
    visibility: tuple[str, ...]
    activated_by: tuple[str, ...]


@dataclass(frozen=True)
class CoverageBlock:
    """Stored documentation path, catalog domain, and available heading label.

    Intent
    ------
    Keep the path and domain used by coverage consumers together with the heading
    label retained in the coverage configuration.

    Rationale
    ---------
    Renderers and validators currently select blocks by path and domain; the heading
    remains available metadata rather than controlling generated coverage behavior.

    Pseudocode
    ----------
    - set coverage_block_contract = document path, domain, and heading

    Wraps
    -----
    - none
    """

    doc_path: Path
    domain: str
    heading: str

    @property
    def marker_id(self) -> str:
        """Return the domain token delimiting this generated coverage block.

        Intent
        ------
        Expose the catalog domain as the stable identifier embedded in generated
        documentation markers.

        Rationale
        ---------
        Reusing the domain keeps coverage selection and marker replacement on one
        identifier instead of maintaining a second synchronization key.

        Pseudocode
        ----------
        - return self.domain

        Wraps
        -----
        - none
        """
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
    "recurring-tasks": "Manage AI-driven recurring jobs as systemd user timers with health checks",
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
    """Parse a leading YAML frontmatter mapping from a skill gateway.

    Intent
    ------
    Return structured frontmatter only when the gateway begins with a complete
    delimiter block and the decoded payload is a mapping.

    Rationale
    ---------
    Missing frontmatter and scalar YAML are ordinary absence cases, while file-read
    and YAML syntax failures propagate so catalog generation cannot hide bad input.

    Pseudocode
    ----------
    - set source = UTF-8 text from skill_md
    - if source has no leading frontmatter block:
      - return empty mapping
    - set payload = parsed frontmatter YAML
    - if payload is a mapping:
      - return payload
    - return empty mapping

    Wraps
    -----
    - none

    Raises
    ------
    OSError
        The gateway cannot be read.
    UnicodeError
        The gateway is not valid UTF-8 text.
    yaml.YAMLError
        The frontmatter is not valid YAML.
    """
    text = skill_md.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}
    data = yaml.safe_load(match.group(1))
    return data if isinstance(data, dict) else {}


def _summary(description: str) -> str:
    """Derive a standalone catalog summary from a skill description.

    Intent
    ------
    Keep the normalized first sentence, remove one recognized activation prefix,
    and provide a fixed fallback when no descriptive text remains.

    Rationale
    ---------
    Skill descriptions are trigger-oriented, but generated indexes need concise
    reader-facing labels without copying later exclusions or workflow prose.

    Pseudocode
    ----------
    - set sentence = whitespace-normalized first sentence
    - for prefix in trigger prefixes:
      - if prefix matches sentence:
        - set sentence = sentence without the matched prefix
        - break
    - set sentence = sentence without surrounding space or a terminal period
    - if sentence is not empty:
      - set sentence = sentence with an uppercase initial
    - return sentence or the fallback summary

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
    """Load the configured discovery vocabulary in documentation order.

    Intent
    ------
    Read the central blueprint catalog configuration and return its accepted domain,
    topic, visibility, and activation values as immutable ordered tuples.

    Rationale
    ---------
    Catalog validation and rendering must share the configured order; failing before
    construction on a missing or rejected configuration prevents silent defaults.

    Pseudocode
    ----------
    - set config_path = repository blueprint catalog configuration path
    - if config_path is not a file:
      - raise ValueError(config_path)
    - config = load_configuration(config_path)
    - return CatalogVocabulary(configured fields)

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.common.configured_schema.load_configuration:
      why:
        constructs: "Builds the validated configuration mapping whose catalog fields populate the returned vocabulary."
    .CatalogVocabulary:
      why:
        constructs: "Builds the immutable ordered vocabulary returned to catalog validation and rendering callers."

    Raises
    ------
    ValueError
        The catalog configuration is missing or rejected by configured-schema loading.
    """
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
    """Require one scalar value to belong to a configured vocabulary.

    Intent
    ------
    Return a string unchanged only when it is one of the allowed values for the
    named blueprint field.

    Rationale
    ---------
    A field-specific failure with the configured choices makes invalid discovery
    metadata actionable while preserving the canonical spelling on success.

    Pseudocode
    ----------
    - if value is not one allowed string:
      - set choices = comma-separated allowed values
      - raise ValueError(field and choices)
    - return value

    Wraps
    -----
    - none

    Raises
    ------
    ValueError
        The value is not a configured string for the named field.
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
    """Require a nonempty list of unique configured string values.

    Intent
    ------
    Validate collection shape, uniqueness, element types, and membership before
    returning the values as an immutable tuple.

    Rationale
    ---------
    One boundary keeps topic and activation metadata from accepting empty, duplicate,
    unknown, or non-string entries that would make generated discovery views ambiguous.

    Pseudocode
    ----------
    - if value is not a nonempty list of unique allowed strings:
      - set choices = comma-separated allowed values
      - raise ValueError(field and choices)
    - return tuple(value)

    Wraps
    -----
    - none

    Raises
    ------
    ValueError
        The collection is empty, duplicated, mistyped, or outside the vocabulary.
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
    """Build the documentation catalog from top-level skill providers.

    Intent
    ------
    Validate discovery metadata for each top-level skill blueprint that has a
    ``SKILL.md`` gateway, then return its documentation record in lexical path order.

    Rationale
    ---------
    Catalog-specific checks give precise field errors before full blueprint loading;
    gateways that are absent are outside this provider set, while malformed selected
    providers fail instead of disappearing from generated documentation.

    Pseudocode
    ----------
    - for blueprint_path in sorted top-level skill blueprints:
      - if the matching skill gateway is missing:
        - continue
      - set catalog_metadata = parsed discovery fields validated against configured vocabulary
      - if catalog metadata or the persistent modifier contract is invalid:
        - raise ValueError(blueprint_path)
      - @load_module_blueprint(repo_root, skill_dir)
      - frontmatter = _frontmatter(skill_md)
      - set description = stripped frontmatter description
      - if a summary override exists:
        - set summary = configured override
      - else:
        - summary = _summary(description)
      - skill = SkillInfo(catalog_metadata, description, summary)
      - set skills = skills plus skill
    - return skills

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.blueprint_graph.load_module_blueprint:
      why:
        validates: "Checks the complete selected blueprint against the repository schema after catalog-specific validation."

    InstantiationsFromRepo
    ----------------------
    .load_catalog_vocabulary:
      why:
        constructs: "Builds the ordered allowed values used to validate every selected provider's discovery fields."
    ._configured_value:
      why:
        transforms: "Carries a validated scalar domain or visibility spelling into the resulting skill record."
    ._configured_values:
      why:
        transforms: "Carries validated topic or activation tuples into the resulting skill record."
    ._frontmatter:
      why:
        transforms: "Carries the parsed gateway description through normalization into the final skill record."
    ._summary:
      why:
        transforms: "Carries the derived fallback label into the final skill record when no named override exists."
    .SkillInfo:
      why:
        constructs: "Builds each immutable skill record appended to the returned lexical catalog."

    Raises
    ------
    ValueError
        Discovery metadata or persistent-modifier constraints are invalid, or full
        vocabulary loading rejects the repository configuration.
    BlueprintGraphError
        Full blueprint validation rejects a selected provider.
    AttributeError
        A selected blueprint is a truthy YAML value whose root is not a mapping.
    OSError
        A selected blueprint or gateway cannot be read.
    UnicodeError
        A selected blueprint or gateway is not valid UTF-8 text.
    yaml.YAMLError
        A selected blueprint or gateway frontmatter is not valid YAML.
    """
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
    """Group catalog records by domain while optionally excluding hidden skills.

    Intent
    ------
    Preserve catalog order within each domain and omit hidden records unless the
    caller explicitly requests them.

    Rationale
    ---------
    Rendering consumes domain buckets, but visibility filtering belongs at the shared
    grouping boundary so indexes and coverage blocks make the same inclusion decision.

    Pseudocode
    ----------
    - set grouped = empty domain mapping
    - for skill in catalog:
      - if skill is hidden and hidden skills are excluded:
        - continue
      - set domain_skills = existing records for skill.domain plus skill
    - return grouped

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
    """Expose the configured domain sequence that orders documentation sections.

    Intent
    ------
    Expose the central vocabulary's domain order independently of which domains are
    represented in the current catalog.

    Rationale
    ---------
    The catalog argument remains for rendering-call compatibility, but current
    contents must not reorder or suppress configured documentation sections.

    Pseudocode
    ----------
    - set catalog = ignored compatibility argument
    - return vocabulary.domains from the delegated loader

    Wraps
    -----
    .load_catalog_vocabulary -> preprocess: discards catalog and forwards repo_root; postprocess: returns vocabulary.domains; fixed_arguments: none
    """
    del catalog
    return load_catalog_vocabulary(repo_root).domains


def configured_visibilities(repo_root: Path) -> tuple[str, ...]:
    """Project configured visibility precedence for documentation renderers.

    Intent
    ------
    Project the ordered visibility tuple from the central discovery vocabulary for
    documentation renderers.

    Rationale
    ---------
    Reading the shared vocabulary avoids a parallel hardcoded rendering order while
    keeping this accessor's result immutable and narrowly scoped.

    Pseudocode
    ----------
    - return vocabulary.visibility from the delegated loader

    Wraps
    -----
    .load_catalog_vocabulary -> preprocess: forwards repo_root unchanged; postprocess: returns vocabulary.visibility; fixed_arguments: none
    """
    return load_catalog_vocabulary(repo_root).visibility
