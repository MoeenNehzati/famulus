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


def test_render_bridge_forwards_exact_protocol_and_entrypoint_errors(tmp_path, capsys):
    source = tmp_path / "todo.yaml"
    source.write_text("unused by the adapter spy")

    scenarios = [
        (
            [str(source), "state=incomplete", "--sort", "deadline"],
            ["read", str(source), "--sort", "deadline", "state=incomplete"],
            ["--relative-deadlines", "--markdown", "--ids"],
        ),
        (
            [str(source), "--markdown", "--table", "--diff", "--no-descriptions", "--no-ids"],
            ["read", str(source)],
            ["--relative-deadlines", "--diff", "--no-descriptions"],
        ),
        (
            [str(source), "--markdown", "--table"],
            ["read", str(source)],
            ["--relative-deadlines", "--table", "--ids"],
        ),
    ]

    for argv, expected_read, expected_beautify in scenarios:
        calls = []

        def fake_entrypoint(main, command, *, stdin=""):
            calls.append((main, command, stdin))
            if main is render_bridge._yaml_store.main:
                return 0, "filtered-yaml\n", ""
            return 0, "rendered\n", ""

        with patch.object(render_bridge, "_run_entrypoint", side_effect=fake_entrypoint):
            assert render_bridge.main(argv) == 0

        assert [call[1] for call in calls] == [expected_read, expected_beautify]
        assert calls[1][2] == "filtered-yaml\n"
        assert capsys.readouterr().out == "rendered\n"

    with patch.object(render_bridge, "_run_entrypoint", return_value=(7, "partial\n", "read failed\n")) as child:
        assert render_bridge.main([str(source)]) == 7
    assert child.call_count == 1
    captured = capsys.readouterr()
    assert captured.out == "partial\n"
    assert captured.err == "read failed\n"

    results = [(0, "filtered-yaml\n", ""), (9, "partial render\n", "render failed\n")]
    with patch.object(render_bridge, "_run_entrypoint", side_effect=results):
        assert render_bridge.main([str(source)]) == 9
    captured = capsys.readouterr()
    assert captured.out == "partial render\n"
    assert captured.err == "render failed\n"


def test_cloud_transport_dispatch_has_no_process_timeout(monkeypatch):
    observed = {}

    class Result:
        returncode = 0
        stdout = "content"
        stderr = ""

    def dispatch(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(render_bridge.cloud_transport._DISPATCHER, "dispatch", dispatch)

    assert render_bridge.cloud_transport._dispatch("lists-read", "lists/todo.yaml") == (0, "content", "")
    assert observed["kwargs"] == {
        "args": ["lists/todo.yaml"],
        "stdin": None,
        "capture_output": True,
        "text": True,
        "check": False,
    }


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
