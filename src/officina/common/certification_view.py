"""Read-only certification boundary consumed by dispatch and projection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping, Protocol


@dataclass(frozen=True)
class CertificationDecision:
    certified: bool
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("certification decisions require nonempty code and message")


@dataclass(frozen=True)
class CurrentCertificate:
    node_id: str
    node_hash: str
    certificate_hash: str
    certified_at: str | None = None


class CertificationView(Protocol):
    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
    ) -> CertificationDecision: ...

    def certificate_for(self, node_id: str) -> CurrentCertificate | None: ...


class CertificateRecordView:
    """Read-only adapter over already verified current certificate records."""

    def __init__(
        self,
        records: Mapping[str, Mapping[str, object]],
        *,
        expected_node_hashes: Mapping[str, str] | None = None,
    ) -> None:
        self._certificates: dict[str, CurrentCertificate] = {}
        expected = expected_node_hashes or {}
        for node_id, record in records.items():
            payload = record.get("payload")
            subject = payload.get("subject") if isinstance(payload, Mapping) else None
            node_hash = payload.get("node_hash") if isinstance(payload, Mapping) else None
            certified_at = payload.get("certified_at") if isinstance(payload, Mapping) else None
            if (
                not isinstance(subject, Mapping)
                or subject.get("id") != node_id
                or not isinstance(node_hash, str)
            ):
                continue
            expected_hash = expected.get(node_id)
            if expected_hash is not None and expected_hash != node_hash:
                continue
            canonical = json.dumps(
                record,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            self._certificates[node_id] = CurrentCertificate(
                node_id=node_id,
                node_hash=node_hash,
                certificate_hash="sha256:" + hashlib.sha256(canonical).hexdigest(),
                certified_at=certified_at if isinstance(certified_at, str) else None,
            )

    def certificate_for(self, node_id: str) -> CurrentCertificate | None:
        return self._certificates.get(node_id)

    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
    ) -> CertificationDecision:
        del interface_version
        certificate = self.certificate_for(module_id)
        if certificate is None:
            return CertificationDecision(
                False,
                "certification-unavailable",
                f"{module_id}: no current certificate for {interface_id}",
            )
        return CertificationDecision(True, "current", "Current certificate.")


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

    def certificate_for(self, node_id: str) -> CurrentCertificate | None:
        del node_id
        return None
