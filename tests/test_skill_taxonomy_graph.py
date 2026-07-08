"""Guard that the taxonomy graph stays in sync with the live skill set.

The README already has a generated taxonomy table, but the graph assets are
separate files with their own render/update flow. This test makes "add a new
skill but forget to update the graph" fail loudly.
"""
from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from graphs.graph_specs import build_specs


def _live_skills() -> set[str]:
    return {
        path.name
        for path in SKILLS_DIR.iterdir()
        if path.is_dir() and not path.name.startswith(".") and (path / "SKILL.md").is_file()
    }


def _graph_labels() -> set[str]:
    spec = build_specs()["skill-taxonomy"]
    labels: set[str] = set()

    def walk(children: list[dict]) -> None:
        for child in children:
            label = child.get("label")
            if isinstance(label, str):
                labels.add(label)
            if child.get("kind") == "group":
                walk(child.get("children", []))

    walk(spec.get("children", []))
    return labels


def test_skill_taxonomy_graph_covers_all_live_skills() -> None:
    live_skills = _live_skills()
    graph_labels = _graph_labels()
    missing = sorted(live_skills - graph_labels)

    assert not missing, (
        "graphs graph spec for skill-taxonomy is missing live skills: "
        + ", ".join(missing)
        + ". Add them to graphs/graph_specs.py and rerender with "
        + "`python3 graphs/render-graphs.py`."
    )


def test_skill_taxonomy_graph_has_no_unknown_skill_labels() -> None:
    live_skills = _live_skills()
    graph_skill_labels = {
        label
        for label in _graph_labels()
        if "/" not in label and " " not in label and "-" in label
    }
    unknown = sorted(graph_skill_labels - live_skills)

    assert not unknown, (
        "skill-taxonomy graph spec has labels that look like skill names but "
        "do not exist under skills/: "
        + ", ".join(unknown)
    )
