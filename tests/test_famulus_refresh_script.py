from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "famulus-refresh"


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


def test_default_dry_run_refreshes_both_from_local_and_preserves_plugin_data() -> None:
    result = run_refresh("--dry-run", env={"AI": str(ROOT)})

    assert result.returncode == 0, result.stderr
    assert "codex plugin remove famulus@nullkit --json" in result.stdout
    assert f"codex plugin marketplace add {ROOT} --json" in result.stdout
    assert "codex plugin add famulus@nullkit --json" in result.stdout
    assert "claude plugin uninstall famulus@nullkit --keep-data" in result.stdout
    assert f"claude plugin marketplace add {ROOT} --scope user" in result.stdout
    assert "claude plugin install famulus@nullkit --scope user -y" in result.stdout
    assert "rm -rf" not in result.stdout


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
