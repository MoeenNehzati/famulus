from __future__ import annotations

import ast
import json
import os
import sys
import types
from pathlib import Path

import pytest

from officina.install.context import (
    load_or_create_development_installation_id,
    resolve_installation_context,
)
from .. import _install_scaffold as scaffold
from .._install_launcher import _windows_launcher as windows_launcher
from .._install_launcher._base_launcher import LauncherInstallerBase

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
from .install_test_utils import assert_default_bin_dir_matches_famulus_paths


def assigned_string(source: str, name: str) -> str:
    """Read one generated module constant without comparing escaped source."""
    for statement in ast.parse(source).body:
        if (
            isinstance(statement, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in statement.targets)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            return statement.value.value
    raise AssertionError(f"generated source has no string assignment for {name}")


def write_runtime_dependencies_manifest(repo_root: Path, python_packages: list[str]) -> None:
    manifest = repo_root / "references" / "blueprint-schema" / "runtime_dependencies.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "version": 2,
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


def test_windows_path_preexisting_component_is_not_manifest_owned(tmp_path, monkeypatch):
    bin_dir = tmp_path / "Bin"
    set_calls = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(), KEY_READ=1, KEY_WRITE=2, REG_EXPAND_SZ=3,
        OpenKey=lambda *_args: Key(),
        QueryValueEx=lambda _key, _name: (f"{bin_dir};C:\\Windows", 3),
        SetValueEx=lambda *args: set_calls.append(args),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    manifest = scaffold.Manifest(tmp_path / "manifest.json")

    scaffold.ensure_path_windows(bin_dir, False, manifest)

    assert set_calls == []
    assert not any(entry["kind"] == "registry_env" for entry in manifest.entries)


def test_windows_path_failed_write_leaves_no_manifest_claim(tmp_path, monkeypatch):
    bin_dir = tmp_path / "Bin"

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(), KEY_READ=1, KEY_WRITE=2, REG_EXPAND_SZ=3,
        OpenKey=lambda *_args: Key(),
        QueryValueEx=lambda _key, _name: (r"C:\Windows", 3),
        SetValueEx=lambda *_args: (_ for _ in ()).throw(OSError("injected failure")),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    manifest = scaffold.Manifest(tmp_path / "manifest.json")

    with pytest.raises(OSError, match="injected failure"):
        scaffold.ensure_path_windows(bin_dir, False, manifest)

    assert not any(entry["kind"] == "registry_env" for entry in manifest.entries)


def test_windows_path_manifest_commit_follows_write_and_commit_failure_rolls_back(
    tmp_path, monkeypatch
):
    bin_dir = tmp_path / "Bin"
    prior = r"C:\Windows;C:\Tools"
    state = {"PATH": prior}
    events = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def set_value(_key, _name, _reserved, _value_type, value):
        events.append(("set", value))
        state["PATH"] = value

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(), KEY_READ=1, KEY_WRITE=2, REG_EXPAND_SZ=3,
        OpenKey=lambda *_args: Key(),
        QueryValueEx=lambda _key, _name: (state["PATH"], 3),
        SetValueEx=set_value,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)

    class FailingManifest:
        entries = []

        def record(self, *_args, **_kwargs):
            events.append(("record", state["PATH"]))
            raise OSError("manifest commit failed")

    with pytest.raises(OSError, match="manifest commit failed"):
        scaffold.ensure_path_windows(bin_dir, False, FailingManifest())

    assert events == [("record", prior)]
    assert state["PATH"] == prior


def test_windows_path_pending_intent_recovers_hard_interrupt_after_write(
    tmp_path, monkeypatch
):
    bin_dir = tmp_path / "Bin"
    prior = r"C:\Windows"
    state = {"PATH": prior}
    writes = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def set_value(_key, _name, _reserved, _value_type, value):
        writes.append(value)
        state["PATH"] = value

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(), KEY_READ=1, KEY_WRITE=2, REG_EXPAND_SZ=3,
        OpenKey=lambda *_args: Key(),
        QueryValueEx=lambda _key, _name: (state["PATH"], 3),
        SetValueEx=set_value,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    manifest = scaffold.Manifest(tmp_path / "manifest.json")
    real_record = manifest.record

    def interrupt_commit(kind, *, path, **fields):
        if fields.get("transaction_state") == "committed":
            raise KeyboardInterrupt("hard interruption")
        real_record(kind, path=path, **fields)

    monkeypatch.setattr(manifest, "record", interrupt_commit)
    with pytest.raises(KeyboardInterrupt, match="hard interruption"):
        scaffold.ensure_path_windows(bin_dir, False, manifest)

    pending = scaffold.Manifest(manifest.path)
    assert pending.entries[0]["transaction_state"] == "pending"
    assert state["PATH"] == f"{bin_dir};{prior}"

    scaffold.ensure_path_windows(bin_dir, False, pending)

    assert scaffold.Manifest(manifest.path).entries[0]["transaction_state"] == "committed"
    assert writes == [f"{bin_dir};{prior}"]


def test_run_writes_dispatcher_wakeup_and_invoke_skill_launchers(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    status = scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False, environ=dict(os.environ))

    dispatcher = bin_dir / "dispatcher"
    llm_wakeup = bin_dir / "llm-wakeup"
    lw = bin_dir / "lw"
    invoke_skill = bin_dir / "invoke-skill"
    assert status == 0
    assert dispatcher.is_file()
    assert llm_wakeup.is_file()
    assert lw.is_file()
    assert invoke_skill.is_file()
    if os.name != "nt":
        assert dispatcher.stat().st_mode & 0o111
        assert llm_wakeup.stat().st_mode & 0o111
        assert lw.stat().st_mode & 0o111
    dispatcher_text = dispatcher.read_text()
    invoke_text = invoke_skill.read_text(encoding="utf-8")
    assert str(repo_root) not in dispatcher_text
    assert sys.executable not in dispatcher_text
    assert "bootstrap" in dispatcher_text and "resolvers" in dispatcher_text and "launch.py" in dispatcher_text
    assert "os.execv(RESOLVER" in dispatcher_text
    assert sys.executable not in invoke_text
    assert "_agent_invoker.sh" not in invoke_text


def test_development_scaffold_embeds_only_the_selected_context_runtime_root(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "platform", "linux")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    stable_home = tmp_path / "stable-home"
    identifier = load_or_create_development_installation_id(
        checkout,
        platform="linux",
        home=stable_home,
        environ={},
    )
    context = resolve_installation_context(
        mode="development",
        source_root=checkout,
        development_root=checkout,
        platform="linux",
        home=stable_home,
        environ={},
        installation_id=identifier,
    )
    hostile = {"XDG_DATA_HOME": str(tmp_path / "hostile-data")}
    monkeypatch.setenv("XDG_DATA_HOME", hostile["XDG_DATA_HOME"])

    scaffold.run(context=context, environ=hostile, dry_run=False)

    dispatcher_text = (context.paths.user_bin / "dispatcher").read_text(encoding="utf-8")
    invoke_text = (context.paths.user_bin / "invoke-skill").read_text(encoding="utf-8")
    expected_resolver = context.paths.runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"
    assert Path(assigned_string(dispatcher_text, "RESOLVER")) == expected_resolver
    assert Path(assigned_string(invoke_text, "RESOLVER")) == expected_resolver
    assert hostile["XDG_DATA_HOME"] not in dispatcher_text
    assert hostile["XDG_DATA_HOME"] not in invoke_text
    assert hostile["XDG_DATA_HOME"] not in capsys.readouterr().out


def test_invoke_skill_uses_selected_home_instead_of_ambient_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    selected_home = tmp_path / "selected-home"
    ambient_home = tmp_path / "ambient-home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: ambient_home))
    for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(name, raising=False)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    shell_rc = tmp_path / ".bashrc"
    shell_rc.write_text("", encoding="utf-8")

    scaffold.run(
        repo_root=repo_root,
        home=selected_home,
        bin_dir=bin_dir,
        shell_rc=shell_rc,
        dry_run=False,
        environ=dict(os.environ),
    )

    rendered = (bin_dir / "invoke-skill").read_text(encoding="utf-8")
    expected_resolver = (
        selected_home / ".local" / "share" / "famulus" / "runtime"
        / "bootstrap" / "resolvers" / "v1" / "launch.py"
    )
    rendered_resolver = Path(assigned_string(rendered, "RESOLVER"))
    assert rendered_resolver == expected_resolver
    assert ambient_home not in rendered_resolver.parents


def test_run_writes_windows_dispatcher_wakeup_and_invoke_skill_launchers(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(scaffold, "ensure_path_windows", lambda *args, **kwargs: None)
    # A real Windows host always has LOCALAPPDATA set; resolving the
    # resolver's fixed path needs it now that this monkeypatches sys.platform.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    # Dispatcher.bat generation resolves a concrete interpreter path via
    # shutil.which; mock it (as test_schedule_backend.py's Windows tests
    # do) rather than let the real shutil.which run its win32-specific
    # branch on this non-Windows test host, where it would fail since
    # _winapi isn't importable.
    monkeypatch.setattr(
        windows_launcher.shutil,
        "which",
        lambda name: r"C:\Python312\python.exe" if name == "python" else None,
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"

    status = scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, dry_run=False, environ=dict(os.environ))

    output = capsys.readouterr().out
    dispatcher = bin_dir / "dispatcher.bat"
    llm_wakeup = bin_dir / "llm-wakeup.bat"
    lw = bin_dir / "lw.bat"
    invoke_skill = bin_dir / "invoke-skill.bat"
    assert status == 0
    assert dispatcher.is_file()
    assert llm_wakeup.is_file()
    assert lw.is_file()
    assert invoke_skill.is_file()
    dispatcher_text = dispatcher.read_text(encoding="utf-8")
    assert "-m officina.dispatcher.cli %*" in dispatcher_text
    assert "bootstrap" in dispatcher_text and "resolvers" in dispatcher_text and "launch.py" in dispatcher_text
    assert str(repo_root) not in dispatcher_text
    assert sys.executable not in dispatcher_text
    assert "py -3" not in dispatcher_text
    assert (
        "-m officina.launchers.agent --invoke-skill %*"
        in invoke_skill.read_text(encoding="utf-8")
    )
    assert "OK: dispatcher" in output
    assert "OK: llm-wakeup" in output
    assert "OK: invoke-skill" in output
    assert not (bin_dir / "dispatcher").exists()
    assert not (bin_dir / "llm-wakeup").exists()
    assert not (bin_dir / "lw").exists()
    assert not (bin_dir / "invoke-skill").exists()


def test_windows_invoke_skill_receives_selected_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(scaffold, "ensure_path_windows", lambda *args, **kwargs: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setattr(
        windows_launcher.shutil,
        "which",
        lambda name: r"C:\Python312\python.exe" if name == "python" else None,
    )
    selected_home = tmp_path / "selected-home"
    seen_homes: list[Path | None] = []
    real_resolve = windows_launcher.resolve_famulus_paths

    def recording_resolve(*, platform, home, environ):
        seen_homes.append(home)
        return real_resolve(platform=platform, home=home, environ=environ)

    monkeypatch.setattr(windows_launcher, "resolve_famulus_paths", recording_resolve)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    scaffold.run(
        repo_root=repo_root,
        home=selected_home,
        bin_dir=tmp_path / "bin",
        dry_run=False,
        environ=dict(os.environ),
    )

    assert seen_homes
    assert all(home == selected_home for home in seen_homes)


def test_run_adds_bin_dir_to_path_in_rc_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("# pre-existing line\n")

    scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False, environ=dict(os.environ))

    content = rc_file.read_text()
    assert "# pre-existing line" in content
    assert f'export PATH="{bin_dir}:$PATH"' in content
    # Production persists neither legacy selector: launchers.json and the
    # active installation context own those decisions.
    assert "ASSISTANT_DEFAULT" not in content
    assert not content.count("export AI=")


def test_run_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    status = scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=True, environ=dict(os.environ))

    assert status == 0
    assert not (bin_dir / "dispatcher").exists()
    assert not (bin_dir / "llm-wakeup").exists()
    assert not (bin_dir / "lw").exists()
    assert rc_file.read_text() == ""


def test_run_dry_run_reports_required_capabilities(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    status = scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=True, environ=dict(os.environ))

    output = capsys.readouterr().out
    assert status == 0
    assert "Scaffold capability report:" in output
    assert "WOULD-INSTALL: dispatcher" in output
    assert "WOULD-INSTALL: llm-wakeup" in output
    assert "WOULD-INSTALL: invoke-skill" in output
    assert "machine-interface dispatch" in output
    assert "guarded LLM session wakeups" in output
    assert "recurring automation" in output


def test_run_reruns_idempotently(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False, environ=dict(os.environ))
    scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False, environ=dict(os.environ))

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

    scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False, environ=dict(os.environ))

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

    status = scaffold.run(repo_root=repo_root, home=tmp_path, bin_dir=bin_dir, shell_rc=rc_file, dry_run=False, environ=dict(os.environ))

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
                "version": 2,
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
                "version": 2,
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


def test_runtime_dependencies_manifest_uses_direct_route_schema_v2():
    repo_root = Path(__file__).resolve().parents[4]
    manifest_path = repo_root / scaffold.RUNTIME_DEPENDENCIES_MANIFEST
    payload = json.loads(manifest_path.read_text())
    assert payload["version"] == 2
    assert "skills" in payload
    # Spot check a known live entry keeps the documented shape.
    entry = payload["skills"]["install-assistant-tools"]["interfaces"][
        "install-assistant-tools._rtx.interface.scripts-install"
    ]
    dep = entry["dependencies"][0]
    assert set(dep) >= {"kind", "name", "platforms"}
    assert set(dep["platforms"]) <= {"linux", "macos", "windows"}


def test_certifier_runtime_declares_its_validator_runner_dependencies() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    payload = json.loads(
        (repo_root / scaffold.RUNTIME_DEPENDENCIES_MANIFEST).read_text(
            encoding="utf-8"
        )
    )
    dependencies = payload["skills"]["skill-certifier"]["interfaces"][
        "skill-certifier._rtx.interface.certify"
    ]["dependencies"]
    versions = {
        dependency["name"]: dependency["version"]
        for dependency in dependencies
    }

    assert versions["pytest"] == "==8.3.4"
    assert versions["pytest-xdist"] == "==3.8.0"
    assert versions["pyflakes"] == "==3.2.0"


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
