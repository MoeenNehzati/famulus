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


# ── run_dispatcher resolves the names call sites actually use ──────────────────
#
# The v6 inventory migration renamed module ids to the `<skill>._rtx` form and
# updated _DISPATCH_KEYS, but every call site still passes the bare skill name.
# That made all eight table entries unreachable, so every daily-plan run failed
# with "unknown declared dispatch" -- for two days, while each run's agent
# reported a different invented cause. These pin both spellings.

import types

import pytest

if __package__ and __package__.count(".") >= 1:
    from .. import _day_model
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import _day_model


class _RecordingDispatch:
    """Stands in for the machine interface, recording the key it was given."""

    def __init__(self):
        self.keys = []

    def dispatch(self, key, **kwargs):
        self.keys.append(key)
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")


@pytest.fixture
def recording(monkeypatch):
    interface = _RecordingDispatch()
    monkeypatch.setattr(_day_model, "_dispatch_interface", interface)
    return interface


@pytest.mark.parametrize("skill", ["cloud-files", "cloud-files._rtx"])
def test_run_dispatcher_accepts_the_bare_skill_name_and_the_module_id(
    recording, skill
):
    _day_model.run_dispatcher(skill, "plans-read", "plans/2026-08-11.md")

    assert recording.keys == ["cloud-plans-read"]


def test_run_dispatcher_still_rejects_an_undeclared_target(recording):
    with pytest.raises(_day_model.PlanError, match="unknown declared dispatch"):
        _day_model.run_dispatcher("cloud-files", "no-such-interface")

    assert recording.keys == []
