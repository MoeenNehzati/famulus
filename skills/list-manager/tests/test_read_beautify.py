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
    state: done
    created: '{today.isoformat()}'
    deadline: '{tomorrow}'
"""
    )
    return f


def test_read_beautify_diff_relative_deadlines(todo_file):
    result = run([str(todo_file), '--sort', 'deadline'])
    assert result.returncode == 0, result.stderr
    assert 'schema:' not in result.stdout
    assert '[2d overdue]' in result.stdout
    assert '[due today]' in result.stdout
    assert '[in 1d]' in result.stdout
    assert 'Overdue task' in result.stdout


def test_read_beautify_markdown_and_filter(todo_file):
    result = run([str(todo_file), 'state=incomplete', '--markdown'])
    assert result.returncode == 0, result.stderr
    assert 'Tomorrow task' not in result.stdout
    assert 'Overdue task' in result.stdout
    assert 'Today task' in result.stdout
