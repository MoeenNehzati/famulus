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


def test_v2_pointer_carries_validated_repository_configuration(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    release_dir = runtime_root / "releases" / "release"
    (release_dir / "venv" / "bin").mkdir(parents=True)
    python_bin = release_dir / "venv" / "bin" / "python"
    python_bin.write_text("#!/bin/sh\n")
    repository = tmp_path / "repository"
    (repository / "skills").mkdir(parents=True)
    config = repository / "officina.toml"
    config.write_text('schema_version = 1\n[modules]\nroots = ["skills"]\n')

    activated = activate_release(
        runtime_root=runtime_root,
        release_dir=release_dir,
        python_bin=python_bin,
        repository_config=config,
    )
    loaded = load_current_pointer(runtime_root=runtime_root)

    assert activated.repository_config == config
    assert loaded.repository_config == config
    assert json.loads((runtime_root / "current.json").read_text())["schema_version"] == 2


def test_v2_pointer_rejects_missing_repository_configuration(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    release = runtime_root / "releases" / "release"
    python_bin = release / "venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/bin/sh\n")
    (runtime_root / "current.json").write_text(json.dumps({
        "schema_version": 2,
        "release_id": "release",
        "runtime_source": str(release),
        "python_bin": str(python_bin),
        "repository_config": str(tmp_path / "missing" / "officina.toml"),
    }))

    with pytest.raises(RuntimePointerError):
        load_current_pointer(runtime_root=runtime_root)


@pytest.mark.parametrize("payload", ["{", "[]", "null"])
def test_load_current_pointer_translates_malformed_payloads(
    tmp_path: Path,
    payload: str,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "current.json").write_text(payload)

    with pytest.raises(RuntimePointerError):
        load_current_pointer(runtime_root=runtime_root)


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


def test_activate_release_rejects_python_bin_symlink_to_untrusted_target(tmp_path: Path) -> None:
    """A python_bin symlink that physically lives under runtime_root but
    resolves to an attacker-controlled binary outside it (and outside any
    trusted interpreter root) must be rejected, not silently accepted."""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    release_dir = runtime_root / "releases" / "evil-release"
    (release_dir / "venv" / "bin").mkdir(parents=True)
    python_bin = release_dir / "venv" / "bin" / "python"
    attacker_binary = tmp_path / "attacker-controlled-binary"
    attacker_binary.write_text("#!/bin/sh\necho pwned\n")
    python_bin.symlink_to(attacker_binary)

    with pytest.raises(RuntimePointerError):
        activate_release(runtime_root=runtime_root, release_dir=release_dir, python_bin=python_bin)


def test_load_current_pointer_rejects_python_bin_symlink_to_untrusted_target(tmp_path: Path) -> None:
    """Same attack via a forged current.json rather than activate_release:
    python_bin's own path is under runtime_root, but it resolves (through a
    symlink) to an arbitrary attacker-controlled binary."""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    release_dir = runtime_root / "releases" / "evil-release"
    (release_dir / "venv" / "bin").mkdir(parents=True)
    python_bin = release_dir / "venv" / "bin" / "python"
    attacker_binary = tmp_path / "attacker-controlled-binary"
    attacker_binary.write_text("#!/bin/sh\necho pwned\n")
    python_bin.symlink_to(attacker_binary)
    (runtime_root / "current.json").write_text(json.dumps({
        "schema_version": 1,
        "release_id": "evil-release",
        "runtime_source": str(release_dir),
        "python_bin": str(python_bin),
    }))

    with pytest.raises(RuntimePointerError):
        load_current_pointer(runtime_root=runtime_root)


def test_activate_release_accepts_python_bin_symlink_into_trusted_interpreter_root(
    tmp_path: Path,
) -> None:
    """A python_bin symlink resolving into an explicitly-trusted interpreter
    store (as a real ``uv venv``-created interpreter does) is accepted."""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    release_dir = runtime_root / "releases" / "good-release"
    (release_dir / "venv" / "bin").mkdir(parents=True)
    python_bin = release_dir / "venv" / "bin" / "python"

    trusted_store = tmp_path / "uv-python-store" / "cpython-3.11" / "bin"
    trusted_store.mkdir(parents=True)
    real_interpreter = trusted_store / "python3.11"
    real_interpreter.write_text("#!/bin/sh\n")
    python_bin.symlink_to(real_interpreter)

    pointer = activate_release(
        runtime_root=runtime_root,
        release_dir=release_dir,
        python_bin=python_bin,
        trusted_interpreter_roots=(tmp_path / "uv-python-store",),
    )
    assert pointer.python_bin == python_bin

    loaded = load_current_pointer(
        runtime_root=runtime_root,
        trusted_interpreter_roots=(tmp_path / "uv-python-store",),
    )
    assert loaded.release_id == "good-release"


def test_load_current_pointer_still_rejects_untrusted_symlink_even_with_a_different_trusted_root(
    tmp_path: Path,
) -> None:
    """Trusting one interpreter store must not accidentally trust everything:
    an attacker target outside runtime_root and outside the (unrelated)
    trusted root supplied by the caller is still rejected."""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    release_dir = runtime_root / "releases" / "evil-release"
    (release_dir / "venv" / "bin").mkdir(parents=True)
    python_bin = release_dir / "venv" / "bin" / "python"
    attacker_binary = tmp_path / "attacker-controlled-binary"
    attacker_binary.write_text("#!/bin/sh\n")
    python_bin.symlink_to(attacker_binary)
    (runtime_root / "current.json").write_text(json.dumps({
        "schema_version": 1,
        "release_id": "evil-release",
        "runtime_source": str(release_dir),
        "python_bin": str(python_bin),
    }))

    unrelated_trusted_store = tmp_path / "uv-python-store"
    unrelated_trusted_store.mkdir(parents=True)
    with pytest.raises(RuntimePointerError):
        load_current_pointer(
            runtime_root=runtime_root,
            trusted_interpreter_roots=(unrelated_trusted_store,),
        )


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
