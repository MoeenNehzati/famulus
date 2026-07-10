"""Unit tests for beautify.py's date-badge logic: a finished entry (state in
complete/accepted/rejected) should show its `completed` date instead of its
(by-then-irrelevant) `deadline`, and show nothing if `completed` was never
recorded -- never a misleading "Nd overdue" for something that's done.
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent / "_rtx"
sys.path.insert(0, str(SCRIPTS_DIR))

import _list_beautify as beautify  # noqa: E402


def test_date_badge_shows_deadline_for_open_entry():
    entry = {"state": "incomplete", "deadline": "2026-01-01"}
    assert "2026-01-01" in beautify._date_badge(entry, relative_deadlines=False)


def test_date_badge_shows_completed_for_finished_entry():
    entry = {"state": "complete", "deadline": "2026-01-01", "completed": "2026-02-02"}
    label = beautify._date_badge(entry, relative_deadlines=False)
    assert "completed 2026-02-02" == label
    assert "2026-01-01" not in label  # the old deadline must not leak through


def test_date_badge_empty_for_finished_entry_without_completed():
    # Legacy entries marked done before `completed` existed have nothing
    # recoverable to show -- must not fall back to the misleading deadline.
    entry = {"state": "complete", "deadline": "2026-01-01"}
    assert beautify._date_badge(entry, relative_deadlines=False) == ""


def test_date_badge_applies_to_triage_states_too():
    for state in ("accepted", "rejected"):
        entry = {"state": state, "deadline": "2026-01-01", "completed": "2026-02-02"}
        assert beautify._date_badge(entry, relative_deadlines=False) == "completed 2026-02-02"


def test_date_emoji_badge_uses_checkmark_for_completed():
    entry = {"state": "complete", "completed": "2026-02-02"}
    assert beautify._date_emoji_badge(entry, relative_deadlines=False).startswith("✅")


def test_date_emoji_badge_empty_for_finished_without_completed():
    entry = {"state": "accepted"}
    assert beautify._date_emoji_badge(entry, relative_deadlines=False) == ""


def test_modified_never_rendered(tmp_path, monkeypatch, capsys):
    import subprocess
    yaml_in = (
        "schema: todo\nname: todo\ncategories:\n"
        "- name: Work\n  entries:\n"
        "  - id: aaaaaa\n    title: Task\n    state: complete\n"
        "    created: '2026-01-01'\n    deadline: '2026-01-05'\n"
        "    modified: '2026-06-30'\n    completed: '2026-06-29'\n"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "_list_beautify.py")],
        input=yaml_in, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "2026-06-30" not in result.stdout  # modified must never leak into output
    assert "completed 2026-06-29" in result.stdout
