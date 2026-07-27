from __future__ import annotations

import json
from pathlib import Path

import pytest

from officina.install.runtime_pointer import (
    RuntimePointer,
    RuntimePointerError,
    activate_release,
    load_current_pointer,
)


def test_activate_release_writes_pointer_atomically(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    releases_root = runtime_root / "releases"
    release_dir = releases_root / "2026-07-27T00-00-00Z-abc123"
    release_dir.mkdir(parents=True)
    (release_dir / "venv" / "bin").mkdir(parents=True)
    python_bin = release_dir / "venv" / "bin" / "python"
    python_bin.write_text("#!/bin/sh\n")

    activate_release(runtime_root=runtime_root, release_dir=release_dir, python_bin=python_bin)

    pointer = load_current_pointer(runtime_root=runtime_root)
    assert pointer.release_id == "2026-07-27T00-00-00Z-abc123"
    assert pointer.python_bin == python_bin


def test_load_current_pointer_rejects_path_outside_runtime_root(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    outside = tmp_path / "outside" / "python"
    (runtime_root / "current.json").write_text(json.dumps({
        "schema_version": 1,
        "release_id": "x",
        "runtime_source": str(outside.parent),
        "python_bin": str(outside),
    }))
    with pytest.raises(RuntimePointerError):
        load_current_pointer(runtime_root=runtime_root)


def test_load_current_pointer_rejects_python_bin_outside_root_even_with_contained_source(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    inside_source = runtime_root / "releases" / "good-release"
    outside_python = tmp_path / "outside" / "python"
    (runtime_root / "current.json").write_text(json.dumps({
        "schema_version": 1,
        "release_id": "x",
        "runtime_source": str(inside_source),
        "python_bin": str(outside_python),
    }))
    with pytest.raises(RuntimePointerError):
        load_current_pointer(runtime_root=runtime_root)


def test_load_current_pointer_rejects_runtime_source_outside_root_even_with_contained_python_bin(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    outside_source = tmp_path / "outside" / "good-release"
    inside_python = runtime_root / "releases" / "good-release" / "venv" / "bin" / "python"
    (runtime_root / "current.json").write_text(json.dumps({
        "schema_version": 1,
        "release_id": "x",
        "runtime_source": str(outside_source),
        "python_bin": str(inside_python),
    }))
    with pytest.raises(RuntimePointerError):
        load_current_pointer(runtime_root=runtime_root)


def test_activate_release_rejects_python_bin_outside_runtime_root(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    release_dir = runtime_root / "releases" / "escaping-release"
    release_dir.mkdir(parents=True)
    outside_python = tmp_path / "outside" / "python"
    outside_python.parent.mkdir(parents=True)
    outside_python.write_text("#!/bin/sh\n")

    with pytest.raises(RuntimePointerError):
        activate_release(runtime_root=runtime_root, release_dir=release_dir, python_bin=outside_python)


def test_failed_activation_leaves_prior_pointer_untouched(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    releases_root = runtime_root / "releases"
    good = releases_root / "good-release"
    (good / "venv" / "bin").mkdir(parents=True)
    (good / "venv" / "bin" / "python").write_text("#!/bin/sh\n")
    activate_release(runtime_root=runtime_root, release_dir=good, python_bin=good / "venv" / "bin" / "python")

    missing_python = releases_root / "bad-release" / "venv" / "bin" / "python"
    with pytest.raises(RuntimePointerError):
        activate_release(runtime_root=runtime_root, release_dir=missing_python.parents[1], python_bin=missing_python)

    pointer = load_current_pointer(runtime_root=runtime_root)
    assert pointer.release_id == "good-release"
