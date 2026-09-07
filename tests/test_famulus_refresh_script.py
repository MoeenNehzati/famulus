from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "famulus-refresh"


def _dry_run_commands(stdout: str) -> list[list[str]]:
    return [
        shlex.split(line[2:])
        for line in stdout.splitlines()
        if line.startswith("+ ")
    ]


def run_refresh(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=process_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_default_dry_run_refreshes_both_from_local_and_preserves_plugin_data(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / r"Famulus Checkout\windows-path"
    checkout.mkdir(parents=True)
    result = run_refresh("--dry-run", env={"AI": str(checkout)})

    assert result.returncode == 0, result.stderr
    commands = _dry_run_commands(result.stdout)
    assert ["codex", "plugin", "remove", "famulus@nullkit", "--json"] in commands
    assert [
        "codex",
        "plugin",
        "marketplace",
        "add",
        str(checkout),
        "--json",
    ] in commands
    assert ["codex", "plugin", "add", "famulus@nullkit", "--json"] in commands
    assert [
        "claude",
        "plugin",
        "uninstall",
        "famulus@nullkit",
        "--keep-data",
    ] in commands
    assert [
        "claude",
        "plugin",
        "marketplace",
        "add",
        str(checkout),
        "--scope",
        "user",
    ] in commands
    assert [
        "claude",
        "plugin",
        "install",
        "famulus@nullkit",
        "--scope",
        "user",
        "-y",
    ] in commands
    assert not any(command[:2] == ["rm", "-rf"] for command in commands)


def test_reset_refuses_a_codex_data_path_outside_the_agent_plugin_root(tmp_path: Path) -> None:
    result = run_refresh(
        "--codex",
        "--reset-plugin-data",
        "--dry-run",
        env={
            "AI": str(ROOT),
            "CODEX_HOME": str(tmp_path / "codex"),
            "FAMULUS_CODEX_PLUGIN_DATA": str(tmp_path / "wrong-place"),
        },
    )

    assert result.returncode != 0
    assert "refusing unsafe Codex plugin-data path" in result.stderr
