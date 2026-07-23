from __future__ import annotations

import inspect
from pathlib import Path
import subprocess

import pytest
import yaml

from officina.common.certification_view import CertificationDecision
from officina.dispatcher.core import (
    InvocationError,
    dispatch,
    resolve_dispatch,
    resolve_dispatch_metadata,
)


class _PassingCertificationView:
    def check_export(self, module_id: str, interface_id: str, interface_version: int, source_node_id: str | None) -> CertificationDecision:
        return CertificationDecision(True, "current", "Current test certificate.")


@pytest.fixture(autouse=True)
def _current_test_certificates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "officina.dispatcher.core.repository_certification_view",
        lambda _root: _PassingCertificationView(),
    )


def test_public_dispatcher_apis_accept_only_canonical_target() -> None:
    for function in (resolve_dispatch, resolve_dispatch_metadata, dispatch):
        parameters = inspect.signature(function).parameters
        assert "target" in parameters
        assert "target_skill" not in parameters
        assert "script_interface" not in parameters
        assert "certification_view" not in parameters
        assert "_certification_view" not in parameters


@pytest.mark.parametrize(
    "target",
    [
        "demo-skill" ".machine" "." "run",
        "demo-skill" ".llm" "." "default",
        "demo-skill",
        "demo-skill.interface",
        "demo-skill.interface.run.extra",
    ],
)
def test_public_dispatch_rejects_noncanonical_v4_target(
    tmp_path: Path,
    target: str,
) -> None:
    with pytest.raises(
        InvocationError,
        match=r"target must have form `<module>\.interface\.<name>`",
    ):
        resolve_dispatch_metadata(
            caller_skill="caller-skill",
            target=target,
            repo_root=tmp_path,
        )


def _v4_contract(arguments: dict[str, object]) -> dict[str, object]:
    return {
        "arguments": arguments,
        "preconditions": [],
        "interaction": {"mode": "unattended"},
        "caller_warnings": [],
        "outputs": [
            {
                "id": "result",
                "audience": "machine",
                "description": "Result.",
                "type": {"kind": "string"},
                "direct_io_ref": "stdout",
                "cardinality": {"minimum": 1, "maximum": 1},
                "ordering": "stable",
                "pagination": {"kind": "none"},
                "truncation": {"kind": "none"},
                "empty": "Never empty.",
            }
        ],
        "outcomes": [
            {
                "id": "success",
                "class": "success",
                "outputs": ["result"],
                "effects": [],
                "caller_action": "Continue.",
            }
        ],
        "execution": {
            "state_effect": "read-only",
            "lifecycle": "finite",
            "consistency": {"snapshot": "One snapshot."},
            "verification": [{"method": "output-schema", "output_ref": "result"}],
        },
        "helpers": [],
        "direct_io": {
            "reads": [],
            "writes": [
                {
                    "id": "stdout",
                    "medium": "stdout",
                    "access": "write",
                    "content": "Result.",
                    "formats": ["text"],
                    "sensitivity": "public",
                }
            ],
            "network": [],
        },
    }


def _write_v4_module(
    repo: Path,
    *,
    language: str = "Python>=3.11",
    worker_source: str | None = None,
    extra_python_sources: dict[str, str] | None = None,
) -> None:
    module = repo / "skills" / "demo-skill"
    runtime = module / "_rtx"
    runtime.mkdir(parents=True)
    (module / "SKILL.md").write_text("Instructions.\n", encoding="utf-8")
    (runtime / "__init__.py").write_text("", encoding="utf-8")
    (runtime / "_worker.py").write_text(
        worker_source
        or (
            "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
            "class Interface(PythonMachineInterface):\n"
            "    def run(self, args):\n"
            "        return 0\n"
        ),
        encoding="utf-8",
    )
    for relative_path, source in (extra_python_sources or {}).items():
        (runtime / relative_path).write_text(source, encoding="utf-8")
    source_id = "demo-skill.source.worker"
    source_interface = f"{source_id}.interface.run"
    (module / "blueprints").mkdir()
    (module / "blueprints" / "worker.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 4,
                "node_type": "behavioral_source",
                "id": source_id,
                "version": 1,
                "description": "Worker.",
                "gateway": {"path": "_rtx/_worker.py", "language": language},
                "content": [r"_rtx/.*\.py"],
                "platform_support": {"linux": True, "macos": True, "windows": True},
                "runtime_dependencies": [],
                "dependencies": [],
                "uses_interfaces": [],
                "interfaces": {
                    source_interface: {
                        "version": 1,
                        "description": "Run.",
                        "contract": _v4_contract(
                            {
                                "value": {
                                    "description": "One value.",
                                    "required": True,
                                    "sensitivity": "public",
                                    "type": {"kind": "string"},
                                },
                                "count": {
                                    "description": "Count.",
                                    "required": False,
                                    "default": 2,
                                    "sensitivity": "public",
                                    "type": {"kind": "integer", "minimum": 1},
                                },
                            }
                        ),
                        "process_binding": {
                            "kind": "process",
                            "entry": "Interface",
                            "args_prefix": ["prefix"],
                            "arguments": {
                                "value": {
                                    "kind": "positional",
                                    "position": 1,
                                    "arity": {"minimum": 1, "maximum": 1},
                                },
                                "count": {
                                    "kind": "option",
                                    "name": "--count",
                                    "arity": {"minimum": 1, "maximum": 1},
                                },
                            },
                            "fixed": [
                                {
                                    "kind": "positional",
                                    "position": 0,
                                    "value": "run",
                                    "type": {"kind": "string"},
                                }
                            ],
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (module / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 4,
                "node_type": "module",
                "id": "demo-skill",
                "version": 1,
                "description": "Demo.",
                "gateway": {"path": "SKILL.md", "language": "Markdown"},
                "content": [r"SKILL\.md", r"_rtx/(?:__init__|_worker)\.py"],
                "authority": {"owns_filesystem": []},
                "sources": {
                    source_id: {
                        "blueprint": {
                            "base": "module-root",
                            "path": "blueprints/worker.yaml",
                        }
                    }
                },
                "exports": {
                    "demo-skill.interface.run": {
                        "source_interface": source_interface,
                        "access": {
                            "allow_all_modules": True,
                            "allowed_callers": [],
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_v4_caller(
    repo: Path,
    *,
    interface: str | None = "demo-skill.interface.run",
    version: int = 1,
) -> None:
    module = repo / "skills" / "caller-skill"
    module.mkdir(parents=True)
    (module / "SKILL.md").write_text("Caller.\n", encoding="utf-8")
    source_id = "caller-skill.source.gateway"
    uses = (
        [{"interface": interface, "version": version}]
        if interface is not None
        else []
    )
    (module / "blueprints").mkdir()
    (module / "blueprints" / "gateway.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 4,
                "node_type": "behavioral_source",
                "id": source_id,
                "version": 1,
                "description": "Caller.",
                "gateway": {"path": "SKILL.md", "language": "Markdown"},
                "content": [r"SKILL\.md"],
                "dependencies": [],
                "uses_interfaces": uses,
                "interfaces": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (module / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 4,
                "node_type": "module",
                "id": "caller-skill",
                "version": 1,
                "description": "Caller.",
                "gateway": {"path": "SKILL.md", "language": "Markdown"},
                "content": [r"SKILL\.md"],
                "authority": {"owns_filesystem": []},
                "sources": {
                    source_id: {
                        "blueprint": {
                            "base": "module-root",
                            "path": "blueprints/gateway.yaml",
                        }
                    }
                },
                "exports": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_v4_export_uses_source_gateway_language_and_process_entry(tmp_path: Path) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path)

    metadata = resolve_dispatch_metadata(
        caller_skill="caller-skill",
        target="demo-skill.interface.run",
        args=["value"],
        repo_root=tmp_path,
    )

    assert metadata.target == "demo-skill.interface.run"
    assert metadata.command[-6:] == [
        "_rtx/_worker.py:Interface",
        "prefix",
        "run",
        "value",
        "--count",
        "2",
    ]


def test_v4_dispatch_uses_repository_certification_view_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path)
    observed: list[Path] = []

    def repository_view(root: Path) -> _PassingCertificationView:
        observed.append(root)
        return _PassingCertificationView()

    monkeypatch.setattr(
        "officina.dispatcher.core.repository_certification_view",
        repository_view,
    )

    metadata = resolve_dispatch_metadata(
        caller_skill="caller-skill",
        target="demo-skill.interface.run",
        args=["value"],
        repo_root=tmp_path,
    )

    assert metadata.target == "demo-skill.interface.run"
    assert observed == [tmp_path.resolve()]


def test_v4_route_smoke_bypasses_required_caller_arguments(tmp_path: Path) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path)

    metadata = resolve_dispatch_metadata(
        caller_skill="caller-skill",
        target="demo-skill.interface.run",
        args=["--route-smoke"],
        repo_root=tmp_path,
    )

    assert metadata.command[-4:] == [
        "_rtx/_worker.py:Interface",
        "prefix",
        "run",
        "--route-smoke",
    ]


def test_v4_process_dispatch_fails_closed_for_unsupported_language(tmp_path: Path) -> None:
    _write_v4_module(tmp_path, language="Markdown")
    _write_v4_caller(tmp_path)

    with pytest.raises(InvocationError, match="unsupported process binding language"):
        resolve_dispatch_metadata(
            caller_skill="caller-skill",
            target="demo-skill.interface.run",
            args=["value"],
            repo_root=tmp_path,
        )


def test_v4_dispatch_rejects_unknown_caller_module(tmp_path: Path) -> None:
    _write_v4_module(tmp_path)

    with pytest.raises(InvocationError, match="caller module.*does not exist"):
        resolve_dispatch_metadata(
            caller_skill="caller-skill",
            target="demo-skill.interface.run",
            args=["value"],
            repo_root=tmp_path,
        )


def test_v4_dispatch_requires_exact_declared_use_in_contained_source(
    tmp_path: Path,
) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path, interface=None)

    with pytest.raises(InvocationError, match="does not declare use.*version 1"):
        resolve_dispatch_metadata(
            caller_skill="caller-skill",
            target="demo-skill.interface.run",
            args=["value"],
            repo_root=tmp_path,
        )


def test_v4_dispatch_rejects_declared_use_with_wrong_version(tmp_path: Path) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path, version=2)

    with pytest.raises(InvocationError, match="version 2.*target version is 1"):
        resolve_dispatch_metadata(
            caller_skill="caller-skill",
            target="demo-skill.interface.run",
            args=["value"],
            repo_root=tmp_path,
        )


def test_v4_python_runtime_preserves_utf8_and_descriptor_confinement(
    tmp_path: Path,
) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path)

    with resolve_dispatch(
        caller_skill="caller-skill",
        target="demo-skill.interface.run",
        args=["value"],
        repo_root=tmp_path,
    ) as resolved:
        assert resolved.env is not None
        assert resolved.env["PYTHONIOENCODING"] == "utf-8:strict"
        assert resolved.command[1:3] == [
            "-m",
            "officina.runtime.python_machine_interface_runner",
        ]
        assert "--source-fd" in resolved.command
        assert "--package-file" in resolved.command
        assert resolved.pass_fds


def test_v4_dispatch_pins_utf8_strict_text_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("officina.dispatcher.core.subprocess.run", fake_run)

    result = dispatch(
        caller_skill="caller-skill",
        target="demo-skill.interface.run",
        args=["value"],
        text=True,
        repo_root=tmp_path,
    )

    assert result.returncode == 0
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "strict"


def test_v4_dispatch_normalizes_launch_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path)

    def fail_launch(_command: list[str], **_kwargs: object):
        raise OSError("launch broke")

    monkeypatch.setattr("officina.dispatcher.core.subprocess.run", fail_launch)

    with pytest.raises(InvocationError, match="launch failed: launch broke"):
        dispatch(
            caller_skill="caller-skill",
            target="demo-skill.interface.run",
            args=["value"],
            repo_root=tmp_path,
        )


def test_v4_dispatch_rejects_symlinked_python_gateway(tmp_path: Path) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path)
    worker = tmp_path / "skills" / "demo-skill" / "_rtx" / "_worker.py"
    real_worker = worker.with_name("_real_worker.py")
    worker.replace(real_worker)
    worker.symlink_to(real_worker.name)

    with pytest.raises(InvocationError, match="gateway must be included in content"):
        resolve_dispatch_metadata(
            caller_skill="caller-skill",
            target="demo-skill.interface.run",
            args=["value"],
            repo_root=tmp_path,
        )
