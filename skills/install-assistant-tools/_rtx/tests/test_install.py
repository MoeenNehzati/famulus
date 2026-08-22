from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .. import _phase_entry as install
else:
    import _phase_entry as install

from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.managed_runtime import ManagedRuntimeError
from officina.install.doctor import DiagnosticCheck
from officina.install.runtime_pointer import load_current_pointer


@pytest.fixture(autouse=True)
def _isolate_managed_uv_bootstrap(monkeypatch):
    """Keep phase-orchestration unit tests independent of network access.

    Tests that exercise ``_ensure_managed_uv`` replace this no-op with their
    own recording double. End-to-end bootstrap coverage lives in the managed
    runtime and installer integration suites.
    """
    monkeypatch.setattr(install.uv_bootstrap, "bootstrap_uv", lambda **kw: None)


def test_interface_restarts_with_current_source_when_runtime_module_is_foreign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign_module = tmp_path / "old-runtime" / "officina" / "install" / "managed_runtime.py"
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(install.managed_runtime, "__file__", str(foreign_module))

    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(install.subprocess, "run", run_child)

    status = install.Interface().run(["--non-interactive", "--no-dev-mode"])

    assert status == 7
    assert calls[0][0] == [
        sys.executable,
        str(Path(install.__file__).resolve()),
        "--non-interactive",
        "--no-dev-mode",
    ]
    child_env = calls[0][1]["env"]
    assert isinstance(child_env, dict)
    assert child_env["PYTHONPATH"] == str(install.REPO_SRC)
    assert calls[0][1]["check"] is False


def test_interface_restarts_when_an_imported_officina_sibling_is_foreign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign_module = tmp_path / "old-runtime" / "officina" / "install" / "runtime_pointer.py"
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(install.runtime_pointer, "__file__", str(foreign_module))
    monkeypatch.setenv("PYTHONPATH", "/hostile/first:/hostile/second")

    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 9)

    monkeypatch.setattr(install.subprocess, "run", run_child)

    status = install.Interface().run(["--non-interactive", "--no-dev-mode"])

    assert status == 9
    child_env = calls[0][1]["env"]
    assert isinstance(child_env, dict)
    assert child_env["PYTHONPATH"] == str(install.REPO_SRC)


def test_apply_uses_one_resolved_context_for_every_stage_and_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path(install.__file__).resolve().parents[3]
    paths = resolve_famulus_paths(platform=sys.platform, home=tmp_path, environ={})
    context = install.InstallationContext(
        mode="standard",
        source_root=source,
        development_root=None,
        paths=paths,
        codex_home=tmp_path / ".codex",
        claude_home=tmp_path / ".claude",
        installation_id="standard",
    )
    choices = install.ApplyChoices(agents=("assistant",), default_backend="codex")
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        install,
        "_build_managed_runtime_candidate",
        lambda **kw: calls.append(("candidate", kw)) or 0,
    )
    monkeypatch.setattr(
        install.scaffold,
        "run",
        lambda **kw: calls.append(("scaffold", kw)) or 0,
    )
    monkeypatch.setattr(
        install.dev_link,
        "run",
        lambda **kw: calls.append(("projection", kw)),
    )
    monkeypatch.setattr(
        install.launchers,
        "run",
        lambda **kw: calls.append(("helpers", kw)) or True,
    )
    monkeypatch.setattr(
        install,
        "diagnose_installation",
        lambda **kw: calls.append(("verify", kw)) or install.DiagnosticReport.healthy_for(context),
    )

    status = install.apply(context=context, choices=choices, environ={})

    assert status == 0
    assert [name for name, _ in calls] == ["candidate", "scaffold", "helpers", "verify"]
    for _name, kwargs in calls:
        assert kwargs["context"] is context


def test_apply_records_bootstrap_resolver_tree_identity(tmp_path: Path) -> None:
    source = Path(install.__file__).resolve().parents[3]
    paths = resolve_famulus_paths(platform=sys.platform, home=tmp_path, environ={})
    context = install.InstallationContext(
        mode="standard", source_root=source, development_root=None, paths=paths,
        codex_home=tmp_path / ".codex", claude_home=tmp_path / ".claude",
        installation_id="standard",
    )
    release = paths.releases_root / "release-1"
    release.mkdir(parents=True)
    (release / "payload").write_text("release\n", encoding="utf-8")
    resolver = paths.runtime_root / "bootstrap" / "resolvers"
    (resolver / "v1").mkdir(parents=True)
    (resolver / "v1" / "launch.py").write_text("resolver\n", encoding="utf-8")
    paths.current_pointer.parent.mkdir(parents=True, exist_ok=True)
    paths.current_pointer.write_text(
        __import__("json").dumps({"runtime_source": str(release)}), encoding="utf-8"
    )
    manifest = install.scaffold.Manifest(tmp_path / "manifest.json")

    install._record_managed_runtime_state(context=context, manifest=manifest)

    entries = {entry["path"]: entry for entry in manifest.entries}
    assert entries[str(paths.current_pointer)]["sha256"]
    assert entries[str(release)]["tree_sha256"]
    assert entries[str(resolver)]["tree_sha256"]
    assert entries[str(resolver)]["purge_only"] is True


def test_apply_stops_before_later_effects_when_scaffold_is_required_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path(install.__file__).resolve().parents[3]
    context = install.InstallationContext(
        mode="standard",
        source_root=source,
        development_root=None,
        paths=resolve_famulus_paths(platform=sys.platform, home=tmp_path, environ={}),
        codex_home=tmp_path / ".codex",
        claude_home=tmp_path / ".claude",
        installation_id="standard",
    )
    calls: list[str] = []
    monkeypatch.setattr(install, "_build_managed_runtime_candidate", lambda **kw: 0)
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append("scaffold") or 1)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append("helpers"))
    monkeypatch.setattr(install, "diagnose_installation", lambda **kw: calls.append("verify"))

    status = install.apply(
        context=context,
        choices=install.ApplyChoices(agents=(), default_backend="claude"),
        environ={},
    )

    assert status == 1
    assert calls == ["scaffold"]


@pytest.mark.parametrize(
    "raw",
    [
        b"{malformed",
        b'{"version": 999, "entries": []}\n',
        b'{"version": 2, "entries": []}\n',
        b'{"version": 2, "entries": {}, "installation": {"mode": "standard", "installation_id": "standard"}}\n',
    ],
)
def test_apply_preserves_invalid_existing_manifest_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    source = Path(install.__file__).resolve().parents[3]
    context = install.InstallationContext(
        mode="standard", source_root=source, development_root=None,
        paths=resolve_famulus_paths(platform=sys.platform, home=tmp_path, environ={}),
        codex_home=tmp_path / ".codex", claude_home=tmp_path / ".claude",
        installation_id="standard",
    )
    manifest = install._manifest_path(context)
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(raw)
    later = []
    monkeypatch.setattr(install, "_build_managed_runtime_candidate", lambda **kw: later.append("candidate") or 0)

    status = install.apply(
        context=context,
        choices=install.ApplyChoices(agents=(), default_backend="claude"),
        environ={},
    )

    assert status == 1
    assert manifest.read_bytes() == raw
    assert later == []


def test_real_standard_apply_is_accepted_by_real_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production manifest writer and reader agree after a real apply."""
    source = Path(install.__file__).resolve().parents[3]
    context = install.resolve_installation_context(
        mode="standard",
        source_root=source,
        development_root=None,
        platform=sys.platform,
        home=tmp_path,
        environ={},
    )

    def publish_candidate(*, context, optional_module_ids):
        assert optional_module_ids == ()
        release = context.paths.releases_root / "doctor-integration"
        python_dir = release / "venv" / ("Scripts" if sys.platform == "win32" else "bin")
        python_dir.mkdir(parents=True)
        python_bin = python_dir / ("python.exe" if sys.platform == "win32" else "python")
        shutil.copy2(sys.executable, python_bin)
        python_bin.chmod(0o755)
        record = install.managed_runtime._publish_installation_context(
            release_dir=release, context=context
        )
        launcher_resources = release / "launcher-resources"
        launcher_resources.mkdir()
        install.managed_runtime._deploy_resolver(
            runtime_root=context.paths.runtime_root,
            trusted_interpreter_roots=(),
        )
        install.runtime_pointer.activate_release(
            runtime_root=context.paths.runtime_root,
            release_dir=release,
            python_bin=python_bin,
            repository_config=context.source_root / "officina.toml",
            launcher_resources=launcher_resources,
            installation_context=record,
        )
        return 0

    monkeypatch.setattr(install, "_build_managed_runtime_candidate", publish_candidate)

    status = install.apply(
        context=context,
        choices=install.ApplyChoices(
            agents=(), default_backend="codex", home=tmp_path, shell_rc=tmp_path / ".bashrc"
        ),
        environ={},
    )

    assert status == 0
    manifest_check = next(
        check
        for check in install.diagnose_installation(
            context=context,
            environ={"PATH": str(context.paths.user_bin)},
            platform=sys.platform,
        ).checks
        if check.id == "manifest"
    )
    assert manifest_check.status == "ok"


def test_reapply_succeeds_when_doctor_reports_valid_recurring_registrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path(install.__file__).resolve().parents[3]
    context = install.InstallationContext(
        mode="standard",
        source_root=source,
        development_root=None,
        paths=resolve_famulus_paths(platform=sys.platform, home=tmp_path, environ={}),
        codex_home=tmp_path / ".codex",
        claude_home=tmp_path / ".claude",
        installation_id="standard",
    )
    report = install.DiagnosticReport(
        1,
        "standard",
        "standard",
        "healthy",
        (DiagnosticCheck("recurring", "ok", "Recurring registrations: 1"),),
    )
    monkeypatch.setattr(install, "_build_managed_runtime_candidate", lambda **kw: 0)
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: 0)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: True)
    monkeypatch.setattr(install, "diagnose_installation", lambda **kw: report)

    status = install.apply(
        context=context,
        choices=install.ApplyChoices(agents=(), default_backend="claude"),
        environ={},
    )

    assert status == 0


def test_moved_installed_development_checkout_repair_rebases_pointer_context_and_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = tmp_path / "original installed checkout 雪"
    moved = tmp_path / "moved installed checkout 雪"
    stable_home = tmp_path / "stable home 雪"
    (original / "skills").mkdir(parents=True)
    (original / "src" / "officina").mkdir(parents=True)
    (original / "officina.toml").write_text(
        'schema_version = 1\n[modules]\nroots = ["skills", "src/officina"]\n',
        encoding="utf-8",
    )
    installation_id = install.load_or_create_development_installation_id(
        original, platform=sys.platform, home=stable_home, environ={}
    )

    def context_for(checkout: Path):
        return install.resolve_installation_context(
            mode="development",
            source_root=checkout,
            development_root=checkout,
            platform=sys.platform,
            home=stable_home,
            environ={},
            installation_id=installation_id,
        )

    release_number = 0

    def publish_candidate(*, context, optional_module_ids):
        nonlocal release_number
        assert optional_module_ids == ()
        release_number += 1
        release = context.paths.releases_root / f"moved-repair-{release_number}"
        python_dir = release / "venv" / ("Scripts" if sys.platform == "win32" else "bin")
        python_dir.mkdir(parents=True)
        python_bin = python_dir / ("python.exe" if sys.platform == "win32" else "python")
        shutil.copy2(sys.executable, python_bin)
        python_bin.chmod(0o755)
        context_record = install.managed_runtime._publish_installation_context(
            release_dir=release, context=context
        )
        install.managed_runtime._deploy_resolver(
            runtime_root=context.paths.runtime_root,
            trusted_interpreter_roots=(),
        )
        install.runtime_pointer.activate_release(
            runtime_root=context.paths.runtime_root,
            release_dir=release,
            python_bin=python_bin,
            repository_config=context.source_root / "officina.toml",
            launcher_resources=context.source_root,
            installation_context=context_record,
        )
        return 0

    monkeypatch.setattr(install, "_build_managed_runtime_candidate", publish_candidate)
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: 0)
    monkeypatch.setattr(install.dev_link, "run", lambda **kw: None)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: True)
    monkeypatch.setattr(
        install,
        "diagnose_installation",
        lambda **kw: install.DiagnosticReport.healthy_for(kw["context"]),
    )
    choices = install.ApplyChoices(agents=(), default_backend="codex")

    first_context = context_for(original)
    assert install.apply(context=first_context, choices=choices, environ={}) == 0
    stale_pointer = first_context.paths.current_pointer.read_text(encoding="utf-8")
    stale_manifest_path = install._manifest_path(first_context)
    stale_manifest = stale_manifest_path.read_text(encoding="utf-8")
    stale_pointer_payload = json.loads(stale_pointer)
    assert any(
        str(original) in value
        for value in stale_pointer_payload.values()
        if isinstance(value, str)
    )

    def contains_original(value: object) -> bool:
        if isinstance(value, str):
            return str(original) in value
        if isinstance(value, dict):
            return any(contains_original(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_original(item) for item in value)
        return False

    assert contains_original(json.loads(stale_manifest))

    standard_context = install.resolve_installation_context(
        mode="standard",
        source_root=original,
        development_root=None,
        platform=sys.platform,
        home=stable_home,
        environ={},
    )
    stable_canaries = {
        stable_home / ".codex" / "canary.bin": b"stable-codex\x00\xff",
        standard_context.paths.config_root / "canary.bin": b"stable-config\x00\xfe",
        standard_context.paths.state_root / "canary.bin": b"stable-state\x00\xfd",
    }
    for path, raw in stable_canaries.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    original.rename(moved)
    assert not original.exists()
    repaired_context = context_for(moved)
    assert install.apply(context=repaired_context, choices=choices, environ={}) == 0

    pointer_raw = repaired_context.paths.current_pointer.read_text(encoding="utf-8")
    pointer_payload = json.loads(pointer_raw)
    assert pointer_payload["schema_version"] == 3
    assert Path(pointer_payload["runtime_source"]).is_relative_to(moved)
    assert Path(pointer_payload["launcher_resources"]) == moved.resolve()
    context_path = Path(pointer_payload["installation_context"])
    context_raw = context_path.read_text(encoding="utf-8")
    context_payload = json.loads(context_raw)
    assert context_payload["source_root"] == str(moved.resolve())
    assert context_payload["development_root"] == str(moved.resolve())
    assert context_payload["installation_id"] == installation_id

    manifest_path = install._manifest_path(repaired_context)
    manifest_raw = manifest_path.read_text(encoding="utf-8")
    manifest_payload = json.loads(manifest_raw)
    assert str(original) not in manifest_raw
    assert manifest_payload["installation"] == {
        "mode": "development",
        "installation_id": installation_id,
        "development_root": str(moved.resolve()),
    }
    assert any(
        entry.get("path") == str(repaired_context.paths.current_pointer)
        for entry in manifest_payload["entries"]
    )
    loaded = load_current_pointer(
        runtime_root=repaired_context.paths.runtime_root,
        trusted_interpreter_roots=(),
    )
    assert loaded.installation_context == context_path
    for path, raw in stable_canaries.items():
        assert path.read_bytes() == raw


def test_interface_runs_in_process_when_runtime_module_is_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(install, "main", lambda argv: calls.append(argv) or 3)
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("current source must not restart")
        ),
    )

    assert install.Interface().run(["--help"]) == 3
    assert calls == [["--help"]]


def test_dry_run_stops_after_confirming_choices_without_effects(tmp_path, monkeypatch, capsys):
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
    assert names == []
    output = capsys.readouterr().out
    assert "Stage 1/5: Choose mode" in output
    assert "Stage 2/5: Confirm choices" in output
    assert "Stage 3/5" not in output


def test_successful_install_reports_exactly_five_stages_in_order(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(install, "_build_managed_runtime_candidate", lambda **kw: 0)
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: 0)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: True)
    monkeypatch.setattr(
        install,
        "diagnose_installation",
        lambda **kw: install.DiagnosticReport.healthy_for(kw["context"]),
    )

    status = install.run(
        home=tmp_path,
        non_interactive=True,
        dev_mode=False,
        agents=[],
        default_llm="claude",
        yes=True,
        environ={},
    )

    assert status == 0
    output = capsys.readouterr().out
    positions = [output.index(f"Stage {number}/5") for number in range(1, 6)]
    assert positions == sorted(positions)
    assert output.count("Stage ") == 5
    assert positions[3] < output.index("Famulus installation: healthy")


def test_dev_mode_requires_repo_path_non_interactively(tmp_path, monkeypatch):
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: None)
    monkeypatch.setattr(install.dev_link, "run", lambda **kw: None)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: None)

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
    repo_path.mkdir()

    status = install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=True, repo_path=repo_path, agents=["assistant"], default_llm="codex",
    )

    names = [name for name, _ in calls]
    assert status == 0
    assert names == []


def test_plugin_mode_uses_auto_derived_repo_root(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        install,
        "_preview_context_lines",
        lambda **kw: calls.append(kw) or (),
    )

    install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    preview_kwargs = calls[0]
    expected_repo_root = Path(install.__file__).resolve().parents[3]
    assert preview_kwargs["source_root"] == expected_repo_root


# ── Managed-runtime candidate wiring (Task 7) ────────────────────────────────


def test_non_interactive_install_uses_checked_in_core_lock(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: None)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: None)
    monkeypatch.setattr(install, "diagnose_installation", lambda **kw: install.DiagnosticReport.healthy_for(kw["context"]))
    monkeypatch.setattr(
        install.managed_runtime,
        "build_candidate_release",
        lambda **kwargs: calls.append(kwargs),
    )

    status = install.run(
        home=tmp_path, dry_run=False, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude", yes=True,
    )

    assert calls[0]["optional_module_ids"] == ()
    assert calls[0]["lock_input_path"].name == "requirements-core.in"
    assert calls[0]["lock_path"].name == "requirements-core.lock"
    assert calls[0]["uv_version"] == "0.11.29"


def test_non_interactive_install_rejects_optional_selection(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: None)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: None)
    monkeypatch.setattr(install, "diagnose_installation", lambda **kw: install.DiagnosticReport.healthy_for(kw["context"]))
    monkeypatch.setattr(
        install.managed_runtime,
        "build_candidate_release",
        lambda **kwargs: calls.append(kwargs),
    )

    status = install.run(
        home=tmp_path, dry_run=False, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude", yes=True,
        optional_modules=["pdf-to-markdown"],
    )

    assert status != 0
    assert calls == []


def test_interactive_install_prompts_for_optional_modules(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: None)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: None)
    monkeypatch.setattr(install, "diagnose_installation", lambda **kw: install.DiagnosticReport.healthy_for(kw["context"]))
    monkeypatch.setattr(
        install.managed_runtime,
        "build_candidate_release",
        lambda **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(install, "_prompt_optional_modules", lambda **kwargs: ["pdf-to-markdown"])

    status = install.run(
        home=tmp_path,
        dry_run=False,
        non_interactive=False,
        dev_mode=False,
        agents=[],
        default_llm="claude",
        yes=True,
    )

    assert status == 0
    assert calls[0]["optional_module_ids"] == ("pdf-to-markdown",)


def test_optional_module_prompt_names_packages_and_unavailable_estimates(monkeypatch, capsys):
    manifest = Path(install.__file__).resolve().parents[3] / "references" / "blueprint" / "runtime_dependencies.json"
    monkeypatch.setattr("builtins.input", lambda _: "")

    assert install._prompt_optional_modules(manifest_path=manifest, platform_name="linux") == []

    output = capsys.readouterr().out
    assert "pdf-to-markdown" in output
    assert "marker-pdf" in output
    assert "estimate unavailable" in output


def test_optional_module_prompt_reports_rough_known_total(monkeypatch, capsys):
    manifest = Path(install.__file__).resolve().parents[3] / "references" / "blueprint" / "runtime_dependencies.json"
    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(
        install.managed_runtime,
        "package_size_estimates",
        lambda packages, **kwargs: (
            install.managed_runtime.PackageSizeEstimate("marker-pdf", 120),
        ),
    )

    install._prompt_optional_modules(manifest_path=manifest, platform_name="linux")

    assert "rough download estimate: 120 bytes" in capsys.readouterr().out


def test_phase_entry_builds_candidate_before_scaffold(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append(("scaffold", kw)))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append(("launchers", kw)))
    monkeypatch.setattr(install, "diagnose_installation", lambda **kw: install.DiagnosticReport.healthy_for(kw["context"]))
    monkeypatch.setattr(
        install.managed_runtime,
        "build_candidate_release",
        lambda **kwargs: calls.append(("build_candidate_release", kwargs)),
    )

    status = install.run(
        home=tmp_path, dry_run=False, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude", yes=True,
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
        dev_mode=False, agents=[], default_llm="claude", yes=True,
    )

    assert status != 0
    assert "scaffold" not in [name for name, _ in calls]
    paths = resolve_famulus_paths(platform=sys.platform, home=tmp_path, environ={})
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
        dev_mode=False, agents=[], default_llm="claude", yes=True,
    )

    assert status != 0
    assert "scaffold" not in [name for name, _ in calls]
    paths = resolve_famulus_paths(platform=sys.platform, home=tmp_path, environ={})
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
        dev_mode=False, agents=[], default_llm="claude", yes=True,
    )

    assert status != 0
    assert "scaffold" not in [name for name, _ in calls]
    paths = resolve_famulus_paths(platform=sys.platform, home=tmp_path, environ={})
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
    paths = _resolve(platform=sys.platform, home=home, environ={})
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
