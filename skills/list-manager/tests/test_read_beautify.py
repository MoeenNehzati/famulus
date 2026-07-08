from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

READ_BEAUTIFY_PY = Path(__file__).parent.parent / "scripts" / "read_beautify.py"


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(READ_BEAUTIFY_PY)] + args, capture_output=True, text=True)


@pytest.fixture
def todo_file(tmp_path):
    today = date.today()
    overdue = (today - timedelta(days=2)).isoformat()
    due_today = today.isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()
    f = tmp_path / "todo.yaml"
    f.write_text(
        f"""schema: todo
name: todo
categories:
- name: Work
  entries:
  - id: aaaaaa
    title: Overdue task
    state: incomplete
    created: '{today.isoformat()}'
    deadline: '{overdue}'
  - id: bbbbbb
    title: Today task
    state: incomplete
    created: '{today.isoformat()}'
    deadline: '{due_today}'
  - id: cccccc
    title: Tomorrow task
    state: complete
    created: '{today.isoformat()}'
    deadline: '{tomorrow}'
"""
    )
    return f


def test_read_beautify_bullet_relative_deadlines(todo_file):
    result = run([str(todo_file), '--sort', 'deadline'])
    assert result.returncode == 0, result.stderr
    assert 'schema:' not in result.stdout
    assert '2d overdue' in result.stdout
    assert 'due today' in result.stdout
    assert 'Overdue task' in result.stdout
    # Default render is now a nested bullet list, not a ```diff``` fence or a table.
    assert '```diff' not in result.stdout
    assert '| # |' not in result.stdout
    assert '- [ ] Overdue task' in result.stdout
    # "Tomorrow task" is complete but has no recorded `completed` date -- it
    # must show no badge at all, not the (by-then-irrelevant) "in 1d" deadline.
    assert 'in 1d' not in result.stdout
    assert '- [x] ~~Tomorrow task~~  `#cccccc`' in result.stdout


def test_read_beautify_diff_flag(todo_file):
    result = run([str(todo_file), '--sort', 'deadline', '--diff'])
    assert result.returncode == 0, result.stderr
    assert '```diff' in result.stdout
    assert '[2d overdue]' in result.stdout


def test_read_beautify_table_flag(todo_file):
    result = run([str(todo_file), '--sort', 'deadline', '--table'])
    assert result.returncode == 0, result.stderr
    assert '| # |' in result.stdout
    assert '2d overdue' in result.stdout


def test_read_beautify_markdown_and_filter(todo_file):
    result = run([str(todo_file), 'state=incomplete', '--markdown'])
    assert result.returncode == 0, result.stderr
    assert 'Tomorrow task' not in result.stdout
    assert 'Overdue task' in result.stdout
    assert 'Today task' in result.stdout


def test_read_beautify_shows_ids_by_default(todo_file):
    result = run([str(todo_file)])
    assert result.returncode == 0, result.stderr
    # Each row carries its #id so follow-up edits key on it directly.
    assert '#aaaaaa' in result.stdout
    assert '#bbbbbb' in result.stdout


def test_read_beautify_no_ids(todo_file):
    result = run([str(todo_file), '--no-ids'])
    assert result.returncode == 0, result.stderr
    assert '#aaaaaa' not in result.stdout
    assert 'Overdue task' in result.stdout


def test_read_beautify_ids_survive_filter(todo_file):
    # The id must still be present in a filtered view (the whole point).
    result = run([str(todo_file), 'state=incomplete'])
    assert result.returncode == 0, result.stderr
    assert '#aaaaaa' in result.stdout
