"""Integration tests for lists.py subcommands. All tests operate on local temp files."""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

LISTS_PY = Path(__file__).parent.parent / "scripts" / "lists.py"

# A valid todo YAML used by multiple tests
TODO_YAML = """\
schema: todo
name: todo
categories:
- name: Work
  categories:
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
- name: Personal
  entries:
  - id: c3d1e5
    title: Book dentist
    state: done
    created: '2026-06-20'
    deadline: '2026-06-30'
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


def test_read_filter_no_matches(todo_file):
    result = run(["read", str(todo_file), "state=cancelled"])
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
    writing_cat = data["categories"][0]["categories"][0]
    assert writing_cat["name"] == "Writing"
    assert len(writing_cat["entries"]) == 3
    assert writing_cat["entries"][2]["title"] == "Draft blog post"


def test_create_entry_assigns_id(todo_file):
    result = run(["create-entry", str(todo_file), "Work/Writing"], stdin=NEW_ENTRY_NO_ID_YAML)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    new_entry = data["categories"][0]["categories"][0]["entries"][2]
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
    parent = data["categories"][0]["categories"][0]["entries"][0]
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
    assert len(data["categories"][0]["categories"][0]["entries"]) == 3


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
    entries = data["categories"][0]["categories"][0]["entries"]
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
    entry = data["categories"][0]["categories"][0]["entries"][0]
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
    entries = data["categories"][0]["categories"][0]["entries"]
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
    entry = data["categories"][0]["categories"][0]["entries"][0]
    assert entry["state"] == "done"


# ── migrate-md ────────────────────────────────────────────────────────────────

SAMPLE_MD = """\
# My Todo List

## Work

- [ ] Finish quarterly report (due: 2026-07-15)
- [x] Review team PRs (due: 2026-06-30)

### Writing

- [ ] Draft blog post (due: 2026-07-01)

## Personal

- [ ] Book dentist (due: 2026-07-10)
"""


def test_migrate_md_creates_yaml(tmp_path):
    src = tmp_path / "todo.md"
    dst = tmp_path / "todo.yaml"
    src.write_text(SAMPLE_MD)
    result = run(["migrate-md", str(src), str(dst), "--schema", "todo"])
    assert result.returncode == 0, result.stderr
    assert dst.exists()
    data = yaml.safe_load(dst.read_text())
    assert data["schema"] == "todo"
    assert isinstance(data["categories"], list)
    assert len(data["categories"]) >= 1


def test_migrate_md_preserves_done_state(tmp_path):
    src = tmp_path / "todo.md"
    dst = tmp_path / "todo.yaml"
    src.write_text(SAMPLE_MD)
    run(["migrate-md", str(src), str(dst), "--schema", "todo"])
    data = yaml.safe_load(dst.read_text())
    # Find the done entry (Review team PRs)
    all_entries = []

    def collect(node):
        if isinstance(node, dict) and "id" in node:
            all_entries.append(node)
            for child in node.get("children", []):
                collect(child)
        elif isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, dict):
            for v in node.values():
                collect(v)

    collect(data)
    done_entries = [e for e in all_entries if e.get("state") == "done"]
    assert len(done_entries) >= 1


def test_migrate_md_iso_deadlines_preserved(tmp_path):
    src = tmp_path / "todo.md"
    dst = tmp_path / "todo.yaml"
    src.write_text(SAMPLE_MD)
    run(["migrate-md", str(src), str(dst), "--schema", "todo"])
    data = yaml.safe_load(dst.read_text())
    # Finish quarterly report has deadline 2026-07-15
    all_entries = []

    def collect(node):
        if isinstance(node, dict) and "id" in node:
            all_entries.append(node)
        if isinstance(node, dict):
            for v in node.values():
                collect(v)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(data)
    report = next((e for e in all_entries if "quarterly" in e.get("title", "").lower()), None)
    assert report is not None
    assert report["deadline"] == "2026-07-15"


def test_migrate_md_fails_if_dst_exists(tmp_path):
    src = tmp_path / "todo.md"
    dst = tmp_path / "todo.yaml"
    src.write_text(SAMPLE_MD)
    dst.write_text("existing content")
    result = run(["migrate-md", str(src), str(dst), "--schema", "todo"])
    assert result.returncode != 0
    assert "exists" in result.stderr


def test_migrate_md_custom_name(tmp_path):
    src = tmp_path / "todo.md"
    dst = tmp_path / "out.yaml"
    src.write_text(SAMPLE_MD)
    result = run(["migrate-md", str(src), str(dst), "--schema", "todo", "--name", "Work Tasks"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(dst.read_text())
    assert data["name"] == "Work Tasks"
