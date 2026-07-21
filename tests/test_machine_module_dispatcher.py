from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from officina.common.certification_view import CertificationDecision
from officina.dispatcher.core import InvocationError, resolve_dispatch_metadata


class _PassingCertificationView:
    def check_export(self, module_id: str, interface_id: str, interface_version: int) -> CertificationDecision:
        return CertificationDecision(True, "current", "Current test certificate.")


class _FailingCertificationView:
    def check_export(self, module_id: str, interface_id: str, interface_version: int) -> CertificationDecision:
        return CertificationDecision(False, "node-hash-mismatch", "The module changed.")


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
                    "format": "text",
                    "sensitivity": "public",
                }
            ],
            "network": [],
        },
    }


def _write_module(repo: Path) -> None:
    skill = repo / "skills" / "demo-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_text("", encoding="utf-8")
    (runtime / "_worker.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    def run(self, args):\n"
        "        return 0\n",
        encoding="utf-8",
    )
    (skill / "interface-conformance.yaml").write_text(
        "schema_version: 1\n", encoding="utf-8"
    )
    argument = {
        "description": "One value.",
        "required": True,
        "sensitivity": "public",
        "invocation_binding": {
            "kind": "positional",
            "position": 1,
            "arity": {"minimum": 1, "maximum": 1},
        },
        "type": {"kind": "string"},
    }
    optional = {
        "description": "Count.",
        "required": False,
        "default": 2,
        "sensitivity": "public",
        "invocation_binding": {
            "kind": "option",
            "name": "--count",
            "arity": {"minimum": 1, "maximum": 1},
        },
        "type": {"kind": "integer", "minimum": 1},
    }
    export_base = {
        "version": 1,
        "description": "Run.",
        "invocation_binding": {
            "fixed": [
                {
                    "kind": "positional",
                    "position": 0,
                    "value": "run",
                    "type": {"kind": "string"},
                }
            ]
        },
        "uses_interfaces": [],
        "helpers": [],
        "direct_io": {"reads": [], "writes": [], "network": []},
        "owns_filesystem": [],
        "contract": {"arguments": {"value": argument, "count": optional}},
    }
    declaration = {
        "schema_version": 3,
        "node_type": "machine-module",
        "id": "demo-skill.machine-module.worker",
        "version": 1,
        "description": "Worker.",
        "gateway": {
            "kind": "python-entrypoint",
            "path": "_rtx/_worker.py",
            "symbol": "Interface",
            "args_prefix": ["prefix"],
            "conformance": {
                "adapter_protocol": "officina-python-adapters@1",
                "bind_method": "bind_conformance_adapters",
                "sandbox_profile": "officina-isolated-effects@1",
            },
        },
        "content": [r"_rtx/(?:__init__|_worker)\.py"],
        "conformance_manifest": {
            "base": "skill-root",
            "path": "interface-conformance.yaml",
        },
        "platform_support": {"linux": True, "macos": True, "windows": True},
        "dependencies": [],
        "behavior_sources": [],
        "owns_filesystem": [],
        "uses_interfaces": [],
        "interfaces": {
            "run": {
                **export_base,
                "id": "demo-skill.machine.run",
                "allow_all_skills": True,
                "allowed_callers": [],
            },
            "private": {
                **export_base,
                "id": "demo-skill.machine.private",
                "allow_all_skills": False,
                "allowed_callers": ["allowed-skill"],
            },
        },
    }
    (runtime / "._worker.py.blueprint.yaml").write_text(
        yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8"
    )


def _write_v4_module(repo: Path, *, language: str = "Python>=3.11") -> None:
    module = repo / "skills" / "demo-skill"
    runtime = module / "_rtx"
    runtime.mkdir(parents=True)
    (module / "SKILL.md").write_text("Instructions.\n", encoding="utf-8")
    (runtime / "__init__.py").write_text("", encoding="utf-8")
    (runtime / "_worker.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    def run(self, args):\n"
        "        return 0\n",
        encoding="utf-8",
    )
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
                "content": [r"_rtx/(?:__init__|_worker)\.py"],
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


def test_production_view_rejects_machine_modules_until_certification_exists(
    tmp_path: Path,
) -> None:
    _write_module(tmp_path)
    with pytest.raises(InvocationError, match="certification-unavailable"):
        resolve_dispatch_metadata(
            caller_skill="caller-skill",
            target="demo-skill.machine.run",
            args=["value"],
            repo_root=tmp_path,
        )


def test_nested_export_resolves_to_module_gateway_and_compiled_binding(
    tmp_path: Path,
) -> None:
    _write_module(tmp_path)
    metadata = resolve_dispatch_metadata(
        caller_skill="caller-skill",
        target="demo-skill.machine.run",
        args=["value"],
        target_version=1,
        certification_view=_PassingCertificationView(),
        repo_root=tmp_path,
    )

    assert metadata.target == "demo-skill.machine.run"
    assert metadata.script_interface == "run"
    assert metadata.cwd == tmp_path / "skills" / "demo-skill"
    assert metadata.command[-6:] == [
        "_rtx/_worker.py:Interface",
        "prefix",
        "run",
        "value",
        "--count",
        "2",
    ]


def test_certification_reason_and_export_local_access_are_preserved(
    tmp_path: Path,
) -> None:
    _write_module(tmp_path)
    with pytest.raises(InvocationError, match="node-hash-mismatch.*module changed"):
        resolve_dispatch_metadata(
            caller_skill="caller-skill",
            target="demo-skill.machine.run",
            args=["value"],
            certification_view=_FailingCertificationView(),
            repo_root=tmp_path,
        )
    with pytest.raises(InvocationError, match="not allowed"):
        resolve_dispatch_metadata(
            caller_skill="caller-skill",
            target="demo-skill.machine.private",
            args=["value"],
            certification_view=_PassingCertificationView(),
            repo_root=tmp_path,
        )


def test_module_id_is_not_publicly_callable(tmp_path: Path) -> None:
    _write_module(tmp_path)
    with pytest.raises(InvocationError, match="module id.*not callable"):
        resolve_dispatch_metadata(
            caller_skill="caller-skill",
            target="demo-skill.machine-module.worker",
            args=[],
            certification_view=_PassingCertificationView(),
            repo_root=tmp_path,
        )


def test_v4_export_uses_source_gateway_language_and_process_entry(tmp_path: Path) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path)

    metadata = resolve_dispatch_metadata(
        caller_skill="caller-skill",
        target="demo-skill.interface.run",
        args=["value"],
        certification_view=_PassingCertificationView(),
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


def test_v4_route_smoke_bypasses_required_caller_arguments(tmp_path: Path) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path)

    metadata = resolve_dispatch_metadata(
        caller_skill="caller-skill",
        target="demo-skill.interface.run",
        args=["--route-smoke"],
        certification_view=_PassingCertificationView(),
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
            certification_view=_PassingCertificationView(),
            repo_root=tmp_path,
        )


def test_v4_dispatch_rejects_unknown_caller_module(tmp_path: Path) -> None:
    _write_v4_module(tmp_path)

    with pytest.raises(InvocationError, match="caller module.*does not exist"):
        resolve_dispatch_metadata(
            caller_skill="caller-skill",
            target="demo-skill.interface.run",
            args=["value"],
            certification_view=_PassingCertificationView(),
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
            certification_view=_PassingCertificationView(),
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
            certification_view=_PassingCertificationView(),
            repo_root=tmp_path,
        )
