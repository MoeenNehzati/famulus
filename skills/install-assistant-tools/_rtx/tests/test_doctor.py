from __future__ import annotations

import json
from pathlib import Path

import pytest

from .. import _scripts_doctor as scripts_doctor


def test_doctor_requires_explicit_mode() -> None:
    with pytest.raises(SystemExit):
        scripts_doctor.parse_args([])


def test_doctor_json_uses_schema_report_without_mutating(tmp_path, monkeypatch, capsys) -> None:
    before = set(tmp_path.rglob("*"))
    monkeypatch.setattr(
        scripts_doctor,
        "diagnose_installation",
        lambda **kw: scripts_doctor.DiagnosticReport.healthy_for(kw["context"]),
    )

    status = scripts_doctor.main(
        ["--mode", "standard", "--home", str(tmp_path), "--json"],
        environ={},
    )

    assert status == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1
    assert set(tmp_path.rglob("*")) == before


def test_development_doctor_requires_absolute_checkout() -> None:
    with pytest.raises(SystemExit):
        scripts_doctor.main(["--mode", "development", "--checkout", "relative"])
