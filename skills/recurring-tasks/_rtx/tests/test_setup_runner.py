from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
import yaml

from officina.runtime.python_machine_interface import set_runtime_dispatch_context

from .. import _setup_runner as setup_runner

ROOT = Path(__file__).resolve().parents[4]


def test_setup_interface_uses_dispatcher_python_and_plugin_root(monkeypatch):
    delegated = mock.Mock(return_value=7)
    monkeypatch.setattr(setup_runner, "run_managed_control", delegated)
    monkeypatch.setattr(setup_runner.sys, "executable", "/opt/famulus/venv/bin/python")
    interface = setup_runner.Interface()
    set_runtime_dispatch_context(interface, repo_root=Path("/opt/plugin"))

    assert interface.run([]) == 7
    delegated.assert_called_once_with(
        "setup",
        python=Path("/opt/famulus/venv/bin/python"),
        plugin_root=Path("/opt/plugin"),
    )


def test_setup_interface_fails_closed_without_dispatcher_plugin_root(monkeypatch):
    delegated = mock.Mock()
    monkeypatch.setattr(setup_runner, "run_managed_control", delegated)

    with pytest.raises(RuntimeError, match="dispatcher plugin root"):
        setup_runner.Interface().run([])

    delegated.assert_not_called()


def test_setup_interface_rejects_caller_path_arguments(monkeypatch):
    delegated = mock.Mock()
    monkeypatch.setattr(setup_runner, "run_managed_control", delegated)
    interface = setup_runner.Interface()
    set_runtime_dispatch_context(interface, repo_root=Path("/opt/plugin"))

    with pytest.raises(SystemExit):
        interface.run(["--canonical-python", "/opt/python"])

    delegated.assert_not_called()


def test_gateway_declares_no_bootstrap_dependency():
    gateway = yaml.safe_load((ROOT / "skills/recurring-tasks/blueprints/gateway.yaml").read_text())
    setup = yaml.safe_load((ROOT / "skills/recurring-tasks/_rtx/blueprints/rtx-setup-runner.yaml").read_text())
    text = (ROOT / "skills/recurring-tasks/SKILL.md").read_text()

    assert gateway["dependencies"] == []
    authored = text.split("<!-- END BLUEPRINT INTERFACES -->", 1)[1]
    assert "bootstrap-dispatcher-runtime" not in authored
    pattern = next(iter(setup["interfaces"].values()))["process_binding"]["patterns"][0]
    assert setup["version"] == 2
    setup_interface = next(iter(setup["interfaces"].values()))
    assert setup_interface["version"] == 2
    assert setup_interface["contract"]["arguments"] == {}
    pattern = setup_interface["process_binding"]["patterns"][0]
    assert pattern["allowed_flags"] == []
