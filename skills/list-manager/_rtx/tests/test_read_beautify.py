from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from .. import _list_beautify as beautify
from .. import _render_bridge as render_bridge

READ_BEAUTIFY_PY = Path(__file__).parent.parent / "_render_bridge.py"
REPO_SRC = Path(__file__).resolve().parents[4] / "src"
SCRIPTS_DIR = READ_BEAUTIFY_PY.parent


def run(args: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_SRC), str(SCRIPTS_DIR)])
    return subprocess.run(
        [sys.executable, str(READ_BEAUTIFY_PY)] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=env,
    )


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


def test_read_beautify_default_executable_chain(todo_file):
    result = run([str(todo_file), "--sort", "deadline"])
    assert result.returncode == 0, result.stderr
    assert "schema:" not in result.stdout
    assert "2d overdue" in result.stdout
    assert "due today" in result.stdout
    assert "Overdue task" in result.stdout
    assert "```diff" not in result.stdout
    assert "| # |" not in result.stdout
    assert "- [ ] Overdue task" in result.stdout
    assert "in 1d" not in result.stdout
    assert "- [x] ~~Tomorrow task~~  `#cccccc`" in result.stdout


def test_render_bridge_forwards_exact_protocol_and_child_errors(tmp_path, capsys):
    source = tmp_path / "todo.yaml"
    source.write_text("unused by the adapter spy")

    scenarios = [
        (
            [str(source), "state=incomplete", "--sort", "deadline"],
            [sys.executable, str(render_bridge.LISTS_PY), "read", str(source), "--sort", "deadline", "state=incomplete"],
            [sys.executable, str(render_bridge.BEAUTIFY_PY), "--relative-deadlines", "--markdown", "--ids"],
        ),
        (
            [str(source), "--markdown", "--table", "--diff", "--no-descriptions", "--no-ids"],
            [sys.executable, str(render_bridge.LISTS_PY), "read", str(source)],
            [sys.executable, str(render_bridge.BEAUTIFY_PY), "--relative-deadlines", "--diff", "--no-descriptions"],
        ),
        (
            [str(source), "--markdown", "--table"],
            [sys.executable, str(render_bridge.LISTS_PY), "read", str(source)],
            [sys.executable, str(render_bridge.BEAUTIFY_PY), "--relative-deadlines", "--table", "--ids"],
        ),
    ]

    for argv, expected_read, expected_beautify in scenarios:
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            if command[1] == str(render_bridge.LISTS_PY):
                return subprocess.CompletedProcess(command, 0, "filtered-yaml\n", "")
            return subprocess.CompletedProcess(command, 0, "rendered\n", "")

        with patch.object(render_bridge.subprocess, "run", side_effect=fake_run):
            assert render_bridge.main(argv) == 0

        assert [call[0] for call in calls] == [expected_read, expected_beautify]
        assert calls[0][1] == {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "strict",
            "check": False,
        }
        assert calls[1][1] == {
            "input": "filtered-yaml\n",
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "strict",
            "check": False,
        }
        assert capsys.readouterr().out == "rendered\n"

    read_error = subprocess.CompletedProcess([], 7, "partial\n", "read failed\n")
    with patch.object(render_bridge.subprocess, "run", return_value=read_error) as child:
        assert render_bridge.main([str(source)]) == 7
    assert child.call_count == 1
    captured = capsys.readouterr()
    assert captured.out == "partial\n"
    assert captured.err == "read failed\n"

    results = [
        subprocess.CompletedProcess([], 0, "filtered-yaml\n", ""),
        subprocess.CompletedProcess([], 9, "partial render\n", "render failed\n"),
    ]
    with patch.object(render_bridge.subprocess, "run", side_effect=results):
        assert render_bridge.main([str(source)]) == 9
    captured = capsys.readouterr()
    assert captured.out == "partial render\n"
    assert captured.err == "render failed\n"


def test_renderer_modes_and_filtered_ids(todo_file):
    filtered = yaml.safe_load(todo_file.read_text())
    filtered["categories"][0]["entries"] = [
        entry
        for entry in filtered["categories"][0]["entries"]
        if entry["state"] == "incomplete"
    ]
    source = yaml.safe_dump(filtered, sort_keys=False)

    def render(*, diff=False, table=False, markdown=False, ids=False):
        args = argparse.Namespace(
            no_descriptions=False,
            diff=diff,
            table=table,
            markdown=markdown,
            relative_deadlines=True,
            ids=ids,
        )
        output = io.StringIO()
        with (
            patch.object(sys, "stdin", io.StringIO(source)),
            patch.object(beautify, "_SHOW_IDS", False),
            redirect_stdout(output),
        ):
            assert beautify._run_from_args(args) == 0
        return output.getvalue()

    diff = render(diff=True, ids=True)
    assert "```diff" in diff
    assert "[2d overdue]" in diff
    assert "#aaaaaa" in diff

    table = render(table=True)
    assert "| # |" in table
    assert "2d overdue" in table
    assert "#aaaaaa" not in table

    markdown = render(markdown=True, ids=True)
    assert "Tomorrow task" not in markdown
    assert "Overdue task" in markdown
    assert "#aaaaaa" in markdown
    assert "#bbbbbb" in markdown
