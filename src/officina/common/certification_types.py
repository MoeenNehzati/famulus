"""Lightweight certification value types shared with latency-sensitive callers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CertificationDecision:
    """One advisory certification outcome with a stable code and message."""

    certified: bool
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("certification decisions require nonempty code and message")


__all__ = ["CertificationDecision"]
