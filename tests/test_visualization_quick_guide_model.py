from __future__ import annotations

import pytest

from officina.visualization.html_renderer.quick_guide import (
    QuickGuide,
    QuickGuideStep,
)


def test_quick_guide_rejects_duplicate_ids() -> None:
    steps = (
        QuickGuideStep(id="read", target="#read", title="Read", body="Read"),
        QuickGuideStep(id="inspect", target="#inspect", title="Inspect", body="Inspect"),
        QuickGuideStep(id="read", target="#again", title="Again", body="Again"),
    )

    with pytest.raises(ValueError) as exc_info:
        QuickGuide(title="Guide", steps=steps)
    assert "duplicate step id: read" in str(exc_info.value)


def test_replace_step_unknown_id_raises_key_error() -> None:
    guide = QuickGuide(
        title="Guide",
        steps=(QuickGuideStep(id="read", target="#read", title="Read", body="Read"),),
    )

    with pytest.raises(KeyError) as exc_info:
        guide.replace_step("missing")
    assert exc_info.value.args[0] == "missing"


def test_replace_step_returns_new_guide() -> None:
    original = QuickGuide(
        title="Guide",
        steps=(
            QuickGuideStep(id="a", target="#a", title="A", body="One"),
            QuickGuideStep(id="b", target="#b", title="B", body="Two"),
        ),
    )
    updated = original.replace_step("a", title="Updated")

    assert updated is not original
    assert updated.title == original.title


def test_replace_step_preserves_untouched_steps() -> None:
    step_a = QuickGuideStep(id="a", target="#a", title="A", body="One")
    step_b = QuickGuideStep(id="b", target="#b", title="B", body="Two")
    step_c = QuickGuideStep(id="c", target="#c", title="C", body="Three")
    original = QuickGuide(title="Guide", steps=(step_a, step_b, step_c))

    updated = original.replace_step("b", target="#new")

    assert updated.steps[0] is step_a
    assert updated.steps[2] is step_c
    assert updated.steps[1] is not step_b
    assert updated.steps[1].id == "b"
    assert updated.steps[1].target == "#new"
    assert updated.steps[1].title == "B"
    assert updated.steps[1].body == "Two"


def test_replace_step_does_not_change_id() -> None:
    original = QuickGuide(
        title="Guide",
        steps=(QuickGuideStep(id="a", target="#a", title="A", body="One"),),
    )

    updated = original.replace_step("a", target="#updated", title="Updated", body="Updated")

    assert updated.steps[0].id == original.steps[0].id == "a"
