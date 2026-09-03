from __future__ import annotations

from pathlib import Path
from unittest import mock

import yaml

from .. import _setup_runner as setup_runner

ROOT = Path(__file__).resolve().parents[4]


def test_setup_entrypoint_delegates_to_managed_control(monkeypatch):
    delegated = mock.Mock(return_value=7)
    monkeypatch.setattr(setup_runner, "run_managed_control", delegated)

    assert setup_runner.main(["--canonical-python", "/opt/python", "--plugin-root", "/opt/plugin"]) == 7
    delegated.assert_called_once_with("setup", python=Path("/opt/python"), plugin_root=Path("/opt/plugin"))


def test_gateway_composes_exact_task2_owner_and_selected_values():
    gateway = yaml.safe_load((ROOT / "skills/recurring-tasks/blueprints/gateway.yaml").read_text())
    setup = yaml.safe_load((ROOT / "skills/recurring-tasks/_rtx/blueprints/rtx-setup-runner.yaml").read_text())
    text = (ROOT / "skills/recurring-tasks/SKILL.md").read_text()

    assert {item["source"] for item in gateway["dependencies"]} == {
        "setup-dispatcher-runtime.source.gateway"
    }
    assert '["PyYAML"]' in text and "byte-equal" in text
    pattern = next(iter(setup["interfaces"].values()))["process_binding"]["patterns"][0]
    assert pattern["required_flags"] == ["--canonical-python", "--plugin-root"]
