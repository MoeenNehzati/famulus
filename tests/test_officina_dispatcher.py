"""Tests for shared dispatcher runtime behavior."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.dispatcher import dispatch, resolve_dispatch


def _write_skill(repo_root: Path) -> None:
    skill_root = repo_root / "skills" / "unicode-skill"
    runtime_root = skill_root / "_rtx"
    runtime_root.mkdir(parents=True)
    (runtime_root / "__init__.py").write_text("", encoding="utf-8")
    (runtime_root / "_echo_text.py").write_text(
        "import sys\n"
        "text = sys.stdin.read()\n"
        "print(sys.stdout.encoding)\n"
        "print(text, end='')\n",
        encoding="utf-8",
    )
    (skill_root / "blueprint.yaml").write_text(
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "interfaces:\n"
        "  machine:\n"
        "    echo-text:\n"
        "      runtime:\n"
        "        kind: python_module\n"
        "        module: _rtx._echo_text\n"
        "      dependencies: []\n"
        "      patterns:\n"
        "        - name: stdin\n"
        "          allow_stdin: true\n",
        encoding="utf-8",
    )


def test_python_module_runtime_gets_utf8_stdio_env(tmp_path: Path) -> None:
    _write_skill(tmp_path)

    resolved = resolve_dispatch(
        caller_skill="unicode-skill",
        target="unicode-skill.machine.echo-text",
        stdin_requested=True,
        repo_root=tmp_path,
    )

    assert resolved.env is not None
    assert resolved.env["PYTHONIOENCODING"] == "utf-8:strict"


def test_python_machine_interface_runtime_uses_shared_runner(tmp_path: Path) -> None:
    skill_root = tmp_path / "skills" / "demo-skill"
    runtime_root = skill_root / "_rtx"
    runtime_root.mkdir(parents=True)
    (skill_root / "blueprint.yaml").write_text(
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "interfaces:\n"
        "  machine:\n"
        "    ping:\n"
        "      runtime:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_ping.py:Interface\n"
        "        args_prefix: [ping]\n"
        "      dependencies: []\n"
        "      patterns:\n"
        "        - name: any\n"
        "          allow_extra_positionals: true\n",
        encoding="utf-8",
    )

    resolved = resolve_dispatch(
        caller_skill="demo-skill",
        target="demo-skill.machine.ping",
        args=["--route-smoke"],
        repo_root=tmp_path,
    )

    assert resolved.cwd == skill_root
    assert resolved.env is not None
    assert resolved.env["PYTHONIOENCODING"] == "utf-8:strict"
    assert resolved.command[:4] == [
        sys.executable,
        "-m",
        "officina.runtime.python_machine_interface_runner",
        "_rtx/_ping.py:Interface",
    ]
    assert resolved.command[4:] == ["ping", "--route-smoke"]


def test_dispatch_text_mode_pins_utf8_strict(monkeypatch, tmp_path: Path) -> None:
    _write_skill(tmp_path)
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    dispatch(
        caller_skill="unicode-skill",
        target="unicode-skill.machine.echo-text",
        stdin="Résumé π\n",
        repo_root=tmp_path,
    )

    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "strict"


def test_dispatch_round_trips_non_ascii_text(tmp_path: Path) -> None:
    _write_skill(tmp_path)

    completed = dispatch(
        caller_skill="unicode-skill",
        target="unicode-skill.machine.echo-text",
        stdin="Résumé π 東京\n",
        text=True,
        repo_root=tmp_path,
    )

    assert completed.returncode == 0
    stdout_encoding, echoed = completed.stdout.split("\n", 1)
    assert stdout_encoding.lower().replace("-", "") == "utf8"
    assert echoed == "Résumé π 東京\n"
