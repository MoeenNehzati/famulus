"""Integration-level managed-runtime lifecycle acceptance tests.

Exercises `officina.install.managed_runtime.build_candidate_release` and
`officina.install.runtime_pointer` *together*, as a caller (e.g.
`_phase_entry.py`) would: first install, update, failed update, and
rollback. `tests/test_officina_managed_runtime.py` already covers
`build_candidate_release`'s internal call shapes/ordering in isolation;
this file is the acceptance layer proving the two modules compose into a
correct install/update/rollback lifecycle, not a duplicate of that unit
coverage.

Uses the fake `subprocess.run` shared with test_officina_managed_runtime.py
(see conftest.py::fake_uv_subprocess_run) rather than a real `uv` binary:
this keeps the lifecycle acceptance suite fast and network-independent,
while
`test_officina_managed_runtime.py::test_build_candidate_release_end_to_end_with_real_uv`
already proves the real-uv path works end to end.

Uninstall/purge invariants (Task 1, Step 5 of the acceptance plan) are
skipped: they require ownership-aware manifest-v2
(`skills/install-assistant-tools/_rtx/_state_record.py::MANIFEST_VERSION`
is still 1 as of this writing), which is tracked as a separate follow-up.
Faking that behavior here would produce a false acceptance signal.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import fake_uv_subprocess_run
from officina.install.managed_runtime import ManagedRuntimeError, build_candidate_release
from officina.install.runtime_pointer import activate_release, load_current_pointer


def _build(monkeypatch, calls, tmp_path, runtime_root, *, trusted_python_dir=None):
    trusted_python_dir = trusted_python_dir or (tmp_path / "uv-python-store")
    monkeypatch.setattr(
        "subprocess.run", fake_uv_subprocess_run(calls, trusted_python_dir=trusted_python_dir)
    )
    manifest = tmp_path / "runtime_dependencies.json"
    if not manifest.exists():
        manifest.write_text(
            '{"version": 2, "skills": {}}',
            encoding="utf-8",
        )
    return build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest,
        platform="linux",
        uv_bin=Path("/fake/uv"),
        python_version="3.11",
    ), trusted_python_dir


def test_first_install_creates_a_release_and_activates_it(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    calls: list = []
    pointer, trusted_python_dir = _build(monkeypatch, calls, tmp_path, runtime_root)

    loaded = load_current_pointer(
        runtime_root=runtime_root, trusted_interpreter_roots=(trusted_python_dir,)
    )
    assert loaded.runtime_source == pointer.runtime_source
    assert loaded.release_id == pointer.release_id
    assert pointer.runtime_source.exists()
    assert pointer.python_bin.exists()


def test_first_install_never_invokes_ambient_python(monkeypatch, tmp_path):
    """A poisoned sys.executable is not enough to prove the invariant unless
    we also assert it was never *read* into a subprocess call: patch
    sys.executable to an obviously-wrong sentinel value, spy on every
    subprocess.run argv, and assert that sentinel never appears in any
    invoked command. build_candidate_release must drive uv exclusively via
    the managed uv_bin/python_bin it creates itself, never via the ambient
    interpreter running the installer.

    The manifest here declares a real python-package dependency (unlike
    the empty-``skills`` manifest `_build` defaults to elsewhere in this
    file): an empty dependency list makes `_run_dependency_install` hit its
    early-return guard (managed_runtime.py) and skip the `uv pip install`
    call entirely, so a regression that leaked `sys.executable` into
    specifically *that* call site would pass undetected. Asserting
    `len(calls) == 3` below proves all three real call sites (`uv venv`,
    `uv pip install`, `uv python dir`) actually fired, not just two of
    them.
    """
    poison = "/should/never/be/used/python3"
    monkeypatch.setattr(sys, "executable", poison)

    runtime_root = tmp_path / "runtime"
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 2,
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
                                    }
                                ]
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    calls: list = []
    _build(monkeypatch, calls, tmp_path, runtime_root)

    assert calls, "expected build_candidate_release to invoke subprocess.run at all"
    assert len(calls) == 3, (
        "expected exactly 3 real uv call sites (venv, pip install, python dir) "
        f"to have fired, got {len(calls)}: {calls}"
    )
    pip_install_calls = [c for c in calls if len(c) > 2 and c[1:3] == ["pip", "install"]]
    assert pip_install_calls, "expected the uv pip install call site to actually run"
    for call in calls:
        assert poison not in call, f"ambient sys.executable leaked into a subprocess call: {call}"


def test_successful_update_activates_new_release_old_retained(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text('{"version": 2, "skills": {}}', encoding="utf-8")

    calls: list = []
    first, trusted_python_dir = _build(monkeypatch, calls, tmp_path, runtime_root)
    second, _ = _build(monkeypatch, calls, tmp_path, runtime_root, trusted_python_dir=trusted_python_dir)

    assert second.release_id != first.release_id
    loaded = load_current_pointer(
        runtime_root=runtime_root, trusted_interpreter_roots=(trusted_python_dir,)
    )
    assert loaded.runtime_source == second.runtime_source
    # Previous release is retained on disk for rollback, not deleted.
    assert first.runtime_source.exists()
    assert first.python_bin.exists()


def test_failed_update_leaves_prior_pointer_and_release_usable(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text('{"version": 2, "skills": {}}', encoding="utf-8")

    calls: list = []
    good, trusted_python_dir = _build(monkeypatch, calls, tmp_path, runtime_root)

    def fail(**kwargs):
        raise ManagedRuntimeError("simulated dependency install failure")

    monkeypatch.setattr("officina.install.managed_runtime._run_dependency_install", fail)

    with pytest.raises(ManagedRuntimeError):
        build_candidate_release(
            runtime_root=runtime_root,
            manifest_path=manifest,
            platform="linux",
            uv_bin=Path("/fake/uv"),
            python_version="3.11",
        )

    loaded = load_current_pointer(
        runtime_root=runtime_root, trusted_interpreter_roots=(trusted_python_dir,)
    )
    assert loaded.runtime_source == good.runtime_source
    assert loaded.release_id == good.release_id
    assert good.runtime_source.exists()
    assert good.python_bin.exists()


def test_candidate_build_never_constructs_dispatch_snapshot(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    repo_root = tmp_path / "repo"
    manifest = repo_root / "references" / "blueprint" / "runtime_dependencies.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"version": 2, "skills": {}}', encoding="utf-8")
    (repo_root / "officina.toml").write_text(
        'schema_version = 1\n[modules]\nroots = ["skills", "src/officina"]\n',
        encoding="utf-8",
    )
    (repo_root / "skills").mkdir()
    (repo_root / "src" / "officina").mkdir(parents=True)
    calls: list = []
    trusted_python_dir = tmp_path / "uv-python-store"
    monkeypatch.setattr(
        "subprocess.run",
        fake_uv_subprocess_run(calls, trusted_python_dir=trusted_python_dir),
    )

    def fail_snapshot(*args, **kwargs):
        raise AssertionError("candidate construction must not build routing state")

    monkeypatch.setattr(
        "officina.install.managed_runtime.build_dispatch_snapshot",
        fail_snapshot,
        raising=False,
    )

    pointer = build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest,
        platform="linux",
        uv_bin=Path("/fake/uv"),
        python_version="3.11",
        repo_root=repo_root,
    )

    assert (runtime_root / "current.json").exists()
    assert pointer.repository_config == (repo_root / "officina.toml").resolve()


def test_rollback_reactivates_previous_release(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text('{"version": 2, "skills": {}}', encoding="utf-8")

    calls: list = []
    first, trusted_python_dir = _build(monkeypatch, calls, tmp_path, runtime_root)
    second, _ = _build(monkeypatch, calls, tmp_path, runtime_root, trusted_python_dir=trusted_python_dir)

    loaded = load_current_pointer(
        runtime_root=runtime_root, trusted_interpreter_roots=(trusted_python_dir,)
    )
    assert loaded.runtime_source == second.runtime_source

    rolled_back = activate_release(
        runtime_root=runtime_root,
        release_dir=first.runtime_source,
        python_bin=first.python_bin,
        trusted_interpreter_roots=(trusted_python_dir,),
    )
    assert rolled_back.release_id == first.release_id

    loaded_after_rollback = load_current_pointer(
        runtime_root=runtime_root, trusted_interpreter_roots=(trusted_python_dir,)
    )
    assert loaded_after_rollback.runtime_source == first.runtime_source
    assert loaded_after_rollback.release_id == first.release_id


# famulus-skip: category=capability-unavailable; reason=requires manifest-v2 ownership-aware entries, not landed yet; alternate=none -- real coverage lands with the manifest-v2 follow-up
@pytest.mark.skip(reason="requires manifest-v2 (ownership-aware uninstall), tracked separately")
def test_default_uninstall_removes_only_this_installations_resources(tmp_path):
    """Default uninstall must remove only the resources this installation
    owns (per an ownership-aware manifest), leaving anything not recorded
    as this installation's own side effect untouched. Not implementable
    against today's manifest v1
    (skills/install-assistant-tools/_rtx/_state_record.py::MANIFEST_VERSION
    == 1): manifest v1 has no ownership/ID concept to distinguish "this
    installation's releases" from any other. Faking this against v1 would
    give a false acceptance signal; real coverage lands with manifest-v2.
    """
    raise NotImplementedError("requires manifest-v2")


# famulus-skip: category=capability-unavailable; reason=requires manifest-v2 ownership-aware entries, not landed yet; alternate=none -- real coverage lands with the manifest-v2 follow-up
@pytest.mark.skip(reason="requires manifest-v2 (ownership-aware uninstall), tracked separately")
def test_purge_removes_config_and_state_roots(tmp_path):
    """Purge (uninstall --purge) must additionally remove the config and
    state roots (e.g. XDG config/state dirs) that a default uninstall
    leaves behind. Same manifest-v2 dependency as the uninstall test above:
    without ownership-aware records, there is no reliable way to scope
    "this installation's config/state" for a real (non-faked) assertion.
    """
    raise NotImplementedError("requires manifest-v2")
