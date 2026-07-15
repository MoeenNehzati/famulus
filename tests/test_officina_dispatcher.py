"""Tests for shared dispatcher runtime behavior."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.dispatcher import dispatch, resolve_dispatch
from officina.dispatcher.core import InvocationError, build_machine_runtime


REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_schema_bundle(repo_root: Path) -> None:
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint",
        repo_root / "references" / "blueprint",
    )


def _write_typed_command_skill(repo_root: Path) -> tuple[Path, Path, Path]:
    _copy_schema_bundle(repo_root)
    skill = repo_root / "skills" / "demo-skill"
    commands = skill / "_cx"
    commands.mkdir(parents=True)
    command = commands / "run-task"
    command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    command.chmod(0o755)
    (skill / "SKILL.md").write_text("Body.\n", encoding="utf-8")
    (skill / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "blueprint_type": "skill",
                "id": "demo-skill",
                "category": "development-assistant",
                "role": "automation",
                "kind": "tool",
                "interfaces": [
                    {
                        "interface": "demo-skill.llm.default",
                        "version": 1,
                        "blueprint": {
                            "base": "skill-root",
                            "path": ".SKILL.md.blueprint.yaml",
                        },
                    },
                    {
                        "interface": "demo-skill.machine.run",
                        "version": 1,
                        "blueprint": {
                            "base": "skill-root",
                            "path": "_cx/.run-task.blueprint.yaml",
                        },
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (skill / ".SKILL.md.blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "blueprint_type": "llm-interface",
                "id": "demo-skill.llm.default",
                "version": 1,
                "description": "Primary instructions.",
                "binding": {"kind": "instruction-file", "path": "SKILL.md"},
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    sidecar = commands / ".run-task.blueprint.yaml"
    sidecar.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "blueprint_type": "machine-interface",
                "id": "demo-skill.machine.run",
                "version": 1,
                "description": "Run task.",
                "usage": "run",
                "binding": {
                    "kind": "command-file",
                    "path": "_cx/run-task",
                    "args_prefix": [],
                },
                "allow_all_skills": True,
                "allowed_callers": [],
                "platform_support": {
                    "linux": True,
                    "macos": True,
                    "windows": True,
                },
                "dependencies": [],
                "uses_interfaces": [],
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return skill, sidecar, command


def _write_v3_command_skill(repo_root: Path) -> tuple[Path, Path, Path]:
    skill, sidecar, command = _write_typed_command_skill(repo_root)
    (skill / ".SKILL.md.blueprint.yaml").unlink()
    (skill / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 3,
                "node_type": "skill",
                "id": "demo-skill",
                "category": "development-assistant",
                "role": "automation",
                "kind": "tool",
                "gateway": {"kind": "instruction-file", "path": "SKILL.md"},
                "content": [r"SKILL\.md"],
                "default_interface": {
                    "version": 1,
                    "description": "Primary instructions.",
                    "allow_all_skills": True,
                    "uses_interfaces": [],
                    "behavior_sources": [],
                    "direct_io": {"reads": [], "writes": [], "network": []},
                    "owns_filesystem": [],
                },
                "interfaces": [
                    {
                        "interface": "demo-skill.machine.run",
                        "version": 1,
                        "blueprint": {
                            "base": "skill-root",
                            "path": "_cx/.run-task.blueprint.yaml",
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    declaration["schema_version"] = 3
    declaration["node_type"] = declaration.pop("blueprint_type")
    declaration["gateway"] = declaration.pop("binding")
    declaration["content"] = [r"_cx/run-task"]
    sidecar.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")
    return skill, sidecar, command


def _write_v3_python_skill(repo_root: Path) -> tuple[Path, Path, Path]:
    skill, command_sidecar, _command = _write_v3_command_skill(repo_root)
    runtime = skill / "_rtx"
    runtime.mkdir()
    gateway = runtime / "_run.py"
    gateway.write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    pass\n",
        encoding="utf-8",
    )
    declaration = yaml.safe_load(command_sidecar.read_text(encoding="utf-8"))
    command_sidecar.unlink()
    declaration["gateway"] = {
        "kind": "python-entrypoint",
        "path": "_rtx/_run.py",
        "symbol": "Interface",
        "args_prefix": [],
    }
    declaration["content"] = [r"_rtx/_run\.py"]
    sidecar = runtime / "._run.py.blueprint.yaml"
    sidecar.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")
    root_path = skill / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    root["interfaces"][0]["blueprint"]["path"] = "_rtx/._run.py.blueprint.yaml"
    root_path.write_text(yaml.safe_dump(root, sort_keys=False), encoding="utf-8")
    return skill, sidecar, gateway


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
        "interfaces:\n"
        "  machine:\n"
        "    echo-text:\n"
        "      version: 1\n"
        "      invocation:\n"
        "        kind: python_module\n"
        "        module: _rtx._echo_text\n"
        "      dependencies: []\n"
        "      patterns:\n"
        "        - name: stdin\n"
        "          allow_stdin: true\n",
        encoding="utf-8",
    )


def _write_portable_legacy_skill(repo_root: Path) -> Path:
    _copy_schema_bundle(repo_root)
    skill_root = repo_root / "skills" / "portable-skill"
    runtime_root = skill_root / "_rtx"
    runtime_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("Portable skill.\n", encoding="utf-8")
    (runtime_root / "_run.py").write_text("class Interface: pass\n", encoding="utf-8")
    direct_io = {"reads": [], "writes": [], "network": []}

    def machine_interface(windows: bool) -> dict[str, Any]:
        return {
            "version": 1,
            "invocation": {
                "kind": "python_machine_interface",
                "entrypoint": "_rtx/_run.py:Interface",
                "behavior_sources": [],
            },
            "platform_support": {
                "linux": True,
                "macos": True,
                "windows": windows,
            },
            "dependencies": [],
            "direct_io": direct_io,
            "owns_filesystem": [],
        }

    (skill_root / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "category": "workflow-general-assistant",
                "role": "automation",
                "kind": "tool",
                "interfaces": {
                    "machine": {
                        "supported": machine_interface(True),
                        "unsupported": machine_interface(False),
                    },
                    "llm": {
                        "default": {
                            "version": 1,
                            "description": "Primary instructions.",
                            "binding": {"kind": "skill_file", "path": "SKILL.md"},
                            "behavior_sources": [],
                            "direct_io": direct_io,
                            "owns_filesystem": [],
                        }
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return skill_root


def _simulate_windows_without_descriptor_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "officina.common.blueprint_graph._descriptor_safe_open_supported",
        lambda: False,
    )


def test_windows_legacy_dispatch_uses_one_portable_root_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_root = _write_portable_legacy_skill(tmp_path)
    _simulate_windows_without_descriptor_access(monkeypatch)
    real_read_bytes = Path.read_bytes
    reads: list[Path] = []

    def tracked_read_bytes(path: Path) -> bytes:
        reads.append(path)
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)

    resolved = resolve_dispatch(
        caller_skill="portable-skill",
        target="portable-skill.machine.supported",
        repo_root=tmp_path,
    )

    assert reads.count(skill_root / "blueprint.yaml") == 1
    assert resolved.runtime_bindings == ()
    assert resolved.command[-1] == "_rtx/_run.py:Interface"


def test_windows_legacy_dispatch_rejects_platform_unsupported_interface_normally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_portable_legacy_skill(tmp_path)
    _simulate_windows_without_descriptor_access(monkeypatch)

    with pytest.raises(InvocationError, match="does not support platform `windows`"):
        resolve_dispatch(
            caller_skill="portable-skill",
            target="portable-skill.machine.unsupported",
            repo_root=tmp_path,
        )


@pytest.mark.parametrize(
    "invalid_shape",
    [
        "missing-platform-support",
        "non-mapping-platform-support",
        "missing-windows-key",
        "non-boolean-windows",
        "invalid-invocation-entrypoint",
    ],
)
def test_windows_legacy_dispatch_rejects_schema_invalid_snapshot_before_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_shape: str,
) -> None:
    skill_root = _write_portable_legacy_skill(tmp_path)
    blueprint_path = skill_root / "blueprint.yaml"
    declaration = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    interface = declaration["interfaces"]["machine"]["supported"]
    if invalid_shape == "missing-platform-support":
        del interface["platform_support"]
    elif invalid_shape == "non-mapping-platform-support":
        interface["platform_support"] = "windows"
    elif invalid_shape == "missing-windows-key":
        del interface["platform_support"]["windows"]
    elif invalid_shape == "non-boolean-windows":
        interface["platform_support"]["windows"] = "yes"
    else:
        interface["invocation"]["entrypoint"] = 42
    blueprint_path.write_text(
        yaml.safe_dump(declaration, sort_keys=False),
        encoding="utf-8",
    )
    _simulate_windows_without_descriptor_access(monkeypatch)
    real_read_bytes = Path.read_bytes
    reads: list[Path] = []

    def tracked_read_bytes(path: Path) -> bytes:
        reads.append(path)
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)
    monkeypatch.setattr(
        "officina.dispatcher.core.build_machine_runtime",
        lambda *_args, **_kwargs: pytest.fail("runtime construction must not run"),
    )

    with pytest.raises(
        InvocationError,
        match="legacy blueprint schema validation failed",
    ) as captured:
        resolve_dispatch(
            caller_skill="portable-skill",
            target="portable-skill.machine.supported",
            repo_root=tmp_path,
        )

    assert str(blueprint_path) in str(captured.value)
    assert reads.count(blueprint_path) == 1


def test_windows_typed_dispatch_fails_closed_without_descriptor_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_typed_command_skill(tmp_path)
    _simulate_windows_without_descriptor_access(monkeypatch)

    with pytest.raises(InvocationError, match="descriptor-safe no-follow file access is unavailable"):
        resolve_dispatch(
            caller_skill="demo-skill",
            target="demo-skill.machine.run",
            repo_root=tmp_path,
        )


def test_non_windows_legacy_dispatch_fails_closed_without_descriptor_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_portable_legacy_skill(tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "officina.common.blueprint_graph._descriptor_safe_open_supported",
        lambda: False,
    )

    with pytest.raises(InvocationError, match="descriptor-safe no-follow file access is unavailable"):
        resolve_dispatch(
            caller_skill="portable-skill",
            target="portable-skill.machine.supported",
            repo_root=tmp_path,
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
    (runtime_root / "_ping.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    def run(self, args): return 0\n",
        encoding="utf-8",
    )
    (skill_root / "blueprint.yaml").write_text(
        "category: workflow-general-assistant\n"
        "interfaces:\n"
        "  machine:\n"
        "    ping:\n"
        "      version: 1\n"
        "      invocation:\n"
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
        "--source-fd",
    ]
    assert resolved.command[5:8] == [
        "--package-file",
        resolved.command[4],
        "_rtx/_ping.py",
    ]
    assert resolved.command[8:] == [
        "_rtx/_ping.py:Interface",
        "ping",
        "--route-smoke",
    ]
    assert resolved.pass_fds == (int(resolved.command[4]),)
    assert resolved.metadata().command == [
        sys.executable,
        "-m",
        "officina.runtime.python_machine_interface_runner",
        "_rtx/_ping.py:Interface",
        "ping",
        "--route-smoke",
    ]
    resolved.close()


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


def test_typed_machine_interface_resolves_through_file_sidecar(tmp_path: Path) -> None:
    _copy_schema_bundle(tmp_path)
    skill = tmp_path / "skills" / "demo-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Body.\n", encoding="utf-8")
    (runtime / "_ping.py").write_text("class Interface: pass\n", encoding="utf-8")
    (skill / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "blueprint_type": "skill",
                "id": "demo-skill",
                "category": "development-assistant",
                "role": "automation",
                "kind": "tool",
                "interfaces": [
                    {
                        "interface": "demo-skill.llm.default",
                        "version": 1,
                        "blueprint": {
                            "base": "skill-root",
                            "path": ".SKILL.md.blueprint.yaml",
                        },
                    },
                    {
                        "interface": "demo-skill.machine.ping",
                        "version": 1,
                        "blueprint": {
                            "base": "skill-root",
                            "path": "_rtx/._ping.py.blueprint.yaml",
                        },
                    }
                ],
            },
            sort_keys=False,
        )
    )
    (skill / ".SKILL.md.blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "blueprint_type": "llm-interface",
                "id": "demo-skill.llm.default",
                "version": 1,
                "description": "Primary instructions.",
                "binding": {"kind": "instruction-file", "path": "SKILL.md"},
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (runtime / "._ping.py.blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "blueprint_type": "machine-interface",
                "id": "demo-skill.machine.ping",
                "version": 1,
                "description": "Ping.",
                "binding": {
                    "kind": "python-entrypoint",
                    "path": "_rtx/_ping.py",
                    "symbol": "Interface",
                    "args_prefix": ["ping"],
                },
                "allow_all_skills": True,
                "allowed_callers": [],
                "dependencies": [],
                "uses_interfaces": [],
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
                "platform_support": {"linux": True, "macos": True, "windows": True},
            },
            sort_keys=False,
        )
    )

    resolved = resolve_dispatch(
        caller_skill="demo-skill",
        target="demo-skill.machine.ping",
        args=["--route-smoke"],
        repo_root=tmp_path,
    )

    assert resolved.command[-2:] == ["ping", "--route-smoke"]


def test_typed_command_file_executes_directly_from_opaque_cx_directory(
    tmp_path: Path,
) -> None:
    _skill, sidecar, command_file = _write_typed_command_skill(tmp_path)
    declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    declaration["binding"]["args_prefix"] = ["fixed"]
    sidecar.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")

    resolved = resolve_dispatch(
        caller_skill="demo-skill",
        target="demo-skill.machine.run",
        args=["dynamic"],
        repo_root=tmp_path,
    )

    assert resolved.command == [
        f"/proc/self/fd/{resolved.pass_fds[0]}",
        "fixed",
        "dynamic",
    ]
    assert resolved.runtime_bindings[0].path == command_file
    assert resolved.command[0] not in {"bash", "sh"}
    resolved.close()


def test_version_three_command_dispatch_does_not_expand_legacy_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill, sidecar, command_file = _write_v3_command_skill(tmp_path)
    other = skill / "_cx" / "other"
    other.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    other.chmod(0o755)
    declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    declaration["content"] = [r"_cx/(?:run-task|other)"]
    sidecar.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(
        "officina.dispatcher.core.expanded_legacy_blueprint",
        lambda _graph: pytest.fail("v3 dispatch must not expand legacy invocation"),
    )

    resolved = resolve_dispatch(
        caller_skill="demo-skill",
        target="demo-skill.machine.run",
        args=["dynamic"],
        repo_root=tmp_path,
    )

    assert resolved.runtime_bindings[0].path == command_file
    assert resolved.runtime_bindings[0].path != other
    resolved.close()


def test_version_three_python_dispatch_does_not_expand_legacy_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skill, _sidecar, gateway = _write_v3_python_skill(tmp_path)
    monkeypatch.setattr(
        "officina.dispatcher.core.expanded_legacy_blueprint",
        lambda _graph: pytest.fail("v3 dispatch must not expand legacy invocation"),
    )

    resolved = resolve_dispatch(
        caller_skill="demo-skill",
        target="demo-skill.machine.run",
        args=["--route-smoke"],
        repo_root=tmp_path,
    )

    assert resolved.runtime_bindings[0].path == gateway
    assert resolved.metadata().command[-2:] == [
        "_rtx/_run.py:Interface",
        "--route-smoke",
    ]
    resolved.close()


def test_command_file_runtime_rejects_traversal_and_non_executable(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    (skill / "_cx").mkdir(parents=True)
    command = skill / "_cx" / "run-task"
    command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    command.chmod(0o644)

    with pytest.raises(InvocationError, match="parent traversal"):
        build_machine_runtime(
            "demo-skill",
            "run",
            {"invocation": {"kind": "command_file", "path": "_cx/../escape"}},
            [],
            repo_root=tmp_path,
        )
    with pytest.raises(InvocationError, match="not executable"):
        build_machine_runtime(
            "demo-skill",
            "run",
            {"invocation": {"kind": "command_file", "path": "_cx/run-task"}},
            [],
            repo_root=tmp_path,
        )


def test_command_file_runtime_fails_closed_without_descriptor_safe_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = tmp_path / "skills" / "demo-skill" / "_cx"
    skill.mkdir(parents=True)
    command = skill / "run-task"
    command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    command.chmod(0o755)
    monkeypatch.setattr(
        "officina.common.blueprint_graph._descriptor_safe_open_supported",
        lambda: False,
    )

    with pytest.raises(InvocationError, match="unavailable"):
        build_machine_runtime(
            "demo-skill",
            "run",
            {"invocation": {"kind": "command_file", "path": "_cx/run-task"}},
            [],
            repo_root=tmp_path,
        )


def test_command_file_runtime_rejects_execute_bit_for_wrong_identity_class(
    tmp_path: Path,
) -> None:
    command_dir = tmp_path / "skills" / "demo-skill" / "_cx"
    command_dir.mkdir(parents=True)
    command = command_dir / "run-task"
    command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    command.chmod(0o001)

    with pytest.raises(InvocationError, match="not executable"):
        build_machine_runtime(
            "demo-skill",
            "run",
            {"invocation": {"kind": "command_file", "path": "_cx/run-task"}},
            [],
            repo_root=tmp_path,
        )


def test_dispatch_normalizes_bound_command_launch_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_typed_command_skill(tmp_path)

    def fail_launch(_argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(subprocess, "run", fail_launch)

    with pytest.raises(InvocationError, match="launch failed.*Permission denied"):
        dispatch(
            caller_skill="demo-skill",
            target="demo-skill.machine.run",
            repo_root=tmp_path,
        )


def test_dispatcher_rejects_schema_invalid_typed_sidecar_with_json_path(
    tmp_path: Path,
) -> None:
    _skill, sidecar, _command = _write_typed_command_skill(tmp_path)
    declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    del declaration["description"]
    sidecar.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")

    with pytest.raises(InvocationError) as captured:
        resolve_dispatch(
            caller_skill="demo-skill",
            target="demo-skill.machine.run",
            repo_root=tmp_path,
        )

    message = str(captured.value)
    assert str(sidecar) in message
    assert "$.description" in message


@pytest.mark.parametrize("location", ["root", "sidecar"])
@pytest.mark.parametrize("schema_version", [None, 1])
def test_dispatcher_rejects_missing_or_wrong_typed_schema_version(
    tmp_path: Path,
    location: str,
    schema_version: int | None,
) -> None:
    skill, sidecar, _command = _write_typed_command_skill(tmp_path)
    target = skill / "blueprint.yaml" if location == "root" else sidecar
    declaration = yaml.safe_load(target.read_text(encoding="utf-8"))
    if schema_version is None:
        declaration.pop("schema_version")
    else:
        declaration["schema_version"] = schema_version
    target.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")

    with pytest.raises(InvocationError) as captured:
        resolve_dispatch(
            caller_skill="demo-skill",
            target="demo-skill.machine.run",
            repo_root=tmp_path,
        )

    message = str(captured.value)
    assert str(target) in message
    assert "$.schema_version" in message


@pytest.mark.parametrize(
    ("location", "field"),
    [("root", "id"), ("sidecar", "id"), ("sidecar", "version")],
)
def test_dispatcher_schema_validates_required_identity_before_graph_semantics(
    tmp_path: Path,
    location: str,
    field: str,
) -> None:
    skill, sidecar, _command = _write_typed_command_skill(tmp_path)
    target = skill / "blueprint.yaml" if location == "root" else sidecar
    declaration = yaml.safe_load(target.read_text(encoding="utf-8"))
    del declaration[field]
    target.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")

    with pytest.raises(InvocationError) as captured:
        resolve_dispatch(
            caller_skill="demo-skill",
            target="demo-skill.machine.run",
            repo_root=tmp_path,
        )

    message = str(captured.value)
    assert str(target) in message
    assert f"$.{field}" in message


def test_dispatcher_normalizes_malformed_root_yaml(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("interfaces: [\n", encoding="utf-8")

    with pytest.raises(InvocationError) as captured:
        resolve_dispatch(
            caller_skill="demo-skill",
            target="demo-skill.machine.run",
            repo_root=tmp_path,
        )

    assert str(skill / "blueprint.yaml") in str(captured.value)
    assert "YAML" in str(captured.value)


@pytest.mark.parametrize("schema_state", ["missing", "malformed"])
def test_dispatcher_normalizes_concrete_schema_bundle_failures(
    tmp_path: Path,
    schema_state: str,
) -> None:
    _skill, _sidecar, _command = _write_typed_command_skill(tmp_path)
    schema_path = tmp_path / "references" / "blueprint" / "v2" / "machine-interface.schema.json"
    if schema_state == "missing":
        schema_path.unlink()
    else:
        schema_path.write_text("{", encoding="utf-8")

    with pytest.raises(InvocationError) as captured:
        resolve_dispatch(
            caller_skill="demo-skill",
            target="demo-skill.machine.run",
            repo_root=tmp_path,
        )

    message = str(captured.value)
    assert str(schema_path) in message
    assert "$" in message


def test_dispatcher_normalizes_unresolved_concrete_schema_reference(
    tmp_path: Path,
) -> None:
    _write_typed_command_skill(tmp_path)
    schema_path = tmp_path / "references" / "blueprint" / "v2" / "machine-interface.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["$ref"] = "missing-local.schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(InvocationError) as captured:
        resolve_dispatch(
            caller_skill="demo-skill",
            target="demo-skill.machine.run",
            repo_root=tmp_path,
        )

    message = str(captured.value)
    sidecar = tmp_path / "skills" / "demo-skill" / "_cx" / ".run-task.blueprint.yaml"
    assert str(schema_path) in message
    assert str(sidecar) in message
    assert "$" in message


def test_dispatcher_normalizes_not_directory_local_input_failure(tmp_path: Path) -> None:
    skill, sidecar, _command = _write_typed_command_skill(tmp_path)
    (skill / "inputs").write_text("not a directory\n", encoding="utf-8")
    declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    declaration["local_hash_inputs"] = ["inputs/policy.txt"]
    sidecar.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")

    with pytest.raises(InvocationError) as captured:
        resolve_dispatch(
            caller_skill="demo-skill",
            target="demo-skill.machine.run",
            repo_root=tmp_path,
        )

    assert "inputs/policy.txt" in str(captured.value)
    assert "not a directory" in str(captured.value).lower()


def test_dispatcher_accepts_schema_valid_copied_install_without_git(
    tmp_path: Path,
) -> None:
    _write_typed_command_skill(tmp_path)
    assert not (tmp_path / ".git").exists()

    completed = dispatch(
        caller_skill="demo-skill",
        target="demo-skill.machine.run",
        capture_output=True,
        repo_root=tmp_path,
    )

    assert completed.returncode == 0


@pytest.mark.parametrize("swap_target", ["final", "parent"])
def test_command_file_execution_uses_validated_object_after_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swap_target: str,
) -> None:
    skill, _sidecar, command = _write_typed_command_skill(tmp_path)
    command.write_text("#!/bin/sh\nprintf 'trusted\\n'\n", encoding="utf-8")
    command.chmod(0o755)
    real_run = subprocess.run

    def swap_then_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if swap_target == "final":
            command.replace(command.with_name("trusted-command"))
        else:
            commands = skill / "_cx"
            commands.replace(skill / "trusted-cx")
            commands.mkdir()
        replacement = skill / "_cx" / "run-task"
        replacement.write_text("#!/bin/sh\nprintf 'untrusted\\n'\n", encoding="utf-8")
        replacement.chmod(0o755)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", swap_then_run)

    completed = dispatch(
        caller_skill="demo-skill",
        target="demo-skill.machine.run",
        capture_output=True,
        text=True,
        repo_root=tmp_path,
    )

    assert completed.stdout == "trusted\n"


def test_python_package_relative_import_uses_validated_snapshot_after_parent_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT / "src"))
    skill = tmp_path / "skills" / "demo-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_text("VALUE = 'trusted'\n", encoding="utf-8")
    (runtime / "_run.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "from . import VALUE\n"
        "class Interface(PythonMachineInterface):\n"
        "    def run(self, args):\n"
        "        print(VALUE)\n",
        encoding="utf-8",
    )
    (skill / "blueprint.yaml").write_text(
        "category: workflow-general-assistant\n"
        "interfaces:\n"
        "  machine:\n"
        "    run:\n"
        "      version: 1\n"
        "      invocation:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_run.py:Interface\n",
        encoding="utf-8",
    )
    real_run = subprocess.run

    def swap_then_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        runtime.replace(skill / "trusted-rtx")
        runtime.mkdir()
        (runtime / "__init__.py").write_text("VALUE = 'untrusted'\n", encoding="utf-8")
        (runtime / "_run.py").write_text("raise RuntimeError('replacement')\n", encoding="utf-8")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", swap_then_run)

    completed = dispatch(
        caller_skill="demo-skill",
        target="demo-skill.machine.run",
        capture_output=True,
        text=True,
        repo_root=tmp_path,
    )

    assert completed.returncode == 0
    assert completed.stdout == "trusted\n"


def test_python_lazy_import_uses_validated_snapshot_after_parent_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT / "src"))
    skill = tmp_path / "skills" / "demo-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_text("", encoding="utf-8")
    (runtime / "_helper.py").write_text("VALUE = 'trusted'\n", encoding="utf-8")
    (runtime / "_run.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    def run(self, args):\n"
        "        from ._helper import VALUE\n"
        "        print(VALUE)\n",
        encoding="utf-8",
    )
    (skill / "blueprint.yaml").write_text(
        "category: workflow-general-assistant\n"
        "interfaces:\n"
        "  machine:\n"
        "    run:\n"
        "      version: 1\n"
        "      invocation:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_run.py:Interface\n",
        encoding="utf-8",
    )
    real_run = subprocess.run

    def swap_then_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        runtime.replace(skill / "trusted-rtx")
        runtime.mkdir()
        (runtime / "__init__.py").write_text("", encoding="utf-8")
        (runtime / "_helper.py").write_text("VALUE = 'untrusted'\n", encoding="utf-8")
        (runtime / "_run.py").write_text("raise RuntimeError('replacement')\n", encoding="utf-8")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", swap_then_run)

    completed = dispatch(
        caller_skill="demo-skill",
        target="demo-skill.machine.run",
        capture_output=True,
        text=True,
        repo_root=tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "trusted\n"


@pytest.mark.parametrize("swap_target", ["final", "parent"])
def test_python_entrypoint_execution_uses_validated_snapshot_after_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swap_target: str,
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT / "src"))
    skill = tmp_path / "skills" / "demo-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    entrypoint = runtime / "_run.py"
    trusted_source = (
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    def run(self, args):\n"
        "        print('trusted')\n"
    )
    untrusted_source = trusted_source.replace("trusted", "untrusted")
    entrypoint.write_text(trusted_source, encoding="utf-8")
    (skill / "blueprint.yaml").write_text(
        "category: workflow-general-assistant\n"
        "interfaces:\n"
        "  machine:\n"
        "    run:\n"
        "      version: 1\n"
        "      invocation:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_run.py:Interface\n",
        encoding="utf-8",
    )
    real_run = subprocess.run

    def swap_then_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if swap_target == "final":
            entrypoint.replace(entrypoint.with_name("trusted.py"))
        else:
            runtime.replace(skill / "trusted-rtx")
            runtime.mkdir()
        replacement = skill / "_rtx" / "_run.py"
        replacement.write_text(untrusted_source, encoding="utf-8")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", swap_then_run)

    completed = dispatch(
        caller_skill="demo-skill",
        target="demo-skill.machine.run",
        capture_output=True,
        text=True,
        repo_root=tmp_path,
    )

    assert completed.stdout == "trusted\n"


@pytest.mark.parametrize("path_kind", ["sidecar", "binding", "local-input"])
def test_dispatcher_rejects_in_tree_symlinked_typed_inputs(
    tmp_path: Path,
    path_kind: str,
) -> None:
    skill, sidecar, command = _write_typed_command_skill(tmp_path)
    if path_kind == "sidecar":
        real_sidecar = skill / "_cx" / ".real-sidecar.yaml"
        sidecar.replace(real_sidecar)
        sidecar.symlink_to(real_sidecar.name)
    elif path_kind == "binding":
        real_command = skill / "_cx" / "real-command"
        command.replace(real_command)
        command.symlink_to(real_command.name)
    else:
        local_input = skill / "inputs" / "policy.txt"
        local_input.parent.mkdir()
        real_input = skill / "inputs" / "real-policy.txt"
        real_input.write_text("policy\n", encoding="utf-8")
        local_input.symlink_to(real_input.name)
        declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
        declaration["local_hash_inputs"] = ["inputs/policy.txt"]
        sidecar.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")

    with pytest.raises(InvocationError, match="symlink"):
        resolve_dispatch(
            caller_skill="demo-skill",
            target="demo-skill.machine.run",
            repo_root=tmp_path,
        )


def test_dispatcher_rejects_command_path_component_symlink(tmp_path: Path) -> None:
    skill, _sidecar, _command = _write_typed_command_skill(tmp_path)
    commands = skill / "_cx"
    real_commands = skill / "commands"
    commands.replace(real_commands)
    commands.symlink_to(real_commands.name, target_is_directory=True)

    with pytest.raises(InvocationError, match="symlink"):
        resolve_dispatch(
            caller_skill="demo-skill",
            target="demo-skill.machine.run",
            repo_root=tmp_path,
        )


def test_dispatcher_accepts_machine_use_declared_by_caller_llm_interface(
    tmp_path: Path,
) -> None:
    _write_typed_command_skill(tmp_path)
    caller = tmp_path / "skills" / "caller-skill"
    caller.mkdir()
    (caller / "SKILL.md").write_text(
        "Use demo-skill.machine.run.\n",
        encoding="utf-8",
    )
    (caller / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "blueprint_type": "skill",
                "id": "caller-skill",
                "category": "development-assistant",
                "role": "automation",
                "kind": "tool",
                "interfaces": [
                    {
                        "interface": "caller-skill.llm.default",
                        "version": 1,
                        "blueprint": {
                            "base": "skill-root",
                            "path": ".SKILL.md.blueprint.yaml",
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (caller / ".SKILL.md.blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "blueprint_type": "llm-interface",
                "id": "caller-skill.llm.default",
                "version": 1,
                "description": "Caller instructions.",
                "binding": {"kind": "instruction-file", "path": "SKILL.md"},
                "uses_interfaces": [
                    {"interface": "demo-skill.machine.run", "version": 1}
                ],
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    resolved = resolve_dispatch(
        caller_skill="caller-skill",
        target="demo-skill.machine.run",
        repo_root=tmp_path,
    )

    assert resolved.target == "demo-skill.machine.run"
