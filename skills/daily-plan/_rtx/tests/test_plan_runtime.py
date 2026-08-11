from __future__ import annotations

from pathlib import Path

from .. import _day_model as plan_runtime
from .. import _state_patch as state_patch


def test_get_today_date_uses_shared_date_key_formatter(monkeypatch):
    monkeypatch.setattr(plan_runtime, "get_today_date_key", lambda: "1-5-07")

    assert plan_runtime.get_today_date() == "1-5-07"


def test_normalize_plan_date_accepts_storage_key_and_iso(monkeypatch):
    monkeypatch.setattr(plan_runtime, "get_today_date_key", lambda: "1-5-07")

    assert plan_runtime.normalize_plan_date(None) == "1-5-07"
    assert plan_runtime.normalize_plan_date("07-03-26") == "7-3-26"
    assert plan_runtime.normalize_plan_date("2026-07-04") == "7-4-26"


def test_initial_meta_filters_and_sorts():
    todo = {
        "categories": [{"name": "Work", "entries": [
            {"id": "b", "title": "later", "state": "incomplete", "deadline": "2026-07-04", "created": "2026-07-01"},
            {"id": "a", "title": "earlier", "state": "inprogress", "deadline": "2026-07-02", "created": "2026-07-01"},
            {"id": "c", "title": "done", "state": "complete", "deadline": "2026-07-01", "created": "2026-07-01"},
        ]}]}
    assert plan_runtime.initial_meta_for_section("actions", todo) == [["a", "shown"], ["b", "shown"]]

    triage = {"categories": [{"name": "Work", "entries": [
        {"id": "x", "title": "undecided", "state": "undecided", "deadline": "2026-07-03", "created": "2026-07-01"},
        {"id": "y", "title": "accepted", "state": "accepted", "deadline": "2026-07-02", "created": "2026-07-01"},
    ]}]}
    assert plan_runtime.initial_meta_for_section("triage", triage) == [["x", "shown"]]


def test_resolve_section_prunes_missing_and_indexes_visible():
    doc = {"categories": [{"name": "Work", "entries": [
        {"id": "a", "title": "A"},
        {"id": "c", "title": "C"},
    ]}]}
    meta = [["a", "shown"], ["b", "hidden"], ["c", "shown"]]
    new_meta, visible = plan_runtime.resolve_section("actions", meta, doc)
    assert new_meta == [["a", "shown"], ["c", "shown"]]
    assert [row[0] for row in visible] == [1, 2]
    assert [row[2] for row in visible] == ["a", "c"]


def test_apply_local_mutation_keep_hide_remove():
    meta = [["a", "shown"], ["b", "shown"], ["c", "hidden"]]
    visible = [(1, 0, "a", {"id": "a"}), (2, 1, "b", {"id": "b"})]
    kept = plan_runtime.apply_local_mutation([row[:] for row in meta], visible, "keep", [2])
    assert kept == [["a", "hidden"], ["b", "shown"], ["c", "hidden"]]
    hidden = plan_runtime.apply_local_mutation([row[:] for row in meta], visible, "hide", [1])
    assert hidden[0][1] == "hidden"
    removed = plan_runtime.apply_local_mutation([row[:] for row in meta], visible, "remove", [2])
    assert removed == [["a", "shown"], ["c", "hidden"]]


def test_refresh_rendered_plan_reinjects_blocks_and_prunes_missing(monkeypatch):
    plan_text = """# Plan: July 02, 2026

## Actions (suggestions)
<!-- BEGIN ACTIONS -->
old actions
<!-- END ACTIONS -->

## Triage
<!-- BEGIN TRIAGE -->
old triage
<!-- END TRIAGE -->
"""
    meta = {"actions": [["a", "shown"], ["gone", "hidden"]], "triage": [["t", "shown"]]}
    docs = {
        "todo": {"categories": [{"name": "Work", "entries": [{"id": "a", "title": "A"}]}]},
        "triage": {"categories": [{"name": "Work", "entries": [{"id": "t", "title": "T"}]}]},
    }
    written = {}

    monkeypatch.setattr(plan_runtime, "load_list_doc", lambda name: docs[name])
    monkeypatch.setattr(plan_runtime, "render_entries", lambda entries: ", ".join(e["title"] for e in entries))
    monkeypatch.setattr(plan_runtime, "write_meta", lambda date_key, payload: written.setdefault("meta", payload))
    monkeypatch.setattr(plan_runtime, "write_plan_text", lambda date_key, content: written.setdefault("plan", content))

    result = plan_runtime.refresh_rendered_plan("7-2-26", plan_text=plan_text, meta=meta)
    assert "<!-- BEGIN ACTIONS -->\nA\n<!-- END ACTIONS -->" in result
    assert "<!-- BEGIN TRIAGE -->\nT\n<!-- END TRIAGE -->" in result
    assert written["meta"] == {"actions": [["a", "shown"]], "triage": [["t", "shown"]]}
    assert written["plan"] == result


def test_mutate_plan_add_only_changes_plan_metadata(monkeypatch):
    meta = {"actions": [], "triage": []}
    docs = {
        "todo": {"categories": [{"name": "Work", "entries": [{"id": "a", "title": "A"}]}]},
        "triage": {"categories": []},
    }
    calls = []

    monkeypatch.setattr(plan_runtime, "plan_exists", lambda _: True)
    monkeypatch.setattr(plan_runtime, "read_meta", lambda _: {k: [row[:] for row in v] for k, v in meta.items()})
    monkeypatch.setattr(plan_runtime, "load_list_doc", lambda name: docs[name])
    monkeypatch.setattr(plan_runtime, "update_master_list", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(plan_runtime, "refresh_rendered_plan", lambda date_key, meta=None, plan_text=None: meta)

    result = plan_runtime.mutate_plan("7-2-26", "add", section="actions", item_id="a")
    assert result == {"actions": [["a", "shown"]], "triage": []}
    assert calls == []


def test_mutate_plan_mark_done_updates_master_list_and_hides_item(monkeypatch):
    meta = {"actions": [["a", "shown"], ["b", "shown"]], "triage": []}
    docs = {
        "todo": {"categories": [{"name": "Work", "entries": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]}]},
        "triage": {"categories": []},
    }
    calls = []

    monkeypatch.setattr(plan_runtime, "plan_exists", lambda _: True)
    monkeypatch.setattr(plan_runtime, "read_meta", lambda _: {k: [row[:] for row in v] for k, v in meta.items()})
    monkeypatch.setattr(plan_runtime, "load_list_doc", lambda name: docs[name])
    monkeypatch.setattr(plan_runtime, "update_master_list", lambda list_name, patches: calls.append((list_name, patches)))
    monkeypatch.setattr(plan_runtime, "refresh_rendered_plan", lambda date_key, meta=None, plan_text=None: meta)

    result = plan_runtime.mutate_plan("7-2-26", "mark-done", section="actions", indices=[2])
    assert calls == [("todo", [{"id": "b", "state": "complete"}])]
    assert result == {"actions": [["a", "shown"], ["b", "hidden"]], "triage": []}


def test_state_patch_uses_requested_date(monkeypatch, capsys):
    calls = []

    monkeypatch.setattr(
        state_patch,
        "mutate_plan",
        lambda date_key, command, **kwargs: calls.append((date_key, command, kwargs)) or "updated",
    )

    assert state_patch.main(["hide", "actions", "2", "--date", "2026-07-04"]) == 0
    assert calls == [("7-4-26", "hide", {"section": "actions", "indices": [2]})]
    assert capsys.readouterr().out == "updated"


# ── self-reported status ───────────────────────────────────────────────────────
# recurring-tasks' scheduled daily-plan job evaluates success from
# state/status.json (read_inner_status). Persisting a plan is the job's real
# deliverable, so only that may report ok -- an agent that exits 0 without
# producing a plan must leave no status behind.

def test_successful_write_records_an_ok_status(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(plan_runtime, "STATE_DIR", tmp_path / "state")

    plan_runtime._record_status_ok("8-10-26")

    payload = json.loads((tmp_path / "state" / "status.json").read_text())
    assert payload["result"] == "ok"
    assert payload["date_key"] == "8-10-26"
    assert payload["recorded_at"]


def test_status_bookkeeping_never_fails_a_written_plan(tmp_path, monkeypatch):
    """A plan that reached the cloud must not be reported as a failure
    because the local status file could not be written."""
    unwritable = tmp_path / "file-in-the-way"
    unwritable.write_text("not a directory")
    monkeypatch.setattr(plan_runtime, "STATE_DIR", unwritable / "state")

    plan_runtime._record_status_ok("8-10-26")  # must not raise


def test_writing_a_plan_records_the_status_the_job_contract_reads(
    tmp_path, monkeypatch
):
    """The success signal must sit on the path that actually persists a plan.

    It used to live in a second storage module that wrote the same plans
    through rclone, while the orchestrator persisted through cloud-files. No
    scheduled run took the rclone path, so status.json was never written and
    every successful run was recorded as a failure by
    `require_inner_status: ok`.
    """
    import json

    monkeypatch.setattr(plan_runtime, "STATE_DIR", tmp_path / "state")
    written: list[tuple[str, str]] = []
    monkeypatch.setattr(
        plan_runtime,
        "run_dispatcher",
        lambda skill, interface, *args, stdin=None: written.append((interface, args[0]))
        or "",
    )

    plan_runtime.write_plan_text("8-10-26", "# plan")

    assert written == [("plans-write", "plans/8-10-26.md")]
    payload = json.loads((tmp_path / "state" / "status.json").read_text())
    assert payload["result"] == "ok"
    assert payload["date_key"] == "8-10-26"
