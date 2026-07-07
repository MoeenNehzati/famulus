"""Guards that README.md still documents the blueprint/dispatcher access-control
model: skills declare permissions in blueprint.yaml, sync_skill_blueprints.py
regenerates the SKILL.md contract blocks, the dispatcher is the only sanctioned
route for one skill to call another's script, and the pre-commit hook enforces
all of it.

This checks for the presence of that *substance* via loose, semantic matching
(a heading regex plus a handful of load-bearing facts) rather than pinning
exact prose/heading text — a README section can be reworded or its heading
renamed without losing the information this test cares about, and the test
should only fail when the actual facts (not the wording) go missing.

Repo-level (not skill-scoped) because README.md isn't owned by any one skill;
it's the entry point a human or a fresh agent session reads to understand how
skills are authored and how cross-skill calls are policed.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"

# A heading anywhere that plausibly introduces the blueprint/dispatcher access
# model — deliberately loose (any heading level, either word order, either
# word standing in for its concept) so a rewording/retitling doesn't trip it.
_HEADING_RE = re.compile(r"^#{2,4}.*\b(blueprint|dispatcher)\b", re.IGNORECASE | re.MULTILINE)


def test_readme_has_a_blueprint_or_dispatcher_heading() -> None:
    text = README.read_text(encoding="utf-8")
    assert _HEADING_RE.search(text), (
        "README.md should have a heading introducing the blueprint/dispatcher "
        "access model (heading text is free to change; the section itself "
        "should not disappear)"
    )


def test_readme_documents_the_load_bearing_facts() -> None:
    text = README.read_text(encoding="utf-8")

    assert "dispatcher" in text.lower(), \
        "README.md should mention the dispatcher"
    assert "blueprint" in text.lower(), \
        "README.md should mention blueprint.yaml / the blueprint contract"
    assert "references/blueprint" in text, \
        "README.md should point at references/blueprint (the blueprint contract reference)"
    assert re.search(r"sync_skill_blueprints\.py", text), \
        "README.md should reference sync_skill_blueprints.py (regenerates SKILL.md contract blocks)"
    assert re.search(r"\.githooks\b", text) and re.search(r"pre-commit", text, re.IGNORECASE), \
        "README.md should mention that .githooks/pre-commit enforces this"
