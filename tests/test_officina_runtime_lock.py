from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from officina.install.runtime_lock import (
    RuntimeLockError,
    generate_runtime_lock,
    render_runtime_requirements,
    validate_runtime_lock,
)


def _write_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "skills": {
                    "example": {
                        "interfaces": {
                            "first": {
                                "dependencies": [
                                    {
                                        "kind": "python-package",
                                        "name": "ExamplePkg",
                                        "version": ">=1",
                                        "platforms": {
                                            "linux": True,
                                            "macos": True,
                                            "windows": True,
                                        },
                                    },
                                    {
                                        "kind": "python-package",
                                        "name": "linux-only",
                                        "version": "any",
                                        "platforms": {"linux": True},
                                    },
                                    {
                                        "kind": "python-package",
                                        "name": "marker-pdf",
                                        "version": "any",
                                        "platforms": {"linux": True},
                                    },
                                ]
                            },
                            "second": {
                                "dependencies": [
                                    {
                                        "kind": "python-package",
                                        "name": "examplepkg",
                                        "version": "<2",
                                        "platforms": {
                                            "linux": True,
                                            "macos": True,
                                            "windows": True,
                                        },
                                    },
                                    {
                                        "kind": "binary",
                                        "name": "ignored-tool",
                                        "version": "any",
                                        "platforms": {"linux": True},
                                    },
                                ]
                            },
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _lock_text(input_text: str, records: str) -> str:
    digest = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
    return (
        "# famulus-runtime-lock-schema: 1\n"
        f"# input-sha256: {digest}\n"
        "# uv-version: 0.11.29\n"
        "# python-version: 3.11.15\n"
        "#\n"
        f"{records}"
    )


def test_render_runtime_requirements_pools_constraints_and_platforms(tmp_path: Path) -> None:
    manifest = tmp_path / "runtime_dependencies.json"
    _write_manifest(manifest)

    assert render_runtime_requirements(manifest) == (
        "# Generated from references/blueprint/runtime_dependencies.json.\n"
        "# Do not edit by hand.\n"
        "\n"
        "examplepkg<2\n"
        "ExamplePkg>=1\n"
        "linux-only ; sys_platform == 'linux'\n"
        "PyYAML==6.0.2\n"
        "setuptools==80.9.0\n"
    )


def test_validate_runtime_lock_accepts_exact_hashed_records(tmp_path: Path) -> None:
    manifest = tmp_path / "runtime_dependencies.json"
    input_path = tmp_path / "requirements-core.in"
    lock_path = tmp_path / "requirements-core.lock"
    _write_manifest(manifest)
    input_text = render_runtime_requirements(manifest)
    input_path.write_text(input_text, encoding="utf-8")
    lock_path.write_text(
        _lock_text(
            input_text,
            "examplepkg==1.9 \\\n"
            "    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            "linux-only==3.0 ; sys_platform == 'linux' \\\n"
            "    --hash=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
        ),
        encoding="utf-8",
    )

    metadata = validate_runtime_lock(
        manifest_path=manifest,
        input_path=input_path,
        lock_path=lock_path,
        expected_uv_version="0.11.29",
        expected_python_version="3.11.15",
    )

    assert metadata.input_sha256 == hashlib.sha256(input_text.encode("utf-8")).hexdigest()
    assert metadata.uv_version == "0.11.29"
    assert metadata.python_version == "3.11.15"
    assert metadata.lock_sha256 == hashlib.sha256(lock_path.read_bytes()).hexdigest()


def test_validate_runtime_lock_rejects_stale_generated_input(tmp_path: Path) -> None:
    manifest = tmp_path / "runtime_dependencies.json"
    input_path = tmp_path / "requirements-core.in"
    lock_path = tmp_path / "requirements-core.lock"
    _write_manifest(manifest)
    canonical = render_runtime_requirements(manifest)
    input_path.write_text(canonical + "unexpected-package\n", encoding="utf-8")
    lock_path.write_text(_lock_text(canonical, "examplepkg==1.9 --hash=sha256:" + "a" * 64 + "\n"))

    with pytest.raises(RuntimeLockError, match="generated runtime requirements input is stale"):
        validate_runtime_lock(
            manifest_path=manifest,
            input_path=input_path,
            lock_path=lock_path,
            expected_uv_version="0.11.29",
            expected_python_version="3.11.15",
        )


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ("examplepkg>=1 --hash=sha256:" + "a" * 64 + "\n", "not pinned exactly"),
        ("examplepkg==1.9\n", "has no SHA-256 hash"),
    ],
)
def test_validate_runtime_lock_rejects_unpinned_or_unhashed_records(
    tmp_path: Path, record: str, message: str
) -> None:
    manifest = tmp_path / "runtime_dependencies.json"
    input_path = tmp_path / "requirements-core.in"
    lock_path = tmp_path / "requirements-core.lock"
    _write_manifest(manifest)
    input_text = render_runtime_requirements(manifest)
    input_path.write_text(input_text, encoding="utf-8")
    lock_path.write_text(_lock_text(input_text, record), encoding="utf-8")

    with pytest.raises(RuntimeLockError, match=message):
        validate_runtime_lock(
            manifest_path=manifest,
            input_path=input_path,
            lock_path=lock_path,
            expected_uv_version="0.11.29",
            expected_python_version="3.11.15",
        )


def test_generate_runtime_lock_uses_pinned_uv_and_writes_bound_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "runtime_dependencies.json"
    input_path = tmp_path / "requirements-core.in"
    lock_path = tmp_path / "requirements-core.lock"
    uv_bin = tmp_path / "uv"
    _write_manifest(manifest)
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="uv 0.11.29 (test build)\n", stderr="")
        output_path = Path(argv[argv.index("--output-file") + 1])
        output_path.write_text(
            "examplepkg==1.9 \\\n"
            "    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("officina.install.runtime_lock.subprocess.run", fake_run)

    metadata = generate_runtime_lock(
        manifest_path=manifest,
        input_path=input_path,
        lock_path=lock_path,
        uv_bin=uv_bin,
        expected_uv_version="0.11.29",
        python_version="3.11.15",
    )

    assert calls[0] == [str(uv_bin), "--version"]
    assert calls[1] == [
        str(uv_bin),
        "pip",
        "compile",
        "--universal",
        "--python-version",
        "3.11.15",
        "--generate-hashes",
        "--no-header",
        "--output-file",
        str(lock_path) + ".tmp",
        str(input_path),
    ]
    assert lock_path.read_text(encoding="utf-8").startswith(
        "# famulus-runtime-lock-schema: 1\n"
        f"# input-sha256: {metadata.input_sha256}\n"
        "# uv-version: 0.11.29\n"
        "# python-version: 3.11.15\n"
    )
    validate_runtime_lock(
        manifest_path=manifest,
        input_path=input_path,
        lock_path=lock_path,
        expected_uv_version="0.11.29",
        expected_python_version="3.11.15",
    )


def test_generate_runtime_lock_rejects_wrong_uv_before_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "runtime_dependencies.json"
    _write_manifest(manifest)
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="uv 0.11.28\n", stderr="")

    monkeypatch.setattr("officina.install.runtime_lock.subprocess.run", fake_run)

    with pytest.raises(RuntimeLockError, match="expected uv 0.11.29"):
        generate_runtime_lock(
            manifest_path=manifest,
            input_path=tmp_path / "requirements-core.in",
            lock_path=tmp_path / "requirements-core.lock",
            uv_bin=tmp_path / "uv",
            expected_uv_version="0.11.29",
            python_version="3.11.15",
        )
    assert calls == [[str(tmp_path / "uv"), "--version"]]
