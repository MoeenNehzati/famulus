"""Read-only certification boundary consumed by dispatch and projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CertificationDecision:
    certified: bool
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("certification decisions require nonempty code and message")


class CertificationView(Protocol):
    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
    ) -> CertificationDecision: ...


class RejectingCertificationView:
    """Phase-2 production placeholder; Phase 4 supplies the backed view."""

    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
    ) -> CertificationDecision:
        return CertificationDecision(
            False,
            "certification-unavailable",
            "machine-module certification is not available until Phase 4",
        )
