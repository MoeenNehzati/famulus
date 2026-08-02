"""Integration tests for lists.py subcommands. All tests operate on local temp files."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

LISTS_PY = Path(__file__).parent.parent / "_rtx" / "_yaml_store.py"
REPO_SRC = Path(__file__).resolve().parents[3] / "src"
SCRIPTS_DIR = LISTS_PY.parent

# A valid todo YAML used by multiple tests.
# Domain categories (Work, Personal) must have exactly the 6 task-list subcategories.
# Writing is at index 3; Tasks (with Book dentist) is at index 4.
TODO_YAML = """\
schema: todo
name: todo
categories:
- name: Work
  categories:
  - name: Replies
  - name: Payments
  - name: Reading
  - name: Writing
    entries:
    - id: a3f2b9
      title: Reply to Diego
      state: incomplete
      created: '2026-06-29'
      deadline: '2026-07-04'
      location: home
    - id: b7c1e2
      title: Review draft
      state: inprogress
      created: '2026-06-28'
      deadline: '2026-07-10'
  - name: Tasks
  - name: Misc
- name: Personal
  categories:
  - name: Replies
  - name: Payments
  - name: Reading
  - name: Writing
  - name: Tasks
    entries:
    - id: c3d1e5
      title: Book dentist
      state: complete
      created: '2026-06-20'
      deadline: '2026-06-30'
  - name: Misc
  - name: Shop
"""


def run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_SRC), str(SCRIPTS_DIR)])
    return subprocess.run(
        [sys.executable, str(LISTS_PY)] + args,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def todo_file(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text(TODO_YAML)
    return f


# ── init ─────────────────────────────────────────────────────────────────────

def test_init_creates_valid_yaml(tmp_path):
    f = tmp_path / "todo.yaml"
    result = run(["init", str(f), "--schema", "todo"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    assert data["schema"] == "todo"
    assert data["name"] == "todo"
    # A fresh list is seeded with usable default domain categories rather
    # than an empty list (feedback item 23) -- see
    # test_init_seeds_default_categories_for_todo_schema for the details.
    assert data["categories"]


def test_init_custom_name(tmp_path):
    f = tmp_path / "mylist.yaml"
    result = run(["init", str(f), "--schema", "todo", "--name", "My Tasks"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    assert data["name"] == "My Tasks"


def test_init_fails_if_file_exists(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text("schema: todo\nname: todo\ncategories: []\n")
    result = run(["init", str(f), "--schema", "todo"])
    assert result.returncode != 0
    assert "exists" in result.stderr


def test_init_unknown_schema_fails(tmp_path):
    f = tmp_path / "mylist.yaml"
    result = run(["init", str(f), "--schema", "nonexistent"])
    assert result.returncode != 0


def test_init_triage_schema(tmp_path):
    f = tmp_path / "pa.yaml"
    result = run(["init", str(f), "--schema", "triage"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    assert data["schema"] == "triage"


def test_init_default_schema(tmp_path):
    f = tmp_path / "notes.yaml"
    result = run(["init", str(f), "--schema", "default"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    assert data["schema"] == "default"


# Fixed subcategory sets todo/triage domain categories must carry exactly
# (task-list.json / task-list-personal.json's `name` enum). Personal adds
# "Shop"; every other domain (e.g. Work) uses the base six.
_PERSONAL_SUBCATEGORY_NAMES = {"Replies", "Payments", "Reading", "Writing", "Tasks", "Misc", "Shop"}
_WORK_SUBCATEGORY_NAMES = {"Replies", "Payments", "Reading", "Writing", "Tasks", "Misc"}


def _assert_default_domain_categories(data: dict) -> None:
    """Shared assertion for the todo/triage default-category seed: both
    "Personal" and "Work" domains present, each with its exact required
    subcategory set."""
    category_names = [c["name"] for c in data["categories"]]
    assert "Personal" in category_names
    assert "Work" in category_names

    personal = next(c for c in data["categories"] if c["name"] == "Personal")
    assert {sc["name"] for sc in personal["categories"]} == _PERSONAL_SUBCATEGORY_NAMES

    work = next(c for c in data["categories"] if c["name"] == "Work")
    assert {sc["name"] for sc in work["categories"]} == _WORK_SUBCATEGORY_NAMES


def test_init_seeds_default_categories_for_todo_schema(tmp_path):
    f = tmp_path / "todo.yaml"
    result = run(["init", str(f), "--schema", "todo"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    _assert_default_domain_categories(data)


def test_init_seeds_default_categories_for_triage_schema(tmp_path):
    f = tmp_path / "triage.yaml"
    result = run(["init", str(f), "--schema", "triage"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    _assert_default_domain_categories(data)


def test_init_default_schema_categories_stay_empty(tmp_path):
    # The "default" schema has no fixed category vocabulary, so there is no
    # usable default to seed -- an empty list remains correct.
    f = tmp_path / "notes.yaml"
    result = run(["init", str(f), "--schema", "default"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    assert data["categories"] == []


# ── gen-id ───────────────────────────────────────────────────────────────────

def test_gen_id_returns_six_char_hex(todo_file):
    result = run(["gen-id", str(todo_file)])
    assert result.returncode == 0, result.stderr
    id_ = result.stdout.strip()
    assert len(id_) == 6
    assert all(c in "0123456789abcdef" for c in id_)


def test_gen_id_count(todo_file):
    result = run(["gen-id", str(todo_file), "--count", "5"])
    assert result.returncode == 0
    ids = result.stdout.strip().splitlines()
    assert len(ids) == 5
    assert len(set(ids)) == 5  # all unique


def test_gen_id_avoids_collisions(tmp_path):
    existing_id = "aabbcc"
    f = tmp_path / "todo.yaml"
    f.write_text(
        f"schema: todo\nname: todo\ncategories:\n"
        f"- name: Work\n  entries:\n"
        f"  - id: {existing_id}\n    title: X\n"
        f"    state: incomplete\n    created: '2026-06-29'\n"
        f"    deadline: '2026-07-01'\n"
    )
    result = run(["gen-id", str(f), "--count", "20"])
    assert result.returncode == 0
    ids = result.stdout.strip().splitlines()
    assert existing_id not in ids


# ── read ──────────────────────────────────────────────────────────────────────

def _flatten_entries(node) -> list[dict]:
    """Walk a filtered-read result (nested dict or list) and collect every
    entry dict found anywhere in it, for assertions that don't care about the
    surrounding category/parent-entry structure."""
    entries: list[dict] = []
    if isinstance(node, dict):
        if "id" in node and "title" in node:
            entries.append(node)
            for c in node.get("children", []):
                entries.extend(_flatten_entries(c))
        else:
            for e in node.get("entries", []):
                entries.extend(_flatten_entries(e))
            for c in node.get("categories", []):
                entries.extend(_flatten_entries(c))
    elif isinstance(node, list):
        for item in node:
            entries.extend(_flatten_entries(item))
    return entries


def test_read_unfiltered_returns_full_doc(todo_file):
    result = run(["read", str(todo_file)])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(result.stdout)
    assert data["schema"] == "todo"
    assert len(data["categories"]) == 2


def test_read_filter_exact_match(todo_file):
    result = run(["read", str(todo_file), "state=incomplete"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(result.stdout)
    # Structure (schema/categories) is preserved, not flattened.
    assert data["schema"] == "todo"
    entries = _flatten_entries(data)
    assert all(e["state"] == "incomplete" for e in entries)
    assert any(e["title"] == "Reply to Diego" for e in entries)


def test_read_filter_preserves_ancestor_categories(todo_file):
    # The match (Reply to Diego) lives under Work > Writing -- both ancestor
    # categories must survive pruning, and unrelated categories must not.
    result = run(["read", str(todo_file), "title~=Diego"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(result.stdout)
    work = next(c for c in data["categories"] if c["name"] == "Work")
    assert [c["name"] for c in work["categories"]] == ["Writing"]
    assert len(data["categories"]) == 1  # Personal has no match, so it's pruned


def test_read_filter_or_values(todo_file):
    result = run(["read", str(todo_file), "state=incomplete,inprogress"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(result.stdout)
    entries = _flatten_entries(data)
    assert len(entries) == 2
    states = {e["state"] for e in entries}
    assert states == {"incomplete", "inprogress"}


def test_read_filter_and_multiple_keys(todo_file):
    result = run(["read", str(todo_file), "state=incomplete", "location=home"])
    assert result.returncode == 0, result.stderr
    entries = _flatten_entries(yaml.safe_load(result.stdout))
    assert len(entries) == 1
    assert entries[0]["title"] == "Reply to Diego"


def test_read_filter_substring(todo_file):
    result = run(["read", str(todo_file), "title~=Diego"])
    assert result.returncode == 0, result.stderr
    entries = _flatten_entries(yaml.safe_load(result.stdout))
    assert len(entries) == 1
    assert entries[0]["id"] == "a3f2b9"


def test_read_filter_regex_anchored(todo_file):
    # ~= is a regex search: ^Reply matches "Reply to Diego" but not "Review draft".
    result = run(["read", str(todo_file), "title~=^Reply"])
    assert result.returncode == 0, result.stderr
    entries = _flatten_entries(yaml.safe_load(result.stdout))
    assert len(entries) == 1
    assert entries[0]["id"] == "a3f2b9"


def test_read_filter_regex_case_insensitive(todo_file):
    result = run(["read", str(todo_file), "title~=diego"])
    assert result.returncode == 0, result.stderr
    entries = _flatten_entries(yaml.safe_load(result.stdout))
    assert len(entries) == 1
    assert entries[0]["id"] == "a3f2b9"


def test_read_filter_ids_or(todo_file):
    # id filter with comma-OR selects an explicit set — the semantic-selection path.
    result = run(["read", str(todo_file), "id=a3f2b9,c3d1e5"])
    assert result.returncode == 0, result.stderr
    entries = _flatten_entries(yaml.safe_load(result.stdout))
    assert {e["id"] for e in entries} == {"a3f2b9", "c3d1e5"}


def test_read_filter_matching_child_keeps_parent_no_duplicate(tmp_path):
    # A matching nested child must bring its parent entry along for context,
    # and must not also be duplicated as an independent top-level result.
    f = tmp_path / "todo.yaml"
    f.write_text(
        "schema: todo\nname: todo\ncategories:\n"
        "- name: Work\n  entries:\n"
        "  - id: parent1\n    title: Parent task\n"
        "    state: incomplete\n    created: '2026-06-29'\n"
        "    deadline: '2026-07-04'\n"
        "    children:\n"
        "    - id: child1\n      title: Child task\n"
        "      state: complete\n      created: '2026-06-29'\n"
        "      deadline: '2026-07-04'\n"
    )
    result = run(["read", str(f), "state=complete"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(result.stdout)
    work = next(c for c in data["categories"] if c["name"] == "Work")
    assert len(work["entries"]) == 1
    parent = work["entries"][0]
    assert parent["id"] == "parent1"  # kept for context, though it doesn't itself match
    assert [c["id"] for c in parent["children"]] == ["child1"]  # the actual match
    # The child must not also appear a second time as its own top-level entry.
    assert _flatten_entries(data) == [parent, parent["children"][0]]


def test_update_coerces_unquoted_dates(tmp_path):
    # An unquoted `deadline: 2026-07-04` parses as a date object, which would
    # fail the schema's `type: string`. Normalization must coerce it so the
    # update validates and the saved file stores a string.
    f = tmp_path / "todo.yaml"
    f.write_text(TODO_YAML.replace("'2026-07-04'", "2026-07-04"))
    patch = tmp_path / "p.yaml"
    patch.write_text("- id: a3f2b9\n  state: incomplete\n")
    result = run(["update", str(f), "--file", str(patch)])
    assert result.returncode == 0, result.stderr
    saved = yaml.safe_load(f.read_text())
    # If the deadline were saved unquoted, safe_load would return a date object.
    dl = saved["categories"][0]["categories"][3]["entries"][0]["deadline"]
    assert isinstance(dl, str), f"deadline saved as {type(dl)}, expected str"


def test_validation_error_names_offending_entry(tmp_path):
    # Drop `state` from entry a3f2b9 (a required field) → the diagnostic must
    # name the entry's id and title, not just "'state' is a required property".
    f = tmp_path / "todo.yaml"
    f.write_text(TODO_YAML.replace("      state: incomplete\n", "", 1))
    patch = tmp_path / "p.yaml"
    patch.write_text("- id: b7c1e2\n  state: complete\n")
    result = run(["update", str(f), "--file", str(patch)])
    assert result.returncode != 0
    assert "a3f2b9" in result.stderr, result.stderr
    assert "Reply to Diego" in result.stderr, result.stderr


def test_read_filter_invalid_enum_value_errors(todo_file):
    """state=cancelled isn't a valid state (incomplete/inprogress/complete) -- this
    must be a hard error, not a silent empty result, so a typo'd filter value
    can't be misread as "nothing matches"."""
    result = run(["read", str(todo_file), "state=cancelled"])
    assert result.returncode != 0
    assert "cancelled" in result.stderr
    assert "incomplete" in result.stderr and "complete" in result.stderr


def test_read_filter_no_matches_non_enum_field(todo_file):
    result = run(["read", str(todo_file), "location=nowhere"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(result.stdout)
    # No entry matches, so every category is pruned away; the doc shell remains.
    assert _flatten_entries(data) == []
    assert data["categories"] == []


def test_read_filter_complete(todo_file):
    result = run(["read", str(todo_file), "state=complete"])
    assert result.returncode == 0, result.stderr
    entries = _flatten_entries(yaml.safe_load(result.stdout))
    assert len(entries) == 1
    assert entries[0]["title"] == "Book dentist"


# ── create-entry ──────────────────────────────────────────────────────────────

NEW_ENTRY_YAML = """\
- title: Draft blog post
  state: incomplete
  created: '2026-06-29'
  deadline: '2026-07-15'
"""

NEW_ENTRY_NO_ID_YAML = """\
- title: New task without ID
  state: incomplete
  created: '2026-06-29'
  deadline: '2026-07-20'
"""


def test_create_entry_by_category_path(todo_file):
    result = run(["create-entry", str(todo_file), "Work/Writing"], stdin=NEW_ENTRY_YAML)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    writing_cat = data["categories"][0]["categories"][3]
    assert writing_cat["name"] == "Writing"
    assert len(writing_cat["entries"]) == 3
    assert writing_cat["entries"][2]["title"] == "Draft blog post"


def test_create_entry_assigns_id(todo_file):
    result = run(["create-entry", str(todo_file), "Work/Writing"], stdin=NEW_ENTRY_NO_ID_YAML)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    new_entry = data["categories"][0]["categories"][3]["entries"][2]
    assert "id" in new_entry
    assert len(new_entry["id"]) == 6
    assert all(c in "0123456789abcdef" for c in new_entry["id"])


def test_create_entry_by_entry_id(todo_file):
    child_yaml = """\
- title: Sub-task
  state: incomplete
  created: '2026-06-29'
  deadline: '2026-07-05'
"""
    result = run(["create-entry", str(todo_file), "a3f2b9"], stdin=child_yaml)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    parent = data["categories"][0]["categories"][3]["entries"][0]
    assert parent["id"] == "a3f2b9"
    children = parent.get("children", [])
    assert len(children) == 1
    assert children[0]["title"] == "Sub-task"


def test_create_entry_unknown_category_fails(todo_file):
    result = run(["create-entry", str(todo_file), "Nonexistent/Category"], stdin=NEW_ENTRY_YAML)
    assert result.returncode != 0
    assert "not found" in result.stderr.lower()


def test_create_entry_invalid_entry_fails(todo_file):
    """An entry with wrong state should fail validation."""
    bad_entry = """\
- title: Bad entry
  state: undecided
  created: '2026-06-29'
  deadline: '2026-07-15'
"""
    result = run(["create-entry", str(todo_file), "Work/Writing"], stdin=bad_entry)
    assert result.returncode != 0


def test_create_entry_from_file(todo_file, tmp_path):
    entries_file = tmp_path / "entries.yaml"
    entries_file.write_text(NEW_ENTRY_YAML)
    result = run(["create-entry", str(todo_file), "Work/Writing", "--entries", str(entries_file)])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    assert len(data["categories"][0]["categories"][3]["entries"]) == 3


def test_create_entry_bulk(todo_file):
    bulk = """\
- title: Task A
  state: incomplete
  created: '2026-06-29'
  deadline: '2026-07-10'
- title: Task B
  state: incomplete
  created: '2026-06-29'
  deadline: '2026-07-11'
"""
    result = run(["create-entry", str(todo_file), "Work/Writing"], stdin=bulk)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entries = data["categories"][0]["categories"][3]["entries"]
    assert len(entries) == 4
    titles = [e["title"] for e in entries]
    assert "Task A" in titles
    assert "Task B" in titles


# ── update ────────────────────────────────────────────────────────────────────

def test_update_changes_state(todo_file):
    update_yaml = "- id: a3f2b9\n  state: complete\n"
    result = run(["update", str(todo_file)], stdin=update_yaml)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entry = data["categories"][0]["categories"][3]["entries"][0]
    assert entry["state"] == "complete"


def test_update_stamps_modified_on_any_change(todo_file):
    import datetime
    today = datetime.date.today().isoformat()
    update_yaml = "- id: a3f2b9\n  location: office\n"
    result = run(["update", str(todo_file)], stdin=update_yaml)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entry = data["categories"][0]["categories"][3]["entries"][0]
    assert entry["modified"] == today
    assert "completed" not in entry  # unrelated edit, entry never finished


def test_update_stamps_completed_on_finish_only(todo_file):
    import datetime
    today = datetime.date.today().isoformat()
    result = run(["update", str(todo_file)], stdin="- id: a3f2b9\n  state: complete\n")
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entry = data["categories"][0]["categories"][3]["entries"][0]
    assert entry["completed"] == today
    assert entry["modified"] == today


def test_update_never_overwrites_existing_completed(todo_file):
    result = run(["update", str(todo_file)], stdin="- id: a3f2b9\n  state: complete\n")
    assert result.returncode == 0, result.stderr
    # Backdate `completed` directly in the file, then make an unrelated edit --
    # the real completion date must survive, not get bumped to today.
    data = yaml.safe_load(todo_file.read_text())
    data["categories"][0]["categories"][3]["entries"][0]["completed"] = "2026-01-01"
    todo_file.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    result = run(["update", str(todo_file)], stdin="- id: a3f2b9\n  location: office\n")
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entry = data["categories"][0]["categories"][3]["entries"][0]
    assert entry["completed"] == "2026-01-01"


def test_update_multiple_entries(todo_file):
    update_yaml = """\
- id: a3f2b9
  state: complete
- id: b7c1e2
  deadline: '2026-07-20'
"""
    result = run(["update", str(todo_file)], stdin=update_yaml)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entries = data["categories"][0]["categories"][3]["entries"]
    assert entries[0]["state"] == "complete"
    assert entries[1]["deadline"] == "2026-07-20"


def test_update_immutable_created_rejected(todo_file):
    update_yaml = "- id: a3f2b9\n  created: '2026-01-01'\n"
    result = run(["update", str(todo_file)], stdin=update_yaml)
    assert result.returncode != 0
    assert "immutable" in result.stderr.lower()


def test_update_unknown_id_fails(todo_file):
    update_yaml = "- id: ffffff\n  state: complete\n"
    result = run(["update", str(todo_file)], stdin=update_yaml)
    assert result.returncode != 0


def test_update_invalid_state_fails(todo_file):
    update_yaml = "- id: a3f2b9\n  state: undecided\n"
    result = run(["update", str(todo_file)], stdin=update_yaml)
    assert result.returncode != 0


def test_update_from_file(todo_file, tmp_path):
    updates_file = tmp_path / "updates.yaml"
    updates_file.write_text("- id: a3f2b9\n  state: complete\n")
    result = run(["update", str(todo_file), "--file", str(updates_file)])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entry = data["categories"][0]["categories"][3]["entries"][0]
    assert entry["state"] == "complete"


# ── delete ────────────────────────────────────────────────────────────────────

def test_delete_top_level_entry(todo_file):
    """Delete a top-level category entry by id; file is updated and contains no trace."""
    original = todo_file.read_text()
    result = run(["delete", str(todo_file), "a3f2b9"])
    assert result.returncode == 0, result.stderr
    assert "deleted: a3f2b9" in result.stdout
    data = yaml.safe_load(todo_file.read_text())
    writing_entries = data["categories"][0]["categories"][3].get("entries", [])
    assert all(e["id"] != "a3f2b9" for e in writing_entries)
    # Other entries must survive
    assert any(e["id"] == "b7c1e2" for e in writing_entries)


def test_delete_nested_child(todo_file):
    """Delete a nested child entry; only the child is removed, parent survives."""
    # First add a child to a3f2b9
    child_yaml = "- title: Sub-task\n  state: incomplete\n  created: '2026-06-29'\n  deadline: '2026-07-05'\n"
    run(["create-entry", str(todo_file), "a3f2b9"], stdin=child_yaml)
    data = yaml.safe_load(todo_file.read_text())
    parent = data["categories"][0]["categories"][3]["entries"][0]
    child_id = parent["children"][0]["id"]

    result = run(["delete", str(todo_file), child_id])
    assert result.returncode == 0, result.stderr
    data2 = yaml.safe_load(todo_file.read_text())
    parent2 = data2["categories"][0]["categories"][3]["entries"][0]
    assert parent2["id"] == "a3f2b9"                  # parent still present
    assert parent2.get("children", []) == []           # child gone


def test_delete_bulk(todo_file):
    """Delete multiple ids in one call."""
    result = run(["delete", str(todo_file), "a3f2b9", "b7c1e2"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    writing_entries = data["categories"][0]["categories"][3].get("entries", [])
    assert writing_entries == []


def test_delete_unknown_id_exits_nonzero_file_unchanged(todo_file):
    """Unknown id → nonzero exit and file not modified."""
    before = todo_file.read_text()
    result = run(["delete", str(todo_file), "ffffff"])
    assert result.returncode != 0
    assert "ffffff" in result.stderr
    assert todo_file.read_text() == before


def test_delete_partial_missing_aborts(todo_file):
    """If any id is missing, all deletions are aborted; file is unchanged."""
    before = todo_file.read_text()
    result = run(["delete", str(todo_file), "a3f2b9", "zzzzzz"])
    assert result.returncode != 0
    assert todo_file.read_text() == before


# ── create-entry defaults ─────────────────────────────────────────────────────

def test_create_entry_defaults_state_and_created(todo_file):
    """create-entry should default state=incomplete and created=today when omitted."""
    import datetime
    entry_yaml = "- title: Minimal task\n  deadline: '2026-08-01'\n"
    result = run(["create-entry", str(todo_file), "Work/Writing"], stdin=entry_yaml)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entries = data["categories"][0]["categories"][3]["entries"]
    new_entry = next(e for e in entries if e["title"] == "Minimal task")
    assert new_entry["state"] == "incomplete"
    assert new_entry["created"] == datetime.date.today().isoformat()


def test_create_entry_unquoted_date_is_coerced(todo_file):
    """create-entry with an unquoted date value must still validate and save as string."""
    entry_yaml = "- title: Task with date\n  state: incomplete\n  created: '2026-06-01'\n  deadline: 2026-08-15\n"
    result = run(["create-entry", str(todo_file), "Work/Writing"], stdin=entry_yaml)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entries = data["categories"][0]["categories"][3]["entries"]
    new_entry = next(e for e in entries if e["title"] == "Task with date")
    # deadline must be stored as a string (normalize_dates must have coerced it)
    assert isinstance(new_entry["deadline"], str)
    assert new_entry["deadline"] == "2026-08-15"



def test_describe_schema_whole():
    result = run(["describe-schema", "todo"])
    assert result.returncode == 0, result.stderr
    out = yaml.safe_load(result.stdout)
    assert "state" in out["entry_fields"]
    assert out["entry_fields"]["state"]["enum"] == ["incomplete", "inprogress", "complete"]
    assert "deadline" in out["required_fields"]
    assert "state" in out["auto_generated_fields"]


def test_describe_schema_single_field():
    result = run(["describe-schema", "todo", "state"])
    assert result.returncode == 0, result.stderr
    out = yaml.safe_load(result.stdout)
    assert out == {"state": {"enum": ["incomplete", "inprogress", "complete"]}}


def test_describe_schema_unknown_field_errors():
    result = run(["describe-schema", "todo", "not_a_field"])
    assert result.returncode != 0
    assert "not_a_field" in result.stderr


def test_describe_schema_unknown_schema_errors():
    result = run(["describe-schema", "not-a-schema"])
    assert result.returncode != 0
    assert "not-a-schema" in result.stderr


# ── optimistic-concurrency revision check (feedback items 24/25) ──────────────
#
# TODO_YAML has no `revision` field, matching every pre-existing list file on
# disk today -- a missing revision is treated as revision 0 so this feature
# is opt-in and doesn't disturb any file that predates it.

def test_update_without_expected_revision_still_works(todo_file):
    """Backward compat: callers that never pass --expected-revision behave
    exactly as before -- the check is opt-in, not mandatory."""
    result = run(["update", str(todo_file)], stdin="- id: a3f2b9\n  state: complete\n")
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entry = data["categories"][0]["categories"][3]["entries"][0]
    assert entry["state"] == "complete"


def test_update_succeeds_with_matching_expected_revision(todo_file):
    # Fresh file has no revision field -> treated as revision 0.
    result = run(
        ["update", str(todo_file), "--expected-revision", "0"],
        stdin="- id: a3f2b9\n  state: complete\n",
    )
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entry = data["categories"][0]["categories"][3]["entries"][0]
    assert entry["state"] == "complete"
    # A successful revision-checked mutation advances the counter.
    assert data["revision"] == 1


def test_update_revision_increments_across_successive_mutations(todo_file):
    r1 = run(
        ["update", str(todo_file), "--expected-revision", "0"],
        stdin="- id: a3f2b9\n  state: complete\n",
    )
    assert r1.returncode == 0, r1.stderr
    r2 = run(
        ["update", str(todo_file), "--expected-revision", "1"],
        stdin="- id: b7c1e2\n  state: complete\n",
    )
    assert r2.returncode == 0, r2.stderr
    data = yaml.safe_load(todo_file.read_text())
    assert data["revision"] == 2


def test_update_rejects_stale_expected_revision(todo_file):
    result = run(
        ["update", str(todo_file), "--expected-revision", "5"],
        stdin="- id: a3f2b9\n  state: complete\n",
    )
    assert result.returncode != 0
    assert "revision" in result.stderr.lower()


def test_update_rejects_stale_revision_from_a_prior_completed_write(todo_file):
    """Sequential regression test (writer1 fully completes before writer2
    starts -- NOT a concurrency test): a writer whose --expected-revision no
    longer matches because a previous, already-finished write moved the
    revision on must be rejected, not silently clobber that prior write. The
    file on disk must be exactly the first writer's result -- no
    partial/corrupt write from the rejected second attempt. For an actual
    concurrent race that exercises the lock (two processes alive at the same
    time, racing through the check-to-write gap), see
    test_update_concurrent_writers_are_serialized_by_the_lock below."""
    writer1 = run(
        ["update", str(todo_file), "--expected-revision", "0"],
        stdin="- id: a3f2b9\n  state: complete\n",
    )
    assert writer1.returncode == 0, writer1.stderr
    after_writer1 = todo_file.read_text()

    # writer2 still passes the (now stale) revision it would have observed
    # had it read the file before writer1 ran.
    writer2 = run(
        ["update", str(todo_file), "--expected-revision", "0"],
        stdin="- id: b7c1e2\n  state: complete\n",
    )
    assert writer2.returncode != 0
    assert "revision" in writer2.stderr.lower()

    # File is untouched by the rejected second writer: still exactly writer1's
    # result, not a mix of both, not corrupted.
    assert todo_file.read_text() == after_writer1
    data = yaml.safe_load(todo_file.read_text())
    entry_a = data["categories"][0]["categories"][3]["entries"][0]
    entry_b = data["categories"][0]["categories"][3]["entries"][1]
    assert entry_a["state"] == "complete"   # writer1's change applied
    assert entry_b["state"] == "inprogress"  # writer2's change did NOT apply


def test_update_concurrent_writers_are_serialized_by_the_lock(todo_file):
    """Genuinely concurrent race, not sequential: writer1 is started and,
    while it is INSIDE its lock-held critical section -- past check_revision,
    before its save (via the LIST_MANAGER_TEST_RACE_DELAY test hook) --
    writer2 is started without waiting for writer1 to finish. Both are alive
    at the same time and both target --expected-revision 0, so their
    check-then-write windows would genuinely interleave if the mutation were
    only an optimistic check with no lock (writer2's load+check could happen
    before writer1's save, and both would then pass the check and both
    write). This is exactly the gap a bare revision check narrows but does
    not close.

    Proves the file lock closes it: writer2's load_yaml() cannot even begin
    until writer1 releases the lock (i.e. has already saved), so writer2
    always observes the post-writer1 revision and is correctly rejected --
    it can never race through and clobber writer1's write."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_SRC), str(SCRIPTS_DIR)])
    env["LIST_MANAGER_TEST_RACE_DELAY"] = "1.0"

    writer1 = subprocess.Popen(
        [sys.executable, str(LISTS_PY), "update", str(todo_file), "--expected-revision", "0"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    # Give writer1 time to acquire the lock, pass check_revision, and enter
    # its delay -- i.e. to be genuinely inside its critical section -- before
    # writer2 starts. This is what makes the two processes' windows overlap
    # rather than merely running one after the other.
    time.sleep(0.3)

    env2 = os.environ.copy()
    env2["PYTHONPATH"] = env["PYTHONPATH"]
    # writer2 has no injected delay: it is the fast racer that would win the
    # race (and silently clobber writer1) if the lock did not serialize it.
    writer2 = subprocess.Popen(
        [sys.executable, str(LISTS_PY), "update", str(todo_file), "--expected-revision", "0"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env2,
    )

    out1, err1 = writer1.communicate(input="- id: a3f2b9\n  state: complete\n", timeout=15)
    out2, err2 = writer2.communicate(input="- id: b7c1e2\n  state: complete\n", timeout=15)

    assert writer1.returncode == 0, err1
    # writer2 was blocked on the lock until writer1 released it, so by the
    # time it acquired the lock and ran check_revision, the file's revision
    # had already moved to 1 -- it is correctly rejected rather than racing
    # through.
    assert writer2.returncode != 0, f"writer2 unexpectedly succeeded (lock did not serialize): {out2}"
    assert "revision" in err2.lower()

    data = yaml.safe_load(todo_file.read_text())
    entry_a = data["categories"][0]["categories"][3]["entries"][0]
    entry_b = data["categories"][0]["categories"][3]["entries"][1]
    assert entry_a["state"] == "complete"     # writer1's change applied
    assert entry_b["state"] == "inprogress"   # writer2's change never applied
    assert data["revision"] == 1              # exactly one successful write occurred


# famulus-skip: category=platform-contract; reason=this test holds the lock sidecar directly via fcntl.flock, which only exists on os.name == "posix"; alternate=file_lock()'s os.name == "nt" branch shares the same bounded-retry-with-deadline structure exercised here, just via msvcrt instead of fcntl
@pytest.mark.skipif(os.name != "posix", reason="holds the lock directly via fcntl.flock")
def test_update_lock_acquisition_times_out_with_clear_error(todo_file):
    """A HUNG-but-alive lock holder (stuck network call, deadlock -- not a
    crash, which releases the OS lock automatically) must not make a later
    invocation stall silently forever. Hold the `<file>.lock` sidecar
    exclusively (simulating a stuck writer that has acquired the lock but
    never releases it) and confirm a second acquisition attempt -- using
    LIST_MANAGER_TEST_LOCK_TIMEOUT_S to shrink the real 30s default down to
    well under a second -- fails fast with a clear, actionable error instead
    of hanging."""
    import fcntl

    lock_path = todo_file.with_name(todo_file.name + ".lock")
    holder_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(REPO_SRC), str(SCRIPTS_DIR)])
        env["LIST_MANAGER_TEST_LOCK_TIMEOUT_S"] = "0.3"

        start = time.monotonic()
        result = subprocess.run(
            [sys.executable, str(LISTS_PY), "update", str(todo_file)],
            input="- id: a3f2b9\n  state: complete\n",
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        elapsed = time.monotonic() - start

        assert result.returncode != 0
        assert "lock" in result.stderr.lower()
        assert str(lock_path) in result.stderr
        # Fails fast on the shrunk test timeout, not the real 30s default and
        # not a hang -- proves the bound is actually enforced.
        assert elapsed < 5, f"took {elapsed:.1f}s -- did not honor the shortened test timeout"
        # File untouched: a timed-out acquisition attempt never reaches
        # load/mutate/save.
        assert todo_file.read_text() == TODO_YAML
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def test_create_entry_succeeds_with_matching_expected_revision(todo_file):
    result = run(
        ["create-entry", str(todo_file), "Work/Writing", "--expected-revision", "0"],
        stdin=NEW_ENTRY_YAML,
    )
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    assert data["revision"] == 1


def test_create_entry_rejects_stale_expected_revision_no_write(todo_file):
    before = todo_file.read_text()
    result = run(
        ["create-entry", str(todo_file), "Work/Writing", "--expected-revision", "7"],
        stdin=NEW_ENTRY_YAML,
    )
    assert result.returncode != 0
    assert "revision" in result.stderr.lower()
    assert todo_file.read_text() == before  # rejected mutation writes nothing


def test_delete_succeeds_with_matching_expected_revision(todo_file):
    result = run(["delete", str(todo_file), "a3f2b9", "--expected-revision", "0"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    assert data["revision"] == 1


def test_delete_rejects_stale_expected_revision_no_write(todo_file):
    before = todo_file.read_text()
    result = run(["delete", str(todo_file), "a3f2b9", "--expected-revision", "9"])
    assert result.returncode != 0
    assert "revision" in result.stderr.lower()
    assert todo_file.read_text() == before


# ── cloud-mode concurrency (the fresh-tempdir race) ─────────────────────────
#
# --cloud mode downloads the cloud list to a FRESH tempfile.mkdtemp() path on
# every invocation (see main()). The per-command file_lock() is keyed on that
# unique local temp path, so it serializes nothing across two independent
# --cloud processes -- each gets its own lock sidecar that no other process
# will ever contend for. Before the fix: two overlapping --cloud writers can
# both download the same cloud revision, both pass check_revision, and the
# second upload silently clobbers the first's change.

def test_cloud_concurrent_writers_are_serialized_across_processes(tmp_path):
    """Genuinely concurrent race (not a sequential simulation): two real
    subprocess invocations of lists.py --cloud race against a shared fake
    "cloud" backend (LIST_MANAGER_TEST_CLOUD_DIR, see _cloud_transport.py's
    _test_cloud_dir()), each still going through its own private
    tempfile.mkdtemp() download path exactly as production does. writer1 is
    held inside its critical section (past check_revision, before its save --
    via LIST_MANAGER_TEST_RACE_DELAY) while writer2, with no delay, is
    started and races to download+mutate+upload the same cloud list before
    writer1 finishes.

    Proves the fix serializes the two: writer2 cannot even begin its download
    until writer1's whole download-mutate-upload sequence has completed, so
    it always observes the post-writer1 revision and is correctly rejected --
    it never gets a window to race through and clobber writer1's write.
    """
    cloud_dir = tmp_path / "cloud"
    cloud_dir.mkdir()
    (cloud_dir / "todo.yaml").write_text(TODO_YAML)

    env_base = os.environ.copy()
    env_base["PYTHONPATH"] = os.pathsep.join([str(REPO_SRC), str(SCRIPTS_DIR)])
    env_base["LIST_MANAGER_TEST_CLOUD_DIR"] = str(cloud_dir)
    env_base["LIST_MANAGER_CLOUD_LOCK_DIR"] = str(tmp_path / "locks")

    env1 = env_base.copy()
    env1["LIST_MANAGER_TEST_RACE_DELAY"] = "1.0"
    writer1 = subprocess.Popen(
        [sys.executable, str(LISTS_PY), "update", "todo", "--cloud", "--expected-revision", "0"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env1,
    )
    # Give writer1 time to acquire the cloud lock, download, and pass
    # check_revision before writer2 starts -- so their windows genuinely
    # overlap rather than merely running one after the other.
    time.sleep(0.3)

    env2 = env_base.copy()
    writer2 = subprocess.Popen(
        [sys.executable, str(LISTS_PY), "update", "todo", "--cloud", "--expected-revision", "0"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env2,
    )

    out1, err1 = writer1.communicate(input="- id: a3f2b9\n  state: complete\n", timeout=15)
    out2, err2 = writer2.communicate(input="- id: b7c1e2\n  state: complete\n", timeout=15)

    assert writer1.returncode == 0, err1
    assert writer2.returncode != 0, (
        f"writer2 unexpectedly succeeded (cloud writes were not serialized -- "
        f"lost-update race is open): stdout={out2!r} stderr={err2!r}"
    )
    assert "revision" in err2.lower()

    data = yaml.safe_load((cloud_dir / "todo.yaml").read_text())
    entry_a = data["categories"][0]["categories"][3]["entries"][0]
    entry_b = data["categories"][0]["categories"][3]["entries"][1]
    assert entry_a["state"] == "complete"     # writer1's change applied
    assert entry_b["state"] == "inprogress"   # writer2's change never applied
    assert data["revision"] == 1              # exactly one successful write occurred
