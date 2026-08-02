from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_rtx"))

import _install_scaffold as scaffold
import officina.common.certificate_records as certificate_records
from install_test_utils import assert_default_bin_dir_matches_famulus_paths


def write_runtime_dependencies_manifest(repo_root: Path, python_packages: list[str]) -> None:
    manifest = repo_root / "references" / "blueprint" / "runtime_dependencies.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {
                    "fixture": {
                        "interfaces": {
                            "run": {
                                "dependencies": [
                                    {
                                        "kind": "python-package",
                                        "name": package,
                                        "version": "any",
                                        "platforms": {"linux": True, "macos": True, "windows": True},
                                    }
                                    for package in python_packages
                                ]
                            }
                        }
                    }
                },
                "all": {"python-package": python_packages, "binary": ["rg"]},
            }
        ),
        encoding="utf-8",
    )


def test_default_bin_dir_is_not_under_documents(tmp_path):
    assert_default_bin_dir_matches_famulus_paths(scaffold.default_bin_dir, tmp_path)


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
    if os.name != "nt":
        assert dispatcher.stat().st_mode & 0o111  # executable bits set
    dispatcher_text = dispatcher.read_text()
    invoke_text = invoke_skill.read_text(encoding="utf-8")
    # Generated launchers resolve the active release at launch time through
    # the stable managed-runtime resolver instead of embedding this repo
    # checkout's path or this test process's own interpreter.
    assert str(repo_root) not in dispatcher_text
    assert sys.executable not in dispatcher_text
    assert "bootstrap" in dispatcher_text and "resolvers" in dispatcher_text and "launch.py" in dispatcher_text
    assert "os.execv(RESOLVER" in dispatcher_text
    assert sys.executable not in invoke_text
    assert "_agent_invoker.sh" not in invoke_text


def test_run_writes_windows_dispatcher_and_invoke_skill_launchers(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(scaffold, "ensure_path_windows", lambda *args, **kwargs: None)
    # A real Windows host always has LOCALAPPDATA set; resolving the
    # resolver's fixed path needs it now that this monkeypatches sys.platform.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    # Dispatcher.bat generation resolves a concrete interpreter path via
    # shutil.which; mock it (as test_schedule_backend.py's Windows tests
    # do) rather than let the real shutil.which run its win32-specific
    # branch on this non-Windows test host, where it would fail since
    # _winapi isn't importable.
    monkeypatch.setattr(
        "_install_launcher._windows_launcher.shutil.which",
        lambda name: r"C:\Python312\python.exe" if name == "python" else None,
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"

    status = scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, dry_run=False)

    output = capsys.readouterr().out
    dispatcher = bin_dir / "dispatcher.bat"
    invoke_skill = bin_dir / "invoke-skill.bat"
    assert status == 0
    assert dispatcher.is_file()
    assert invoke_skill.is_file()
    dispatcher_text = dispatcher.read_text(encoding="utf-8")
    assert "-m officina.dispatcher.cli %*" in dispatcher_text
    assert "bootstrap" in dispatcher_text and "resolvers" in dispatcher_text and "launch.py" in dispatcher_text
    assert str(repo_root) not in dispatcher_text
    assert sys.executable not in dispatcher_text
    assert "py -3" not in dispatcher_text
    assert "assistant --local --claude" in invoke_skill.read_text(encoding="utf-8")
    assert "OK: dispatcher" in output
    assert "OK: invoke-skill" in output
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


def test_run_never_shells_out_to_ambient_python_for_package_install(tmp_path, monkeypatch):
    """Scaffold must not install third-party Python packages into the
    ambient interpreter at all (that's build_candidate_release's job, into
    the managed release venv, run by _phase_entry.py before scaffold.run
    ever executes) -- confirms feedback item 2/3's ambient-python-install
    violation is fully gone, not just relocated to a different call site."""
    monkeypatch.setattr(sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda cmd, **kw: (calls.append(cmd), type("R", (), {"returncode": 0, "stderr": ""})())[1],
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_runtime_dependencies_manifest(repo_root, ["dateparser"])
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)

    for cmd in calls:
        assert sys.executable not in cmd, f"scaffold invoked ambient sys.executable: {cmd}"
        assert "pip" not in cmd, f"scaffold ran its own pip install: {cmd}"


def test_run_warns_but_does_not_block_when_no_managed_release_is_active(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    status = scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False)

    output = capsys.readouterr().out
    assert status == 0
    assert "NOTE: no managed-runtime release is active yet" in output


def test_required_python_packages_preserve_declared_versions(tmp_path):
    repo_root = tmp_path / "repo"
    manifest = repo_root / scaffold.RUNTIME_DEPENDENCIES_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {
                    "skill-certifier": {
                        "interfaces": {
                            "certify": {
                                "dependencies": [
                                    {
                                        "kind": "python-package",
                                        "name": "cryptography",
                                        "version": ">=44.0.1",
                                        "platforms": {"linux": True, "macos": True, "windows": True},
                                    }
                                ]
                            }
                        }
                    }
                },
                "all": {"python-package": ["cryptography"], "binary": []},
            }
        ),
        encoding="utf-8",
    )

    assert scaffold.required_python_packages(repo_root) == [
        "cryptography>=44.0.1"
    ]


@pytest.mark.parametrize(
    ("runtime_platform", "expected"),
    [
        ("linux", ["example<2"]),
        ("darwin", ["EXAMPLE!=1.5"]),
        ("win32", ["Example~=1.8"]),
    ],
)
def test_required_python_packages_uses_first_declared_version_per_platform(
    tmp_path,
    monkeypatch,
    runtime_platform,
    expected,
):
    """Each platform sees one declared spec per package name: the first
    matching declaration in manifest order wins (see
    officina.install.managed_runtime.declared_python_packages)."""
    repo_root = tmp_path / "repo"
    manifest = repo_root / scaffold.RUNTIME_DEPENDENCIES_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {
                    "fixture": {
                        "interfaces": {
                            "run": {
                                "dependencies": [
                                    {
                                        "kind": "python-package",
                                        "name": "example",
                                        "version": "<2",
                                        "platforms": {
                                            "linux": True,
                                            "macos": False,
                                            "windows": False,
                                        },
                                    },
                                    {
                                        "kind": "python-package",
                                        "name": "EXAMPLE",
                                        "version": "!=1.5",
                                        "platforms": {
                                            "linux": False,
                                            "macos": True,
                                            "windows": False,
                                        },
                                    },
                                    {
                                        "kind": "python-package",
                                        "name": "Example",
                                        "version": "~=1.8",
                                        "platforms": {
                                            "linux": False,
                                            "macos": False,
                                            "windows": True,
                                        },
                                    },
                                ]
                            }
                        }
                    }
                },
                "all": {"python-package": ["Example"], "binary": []},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(scaffold.sys, "platform", runtime_platform)

    assert scaffold.required_python_packages(repo_root) == expected


def test_runtime_dependencies_manifest_is_still_schema_v1():
    repo_root = Path(__file__).resolve().parents[3]
    manifest_path = repo_root / scaffold.RUNTIME_DEPENDENCIES_MANIFEST
    payload = json.loads(manifest_path.read_text())
    assert payload["version"] == 1
    assert "skills" in payload
    # Spot check a known live entry keeps the documented shape.
    entry = payload["skills"]["install-assistant-tools"]["interfaces"]["scripts-install"]
    dep = entry["dependencies"][0]
    assert set(dep) >= {"kind", "name", "platforms"}
    assert set(dep["platforms"]) <= {"linux", "macos", "windows"}


def test_certificate_signing_material_capability_uses_shared_owner(
    tmp_path,
    monkeypatch,
):
    calls = []
    public_key_root = tmp_path / "repo" / "keys"
    monkeypatch.setattr(
        certificate_records,
        "provision_certificate_signing_material",
        lambda repo_root: calls.append(repo_root),
    )
    monkeypatch.setattr(
        certificate_records,
        "certificate_public_key_root",
        lambda repo_root: public_key_root,
    )

    result = scaffold.install_certificate_signing_material(
        tmp_path / "repo",
        dry_run=False,
    )

    assert calls == [tmp_path / "repo"]
    assert result.status == "installed"
    assert result.path == public_key_root


def test_certificate_signing_material_capability_fails_closed(
    tmp_path,
    monkeypatch,
):
    def fail(_repo_root):
        raise ValueError("verification failed")

    monkeypatch.setattr(
        certificate_records,
        "provision_certificate_signing_material",
        fail,
    )

    result = scaffold.install_certificate_signing_material(
        tmp_path / "repo",
        dry_run=False,
    )

    assert result.blocks_install()
    assert result.reason == "verification failed"


def test_certificate_signing_material_capability_fails_clearly_when_cryptography_missing(
    tmp_path,
    monkeypatch,
):
    """On a fresh machine whose ambient interpreter never had `cryptography`
    installed, certificate_records.py's module-level `import cryptography`
    raises ModuleNotFoundError. This must surface as a clear, actionable
    capability-failure reason -- not a raw traceback, and without the
    installer silently pip-installing anything into the ambient
    interpreter (that anti-pattern was deliberately removed elsewhere)."""

    def fail_import():
        raise ModuleNotFoundError("No module named 'cryptography'", name="cryptography")

    monkeypatch.setattr(scaffold, "_import_certificate_records", fail_import)

    result = scaffold.install_certificate_signing_material(
        tmp_path / "repo",
        dry_run=False,
    )

    assert result.blocks_install()
    assert result.status == "failed"
    assert "cryptography" in result.reason
    assert "pip install cryptography" in result.reason


def test_certificate_signing_material_dry_run_does_not_write(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        certificate_records,
        "provision_certificate_signing_material",
        lambda _repo_root: (_ for _ in ()).throw(
            AssertionError("dry-run wrote signing material")
        ),
    )

    result = scaffold.install_certificate_signing_material(
        tmp_path / "repo",
        dry_run=True,
    )

    assert result.status == "would-install"


# ── uv_release_target: resolves the real uv release-asset naming ────────────


@pytest.mark.parametrize(
    "platform_name,machine,expected_triple,expected_extension",
    [
        ("linux", "x86_64", "x86_64-unknown-linux-gnu", ".tar.gz"),
        ("linux", "aarch64", "aarch64-unknown-linux-gnu", ".tar.gz"),
        ("macos", "x86_64", "x86_64-apple-darwin", ".tar.gz"),
        ("macos", "arm64", "aarch64-apple-darwin", ".tar.gz"),
        ("windows", "AMD64", "x86_64-pc-windows-msvc", ".zip"),
        ("windows", "ARM64", "aarch64-pc-windows-msvc", ".zip"),
    ],
)
def test_uv_release_target_resolves_real_asset_naming(
    platform_name, machine, expected_triple, expected_extension
):
    triple, extension = scaffold.uv_release_target(platform_name=platform_name, machine=machine)
    assert triple == expected_triple
    assert extension == expected_extension


def test_uv_release_target_rejects_unsupported_platform():
    with pytest.raises(scaffold.UvReleaseTargetError, match="platform"):
        scaffold.uv_release_target(platform_name="freebsd", machine="x86_64")


def test_uv_release_target_rejects_unsupported_machine():
    with pytest.raises(scaffold.UvReleaseTargetError, match="architecture|machine"):
        scaffold.uv_release_target(platform_name="linux", machine="sparc64")
