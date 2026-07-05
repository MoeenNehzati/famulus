"""Integration tests for lists.py subcommands. All tests operate on local temp files."""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

LISTS_PY = Path(__file__).parent.parent / "scripts" / "lists.py"

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
      state: done
      created: '2026-06-20'
      deadline: '2026-06-30'
  - name: Misc
  - name: Shop
"""


def run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LISTS_PY)] + args,
        input=stdin,
        capture_output=True,
        text=True,
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
    assert data["categories"] == []


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


def test_init_potential_actions_schema(tmp_path):
    f = tmp_path / "pa.yaml"
    result = run(["init", str(f), "--schema", "potential-actions"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    assert data["schema"] == "potential-actions"


def test_init_default_schema(tmp_path):
    f = tmp_path / "notes.yaml"
    result = run(["init", str(f), "--schema", "default"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    assert data["schema"] == "default"


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

def test_read_unfiltered_returns_full_doc(todo_file):
    result = run(["read", str(todo_file)])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(result.stdout)
    assert data["schema"] == "todo"
    assert len(data["categories"]) == 2


def test_read_filter_exact_match(todo_file):
    result = run(["read", str(todo_file), "state=incomplete"])
    assert result.returncode == 0, result.stderr
    entries = yaml.safe_load(result.stdout)
    assert isinstance(entries, list)
    assert all(e["state"] == "incomplete" for e in entries)
    assert any(e["title"] == "Reply to Diego" for e in entries)


def test_read_filter_or_values(todo_file):
    result = run(["read", str(todo_file), "state=incomplete,inprogress"])
    assert result.returncode == 0, result.stderr
    entries = yaml.safe_load(result.stdout)
    assert len(entries) == 2
    states = {e["state"] for e in entries}
    assert states == {"incomplete", "inprogress"}


def test_read_filter_and_multiple_keys(todo_file):
    result = run(["read", str(todo_file), "state=incomplete", "location=home"])
    assert result.returncode == 0, result.stderr
    entries = yaml.safe_load(result.stdout)
    assert len(entries) == 1
    assert entries[0]["title"] == "Reply to Diego"


def test_read_filter_substring(todo_file):
    result = run(["read", str(todo_file), "title~=Diego"])
    assert result.returncode == 0, result.stderr
    entries = yaml.safe_load(result.stdout)
    assert len(entries) == 1
    assert entries[0]["id"] == "a3f2b9"


def test_read_filter_regex_anchored(todo_file):
    # ~= is a regex search: ^Reply matches "Reply to Diego" but not "Review draft".
    result = run(["read", str(todo_file), "title~=^Reply"])
    assert result.returncode == 0, result.stderr
    entries = yaml.safe_load(result.stdout)
    assert len(entries) == 1
    assert entries[0]["id"] == "a3f2b9"


def test_read_filter_regex_case_insensitive(todo_file):
    result = run(["read", str(todo_file), "title~=diego"])
    assert result.returncode == 0, result.stderr
    entries = yaml.safe_load(result.stdout)
    assert len(entries) == 1
    assert entries[0]["id"] == "a3f2b9"


def test_read_filter_ids_or(todo_file):
    # id filter with comma-OR selects an explicit set — the semantic-selection path.
    result = run(["read", str(todo_file), "id=a3f2b9,c3d1e5"])
    assert result.returncode == 0, result.stderr
    entries = yaml.safe_load(result.stdout)
    assert {e["id"] for e in entries} == {"a3f2b9", "c3d1e5"}


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
    patch.write_text("- id: b7c1e2\n  state: done\n")
    result = run(["update", str(f), "--file", str(patch)])
    assert result.returncode != 0
    assert "a3f2b9" in result.stderr, result.stderr
    assert "Reply to Diego" in result.stderr, result.stderr


def test_read_filter_invalid_enum_value_errors(todo_file):
    """state=cancelled isn't a valid state (incomplete/inprogress/done) -- this
    must be a hard error, not a silent empty result, so a typo'd filter value
    can't be misread as "nothing matches"."""
    result = run(["read", str(todo_file), "state=cancelled"])
    assert result.returncode != 0
    assert "cancelled" in result.stderr
    assert "incomplete" in result.stderr and "done" in result.stderr


def test_read_filter_no_matches_non_enum_field(todo_file):
    result = run(["read", str(todo_file), "location=nowhere"])
    assert result.returncode == 0, result.stderr
    entries = yaml.safe_load(result.stdout)
    assert entries == [] or entries is None


def test_read_filter_done(todo_file):
    result = run(["read", str(todo_file), "state=done"])
    assert result.returncode == 0, result.stderr
    entries = yaml.safe_load(result.stdout)
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
    update_yaml = "- id: a3f2b9\n  state: done\n"
    result = run(["update", str(todo_file)], stdin=update_yaml)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entry = data["categories"][0]["categories"][3]["entries"][0]
    assert entry["state"] == "done"


def test_update_multiple_entries(todo_file):
    update_yaml = """\
- id: a3f2b9
  state: done
- id: b7c1e2
  deadline: '2026-07-20'
"""
    result = run(["update", str(todo_file)], stdin=update_yaml)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entries = data["categories"][0]["categories"][3]["entries"]
    assert entries[0]["state"] == "done"
    assert entries[1]["deadline"] == "2026-07-20"


def test_update_immutable_created_rejected(todo_file):
    update_yaml = "- id: a3f2b9\n  created: '2026-01-01'\n"
    result = run(["update", str(todo_file)], stdin=update_yaml)
    assert result.returncode != 0
    assert "immutable" in result.stderr.lower()


def test_update_unknown_id_fails(todo_file):
    update_yaml = "- id: ffffff\n  state: done\n"
    result = run(["update", str(todo_file)], stdin=update_yaml)
    assert result.returncode != 0


def test_update_invalid_state_fails(todo_file):
    update_yaml = "- id: a3f2b9\n  state: undecided\n"
    result = run(["update", str(todo_file)], stdin=update_yaml)
    assert result.returncode != 0


def test_update_from_file(todo_file, tmp_path):
    updates_file = tmp_path / "updates.yaml"
    updates_file.write_text("- id: a3f2b9\n  state: done\n")
    result = run(["update", str(todo_file), "--file", str(updates_file)])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entry = data["categories"][0]["categories"][3]["entries"][0]
    assert entry["state"] == "done"


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
    assert out["entry_fields"]["state"]["enum"] == ["incomplete", "inprogress", "done"]
    assert "deadline" in out["required_fields"]
    assert "state" in out["auto_generated_fields"]


def test_describe_schema_single_field():
    result = run(["describe-schema", "todo", "state"])
    assert result.returncode == 0, result.stderr
    out = yaml.safe_load(result.stdout)
    assert out == {"state": {"enum": ["incomplete", "inprogress", "done"]}}


def test_describe_schema_unknown_field_errors():
    result = run(["describe-schema", "todo", "not_a_field"])
    assert result.returncode != 0
    assert "not_a_field" in result.stderr


def test_describe_schema_unknown_schema_errors():
    result = run(["describe-schema", "not-a-schema"])
    assert result.returncode != 0
    assert "not-a-schema" in result.stderr
