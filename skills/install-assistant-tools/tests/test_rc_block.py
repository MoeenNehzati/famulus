from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_rtx"))

from _shell_block import BLOCK_BEGIN, BLOCK_END, ensure_rc_vars


def test_ensure_rc_vars_writes_new_block(tmp_path):
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("# existing content\n")

    ensure_rc_vars(rc_file, {"PATH": 'export PATH="/bin/dir:$PATH"'}, dry_run=False)

    content = rc_file.read_text()
    assert "# existing content" in content
    assert BLOCK_BEGIN in content
    assert 'export PATH="/bin/dir:$PATH"' in content
    assert BLOCK_END in content


def test_ensure_rc_vars_merges_without_clobbering_other_vars(tmp_path):
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    ensure_rc_vars(rc_file, {"PATH": 'export PATH="/bin/dir:$PATH"'}, dry_run=False)
    ensure_rc_vars(rc_file, {"ASSISTANT_DEFAULT": "export ASSISTANT_DEFAULT=claude"}, dry_run=False)

    content = rc_file.read_text()
    assert 'export PATH="/bin/dir:$PATH"' in content
    assert "export ASSISTANT_DEFAULT=claude" in content
    # Only one managed block, not two
    assert content.count(BLOCK_BEGIN) == 1


def test_ensure_rc_vars_replaces_existing_value_for_same_key(tmp_path):
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    ensure_rc_vars(rc_file, {"ASSISTANT_DEFAULT": "export ASSISTANT_DEFAULT=claude"}, dry_run=False)
    ensure_rc_vars(rc_file, {"ASSISTANT_DEFAULT": "export ASSISTANT_DEFAULT=codex"}, dry_run=False)

    content = rc_file.read_text()
    assert "export ASSISTANT_DEFAULT=codex" in content
    assert "export ASSISTANT_DEFAULT=claude" not in content


def test_ensure_rc_vars_does_not_accumulate_blank_lines_across_repeated_writes(tmp_path):
    """Regression: three separate callers (scaffold/launchers/dev_link) each
    writing their one var, one after another, must not each add another
    blank separator line before the block."""
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("# user line\n")

    ensure_rc_vars(rc_file, {"AI": 'export AI="/repo"'}, dry_run=False)
    ensure_rc_vars(rc_file, {"PATH": 'export PATH="/bin:$PATH"'}, dry_run=False)
    ensure_rc_vars(rc_file, {"ASSISTANT_DEFAULT": "export ASSISTANT_DEFAULT=claude"}, dry_run=False)

    content = rc_file.read_text()
    assert content.startswith("# user line\n\n" + BLOCK_BEGIN)


def test_ensure_rc_vars_dry_run_does_not_write(tmp_path, capsys):
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("original\n")

    ensure_rc_vars(rc_file, {"PATH": 'export PATH="/bin/dir:$PATH"'}, dry_run=True)

    assert rc_file.read_text() == "original\n"
    assert "Would update" in capsys.readouterr().out
