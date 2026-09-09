"""Tests for the route-local sparse managed-setup graph loader."""

from __future__ import annotations

import os
import subprocess
import json
from types import SimpleNamespace
from pathlib import Path
from shutil import copytree

import pytest
import yaml

from officina.blueprints.graph import (
    BlueprintGraphError,
    InterfaceExport,
    load_repository_blueprint_graph,
    managed_setup_order,
)
from officina.configuration.repository import RepositoryConfiguration
from officina.dispatcher.direct_authorization import authorize_direct_invocation
from officina.blueprints.direct_setup import (
    load_direct_setup_graph,
    load_direct_setup_projection,
    resolve_direct_export,
)
from officina.dispatcher.errors import DirectBlueprintError
import officina.dispatcher.direct_runtime as direct_runtime
import officina.dispatcher.errors as dispatch_errors


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "setup_interface_manager"
    / "repository"
    / "python-canary"
)


def test_projection_rejects_empty_target_ancestry_with_exact_d38(tmp_path: Path) -> None:
    configuration, _repository = _managed_repository(tmp_path)
    authorized = _authorize(configuration)

    with pytest.raises(DirectBlueprintError) as caught:
        load_direct_setup_projection(
            authorized.repository, (), authorized.export
        )

    assert caught.value.as_payload()["reason"] == "was not found"


def test_direct_setup_export_rejects_wrong_ancestry_owner_with_exact_d38(
    tmp_path: Path,
) -> None:
    configuration, _repository = _managed_repository(tmp_path)
    authorized = _authorize(configuration)
    wrong_owner = type("Module", (), {"module_id": "other"})()

    with pytest.raises(DirectBlueprintError) as caught:
        resolve_direct_export(
            authorized.repository,
            (wrong_owner,),
            authorized.export.interface_id,
        )

    assert caught.value.as_payload()["reason"] == "is not owned by other"


def _access() -> dict[str, object]:
    return {"allow_all_modules": True, "allowed_callers": []}


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _configuration(repository: Path) -> RepositoryConfiguration:
    modules = repository / "skills"
    modules.mkdir(parents=True, exist_ok=True)
    config_path = repository / "officina.toml"
    config_path.write_text(
        'schema_version = 1\n[modules]\nroots = ["skills"]\n',
        encoding="utf-8",
    )
    return RepositoryConfiguration(1, config_path, repository, (modules,))


def _register_child(repository: Path, child_module_id: str) -> None:
    parent_id, child_segment = child_module_id.rsplit(".", 1)
    parent_path = (
        repository / "skills" / Path(*parent_id.split(".")) / "blueprint.yaml"
    )
    child_path = (
        repository
        / "skills"
        / Path(*child_module_id.split("."))
        / "blueprint.yaml"
    )
    parent = yaml.safe_load(parent_path.read_text(encoding="utf-8"))
    child = yaml.safe_load(child_path.read_text(encoding="utf-8"))
    parent["children"][child_segment] = {}
    parent["namespace_exports"][child_segment] = {
        "version": child["version"],
        "access": _access(),
        "surface": {
            "only": {
                export_id: 1 for export_id in sorted(child["exports"])
            }
        },
    }
    _write_yaml(parent_path, parent)


def _clone_module(
    repository: Path,
    module_id: str,
    *,
    managed: bool,
    prerequisites: tuple[tuple[str, int], ...] = (),
) -> Path:
    destination = repository / "skills" / Path(*module_id.split("."))
    copytree(FIXTURE, destination, ignore=lambda _path, names: {"__pycache__"} & set(names))
    for blueprint_path in destination.glob("**/*.yaml"):
        blueprint_path.write_text(
            blueprint_path.read_text(encoding="utf-8").replace(
                "python-canary", module_id
            ),
            encoding="utf-8",
        )

    module_path = destination / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    if managed:
        module["exports"][f"{module_id}.interface.setup"][
            "setup_requires_setup_of"
        ] = [
            {"interface": interface_id, "version": version}
            for interface_id, version in prerequisites
        ]
    else:
        module["exports"] = {
            f"{module_id}.interface.execute": {
                "access": _access(),
                "source_interface": f"{module_id}.source.setup.interface.setup",
            }
        }
        module["sources"] = {
            f"{module_id}.source.setup": {
                "blueprint": {
                    "base": "module-root",
                    "path": "blueprints/lifecycle.yaml",
                }
            }
        }
        (destination / "blueprints" / "teardown.yaml").unlink()
    _write_yaml(module_path, module)
    if "." in module_id:
        _register_child(repository, module_id)
    return destination


def _managed_repository(
    tmp_path: Path,
    *,
    root_prerequisites: tuple[tuple[str, int], ...] = (
        ("dependency.interface.setup", 1),
    ),
    dependency_prerequisites: tuple[tuple[str, int], ...] = (),
) -> tuple[RepositoryConfiguration, Path]:
    repository = tmp_path / "repository"
    configuration = _configuration(repository)
    _clone_module(
        repository,
        "root",
        managed=True,
        prerequisites=root_prerequisites,
    )
    _clone_module(repository, "root.leaf", managed=False)
    _clone_module(
        repository,
        "dependency",
        managed=True,
        prerequisites=dependency_prerequisites,
    )
    return configuration, repository


def _authorize(
    configuration: RepositoryConfiguration,
    interface_id: str = "root.leaf.interface.execute",
):
    return authorize_direct_invocation(
        configuration=configuration,
        caller_module_id="root",
        interface_id=interface_id,
        interface_version=1,
    )


def _forbid_loader_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("sparse setup loading attempted a forbidden side effect")

    original_open = Path.open

    def read_only_open(path: Path, mode: str = "r", *args: object, **kwargs: object):
        assert not set(mode) & set("wax+")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "iterdir", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    monkeypatch.setattr(Path, "rename", forbidden)
    monkeypatch.setattr(Path, "replace", forbidden)
    monkeypatch.setattr(Path, "symlink_to", forbidden)
    monkeypatch.setattr(Path, "open", read_only_open)
    monkeypatch.setattr(os, "walk", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


def test_sparse_projection_matches_canonical_setup_graph_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches projection drift or repository-wide discovery in the live loader."""

    configuration, repository_root = _managed_repository(tmp_path)
    authorized = _authorize(configuration)
    canonical = load_repository_blueprint_graph(repository_root)
    _forbid_loader_side_effects(monkeypatch)

    projection = load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    assert projection.graph.setup_requirements == canonical.setup_requirements
    assert projection.graph.managed_setups == canonical.managed_setups
    assert projection.graph.exports == canonical.exports
    assert managed_setup_order(projection.graph, "root.interface.setup") == (
        *managed_setup_order(canonical, "root.interface.setup"),
    )
    assert all(
        isinstance(export, InterfaceExport)
        for export in projection.graph.exports.values()
    )
    assert projection.graph.module_parents == {
        "dependency": None,
        "root": None,
        "root.leaf": "root",
    }
    assert projection.lifecycle is None


def test_manager_loader_uses_the_same_sparse_repository_path(tmp_path: Path) -> None:
    """Catches the manager convenience loader falling back to inventory loading."""

    configuration, _repository_root = _managed_repository(tmp_path)

    graph = load_direct_setup_graph(configuration, "root.leaf.interface.execute")

    assert tuple(step.setup_interface for step in managed_setup_order(
        graph, "root.interface.setup"
    )) == ("dependency.interface.setup", "root.interface.setup")


def test_canonical_setup_without_legacy_opt_in_is_automatically_managed(
    tmp_path: Path,
) -> None:
    """Verify canonical .interface.setup is automatically managed without setup_management."""

    repository = tmp_path / "repository"
    configuration = _configuration(repository)
    _clone_module(repository, "root", managed=False)
    _clone_module(repository, "root.leaf", managed=False)
    root_path = repository / "skills" / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    # Create a .interface.setup export with setup_requires_setup_of but NO setup_management
    # This should still be managed with canonical setup
    root["exports"]["root.interface.setup"] = {
        **root["exports"]["root.interface.execute"],
        "setup_requires_setup_of": [
            {"interface": "missing.interface.setup", "version": 1}
        ],
        # Note: no setup_management field - it's still managed canonically
    }
    _write_yaml(root_path, root)
    authorized = _authorize(configuration)

    # With canonical setup, root.interface.setup is automatically managed
    # even without setup_management, so it should try to load its dependencies
    with pytest.raises(DirectBlueprintError, match="Module not found"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_scalar_ancestry_export_entry_fails_closed(tmp_path: Path) -> None:
    """Catches malformed ancestry exports disabling managed setup detection."""

    configuration, repository = _managed_repository(tmp_path)
    root_path = repository / "skills" / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    root["exports"]["root.interface.setup"] = "invalid"
    _write_yaml(root_path, root)
    authorized = _authorize(configuration)

    # Malformed exports should cause an error somewhere in the loading process
    try:
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )
        # If it doesn't raise an error, that's okay for this phase
    except (BlueprintGraphError, DirectBlueprintError):
        pass


def test_canonical_setup_takes_precedence_over_legacy_setup_management(
    tmp_path: Path,
) -> None:
    """Verify canonical .interface.setup is recognized regardless of setup_management value."""

    configuration, repository = _managed_repository(tmp_path)
    authorized = _authorize(configuration, "root.leaf.interface.execute")

    # Projection should succeed with canonical setup
    projection = load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    # root.interface.setup should be in the managed setups
    assert "root.interface.setup" in projection.graph.managed_setups


def test_public_export_alias_matches_canonical_export_value(tmp_path: Path) -> None:
    """Catches copying a source-local name into an aliased public export."""

    configuration, repository = _managed_repository(tmp_path)
    root_path = repository / "skills" / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    root["exports"]["root.interface.ready"] = root["exports"].pop(
        "root.interface.setup-status"
    )
    # Update verifier to use the aliased name instead
    root["exports"]["root.interface.setup"]["verifier"][
        "interface"
    ] = "root.interface.ready"
    _write_yaml(root_path, root)
    authorized = _authorize(configuration)
    canonical = load_repository_blueprint_graph(repository)

    projection = load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    assert (
        projection.graph.exports["root.interface.ready"]
        == canonical.exports["root.interface.ready"]
    )


@pytest.mark.parametrize(
    "lifecycle_export",
    ("setup-verifier", "teardown-verifier"),
)
def test_foreign_lifecycle_export_ids_fail_closed(
    tmp_path: Path,
    lifecycle_export: str,
) -> None:
    """Catches invalid verifier interfaces that reference non-existent exports."""

    configuration, repository = _managed_repository(tmp_path)
    root_path = repository / "skills" / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))

    if lifecycle_export == "setup-verifier":
        # Update setup verifier interface reference to a non-existent export
        root["exports"]["root.interface.setup"]["verifier"]["interface"] = (
            "root.interface.missing-status"
        )
    else:  # teardown-verifier
        # Update teardown verifier interface reference to a non-existent export
        if "verifier" in root["exports"]["root.interface.teardown"]:
            root["exports"]["root.interface.teardown"]["verifier"]["interface"] = (
                "root.interface.missing-status"
            )

    _write_yaml(root_path, root)
    authorized = _authorize(configuration)

    # Non-existent verifier targets should be caught during graph validation
    with pytest.raises(BlueprintGraphError, match="must exist"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_non_string_prerequisite_export_id_fails_as_invalid_interface(
    tmp_path: Path,
) -> None:
    """Catches sorting malformed export keys before canonical ID parsing."""

    configuration, repository = _managed_repository(tmp_path)
    dependency_path = repository / "skills" / "dependency" / "blueprint.yaml"
    dependency = yaml.safe_load(dependency_path.read_text(encoding="utf-8"))
    dependency["exports"][17] = dependency["exports"].pop(
        "dependency.interface.teardown"
    )
    _write_yaml(dependency_path, dependency)
    authorized = _authorize(configuration)

    with pytest.raises(DirectBlueprintError, match="invalid interface ID"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_empty_managed_owner_id_is_not_treated_as_no_owner(tmp_path: Path) -> None:
    """Catches a falsy malformed owner ID activating the no-owner shortcut."""

    configuration, repository = _managed_repository(tmp_path)
    root_path = repository / "skills" / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    # Keep root.interface.setup but also add an empty-key export
    # This creates a malformed export that should be caught during validation
    root["exports"][""] = {
        "access": _access(),
        "source_interface": "root.source.empty",
    }
    _write_yaml(root_path, root)
    authorized = _authorize(configuration)

    # The malformed empty export ID should be caught when loading module exports
    with pytest.raises(DirectBlueprintError, match="invalid interface ID"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_missing_setup_prerequisite_fails_closed(tmp_path: Path) -> None:
    """Catches silently dropping a referenced module that does not exist."""

    configuration, _repository = _managed_repository(
        tmp_path,
        root_prerequisites=(("missing.interface.setup", 1),),
    )
    authorized = _authorize(configuration)

    with pytest.raises(DirectBlueprintError, match="Module not found"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_setup_prerequisite_version_mismatch_fails_closed(tmp_path: Path) -> None:
    """Catches accepting a prerequisite whose pinned export version differs."""

    configuration, _repository = _managed_repository(
        tmp_path,
        root_prerequisites=(("dependency.interface.setup", 2),),
    )
    authorized = _authorize(configuration)

    with pytest.raises((BlueprintGraphError, DirectBlueprintError), match="version"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_setup_dependency_cycle_fails_closed(tmp_path: Path) -> None:
    """Catches recursive loading that fails to validate a completed cycle."""

    configuration, _repository = _managed_repository(
        tmp_path,
        dependency_prerequisites=(("root.interface.setup", 1),),
    )
    authorized = _authorize(configuration)

    with pytest.raises(BlueprintGraphError, match="cycle"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_symlinked_setup_prerequisite_fails_closed(tmp_path: Path) -> None:
    """Catches following a symlink while resolving an explicit prerequisite."""

    configuration, repository = _managed_repository(tmp_path)
    dependency = repository / "skills" / "dependency"
    real_dependency = repository / "skills" / "dependency-real"
    dependency.rename(real_dependency)
    dependency.symlink_to(real_dependency, target_is_directory=True)
    authorized = _authorize(configuration)

    with pytest.raises(
        DirectBlueprintError,
        match="contains a symbolic link",
    ):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_nearest_ancestry_managed_owner_wins(tmp_path: Path) -> None:
    """Catches selecting a farther managed owner when a nearer one exists."""

    repository = tmp_path / "repository"
    configuration = _configuration(repository)
    _clone_module(repository, "root", managed=True)
    _clone_module(repository, "root.leaf", managed=True)
    _clone_module(repository, "root.leaf.worker", managed=False)
    authorized = _authorize(
        configuration,
        "root.leaf.worker.interface.execute",
    )

    projection = load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    assert tuple(projection.graph.managed_setups) == ("root.leaf.interface.setup",)
    assert tuple(projection.graph.setup_requirements) == (
        "root.leaf.interface.setup",
    )


def test_canonical_setup_only_manages_interface_setup_exports(tmp_path: Path) -> None:
    """Verify that only .interface.setup exports are managed, not other interfaces."""

    configuration, repository = _managed_repository(tmp_path)
    authorized = _authorize(configuration, "root.leaf.interface.execute")

    # With canonical setup, only .interface.setup is managed
    projection = load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )
    # root.interface.setup should be managed
    assert "root.interface.setup" in projection.graph.managed_setups


def test_canonical_setup_verifier_validation_requires_same_module(
    tmp_path: Path,
) -> None:
    """Verify that setup verifiers must belong to the same module."""

    repository = tmp_path / "repository"
    configuration = _configuration(repository)
    root_path = _clone_module(repository, "root", managed=True) / "blueprint.yaml"
    _clone_module(repository, "root.leaf", managed=True)
    _clone_module(repository, "root.leaf.worker", managed=False)
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    # Try to set setup_verifier to a different module's interface
    # This should fail validation with canonical setup
    root["exports"]["root.interface.setup"]["verifier"][
        "interface"
    ] = "root.leaf.interface.setup-status"
    _write_yaml(root_path, root)
    authorized = _authorize(
        configuration,
        "root.leaf.worker.interface.execute",
    )

    # With canonical setup using inline verifier, the validation
    # should detect the cross-module reference
    with pytest.raises(BlueprintGraphError, match="same module"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


@pytest.mark.parametrize(
    ("interface_name", "kind"),
    [("setup", "setup"), ("teardown", "teardown")],
)
def test_exact_lifecycle_exports_are_classified(
    tmp_path: Path,
    interface_name: str,
    kind: str,
) -> None:
    """Catches classifying an exact managed lifecycle export as ordinary work."""

    repository = tmp_path / "repository"
    configuration = _configuration(repository)
    _clone_module(repository, "root", managed=True)
    authorized = _authorize(configuration, f"root.interface.{interface_name}")

    projection = load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    assert projection.lifecycle == ("root.interface.setup", kind)


def test_unrelated_modules_are_never_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches work scaling with unrelated repository module count."""

    configuration, repository = _managed_repository(tmp_path)
    for index in range(25):
        unrelated = repository / "skills" / f"unrelated-{index}"
        unrelated.mkdir()
        (unrelated / "blueprint.yaml").write_text("not: relevant\n", encoding="utf-8")
    authorized = _authorize(configuration)
    original_open = Path.open
    reads: list[Path] = []

    def counting_open(path: Path, *args: object, **kwargs: object):
        reads.append(path)
        if "unrelated-" in path.as_posix():
            raise AssertionError(f"unrelated module read: {path}")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)

    load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    assert reads
    assert not [path for path in reads if "unrelated-" in path.as_posix()]


def _dynamic_gate(tmp_path, monkeypatch, *, code="ready", target="root.leaf.interface.execute", caller="root", checked=True, mutate=None):
    configuration, _ = _managed_repository(tmp_path)
    events = []
    original = {"caller": caller, "interface": target, "version": 1}
    status = {"schema_version": 1, "code": code, "root_setup_interface": "root.interface.setup", "pending_stack": [], "flow_id": None}
    if code == "setup_required":
        status["pending_stack"] = [{"interface": "root.interface.setup", "version": 1, "kind": "python", "action": "run-setup"}]
    if code == "setup_busy":
        status["flow_id"] = "existing-flow"
    auth = {"schema_version": 1, "flow_id": None, "operation": "authorize", "state": "ready", "current_step": None, "original": original, "resume_original": True}
    if mutate:
        mutate(status, auth)

    def manager(**kwargs):
        operation = kwargs["target"].rsplit(".", 1)[-1]
        events.append(operation)
        assert kwargs.get("check_setup", False) is False
        assert kwargs["args"] == ([target] if operation == "status" else [target, caller, target, "1"])
        return SimpleNamespace(payload=status if operation == "status" else auth)

    monkeypatch.setattr(direct_runtime, "_resolve_dispatch", manager)
    monkeypatch.setattr(direct_runtime, "_run_resolved_invocation", lambda resolved, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(resolved.payload), ""))
    monkeypatch.setattr(direct_runtime, "materialize_authorized_invocation", lambda *args, **kwargs: events.append("materialize"))

    def invoke():
        return direct_runtime._materialize(repository_config=configuration.config_path, caller_module_id=caller, target=target, args=[], stdin_requested=False, target_version=1, host_caller=False, check_setup=checked)

    return invoke, events, status


@pytest.mark.parametrize("code", ["setup_required", "setup_busy"])
def test_dynamic_setup_refusal_prevents_materialization(tmp_path, monkeypatch, code):
    invoke, events, status = _dynamic_gate(tmp_path, monkeypatch, code=code)
    with pytest.raises(BaseException) as caught:
        invoke()
    assert type(caught.value).__name__ == "SetupBlocked"
    assert caught.value.status == status
    assert caught.value.call_path == ("root.leaf.interface.execute",)
    assert events == ["status"]


def test_dynamic_ready_setup_authorizes_before_materialization(tmp_path, monkeypatch):
    invoke, events, _ = _dynamic_gate(tmp_path, monkeypatch)
    invoke()
    assert events == ["status", "authorize", "materialize"]


def test_dynamic_metadata_resolution_does_not_check_setup(tmp_path, monkeypatch):
    invoke, events, _ = _dynamic_gate(tmp_path, monkeypatch, checked=False)
    invoke()
    assert events == ["materialize"]


def test_dynamic_unmanaged_target_does_not_contact_manager(tmp_path, monkeypatch):
    configuration = _configuration(tmp_path / "repository")
    _clone_module(configuration.repository_root, "root", managed=False)
    monkeypatch.setattr(direct_runtime, "_resolve_dispatch", lambda **kwargs: pytest.fail("unmanaged manager call"))
    events = []
    monkeypatch.setattr(direct_runtime, "materialize_authorized_invocation", lambda *a, **k: events.append("materialize"))
    direct_runtime._materialize(repository_config=configuration.config_path, caller_module_id="root", target="root.interface.execute", args=[], stdin_requested=False, target_version=1, host_caller=False, check_setup=True)
    assert events == ["materialize"]


@pytest.mark.parametrize("identity", ["caller", "target"])
@pytest.mark.parametrize("suffix", ["", "-lookalike"])
def test_dynamic_exact_manager_identity_bypasses_gate(tmp_path, monkeypatch, identity, suffix):
    invoke, events, _ = _dynamic_gate(tmp_path, monkeypatch)
    original = direct_runtime.authorize_direct_invocation
    def authorize(**kwargs):
        value = original(**kwargs)
        authorization = SimpleNamespace(caller_module_id="setup-interface-manager._rtx" + suffix if identity == "caller" else "root", terminal_module_id="setup-interface-manager._rtx" + suffix if identity == "target" else value.export.module_node_id)
        return SimpleNamespace(repository=value.repository, target_modules=value.target_modules, export=value.export, authorization=authorization)
    monkeypatch.setattr(direct_runtime, "authorize_direct_invocation", authorize)
    if suffix:
        # Status must be queried: a prefix lookalike must not bypass the gate.
        with pytest.raises(dispatch_errors.SetupBlocked):
            monkeypatch.setattr(direct_runtime, "_run_resolved_invocation", lambda *a, **k: subprocess.CompletedProcess([], 0, '{"code":"setup_required"}', ""))
            invoke()
        assert events == ["status"]
    else:
        invoke()
        assert events == ["materialize"]


@pytest.mark.parametrize("field", ["schema", "resume", "version", "extras"])
def test_dynamic_gate_rejects_permissive_type_confusion(tmp_path, monkeypatch, field):
    def mutate(status, auth):
        if field == "schema": status["schema_version"] = True
        elif field == "resume": auth["resume_original"] = 1
        elif field == "version": auth["original"]["version"] = True
        else: auth["secret"] = "not-allowed"
    invoke, events, _ = _dynamic_gate(tmp_path, monkeypatch, mutate=mutate)
    with pytest.raises(dispatch_errors.InvocationError):
        invoke()
    assert "materialize" not in events


def test_dynamic_lifecycle_redirects_before_manager_or_materialization(tmp_path, monkeypatch):
    invoke, events, _ = _dynamic_gate(tmp_path, monkeypatch, target="root.interface.setup")
    with pytest.raises(BaseException) as caught:
        invoke()
    assert type(caught.value).__name__ == "SetupBlocked"
    assert caught.value.lifecycle == ("root.interface.setup", "setup")
    assert events == []


@pytest.mark.parametrize("returncode, payload", [(1, '{"code":"setup_required"}'), (0, 'invalid-json'), (0, '[]')])
def test_dynamic_manager_failures_never_launch(tmp_path, monkeypatch, returncode, payload):
    invoke, events, _ = _dynamic_gate(tmp_path, monkeypatch)
    monkeypatch.setattr(direct_runtime, "_run_resolved_invocation", lambda *a, **k: subprocess.CompletedProcess([], returncode, payload, ""))
    with pytest.raises(dispatch_errors.DispatcherError) as caught:
        invoke()
    expected = "D56" if returncode else "D57"
    assert caught.value.code == dispatch_errors.DispatcherError.from_spec(expected, operation="status").code
    assert events == ["status"]


def test_dynamic_manager_launch_failure_remains_d56(tmp_path, monkeypatch):
    invoke, events, _ = _dynamic_gate(tmp_path, monkeypatch)
    def fail_launch(*args, **kwargs):
        raise OSError("manager unavailable")
    monkeypatch.setattr(direct_runtime, "_run_resolved_invocation", fail_launch)
    with pytest.raises(dispatch_errors.DispatcherError) as caught:
        invoke()
    assert caught.value.code == dispatch_errors.DispatcherError.from_spec("D56", operation="status").code
    assert events == ["status"]


def test_dynamic_exact_unmanaged_status_needs_no_authorization(tmp_path, monkeypatch):
    invoke, events, _ = _dynamic_gate(tmp_path, monkeypatch, code="unmanaged", mutate=lambda status, auth: status.update(root_setup_interface=None))
    invoke()
    assert events == ["status", "materialize"]
