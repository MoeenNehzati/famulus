from __future__ import annotations

import json
import errno
import subprocess
import sys
import tomllib
from types import SimpleNamespace
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import assistant_access_probe as probe  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_unrelated_apply_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep focused tests local while retaining the product lifecycle entrypoints."""
    monkeypatch.setattr(
        probe.phase_entry,
        "_build_managed_runtime_candidate",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(
        probe.phase_entry,
        "_record_managed_runtime_state",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(probe.phase_entry.scaffold, "run", lambda **_kwargs: 0)
    monkeypatch.setattr(probe.phase_entry.launchers, "run", lambda **_kwargs: True)
    monkeypatch.setattr(
        probe.phase_entry,
        "diagnose_installation",
        lambda **_kwargs: SimpleNamespace(status="healthy"),
    )
    monkeypatch.setattr(probe.phase_entry, "render_diagnostic_text", lambda _report: "")
    monkeypatch.setattr(
        probe.install_uninstall,
        "_teardown_recurring_context",
        lambda *_args, **_kwargs: True,
    )


def _paths(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "assistant-access"
    return {
        "root": root,
        "home": root / "home",
        "state": root / "probe-state.json",
        "evidence": root / "assistant-access-linux.json",
        "source": Path(__file__).resolve().parents[4],
        "control": tmp_path / "assistant-access-control",
    }


def _prepare(paths: dict[str, Path]) -> dict[str, object]:
    result = probe.main(
        [
            "prepare",
            "--platform",
            "linux",
            "--source-root",
            str(paths["source"]),
            "--home",
            str(paths["home"]),
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
            "--control-root",
            str(paths["control"]),
        ]
    )
    assert result == 0
    return json.loads(paths["state"].read_text(encoding="utf-8"))


def test_prepare_and_restore_use_product_lifecycle_entrypoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    apply_calls = 0
    uninstall_calls = 0
    real_apply = probe.phase_entry.apply
    real_uninstall = probe.install_uninstall.uninstall_context

    def apply_spy(**kwargs: object) -> int:
        nonlocal apply_calls
        apply_calls += 1
        return real_apply(**kwargs)

    def uninstall_spy(**kwargs: object) -> object:
        nonlocal uninstall_calls
        uninstall_calls += 1
        return real_uninstall(**kwargs)

    monkeypatch.setattr(probe.phase_entry, "apply", apply_spy)
    monkeypatch.setattr(probe.install_uninstall, "uninstall_context", uninstall_spy)

    _prepare(paths)
    assert apply_calls == 2
    assert probe.main(
        [
            "restore",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 0
    assert uninstall_calls == 1


def test_restore_purges_full_fixture_runtime_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    runtime_root = paths["home"] / ".local" / "share" / "famulus" / "runtime"
    runtime_pointer = runtime_root / "current.json"
    runtime_release = runtime_root / "releases" / "fixture-release"
    resolver_root = runtime_root / "bootstrap" / "resolvers"
    uninstall_purge: list[bool] = []
    real_uninstall = probe.install_uninstall.uninstall_context

    def record_runtime(*, context: object, manifest: object) -> None:
        runtime_pointer.parent.mkdir(parents=True, exist_ok=True)
        runtime_pointer.write_text("{}\n", encoding="utf-8")
        for directory in (runtime_release, resolver_root):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "owned.txt").write_text("owned\n", encoding="utf-8")
        manifest.record("file", path=str(runtime_pointer), purge_only=True)
        manifest.record("tree", path=str(runtime_release), purge_only=True)
        manifest.record("tree", path=str(resolver_root), purge_only=True)

    def uninstall_spy(**kwargs: object) -> object:
        uninstall_purge.append(bool(kwargs["purge"]))
        return real_uninstall(**kwargs)

    monkeypatch.setattr(
        probe.phase_entry, "_record_managed_runtime_state", record_runtime
    )
    monkeypatch.setattr(probe.install_uninstall, "uninstall_context", uninstall_spy)
    state = _prepare(paths)
    assert all(path.exists() for path in (runtime_pointer, runtime_release, resolver_root))
    assert any(
        entry.get("purge_only")
        for entry in probe.Manifest(Path(state["manifest"])).entries
    )

    assert probe.main(
        [
            "restore",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 0
    assert uninstall_purge == [True]
    assert not any(path.exists() for path in (runtime_pointer, runtime_release, resolver_root))
    assert not Path(state["manifest"]).exists()


@pytest.mark.parametrize("broken_postcondition", ["policy", "local", "manifest"])
def test_restore_postcondition_failure_is_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    broken_postcondition: str,
) -> None:
    paths = _paths(tmp_path)
    state = _prepare(paths)
    real_uninstall = probe.install_uninstall.uninstall_context

    def corrupt_after_uninstall(**kwargs: object) -> object:
        report = real_uninstall(**kwargs)
        if broken_postcondition == "policy":
            codex_path = Path(state["codex_config"])
            raw = codex_path.read_text(encoding="utf-8")
            foreign_literal = json.dumps(str(state["foreign_codex_root"]))
            assert foreign_literal in raw
            codex_path.write_text(
                raw.replace(
                    foreign_literal,
                    json.dumps(str(paths["control"])),
                    1,
                ),
                encoding="utf-8",
            )
        elif broken_postcondition == "local":
            Path(state["claude_local_settings"]).write_text(
                '{"changed":true}\n', encoding="utf-8"
            )
        else:
            manifest = probe.Manifest(Path(state["manifest"]))
            context = kwargs["context"]
            manifest.bind_context(
                mode="standard",
                installation_id="standard",
                codex_home=context.codex_home,
                claude_home=context.claude_home,
            )
        return report

    monkeypatch.setattr(
        probe.install_uninstall, "uninstall_context", corrupt_after_uninstall
    )
    assert probe.main(
        [
            "restore",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 1
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    failure = evidence["evidence"][-1]
    assert failure["label"] == "config"
    assert failure["subject"] == "uninstall restoration"
    assert failure["status"] == "failed"


def test_prepare_records_recovery_and_failure_evidence_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)

    def fail_after_seed(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("synthetic apply failure")

    monkeypatch.setattr(probe.phase_entry, "apply", fail_after_seed)
    assert probe.main(
        [
            "prepare",
            "--platform",
            "linux",
            "--source-root",
            str(paths["source"]),
            "--home",
            str(paths["home"]),
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
            "--control-root",
            str(paths["control"]),
        ]
    ) == 1

    state = json.loads(paths["state"].read_text(encoding="utf-8"))
    assert state["phase"] == "prepare_failed"
    assert set(state["baseline"]) == {
        "manifest",
        "codex_config",
        "claude_settings",
        "claude_local_settings",
    }
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    assert evidence["evidence"][-1]["status"] == "failed"
    assert evidence["evidence"][-1]["subject"] == "install and reapply"

    assert probe.main(
        [
            "restore",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 0
    assert not (paths["home"] / ".codex" / "config.toml").exists()
    assert not (paths["home"] / ".claude" / "settings.json").exists()


def test_prepare_persists_baseline_before_seed_and_recovers_partial_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    real_seed = probe._seed_configuration
    real_apply = probe.phase_entry.apply
    apply_calls = 0

    def seed_after_state(context: object) -> tuple[Path, Path, bytes]:
        state = json.loads(paths["state"].read_text(encoding="utf-8"))
        evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
        assert state["phase"] == "baseline_recorded"
        assert evidence["evidence"] == []
        return real_seed(context)  # type: ignore[arg-type]

    def apply_then_fail(**kwargs: object) -> int:
        nonlocal apply_calls
        apply_calls += 1
        if apply_calls == 2:
            raise RuntimeError("synthetic reapply failure after mutation")
        return real_apply(**kwargs)

    monkeypatch.setattr(probe, "_seed_configuration", seed_after_state)
    monkeypatch.setattr(probe.phase_entry, "apply", apply_then_fail)
    assert probe.main(
        [
            "prepare",
            "--platform",
            "linux",
            "--source-root",
            str(paths["source"]),
            "--home",
            str(paths["home"]),
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
            "--control-root",
            str(paths["control"]),
        ]
    ) == 1
    assert (paths["home"] / ".codex" / "config.toml").exists()

    assert probe.main(
        [
            "restore",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 0
    assert not (paths["home"] / ".codex" / "config.toml").exists()
    assert not (paths["home"] / ".claude" / "settings.json").exists()
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    assert evidence["evidence"][-1]["details"]["failed_prepare_recovered"] is True


def test_failed_apply_recovers_an_empty_bound_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)

    def bind_then_fail(**kwargs: object) -> int:
        context = kwargs["context"]
        manifest = probe.Manifest(context.paths.install_state_root / "install-manifest.json")
        manifest.bind_context(
            mode="standard",
            installation_id="standard",
            codex_home=context.codex_home,
            claude_home=context.claude_home,
        )
        return 1

    monkeypatch.setattr(probe.phase_entry, "apply", bind_then_fail)
    assert probe.main(
        [
            "prepare",
            "--platform",
            "linux",
            "--source-root",
            str(paths["source"]),
            "--home",
            str(paths["home"]),
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
            "--control-root",
            str(paths["control"]),
        ]
    ) == 1
    state = json.loads(paths["state"].read_text(encoding="utf-8"))
    assert Path(state["manifest"]).exists()
    assert probe.main(
        [
            "restore",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 0
    assert not Path(state["manifest"]).exists()


def test_prepare_uses_an_oracle_independent_of_resolver_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(
        probe,
        "resolve_assistant_access_roots",
        lambda _context: (paths["home"] / "wrong",),
    )
    assert probe.main(
        [
            "prepare",
            "--platform",
            "linux",
            "--source-root",
            str(paths["source"]),
            "--home",
            str(paths["home"]),
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
            "--control-root",
            str(paths["control"]),
        ]
    ) == 1
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    assert evidence["evidence"][-1]["status"] == "failed"
    assert "independent canonical oracle" in evidence["evidence"][-1]["detail"]


def _assert_no_canaries(state: dict[str, object]) -> None:
    for value in [*state["allowed_roots"], state["control_root"]]:
        root = Path(value)
        if root.is_dir():
            assert list(root.glob(".famulus-assistant-access-canary-*")) == []


def test_prepare_probe_reapply_os_write_and_restore_are_structured_and_reversible(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    state = _prepare(paths)
    allowed = state["allowed_roots"]
    control = state["control_root"]

    codex = tomllib.loads(Path(state["codex_config"]).read_text(encoding="utf-8"))
    claude = json.loads(Path(state["claude_settings"]).read_text(encoding="utf-8"))
    assert codex["sandbox_workspace_write"]["writable_roots"] == [
        state["foreign_codex_root"],
        *allowed,
    ]
    assert claude["permissions"]["additionalDirectories"] == [
        state["foreign_claude_root"],
        *allowed,
    ]
    assert control not in codex["sandbox_workspace_write"]["writable_roots"]
    assert control not in claude["permissions"]["additionalDirectories"]

    assert probe.main(
        [
            "config-os-write",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 0
    _assert_no_canaries(state)

    assert probe.main(
        [
            "restore",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 0
    restored_codex = tomllib.loads(
        Path(state["codex_config"]).read_text(encoding="utf-8")
    )
    restored_claude = json.loads(
        Path(state["claude_settings"]).read_text(encoding="utf-8")
    )
    assert restored_codex["sandbox_workspace_write"]["writable_roots"] == [
        state["foreign_codex_root"]
    ]
    assert restored_claude["permissions"]["additionalDirectories"] == [
        state["foreign_claude_root"]
    ]
    assert Path(state["claude_local_settings"]).read_text(encoding="utf-8") == (
        '{"hooks":{"Notification":[]}}\n'
    )

    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    assert evidence["schema_version"] == 1
    assert evidence["platform"] == "linux"
    assert evidence["qualifications"] == {
        "claude_authenticated_access": "skipped",
        "codex_ide_app_enforcement": "unverified",
    }
    assert [(item["label"], item["status"]) for item in evidence["evidence"]] == [
        ("config", "passed"),
        ("config", "passed"),
        ("OS-write", "passed"),
        ("config", "passed"),
    ]
    assert evidence["evidence"][2]["details"]["control_attempted"] is False


def test_config_probe_rejects_control_in_policy_before_writing_canaries(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    state = _prepare(paths)
    settings = Path(state["claude_settings"])
    payload = json.loads(settings.read_text(encoding="utf-8"))
    payload["permissions"]["additionalDirectories"].append(state["control_root"])
    settings.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    assert probe.main(
        [
            "config-os-write",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 1
    _assert_no_canaries(state)
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    assert evidence["evidence"][-1]["label"] == "config"
    assert evidence["evidence"][-1]["status"] == "failed"
    assert "control root" in evidence["evidence"][-1]["detail"]


def test_os_write_probe_cleans_prior_canaries_after_partial_failure(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    state = _prepare(paths)
    blocked_root = Path(state["allowed_roots"][1])
    blocked_root.parent.mkdir(parents=True, exist_ok=True)
    blocked_root.write_text("not a directory\n", encoding="utf-8")

    assert probe.main(
        [
            "config-os-write",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 1
    _assert_no_canaries(state)
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    assert evidence["evidence"][-1]["label"] == "OS-write"
    assert evidence["evidence"][-1]["status"] == "failed"


def test_os_write_discovers_canary_when_create_raises_after_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    state = _prepare(paths)
    real_create = probe._create_canary

    def create_then_raise(root: Path, token: str) -> Path:
        real_create(root, token)
        raise OSError("synthetic failure after create")

    monkeypatch.setattr(probe, "_create_canary", create_then_raise)
    with pytest.raises(probe.ProbeError, match="synthetic failure after create"):
        probe.config_os_write(
            state_path=paths["state"],
            evidence_path=paths["evidence"],
            canary_token="create-then-raise",
        )

    _assert_no_canaries(state)
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    assert evidence["evidence"][-1]["status"] == "failed"
    assert evidence["evidence"][-1]["details"]["canaries_cleaned"] is True


def test_cleanup_failure_is_propagated_and_never_claimed_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    state = _prepare(paths)
    real_unlink = Path.unlink

    def reject_canary(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.startswith(".famulus-assistant-access-canary-"):
            raise OSError("synthetic unlink failure")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", reject_canary)
    assert probe.main(
        [
            "config-os-write",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 1
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    result = evidence["evidence"][-1]
    assert result["status"] == "failed"
    assert "unlink failure" in result["detail"]
    assert result.get("details", {}).get("canaries_cleaned") is not True
    for root in state["allowed_roots"]:
        for canary in Path(root).glob(".famulus-assistant-access-canary-*"):
            real_unlink(canary)


def test_host_enforcement_requires_allowed_success_and_control_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    state = _prepare(paths)
    real_create = probe._create_canary

    def deny_control(root: Path, token: str) -> Path:
        if root == Path(state["control_root"]):
            raise PermissionError("sandbox denied synthetic sibling")
        return real_create(root, token)

    monkeypatch.setattr(probe, "_create_canary", deny_control)
    assert probe.main(
        [
            "host-enforcement",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 0
    _assert_no_canaries(state)
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    result = evidence["evidence"][-1]
    assert (result["label"], result["status"]) == ("host-enforcement", "passed")
    assert result["details"] == {
        "allowed_write": "succeeded",
        "control_write": "denied",
        "canaries_cleaned": True,
    }


def test_host_enforcement_does_not_unlink_absent_canary_on_read_only_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    state = _prepare(paths)
    control = Path(state["control_root"])
    real_create = probe._create_canary
    real_unlink = Path.unlink

    def deny_control(root: Path, token: str) -> Path:
        if root == control:
            raise PermissionError("sandbox denied synthetic sibling")
        return real_create(root, token)

    def read_only_control_unlink(
        path: Path, *, missing_ok: bool = False
    ) -> None:
        if path.parent == control:
            raise OSError(errno.EROFS, "read-only file system", str(path))
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(probe, "_create_canary", deny_control)
    monkeypatch.setattr(Path, "unlink", read_only_control_unlink)
    assert probe.main(
        [
            "host-enforcement",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 0
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    assert evidence["evidence"][-1]["status"] == "passed"


def test_host_enforcement_fails_and_cleans_when_control_is_writable(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    state = _prepare(paths)

    assert probe.main(
        [
            "host-enforcement",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 1
    _assert_no_canaries(state)
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    assert evidence["evidence"][-1]["label"] == "host-enforcement"
    assert evidence["evidence"][-1]["status"] == "failed"
    assert "control write unexpectedly succeeded" in evidence["evidence"][-1]["detail"]


def test_evidence_rejects_labels_that_overstate_qualification(tmp_path: Path) -> None:
    with pytest.raises(probe.ProbeError, match="evidence label"):
        probe.append_evidence(
            tmp_path / "evidence.json",
            platform_name="linux",
            item={
                "label": "Claude-authenticated",
                "subject": "claude access",
                "status": "passed",
                "detail": "not actually qualified",
            },
        )


def test_claude_doctor_is_client_health_without_authentication_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    _prepare(paths)

    monkeypatch.setattr(probe.shutil, "which", lambda client: "/tools/claude")

    def completed(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "2.1.237 (Claude Code)\n", "")
        assert argv[-1] == "doctor"
        return subprocess.CompletedProcess(argv, 0, "No installation issues found.\n", "")

    monkeypatch.setattr(probe.subprocess, "run", completed)
    assert probe.main(
        [
            "client-install-health",
            "--client",
            "claude",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 0

    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    result = evidence["evidence"][-1]
    assert (result["label"], result["status"]) == (
        "client-install-health",
        "passed",
    )
    assert result["details"] == {
        "authentication": "not tested",
        "doctor": "passed",
        "version": "2.1.237",
    }
    assert evidence["qualifications"]["claude_authenticated_access"] == "skipped"


def test_codex_client_health_rejects_an_unpinned_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    _prepare(paths)
    monkeypatch.setattr(probe.shutil, "which", lambda client: "/tools/codex")
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, "codex-cli 0.150.0\n", ""
        ),
    )

    assert probe.main(
        [
            "client-install-health",
            "--client",
            "codex",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 1
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    assert evidence["evidence"][-1]["label"] == "client-install-health"
    assert evidence["evidence"][-1]["status"] == "failed"
    assert "0.149.0" in evidence["evidence"][-1]["detail"]


def test_codex_client_health_accepts_exact_version_with_unrelated_stderr_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    _prepare(paths)
    monkeypatch.setattr(probe.shutil, "which", lambda _client: "/tools/codex")
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            "codex-cli 0.149.0\n",
            "WARNING: could not create PATH aliases\n",
        ),
    )

    assert probe.main(
        [
            "client-install-health",
            "--client",
            "codex",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 0
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    assert evidence["evidence"][-1]["details"]["version"] == "0.149.0"


def test_codex_client_health_rejects_expected_version_as_a_substring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    _prepare(paths)
    monkeypatch.setattr(probe.shutil, "which", lambda _client: "/tools/codex")
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv, 0, "codex-cli 0.149.0-dev\n", ""
        ),
    )
    assert probe.main(
        [
            "client-install-health",
            "--client",
            "codex",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 1


def _claude_event_stream(target: Path, *, denied: bool) -> str:
    tool_id = "toolu_famulus_access"
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": "Write",
                        "input": {"file_path": str(target)},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "is_error": denied,
                        "content": "policy denied" if denied else "written",
                    }
                ]
            },
        },
    ]
    return "".join(json.dumps(event) + "\n" for event in events)


def test_authenticated_claude_probe_records_evidence_only_after_real_canary_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    state = _prepare(paths)
    monkeypatch.setattr(probe.shutil, "which", lambda client: "/tools/claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-credential")

    def exercise(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        prompt = argv[-1]
        target = Path(prompt.split("TARGET=", 1)[1].splitlines()[0])
        if target.parent == Path(state["allowed_roots"][0]):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"famulus assistant access probe\n")
            return subprocess.CompletedProcess(
                argv, 0, _claude_event_stream(target, denied=False), ""
            )
        assert target.parent == Path(state["control_root"])
        return subprocess.CompletedProcess(
            argv, 0, _claude_event_stream(target, denied=True), ""
        )

    monkeypatch.setattr(probe.subprocess, "run", exercise)
    assert probe.main(
        [
            "claude-authenticated",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 0
    _assert_no_canaries(state)
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    assert evidence["qualifications"]["claude_authenticated_access"] == "run"
    assert evidence["evidence"][-1]["label"] == "host-enforcement"
    assert evidence["evidence"][-1]["subject"] == "Claude authenticated access"


def test_authenticated_claude_requires_explicit_ci_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    _prepare(paths)
    monkeypatch.setattr(probe.shutil, "which", lambda _client: "/tools/claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Claude must not run without credentials"),
    )
    assert probe.main(
        [
            "claude-authenticated",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 1


def test_authenticated_claude_rejects_unstructured_model_text_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    state = _prepare(paths)
    monkeypatch.setattr(probe.shutil, "which", lambda _client: "/tools/claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-credential")

    def exercise(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        prompt = argv[-1]
        target = Path(prompt.split("TARGET=", 1)[1].splitlines()[0])
        if target.parent == Path(state["allowed_roots"][0]):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("famulus assistant access probe\n", encoding="utf-8")
            return subprocess.CompletedProcess(
                argv, 0, _claude_event_stream(target, denied=False), ""
            )
        return subprocess.CompletedProcess(
            argv, 0, '{"result":"permission denied"}\n', ""
        )

    monkeypatch.setattr(probe.subprocess, "run", exercise)
    assert probe.main(
        [
            "claude-authenticated",
            "--state",
            str(paths["state"]),
            "--evidence",
            str(paths["evidence"]),
        ]
    ) == 1
