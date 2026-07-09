#!/usr/bin/env python3
"""Behavior tests for find-handoff-candidates/scripts/scan.py.

Uses a FakeParser implementing the same interface as the real per-host
parsers (id, opaque_field, default_threshold, list_session_files,
extract_project, extract_session_id, resume_hint) so these tests never
need to touch real transcript directories or care which host is which --
scan.py itself is fully generic, and these tests exercise exactly that.
"""
import datetime
import importlib.util
import json
import os
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
SCRIPT = SKILL_DIR / "scripts" / "scan.py"


def _load():
    spec = importlib.util.spec_from_file_location("scan", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_transcript(path, lines):
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


class FakeParser:
    """Minimal stand-in for a real host parser, backed by a fixed file list."""

    def __init__(self, id_, files, opaque_field="opaque_test_field", default_threshold=0):
        self.id = id_
        self._files = files
        self.opaque_field = opaque_field
        self.default_threshold = default_threshold

    def list_session_files(self):
        return [str(p) for p in self._files]

    def extract_project(self, obj):
        return obj.get("cwd")

    def extract_session_id(self, path, first_obj):
        return os.path.splitext(os.path.basename(path))[0]

    def resume_hint(self, session_id):
        return f"resume-hint {session_id}"


def test_sentinel_regex_requires_exact_comment_form():
    mod = _load()
    # Bare words must NOT match -- this is the false-positive case that
    # surfaced during development (prose discussing the mechanism).
    assert mod.COMPLETE_RE.search("we should emit HANDOFF-COMPLETE here") is None
    assert mod.STARTED_RE.search("the HANDOFF-STARTED marker") is None
    # Exact wrapped form must match.
    assert mod.COMPLETE_RE.search("<!-- HANDOFF-SENTINEL: COMPLETE -->") is not None
    assert mod.STARTED_RE.search("<!-- HANDOFF-SENTINEL: STARTED -->") is not None


def test_opaque_len_excludes_only_the_named_field():
    mod = _load()
    obj = {
        "message": {
            "content": [
                {"type": "thinking", "thinking": "short", "signature": "x" * 500},
                {"type": "text", "text": "hello world"},
            ]
        },
        "payload": {"encrypted_content": "y" * 300, "summary": [{"text": "z" * 20}]},
    }
    assert mod.opaque_len(obj, "signature") == 500
    assert mod.opaque_len(obj, "encrypted_content") == 300
    assert mod.opaque_len(obj, "nonexistent_field") == 0


def test_scan_flags_session_with_large_gap_and_no_handoff(tmp_path):
    mod = _load()
    session_file = tmp_path / "abc123.jsonl"
    lines = [
        {
            "cwd": "/some/project",
            "timestamp": f"2026-07-07T10:{i:02d}:00.000Z",
            "text": "x" * 5000,
        }
        for i in range(50)
    ]
    _write_transcript(session_file, lines)
    mtime = datetime.datetime.fromisoformat("2026-07-07T12:00:00").timestamp()
    os.utime(session_file, (mtime, mtime))

    fake = FakeParser("fake-host", [session_file])
    results = mod.scan("2026-07-07", {"fake-host": 10_000}, parsers=[fake])
    assert len(results) == 1
    assert results[0]["handoff_status"] == "none"
    assert results[0]["session_id"] == "abc123"
    assert results[0]["source"] == "fake-host"
    assert results[0]["gap_net_chars"] >= 10_000
    assert results[0]["resume_hint"] == "resume-hint abc123"


def test_scan_resets_gap_after_completed_handoff(tmp_path):
    mod = _load()
    session_file = tmp_path / "def456.jsonl"
    lines = [
        {"cwd": "/some/project", "timestamp": "2026-07-07T10:00:00.000Z", "text": "x" * 5000}
        for _ in range(20)
    ]
    lines.append({
        "cwd": "/some/project",
        "timestamp": "2026-07-07T10:30:00.000Z",
        "text": "<!-- HANDOFF-SENTINEL: STARTED -->",
    })
    lines.append({
        "cwd": "/some/project",
        "timestamp": "2026-07-07T10:45:00.000Z",
        "text": "<!-- HANDOFF-SENTINEL: COMPLETE -->",
    })
    # small amount of new work after the completed handoff -- should not
    # be enough to re-flag at a high threshold
    lines.append({
        "cwd": "/some/project",
        "timestamp": "2026-07-07T10:50:00.000Z",
        "text": "ok thanks",
    })
    _write_transcript(session_file, lines)

    fake = FakeParser("fake-host", [session_file])
    results = mod.scan("2026-07-07", {"fake-host": 10_000}, parsers=[fake])
    # Gap since COMPLETE is tiny (well under 10_000), so nothing flagged.
    assert results == []


def test_scan_flags_new_work_after_completed_handoff(tmp_path):
    mod = _load()
    session_file = tmp_path / "ghi789.jsonl"
    lines = [
        {"cwd": "/some/project", "timestamp": "2026-07-07T10:00:00.000Z", "text": "x" * 5000}
        for _ in range(5)
    ]
    lines.append({
        "cwd": "/some/project",
        "timestamp": "2026-07-07T10:10:00.000Z",
        "text": "<!-- HANDOFF-SENTINEL: COMPLETE -->",
    })
    # substantial new work AFTER the completed handoff
    lines += [
        {"cwd": "/some/project", "timestamp": "2026-07-07T11:00:00.000Z", "text": "y" * 5000}
        for _ in range(10)
    ]
    _write_transcript(session_file, lines)
    mtime = datetime.datetime.fromisoformat("2026-07-07T12:00:00").timestamp()
    os.utime(session_file, (mtime, mtime))

    fake = FakeParser("fake-host", [session_file])
    results = mod.scan("2026-07-07", {"fake-host": 10_000}, parsers=[fake])
    assert len(results) == 1
    # Even though status is "complete" overall history, the post-handoff gap
    # is large -- this reflects real unhandled-off follow-up work.
    assert results[0]["handoff_status"] == "complete"
    assert results[0]["gap_net_chars"] >= 10_000


def test_scan_filters_by_mtime_not_by_directory_naming(tmp_path):
    """Regression test: a host's directory layout may bucket files by
    CREATION date (e.g. a YYYY/MM/DD path) while the file keeps being
    appended to (mtime advances) on later days. scan()'s own mtime check
    must be what decides inclusion, not anything about the path/filename --
    discovered by comparing real Codex session data, where 40+ real files
    had mtime dates after their path's date component."""
    mod = _load()
    # File "lives" in a directory suggesting 2026-07-05, but its content
    # and (crucially) its mtime reflect 2026-07-07 activity.
    nested_dir = tmp_path / "2026" / "07" / "05"
    nested_dir.mkdir(parents=True)
    session_file = nested_dir / "session-abcdef.jsonl"
    lines = [
        {"cwd": "/some/project", "timestamp": "2026-07-05T10:00:00.000Z", "text": "x" * 1000},
    ]
    lines += [
        {"cwd": "/some/project", "timestamp": "2026-07-07T09:00:00.000Z", "text": "y" * 5000}
        for _ in range(30)
    ]
    _write_transcript(session_file, lines)
    mtime = datetime.datetime.fromisoformat("2026-07-07T12:00:00").timestamp()
    os.utime(session_file, (mtime, mtime))

    fake = FakeParser("fake-host", [session_file])
    results = mod.scan("2026-07-07", {"fake-host": 10_000}, parsers=[fake])
    assert len(results) == 1
    assert results[0]["session_id"] == "session-abcdef"

    # A scan for the (wrong) directory-implied date must NOT find it,
    # confirming inclusion is driven by mtime, not path structure.
    results_wrong_date = mod.scan("2026-07-05", {"fake-host": 10_000}, parsers=[fake])
    assert results_wrong_date == []


def test_scan_accepts_a_set_of_dates_for_window_scanning(tmp_path):
    mod = _load()
    file_today = tmp_path / "today.jsonl"
    file_yesterday = tmp_path / "yesterday.jsonl"
    file_older = tmp_path / "older.jsonl"

    for f, iso in (
        (file_today, "2026-07-07T12:00:00"),
        (file_yesterday, "2026-07-06T12:00:00"),
        (file_older, "2026-07-05T12:00:00"),
    ):
        _write_transcript(f, [
            {"cwd": "/p", "timestamp": iso, "text": "x" * 5000} for _ in range(10)
        ])
        mtime = datetime.datetime.fromisoformat(iso).timestamp()
        os.utime(f, (mtime, mtime))

    fake = FakeParser("fake-host", [file_today, file_yesterday, file_older])
    # A 2-day window (today + yesterday) should find two of the three.
    results = mod.scan({"2026-07-07", "2026-07-06"}, {"fake-host": 10_000}, parsers=[fake])
    ids = sorted(r["session_id"] for r in results)
    assert ids == ["today", "yesterday"]


def test_scan_skips_sessions_below_line_floor(tmp_path):
    mod = _load()
    session_file = tmp_path / "tiny.jsonl"
    lines = [{"cwd": "/some/project", "timestamp": "2026-07-07T10:00:00.000Z"}] * 2
    _write_transcript(session_file, lines)
    mtime = datetime.datetime.fromisoformat("2026-07-07T12:00:00").timestamp()
    os.utime(session_file, (mtime, mtime))

    fake = FakeParser("fake-host", [session_file], default_threshold=0)
    results = mod.scan("2026-07-07", {"fake-host": 0}, parsers=[fake])
    assert results == []


def test_scan_uses_real_parsers_by_default():
    """Sanity check that the module-level PARSERS list (loaded from the
    real __init__.py) is well-formed, without touching real transcripts."""
    mod = _load()
    assert len(mod.PARSERS) == 2
    ids = sorted(p.id for p in mod.PARSERS)
    assert ids == ["claude", "codex"]
    for p in mod.PARSERS:
        assert isinstance(p.opaque_field, str)
        assert isinstance(p.default_threshold, int)


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
