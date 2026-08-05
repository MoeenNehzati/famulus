"""Authorization tests for direct v6 dispatch without a repository graph."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from officina.common.repository_configuration import RepositoryConfiguration
from officina.dispatcher.direct_authorization import resolve_direct_invocation
from officina.dispatcher.direct_runtime import (
    _dispatch_host,
    _resolve_host_dispatch_metadata,
    resolve_dispatch_metadata,
)
from officina.dispatcher.errors import (
    DirectBlueprintError,
    UnauthorizedCallerError,
)


INTERFACE_ID = "root.alpha.leaf.interface.execute"
SOURCE_ID = "root.alpha.leaf.source.runtime"
SOURCE_INTERFACE_ID = f"{SOURCE_ID}.interface.execute"


def _access(*callers: str, public: bool = False) -> dict[str, object]:
    return {"allow_all_modules": public, "allowed_callers": list(callers)}


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _module(
    module_id: str,
    *,
    children: tuple[str, ...] = (),
    routes: dict[str, dict[str, object]] | None = None,
    export_access: dict[str, object] | None = None,
) -> dict[str, object]:
    sources: dict[str, object] = {}
    exports: dict[str, object] = {}
    if export_access is not None:
        sources[SOURCE_ID] = {
            "blueprint": {"base": "module-root", "path": "blueprints/runtime.yaml"}
        }
        exports[INTERFACE_ID] = {
            "source_interface": SOURCE_INTERFACE_ID,
            "access": export_access,
        }
    declaration = {
        "schema_version": 6,
        "node_type": "module",
        "id": module_id,
        "version": 1,
        "gateway": {"path": "__init__.py", "language": "Python"},
        "content": [r"__init__\.py"],
        "authority": {"owns_filesystem": []},
        "sources": sources,
        "children": {child: {} for child in children},
        "namespace_exports": routes or {},
        "exports": exports,
    }
    if "." not in module_id:
        declaration["discovery"] = {"mechanism": "skill"}
    return declaration


def _route(access: dict[str, object]) -> dict[str, object]:
    return {
        "version": 1,
        "access": access,
        "surface": {"only": {INTERFACE_ID: 3}},
    }


def _repository(
    tmp_path: Path,
    *,
    terminal_access: dict[str, object],
    root_gate: dict[str, object] | None = None,
    alpha_gate: dict[str, object] | None = None,
) -> RepositoryConfiguration:
    modules = tmp_path / "skills"
    modules.mkdir()
    (tmp_path / "officina.toml").write_text(
        'schema_version = 1\n[modules]\nroots = ["skills"]\n',
        encoding="utf-8",
    )
    documents = {
        "root": _module(
            "root",
            children=("alpha", "beta"),
            routes={
                "alpha": _route(root_gate or _access(public=True)),
                "beta": {
                    "version": 1,
                    "access": _access(public=True),
                    "surface": {"only": {"root.beta.interface.inspect": 1}},
                },
            },
        ),
        "root.alpha": _module(
            "root.alpha",
            children=("leaf",),
            routes={"leaf": _route(alpha_gate or _access(public=True))},
        ),
        "root.alpha.leaf": _module(
            "root.alpha.leaf",
            export_access=terminal_access,
        ),
        "root.beta": _module("root.beta"),
        "outsider": _module("outsider"),
    }
    for module_id, document in documents.items():
        _write_yaml(modules.joinpath(*module_id.split("."), "blueprint.yaml"), document)
    source = {
        "schema_version": 6,
        "node_type": "behavioral_source",
        "id": SOURCE_ID,
        "version": 1,
        "gateway": {"path": "runtime.py", "language": "Python>=3.11"},
        "content": [r"runtime\.py"],
        "dependencies": [],
        "uses_interfaces": [],
        "interfaces": {
            SOURCE_INTERFACE_ID: {
                "version": 3,
                "contract": {"arguments": {}},
                "process_binding": {
                    "kind": "process",
                    "entry": "Interface",
                    "args_prefix": ["read"],
                    "arguments": {},
                },
            }
        },
    }
    _write_yaml(modules / "root" / "alpha" / "leaf" / "blueprints" / "runtime.yaml", source)
    (modules / "root" / "alpha" / "leaf" / "runtime.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "from .helper import message\n"
        "class Interface(PythonMachineInterface):\n"
        "    def build_parser(self):\n"
        "        parser = super().build_parser()\n"
        "        parser.add_argument('command')\n"
        "        return parser\n"
        "    def run(self, args):\n"
        "        print(message)\n"
        "        return 0\n",
        encoding="utf-8",
    )
    (modules / "root" / "alpha" / "leaf" / "helper.py").write_text(
        "message = 'direct-ok'\n",
        encoding="utf-8",
    )
    return RepositoryConfiguration(1, tmp_path / "officina.toml", tmp_path, (modules,))


@pytest.mark.parametrize(
    ("caller", "terminal_access"),
    [
        ("root.alpha.leaf", _access()),
        ("root.alpha", _access("root.alpha")),
        ("root.beta", _access("root")),
        ("root.beta", _access(public=True)),
    ],
)
def test_terminal_access_accepts_self_exact_ancestor_and_public(
    tmp_path: Path,
    caller: str,
    terminal_access: dict[str, object],
) -> None:
    configuration = _repository(tmp_path, terminal_access=terminal_access)

    invocation = resolve_direct_invocation(
        configuration=configuration,
        caller_module_id=caller,
        interface_id=INTERFACE_ID,
        interface_version=3,
        argv=[],
        stdin_requested=False,
    )

    assert invocation.command == ["read"]
    assert invocation.target_module_id == "root.alpha.leaf"
    assert invocation.implementing_source_id == SOURCE_ID
    assert invocation.authorization is not None
    assert invocation.authorization.allowed


def test_allowed_module_admits_descendants_but_not_unrelated_callers(tmp_path: Path) -> None:
    configuration = _repository(
        tmp_path,
        terminal_access=_access("root.alpha"),
        alpha_gate=_access("root.beta"),
    )
    modules = configuration.module_roots[0]
    beta = yaml.safe_load((modules / "root" / "beta" / "blueprint.yaml").read_text())
    beta["children"] = {"child": {}}
    _write_yaml(modules / "root" / "beta" / "blueprint.yaml", beta)
    _write_yaml(modules / "root" / "beta" / "child" / "blueprint.yaml", _module("root.beta.child"))

    # A descendant's ancestry includes root.beta, so it is admitted by design.
    invocation = resolve_direct_invocation(
        configuration=configuration,
        caller_module_id="root.beta.child",
        interface_id=INTERFACE_ID,
        interface_version=3,
        argv=[],
        stdin_requested=False,
    )
    assert invocation.authorization is not None and invocation.authorization.allowed

    with pytest.raises(UnauthorizedCallerError):
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="outsider",
            interface_id=INTERFACE_ID,
            interface_version=3,
            argv=[],
            stdin_requested=False,
        )


def test_hop_local_namespace_owner_replaces_caller(tmp_path: Path) -> None:
    configuration = _repository(
        tmp_path,
        terminal_access=_access("root.alpha"),
        root_gate=_access("outsider"),
        alpha_gate=_access("root"),
    )

    invocation = resolve_direct_invocation(
        configuration=configuration,
        caller_module_id="outsider",
        interface_id=INTERFACE_ID,
        interface_version=3,
        argv=[],
        stdin_requested=False,
    )

    assert invocation.authorization is not None
    assert [gate.route_owner_id for gate in invocation.authorization.crossed_namespace_gates] == ["root", "root.alpha"]
    assert [item.owner_module_id for item in invocation.authorization.effective_filters] == ["root", "root.alpha", "root.alpha.leaf"]


def test_relative_callers_resolve_from_declaring_owner(tmp_path: Path) -> None:
    configuration = _repository(
        tmp_path,
        terminal_access=_access("root.alpha"),
        alpha_gate=_access("..beta"),
    )
    invocation = resolve_direct_invocation(
        configuration=configuration,
        caller_module_id="root.beta",
        interface_id=INTERFACE_ID,
        interface_version=3,
        argv=[],
        stdin_requested=False,
    )
    assert invocation.authorization is not None and invocation.authorization.allowed


def test_namespace_surface_and_version_are_enforced(tmp_path: Path) -> None:
    configuration = _repository(
        tmp_path,
        terminal_access=_access("root.alpha"),
        root_gate=_access("outsider"),
        alpha_gate=_access("outsider"),
    )
    modules = configuration.module_roots[0]
    alpha_path = modules / "root" / "alpha" / "blueprint.yaml"
    alpha = yaml.safe_load(alpha_path.read_text())
    alpha["namespace_exports"]["leaf"]["surface"]["only"] = {}
    _write_yaml(alpha_path, alpha)

    with pytest.raises(DirectBlueprintError) as caught:
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="outsider",
            interface_id=INTERFACE_ID,
            interface_version=3,
            argv=[],
            stdin_requested=False,
        )
    assert caught.value.code == "dispatcher.namespace_surface_excludes_interface"


def test_namespace_version_must_match_registered_child(tmp_path: Path) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))
    root_path = configuration.module_roots[0] / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text())
    root["namespace_exports"]["alpha"]["version"] = 2
    _write_yaml(root_path, root)

    with pytest.raises(DirectBlueprintError) as caught:
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="outsider",
            interface_id=INTERFACE_ID,
            interface_version=3,
            argv=[],
            stdin_requested=False,
        )
    assert caught.value.code == "dispatcher.namespace_version_mismatch"


def test_namespace_version_rejects_boolean_integer_alias(tmp_path: Path) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))
    root_path = configuration.module_roots[0] / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text())
    root["namespace_exports"]["alpha"]["version"] = True
    _write_yaml(root_path, root)

    with pytest.raises(DirectBlueprintError) as caught:
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="outsider",
            interface_id=INTERFACE_ID,
            interface_version=3,
            argv=[],
            stdin_requested=False,
        )
    assert caught.value.code == "dispatcher.namespace_version_mismatch"


def test_namespace_surface_version_rejects_boolean_integer_alias(tmp_path: Path) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))
    modules = configuration.module_roots[0]
    source_path = modules / "root" / "alpha" / "leaf" / "blueprints" / "runtime.yaml"
    source = yaml.safe_load(source_path.read_text())
    source["interfaces"][SOURCE_INTERFACE_ID]["version"] = 1
    _write_yaml(source_path, source)
    for route_path, child in (
        (modules / "root" / "blueprint.yaml", "alpha"),
        (modules / "root" / "alpha" / "blueprint.yaml", "leaf"),
    ):
        route = yaml.safe_load(route_path.read_text())
        route["namespace_exports"][child]["surface"]["only"][INTERFACE_ID] = True
        _write_yaml(route_path, route)

    with pytest.raises(DirectBlueprintError) as caught:
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="outsider",
            interface_id=INTERFACE_ID,
            interface_version=1,
            argv=[],
            stdin_requested=False,
        )
    assert caught.value.code == "dispatcher.namespace_surface_excludes_interface"


def test_namespace_interface_access_can_narrow_route_access(tmp_path: Path) -> None:
    configuration = _repository(
        tmp_path,
        terminal_access=_access("root.alpha"),
        root_gate=_access("outsider"),
        alpha_gate=_access("root"),
    )
    root_path = configuration.module_roots[0] / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text())
    root["namespace_exports"]["alpha"]["interface_access"] = {
        INTERFACE_ID: _access("root.beta")
    }
    _write_yaml(root_path, root)

    with pytest.raises(UnauthorizedCallerError):
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="outsider",
            interface_id=INTERFACE_ID,
            interface_version=3,
            argv=[],
            stdin_requested=False,
        )


def test_malformed_namespace_interface_access_fails_closed(tmp_path: Path) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))
    root_path = configuration.module_roots[0] / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text())
    root["namespace_exports"]["alpha"]["interface_access"] = [INTERFACE_ID]
    _write_yaml(root_path, root)

    with pytest.raises(DirectBlueprintError) as caught:
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="outsider",
            interface_id=INTERFACE_ID,
            interface_version=3,
            argv=[],
            stdin_requested=False,
        )
    assert caught.value.code == "dispatcher.access_invalid"


def test_source_interface_version_mismatch_is_rejected(tmp_path: Path) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))
    with pytest.raises(DirectBlueprintError) as caught:
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="root.alpha.leaf",
            interface_id=INTERFACE_ID,
            interface_version=4,
            argv=[],
            stdin_requested=False,
        )
    assert caught.value.code == "dispatcher.interface_version_mismatch"


@pytest.mark.parametrize("invalid_version", [0, -1])
def test_source_version_must_be_positive(
    tmp_path: Path,
    invalid_version: int,
) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))
    source_path = (
        configuration.module_roots[0]
        / "root"
        / "alpha"
        / "leaf"
        / "blueprints"
        / "runtime.yaml"
    )
    source = yaml.safe_load(source_path.read_text())
    source["version"] = invalid_version
    _write_yaml(source_path, source)

    with pytest.raises(DirectBlueprintError) as caught:
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="root.alpha.leaf",
            interface_id=INTERFACE_ID,
            interface_version=None,
            argv=[],
            stdin_requested=False,
        )
    assert caught.value.code == "dispatcher.blueprint_malformed"


@pytest.mark.parametrize("invalid_version", [0, -1])
def test_source_interface_version_must_be_positive(
    tmp_path: Path,
    invalid_version: int,
) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))
    source_path = (
        configuration.module_roots[0]
        / "root"
        / "alpha"
        / "leaf"
        / "blueprints"
        / "runtime.yaml"
    )
    source = yaml.safe_load(source_path.read_text())
    source["interfaces"][SOURCE_INTERFACE_ID]["version"] = invalid_version
    _write_yaml(source_path, source)

    with pytest.raises(DirectBlueprintError) as caught:
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="root.alpha.leaf",
            interface_id=INTERFACE_ID,
            interface_version=None,
            argv=[],
            stdin_requested=False,
        )
    assert caught.value.code == "dispatcher.source_interface_invalid"


def test_requested_interface_version_rejects_boolean_integer_alias(
    tmp_path: Path,
) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))
    source_path = (
        configuration.module_roots[0]
        / "root"
        / "alpha"
        / "leaf"
        / "blueprints"
        / "runtime.yaml"
    )
    source = yaml.safe_load(source_path.read_text())
    source["interfaces"][SOURCE_INTERFACE_ID]["version"] = 1
    _write_yaml(source_path, source)

    with pytest.raises(DirectBlueprintError) as caught:
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="root.alpha.leaf",
            interface_id=INTERFACE_ID,
            interface_version=True,
            argv=[],
            stdin_requested=False,
        )
    assert caught.value.code == "dispatcher.interface_version_mismatch"


def test_certification_status_is_warning_only(tmp_path: Path) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))
    invocation = resolve_direct_invocation(
        configuration=configuration,
        caller_module_id="root.beta",
        interface_id=INTERFACE_ID,
        interface_version=3,
        argv=[],
        stdin_requested=False,
        certification_status={SOURCE_ID: "expired"},
    )
    assert any(item.code == "certification-expired" for item in invocation.diagnostics)


def test_host_metadata_uses_explicit_config_without_legacy_routing(
    tmp_path: Path,
) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))

    resolved = _resolve_host_dispatch_metadata(
        caller_skill="root",
        target=INTERFACE_ID,
        args=[],
        target_version=None,
        repository_config=configuration.config_path,
    )

    assert resolved.schema_version == 6
    assert resolved.python_target is not None


def test_public_metadata_api_accepts_exact_repository_config(tmp_path: Path) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))

    resolved = resolve_dispatch_metadata(
        caller_skill="root",
        target=INTERFACE_ID,
        args=[],
        repository_config=configuration.config_path,
    )

    assert resolved.target == INTERFACE_ID


def test_host_executes_direct_route_with_explicit_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))

    completed = _dispatch_host(
        caller_skill="root",
        target=INTERFACE_ID,
        args=[],
        repository_config=configuration.config_path,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == "direct-ok\n"
    assert completed.stderr == ""


def test_host_execution_cannot_import_sibling_from_ambient_pythonpath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))
    modules = configuration.module_roots[0]
    sibling = modules / "root" / "beta"
    (sibling / "secret.py").write_text("message = 'leaked-sibling'\n")
    gateway = modules / "root" / "alpha" / "leaf" / "runtime.py"
    gateway.write_text(
        gateway.read_text().replace("from .helper import message", "from secret import message")
    )
    source_root = Path(__file__).resolve().parents[1] / "src"
    monkeypatch.setenv("PYTHONPATH", f"{sibling}:{source_root}")

    completed = _dispatch_host(
        caller_skill="root",
        target=INTERFACE_ID,
        args=[],
        repository_config=configuration.config_path,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "leaked-sibling" not in completed.stdout


def test_host_rejects_private_child_caller_identity(tmp_path: Path) -> None:
    configuration = _repository(tmp_path, terminal_access=_access(public=True))

    with pytest.raises(DirectBlueprintError) as caught:
        _resolve_host_dispatch_metadata(
            caller_skill="root.alpha.leaf",
            target=INTERFACE_ID,
            args=[],
            repository_config=configuration.config_path,
        )

    assert caught.value.code == "dispatcher.host_caller_invalid"
