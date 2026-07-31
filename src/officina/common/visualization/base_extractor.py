"""Shared extractor core for structured graph payload generation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .base_visualizer import GraphSource

Payload = dict[str, Any]


class BaseJsonExtractor(ABC):
    """Minimal interface for extractors that produce graph payload JSON."""

    @abstractmethod
    def extract(self, source: "GraphSource") -> Payload:
        """Return one graph payload object for ``source``."""
        ...


__all__ = ["BaseJsonExtractor", "Payload"]
