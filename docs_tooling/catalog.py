"""Load live skill metadata and documentation coverage contracts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml

SKILL_INDEX_PATH = Path("docs/skills.md")

GENERAL_DOC = Path("docs/user/general.md")
RESEARCH_DOC = Path("docs/user/research.md")
SYSTEM_DOC = Path("docs/user/system.md")
CONTRIBUTOR_DOC = Path("docs/contributors/README.md")
DOC_SYSTEM_DOC = Path("docs/contributors/documentation-system.md")


@dataclass(frozen=True)
class SkillInfo:
    name: str
    category: str
    summary: str
    description: str


@dataclass(frozen=True)
class CoverageBlock:
    doc_path: Path
    category: str
    heading: str

    @property
    def marker_id(self) -> str:
        return self.category


COVERAGE_BLOCKS = (
    CoverageBlock(GENERAL_DOC, "productivity-general-assistant", "Productivity"),
    CoverageBlock(GENERAL_DOC, "workflow-general-assistant", "Workflow"),
    CoverageBlock(RESEARCH_DOC, "research-assistant", "Research"),
    CoverageBlock(SYSTEM_DOC, "system-assistant", "System"),
    CoverageBlock(CONTRIBUTOR_DOC, "skill-making-development-assistant", "Skill Making"),
    CoverageBlock(CONTRIBUTOR_DOC, "coding-development-assistant", "Coding"),
    CoverageBlock(CONTRIBUTOR_DOC, "development-assistant", "Development"),
)

USER_DOCS = (GENERAL_DOC, RESEARCH_DOC, SYSTEM_DOC)
CONTRIBUTOR_DOCS = (CONTRIBUTOR_DOC, DOC_SYSTEM_DOC)

CATEGORY_DISPLAY = {
    "research-assistant": "Research Assistant",
    "productivity-general-assistant": "Productivity",
    "workflow-general-assistant": "Workflow",
    "skill-making-development-assistant": "Skill Making",
    "coding-development-assistant": "Coding",
    "development-assistant": "Development",
    "system-assistant": "System Assistant",
}

CATEGORY_TREE = (
    ("General Assistant", ("productivity-general-assistant", "workflow-general-assistant")),
    ("Research Assistant", ("research-assistant",)),
    ("System Assistant", ("system-assistant",)),
    (
        "Development Assistant",
        (
            "skill-making-development-assistant",
            "coding-development-assistant",
            "development-assistant",
        ),
    ),
)

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
    "refactor-skills": "Audit and refactor existing skills against local conventions",
    "tight-mode": "Rigorous, verified output mode with certainty over speed",
    "tool-applicability": "Check whether a theorem or framework achieves a target in the current setting",
    "update-skill-guidelines": "Change the skill-writing standard and its mechanical checks in lockstep",
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


def load_catalog(repo_root: Path) -> list[SkillInfo]:
    """Return live skills from blueprints and SKILL.md frontmatter."""
    skills: list[SkillInfo] = []
    for blueprint_path in sorted((repo_root / "skills").glob("*/blueprint.yaml")):
        skill_dir = blueprint_path.parent
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8")) or {}
        category = str(blueprint.get("category", "")).strip()
        description = str(_frontmatter(skill_md).get("description", "")).strip()
        summary = SUMMARY_OVERRIDES.get(skill_dir.name) or _summary(description)
        skills.append(
            SkillInfo(
                name=skill_dir.name,
                category=category,
                summary=summary,
                description=description,
            )
        )
    return skills


def skills_by_category(catalog: list[SkillInfo]) -> dict[str, list[SkillInfo]]:
    grouped: dict[str, list[SkillInfo]] = {}
    for skill in catalog:
        grouped.setdefault(skill.category, []).append(skill)
    return grouped


def is_development_category(category: str) -> bool:
    return category.endswith("development-assistant") or category == "development-assistant"
