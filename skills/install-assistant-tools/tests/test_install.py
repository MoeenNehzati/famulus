from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_rtx"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import _phase_entry as install

from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.managed_runtime import ManagedRuntimeError


def test_plugin_mode_skips_dev_link(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append(("scaffold", kw)))
    monkeypatch.setattr(install.dev_link, "run", lambda **kw: calls.append(("dev_link", kw)))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append(("launchers", kw)))

    status = install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    names = [name for name, _ in calls]
    assert status == 0
    assert names == ["scaffold", "launchers"]


def test_dev_mode_requires_repo_path_non_interactively(tmp_path, monkeypatch):
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: None)
    monkeypatch.setattr(install.dev_link, "run", lambda **kw: None)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: None)

    import pytest
    with pytest.raises(SystemExit):
        install.run(
            home=tmp_path, dry_run=True, non_interactive=True,
            dev_mode=True, repo_path=None, agents=[], default_llm="claude",
        )


def test_dev_mode_with_repo_path_chains_dev_link(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append(("scaffold", kw)))
    monkeypatch.setattr(install.dev_link, "run", lambda **kw: calls.append(("dev_link", kw)))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append(("launchers", kw)))
    repo_path = tmp_path / "myrepo"

    status = install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=True, repo_path=repo_path, agents=["assistant"], default_llm="codex",
    )

    names = [name for name, _ in calls]
    assert status == 0
    assert names == ["scaffold", "dev_link", "launchers"]
    dev_link_kwargs = dict(calls[1][1])
    assert dev_link_kwargs["repo_root"] == repo_path
    launchers_kwargs = dict(calls[2][1])
    assert launchers_kwargs["agents"] == ["assistant"]
    assert launchers_kwargs["default_llm"] == "codex"


def test_plugin_mode_uses_auto_derived_repo_root(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append(("scaffold", kw)))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append(("launchers", kw)))

    install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    scaffold_kwargs = dict(calls[0][1])
    # Auto-derived from install.py's own location: <repo>/skills/install-assistant-tools/_rtx/_phase_entry.py
    expected_repo_root = Path(install.__file__).resolve().parents[3]
    assert scaffold_kwargs["repo_root"] == expected_repo_root


def test_scaffold_failure_stops_install_before_later_phases(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append(("scaffold", kw)) or 1)
    monkeypatch.setattr(install.dev_link, "run", lambda **kw: calls.append(("dev_link", kw)))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append(("launchers", kw)))

    status = install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    assert status == 1
    assert [name for name, _ in calls] == ["scaffold"]


# ── Managed-runtime candidate wiring (Task 7) ────────────────────────────────


def test_phase_entry_builds_candidate_before_scaffold(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append(("scaffold", kw)))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append(("launchers", kw)))
    monkeypatch.setattr(
        install.managed_runtime,
        "build_candidate_release",
        lambda **kwargs: calls.append(("build_candidate_release", kwargs)),
    )

    status = install.run(
        home=tmp_path, dry_run=False, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    names = [name for name, _ in calls]
    assert status == 0
    assert names.index("build_candidate_release") < names.index("scaffold")


def test_phase_entry_failed_candidate_leaves_prior_pointer_and_returns_nonzero(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append(("scaffold", kw)))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append(("launchers", kw)))

    def fail(**kwargs):
        raise ManagedRuntimeError("simulated")

    monkeypatch.setattr(install.managed_runtime, "build_candidate_release", fail)

    status = install.run(
        home=tmp_path, dry_run=False, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    assert status != 0
    assert "scaffold" not in [name for name, _ in calls]
    paths = resolve_famulus_paths(platform=sys.platform, home=tmp_path)
    assert not paths.current_pointer.exists()


def test_phase_entry_resolver_deploy_failure_returns_nonzero_not_a_crash(tmp_path, monkeypatch):
    """A failed resolver deployment inside build_candidate_release (e.g.
    disk full, permissions) must surface to _phase_entry.py as a
    ManagedRuntimeError its `except ManagedRuntimeError` catches cleanly --
    not a raw OSError that crashes the installer with an unhandled
    traceback. Exercises the real build_candidate_release/_deploy_resolver
    code path (only the uv-shelling internals are mocked out) rather than
    mocking build_candidate_release itself, so this actually proves the
    exception-type contract managed_runtime.py promises.
    """
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append(("scaffold", kw)))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append(("launchers", kw)))
    monkeypatch.setattr(install.managed_runtime, "_create_release_venv", lambda **kw: None)
    monkeypatch.setattr(install.managed_runtime, "_run_dependency_install", lambda **kw: None)
    monkeypatch.setattr(
        install.managed_runtime, "_uv_python_install_dir",
        lambda uv_bin: tmp_path / "uv-python-store",
    )
    monkeypatch.setattr(
        install.managed_runtime.shutil, "copy2",
        lambda *a, **k: (_ for _ in ()).throw(OSError("simulated disk full")),
    )

    status = install.run(
        home=tmp_path, dry_run=False, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    assert status != 0
    assert "scaffold" not in [name for name, _ in calls]
    paths = resolve_famulus_paths(platform=sys.platform, home=tmp_path)
    assert not paths.current_pointer.exists()


def test_phase_entry_dry_run_skips_candidate_build(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append(("scaffold", kw)))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append(("launchers", kw)))
    monkeypatch.setattr(
        install.managed_runtime,
        "build_candidate_release",
        lambda **kwargs: calls.append(("build_candidate_release", kwargs)),
    )

    status = install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    assert status == 0
    assert "build_candidate_release" not in [name for name, _ in calls]


# ── Google onboarding wiring (Task 4) ────────────────────────────────────────


def test_phase_entry_calls_google_onboarding_only_after_core_install_succeeds(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        install.managed_runtime, "build_candidate_release",
        lambda **kwargs: calls.append("build"),
    )
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append("scaffold"))
    monkeypatch.setattr(install.dev_link, "run", lambda **kw: calls.append("dev_link"))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append("launchers"))
    monkeypatch.setattr(
        install.google_onboarding, "run_google_onboarding",
        lambda *a, **kw: calls.append("google_onboarding") or install.google_onboarding.OnboardingCapabilityResult(status="needs_selection"),
    )

    status = install.run(
        home=tmp_path, dry_run=False, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    assert status == 0
    assert calls.index("build") < calls.index("scaffold") < calls.index("google_onboarding")


def test_phase_entry_skips_google_onboarding_on_core_install_failure(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: 1)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: called.append("launchers"))
    monkeypatch.setattr(
        install.google_onboarding, "run_google_onboarding",
        lambda *a, **kw: called.append("google_onboarding"),
    )

    status = install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    assert status != 0
    assert called == []


def test_phase_entry_google_onboarding_failure_does_not_fail_install(tmp_path, monkeypatch):
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: None)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: None)

    def boom(*a, **kw):
        raise RuntimeError("simulated dispatcher failure")

    monkeypatch.setattr(install.google_onboarding, "run_google_onboarding", boom)

    status = install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    assert status == 0


def test_phase_entry_google_onboarding_partial_does_not_fail_install(tmp_path, monkeypatch):
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: None)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: None)
    monkeypatch.setattr(
        install.google_onboarding, "run_google_onboarding",
        lambda *a, **kw: install.google_onboarding.OnboardingCapabilityResult(
            status="partial", credential_id="cred-1",
            granted_services=("drive",), denied_services=(), deferred_services=("gmail",),
        ),
    )

    status = install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    assert status == 0
