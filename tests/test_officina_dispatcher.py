from __future__ import annotations

import inspect
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from officina.common.certification_view import CertificationDecision
from officina.common.blueprint_graph import load_repository_blueprint_graph
import officina.dispatcher.core as dispatcher_core
from officina.dispatcher.core import (
    InvocationError,
    ResolvedInvocation,
    ResolvedInvocationMetadata,
    dispatch,
    resolve_dispatch,
    resolve_dispatch_metadata,
)
from v5_blueprint_fixtures import copy_v5_fixture_tree


V5_SCHEMA_ROOT = (
    Path(__file__).resolve().parents[1] / "references" / "blueprint"
)
V4_SCHEMA_ROOT = V5_SCHEMA_ROOT / "migrations" / "v4"
V5_AUTHORIZATION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "blueprint_v5" / "authorization"
)


class _PassingCertificationView:
    def check_export(self, module_id: str, interface_id: str, interface_version: int, source_node_id: str | None) -> CertificationDecision:
        return CertificationDecision(True, "current", "Current test certificate.")


@pytest.fixture(autouse=True)
def _current_test_certificates(monkeypatch: pytest.MonkeyPatch) -> None:
    real_load = dispatcher_core.load_repository_blueprint_graph

    def load_fixture_graph(
        root: Path,
        *args: object,
        **kwargs: object,
    ) -> object:
        if "expected_schema_version" not in kwargs:
            for marker in Path(root).rglob("blueprint.yaml"):
                document = yaml.safe_load(marker.read_text(encoding="utf-8"))
                if isinstance(document, dict) and document.get("schema_version") == 4:
                    kwargs["expected_schema_version"] = 4
                    kwargs["schema_root"] = V4_SCHEMA_ROOT
                    break
        return real_load(root, *args, **kwargs)

    monkeypatch.setattr(
        dispatcher_core,
        "load_repository_blueprint_graph",
        load_fixture_graph,
    )
    monkeypatch.setattr(
        "officina.dispatcher.core.repository_certification_view",
        lambda *_args, **_kwargs: _PassingCertificationView(),
    )


def _use_descriptor_free_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "PYTHONPATH",
        str(Path(__file__).resolve().parents[1] / "src"),
    )
    monkeypatch.setattr(
        dispatcher_core,
        "descriptor_safe_open_supported",
        lambda: False,
    )


def _track_runtime_snapshot_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Path]:
    real_mkstemp = dispatcher_core.tempfile.mkstemp
    paths: list[Path] = []

    def tracked_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        descriptor, raw_path = real_mkstemp(*args, **kwargs)
        paths.append(Path(raw_path))
        return descriptor, raw_path

    monkeypatch.setattr(dispatcher_core.tempfile, "mkstemp", tracked_mkstemp)
    return paths


def test_public_dispatcher_apis_accept_only_canonical_target() -> None:
    for function in (resolve_dispatch, resolve_dispatch_metadata, dispatch):
        parameters = inspect.signature(function).parameters
        assert "target" in parameters
        assert "target_skill" not in parameters
        assert "script_interface" not in parameters
        assert "certification_view" not in parameters
        assert "_certification_view" not in parameters


def test_non_python_metadata_emits_null_python_target(tmp_path: Path) -> None:
    metadata = ResolvedInvocationMetadata(
        caller_module_id="caller",
        target_module_id="target",
        script_interface="default",
        target="target.interface.default",
        pattern="default",
        cwd=tmp_path,
        command=["opaque"],
        stdin=False,
    )

    assert metadata.as_payload()["python_target"] is None


def test_resolved_invocation_types_accept_legacy_v4_module_keywords(
    tmp_path: Path,
) -> None:
    common = {
        "caller_skill": "caller",
        "target_skill": "target",
        "script_interface": "run",
        "target": "target.interface.run",
        "pattern": "run",
        "cwd": tmp_path,
        "command": ["opaque"],
        "stdin": False,
    }

    metadata = ResolvedInvocationMetadata(**common)
    invocation = ResolvedInvocation(**common)

    assert metadata.caller_module_id == invocation.caller_module_id == "caller"
    assert metadata.target_module_id == invocation.target_module_id == "target"


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
    module_root: Path | None = None,
) -> None:
    module = module_root or repo / "skills" / "demo-skill"
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


def _load_v5_dispatch_graph(tmp_path: Path):
    root = copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE,
        tmp_path / "repo",
    )
    source_blueprint = (
        root
        / "skills"
        / "demo"
        / "_rtx"
        / "blueprints"
        / "runtime.yaml"
    )
    declaration = yaml.safe_load(source_blueprint.read_text(encoding="utf-8"))
    interface = declaration["interfaces"][
        "demo-rtx.source.runtime.interface.execute"
    ]
    interface["contract"] = _v4_contract({})
    interface["process_binding"] = {
        "kind": "process",
        "entry": "Interface",
        "args_prefix": [],
        "arguments": {},
        "fixed": [],
    }
    source_blueprint.write_text(
        yaml.safe_dump(declaration, sort_keys=False),
        encoding="utf-8",
    )
    child_root = root / "skills" / "demo" / "_rtx"
    child_blueprint = yaml.safe_load(
        (child_root / "blueprint.yaml").read_text(encoding="utf-8")
    )
    child_blueprint["content"] = [
        r"(?:__init__\.py|runtime\.py|caller\.py)"
    ]
    child_blueprint["sources"]["demo-rtx.source.caller"] = {
        "blueprint": {
            "base": "module-root",
            "path": "blueprints/caller.yaml",
        }
    }
    (child_root / "blueprint.yaml").write_text(
        yaml.safe_dump(child_blueprint, sort_keys=False),
        encoding="utf-8",
    )
    (child_root / "blueprints" / "caller.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 5,
                "node_type": "behavioral_source",
                "id": "demo-rtx.source.caller",
                "version": 1,
                "gateway": {
                    "path": "caller.py",
                    "language": "Python>=3.11",
                },
                "content": [r"caller\.py"],
                "dependencies": [],
                "uses_interfaces": [
                    {"interface": "demo.interface.execute", "version": 3}
                ],
                "interfaces": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (child_root / "caller.py").write_text("", encoding="utf-8")
    runtime = child_root / "runtime.py"
    runtime.write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    def run(self, args):\n"
        "        return 0\n",
        encoding="utf-8",
    )
    graph = load_repository_blueprint_graph(
        root,
        schema_root=V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )
    return root, graph


def _resolve_v5_trace(
    root: Path,
    graph,
    *,
    caller_skill: str,
    target: str,
    caller_source_id: str | None = None,
    target_version: int = 3,
) -> ResolvedInvocationMetadata:
    if caller_source_id is None:
        caller_source_id = {
            "demo": "demo.source.gateway",
            "demo-rtx": "demo-rtx.source.caller",
        }[caller_skill]
    return dispatcher_core._resolve_dispatch_metadata_for_trace(
        caller_module_id=caller_skill,
        caller_source_id=caller_source_id,
        target=target,
        args=["--route-smoke"],
        repo_root=root,
        target_version=target_version,
        certification_view=_PassingCertificationView(),
        graph=graph,
    )


def test_v5_dispatch_preserves_module_caller_and_ignores_source_identity(
    tmp_path: Path,
) -> None:
    root, graph = _load_v5_dispatch_graph(tmp_path)

    metadata = _resolve_v5_trace(
        root,
        graph,
        caller_skill="demo-rtx",
        caller_source_id="demo-rtx.source.caller",
        target="demo.interface.execute",
    )

    assert metadata.caller_module_id == "demo-rtx"
    assert metadata.target_module_id == "demo"
    assert metadata.terminal_module_id == "demo-rtx"
    assert metadata.implementing_source_id == "demo-rtx.source.runtime"
    payload = metadata.as_payload()
    assert payload["caller_module_id"] == "demo-rtx"
    assert payload["target_module_id"] == "demo"
    assert "caller_skill" not in payload
    assert "target_skill" not in payload

    source_mismatch = _resolve_v5_trace(
        root,
        graph,
        caller_skill="demo",
        caller_source_id="demo-rtx.source.caller",
        target="demo.interface.execute",
    )
    assert source_mismatch.caller_module_id == "demo"


def test_v5_dispatch_distinguishes_parent_and_code_child_callers(
    tmp_path: Path,
) -> None:
    root, graph = _load_v5_dispatch_graph(tmp_path)

    parent = _resolve_v5_trace(
        root,
        graph,
        caller_skill="demo",
        target="demo-rtx.interface.execute",
    )
    child = _resolve_v5_trace(
        root,
        graph,
        caller_skill="demo-rtx",
        target="demo.interface.execute",
    )

    assert parent.caller_module_id == "demo"
    assert child.caller_module_id == "demo-rtx"


def test_v5_dispatch_admits_facade_and_direct_child_only_via_shared_resolver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, graph = _load_v5_dispatch_graph(tmp_path)
    real_resolve = dispatcher_core.resolve_interface_authorization
    requests = []

    def recording_resolve(loaded_graph, request):
        requests.append(request)
        return real_resolve(loaded_graph, request)

    monkeypatch.setattr(
        dispatcher_core,
        "resolve_interface_authorization",
        recording_resolve,
    )

    facade = _resolve_v5_trace(
        root,
        graph,
        caller_skill="demo-rtx",
        target="demo.interface.execute",
    )
    direct = _resolve_v5_trace(
        root,
        graph,
        caller_skill="demo",
        target="demo-rtx.interface.execute",
    )

    assert facade.target == "demo.interface.execute"
    assert direct.target == "demo-rtx.interface.execute"
    assert facade.target_module_id == "demo"
    assert direct.target_module_id == "demo-rtx"
    assert facade.terminal_module_id == direct.terminal_module_id == "demo-rtx"
    assert [request.interface_id for request in requests] == [
        "demo.interface.execute",
        "demo-rtx.interface.execute",
    ]


def test_v5_wrong_version_is_denied_by_exactly_one_shared_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, graph = _load_v5_dispatch_graph(tmp_path)
    real_resolve = dispatcher_core.resolve_interface_authorization
    requests = []

    def recording_resolve(loaded_graph, request):
        requests.append(request)
        return real_resolve(loaded_graph, request)

    monkeypatch.setattr(
        dispatcher_core,
        "resolve_interface_authorization",
        recording_resolve,
    )

    with pytest.raises(InvocationError, match="version-mismatch"):
        _resolve_v5_trace(
            root,
            graph,
            caller_skill="demo-rtx",
            target="demo.interface.execute",
            target_version=2,
        )

    assert len(requests) == 1
    assert requests[0].version == 2


def test_v5_certification_seam_receives_the_authorization_result(
    tmp_path: Path,
) -> None:
    root, graph = _load_v5_dispatch_graph(tmp_path)
    observed = []

    class AuthorizationCertificationView:
        def check_authorization(self, authorization):
            observed.append(authorization)
            return CertificationDecision(True, "current", "Current.")

        def check_export(self, *_args):
            pytest.fail("v5 authorization seam fell back to check_export")

    with dispatcher_core._resolve_dispatch(
        caller_skill="demo-rtx",
        caller_source_id="demo-rtx.source.caller",
        target="demo.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
        target_version=3,
        certification_view=AuthorizationCertificationView(),
        graph=graph,
    ) as resolved:
        metadata = resolved.metadata()
        assert observed == [resolved.authorization]
        assert metadata.authorization is resolved.authorization


def test_v5_bootstrap_seam_receives_the_exact_requested_target_module(
    tmp_path: Path,
) -> None:
    root, graph = _load_v5_dispatch_graph(tmp_path)
    observed = []

    class BootstrapCertificationView:
        def check_authorization(self, _authorization):
            return CertificationDecision(False, "stale", "Stale.")

        def check_bootstrap(self, **request):
            observed.append(request)
            return CertificationDecision(True, "bootstrap", "Bootstrap.")

    with dispatcher_core._resolve_dispatch(
        caller_skill="demo-rtx",
        caller_source_id="demo-rtx.source.caller",
        target="demo.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
        target_version=3,
        certification_view=BootstrapCertificationView(),
        graph=graph,
    ):
        pass

    assert observed == [
        {
            "caller_module_id": "demo-rtx",
            "target_module_id": "demo",
            "terminal_module_id": "demo-rtx",
            "interface_id": "demo.interface.execute",
            "pattern_name": None,
            "argv": ("--route-smoke",),
        }
    ]


def test_v5_stale_certification_is_an_advisory_warning(
    tmp_path: Path,
) -> None:
    root, graph = _load_v5_dispatch_graph(tmp_path)

    class StaleCertificationView:
        def check_authorization(self, _authorization):
            return CertificationDecision(False, "stale", "Certificate is stale.")

        def check_export(self, *_args):
            pytest.fail("v5 authorization seam fell back to check_export")

    with dispatcher_core._resolve_dispatch(
        caller_skill="demo-rtx",
        caller_source_id="demo-rtx.source.caller",
        target="demo.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
        target_version=3,
        certification_view=StaleCertificationView(),
        graph=graph,
    ) as resolved:
        metadata = resolved.metadata()

    assert metadata.diagnostics == (
        dispatcher_core.InvocationDiagnostic(
            severity="warning",
            code="stale",
            message="Certificate is stale.",
            subject="demo.interface.execute",
        ),
    )
    assert metadata.as_payload()["warnings"] == [
        {
            "code": "stale",
            "message": "Certificate is stale.",
            "subject": "demo.interface.execute",
        }
    ]


def test_host_resolution_warns_for_unrelated_invalid_blueprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _graph = _load_v5_dispatch_graph(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    outsider_source = root / "modules" / "outsider" / "blueprints" / "caller.yaml"
    declaration = yaml.safe_load(outsider_source.read_text(encoding="utf-8"))
    declaration["uses_interfaces"] = [
        {"interface": "missing.interface.run", "version": 1}
    ]
    outsider_source.write_text(
        yaml.safe_dump(declaration, sort_keys=False),
        encoding="utf-8",
    )

    metadata = dispatcher_core._resolve_host_dispatch_metadata(
        caller_skill="demo",
        target="demo.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
        certification_view=_PassingCertificationView(),
    )

    assert metadata.diagnostics == (
        dispatcher_core.InvocationDiagnostic(
            severity="warning",
            code="unrelated-blueprint-invalid",
            message=(
                "outsider.source.caller: unresolved interface "
                "'missing.interface.run'"
            ),
        ),
        dispatcher_core.InvocationDiagnostic(
            severity="warning",
            code="dispatcher-catalog-rebuilt",
            message="route catalog was missing; canonical state was rebuilt",
            subject="demo.interface.execute",
        ),
    )


def test_second_host_resolution_reuses_fresh_route_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _graph = _load_v5_dispatch_graph(tmp_path)
    outsider_source = root / "modules" / "outsider" / "blueprints" / "caller.yaml"
    declaration = yaml.safe_load(outsider_source.read_text(encoding="utf-8"))
    declaration["uses_interfaces"] = [
        {"interface": "missing.interface.run", "version": 1}
    ]
    outsider_source.write_text(
        yaml.safe_dump(declaration, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    class UncertifiedView:
        def check_authorization(self, _authorization):
            return CertificationDecision(False, "specific", "Specific warning.")

        def check_bootstrap(self, **_request):
            return CertificationDecision(False, "final", "Final warning.")

    monkeypatch.setattr(
        dispatcher_core,
        "repository_certification_view",
        lambda _root: UncertifiedView(),
    )

    first = dispatcher_core._resolve_host_dispatch_metadata(
        caller_skill="demo",
        target="demo.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
    )

    def unexpected_repository_read(*_args, **_kwargs):
        pytest.fail("fresh route catalog rebuilt repository state")

    monkeypatch.setattr(dispatcher_core, "collect_blueprints", unexpected_repository_read)
    monkeypatch.setattr(
        dispatcher_core,
        "load_dispatch_blueprint_graph",
        unexpected_repository_read,
    )
    monkeypatch.setattr(
        dispatcher_core,
        "load_repository_blueprint_graph",
        unexpected_repository_read,
    )
    monkeypatch.setattr(
        dispatcher_core,
        "repository_certification_view",
        unexpected_repository_read,
    )

    second = dispatcher_core._resolve_host_dispatch_metadata(
        caller_skill="demo",
        target="demo.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
    )

    assert second.target == first.target
    rebuild = next(
        item
        for item in first.diagnostics
        if item.code == "dispatcher-catalog-rebuilt"
    )
    assert "missing" in rebuild.message
    assert rebuild.subject == "demo.interface.execute"
    assert not any(
        item.code.startswith("dispatcher-catalog-")
        for item in second.diagnostics
    )
    assert tuple(
        item
        for item in first.diagnostics
        if item.code != "dispatcher-catalog-rebuilt"
    ) == second.diagnostics
    assert second.diagnostics[-1].code == "final"


def test_catalog_write_failure_warns_without_blocking_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _graph = _load_v5_dispatch_graph(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    def failed_store(*_args, **_kwargs):
        raise OSError("cache filesystem is read-only")

    monkeypatch.setattr(dispatcher_core, "store_route_graph", failed_store)

    metadata = dispatcher_core._resolve_host_dispatch_metadata(
        caller_skill="demo",
        target="demo.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
        certification_view=_PassingCertificationView(),
    )

    assert metadata.target == "demo.interface.execute"
    warnings = {item.code: item for item in metadata.diagnostics}
    assert warnings["dispatcher-catalog-rebuilt"].subject == metadata.target
    assert warnings["dispatcher-catalog-write-failed"].subject == metadata.target


def test_catalog_certification_write_failure_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _graph = _load_v5_dispatch_graph(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(
        dispatcher_core,
        "store_route_certification_decision",
        lambda *_args, **_kwargs: False,
    )

    metadata = dispatcher_core._resolve_host_dispatch_metadata(
        caller_skill="demo",
        target="demo.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
    )

    assert any(
        item.code == "dispatcher-catalog-write-failed"
        and "certification" in item.message
        for item in metadata.diagnostics
    )


def test_v5_host_dispatch_accepts_discoverable_parent_not_code_child(
    tmp_path: Path,
) -> None:
    root, graph = _load_v5_dispatch_graph(tmp_path)

    parent = dispatcher_core._resolve_host_dispatch_metadata(
        caller_skill="demo",
        target="demo-rtx.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
        target_version=3,
        certification_view=_PassingCertificationView(),
        graph=graph,
    )

    assert parent.caller_module_id == "demo"
    assert parent.caller_source_id == "demo.source.gateway"
    with pytest.raises(InvocationError, match="not a discoverable host skill"):
        dispatcher_core._resolve_host_dispatch_metadata(
            caller_skill="demo-rtx",
            target="demo.interface.execute",
            args=["--route-smoke"],
            repo_root=root,
            target_version=3,
            certification_view=_PassingCertificationView(),
            graph=graph,
        )


def test_v5_host_dispatch_does_not_treat_declared_use_as_permission(
    tmp_path: Path,
) -> None:
    root, graph = _load_v5_dispatch_graph(tmp_path)
    gateway_path = root / "skills" / "demo" / "blueprints" / "gateway.yaml"
    gateway = yaml.safe_load(gateway_path.read_text(encoding="utf-8"))
    gateway["uses_interfaces"] = []
    gateway_path.write_text(
        yaml.safe_dump(gateway, sort_keys=False),
        encoding="utf-8",
    )
    graph = load_repository_blueprint_graph(
        root,
        schema_root=V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )

    metadata = dispatcher_core._resolve_host_dispatch_metadata(
        caller_skill="demo",
        target="demo-rtx.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
        target_version=3,
        certification_view=_PassingCertificationView(),
        graph=graph,
    )

    assert metadata.caller_module_id == "demo"


def test_v5_python_runtime_uses_child_root_and_logical_package_only(
    tmp_path: Path,
) -> None:
    root, graph = _load_v5_dispatch_graph(tmp_path)

    with dispatcher_core._resolve_dispatch(
        caller_skill="demo-rtx",
        caller_source_id="demo-rtx.source.caller",
        target="demo.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
        target_version=3,
        certification_view=_PassingCertificationView(),
        graph=graph,
    ) as resolved:
        child_root = root / "skills" / "demo" / "_rtx"
        assert resolved.cwd == child_root
        assert resolved.env is not None
        assert str(child_root) not in resolved.env["PYTHONPATH"].split(
            os.pathsep
        )
        assert resolved.python_target is not None
        assert resolved.python_target.gateway_path == Path("runtime.py")
        assert resolved.python_target.logical_package is not None
        assert "--logical-package" in resolved.command
        assert "--physical-package-prefix" in resolved.command
        assert "--runtime-caller-module-id" in resolved.command
        assert "--runtime-caller-source-id" in resolved.command
        assert (
            resolved.command[
                resolved.command.index("--runtime-caller-module-id") + 1
            ]
            == "demo-rtx"
        )
        assert (
            resolved.command[
                resolved.command.index("--runtime-caller-source-id") + 1
            ]
            == "demo-rtx.source.runtime"
        )
        logical_command = resolved.metadata().command
        assert "--runtime-caller-module-id" not in logical_command
        assert "--runtime-caller-source-id" not in logical_command


def test_v5_descriptor_free_snapshot_executes_logical_child_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, graph = _load_v5_dispatch_graph(tmp_path)
    _use_descriptor_free_runtime(monkeypatch)
    resolved = dispatcher_core._resolve_dispatch(
        caller_skill="demo-rtx",
        caller_source_id="demo-rtx.source.caller",
        target="demo.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
        target_version=3,
        certification_view=_PassingCertificationView(),
        graph=graph,
    )

    completed = dispatcher_core._run_resolved_invocation(
        resolved,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == "route-smoke ok\n"


def test_v5_launch_cannot_shadow_the_canonical_runner_from_module_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, graph = _load_v5_dispatch_graph(tmp_path)
    _use_descriptor_free_runtime(monkeypatch)
    child_root = root / "skills" / "demo" / "_rtx"
    inherited_src = Path(__file__).resolve().parents[1] / "src"
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            [
                ".",
                "relative-bootstrap-path",
                str(child_root),
                str(child_root / "nested-bootstrap-path"),
                str(inherited_src),
            ]
        ),
    )
    hostile_runner = child_root / "officina" / "runtime"
    hostile_runner.mkdir(parents=True)
    (child_root / "officina" / "__init__.py").write_text("", encoding="utf-8")
    (hostile_runner / "__init__.py").write_text("", encoding="utf-8")
    marker = child_root / "hostile-runner-loaded"
    (hostile_runner / "python_machine_interface_runner.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('loaded', encoding='utf-8')\n"
        "raise SystemExit(91)\n",
        encoding="utf-8",
    )
    resolved = dispatcher_core._resolve_dispatch(
        caller_skill="demo-rtx",
        caller_source_id="demo-rtx.source.caller",
        target="demo.interface.execute",
        args=["--route-smoke"],
        repo_root=root,
        target_version=3,
        certification_view=_PassingCertificationView(),
        graph=graph,
    )

    assert resolved.command[1] == "-P"
    inherited_entries = resolved.env["PYTHONPATH"].split(os.pathsep)
    assert all(Path(entry).is_absolute() for entry in inherited_entries)
    assert all(
        not Path(entry).resolve().is_relative_to(child_root.resolve())
        for entry in inherited_entries
    )
    assert str(inherited_src) in inherited_entries
    completed = dispatcher_core._run_resolved_invocation(
        resolved,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert not marker.exists()


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
    assert metadata.command[-7:] == [
        "_rtx/_worker.py",
        "Interface",
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

    assert metadata.command[-5:] == [
        "_rtx/_worker.py",
        "Interface",
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


# famulus-skip: category=platform-contract; reason=pass_fds is a POSIX transport; alternate=test_v4_python_runtime_falls_back_to_confined_path_snapshot covers the native-reader path
@pytest.mark.skipif(
    not dispatcher_core.descriptor_safe_open_supported(),
    reason="descriptor inheritance requires POSIX dir-fd support",
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


def test_python_process_target_keeps_gateway_and_entry_separate(
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
        assert resolved.python_target.gateway_path == Path("_rtx/_worker.py")
        assert resolved.python_target.process_entry == "Interface"
        assert resolved.metadata().as_payload()["python_target"] == {
            "gateway_path": "_rtx/_worker.py",
            "process_entry": "Interface",
        }
        logical = resolved.metadata().command
        runner_index = logical.index(
            "officina.runtime.python_machine_interface_runner"
        )
        assert logical[runner_index + 1 : runner_index + 3] == [
            "_rtx/_worker.py",
            "Interface",
        ]
        assert all("_worker.py:Interface" not in token for token in logical)


def test_v4_python_runtime_falls_back_to_confined_path_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path)
    _use_descriptor_free_runtime(monkeypatch)

    def reject_descriptor_package(*_args: object, **_kwargs: object) -> None:
        pytest.fail("descriptor-free runtime attempted to open POSIX package FDs")

    monkeypatch.setattr(
        dispatcher_core,
        "open_runtime_python_package",
        reject_descriptor_package,
    )

    completed = dispatch(
        caller_skill="caller-skill",
        target="demo-skill.interface.run",
        args=["--route-smoke"],
        capture_output=True,
        text=True,
        repo_root=tmp_path,
    )

    assert completed.returncode == 0
    assert completed.stdout == "route-smoke ok\n"


def test_v4_descriptor_free_runtime_executes_parent_snapshot_after_source_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_source = (
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    def route_smoke(self):\n"
        "        print('captured')\n"
        "    def run(self, args):\n"
        "        return 0\n"
    )
    replaced_source = captured_source.replace("'captured'", "'replaced'")
    _write_v4_module(tmp_path, worker_source=captured_source)
    _write_v4_caller(tmp_path)
    _use_descriptor_free_runtime(monkeypatch)
    worker = tmp_path / "skills" / "demo-skill" / "_rtx" / "_worker.py"
    real_run = subprocess.run
    snapshot_paths: list[Path] = []

    def swap_then_run(command: list[str], **kwargs: object):
        snapshot_index = command.index("--package-snapshot") + 1
        snapshot_paths.append(Path(command[snapshot_index]))
        worker.write_text(replaced_source, encoding="utf-8")
        return real_run(command, **kwargs)

    monkeypatch.setattr(dispatcher_core.subprocess, "run", swap_then_run)

    completed = dispatch(
        caller_skill="caller-skill",
        target="demo-skill.interface.run",
        args=["--route-smoke"],
        capture_output=True,
        text=True,
        repo_root=tmp_path,
    )

    assert completed.returncode == 0
    assert completed.stdout == "captured\nroute-smoke ok\n"
    assert snapshot_paths and all(not path.exists() for path in snapshot_paths)


def test_v4_descriptor_free_runtime_rejects_tampered_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path)
    _use_descriptor_free_runtime(monkeypatch)
    real_run = subprocess.run
    snapshot_paths: list[Path] = []

    def tamper_then_run(command: list[str], **kwargs: object):
        snapshot_index = command.index("--package-snapshot") + 1
        snapshot_path = Path(command[snapshot_index])
        snapshot_paths.append(snapshot_path)
        snapshot_path.write_bytes(b"{}")
        return real_run(command, **kwargs)

    monkeypatch.setattr(dispatcher_core.subprocess, "run", tamper_then_run)

    completed = dispatch(
        caller_skill="caller-skill",
        target="demo-skill.interface.run",
        args=["--route-smoke"],
        capture_output=True,
        text=True,
        repo_root=tmp_path,
    )

    assert completed.returncode == 2
    assert "package snapshot digest mismatch" in completed.stderr
    assert snapshot_paths and all(not path.exists() for path in snapshot_paths)


def test_v4_descriptor_free_metadata_and_launch_failure_clean_snapshot_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_v4_module(tmp_path)
    _write_v4_caller(tmp_path)
    _use_descriptor_free_runtime(monkeypatch)
    snapshot_paths = _track_runtime_snapshot_paths(monkeypatch)

    metadata = resolve_dispatch_metadata(
        caller_skill="caller-skill",
        target="demo-skill.interface.run",
        args=["--route-smoke"],
        repo_root=tmp_path,
    )

    assert "--package-snapshot" not in metadata.command
    assert "--package-snapshot-sha256" not in metadata.command
    assert snapshot_paths and all(not path.exists() for path in snapshot_paths)

    def fail_launch(_command: list[str], **_kwargs: object):
        raise OSError("launch broke")

    monkeypatch.setattr(dispatcher_core.subprocess, "run", fail_launch)

    with pytest.raises(InvocationError, match="launch failed: launch broke"):
        dispatch(
            caller_skill="caller-skill",
            target="demo-skill.interface.run",
            args=["--route-smoke"],
            repo_root=tmp_path,
        )

    assert snapshot_paths and all(not path.exists() for path in snapshot_paths)


# famulus-skip: category=platform-contract; reason=this assertion inspects retained POSIX source descriptors; alternate=test_v4_python_runtime_falls_back_to_confined_path_snapshot covers descriptor-free roots
@pytest.mark.skipif(
    not dispatcher_core.descriptor_safe_open_supported(),
    reason="descriptor inheritance requires POSIX dir-fd support",
)
def test_v4_python_runtime_uses_resolved_module_root_outside_skills(
    tmp_path: Path,
) -> None:
    module_root = tmp_path / "modules" / "demo-skill"
    _write_v4_module(tmp_path, module_root=module_root)
    _write_v4_caller(tmp_path)

    with resolve_dispatch(
        caller_skill="caller-skill",
        target="demo-skill.interface.run",
        args=["value"],
        repo_root=tmp_path,
    ) as resolved:
        source_fd_index = resolved.command.index("--source-fd") + 1
        source_fd = int(resolved.command[source_fd_index])
        source_binding = next(
            binding
            for binding in resolved.runtime_bindings
            if binding.fd == source_fd
        )

        assert source_binding.path == module_root / "_rtx" / "_worker.py"
        assert resolved.cwd == module_root
        assert resolved.env is not None
        assert resolved.env["PYTHONPATH"].split(os.pathsep)[:2] == [
            str(module_root),
            str(tmp_path / "src"),
        ]


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
