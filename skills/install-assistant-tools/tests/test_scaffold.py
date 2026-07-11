from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_rtx"))

import _install_scaffold as scaffold


def write_runtime_dependencies_manifest(repo_root: Path, python_packages: list[str]) -> None:
    manifest = repo_root / "references" / "blueprint" / "runtime_dependencies.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {},
                "all": {"python": python_packages, "binary": ["rg"]},
            }
        ),
        encoding="utf-8",
    )


def test_run_writes_dispatcher_and_invoke_skill_launchers(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    status = scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)

    dispatcher = bin_dir / "dispatcher"
    invoke_skill = bin_dir / "invoke-skill"
    assert status == 0
    assert dispatcher.is_file()
    assert invoke_skill.is_file()
    assert dispatcher.stat().st_mode & 0o111  # executable bits set
    assert str(repo_root) in dispatcher.read_text()
    assert "_agent_invoker.sh" not in invoke_skill.read_text(encoding="utf-8")


def test_run_writes_windows_dispatcher_and_reports_unsupported_invoke_skill(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(scaffold, "ensure_path_windows", lambda *args, **kwargs: None)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"

    status = scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, dry_run=False)

    output = capsys.readouterr().out
    dispatcher = bin_dir / "dispatcher.bat"
    assert status == 0
    assert dispatcher.is_file()
    assert "py -3 -m officina.dispatcher.cli %*" in dispatcher.read_text(encoding="utf-8")
    assert "OK: dispatcher" in output
    assert "UNSUPPORTED: invoke-skill" in output
    assert "recurring-tasks is currently systemd/Unix-only" in output
    assert not (bin_dir / "dispatcher").exists()
    assert not (bin_dir / "invoke-skill").exists()


def test_run_adds_bin_dir_to_path_in_rc_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("# pre-existing line\n")

    scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)

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

    status = scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=True)

    assert status == 0
    assert not (bin_dir / "dispatcher").exists()
    assert rc_file.read_text() == ""


def test_run_dry_run_reports_required_capabilities(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    status = scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=True)

    output = capsys.readouterr().out
    assert status == 0
    assert "Scaffold capability report:" in output
    assert "WOULD-INSTALL: dispatcher" in output
    assert "WOULD-INSTALL: invoke-skill" in output
    assert "machine-interface dispatch" in output
    assert "recurring automation" in output


def test_run_reruns_idempotently(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)
    scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)

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
    write_runtime_dependencies_manifest(repo_root, ["dateparser"])
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)

    assert any("dateparser" in " ".join(cmd) for cmd in calls)


def test_run_installs_python_packages_from_runtime_dependency_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(
        scaffold.subprocess, "run",
        lambda cmd, **kw: (calls.append(cmd), type("R", (), {"returncode": 0, "stderr": ""})())[1],
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_runtime_dependencies_manifest(repo_root, ["PyYAML", "jsonschema"])
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)

    installed = {" ".join(cmd) for cmd in calls}
    assert any("PyYAML" in cmd for cmd in installed)
    assert any("jsonschema" in cmd for cmd in installed)
    assert not any(" rg " in f" {cmd} " for cmd in installed)
