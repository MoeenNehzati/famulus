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
