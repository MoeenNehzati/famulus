from __future__ import annotations

import os

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
