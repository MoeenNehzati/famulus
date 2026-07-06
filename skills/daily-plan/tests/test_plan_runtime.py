from __future__ import annotations

import importlib.util
from pathlib import Path

runtime_path = Path(__file__).parent.parent / "scripts" / "plan_runtime.py"
spec = importlib.util.spec_from_file_location("plan_runtime", runtime_path)
plan_runtime = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(plan_runtime)


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
