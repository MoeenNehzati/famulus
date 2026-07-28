from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from officina.install.managed_runtime import (
    ManagedRuntimeError,
    build_candidate_release,
    declared_python_packages,
)

REAL_MANIFEST = Path(__file__).resolve().parents[1] / "references" / "blueprint" / "runtime_dependencies.json"
UV_BIN = shutil.which("uv")


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_declared_python_packages_matches_today_baseline():
    packages = declared_python_packages(REAL_MANIFEST, platform="linux")
    assert packages == (
        "bibtexparser",
        "cryptography>=44.0.1",
        "dateparser",
        "jsonschema>=4",
        "keyring",
        "marker-pdf",
        "PyYAML>=6",
        "rich",
    )


def test_declared_python_packages_filters_by_platform(tmp_path):
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "skills": {
            "example": {
                "interfaces": {
                    "run": {
                        "dependencies": [
                            {"kind": "python-package", "name": "pywin32", "version": "1.0", "platforms": {"windows": True}},
                            {"kind": "python-package", "name": "pyyaml", "version": "6.0", "platforms": {"linux": True, "macos": True, "windows": True}},
                        ]
                    }
                }
            }
        },
    }))
    assert declared_python_packages(manifest, platform="linux") == ("pyyaml==6.0",)


def test_declared_python_packages_rejects_unsupported_schema_version(tmp_path):
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text(json.dumps({"version": 2, "skills": {}}))
    with pytest.raises(ManagedRuntimeError):
        declared_python_packages(manifest, platform="linux")


def _fake_run_creating_venv_python(calls: list, *, trusted_python_dir: Path) -> callable:
    """A fake ``subprocess.run`` that mimics real ``uv`` behavior: ``uv venv``
    creates the interpreter on disk as a side effect, ``uv pip install`` does
    not (it requires the interpreter to already exist at ``--python``), and
    ``uv python dir`` reports a trusted interpreter-store directory. This is
    what caught the original bug, where the fake only created ``python_bin``
    during the (mocked) pip-install call — real ``uv pip install`` never does
    that, so the real happy path always failed.
    """

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "venv":
            venv_dir = Path(cmd[-1])
            (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
            (venv_dir / "bin" / "python").write_text("#!/bin/sh\n")
        elif cmd[1:3] == ["python", "dir"]:
            return FakeCompletedProcess(stdout=str(trusted_python_dir) + "\n")
        return FakeCompletedProcess()

    return fake_run


def test_build_candidate_release_creates_venv_then_one_batch_pip_install(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run", _fake_run_creating_venv_python(calls, trusted_python_dir=tmp_path / "uv-python-store")
    )

    pointer = build_candidate_release(
        runtime_root=tmp_path / "runtime",
        manifest_path=REAL_MANIFEST,
        platform="linux",
        uv_bin=Path("/fake/uv"),
        python_version="3.11",
    )

    assert len(calls) == 3  # uv venv, one batch pip-install call (not per-package), uv python dir
    venv_call, pip_call, python_dir_call = calls
    assert venv_call == ["/fake/uv", "venv", "--python", "3.11", str(pointer.runtime_source / "venv")]
    assert pip_call[:4] == ["/fake/uv", "pip", "install", "--python"]
    assert pip_call[4] == str(pointer.python_bin)
    assert python_dir_call == ["/fake/uv", "python", "dir"]
    assert pointer.python_bin.exists()


def test_build_candidate_release_provisions_venv_before_installing_packages(monkeypatch, tmp_path):
    """Dedicated sanity check for the uv-venv step's exact arguments and
    ordering, independent of the batch-install call-count assertion above."""
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run", _fake_run_creating_venv_python(calls, trusted_python_dir=tmp_path / "uv-python-store")
    )

    build_candidate_release(
        runtime_root=tmp_path / "runtime",
        manifest_path=REAL_MANIFEST,
        platform="linux",
        uv_bin=Path("/fake/uv"),
        python_version="3.11",
    )

    assert calls[0][:4] == ["/fake/uv", "venv", "--python", "3.11"]
    assert calls[1][0] == "/fake/uv"


def test_build_candidate_release_failure_writes_no_pointer(monkeypatch, tmp_path):
    def fail(*a, **k):
        raise ManagedRuntimeError("simulated failure")

    monkeypatch.setattr("officina.install.managed_runtime._run_dependency_install", fail)
    monkeypatch.setattr(
        "officina.install.managed_runtime._create_release_venv",
        lambda **kwargs: None,
    )
    runtime_root = tmp_path / "runtime"
    with pytest.raises(ManagedRuntimeError):
        build_candidate_release(
            runtime_root=runtime_root,
            manifest_path=REAL_MANIFEST,
            platform="linux",
            uv_bin=Path("/fake/uv"),
            python_version="3.11",
        )
    assert not (runtime_root / "current.json").exists()


def test_build_candidate_release_venv_failure_writes_no_pointer(monkeypatch, tmp_path):
    def fail(**kwargs):
        raise ManagedRuntimeError("simulated venv creation failure")

    monkeypatch.setattr("officina.install.managed_runtime._create_release_venv", fail)
    runtime_root = tmp_path / "runtime"
    with pytest.raises(ManagedRuntimeError):
        build_candidate_release(
            runtime_root=runtime_root,
            manifest_path=REAL_MANIFEST,
            platform="linux",
            uv_bin=Path("/fake/uv"),
            python_version="3.11",
        )
    assert not (runtime_root / "current.json").exists()


# famulus-skip: category=capability-unavailable; reason=requires a real uv binary on PATH; alternate=mocked tests above cover call shapes and ordering without uv installed
@pytest.mark.skipif(UV_BIN is None, reason="uv is not installed on this machine")
def test_build_candidate_release_end_to_end_with_real_uv(tmp_path):
    """Integration test against the real uv binary (no mocking): proves the
    venv-creation + batch-install + activation flow actually works, not just
    that mocked call shapes look right."""
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "skills": {
            "example": {
                "interfaces": {
                    "run": {
                        "dependencies": [
                            {
                                "kind": "python-package",
                                "name": "rich",
                                "version": "any",
                                "platforms": {"linux": True, "macos": True, "windows": True},
                            },
                        ]
                    }
                }
            }
        },
    }))
    runtime_root = tmp_path / "runtime"

    pointer = build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest,
        platform="linux",
        uv_bin=Path(UV_BIN),
        python_version="3.11",
    )

    assert pointer.python_bin.exists()
    assert (runtime_root / "current.json").exists()
    site_packages_has_rich = any(
        p.name.startswith("rich") for p in (pointer.runtime_source / "venv").rglob("rich*")
    )
    assert site_packages_has_rich
