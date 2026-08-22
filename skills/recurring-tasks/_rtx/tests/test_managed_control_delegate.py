from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from .. import _managed_control
from .. import _job_control, _unit_writer


def test_control_delegate_bootstraps_canonical_descriptor_before_first_setup(monkeypatch, tmp_path):
    schedule = mock.Mock(
        runtime_resolver=tmp_path / "runtime" / "bootstrap" / "resolvers" / "v1" / "launch.py",
        bootstrap_python=None,
        descriptor_path=tmp_path / "config with spaces" / "schedule-descriptor.json",
    )
    runtime_root = tmp_path / "runtime"
    writer = mock.Mock(return_value=schedule)
    monkeypatch.setattr(_managed_control, "discover_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(_managed_control, "write_managed_schedule", writer)

    assert _managed_control.command("setup") == [
        str(schedule.runtime_resolver),
        "-m",
        "officina.recurring.control",
        "--descriptor",
        str(schedule.descriptor_path),
        "setup",
    ]
    writer.assert_called_once_with(runtime_root=runtime_root, environ=mock.ANY)


def test_control_delegate_validates_existing_descriptor_for_later_operation(monkeypatch, tmp_path):
    schedule = mock.Mock(
        runtime_resolver=tmp_path / "runtime" / "bootstrap" / "resolvers" / "v1" / "launch.py",
        bootstrap_python=None,
        descriptor_path=tmp_path / "config" / "schedule-descriptor.json",
    )
    monkeypatch.setattr(_managed_control, "discover_runtime_root", lambda: tmp_path / "runtime")
    loader = mock.Mock(return_value=schedule)
    monkeypatch.setattr(_managed_control, "load_public_schedule", loader)

    assert _managed_control.command("test", ["demo"])[-2:] == ["test", "demo"]
    loader.assert_called_once_with(runtime_root=tmp_path / "runtime", environ=mock.ANY)


def test_remove_context_rebuilds_authority_descriptor_before_teardown(monkeypatch, tmp_path):
    """Doctor recovery must remain executable when the recorded descriptor is
    absent or malformed; active pointer authority safely reconstructs it first.
    """
    schedule = mock.Mock(
        runtime_resolver=tmp_path / "runtime" / "bootstrap" / "resolvers" / "v1" / "launch.py",
        bootstrap_python=None,
        descriptor_path=tmp_path / "config" / "schedule-descriptor.json",
    )
    runtime_root = tmp_path / "runtime"
    writer = mock.Mock(return_value=schedule)
    loader = mock.Mock(side_effect=AssertionError("remove-context must not trust stale descriptor"))
    monkeypatch.setattr(_managed_control, "discover_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(_managed_control, "write_managed_schedule", writer)
    monkeypatch.setattr(_managed_control, "load_public_schedule", loader)

    assert _managed_control.command("remove-context")[-1] == "remove-context"
    writer.assert_called_once_with(runtime_root=runtime_root, environ=mock.ANY)
    loader.assert_not_called()


def test_windows_control_delegate_uses_validated_bootstrap_interpreter(monkeypatch, tmp_path):
    schedule = mock.Mock(
        runtime_resolver=tmp_path / "runtime" / "resolver.py",
        bootstrap_python=tmp_path / "Python 雪" / "python.exe",
        descriptor_path=tmp_path / "config" / "schedule-descriptor.json",
    )
    monkeypatch.setattr(_managed_control, "discover_runtime_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(_managed_control, "load_public_schedule", lambda **kwargs: schedule)

    command = _managed_control.command("status")

    assert command[:2] == [str(schedule.bootstrap_python), str(schedule.runtime_resolver)]
    assert "officina.recurring.control" in command


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
