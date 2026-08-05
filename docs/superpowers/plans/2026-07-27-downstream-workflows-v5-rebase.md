# Downstream Workflow Repairs (v5 Rebase) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix feedback items 12 (shared with the dispatcher-contracts rebase), 23, 24, 25, 27, 28 from `docs/plans/osx_feedback_fix/README.md` — usable list initialization, transactional triage finalization, source-identity tracking, and safe historical rescan — superseding `docs/plans/osx_feedback_fix/05-downstream-workflows.md`. Item 26 (missing-Subject crash) is already fixed in current code and is dropped from this plan.

**Architecture:** `list-manager` gets seeded default categories on `cmd_init` and a batch-mutation interface to close the concurrent-write race. `email-triage` gets one `scripts-finalize-run` interface that replaces the current fragmented three-script sequence (`_write_metrics.py`, `_watermark_writer.py`, `_envelope_gate.py`), a `source` field on triage actions to support deduplicated rescan, and state paths that resolve through `FamulusPaths`/`EMAIL_TRIAGE_STATE_DIR` instead of defaulting to `SKILL_DIR/state`.

**Tech Stack:** Python 3.11+, pytest, JSON Schema (blueprint schema v5), the existing YAML list store.

**Path note:** All files below are confirmed at their current `_rtx/` locations (verified directly, not just from the audit). `skills/list-manager/tests/` and `skills/list-manager/_rtx/tests/` both currently exist — this plan's test targets go under `_rtx/tests/` to match `email-triage`'s and `recurring-tasks`'s established convention; confirm no canonical-location conflict before creating new test files.

---

## Task 0: Confirm item 26 is closed, retire the stale reference

**Files:** none changed — this is a verification-only step.

- [ ] **Step 1: Verify the fix and its test location**

Run: `python3 -m pytest -q skills/email-client/_rtx/tests/test_mail.py -k decode_mime_words_none -v`
Expected: PASS — `decode_mime_words(None) -> ""` in `skills/email-client/_rtx/_imap_gateway.py:136`. No production change needed; the frozen plan's prerequisite command referenced the pre-v5 path `skills/email-client/tests/test_mail.py`, which no longer exists (moved to `_rtx/tests/`).

- [ ] **Step 2: No commit needed for this task** — proceed to Task 1.

---

## Task 1: Seed usable default categories on list init (feedback item 23)

**Files:**
- Modify: `skills/list-manager/_rtx/_yaml_store.py` (`cmd_init` at line 460, currently writes `"categories": []` unconditionally at line 469)
- Modify (via `skill-maker`): `skills/list-manager/blueprint.yaml` or `_rtx/blueprint.yaml` contract for `cloud-init`/local init, whichever owns `cmd_init`
- Test: `skills/list-manager/_rtx/tests/test_lists.py` (verify exact existing test file name before creating a new one)

- [ ] **Step 1: Write failing tests**

```python
def test_cmd_init_seeds_default_categories_for_todo_schema(tmp_path):
    args = make_init_args(schema="todo", path=tmp_path / "todo.yaml")
    cmd_init(args)
    data = yaml.safe_load((tmp_path / "todo.yaml").read_text())
    category_names = [c["name"] for c in data["categories"]]
    assert "Personal" in category_names
    assert len(category_names) > 0


def test_cmd_init_seeds_default_categories_for_triage_schema(tmp_path):
    args = make_init_args(schema="triage", path=tmp_path / "triage.yaml")
    cmd_init(args)
    data = yaml.safe_load((tmp_path / "triage.yaml").read_text())
    category_names = [c["name"] for c in data["categories"]]
    assert "Replies" in category_names
```

(Match the real `cmd_init`/`args` construction helper already used elsewhere in the test file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_lists.py -k seeds_default_categories -v`
Expected: FAIL — `cmd_init` currently writes `"categories": []` unconditionally (`_yaml_store.py:469`).

- [ ] **Step 3: Implement `default_categories()` and wire it into `cmd_init`**

```python
def default_categories(schema: str) -> list[dict]:
    if schema == "todo":
        return [{"name": "Personal", "categories": []}, {"name": "Work", "categories": []}]
    if schema == "triage":
        return [{"name": "Replies", "categories": []}, {"name": "Follow-ups", "categories": []}]
    return []
```

Replace the unconditional `"categories": []` at `cmd_init` (line 469) with `"categories": default_categories(args.schema)` (match the real parameter name that carries schema type in `cmd_init`'s `args`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_lists.py -v`
Expected: PASS, no regressions in existing init tests.

- [ ] **Step 5: Blueprint update through `skill-maker`**

Update the `cloud-init`/local-init interface contract's `outcomes` to document that init seeds non-empty default categories per schema.

- [ ] **Step 6: Commit**

```bash
git add skills/list-manager/_rtx/_yaml_store.py skills/list-manager/_rtx/tests/test_lists.py
git commit -m "fix(list-manager): seed usable default categories on list init"
```

---

## Task 2: Batch mutation interface to close the concurrent-write race (feedback items 24, 25)

**Files:**
- Modify: `skills/list-manager/_rtx/_yaml_store.py` (add a batch-apply function)
- Modify: `skills/list-manager/blueprint.yaml` (add `list-manager.interface.cloud-apply-batch`, alongside the existing `cloud-create-entry`/`cloud-update`/`cloud-delete`/`cloud-init`/`cloud-list-categories`/`cloud-read`/`cloud-read-beautify` interfaces — confirmed present today)
- Test: `skills/list-manager/_rtx/tests/test_lists.py`

- [ ] **Step 1: Write failing tests**

```python
def test_apply_batch_applies_multiple_mutations_atomically(tmp_path):
    seed_list(tmp_path / "todo.yaml", categories=[{"name": "Personal", "categories": [], "items": []}])
    mutations = [
        {"op": "create", "category_path": "Personal", "item": {"title": "buy milk"}},
        {"op": "create", "category_path": "Personal", "item": {"title": "call mom"}},
    ]
    apply_batch(path=tmp_path / "todo.yaml", mutations=mutations)
    data = yaml.safe_load((tmp_path / "todo.yaml").read_text())
    titles = [i["title"] for i in data["categories"][0]["items"]]
    assert titles == ["buy milk", "call mom"]


def test_apply_batch_rejects_stale_revision(tmp_path):
    seed_list(tmp_path / "todo.yaml", categories=[{"name": "Personal", "categories": [], "items": []}])
    before = read_revision(tmp_path / "todo.yaml")
    # simulate a concurrent writer bumping the revision
    seed_list(tmp_path / "todo.yaml", categories=[{"name": "Personal", "categories": [], "items": [{"title": "x"}]}])
    with pytest.raises(StaleRevisionError):
        apply_batch(path=tmp_path / "todo.yaml", mutations=[{"op": "create", "category_path": "Personal", "item": {"title": "y"}}], expected_revision=before)
```

(If the YAML store doesn't already have a revision/version counter, this task must add one — check `_yaml_store.py` for any existing `revision`/`version` field before assuming it needs to be introduced from scratch.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_lists.py -k apply_batch -v`
Expected: FAIL — `apply_batch` doesn't exist yet.

- [ ] **Step 3: Implement `apply_batch` with optimistic-concurrency revision check**

```python
class StaleRevisionError(Exception):
    pass


def apply_batch(*, path: Path, mutations: list[dict], expected_revision: int | None = None) -> None:
    data = _load(path)
    if expected_revision is not None and data.get("revision", 0) != expected_revision:
        raise StaleRevisionError(f"expected revision {expected_revision}, found {data.get('revision', 0)}")
    for mutation in mutations:
        _apply_one(data, mutation)
    data["revision"] = data.get("revision", 0) + 1
    _atomic_write(path, data)
```

Match `_atomic_write`/`_load`'s real existing helper names in `_yaml_store.py` — reuse them, don't duplicate atomic-write logic.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/list-manager/_rtx/tests/test_lists.py -v`
Expected: PASS

- [ ] **Step 5: Add the `cloud-apply-batch` interface through `skill-maker`**

Add `list-manager.interface.cloud-apply-batch` to `skills/list-manager/blueprint.yaml` following the exact structure of the existing `cloud-create-entry` entry (facade_interface pointing at a matching `list-manager._rtx.interface.cloud-apply-batch`).

- [ ] **Step 6: Update the triage instructions to use batch mutation (item 25)**

Read `skills/email-triage/instructions/triage.md` for the current per-item mutation guidance and rewrite it to call `cloud-apply-batch` once per triage run instead of issuing unsynchronized parallel per-item mutations.

- [ ] **Step 7: Commit**

```bash
git add skills/list-manager/_rtx/_yaml_store.py skills/list-manager/blueprint.yaml skills/email-triage/instructions/triage.md skills/list-manager/_rtx/tests/test_lists.py
git commit -m "feat(list-manager): add optimistic-concurrency batch mutation, close triage lost-update race"
```

---

## Task 3: State paths off `SKILL_DIR/state`, onto `FamulusPaths` (feedback item 12, shared with dispatcher-contracts rebase)

**Files:**
- Modify: `skills/email-triage/_rtx/_write_metrics.py:24-25`, `_watermark_writer.py:24,27`, `_envelope_gate.py:30,33` (all three currently default `STATE_DIR` to `SKILL_DIR / "state"` when `EMAIL_TRIAGE_STATE_DIR` is unset — confirmed today)
- Test: `skills/email-triage/_rtx/tests/test_watermark.py`, `test_filter_envelopes.py`

- [ ] **Step 1: Write failing tests**

```python
def test_state_dir_defaults_to_famulus_state_root_not_skill_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("EMAIL_TRIAGE_STATE_DIR", raising=False)
    from officina.common.famulus_paths import resolve_famulus_paths
    expected = resolve_famulus_paths(platform=sys.platform, home=tmp_path).email_triage_state_root
    import importlib
    import _watermark_writer
    importlib.reload(_watermark_writer)  # or however the module re-reads its default at call time
    assert _watermark_writer.default_state_dir(home=tmp_path) == expected
```

(This depends on `FamulusPaths` existing from the installer-runtime rebase, Task 1 — `docs/superpowers/plans/2026-07-27-osx-installer-runtime-v5-rebase.md`. Match the real module-load-time vs. call-time default resolution pattern in each of the three files before writing this test — `STATE_DIR` is currently a module-level constant computed at import time, which this task should change to a function computed at call time so it can consult `FamulusPaths`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/email-triage/_rtx/tests/test_watermark.py -k state_dir -v`
Expected: FAIL — current default is `SKILL_DIR / "state"`.

- [ ] **Step 3: Replace the module-level `STATE_DIR` constant with a call-time resolver**

In each of the three files, replace:
```python
STATE_DIR = Path(os.environ["EMAIL_TRIAGE_STATE_DIR"]) if os.environ.get("EMAIL_TRIAGE_STATE_DIR") else SKILL_DIR / "state"
```
with a function:
```python
def default_state_dir(*, home: Path | None = None) -> Path:
    override = os.environ.get("EMAIL_TRIAGE_STATE_DIR")
    if override:
        return Path(override)
    from officina.common.famulus_paths import resolve_famulus_paths
    return resolve_famulus_paths(platform=sys.platform, home=home or Path.home()).email_triage_state_root
```
and update every call site that referenced the module-level `STATE_DIR` constant to call `default_state_dir()` instead. Keep `EMAIL_TRIAGE_STATE_DIR` as the explicit override — this preserves existing test/CI behavior that sets that env var, only changes the *default* when it's unset.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/email-triage/_rtx/tests/ -v`
Expected: PASS, no regressions (existing tests that set `EMAIL_TRIAGE_STATE_DIR` explicitly are unaffected).

- [ ] **Step 5: Commit**

```bash
git add skills/email-triage/_rtx/_write_metrics.py skills/email-triage/_rtx/_watermark_writer.py skills/email-triage/_rtx/_envelope_gate.py skills/email-triage/_rtx/tests/
git commit -m "fix(email-triage): default state root to FamulusPaths, not SKILL_DIR/state"
```

---

## Task 4: Transactional finalization (feedback item 28)

**Files:**
- Create: `skills/email-triage/_rtx/_finalize_run.py`
- Create: `skills/email-triage/_rtx/blueprints/rtx-finalize-run.yaml`
- Modify: `skills/email-triage/_rtx/_write_metrics.py` (currently writes `status.json["metrics"]` unconditionally, no run-id upsert or idempotency guard, lines 58-69)
- Modify: `skills/email-triage/_rtx/_watermark_writer.py` (currently advances watermark checking only `status.result != "error"`, lines 39-66)
- Test: `skills/email-triage/_rtx/tests/test_finalize_run.py` (new)

- [ ] **Step 1: Write failing tests**

```python
def test_finalize_run_writes_metrics_then_watermark_in_order(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("_finalize_run._write_metrics_step", lambda *a, **k: calls.append("metrics"))
    monkeypatch.setattr("_finalize_run._advance_watermark_step", lambda *a, **k: calls.append("watermark"))
    finalize_run(run_id="run-1", state_dir=tmp_path, result="ok")
    assert calls == ["metrics", "watermark"]


def test_finalize_run_is_idempotent_for_same_run_id(tmp_path):
    finalize_run(run_id="run-1", state_dir=tmp_path, result="ok")
    finalize_run(run_id="run-1", state_dir=tmp_path, result="ok")  # replay
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["last_finalized_run_id"] == "run-1"
    # metrics/watermark not double-applied — assert via a counter or content equality


def test_finalize_run_skips_watermark_advance_on_error_result(tmp_path):
    finalize_run(run_id="run-2", state_dir=tmp_path, result="error")
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["watermark_advanced"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/email-triage/_rtx/tests/test_finalize_run.py -v`
Expected: FAIL — `_finalize_run.py` doesn't exist yet.

- [ ] **Step 3: Implement `_finalize_run.py`**

```python
"""Single ordered, idempotent finalization step replacing the previously
fragmented write-metrics / advance-watermark script sequence."""
from __future__ import annotations

import json
import os
from pathlib import Path


def finalize_run(*, run_id: str, state_dir: Path, result: str) -> None:
    status_path = state_dir / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}

    if status.get("last_finalized_run_id") == run_id:
        return  # already finalized, replay-safe no-op

    _write_metrics_step(state_dir=state_dir, run_id=run_id, result=result)

    watermark_advanced = False
    if result != "error":
        _advance_watermark_step(state_dir=state_dir, run_id=run_id)
        watermark_advanced = True

    status["last_finalized_run_id"] = run_id
    status["watermark_advanced"] = watermark_advanced
    tmp_path = status_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(status, indent=2))
    os.replace(tmp_path, status_path)


def _write_metrics_step(*, state_dir: Path, run_id: str, result: str) -> None:
    from _write_metrics import write_metrics  # reuse existing logic, don't duplicate
    write_metrics(state_dir=state_dir, run_id=run_id, result=result)


def _advance_watermark_step(*, state_dir: Path, run_id: str) -> None:
    from _watermark_writer import advance_watermark  # reuse existing logic, don't duplicate
    advance_watermark(state_dir=state_dir, run_id=run_id)
```

Match the real function names already exported by `_write_metrics.py`/`_watermark_writer.py` — read both files first; this task orders and idempotency-guards the existing logic, it does not rewrite metrics/watermark computation itself.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/email-triage/_rtx/tests/test_finalize_run.py -v`
Expected: PASS

- [ ] **Step 5: Wire `scripts-finalize-run` as the single public interface**

Add `email-triage.interface.scripts-finalize-run` to `skills/email-triage/blueprint.yaml`/`_rtx/blueprint.yaml` (matching the existing `instructions-triage` gateway pattern), update `instructions/triage.md` to call this one interface instead of invoking metrics/watermark scripts separately.

- [ ] **Step 6: Blueprint updates through `skill-maker`**

Create `blueprints/rtx-finalize-run.yaml`; update `blueprints/rtx-write-metrics.yaml`/`rtx-watermark-writer.yaml` `direct_io` to note they are now called internally by `_finalize_run`, not directly by the triage instructions.

- [ ] **Step 7: Commit**

```bash
git add skills/email-triage/_rtx/_finalize_run.py skills/email-triage/_rtx/blueprints/rtx-finalize-run.yaml skills/email-triage/instructions/triage.md skills/email-triage/blueprint.yaml skills/email-triage/_rtx/tests/test_finalize_run.py
git commit -m "feat(email-triage): single ordered, idempotent, replay-safe finalization step"
```

---

## Task 5: Source-identity tracking and deduplicated historical rescan (feedback item 27)

**Files:**
- Modify: `skills/list-manager/_rtx/schemas/types/action.json`, `triage_action.json` (neither currently has a `source` property — confirmed today)
- Create: `skills/email-triage/_rtx/_rescan.py`
- Test: `skills/email-triage/_rtx/tests/test_rescan.py` (new)

- [ ] **Step 1: Write failing tests for the schema change**

```python
def test_triage_action_schema_accepts_source_field():
    import jsonschema
    schema = json.loads(Path("skills/list-manager/_rtx/schemas/types/triage_action.json").read_text())
    instance = {"title": "reply to Bob", "source": {"message_id": "abc123", "mailbox": "INBOX"}}
    jsonschema.validate(instance, schema)  # matched against the real full schema shape, adjust required fields
```

```python
def test_rescan_filters_actions_already_created_from_same_source(tmp_path):
    seed_list(tmp_path / "triage.yaml", categories=[{"name": "Replies", "categories": [], "items": [
        {"title": "reply to Bob", "source": {"message_id": "abc123"}},
    ]}])
    candidates = [{"title": "reply to Bob (dup)", "source": {"message_id": "abc123"}},
                  {"title": "reply to Alice", "source": {"message_id": "def456"}}]
    filtered = filter_destination_duplicates(candidates, existing_list_path=tmp_path / "triage.yaml")
    assert [c["source"]["message_id"] for c in filtered] == ["def456"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/email-triage/_rtx/tests/test_rescan.py -v`
Expected: FAIL — no `source` schema property, no `filter_destination_duplicates`.

- [ ] **Step 3: Add `source` to the action schemas**

```json
{
  "source": {
    "type": "object",
    "properties": {
      "message_id": {"type": "string"},
      "mailbox": {"type": "string"}
    },
    "required": ["message_id"]
  }
}
```

Add this as an optional property in both `action.json` and `triage_action.json`, matching the existing schema's structure/style (read the full files first — do not assume a flat top-level `properties` object without checking for `allOf`/`$ref` composition already in use).

- [ ] **Step 4: Implement `filter_destination_duplicates` and `--rescan-after` support**

```python
"""Deduplicate rescan candidates against items already present in the
destination list, identified by source.message_id, so a historical rescan
can safely re-run without creating duplicate triage entries."""
from __future__ import annotations

from pathlib import Path


def filter_destination_duplicates(candidates: list[dict], *, existing_list_path: Path) -> list[dict]:
    from _yaml_store import _load  # reuse existing loader, don't duplicate
    data = _load(existing_list_path)
    existing_ids = {
        item.get("source", {}).get("message_id")
        for category in data.get("categories", [])
        for item in category.get("items", [])
        if item.get("source")
    }
    return [c for c in candidates if c.get("source", {}).get("message_id") not in existing_ids]
```

Add a `--rescan-after <watermark>` CLI flag to whatever script currently drives triage fetch (check `_mail_envelope_stream.py`/`_envelope_gate.py` for the real fetch entry point) that fetches from an explicit prior watermark instead of the current one, then routes candidates through `filter_destination_duplicates` before creating list entries.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/email-triage/_rtx/tests/test_rescan.py -v`
Expected: PASS

- [ ] **Step 6: Blueprint updates through `skill-maker`**

Update `blueprints/rtx-mail-envelope-stream.yaml` (or wherever `--rescan-after` lands) and the `list-manager` schema-referencing blueprints for the new `source` field.

- [ ] **Step 7: Commit**

```bash
git add skills/list-manager/_rtx/schemas/types/action.json skills/list-manager/_rtx/schemas/types/triage_action.json skills/email-triage/_rtx/_rescan.py skills/email-triage/_rtx/tests/test_rescan.py
git commit -m "feat(email-triage): source-identity tracking and deduplicated historical rescan"
```

---

## Dependency order summary

```
Task 0 (verify item 26 closed) ── no dependency
Task 1 (default categories) ── independent
Task 2 (batch mutation) ── independent of Task 1, needs list-manager revision counter
Task 3 (FamulusPaths state root) ── depends on installer-runtime rebase Task 1 (FamulusPaths)
Task 4 (transactional finalization) ── independent of Tasks 1-3, but should land after Task 3 so it inherits the corrected state root
Task 5 (source identity + rescan) ── depends on Task 2's schema conventions being settled first
```

## Explicitly out of scope

- Item 26 (missing-Subject crash) — already fixed, not part of this plan's diff.
- Dispatcher-level structured error propagation for triage failures — owned by the dispatcher-contracts rebase (`docs/superpowers/plans/2026-07-27-dispatcher-contracts-v5-rebase.md`), item 12 is fixed here only insofar as it concerns state-path defaults, not dispatcher error payloads.
