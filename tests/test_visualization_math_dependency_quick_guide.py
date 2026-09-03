from __future__ import annotations

from officina.visualization.html_renderer.quick_guides.default import DEFAULT_QUICK_GUIDE
from officina.visualization.html_renderer.quick_guides.math_dependency import (
    MATH_DEPENDENCY_QUICK_GUIDE,
)


def test_math_dependency_guide_keeps_the_default_step_order() -> None:
    assert MATH_DEPENDENCY_QUICK_GUIDE.title == DEFAULT_QUICK_GUIDE.title
    assert [step.id for step in MATH_DEPENDENCY_QUICK_GUIDE.steps] == [
        step.id for step in DEFAULT_QUICK_GUIDE.steps
    ]


def test_math_dependency_guide_rewords_only_the_two_domain_steps() -> None:
    assert DEFAULT_QUICK_GUIDE.steps[0].body == (
        "Nodes are items, and arrows and relations show how they connect."
    )
    assert DEFAULT_QUICK_GUIDE.steps[2].body == (
        "Select a result, then use the all-relations Ancestors control to add "
        "prerequisites through currently visible relation types."
    )

    assert MATH_DEPENDENCY_QUICK_GUIDE.steps[0].title == "Read mathematical dependencies"
    assert MATH_DEPENDENCY_QUICK_GUIDE.steps[0].body == (
        "Follow arrows from prerequisites toward the results they support."
    )
    assert MATH_DEPENDENCY_QUICK_GUIDE.steps[2].title == "Trace prerequisites"
    assert MATH_DEPENDENCY_QUICK_GUIDE.steps[2].body == (
        "Select a theorem, then use this control to add its prerequisites."
    )


def test_math_dependency_guide_leaves_the_generic_steps_untouched() -> None:
    reworded = {"read-graph", "trace-ancestors"}
    for derived, default in zip(MATH_DEPENDENCY_QUICK_GUIDE.steps, DEFAULT_QUICK_GUIDE.steps):
        if derived.id in reworded:
            continue
        assert derived == default
