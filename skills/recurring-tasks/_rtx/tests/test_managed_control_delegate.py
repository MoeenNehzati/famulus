from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from .. import _managed_control
from .. import _job_control, _unit_writer


def test_control_delegate_reconciles_selected_setup_before_publishing_descriptor(monkeypatch, tmp_path):
    schedule = mock.Mock(
        python=tmp_path / "Python 雪" / "python",
        plugin_root=tmp_path / "plugin with spaces",
        descriptor_path=tmp_path / "config with spaces" / "schedule-descriptor.json",
    )
    builder = mock.Mock(return_value=schedule)
    writer = mock.Mock()
    operation = mock.Mock(return_value=0)
    monkeypatch.setattr(_managed_control, "build_managed_schedule", builder)
    monkeypatch.setattr(_managed_control, "write_managed_schedule", writer)
    monkeypatch.setattr(_managed_control, "run_operation", operation)

    assert _managed_control.run(
        "setup", python=schedule.python, plugin_root=schedule.plugin_root
    ) == 0
    builder.assert_called_once_with(python=schedule.python, plugin_root=schedule.plugin_root, environ=mock.ANY)
    operation.assert_called_once_with(
        schedule, operation="setup", name=None, lines=50
    )
    writer.assert_called_once_with(
        python=schedule.python, plugin_root=schedule.plugin_root, environ=mock.ANY
    )


def test_control_delegate_loads_existing_descriptor_for_later_operation(monkeypatch, tmp_path):
    schedule = mock.Mock(
        python=tmp_path / "python",
        plugin_root=tmp_path / "plugin",
        descriptor_path=tmp_path / "config" / "schedule-descriptor.json",
    )
    loader = mock.Mock(return_value=schedule)
    monkeypatch.setattr(_managed_control, "load_public_schedule", loader)

    assert _managed_control.command("test", ["demo"])[-2:] == ["test", "demo"]
    loader.assert_called_once_with(environ=mock.ANY)


def test_public_control_and_sync_expose_only_managed_canonical_routes(monkeypatch):
    calls = []
    monkeypatch.setattr(_job_control, "run_managed_control", lambda operation, arguments=None: calls.append((operation, arguments)) or 0)
    monkeypatch.setattr(_unit_writer, "run_managed_control", lambda operation: calls.append((operation, None)) or 0)

    assert _job_control.main(["enable", "demo"]) == 0
    assert _job_control.main(["disable", "demo"]) == 0
    assert _job_control.main(["remove-context"]) == 0
    assert _unit_writer.main([]) == 0
    assert calls == [
        ("enable", ["demo"]),
        ("disable", ["demo"]),
        ("remove-context", []),
        ("sync", None),
    ]

    with pytest.raises(SystemExit):
        _job_control.main(["enable", "demo", "--jobs-file", "/tmp/other"])
    with pytest.raises(SystemExit):
        _unit_writer.main(["--adopt"])
