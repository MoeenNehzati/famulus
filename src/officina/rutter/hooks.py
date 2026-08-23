"""Pure transition-hook definitions and edge match constructors."""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass
from typing import Callable, Protocol

from officina.rutter.model import (
    EdgeContext,
    JsonObject,
    Rutter,
    RutterDefinitionError,
    _require_id,
)


class _EdgeLike(Protocol):
    source: str
    outcome: str
    target: str | None


@dataclass(frozen=True)
class EdgeMatch:
    source: str | None = None
    outcome: str | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.source, "edge source"),
            (self.outcome, "edge outcome"),
            (self.target, "edge target"),
        ):
            if value is not None:
                _require_id(value, label, RutterDefinitionError)

    def matches(self, edge: _EdgeLike) -> bool:
        return all(
            expected is None or expected == actual
            for expected, actual in (
                (self.source, edge.source),
                (self.outcome, edge.outcome),
                (self.target, edge.target),
            )
        )


@dataclass(frozen=True)
class CaseMaker:
    id: str
    _: KW_ONLY
    on: EdgeMatch
    child: type[Rutter]
    charter: Callable[[EdgeContext], JsonObject | None]

    def __post_init__(self) -> None:
        _require_id(self.id, "CaseMaker", RutterDefinitionError)
        if not isinstance(self.on, EdgeMatch):
            raise RutterDefinitionError("CaseMaker on must be an EdgeMatch")
        if not isinstance(self.child, type) or not issubclass(self.child, Rutter):
            raise RutterDefinitionError("CaseMaker child must be a Rutter class")
        if not callable(self.charter):
            raise RutterDefinitionError("CaseMaker charter must be callable")


def after(source: str) -> EdgeMatch:
    return EdgeMatch(source=source)


def before(target: str) -> EdgeMatch:
    return EdgeMatch(target=target)


def on_edge(
    *,
    source: str | None = None,
    outcome: str | None = None,
    target: str | None = None,
) -> EdgeMatch:
    return EdgeMatch(source=source, outcome=outcome, target=target)


__all__ = ("CaseMaker", "EdgeMatch", "after", "before", "on_edge")
