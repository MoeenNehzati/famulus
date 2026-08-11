from __future__ import annotations

import os
import sys
from pathlib import Path

from install_test_utils import python_test_env, run_command


def test_run_command_resolves_commands_from_passed_env_path(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    if os.name == "nt":
        tool = bin_dir / "env-path-tool.bat"
        tool.write_text("@echo off\necho env-path-ok\n", encoding="utf-8")
    else:
        tool = bin_dir / "env-path-tool"
        tool.write_text("#!/bin/sh\necho env-path-ok\n", encoding="utf-8")
        tool.chmod(0o755)

    env = python_test_env(tmp_path, {"PATH": str(bin_dir)})

    result = run_command(["env-path-tool"], env=env)

    assert result.stdout.strip() == "env-path-ok"


def test_python_test_env_provides_an_isolated_persistent_keyring(tmp_path):
    env = python_test_env(tmp_path)

    assert env["PYTHON_KEYRING_BACKEND"] == (
        "famulus_test_keyring.IsolatedFileKeyring"
    )
    assert Path(env["FAMULUS_TEST_KEYRING_PATH"]).is_relative_to(tmp_path)

    run_command(
        [
            sys.executable,
            "-c",
            (
                "import keyring; "
                "keyring.set_password('Famulus:test', 'account', 'secret')"
            ),
        ],
        env=env,
    )
    lookup = run_command(
        [
            sys.executable,
            "-c",
            (
                "import keyring; "
                "print(keyring.get_password('Famulus:test', 'account')); "
                "keyring.delete_password('Famulus:test', 'account')"
            ),
        ],
        env=env,
    )
    cleared = run_command(
        [
            sys.executable,
            "-c",
            "import keyring; print(keyring.get_password('Famulus:test', 'account'))",
        ],
        env=env,
    )

    assert lookup.stdout.strip() == "secret"
    assert cleared.stdout.strip() == "None"
