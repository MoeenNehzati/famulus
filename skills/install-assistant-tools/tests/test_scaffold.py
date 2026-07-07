from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import scaffold


def test_run_writes_dispatcher_and_invoke_skill_launchers(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    scaffold.run(repo_root=repo_root, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)

    dispatcher = bin_dir / "dispatcher"
    invoke_skill = bin_dir / "invoke-skill"
    assert dispatcher.is_file()
    assert invoke_skill.is_file()
    assert dispatcher.stat().st_mode & 0o111  # executable bits set
    assert str(repo_root) in dispatcher.read_text()


def test_run_adds_bin_dir_to_path_in_rc_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("# pre-existing line\n")

    scaffold.run(repo_root=repo_root, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)

    content = rc_file.read_text()
    assert "# pre-existing line" in content
    assert f'export PATH="{bin_dir}:$PATH"' in content
    # scaffold must not write ASSISTANT_DEFAULT or AI — those belong to
    # launchers/dev-link
    assert "ASSISTANT_DEFAULT" not in content
    assert not content.count("export AI=")


def test_run_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    scaffold.run(repo_root=repo_root, bin_dir=bin_dir, shell_rc=rc_file, dry_run=True)

    assert not (bin_dir / "dispatcher").exists()
    assert rc_file.read_text() == ""


def test_run_reruns_idempotently(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    scaffold.run(repo_root=repo_root, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)
    scaffold.run(repo_root=repo_root, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)

    content = rc_file.read_text()
    assert content.count('export PATH="') == 1


def test_run_installs_required_python_packages(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(
        scaffold.subprocess, "run",
        lambda cmd, **kw: (calls.append(cmd), type("R", (), {"returncode": 0, "stderr": ""})())[1],
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    scaffold.run(repo_root=repo_root, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)

    assert any("dateparser" in " ".join(cmd) for cmd in calls)
