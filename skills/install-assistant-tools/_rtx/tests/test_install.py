from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .. import _phase_entry as install
else:
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


def test_non_interactive_install_defaults_to_excluding_optional_dependencies(tmp_path, monkeypatch):
    """Non-interactive installs must not silently pull in large, single-skill
    dependencies (e.g. pdf-to-markdown's OCR/ML models, several GB) -- the
    user has to opt in explicitly, either interactively or via
    --include-optional-deps."""
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: None)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: None)
    monkeypatch.setattr(
        install.managed_runtime,
        "build_candidate_release",
        lambda **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(install.managed_runtime, "optional_python_packages", lambda *a, **kw: ("marker-pdf",))

    install.run(
        home=tmp_path, dry_run=False, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    assert calls[0]["include_optional_dependencies"] is False


def test_non_interactive_install_honors_explicit_include_optional_deps(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: None)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: None)
    monkeypatch.setattr(
        install.managed_runtime,
        "build_candidate_release",
        lambda **kwargs: calls.append(kwargs),
    )

    install.run(
        home=tmp_path, dry_run=False, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
        include_optional_dependencies=True,
    )

    assert calls[0]["include_optional_dependencies"] is True


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
    # Real bootstrap_uv is not under test here and would otherwise also
    # observe the atomic_files.atomic_replace_bytes patch below (it's the
    # same shared module object) and make a real network call -- stub it
    # out as a plain no-op success so only _deploy_resolver's failure is
    # exercised.
    monkeypatch.setattr(install.uv_bootstrap, "bootstrap_uv", lambda **kw: None)
    monkeypatch.setattr(install.managed_runtime, "_create_release_venv", lambda **kw: None)
    monkeypatch.setattr(install.managed_runtime, "_run_dependency_install", lambda **kw: None)
    monkeypatch.setattr(
        install.managed_runtime, "_uv_python_install_dir",
        lambda uv_bin: tmp_path / "uv-python-store",
    )
    monkeypatch.setattr(
        install.managed_runtime.atomic_files, "atomic_replace_bytes",
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


def test_phase_entry_catches_runtime_pointer_error_not_just_managed_runtime_error(tmp_path, monkeypatch):
    """Regression test for a real bug: build_candidate_release used to
    hardcode a POSIX-only venv interpreter path, so on Windows the computed
    python_bin never existed and runtime_pointer.activate_release raised
    RuntimePointerError -- which is NOT a subclass of
    managed_runtime.ManagedRuntimeError. _build_managed_runtime_candidate's
    `except managed_runtime.ManagedRuntimeError` alone did not catch it, so
    it propagated as an unhandled crash instead of a clean nonzero exit.
    Simulates that exact situation (a python_bin that is never created on
    disk) without needing a real Windows host, and asserts install.run
    returns nonzero instead of raising.
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
    # python_bin is never actually created on disk (venv creation is
    # mocked away above), so activate_release's `python_bin.exists()`
    # check fails and it raises RuntimePointerError -- the real failure
    # mode this test is guarding against.

    status = install.run(
        home=tmp_path, dry_run=False, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    assert status != 0
    assert "scaffold" not in [name for name, _ in calls]
    paths = resolve_famulus_paths(platform=sys.platform, home=tmp_path)
    assert not paths.current_pointer.exists()


def test_ensure_managed_uv_calls_bootstrap_even_when_binary_already_exists(tmp_path, monkeypatch):
    """Regression test for a real bug: _ensure_managed_uv used to return
    early with `if paths.uv_bin.exists(): return 0`, before ever calling
    uv_bootstrap.bootstrap_uv -- the only production call site of
    bootstrap_uv. bootstrap_uv already has correct no-op-if-matching /
    re-bootstrap-if-stale version logic internally, but the premature
    short-circuit here prevented that logic from ever running once any
    binary already existed, so a future bump to the pinned uv_version would
    never reach an already-provisioned machine. This asserts bootstrap_uv
    is called even when paths.uv_bin already exists on disk.
    """
    from officina.common.famulus_paths import resolve_famulus_paths as _resolve

    home = tmp_path
    paths = _resolve(platform=sys.platform, home=home)
    paths.uv_bin.parent.mkdir(parents=True, exist_ok=True)
    paths.uv_bin.write_text("#!/bin/sh\necho 'uv 0.0.0 (stale stub)'\n")
    paths.uv_bin.chmod(0o755)

    bootstrap_calls = []
    monkeypatch.setattr(
        install.uv_bootstrap, "bootstrap_uv",
        lambda **kwargs: bootstrap_calls.append(kwargs),
    )

    class _Info:
        uv_version = "9.9.9"
        managed_python = "3.11"

    status = install._ensure_managed_uv(
        info=_Info(), paths=paths, platform_name="linux",
    )

    assert status == 0
    assert len(bootstrap_calls) == 1
    assert bootstrap_calls[0]["uv_bin"] == paths.uv_bin
    assert bootstrap_calls[0]["version"] == "9.9.9"


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
