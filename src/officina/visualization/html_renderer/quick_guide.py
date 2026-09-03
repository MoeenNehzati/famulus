"""Quick guide data model used by the ELK HTML renderer."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class QuickGuideStep:
    id: str
    target: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class QuickGuide:
    """Collection of ordered quick-guide steps."""

    title: str
    steps: tuple[QuickGuideStep, ...]

    def __post_init__(self) -> None:
        step_ids = {step.id for step in self.steps}
        if len(step_ids) != len(self.steps):
            duplicate_ids = [step.id for step in self.steps]
            for index, step_id in enumerate(duplicate_ids):
                if duplicate_ids.index(step_id) != index:
                    raise ValueError(f"duplicate step id: {step_id}")
            raise ValueError("duplicate step ids")

    def replace_step(
        self,
        step_id: str,
        *,
        target: str | None = None,
        title: str | None = None,
        body: str | None = None,
    ) -> "QuickGuide":
        """Return a new guide with one step replaced by matching step_id."""

        index = -1
        for i, step in enumerate(self.steps):
            if step.id == step_id:
                index = i
                break
        if index == -1:
            raise KeyError(step_id)

        replacement = replace(
            self.steps[index],
            target=target if target is not None else self.steps[index].target,
            title=title if title is not None else self.steps[index].title,
            body=body if body is not None else self.steps[index].body,
        )

        return QuickGuide(
            self.title,
            self.steps[:index] + (replacement,) + self.steps[index + 1 :],
        )


__all__ = ["QuickGuide", "QuickGuideStep"]
