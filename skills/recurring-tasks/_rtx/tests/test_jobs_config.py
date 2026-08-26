from __future__ import annotations

import pytest

from .. import _jobs_config


def test_load_write_jobs_persists_valid_roundtrip(tmp_path):
    path = tmp_path / "jobs.yaml"
    path.write_text(
        "jobs:\n"
        "  - name: example\n"
        "    command: 'true'\n"
        "    schedule: '0 * * * *'\n"
        "    enabled: true\n",
        encoding="utf-8",
    )

    jobs = _jobs_config.load_jobs(path)
    assert jobs == [
        {
            "name": "example",
            "command": "true",
            "schedule": "0 * * * *",
            "enabled": True,
        }
    ]

    jobs[0]["enabled"] = False
    _jobs_config.write_jobs(path, jobs)

    assert _jobs_config.load_jobs(path) == [
        {
            "name": "example",
            "command": "true",
            "schedule": "0 * * * *",
            "enabled": False,
        }
    ]


def test_load_jobs_rejects_empty_document_root(tmp_path):
    path = tmp_path / "jobs.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="document root must be an object"):
        _jobs_config.load_jobs(path)
