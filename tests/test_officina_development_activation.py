from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from officina.common.famulus_paths import resolve_famulus_paths


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "skills" / "dev-activation" / "_rtx" / "_development_activation.py"


def _load_runtime():
    assert RUNTIME.is_file(), "dev-activation runtime is not implemented"
    spec = importlib.util.spec_from_file_location("task6_development_activation", RUNTIME)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout with spaces" / "fåmulus"
    (checkout / "skills" / "local-canary").mkdir(parents=True)
    (checkout / ".claude-plugin").mkdir()
    (checkout / ".codex-plugin").mkdir()
    (checkout / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "famulus", "mcpServers": {"famulus": {"command": "python", "args": ["${CLAUDE_PLUGIN_ROOT}/mcp_server.py"]}}}),
        encoding="utf-8",
    )
    (checkout / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "famulus", "skills": "./skills/", "mcpServers": "./.mcp.json"}),
        encoding="utf-8",
    )
    (checkout / ".mcp.json").write_text(
        json.dumps({"famulus": {"command": "python", "args": ["mcp_server.py"], "cwd": "."}}),
        encoding="utf-8",
    )
    (checkout / "mcp_server.py").write_text("# local MCP canary\n", encoding="utf-8")
    runtime = checkout / "skills" / "dev-activation" / "_rtx" / "_development_activation.py"
    runtime.parent.mkdir(parents=True)
    shutil.copy2(RUNTIME, runtime)
    return checkout


def _real_environment(tmp_path: Path) -> tuple[dict[str, str], dict[str, Path]]:
    roots = {
        "home": tmp_path / "real-home",
        "userprofile": tmp_path / "real-userprofile",
        "appdata": tmp_path / "real-appdata",
        "localappdata": tmp_path / "real-localappdata",
    }
    for root in roots.values():
        root.mkdir(parents=True)
    (roots["home"] / ".agents" / "skills" / "home-global-canary").mkdir(parents=True)
    (roots["userprofile"] / ".agents" / "skills" / "userprofile-global-canary").mkdir(parents=True)
    (roots["appdata"] / "state-global-canary").mkdir()
    (roots["localappdata"] / "state-global-canary").mkdir()
    env = {
        "HOME": str(roots["home"]),
        "USERPROFILE": str(roots["userprofile"]),
        "APPDATA": str(roots["appdata"]),
        "LOCALAPPDATA": str(roots["localappdata"]),
        "CODEX_HOME": str(roots["home"] / ".codex"),
        "CLAUDE_CONFIG_DIR": str(roots["home"] / ".claude"),
        "PATH": os.environ.get("PATH", ""),
        "DISPLAY": ":17",
        "WAYLAND_DISPLAY": "wayland-17",
        "SSH_AUTH_SOCK": "/tmp/fake-agent.sock",
        "LANG": "en_CA.UTF-8",
        "HTTPS_PROXY": "http://proxy.invalid:8080",
        "AI": "/leak/ai",
        "FAMULUS_REPO_ROOT": "/leak/repo",
        "PYTHONPATH": "/leak/python",
        "PYTHONHOME": "/leak/python-home",
        "VIRTUAL_ENV": "/leak/venv",
        "CONDA_PREFIX": "/leak/conda",
        "ASSISTANT_LOGS": "/leak/logs",
    }
    return env, roots


def test_posix_environment_isolates_discovery_and_preserves_operations(tmp_path: Path) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)
    inherited, _ = _real_environment(tmp_path)
    activated = runtime.build_activation_environment(checkout, environ=inherited, platform="linux")
    isolated = checkout / ".famulus" / "home"
    assert activated["HOME"] == str(isolated)
    assert activated["CODEX_HOME"] == str(isolated / ".codex")
    assert activated["CLAUDE_CONFIG_DIR"] == str(isolated / ".claude")
    assert activated["XDG_CONFIG_HOME"] == str(isolated / ".config")
    assert activated["XDG_DATA_HOME"] == str(isolated / ".local" / "share")
    assert activated["XDG_STATE_HOME"] == str(isolated / ".local" / "state")
    for name in ("PATH", "DISPLAY", "WAYLAND_DISPLAY", "SSH_AUTH_SOCK", "LANG", "HTTPS_PROXY"):
        assert activated[name] == inherited[name]
    for name in ("AI", "FAMULUS_REPO_ROOT", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "ASSISTANT_LOGS"):
        assert name not in activated


def test_windows_environment_replaces_every_native_home_selector(tmp_path: Path) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)
    inherited, roots = _real_environment(tmp_path)
    activated = runtime.build_activation_environment(checkout, environ=inherited, platform="win32")
    isolated = checkout / ".famulus" / "home"
    assert activated["HOME"] == str(isolated)
    assert activated["USERPROFILE"] == str(isolated)
    assert activated["APPDATA"] == str(isolated / "AppData" / "Roaming")
    assert activated["LOCALAPPDATA"] == str(isolated / "AppData" / "Local")
    assert activated["CODEX_HOME"] == str(isolated / ".codex")
    assert activated["CLAUDE_CONFIG_DIR"] == str(isolated / ".claude")
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        assert activated[name] not in {str(path) for path in roots.values()}
    assert not any(name.startswith("XDG_") for name in activated)


def test_unknown_platform_is_rejected_before_environment_or_state_changes(tmp_path: Path) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)

    with pytest.raises(runtime.ActivationError, match="unsupported platform"):
        runtime.create_activation(checkout, environ={}, platform="plan9")

    assert not (checkout / ".famulus").exists()


def test_macos_activation_and_durable_paths_stay_below_isolated_home(tmp_path: Path) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)
    inherited, _ = _real_environment(tmp_path)
    activated = runtime.build_activation_environment(checkout, environ=inherited, platform="darwin")
    isolated = checkout / ".famulus" / "home"
    paths = resolve_famulus_paths(platform="darwin", home=isolated, environ=activated)
    assert activated["HOME"] == str(isolated)
    assert not any(name.startswith("XDG_") for name in activated)
    for value in vars(paths).values():
        assert Path(value).is_relative_to(isolated)
    assert "Library/Application Support" in str(paths.data_root)


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_create_validate_and_reentry_are_idempotent(tmp_path: Path, platform: str) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)
    inherited, _ = _real_environment(tmp_path)
    first = runtime.create_activation(checkout, environ=inherited, platform=platform)
    before = sorted(path.relative_to(checkout).as_posix() for path in (checkout / ".famulus").rglob("*"))
    second = runtime.create_activation(checkout, environ=inherited, platform=platform)
    after = sorted(path.relative_to(checkout).as_posix() for path in (checkout / ".famulus").rglob("*"))
    assert first == second
    assert before == after
    runtime.validate_activation(checkout, environ=inherited, platform=platform)
    assert not any(path.name == "current.json" or path.name == "releases" for path in (checkout / ".famulus").rglob("*"))


def test_validation_failure_publishes_no_partial_activation(tmp_path: Path) -> None:
    runtime = _load_runtime()
    checkout = tmp_path / "not-a-famulus-checkout"
    checkout.mkdir()
    with pytest.raises(runtime.ActivationError, match="plugin metadata"):
        runtime.create_activation(checkout, environ={}, platform="linux")
    assert not (checkout / ".famulus").exists()


def test_validation_rejects_missing_portable_entry(tmp_path: Path) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)
    (checkout / "skills" / "dev-activation" / "_rtx" / "_development_activation.py").unlink()

    with pytest.raises(runtime.ActivationError, match="plugin metadata"):
        runtime.create_activation(checkout, environ={}, platform="linux")

    assert not (checkout / ".famulus").exists()


def test_existing_home_symlink_cannot_escape_checkout(tmp_path: Path) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (checkout / ".famulus").mkdir()
    (checkout / ".famulus" / "home").symlink_to(outside, target_is_directory=True)

    with pytest.raises(runtime.ActivationError, match="outside the checkout"):
        runtime.create_activation(checkout, environ={}, platform="linux")

    assert list(outside.iterdir()) == []


def test_dangling_famulus_symlink_cannot_create_outside_checkout(tmp_path: Path) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)
    outside = tmp_path / "outside"
    (checkout / ".famulus").symlink_to(outside, target_is_directory=True)

    with pytest.raises(runtime.ActivationError, match="outside the checkout"):
        runtime.create_activation(checkout, environ={}, platform="linux")

    assert not outside.exists()


@pytest.mark.parametrize(
    ("platform", "intermediate"),
    [("linux", ".local"), ("win32", "AppData")],
)
def test_intermediate_activation_symlink_cannot_escape_checkout(
    tmp_path: Path, platform: str, intermediate: str
) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    home = checkout / ".famulus" / "home"
    home.mkdir(parents=True)
    (home / intermediate).symlink_to(outside, target_is_directory=True)

    with pytest.raises(runtime.ActivationError, match="outside the checkout"):
        runtime.create_activation(checkout, environ={}, platform=platform)

    assert list(outside.iterdir()) == []


def test_host_routes_select_packaged_checkout_without_root_mcp_for_claude(tmp_path: Path) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)
    assert runtime.host_command(checkout, "claude", ["--version"]) == ["claude", "--plugin-dir", str(checkout.resolve()), "--version"]
    assert runtime.host_command(checkout, "codex", ["--version"]) == ["codex", "-C", str(checkout.resolve()), "--version"]
    assert ".mcp.json" not in runtime.host_command(checkout, "claude", [])


def test_codex_server_map_is_rejected_as_claude_project_config(tmp_path: Path) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)
    claude = shutil.which("claude")
    assert claude is not None, "frozen Task 6 capability requires the Claude parser"
    isolated = tmp_path / "claude-home"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(isolated),
        "CLAUDE_CONFIG_DIR": str(isolated / ".claude"),
        "XDG_CONFIG_HOME": str(isolated / ".config"),
    }
    result = subprocess.run(
        [claude, "--mcp-config", str(checkout / ".mcp.json"), "--strict-mcp-config", "mcp", "list"],
        cwd=checkout, env=env, text=True, encoding="utf-8", errors="strict",
        capture_output=True, check=False,
    )
    diagnostics = result.stdout + result.stderr
    assert "[Failed to parse]" in diagnostics
    assert "mcpServers: Invalid input" in diagnostics
    assert runtime.host_command(checkout, "claude", []) == [
        "claude", "--plugin-dir", str(checkout.resolve()),
    ]


def test_packaged_declarations_share_literal_python_and_one_mcp(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    claude = json.loads((checkout / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["mcpServers"]
    codex_plugin = json.loads((checkout / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    codex = json.loads((checkout / codex_plugin["mcpServers"]).read_text(encoding="utf-8"))
    assert list(claude) == ["famulus"]
    assert list(codex) == ["famulus"]
    assert claude["famulus"] == {"command": "python", "args": ["${CLAUDE_PLUGIN_ROOT}/mcp_server.py"]}
    assert codex["famulus"] == {"command": "python", "args": ["mcp_server.py"], "cwd": "."}


def test_durable_linux_and_windows_state_is_below_isolated_home(tmp_path: Path) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)
    inherited, roots = _real_environment(tmp_path)
    for platform in ("linux", "win32"):
        activated = runtime.build_activation_environment(checkout, environ=inherited, platform=platform)
        isolated = checkout / ".famulus" / "home"
        paths = resolve_famulus_paths(platform=platform, home=isolated, environ=activated)
        for value in vars(paths).values():
            path = Path(value)
            assert path.is_relative_to(isolated)
            assert not any(path.is_relative_to(root) for root in roots.values())


def test_direct_portable_exec_uses_isolated_environment(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    inherited, _ = _real_environment(tmp_path)
    inherited.pop("PYTHONHOME")
    inherited.pop("PYTHONPATH")
    probe = "import json,os; print(json.dumps({k: os.environ.get(k) for k in ['HOME','CODEX_HOME','CLAUDE_CONFIG_DIR','PATH','PYTHONPATH']}))"
    result = subprocess.run(
        ["python", str(RUNTIME), "exec", "--checkout", str(checkout), "--platform", "linux", "--", "python", "-c", probe],
        env=inherited,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    isolated = checkout / ".famulus" / "home"
    assert payload == {"HOME": str(isolated), "CODEX_HOME": str(isolated / ".codex"), "CLAUDE_CONFIG_DIR": str(isolated / ".claude"), "PATH": inherited["PATH"], "PYTHONPATH": None}


def test_direct_portable_report_emits_json(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    result = subprocess.run(
        ["python", str(RUNTIME), "report", "--checkout", str(checkout), "--platform", "linux"],
        text=True, encoding="utf-8", errors="strict", capture_output=True, check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["checkout"] == str(checkout.resolve())
    assert payload["codex"][-2:] == ["--host", "codex"]


def test_git_identity_uses_only_narrow_global_config_bridge(tmp_path: Path) -> None:
    runtime = _load_runtime()
    checkout = _checkout(tmp_path)
    inherited, roots = _real_environment(tmp_path)
    gitconfig = roots["home"] / ".gitconfig"
    gitconfig.write_text("[user]\n\tname = Dev User\n\temail = dev@example.invalid\n", encoding="utf-8")
    activated = runtime.build_activation_environment(checkout, environ=inherited, platform="linux")
    assert activated["GIT_CONFIG_GLOBAL"] == str(gitconfig)
    # famulus-raw-git: category=ambient-config; reason=the subject is Git real global-config selection under activation
    result = subprocess.run(
        ["git", "config", "--global", "user.email"], cwd=checkout, env=activated,
        text=True, encoding="utf-8", errors="strict", capture_output=True, check=True,
    )
    assert result.stdout.strip() == "dev@example.invalid"
    assert not (checkout / ".famulus" / "home" / ".gitconfig").exists()


def test_feature_blueprints_own_one_portable_runtime_surface() -> None:
    module = yaml.safe_load((ROOT / "skills" / "dev-activation" / "blueprint.yaml").read_text())
    runtime = yaml.safe_load((ROOT / "skills" / "dev-activation" / "_rtx" / "blueprint.yaml").read_text())
    source = yaml.safe_load((ROOT / "skills" / "dev-activation" / "_rtx" / "blueprints" / "rtx-development-activation.yaml").read_text())
    assert module["id"] == "dev-activation"
    assert module["children"] == {"_rtx": {}}
    assert set(runtime["sources"]) == {"dev-activation._rtx.source.rtx-development-activation"}
    assert source["gateway"] == {"language": "Python", "path": "__init__.py"}
    assert "_development_activation\\.py" in source["content"]
    assert source["platform_support"] == {"linux": True, "macos": True, "windows": True}


def test_old_install_owned_activation_surface_is_absent() -> None:
    assert not (ROOT / "src" / "officina" / "install" / "development_activation.py").exists()
    assert not (ROOT / "src" / "officina" / "install" / "blueprints" / "development-activation.yaml").exists()
    install_init = (ROOT / "src" / "officina" / "install" / "__init__.py").read_text()
    install_blueprint = yaml.safe_load((ROOT / "src" / "officina" / "install" / "blueprint.yaml").read_text())
    assert "development_activation" not in install_init
    assert "development-activation" not in install_blueprint["sources"]


# famulus-skip: category=platform-contract; reason=direnv and Bash sourcing are POSIX-only; alternate=test_cmd_wrapper_declares_controlled_ordered_argv covers the Windows outer boundary over the same shared runtime
@pytest.mark.skipif(os.name == "nt", reason="direnv convenience is POSIX-only")
def test_envrc_delegates_to_shared_python_activation(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    (checkout / "skills" / "dev-activation" / "_rtx").mkdir(parents=True, exist_ok=True)
    (checkout / "skills" / "dev-activation" / "_rtx" / "_development_activation.py").write_text("# delegated target\n", encoding="utf-8")
    (checkout / ".envrc").write_text((ROOT / ".envrc").read_text(), encoding="utf-8")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    record = tmp_path / "python-argv.txt"
    fake_python = fake_bin / "python"
    fake_python.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$FAMULUS_TEST_RECORD\"\nprintf '%s\\n' 'export FAMULUS_ENVRC_PROBE=ok'\n", encoding="utf-8")
    fake_python.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    env["FAMULUS_TEST_RECORD"] = str(record)
    result = subprocess.run(
        ["bash", "-c", "source .envrc && printf '%s' \"$FAMULUS_ENVRC_PROBE\""], cwd=checkout, env=env,
        text=True, encoding="utf-8", errors="strict", capture_output=True, check=True,
    )
    assert result.stdout == "ok"
    assert record.read_text(encoding="utf-8").splitlines() == [
        str(checkout / "skills" / "dev-activation" / "_rtx" / "_development_activation.py"),
        "export", "--checkout", str(checkout), "--shell", "bash",
    ]


# famulus-skip: category=platform-contract; reason=the tracked POSIX wrapper requires an executable POSIX shell; alternate=test_cmd_wrapper_declares_controlled_ordered_argv covers exact Windows wrapper ordering
@pytest.mark.skipif(os.name == "nt", reason="POSIX wrapper is exercised on POSIX")
def test_posix_wrapper_preserves_ordered_argv(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    wrapper = checkout / "tools" / "dev-code"
    wrapper.parent.mkdir()
    shutil.copy2(ROOT / "tools" / "dev-code", wrapper)
    fake_bin = tmp_path / "fake bin"
    fake_bin.mkdir()
    record = tmp_path / "argv.txt"
    fake_python = fake_bin / "python"
    fake_python.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$FAMULUS_TEST_RECORD\"\n", encoding="utf-8")
    fake_python.chmod(0o755)
    fake_code = fake_bin / "code"
    fake_code.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_code.chmod(0o755)
    env = dict(
        os.environ,
        PATH=str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        FAMULUS_TEST_RECORD=str(record),
    )
    subprocess.run([wrapper, "two words", "β"], env=env, check=True)
    assert record.read_text(encoding="utf-8").splitlines() == [
        str(checkout / "skills" / "dev-activation" / "_rtx" / "_development_activation.py"),
        "exec", "--checkout", str(checkout), "--", str(fake_code), str(checkout),
        "two words", "β",
    ]


def test_cmd_wrapper_declares_controlled_ordered_argv() -> None:
    invocation = next(
        line for line in (ROOT / "tools" / "dev-code.cmd").read_text(encoding="utf-8").splitlines()
        if line.startswith("python ")
    )
    assert invocation == (
        'python "%FAMULUS_CHECKOUT%\\skills\\dev-activation\\_rtx\\_development_activation.py" '
        'exec --checkout "%FAMULUS_CHECKOUT%" -- "%CODE_EXE%" "%FAMULUS_CHECKOUT%" %*'
    )
    checkout = "C:\\work trees\\fåmulus"
    code = "C:\\Program Files\\Code\\code.cmd"
    expected = [
        "python", checkout + "\\skills\\dev-activation\\_rtx\\_development_activation.py",
        "exec", "--checkout", checkout, "--", code, checkout, "two words", "β",
    ]
    expanded = invocation.replace("%FAMULUS_CHECKOUT%", checkout).replace(
        "%CODE_EXE%", code
    ).replace('%*', '"two words" β')
    assert expanded == subprocess.list2cmdline(expected)
