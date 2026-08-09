from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
REPO_SRC = str(REPO_ROOT / "src")
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

from officina.dispatcher.core import resolve_dispatch_metadata


def test_forced_orchestrate_dispatch_pattern_is_unambiguous() -> None:
    metadata = resolve_dispatch_metadata(
        caller_skill="daily-plan",
        target="daily-plan._rtx.interface.orchestrate",
        args=["--forced"],
        repository_config=REPO_ROOT / "officina.toml",
    )

    assert metadata.target == "daily-plan._rtx.interface.orchestrate"
    assert metadata.pattern == "pattern_1"
    assert metadata.command[-1] == "--forced"
