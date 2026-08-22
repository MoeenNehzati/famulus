import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from officina.install.context import (
    load_or_create_development_installation_id,
    resolve_installation_context,
)
from officina.install.development_activation import (
    ActivationError,
    build_interactive_environment,
    install_development_activation,
    verify_managed_commands,
)


def _stable_inputs(tmp_path: Path, platform: str) -> tuple[Path, dict[str, str]]:
    home = tmp_path / "stable-home"
    if platform == "win32":
        return home, {
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "APPDATA": str(home / "AppData" / "Roaming"),
        }
    return home, {}


def _context(tmp_path, *, platform="linux"):
    checkout = tmp_path / "checkout with spaces" / "fåmulus"
    checkout.mkdir(parents=True)
    home, environ = _stable_inputs(tmp_path, platform)
    installation_id = load_or_create_development_installation_id(
        checkout,
        platform=platform,
        home=home,
        environ=environ,
    )
    return resolve_installation_context(
        mode="development",
        source_root=checkout,
        development_root=checkout,
        platform=platform,
        home=home,
        environ=environ,
        installation_id=installation_id,
    )


def _install_activation(context, tmp_path, *, platform="linux", **kwargs):
    home, environ = _stable_inputs(tmp_path, platform)
    return install_development_activation(
        context,
        platform=platform,
        home=home,
        environ=environ,
        **kwargs,
    )


def _executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_interactive_environment_preserves_host_tools_and_session_but_cleans_python(tmp_path):
    context = _context(tmp_path)
    inherited_path = os.pathsep.join(("/host/bin", "/opt/tools"))
    env = build_interactive_environment(
        context,
        environ={
            "PATH": inherited_path,
            "DISPLAY": ":9",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "HTTPS_PROXY": "https://proxy.invalid",
            "AI": "/wrong",
            "FAMULUS_REPO_ROOT": "/wrong",
            "PYTHONPATH": "/wrong",
            "PYTHONHOME": "/wrong",
            "VIRTUAL_ENV": "/wrong",
        },
        platform="linux",
    )
    assert env["PATH"] == str(context.paths.user_bin) + os.pathsep + inherited_path
    assert env["DISPLAY"] == ":9"
    assert env["SSH_AUTH_SOCK"] == "/tmp/agent.sock"
    assert env["HTTPS_PROXY"] == "https://proxy.invalid"
    assert env["HOME"] == str(context.development_root / ".famulus" / "home")
    assert env["CODEX_HOME"] == str(context.codex_home)
    assert env["CLAUDE_CONFIG_DIR"] == str(context.claude_home)
    for name in ("AI", "FAMULUS_REPO_ROOT", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        assert name not in env


def test_managed_command_must_exist_at_exact_development_origin(tmp_path):
    context = _context(tmp_path)
    stable_bin = tmp_path / "stable-bin"
    _executable(stable_bin / "dispatcher")
    with pytest.raises(ActivationError, match="dispatcher"):
        verify_managed_commands(
            context,
            ("dispatcher",),
            environ={"PATH": str(stable_bin)},
        )
    managed = _executable(context.paths.user_bin / "dispatcher")
    assert verify_managed_commands(context, ("dispatcher",), environ={"PATH": str(stable_bin)}) == {
        "dispatcher": managed.resolve()
    }


def test_activation_install_rejects_missing_or_relative_runtime(tmp_path):
    context = _context(tmp_path)
    with pytest.raises(ActivationError):
        _install_activation(
            context,
            tmp_path,
            python_executable=tmp_path / "missing-python",
            managed_commands=(),
        )
    with pytest.raises(ActivationError):
        _install_activation(
            context,
            tmp_path,
            python_executable=Path("python"),
            managed_commands=(),
        )


def test_activation_install_rejects_bin_symlink_escape(tmp_path):
    context = _context(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    activation_bin = context.development_root / ".famulus" / "bin"
    activation_bin.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ActivationError):
        _install_activation(
            context,
            tmp_path,
            python_executable=Path(sys.executable),
            managed_commands=(),
        )


def test_activation_install_rechecks_actual_normal_assistant_home(tmp_path):
    normal_home = tmp_path / "normal-home"
    checkout = normal_home / ".claude" / "checkout"
    checkout.mkdir(parents=True)
    bootstrap_root = tmp_path / "bootstrap"
    bootstrap_home, bootstrap_environ = _stable_inputs(bootstrap_root, "linux")
    installation_id = load_or_create_development_installation_id(
        checkout,
        platform="linux",
        home=bootstrap_home,
        environ=bootstrap_environ,
    )
    context = resolve_installation_context(
        mode="development",
        source_root=checkout,
        development_root=checkout,
        platform="linux",
        home=bootstrap_home,
        environ=bootstrap_environ,
        installation_id=installation_id,
    )
    with pytest.raises(ActivationError, match="stable root"):
        install_development_activation(
            context,
            python_executable=Path(sys.executable),
            managed_commands=(),
            platform="linux",
            home=normal_home,
            environ={},
        )


# famulus-skip: category=platform-contract; reason=the generated shell bootstrap requires POSIX exec and shell evaluation; alternate=the native cmd export and exec tests below cover the same activation contract on Windows
@pytest.mark.skipif(os.name == "nt", reason="POSIX shell activation test")
def test_generated_shell_exec_and_export_are_real_and_unicode_safe(tmp_path):
    context = _context(tmp_path)
    probe = _executable(
        context.paths.user_bin / "probe",
        "#!/bin/sh\nprintf '%s\\n' \"$HOME\" \"$CODEX_HOME\" \"$PATH\" \"${PYTHONPATH-unset}\"\n",
    )
    bootstrap = _install_activation(
        context,
        tmp_path,
        python_executable=Path(sys.executable),
        managed_commands=("probe",),
    )
    inherited = dict(os.environ)
    inherited["PATH"] = "/host tools/bin:/usr/bin:/bin"
    inherited["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    executed = subprocess.run(
        [str(bootstrap), "exec", "--", "probe"],
        env=inherited,
        text=True,
        capture_output=True,
        check=False,
    )
    assert executed.returncode == 0, executed.stderr
    lines = executed.stdout.splitlines()
    assert lines[0] == str(context.development_root / ".famulus" / "home")
    assert lines[1] == str(context.codex_home)
    assert lines[2].startswith(str(context.paths.user_bin) + os.pathsep)
    assert lines[3] == "unset"
    assert "not a security sandbox" in executed.stderr

    exported = subprocess.run(
        [str(bootstrap), "export", "--shell", "sh"],
        env=inherited,
        text=True,
        capture_output=True,
        check=False,
    )
    assert exported.returncode == 0, exported.stderr
    expected_home = context.development_root / ".famulus" / "home"
    assert str(expected_home) in exported.stdout
    evaluated = subprocess.run(
        ["/bin/sh", "-c", exported.stdout + "\nprintf '%s\\n' \"$HOME\" \"${PYTHONPATH-unset}\""],
        env=inherited,
        text=True,
        capture_output=True,
        check=True,
    )
    evaluated_lines = evaluated.stdout.splitlines()
    assert Path(evaluated_lines[0]).parts[-2:] == (".famulus", "home")
    assert evaluated_lines[1] == "unset"
    assert probe.exists()


def test_export_failure_emits_no_partial_stdout(tmp_path):
    context = _context(tmp_path)
    bootstrap = _install_activation(
        context,
        tmp_path,
        python_executable=Path(sys.executable),
        managed_commands=("missing-command",),
    )
    if os.name == "nt":
        bootstrap = bootstrap.with_suffix(".cmd")
    result = subprocess.run(
        [str(bootstrap), "export", "--shell", "sh"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""


# famulus-skip: category=platform-contract; reason=the tracked dev-code adapter uses a POSIX executable and shell argument forwarding; alternate=the native dev-code.cmd adapter test below covers Windows command forwarding
@pytest.mark.skipif(os.name == "nt", reason="POSIX adapter test")
def test_tracked_dev_code_resolves_code_before_activation_and_derives_checkout(tmp_path):
    repository = Path(__file__).parents[1]
    checkout = tmp_path / "clone with spaces" / "fåmulus"
    tools = checkout / "tools"
    tools.mkdir(parents=True)
    shutil.copy2(repository / "tools" / "dev-code", tools / "dev-code")
    activation = checkout / ".famulus" / "bin" / "famulus-env"
    record = tmp_path / "record.txt"
    _executable(
        activation,
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$FAMULUS_TEST_RECORD\"\n",
    )
    host_bin = tmp_path / "host tools"
    code = _executable(host_bin / "code")
    env = dict(os.environ)
    env["PATH"] = str(host_bin) + os.pathsep + env.get("PATH", "")
    env["FAMULUS_TEST_RECORD"] = str(record)
    result = subprocess.run(
        [str(tools / "dev-code")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert record.read_text(encoding="utf-8").splitlines() == [
        "exec",
        "--",
        str(code.resolve()),
        str(checkout.resolve()),
    ]


# famulus-skip: category=platform-contract; reason=the tracked direnv adapter is evaluated by Bash and has no native cmd equivalent; alternate=generated shell and cmd export tests cover complete activation export on their supported shells
@pytest.mark.skipif(os.name == "nt", reason="direnv uses Bash")
def test_tracked_envrc_evaluates_complete_export_from_its_checkout(tmp_path):
    repository = Path(__file__).parents[1]
    checkout = tmp_path / "clone with spaces" / "fåmulus"
    checkout.mkdir(parents=True)
    shutil.copy2(repository / ".envrc", checkout / ".envrc")
    activation = checkout / ".famulus" / "bin" / "famulus-env"
    _executable(
        activation,
        "#!/bin/sh\n"
        "test \"$1 $2 $3\" = \"export --shell bash\" || exit 9\n"
        "printf '%s\\n' \"export FAMULUS_TEST_ACTIVATED='yes with spaces å'\"\n",
    )
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            '. "$1/.envrc"; printf "%s\\n" "$FAMULUS_TEST_ACTIVATED"',
            "sh",
            str(checkout),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "yes with spaces å\n"


# famulus-skip: category=platform-contract; reason=the generated cmd bootstrap requires native cmd.exe expansion semantics; alternate=the POSIX shell export test above covers the same all-or-nothing activation state on non-Windows hosts
@pytest.mark.skipif(os.name != "nt", reason="Windows cmd activation test")
def test_generated_cmd_export_is_real_and_special_character_safe(tmp_path):
    context = _context(tmp_path / "100%! value", platform="win32")
    bootstrap = _install_activation(
        context,
        tmp_path / "100%! value",
        platform="win32",
        python_executable=Path(sys.executable),
        managed_commands=(),
    ).with_suffix(".cmd")
    exported = subprocess.run(
        [str(bootstrap), "export", "--shell", "cmd"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert exported.returncode == 0, exported.stderr
    script = tmp_path / "apply-export.cmd"
    script.write_text(
        "@echo off\r\n"
        + exported.stdout
        + "\r\necho %HOME%\r\n"
        + "if defined PYTHONPATH (echo present) else echo unset\r\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = "must-disappear"
    evaluated = subprocess.run(
        ["cmd.exe", "/d", "/c", str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert evaluated.returncode == 0, evaluated.stderr
    evaluated_lines = evaluated.stdout.splitlines()
    assert Path(evaluated_lines[0]).parts[-2:] == (".famulus", "home")
    assert evaluated_lines[1] == "unset"


# famulus-skip: category=platform-contract; reason=the managed command path uses native cmd.exe call and environment semantics; alternate=the POSIX shell exec test above covers the same managed-command isolation contract on non-Windows hosts
@pytest.mark.skipif(os.name != "nt", reason="Windows cmd activation test")
def test_generated_cmd_exec_runs_managed_command_in_isolated_environment(tmp_path):
    context = _context(tmp_path, platform="win32")
    probe = context.paths.user_bin / "probe.cmd"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "@echo off\r\n"
        "echo %HOME%\r\n"
        "echo %CODEX_HOME%\r\n"
        "echo %PATH%\r\n"
        "if defined PYTHONPATH (echo present) else echo unset\r\n",
        encoding="utf-8",
    )
    bootstrap = _install_activation(
        context,
        tmp_path,
        platform="win32",
        python_executable=Path(sys.executable),
        managed_commands=("probe",),
    ).with_suffix(".cmd")
    inherited = dict(os.environ)
    inherited["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    executed = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/s",
            "/c",
            "call",
            str(bootstrap),
            "exec",
            "--",
            "probe",
        ],
        env=inherited,
        text=True,
        capture_output=True,
        check=False,
    )
    assert executed.returncode == 0, executed.stderr
    lines = executed.stdout.splitlines()
    assert Path(lines[0]).parts[-2:] == (".famulus", "home")
    assert Path(lines[1]).parts[-3:] == (".famulus", "homes", "codex")
    activated_bin = Path(lines[2].split(os.pathsep)[0])
    assert activated_bin.name == "bin"
    assert "Famulus" in activated_bin.parts
    assert lines[3] == "unset"
    assert "not a security sandbox" in executed.stderr


# famulus-skip: category=platform-contract; reason=the tracked dev-code.cmd adapter requires native batch path and argument semantics; alternate=the POSIX dev-code adapter test above covers checkout derivation and managed command forwarding
@pytest.mark.skipif(os.name != "nt", reason="Windows cmd adapter test")
def test_tracked_dev_code_cmd_resolves_code_before_activation_and_derives_checkout(tmp_path):
    repository = Path(__file__).parents[1]
    checkout = tmp_path / "clone with spaces" / "fåmulus"
    tools = checkout / "tools"
    tools.mkdir(parents=True)
    shutil.copy2(repository / "tools" / "dev-code.cmd", tools / "dev-code.cmd")
    activation = checkout / ".famulus" / "bin" / "famulus-env.cmd"
    activation.parent.mkdir(parents=True)
    record = tmp_path / "record.txt"
    activation.write_text(
        "@echo off\r\n"
        ">\"%FAMULUS_TEST_RECORD%\" (\r\n"
        "  echo %~1\r\n"
        "  echo %~2\r\n"
        "  echo %~3\r\n"
        "  echo %~4\r\n"
        ")\r\n",
        encoding="utf-8",
    )
    host_bin = tmp_path / "host tools"
    code = host_bin / "code.cmd"
    code.parent.mkdir(parents=True)
    code.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(host_bin) + os.pathsep + env.get("PATH", "")
    env["FAMULUS_TEST_RECORD"] = str(record)
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(tools / "dev-code.cmd")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert record.read_text(encoding="utf-8").splitlines() == [
        "exec",
        "--",
        str(code.resolve()),
        str(checkout.resolve()),
    ]
