from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_SRC = str(REPO_ROOT / "src")
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

from officina.common.certification_view import CertificationDecision
from officina.dispatcher.core import resolve_dispatch_metadata


class _PassingCertificationView:
    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
        source_node_id: str | None,
    ) -> CertificationDecision:
        return CertificationDecision(True, "current", "Current test certificate.")


def test_forced_orchestrate_dispatch_pattern_is_unambiguous() -> None:
    metadata = resolve_dispatch_metadata(
        caller_skill="daily-plan",
        target="daily-plan.interface.orchestrate",
        args=["--forced"],
        repo_root=REPO_ROOT,
        certification_view=_PassingCertificationView(),
    )

    assert metadata.target == "daily-plan.interface.orchestrate"
    assert metadata.pattern == "pattern_1"
    assert metadata.command[-1] == "--forced"
