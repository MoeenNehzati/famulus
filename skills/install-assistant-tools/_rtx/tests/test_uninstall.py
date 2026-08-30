"""Tests for uninstall.py — manifest-based reversal of install side effects.

The installed state is produced by REALLY running the installers
(dev_link.run + scaffold.run) against a fake repo and
sandboxed homes, so a genuine manifest drives the uninstall — the only
supported path. A missing manifest is a hard error (tested), never a
heuristic guess.
"""
from __future__ import annotations

import io
import hashlib
import json
import subprocess
import sys
import stat
import types
from argparse import Namespace
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest
from test_support.git_repository import GitTestRepository

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from install_test_utils import (
    REPO_ROOT,
    assert_default_bin_dir_matches_famulus_paths,
    can_create_symlink,
    python_test_env,
    run_command,
)
from officina.install.assistant_access import resolve_assistant_access_roots
from officina.install.context import (
    load_or_create_development_installation_id,
    resolve_installation_context,
)
from officina.recurring import native as recurring_native

SCRIPTS = REPO_ROOT / "skills" / "install-assistant-tools" / "_rtx"
sys.path.insert(0, str(SCRIPTS))

if __package__ and __package__.count('.') >= 1:
    from .._state_record import Manifest, manifest_path
else:
    from _state_record import Manifest, manifest_path  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .._assistant_access_config import reconcile_assistant_access
else:
    from _assistant_access_config import reconcile_assistant_access  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .. import _config_bridge as dev_link
else:
    import _config_bridge as dev_link  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .. import _install_scaffold as scaffold
else:
    import _install_scaffold as scaffold  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .. import _install_uninstall as uninstall
else:
    import _install_uninstall as uninstall  # noqa: E402


@pytest.fixture(autouse=True)
def _empty_native_scheduler_inventory(monkeypatch, tmp_path):
    empty = recurring_native._NativeInventory(True, ())
    monkeypatch.setattr(recurring_native, "_systemd_unit_inventory", lambda *args, **kwargs: empty)
    monkeypatch.setattr(recurring_native, "_launchd_label_inventory", lambda *args, **kwargs: empty)
    monkeypatch.setattr(recurring_native, "_windows_task_inventory", lambda *args, **kwargs: empty)
    monkeypatch.setattr(recurring_native, "_read_crontab", lambda: "")
    monkeypatch.setattr(
        uninstall, "native_registration_root", lambda context, platform: tmp_path / "native"
    )


def test_manifest_preserves_identity_recorded_file_when_user_modified_it(tmp_path):
    path = tmp_path / "launchers.json"
    installed = b'{"schema_version": 1, "default_backend": "claude"}\n'
    path.write_bytes(installed)
    manifest = Manifest(tmp_path / "manifest.json")
    manifest.record(
        "file",
        path=str(path),
        sha256=hashlib.sha256(installed).hexdigest(),
        preserve_if_modified=True,
    )
    path.write_text('{"schema_version": 1, "default_backend": "codex"}\n')
    report = uninstall.Report()

    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=True,
        no_pip=True,
        no_git_hooks=True,
    )

    assert path.exists()
    assert json.loads(path.read_text())["default_backend"] == "codex"
    assert any(
        status == "skipped" and "modified" in detail
        for status, _action, detail in report.items
    )


def _standard_context(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    return resolve_installation_context(
        mode="standard",
        source_root=source,
        development_root=None,
        platform=sys.platform,
        home=home,
        environ={},
    )


def _development_context(tmp_path: Path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    home = tmp_path / "stable-home"
    home.mkdir()
    installation_id = load_or_create_development_installation_id(
        checkout,
        platform=sys.platform,
        home=home,
        environ={},
    )
    return resolve_installation_context(
        mode="development",
        source_root=checkout,
        development_root=checkout,
        platform=sys.platform,
        home=home,
        environ={},
        installation_id=installation_id,
    )


def test_access_replay_deletes_only_created_config_files_that_become_empty(
    tmp_path: Path,
) -> None:
    context = _standard_context(tmp_path)
    local_settings = context.claude_home / "settings.local.json"
    local_settings.parent.mkdir(parents=True)
    local_settings.write_bytes(b'{"hooks": {}}\n')
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)
    report = uninstall.Report()

    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert not (context.codex_home / "config.toml").exists()
    assert not (context.claude_home / "settings.json").exists()
    assert local_settings.read_bytes() == b'{"hooks": {}}\n'
    assert not manifest.path.exists()


def test_access_replay_removes_owned_values_while_preserving_unrelated_edits(
    tmp_path: Path,
) -> None:
    context = _standard_context(tmp_path)
    codex = context.codex_home / "config.toml"
    claude = context.claude_home / "settings.json"
    codex.parent.mkdir(parents=True)
    claude.parent.mkdir(parents=True)
    codex.write_text(
        '[sandbox_workspace_write]\nwritable_roots = ["/foreign"]\n',
        encoding="utf-8",
    )
    claude.write_text(
        '{"theme":"dark","permissions":{"additionalDirectories":["/foreign"]}}\n',
        encoding="utf-8",
    )
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)
    codex.write_text(
        codex.read_text(encoding="utf-8").replace(
            "  # >>> famulus-access >>>", '  "/added-later",\n  # >>> famulus-access >>>'
        )
        + 'user_note = "keep"\n',
        encoding="utf-8",
    )
    payload = json.loads(claude.read_text(encoding="utf-8"))
    payload["theme"] = "light"
    payload["unrelated"] = {"keep": True}
    claude.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    report = uninstall.Report()

    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert "/foreign" in codex.read_text(encoding="utf-8")
    assert "/added-later" in codex.read_text(encoding="utf-8")
    assert 'user_note = "keep"' in codex.read_text(encoding="utf-8")
    claude_payload = json.loads(claude.read_text(encoding="utf-8"))
    assert claude_payload["permissions"]["additionalDirectories"] == ["/foreign"]
    assert claude_payload["theme"] == "light"
    assert claude_payload["unrelated"] == {"keep": True}


def test_access_replay_preserves_modified_owned_content_and_keeps_manifest_entry(
    tmp_path: Path,
) -> None:
    context = _standard_context(tmp_path)
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)
    codex = context.codex_home / "config.toml"
    codex.write_text(
        codex.read_text(encoding="utf-8").replace(".assistant-logs", ".assistant-logs-edited", 1),
        encoding="utf-8",
    )
    claude = context.claude_home / "settings.json"
    payload = json.loads(claude.read_text(encoding="utf-8"))
    payload["permissions"]["additionalDirectories"][0] += "-edited"
    claude.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    report = uninstall.Report()

    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert ".assistant-logs-edited" in codex.read_text(encoding="utf-8")
    assert "-edited" in claude.read_text(encoding="utf-8")
    remaining = Manifest(manifest.path)
    assert {
        entry["kind"]
        for entry in remaining.entries
        if entry["kind"] in {"codex_access_array_block", "json_array_values"}
    } == {"codex_access_array_block", "json_array_values"}
    assert any(status == "skipped" and "modified" in detail for status, _action, detail in report.items)


def test_access_replay_persists_exact_uninstall_intent_before_target_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _standard_context(tmp_path)
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)

    def stop_before_mutation(plan: object) -> None:
        loaded = Manifest(manifest.path)
        entry = next(
            item
            for item in loaded.entries
            if item["kind"] == "codex_access_array_block"
        )
        assert entry["uninstall_transaction"] == "pending"
        path = context.codex_home / "config.toml"
        assert entry["uninstall_pre_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert entry["uninstall_post_sha256"] is None
        raise RuntimeError("stop before uninstall mutation")

    monkeypatch.setattr(uninstall.codex_toml, "apply_access_plan", stop_before_mutation)

    with pytest.raises(RuntimeError, match="stop before uninstall mutation"):
        uninstall.replay_manifest(
            manifest,
            uninstall.Report(),
            dry_run=False,
            purge=False,
            no_pip=True,
            no_git_hooks=True,
            context=context,
        )

    assert (context.codex_home / "config.toml").exists()


def test_access_replay_rejects_external_edit_immediately_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _standard_context(tmp_path)
    codex = context.codex_home / "config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text(
        '[sandbox_workspace_write]\nwritable_roots = ["/foreign"]\n',
        encoding="utf-8",
    )
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)
    external = b'model = "external user edit"\n'
    real_write = uninstall.codex_toml.apply_access_plan

    def edit_then_write(plan: object) -> None:
        codex.write_bytes(external)
        real_write(plan)

    monkeypatch.setattr(uninstall.codex_toml, "apply_access_plan", edit_then_write)
    report = uninstall.Report()

    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert codex.read_bytes() == external
    assert report.failed
    remaining = Manifest(manifest.path)
    assert any(item["kind"] == "codex_access_array_block" for item in remaining.entries)


def test_access_replay_rejects_external_edit_immediately_before_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _standard_context(tmp_path)
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)
    codex = context.codex_home / "config.toml"
    external = b'model = "external user edit"\n'
    real_write = uninstall.codex_toml.apply_access_plan

    def edit_then_unlink(plan: object) -> None:
        codex.write_bytes(external)
        real_write(plan)

    monkeypatch.setattr(uninstall.codex_toml, "apply_access_plan", edit_then_unlink)
    report = uninstall.Report()

    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert codex.read_bytes() == external
    assert report.failed


def test_access_replay_recovers_rewrite_completed_before_manifest_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _standard_context(tmp_path)
    codex = context.codex_home / "config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text(
        '[sandbox_workspace_write]\nwritable_roots = ["/foreign"]\n',
        encoding="utf-8",
    )
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)

    def crash_before_manifest_removal(entry: dict) -> None:
        if entry.get("kind") == "codex_access_array_block":
            raise RuntimeError("crash before manifest removal")
        Manifest.remove(manifest, entry)

    monkeypatch.setattr(manifest, "remove", crash_before_manifest_removal)
    with pytest.raises(RuntimeError, match="crash before manifest removal"):
        uninstall.replay_manifest(
            manifest,
            uninstall.Report(),
            dry_run=False,
            purge=False,
            no_pip=True,
            no_git_hooks=True,
            context=context,
        )

    pending = Manifest(manifest.path)
    entry = next(item for item in pending.entries if item["kind"] == "codex_access_array_block")
    assert entry["uninstall_transaction"] == "pending"
    assert hashlib.sha256(codex.read_bytes()).hexdigest() == entry["uninstall_post_sha256"]

    uninstall.replay_manifest(
        pending,
        uninstall.Report(),
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert "/foreign" in codex.read_text(encoding="utf-8")
    assert uninstall.ACCESS_BEGIN not in codex.read_text(encoding="utf-8")
    if pending.path.exists():
        assert all(
            item["kind"] != "codex_access_array_block"
            for item in Manifest(pending.path).entries
        )


def test_access_replay_preserves_state_outside_pending_uninstall_pre_or_post_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _standard_context(tmp_path)
    codex = context.codex_home / "config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text(
        '[sandbox_workspace_write]\nwritable_roots = ["/foreign"]\n',
        encoding="utf-8",
    )
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)

    monkeypatch.setattr(
        manifest,
        "remove",
        lambda _entry: (_ for _ in ()).throw(RuntimeError("crash before removal")),
    )
    with pytest.raises(RuntimeError, match="crash before removal"):
        uninstall.replay_manifest(
            manifest,
            uninstall.Report(),
            dry_run=False,
            purge=False,
            no_pip=True,
            no_git_hooks=True,
            context=context,
        )

    external = b'model = "third state"\n'
    codex.write_bytes(external)
    pending = Manifest(manifest.path)
    report = uninstall.Report()
    uninstall.replay_manifest(
        pending,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert report.failed
    assert codex.read_bytes() == external
    assert any(
        item["kind"] == "codex_access_array_block"
        for item in Manifest(pending.path).entries
    )


@pytest.mark.parametrize(
    "settings_template",
    [
        '{{"permissions": {{}}, "permissions": {{"additionalDirectories": {roots}}}}}',
        '{{"permissions": {{"additionalDirectories": [], "additionalDirectories": {roots}}}}}',
        '{{"unrelated": {{"nested": 1, "nested": 2}}, "permissions": {{"additionalDirectories": {roots}}}}}',
    ],
)
def test_access_replay_rejects_duplicate_json_object_keys_recursively(
    tmp_path: Path, settings_template: str
) -> None:
    context = _standard_context(tmp_path)
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)
    settings = context.claude_home / "settings.json"
    roots = json.dumps(
        next(item for item in manifest.entries if item["kind"] == "json_array_values")[
            "introduced"
        ]
    )
    duplicate = settings_template.format(roots=roots).encode("utf-8")
    settings.write_bytes(duplicate)
    report = uninstall.Report()

    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert settings.read_bytes() == duplicate
    assert report.failed
    assert uninstall.ACCESS_BEGIN in (
        context.codex_home / "config.toml"
    ).read_text(encoding="utf-8")
    assert any(
        item["kind"] == "json_array_values" for item in Manifest(manifest.path).entries
    )


@pytest.mark.parametrize(
    "replacement",
    [
        '{"permissions": []}\n',
        '{"permissions": {"additionalDirectories": "wrong"}}\n',
        '{"permissions": {"additionalDirectories": ["/ok", 3]}}\n',
    ],
)
def test_access_replay_preflights_complete_claude_structure_before_either_target_changes(
    tmp_path: Path, replacement: str
) -> None:
    context = _standard_context(tmp_path)
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)
    codex = context.codex_home / "config.toml"
    claude = context.claude_home / "settings.json"
    claude.write_text(replacement, encoding="utf-8")
    codex_before = codex.read_bytes()
    claude_before = claude.read_bytes()
    report = uninstall.Report()

    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert report.failed
    assert codex.read_bytes() == codex_before
    assert claude.read_bytes() == claude_before
    remaining = Manifest(manifest.path)
    assert {
        item["kind"]
        for item in remaining.entries
        if item["kind"] in {"codex_access_array_block", "json_array_values"}
    } == {"codex_access_array_block", "json_array_values"}


def test_access_replay_rejects_claude_edit_after_frozen_preflight_without_codex_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _standard_context(tmp_path)
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)
    codex = context.codex_home / "config.toml"
    claude = context.claude_home / "settings.json"
    codex_before = codex.read_bytes()
    manifest_before = manifest.path.read_bytes()
    real_preflight = uninstall._assistant_access_preflight

    def edit_after_preflight(*args: object, **kwargs: object):
        result = real_preflight(*args, **kwargs)
        claude.write_bytes(b"{malformed")
        return result

    monkeypatch.setattr(uninstall, "_assistant_access_preflight", edit_after_preflight)
    report = uninstall.Report()

    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert report.failed
    assert codex.read_bytes() == codex_before
    assert claude.read_bytes() == b"{malformed"
    assert manifest.path.read_bytes() == manifest_before


def test_access_replay_keeps_pair_ownership_until_both_targets_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _standard_context(tmp_path)
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)
    real_apply = uninstall.codex_toml.apply_access_plan

    def fail_codex(_plan: object) -> None:
        raise uninstall.toml_io.TomlManagedArrayError("injected Codex failure")

    monkeypatch.setattr(uninstall.codex_toml, "apply_access_plan", fail_codex)
    first_report = uninstall.Report()
    uninstall.replay_manifest(
        manifest,
        first_report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert first_report.failed
    remaining = Manifest(manifest.path)
    assert {
        entry["kind"]
        for entry in remaining.entries
        if entry["kind"] in {"codex_access_array_block", "json_array_values"}
    } == {"codex_access_array_block", "json_array_values"}

    monkeypatch.setattr(uninstall.codex_toml, "apply_access_plan", real_apply)
    retry_report = uninstall.Report()
    uninstall.replay_manifest(
        remaining,
        retry_report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert not retry_report.failed
    assert not manifest.path.exists()


def test_access_replay_refreshes_stale_empty_manifest_inside_context_lock(
    tmp_path: Path,
) -> None:
    context = _standard_context(tmp_path)
    installed = Manifest(context.paths.install_state_root / "install-manifest.json")
    installed.bind_context(mode="standard", installation_id="standard")
    stale = Manifest(installed.path)
    reconcile_assistant_access(context, installed)
    codex = context.codex_home / "config.toml"
    claude = context.claude_home / "settings.json"
    report = uninstall.Report()

    uninstall.replay_manifest(
        stale,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert not report.failed
    assert not codex.exists()
    assert not claude.exists()
    assert not stale.path.exists()


@pytest.mark.parametrize("context_factory", [_standard_context, _development_context])
@pytest.mark.parametrize(
    "manifest_edit",
    ["duplicate-codex", "duplicate-claude", "wrong-codex", "wrong-claude"],
)
def test_access_replay_requires_one_exact_context_owned_entry_per_target_before_mutation(
    tmp_path: Path, context_factory, manifest_edit: str
) -> None:
    context = context_factory(tmp_path)
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(
        mode=context.mode,
        installation_id=context.installation_id,
        development_root=context.development_root,
    )
    reconcile_assistant_access(context, manifest)
    kind = (
        "codex_access_array_block"
        if manifest_edit.endswith("codex")
        else "json_array_values"
    )
    entry = next(item for item in manifest.entries if item["kind"] == kind)
    if manifest_edit.startswith("duplicate"):
        manifest.entries.append(dict(entry))
    else:
        entry["path"] = str(tmp_path / f"unexpected-{kind}")
    manifest.save()
    codex = context.codex_home / "config.toml"
    claude = context.claude_home / "settings.json"
    codex_before = codex.read_bytes()
    claude_before = claude.read_bytes()
    manifest_before = manifest.path.read_bytes()
    report = uninstall.Report()

    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert report.failed
    assert codex.read_bytes() == codex_before
    assert claude.read_bytes() == claude_before
    assert manifest.path.read_bytes() == manifest_before


@pytest.mark.parametrize("context_factory", [_standard_context, _development_context])
@pytest.mark.parametrize("target_name", ["codex", "claude"])
def test_access_replay_rejects_symlinked_config_before_either_target_changes(
    tmp_path: Path, context_factory, target_name: str
) -> None:
    context = context_factory(tmp_path)
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(
        mode=context.mode,
        installation_id=context.installation_id,
        development_root=context.development_root,
    )
    reconcile_assistant_access(context, manifest)
    codex = context.codex_home / "config.toml"
    claude = context.claude_home / "settings.json"
    target = codex if target_name == "codex" else claude
    external = tmp_path / f"external-uninstall-{context.mode}-{target_name}"
    external.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(external)
    except OSError as exc:
        # famulus-skip: category=capability-unavailable; reason=this host denied creation of the target-file symlink; alternate=symlink-capable hosts run this case while regular-file pair preflight tests cover no-mutation ordering
        pytest.skip(f"symlinks unavailable: {exc}")
    other = claude if target == codex else codex
    other_before = other.read_bytes()
    external_before = external.read_bytes()
    manifest_before = manifest.path.read_bytes()
    report = uninstall.Report()

    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert report.failed
    assert target.is_symlink()
    assert external.read_bytes() == external_before
    assert other.read_bytes() == other_before
    assert manifest.path.read_bytes() == manifest_before


def test_access_replay_recovery_rejects_symlinked_pending_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _standard_context(tmp_path)
    codex = context.codex_home / "config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_bytes(
        b'[sandbox_workspace_write]\nwritable_roots = ["/foreign"]\n'
    )
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)
    claude = context.claude_home / "settings.json"

    def crash_after_codex_rewrite(entry: dict) -> None:
        if entry.get("kind") == "codex_access_array_block":
            raise RuntimeError("stop after Codex rewrite")
        Manifest.remove(manifest, entry)

    monkeypatch.setattr(manifest, "remove", crash_after_codex_rewrite)
    with pytest.raises(RuntimeError, match="stop after Codex rewrite"):
        uninstall.replay_manifest(
            manifest,
            uninstall.Report(),
            dry_run=False,
            purge=False,
            no_pip=True,
            no_git_hooks=True,
            context=context,
        )

    external = tmp_path / "external-pending-codex"
    external.write_bytes(codex.read_bytes())
    codex.unlink()
    try:
        codex.symlink_to(external)
    except OSError as exc:
        # famulus-skip: category=capability-unavailable; reason=this host denied creation of the pending-target symlink; alternate=symlink-capable hosts run this case while pending-identity tests cover durable non-symlink recovery
        pytest.skip(f"symlinks unavailable: {exc}")
    pending = Manifest(manifest.path)
    manifest_before = pending.path.read_bytes()
    assert not claude.exists()
    external_before = external.read_bytes()
    report = uninstall.Report()

    uninstall.replay_manifest(
        pending,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert report.failed
    assert codex.is_symlink()
    assert external.read_bytes() == external_before
    assert not claude.exists()
    assert pending.path.read_bytes() == manifest_before


@pytest.mark.parametrize("copied_location", ["outside-array", "multiline-string"])
def test_access_replay_rejects_exact_codex_block_outside_selected_writable_roots(
    tmp_path: Path, copied_location: str
) -> None:
    context = _standard_context(tmp_path)
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)
    codex = context.codex_home / "config.toml"
    claude = context.claude_home / "settings.json"
    lines = codex.read_text(encoding="utf-8").splitlines(keepends=True)
    begin = next(index for index, line in enumerate(lines) if line.strip() == uninstall.ACCESS_BEGIN)
    end = next(index for index, line in enumerate(lines) if line.strip() == uninstall.ACCESS_END)
    recorded_block = "".join(lines[begin : end + 1])
    roots = [str(path) for path in resolve_assistant_access_roots(context)]
    selected = (
        "[sandbox_workspace_write]\nwritable_roots = [\n"
        + "".join(f"  {json.dumps(root)},\n" for root in roots)
        + "]\n"
    )
    if copied_location == "outside-array":
        edited = "unrelated = [\n" + recorded_block + "]\n" + selected
    else:
        edited = 'description = """\n' + recorded_block + '"""\n' + selected
    codex.write_text(edited, encoding="utf-8")
    codex_before = codex.read_bytes()
    claude_before = claude.read_bytes()
    report = uninstall.Report()

    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert report.failed
    assert codex.read_bytes() == codex_before
    assert claude.read_bytes() == claude_before
    remaining = Manifest(manifest.path)
    assert {
        item["kind"]
        for item in remaining.entries
        if item["kind"] in {"codex_access_array_block", "json_array_values"}
    } == {"codex_access_array_block", "json_array_values"}


# famulus-skip: category=platform-contract; reason=Windows st_mode does not represent the secured DACL; alternate=native DACL behavior is covered by atomic-files Windows tests
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode preservation contract")
def test_access_replay_preserves_full_file_mode_on_rewrite(tmp_path: Path) -> None:
    context = _standard_context(tmp_path)
    claude = context.claude_home / "settings.json"
    claude.parent.mkdir(parents=True)
    claude.write_text('{"theme": "dark"}\n', encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    reconcile_assistant_access(context, manifest)
    claude.chmod(0o1640)

    uninstall.replay_manifest(
        manifest,
        uninstall.Report(),
        dry_run=False,
        purge=False,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert stat.S_IMODE(claude.stat().st_mode) == 0o1640


def test_context_uninstall_auto_tears_down_recurring_before_manifest_replay(
    tmp_path, monkeypatch
):
    context = _standard_context(tmp_path)
    owned = context.paths.user_bin / "dispatcher"
    owned.parent.mkdir(parents=True)
    owned.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    manifest.record("file", path=str(owned))
    registrations = context.paths.recurring_state_root / "registrations.json"
    registrations.parent.mkdir(parents=True)
    registrations.write_text(
        json.dumps({"schema_version": 1, "installation_id": "standard", "registrations": ["daily"]}),
        encoding="utf-8",
    )
    statuses = iter(
        (
            recurring_native.RegistrationNamespaceStatus(
                True, True, ("daily",)
            ),
            recurring_native.RegistrationNamespaceStatus(True, False),
        )
    )
    monkeypatch.setattr(
        uninstall, "inspect_registration_namespace", lambda **_kwargs: next(statuses)
    )
    teardown_calls = []
    monkeypatch.setattr(
        uninstall,
        "remove_installation_context",
        lambda selected, platform: teardown_calls.append((selected, platform)),
        raising=False,
    )

    report = uninstall.uninstall_context(
        context=context,
        platform=sys.platform,
        home=tmp_path / "home",
        environ={},
        purge=False,
        dry_run=False,
        no_pip=True,
        no_git_hooks=True,
    )

    assert not report.failed
    assert teardown_calls == [(context, sys.platform)]
    assert not owned.exists()
    assert any(
        status == "removed" and "recurring registrations" in action
        for status, action, _detail in report.items
    )


def test_context_uninstall_holds_lifecycle_lock_through_teardown_and_replay(
    tmp_path, monkeypatch
):
    context = _standard_context(tmp_path)
    owned = context.paths.user_bin / "dispatcher"
    owned.parent.mkdir(parents=True)
    owned.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    manifest.record("file", path=str(owned))
    statuses = iter(
        (
            recurring_native.RegistrationNamespaceStatus(True, True, ("daily",)),
            recurring_native.RegistrationNamespaceStatus(True, False),
        )
    )
    events = []

    @contextmanager
    def fake_lock(path, *, allowed_root):
        label = "recurring" if path.name == "lifecycle.lock" else "assistant"
        events.append(f"{label}-enter")
        yield
        events.append(f"{label}-exit")

    monkeypatch.setattr(uninstall, "exclusive_file_lock", fake_lock, raising=False)
    monkeypatch.setattr(
        uninstall, "inspect_registration_namespace", lambda **_kwargs: next(statuses)
    )
    monkeypatch.setattr(
        uninstall,
        "remove_installation_context",
        lambda _selected, _platform: events.append("teardown"),
    )
    monkeypatch.setattr(
        uninstall,
        "_replay_manifest_unlocked",
        lambda *_args, **_kwargs: events.append("replay"),
    )

    report = uninstall.uninstall_context(
        context=context, platform=sys.platform, home=tmp_path / "home", environ={},
        purge=False, dry_run=False, no_pip=True, no_git_hooks=True,
    )

    assert not report.failed
    assert events == [
        "recurring-enter",
        "assistant-enter",
        "teardown",
        "replay",
        "assistant-exit",
        "recurring-exit",
    ]


def test_context_uninstall_revalidates_manifest_after_lifecycle_lock(
    tmp_path, monkeypatch
):
    context = _standard_context(tmp_path)
    owned = context.paths.user_bin / "dispatcher"
    owned.parent.mkdir(parents=True)
    owned.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    manifest.record("file", path=str(owned))

    @contextmanager
    def replace_manifest_on_lock(path, *, allowed_root):
        if path.name == "lifecycle.lock":
            replacement = Manifest(manifest.path)
            replacement.installation["installation_id"] = "foreign"
            replacement.save()
        yield

    monkeypatch.setattr(
        uninstall, "exclusive_file_lock", replace_manifest_on_lock, raising=False
    )
    monkeypatch.setattr(
        uninstall,
        "inspect_registration_namespace",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("inventory must follow locked manifest validation")
        ),
    )

    report = uninstall.uninstall_context(
        context=context, platform=sys.platform, home=tmp_path / "home", environ={},
        purge=False, dry_run=False, no_pip=True, no_git_hooks=True,
    )

    assert report.failed
    assert owned.read_text(encoding="utf-8") == "owned\n"


def test_context_uninstall_aborts_manifest_replay_when_auto_teardown_fails(
    tmp_path, monkeypatch
):
    context = _standard_context(tmp_path)
    owned = context.paths.user_bin / "dispatcher"
    owned.parent.mkdir(parents=True)
    owned.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    manifest.record("file", path=str(owned))
    before = manifest.path.read_bytes()
    monkeypatch.setattr(
        uninstall,
        "inspect_registration_namespace",
        lambda **_kwargs: recurring_native.RegistrationNamespaceStatus(
            True, True, ("daily",)
        ),
    )
    teardown_calls = []

    def fail_teardown(selected, platform):
        teardown_calls.append((selected, platform))
        raise RuntimeError("scheduler teardown failed")

    monkeypatch.setattr(
        uninstall, "remove_installation_context", fail_teardown, raising=False
    )

    report = uninstall.uninstall_context(
        context=context, platform=sys.platform, home=tmp_path / "home", environ={},
        purge=False, dry_run=False, no_pip=True, no_git_hooks=True,
    )

    assert teardown_calls == [(context, sys.platform)]
    assert report.failed
    assert owned.read_text(encoding="utf-8") == "owned\n"
    assert manifest.path.read_bytes() == before
    assert any(
        "scheduler teardown failed" in detail
        for status, _action, detail in report.items
        if status == "FAILED"
    )


def test_context_uninstall_requires_empty_namespace_after_auto_teardown(
    tmp_path, monkeypatch
):
    context = _standard_context(tmp_path)
    owned = context.paths.user_bin / "dispatcher"
    owned.parent.mkdir(parents=True)
    owned.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    manifest.record("file", path=str(owned))
    status = recurring_native.RegistrationNamespaceStatus(
        True, True, ("daily",)
    )
    monkeypatch.setattr(
        uninstall, "inspect_registration_namespace", lambda **_kwargs: status
    )
    monkeypatch.setattr(
        uninstall,
        "remove_installation_context",
        lambda _selected, _platform: None,
        raising=False,
    )

    report = uninstall.uninstall_context(
        context=context, platform=sys.platform, home=tmp_path / "home", environ={},
        purge=False, dry_run=False, no_pip=True, no_git_hooks=True,
    )

    assert report.failed
    assert owned.exists()
    assert any(
        "still present" in detail
        for status, _action, detail in report.items
        if status == "FAILED"
    )


def test_context_uninstall_aborts_when_auto_teardown_postcheck_is_uncertain(
    tmp_path, monkeypatch
):
    context = _standard_context(tmp_path)
    owned = context.paths.user_bin / "dispatcher"
    owned.parent.mkdir(parents=True)
    owned.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    manifest.record("file", path=str(owned))
    statuses = iter(
        (
            recurring_native.RegistrationNamespaceStatus(
                True, True, ("daily",)
            ),
            recurring_native.RegistrationNamespaceStatus(
                False, True, detail="scheduler inventory unavailable"
            ),
        )
    )
    monkeypatch.setattr(
        uninstall, "inspect_registration_namespace", lambda **_kwargs: next(statuses)
    )
    monkeypatch.setattr(
        uninstall,
        "remove_installation_context",
        lambda _selected, _platform: None,
    )

    report = uninstall.uninstall_context(
        context=context, platform=sys.platform, home=tmp_path / "home", environ={},
        purge=False, dry_run=False, no_pip=True, no_git_hooks=True,
    )

    assert report.failed
    assert owned.exists()
    assert any(
        "scheduler inventory unavailable" in detail
        for status, _action, detail in report.items
        if status == "FAILED"
    )


def test_context_uninstall_checks_development_containment_before_teardown(
    tmp_path, monkeypatch
):
    context = _development_context(tmp_path)
    outside = tmp_path / "outside-owned"
    outside.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(
        mode="development",
        installation_id=context.installation_id,
        development_root=context.development_root,
    )
    manifest.record("file", path=str(outside))

    def must_not_inspect(**_kwargs):
        raise AssertionError("containment must precede recurring inventory")

    def must_not_teardown(_selected, _platform):
        raise AssertionError("containment must precede recurring teardown")

    monkeypatch.setattr(uninstall, "inspect_registration_namespace", must_not_inspect)
    monkeypatch.setattr(uninstall, "remove_installation_context", must_not_teardown)

    report = uninstall.uninstall_context(
        context=context,
        platform=sys.platform,
        home=tmp_path / "stable-home",
        environ={},
        purge=False,
        dry_run=False,
        no_pip=True,
        no_git_hooks=True,
    )

    assert report.failed
    assert outside.read_text(encoding="utf-8") == "owned\n"


def test_context_uninstall_dry_run_reports_recurring_teardown_without_calling_it(
    tmp_path, monkeypatch
):
    context = _standard_context(tmp_path)
    owned = context.paths.user_bin / "dispatcher"
    owned.parent.mkdir(parents=True)
    owned.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    manifest.record("file", path=str(owned))
    before = manifest.path.read_bytes()
    monkeypatch.setattr(
        uninstall,
        "inspect_registration_namespace",
        lambda **_kwargs: recurring_native.RegistrationNamespaceStatus(
            True, True, ("daily",)
        ),
    )

    def must_not_teardown(_selected, _platform):
        raise AssertionError("dry-run must not tear down recurring state")

    monkeypatch.setattr(
        uninstall, "remove_installation_context", must_not_teardown, raising=False
    )

    report = uninstall.uninstall_context(
        context=context, platform=sys.platform, home=tmp_path / "home", environ={},
        purge=False, dry_run=True, no_pip=True, no_git_hooks=True,
    )

    assert not report.failed
    assert owned.read_text(encoding="utf-8") == "owned\n"
    assert manifest.path.read_bytes() == before
    assert any(
        status == "removed"
        and "recurring registrations" in action
        and detail == "(dry-run)"
        for status, action, detail in report.items
    )


def test_context_uninstall_accepts_empty_registration_summary(tmp_path):
    context = _standard_context(tmp_path)
    owned = context.paths.user_bin / "dispatcher"
    owned.parent.mkdir(parents=True)
    owned.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    manifest.record("file", path=str(owned))
    registrations = context.paths.recurring_state_root / "registrations.json"
    registrations.parent.mkdir(parents=True)
    registrations.write_text(
        '{"schema_version": 1, "installation_id": "standard", "registrations": []}\n',
        encoding="utf-8",
    )

    report = uninstall.uninstall_context(
        context=context, platform=sys.platform, home=tmp_path / "home", environ={},
        purge=False, dry_run=False, no_pip=True, no_git_hooks=True,
    )

    assert not report.failed
    assert not owned.exists()
    assert registrations.exists()


def test_context_uninstall_fails_closed_on_malformed_registration_summary(tmp_path):
    context = _standard_context(tmp_path)
    owned = context.paths.user_bin / "dispatcher"
    owned.parent.mkdir(parents=True)
    owned.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    manifest.record("file", path=str(owned))
    registrations = context.paths.recurring_state_root / "registrations.json"
    registrations.parent.mkdir(parents=True)
    registrations.write_text('{"registrations": {}}\n', encoding="utf-8")

    report = uninstall.uninstall_context(
        context=context, platform=sys.platform, home=tmp_path / "home", environ={},
        purge=True, dry_run=False, no_pip=True, no_git_hooks=True,
    )

    assert report.failed
    assert owned.exists()


def test_context_uninstall_rejects_manifest_for_another_installation(tmp_path):
    context = _standard_context(tmp_path)
    owned = context.paths.user_bin / "dispatcher"
    owned.parent.mkdir(parents=True)
    owned.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(
        mode="development",
        installation_id="dev-0123456789abcdef0123456789abcdef",
        development_root=tmp_path / "checkout",
    )
    manifest.record("file", path=str(owned))

    report = uninstall.uninstall_context(
        context=context,
        platform=sys.platform,
        home=tmp_path / "home",
        environ={},
        purge=False,
        dry_run=False,
        no_pip=True,
        no_git_hooks=True,
    )

    assert report.failed
    assert owned.exists()


def test_ordinary_uninstall_preserves_purge_only_config_then_purge_removes_it(tmp_path):
    context = _standard_context(tmp_path)
    launcher_config = context.paths.config_root / "launchers.json"
    launcher_config.parent.mkdir(parents=True)
    launcher_config.write_text(
        '{"schema_version": 1, "default_backend": "claude"}\n', encoding="utf-8"
    )
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    manifest.record("file", path=str(launcher_config), purge_only=True)

    ordinary = uninstall.uninstall_context(
        context=context,
        platform=sys.platform,
        home=tmp_path / "home",
        environ={},
        purge=False,
        dry_run=False,
        no_pip=True,
        no_git_hooks=True,
    )
    assert not ordinary.failed
    assert launcher_config.exists()

    purged = uninstall.uninstall_context(
        context=context,
        platform=sys.platform,
        home=tmp_path / "home",
        environ={},
        purge=True,
        dry_run=False,
        no_pip=True,
        no_git_hooks=True,
    )
    assert not purged.failed
    assert not launcher_config.exists()


def test_git_hooks_restore_prior_value_only_while_installer_value_matches(tmp_path):
    repo = make_fake_repo(tmp_path)
    # famulus-raw-git: category=hooks; reason=seed the installer-owned real hooksPath for uninstall replay
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath", ".githooks"], check=True
    )
    entry = {
        "kind": "git_hooks_path",
        "path": str(repo),
        "installed_value": ".githooks",
        "prior_value": "custom-hooks",
    }
    report = uninstall.Report()

    assert uninstall.remove_manifest_git_hooks(entry, report, dry_run=False)
    # famulus-raw-git: category=hooks; reason=observe the real hooksPath restored by uninstall
    restored = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert restored.stdout.strip() == "custom-hooks"

    # famulus-raw-git: category=hooks; reason=replace the real hooksPath to prove user changes are preserved
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath", "user-hooks"], check=True
    )
    report = uninstall.Report()
    assert uninstall.remove_manifest_git_hooks(entry, report, dry_run=False)
    # famulus-raw-git: category=hooks; reason=observe the real user-modified hooksPath after uninstall
    preserved = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert preserved.stdout.strip() == "user-hooks"


def test_development_uninstall_rechecks_symlink_boundaries_before_mutation(tmp_path):
    context = _development_context(tmp_path)
    owned = context.development_root / ".famulus" / "bin" / "dispatcher"
    owned.parent.mkdir(parents=True)
    owned.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(
        mode="development",
        installation_id=context.installation_id,
        development_root=context.development_root,
    )
    manifest.record("file", path=str(owned))
    homes = context.development_root / ".famulus" / "homes"
    outside = tmp_path / "outside"
    outside.mkdir()
    homes.symlink_to(outside, target_is_directory=True)

    report = uninstall.uninstall_context(
        context=context,
        platform=sys.platform,
        home=tmp_path / "stable-home",
        environ={},
        purge=True,
        dry_run=False,
        no_pip=True,
        no_git_hooks=True,
    )

    assert report.failed
    assert owned.exists()


def test_development_containment_does_not_follow_owned_leaf_symlink(tmp_path):
    context = _development_context(tmp_path)
    checkout = context.development_root
    assert checkout is not None
    target = checkout / "skills" / "demo"
    target.mkdir(parents=True)
    link = checkout / ".famulus" / "homes" / "codex" / "skills" / "demo"
    link.parent.mkdir(parents=True)
    link.symlink_to(target, target_is_directory=True)

    assert uninstall._development_entry_is_contained(
        {"kind": "symlink", "path": str(link), "target": str(target)},
        context,
    )


def test_uninstall_removes_access_before_other_block_in_same_codex_config(tmp_path):
    context = _development_context(tmp_path)
    codex = context.codex_home / "config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text(
        "# >>> skill-system-hooks >>>\n"
        "hook = \"owned\"\n"
        "# <<< skill-system-hooks <<<\n",
        encoding="utf-8",
    )
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(
        mode="development",
        installation_id=context.installation_id,
        development_root=context.development_root,
    )
    manifest.record(
        "marker_block",
        path=str(codex),
        begin="# >>> skill-system-hooks >>>",
        end="# <<< skill-system-hooks <<<",
    )
    reconcile_assistant_access(context, manifest)

    report = uninstall.Report()
    uninstall.replay_manifest(
        manifest,
        report,
        dry_run=False,
        purge=True,
        no_pip=True,
        no_git_hooks=True,
        context=context,
    )

    assert not report.failed
    assert not codex.exists()
    assert not manifest.path.exists()


def test_development_purge_preserves_install_id_recurring_state_and_tracked_adapters(tmp_path):
    context = _development_context(tmp_path)
    checkout = context.development_root
    assert checkout is not None
    (checkout / ".envrc").write_text("dirty adapter\n", encoding="utf-8")
    (checkout / "tools").mkdir()
    (checkout / "tools" / "dev-code").write_text("dirty launcher\n", encoding="utf-8")
    unrelated = checkout / "unrelated.txt"
    unrelated.write_text("dirty user work\n", encoding="utf-8")
    recurring = context.paths.recurring_state_root / "history" / "run.json"
    recurring.parent.mkdir(parents=True)
    recurring.write_text("{}\n", encoding="utf-8")
    owned = checkout / ".famulus" / "bin" / "dispatcher"
    owned.parent.mkdir(parents=True)
    owned.write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(
        mode="development",
        installation_id=context.installation_id,
        development_root=checkout,
    )
    manifest.record("file", path=str(owned))
    manifest.record("file", path=str(recurring), purge_only=True)

    report = uninstall.uninstall_context(
        context=context,
        platform=sys.platform,
        home=tmp_path / "stable-home",
        environ={},
        purge=True,
        dry_run=False,
        no_pip=True,
        no_git_hooks=True,
    )

    assert not report.failed
    assert not owned.exists()
    assert (checkout / ".famulus" / "install-id").read_text().strip() == context.installation_id
    assert recurring.exists()
    assert (checkout / ".envrc").read_text() == "dirty adapter\n"
    assert (checkout / "tools" / "dev-code").read_text() == "dirty launcher\n"
    assert unrelated.read_text() == "dirty user work\n"


def test_manifest_replay_persists_progress_and_retry_is_idempotent(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    manifest = Manifest(tmp_path / "manifest.json")
    manifest.record("file", path=str(first))
    manifest.record("file", path=str(second))
    real_remove = uninstall.remove_manifest_file

    def interrupt_second(entry, report, dry_run):
        if entry["path"] == str(second):
            report.add("FAILED", f"generated file: {second}", "injected interruption")
            return False
        return real_remove(entry, report, dry_run)

    monkeypatch.setattr(uninstall, "remove_manifest_file", interrupt_second)
    first_report = uninstall.Report()
    uninstall.replay_manifest(
        manifest, first_report, dry_run=False, purge=True, no_pip=True, no_git_hooks=True
    )
    assert not first.exists()
    assert second.exists()
    assert [entry["path"] for entry in Manifest(manifest.path).entries] == [str(second)]

    monkeypatch.setattr(uninstall, "remove_manifest_file", real_remove)
    retry = Manifest(manifest.path)
    retry_report = uninstall.Report()
    uninstall.replay_manifest(
        retry, retry_report, dry_run=False, purge=True, no_pip=True, no_git_hooks=True
    )
    assert not second.exists()
    assert not manifest.path.exists()


def test_crlf_marker_block_replay_removes_block_and_settles_entry(tmp_path):
    rc = tmp_path / ".bashrc"
    rc.write_bytes(
        b"user\r\n\r\n# >>> assistant-tools >>>\r\nexport AI=/repo\r\n"
        b"# <<< assistant-tools <<<\r\n"
    )
    manifest = Manifest(tmp_path / "manifest.json")
    manifest.record(
        "marker_block",
        path=str(rc),
        begin="# >>> assistant-tools >>>",
        end="# <<< assistant-tools <<<",
    )

    report = uninstall.Report()
    uninstall.replay_manifest(
        manifest, report, dry_run=False, purge=False, no_pip=True, no_git_hooks=True
    )

    assert rc.read_bytes() == b"user\r\n"
    assert not manifest.path.exists()


def test_marker_replay_removes_only_identity_matched_span_and_owned_separator(tmp_path):
    rc = tmp_path / ".bashrc"
    first = (
        b"# >>> assistant-tools >>>\r\nexport AI=/owned\r\n"
        b"# <<< assistant-tools <<<\r\n"
    )
    later = (
        b"middle\r\n\r\n# >>> assistant-tools >>>\r\nuser modified\r\n"
        b"# <<< assistant-tools <<<\r\ntail\r\n"
        b"# >>> assistant-tools >>>\r\nunmatched trailing bytes\x00\xff"
    )
    rc.write_bytes(b"user\r\n\r\n" + first + later)
    manifest = Manifest(tmp_path / "manifest.json")
    manifest.record(
        "marker_block", path=str(rc),
        begin="# >>> assistant-tools >>>", end="# <<< assistant-tools <<<",
    )

    uninstall.replay_manifest(
        manifest, uninstall.Report(), dry_run=False, purge=False,
        no_pip=True, no_git_hooks=True,
    )

    assert rc.read_bytes() == b"user\r\n" + later


def test_marker_replay_preserves_ambiguous_identical_duplicate_blocks(tmp_path):
    rc = tmp_path / ".bashrc"
    block = (
        b"# >>> assistant-tools >>>\nexport AI=/same\n"
        b"# <<< assistant-tools <<<\n"
    )
    original = b"user\n\n" + block + b"middle\n\n" + block + b"tail\n"
    rc.write_bytes(original)
    manifest = Manifest(tmp_path / "manifest.json")
    manifest.record(
        "marker_block", path=str(rc),
        begin="# >>> assistant-tools >>>", end="# <<< assistant-tools <<<",
    )

    uninstall.replay_manifest(
        manifest, uninstall.Report(), dry_run=False, purge=False,
        no_pip=True, no_git_hooks=True,
    )

    assert rc.read_bytes() == original
    assert manifest.path.exists()


def test_pending_windows_path_replay_reconciles_exact_insert_but_preserves_modified(
    monkeypatch,
):
    bin_dir = r"C:\Famulus\Bin"
    prior = r"C:\Windows"
    installed = f"{bin_dir};{prior}"
    state = {"PATH": installed, "type": 7}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(), KEY_READ=1, KEY_WRITE=2,
        OpenKey=lambda *_args: Key(),
        QueryValueEx=lambda _key, _name: (state["PATH"], state["type"]),
        SetValueEx=lambda _key, _name, _reserved, value_type, value: state.update(
            PATH=value, type=value_type
        ),
        DeleteValue=lambda *_args: None,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(uninstall.sys, "platform", "win32")
    entry = {
        "kind": "registry_env", "path": bin_dir, "names": ["PATH"],
        "path_inserted": True, "transaction_state": "pending",
        "path_value": bin_dir, "value_type": 7, "prior_path": prior,
        "prior_path_sha256": hashlib.sha256(prior.encode()).hexdigest(),
        "installed_path_sha256": hashlib.sha256(installed.encode()).hexdigest(),
    }

    assert uninstall.remove_registry_env(entry, uninstall.Report(), dry_run=False)
    assert state["PATH"] == prior

    state["PATH"] = f"C:\\Foreign;{installed}"
    report = uninstall.Report()
    assert not uninstall.remove_registry_env(entry, report, dry_run=False)
    assert state["PATH"] == f"C:\\Foreign;{installed}"


def test_windows_registry_replay_removes_owned_path_and_preserves_user_edits(
    tmp_path, monkeypatch
):
    bin_dir = r"C:\Famulus\Bin"
    prior = r"C:\Windows;C:\Tools"
    state = {"PATH": rf"C:\New;{bin_dir};C:\Windows;C:\Tools;C:\Tail", "type": 7}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(), KEY_READ=1, KEY_WRITE=2,
        OpenKey=lambda *_args: Key(),
        QueryValueEx=lambda _key, _name: (state["PATH"], state["type"]),
        SetValueEx=lambda _key, _name, _reserved, value_type, value: state.update(
            PATH=value, type=value_type
        ),
        DeleteValue=lambda *_args: None,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(uninstall.sys, "platform", "win32")
    installed = f"{bin_dir};{prior}"
    entry = {
        "kind": "registry_env",
        "path": bin_dir,
        "names": ["PATH"],
        "path_inserted": True,
        "path_value": bin_dir,
        "value_type": 7,
        "prior_path": prior,
        "prior_path_sha256": hashlib.sha256(prior.encode("utf-8")).hexdigest(),
        "installed_path_sha256": hashlib.sha256(installed.encode("utf-8")).hexdigest(),
    }
    report = uninstall.Report()

    assert uninstall.remove_registry_env(entry, report, dry_run=False)
    assert state["PATH"] == r"C:\New;C:\Windows;C:\Tools;C:\Tail"


def test_windows_registry_replay_preserves_path_when_identity_record_is_invalid(
    monkeypatch,
):
    state = {"PATH": r"C:\Famulus\Bin;C:\Windows", "type": 7}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(), KEY_READ=1, KEY_WRITE=2,
        OpenKey=lambda *_args: Key(),
        QueryValueEx=lambda _key, _name: (state["PATH"], state["type"]),
        SetValueEx=lambda *_args: (_ for _ in ()).throw(AssertionError("must preserve")),
        DeleteValue=lambda *_args: None,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(uninstall.sys, "platform", "win32")
    entry = {
        "kind": "registry_env", "path": r"C:\Famulus\Bin", "names": ["PATH"],
        "path_inserted": True, "path_value": r"C:\Famulus\Bin", "value_type": 7,
        "prior_path": r"C:\Windows", "prior_path_sha256": "wrong",
        "installed_path_sha256": "wrong",
    }

    assert uninstall.remove_registry_env(entry, uninstall.Report(), dry_run=False)
    assert state["PATH"] == r"C:\Famulus\Bin;C:\Windows"


def test_purge_removes_exact_resolver_tree_but_preserves_modified_tree(tmp_path):
    context = _standard_context(tmp_path)
    resolver = context.paths.runtime_root / "bootstrap" / "resolvers"
    (resolver / "v1").mkdir(parents=True)
    (resolver / "v1" / "launch.py").write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    manifest.record("tree", path=str(resolver), purge_only=True)

    report = uninstall.uninstall_context(
        context=context, platform=sys.platform, home=tmp_path / "home", environ={},
        purge=True, dry_run=False, no_pip=True, no_git_hooks=True,
    )
    assert not report.failed
    assert not resolver.exists()

    (resolver / "v1").mkdir(parents=True)
    (resolver / "v1" / "launch.py").write_text("owned\n", encoding="utf-8")
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    manifest.record("tree", path=str(resolver), purge_only=True)
    (resolver / "foreign.txt").write_text("user change\n", encoding="utf-8")
    report = uninstall.uninstall_context(
        context=context, platform=sys.platform, home=tmp_path / "home", environ={},
        purge=True, dry_run=False, no_pip=True, no_git_hooks=True,
    )
    assert not report.failed
    assert resolver.exists()


def test_windows_main_resolves_home_before_platform_roots(tmp_path, monkeypatch):
    home = tmp_path / "selected-home"
    repo = tmp_path / "repo"
    repo.mkdir()
    seen = {}
    monkeypatch.setattr(uninstall.sys, "platform", "win32")
    monkeypatch.setattr(
        uninstall,
        "parse_args",
        lambda: Namespace(
            mode="standard", checkout=None, home=str(home), repo_root=str(repo),
            purge=False, dry_run=True, no_pip=True, no_git_hooks=True,
            claude_home=None, codex_home=None, bin_dir=None, shell_rc=None,
            system_shell_rc=None, no_system_shell_rc=True,
        ),
    )

    def record_context(**kwargs):
        seen.update(kwargs)
        return uninstall.Report()

    monkeypatch.setattr(uninstall, "uninstall_context", record_context)
    with pytest.raises(SystemExit) as stopped:
        uninstall.main()

    assert stopped.value.code == 0
    assert seen["home"] == home.resolve()
    assert seen["environ"] == {
        "APPDATA": str(home.resolve() / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home.resolve() / "AppData" / "Local"),
    }

UNINSTALL = SCRIPTS / "_install_uninstall.py"

BLOCK_BEGIN = "# >>> assistant-tools >>>"
BLOCK_END = "# <<< assistant-tools <<<"
HOOKS_BLOCK_BEGIN = "# >>> skill-system-hooks >>>"
HOOKS_BLOCK_END = "# <<< skill-system-hooks <<<"

# registry stub with one real binding so managed hook entries exist
_REGISTRY_STUB = """\
class _Binding:
    def __init__(self, event, argv):
        self.event = event
        self.matcher = None
        self.argv = argv


class _Hook:
    def install_binding(self, host, repo_root):
        return _Binding(
            "SessionStart" if host == "claude" else "session_start",
            ("python3", f"{repo_root}/llmhooks/stub_hook.py", f"--{host}"),
        )


def hooks_for_host(host):
    return (_Hook(),)
"""

# famulus-skip: category=capability-unavailable; reason=uninstall fixture exercises symlink preservation and removal; alternate=manifest and Windows launcher tests cover non-symlink installs
pytestmark = pytest.mark.skipif(not can_create_symlink(), reason="symlinks unavailable")


def make_fake_repo(root: Path) -> Path:
    """Minimal fake repo: uninstall must never run against the real one
    (it removes repo-scoped artifacts like recurring-tasks env.sh)."""
    repo = root / "repo"
    GitTestRepository.create(repo)
    for d in ("references", "agents"):
        (repo / d).mkdir(parents=True)
    (repo / "skills" / "repo-skill").mkdir(parents=True)
    (repo / "skills" / "repo-skill" / "SKILL.md").write_text("# repo skill\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("# fake\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# fake\n", encoding="utf-8")

    profiles = repo / "profiles"
    profiles.mkdir()
    (profiles / "assistant.config.toml").write_text('model = "a"\n', encoding="utf-8")
    (profiles / "collab.config.toml").write_text('model = "b"\n', encoding="utf-8")

    src_bin = repo / "skills" / "install-assistant-tools" / "_rtx/assets/bin"
    src_bin.mkdir(parents=True)
    for name in ("_agent_launch.py", "assistant", "collab", "coauthor", "tmux-workspace",
                 "assistant.bat", "collab.bat", "coauthor.bat"):
        (src_bin / name).write_text("#!/bin/bash\n", encoding="utf-8")

    rt_scripts = repo / "skills" / "recurring-tasks" / "_rtx"
    rt_scripts.mkdir(parents=True)
    (rt_scripts / "env.sh").write_text("export PATH=fake:$PATH\n", encoding="utf-8")

    (repo / ".githooks").mkdir()
    (repo / ".githooks" / "pre-commit").write_text("#!/bin/bash\n", encoding="utf-8")

    (repo / "llmhooks").mkdir()
    (repo / "llmhooks" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "llmhooks" / "registry.py").write_text(_REGISTRY_STUB, encoding="utf-8")
    (repo / "llmhooks" / "stub_hook.py").write_text("print('hi')\n", encoding="utf-8")

    return repo


def make_installed_state(root: Path) -> dict[str, Path]:
    """Real install into sandboxed homes against the fake repo."""
    repo = make_fake_repo(root)
    home = root / "home"
    claude_home = home / ".claude"
    codex_home = home / ".codex"
    bin_dir = home / "Documents" / "_rtx" / "bin"
    for d in (claude_home, codex_home, bin_dir):
        d.mkdir(parents=True)
    codex_system = codex_home / "skills" / ".system"
    codex_system.mkdir(parents=True)
    (codex_system / "keep.txt").write_text("system\n", encoding="utf-8")

    # user-owned content that must survive uninstall untouched
    shell_rc = home / ".bashrc"
    shell_rc.write_text("# user line before\n", encoding="utf-8")
    (codex_home / "config.toml").write_text('model = "user-choice"\n', encoding="utf-8")
    user_hook_entry = {"hooks": [{"type": "command", "command": "echo user-hook"}]}
    (claude_home / "settings.local.json").write_text(
        json.dumps(
            {"hooks": {"SessionStart": [user_hook_entry]}, "permissions": {"allow": ["Bash(ls:*)"]}},
            indent=2,
        ),
        encoding="utf-8",
    )
    (claude_home / "personal.config.toml").write_text("mine\n", encoding="utf-8")
    foreign_target = root / "foreign"
    foreign_target.mkdir()
    (claude_home / "foreign-link").symlink_to(foreign_target)

    saved_path = list(sys.path)
    saved_llmhooks = {
        name: mod for name, mod in sys.modules.items()
        if name == "llmhooks" or name.startswith("llmhooks.")
    }
    try:
        with redirect_stdout(io.StringIO()):
            dev_link.run(
                home=home, repo_root=repo,
                claude_home=claude_home, codex_home=codex_home,
            )
            scaffold.run(
                repo_root=repo, home=home, bin_dir=bin_dir, shell_rc=shell_rc,
                environ={},
            )
    finally:
        sys.path[:] = saved_path
        for name in [n for n in sys.modules if n == "llmhooks" or n.startswith("llmhooks.")]:
            del sys.modules[name]
        sys.modules.update(saved_llmhooks)

    return {
        "home": home,
        "claude_home": claude_home,
        "codex_home": codex_home,
        "bin_dir": bin_dir,
        "shell_rc": shell_rc,
        "repo": repo,
    }


def _uninstall_args(paths: dict[str, Path], *extra: str) -> list[str]:
    """Build the shared CLI argument contract used by both invocation modes."""
    return [
        "--mode", "standard",
        "--home", str(paths["home"]),
        "--claude-home", str(paths["claude_home"]),
        "--codex-home", str(paths["codex_home"]),
        "--bin-dir", str(paths["bin_dir"]),
        "--shell-rc", str(paths["shell_rc"]),
        # never point uninstall at the real repo: it removes repo-scoped
        # artifacts (recurring-tasks env.sh, git hooksPath)
        "--repo-root", str(paths["repo"]),
        "--no-system-shell-rc",
        "--no-pip",
        "--no-git-hooks",
        *extra,
    ]


def _publish_context_bound_manifest(paths: dict[str, Path]) -> None:
    """Migrate this legacy fixture's recorded entries into the selected context."""
    legacy = Manifest(manifest_path(paths["home"]))
    if not legacy.entries:
        return
    context = resolve_installation_context(
        mode="standard",
        source_root=paths["repo"],
        development_root=None,
        platform=sys.platform,
        home=paths["home"],
        environ={},
    )
    current = Manifest(context.paths.install_state_root / "install-manifest.json")
    current.entries = list(legacy.entries)
    current.bind_context(mode="standard", installation_id="standard")


def run_uninstall(paths: dict[str, Path], *extra: str, check: bool = True):
    """Exercise parser/main on a fresh tree; the report test retains executable smoke coverage."""
    _publish_context_bound_manifest(paths)
    args = _uninstall_args(paths, *extra)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        patch.object(sys, "argv", [str(UNINSTALL), *args]),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        try:
            uninstall.main()
        except SystemExit as exc:
            returncode = int(exc.code or 0)
        else:
            returncode = 0

    result = subprocess.CompletedProcess(
        [sys.executable, str(UNINSTALL), *args],
        returncode,
        stdout.getvalue(),
        stderr.getvalue(),
    )
    if check and returncode != 0:
        raise AssertionError(
            f"uninstall exited {returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def run_uninstall_subprocess(paths: dict[str, Path], *extra: str, check: bool = True):
    """Exercise the entrypoint while retaining the test's isolated native inventory."""
    return run_uninstall(paths, *extra, check=check)


@pytest.fixture()
def installed(tmp_path: Path) -> dict[str, Path]:
    return make_installed_state(tmp_path)


def test_default_bin_dir_is_not_under_documents(tmp_path):
    assert_default_bin_dir_matches_famulus_paths(uninstall.default_bin_dir, tmp_path)


def test_removes_repo_symlinks_from_homes(installed):
    codex_skills = installed["codex_home"] / "skills"
    assert codex_skills.is_dir() and not codex_skills.is_symlink()
    assert (codex_skills / "repo-skill").is_symlink()

    run_uninstall(installed)
    for name in ("skills", "references", "agents", "CLAUDE.md"):
        assert not (installed["claude_home"] / name).is_symlink(), name
    assert codex_skills.is_dir() and not codex_skills.is_symlink()
    assert not (codex_skills / "repo-skill").exists()
    assert (codex_skills / ".system" / "keep.txt").read_text(encoding="utf-8") == "system\n"
    assert not (installed["codex_home"] / "AGENTS.md").is_symlink()


def test_general_uninstall_preserves_optional_profile_state(installed):
    optional = installed["claude_home"] / "assistant.config.toml"
    optional.write_text("optional feature\n", encoding="utf-8")
    run_uninstall(installed, "--purge")
    assert optional.read_text(encoding="utf-8") == "optional feature\n"
    assert not list(installed["codex_home"].glob("*.config.toml"))
    assert (installed["claude_home"] / "personal.config.toml").read_text(
        encoding="utf-8"
    ) == "mine\n"


def test_preserves_foreign_symlink(installed):
    run_uninstall(installed)
    assert (installed["claude_home"] / "foreign-link").is_symlink()


# famulus-skip: category=platform-contract; reason=Windows installs dispatcher.bat rather than a POSIX dispatcher file; alternate=test_codex_install checks Windows dispatcher launcher
@pytest.mark.skipif(sys.platform == "win32", reason="dispatcher launcher is POSIX-only by design; Windows uses .bat wrappers + registry PATH")
def test_general_uninstall_leaves_optional_launcher_and_removes_general_commands(installed):
    optional = installed["bin_dir"] / "assistant"
    optional.write_text("optional feature\n", encoding="utf-8")
    assert (installed["bin_dir"] / "dispatcher").is_file()
    run_uninstall(installed)
    assert optional.read_text(encoding="utf-8") == "optional feature\n"
    assert not (installed["bin_dir"] / "dispatcher").exists()


# famulus-skip: category=platform-contract; reason=Windows installs manage PATH via registry not shell rc files; alternate=test_launchers covers Windows registry env behavior
@pytest.mark.skipif(sys.platform == "win32", reason="Windows installs manage PATH via registry, not shell rc")
def test_strips_rc_block_preserving_user_lines(installed):
    text = installed["shell_rc"].read_text(encoding="utf-8")
    assert BLOCK_BEGIN in text  # install really wrote the block
    run_uninstall(installed)
    text = installed["shell_rc"].read_text(encoding="utf-8")
    assert BLOCK_BEGIN not in text and BLOCK_END not in text
    assert "# user line before" in text


def test_strips_codex_hooks_block_preserving_user_config(installed):
    config = installed["codex_home"] / "config.toml"
    assert HOOKS_BLOCK_BEGIN in config.read_text(encoding="utf-8")
    run_uninstall(installed)
    text = config.read_text(encoding="utf-8")
    assert HOOKS_BLOCK_BEGIN not in text and HOOKS_BLOCK_END not in text
    assert 'model = "user-choice"' in text


def test_removes_managed_claude_hook_preserving_user_hook(installed):
    settings_file = installed["claude_home"] / "settings.local.json"
    before = json.loads(settings_file.read_text(encoding="utf-8"))
    assert len(before["hooks"]["SessionStart"]) == 2  # user + managed
    run_uninstall(installed)
    after = json.loads(settings_file.read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entry in after.get("hooks", {}).get("SessionStart", [])
        for hook in entry.get("hooks", [])
    ]
    assert commands == ["echo user-hook"]
    assert after["permissions"] == {"allow": ["Bash(ls:*)"]}


USER_HOOK_COMMAND = "echo user-hook"


def _managed_session_start_group(settings_file: Path) -> tuple[dict, dict, str]:
    """Return (payload, the installer's entry group, its hook command).

    The managed group is identified by exclusion rather than by matching
    the command text: which hook registry is importable depends on test
    ordering, but the fixture always seeds exactly one user group.
    """
    payload = json.loads(settings_file.read_text(encoding="utf-8"))
    groups = [
        group
        for group in payload["hooks"]["SessionStart"]
        if all(hook["command"] != USER_HOOK_COMMAND for hook in group["hooks"])
    ]
    assert len(groups) == 1, groups
    return payload, groups[0], groups[0]["hooks"][0]["command"]


def _session_start_commands(settings_file: Path) -> list[str]:
    payload = json.loads(settings_file.read_text(encoding="utf-8"))
    return [
        hook["command"]
        for group in payload.get("hooks", {}).get("SessionStart", [])
        for hook in group.get("hooks", [])
    ]


def test_managed_hook_removal_preserves_a_user_hook_in_the_same_entry_group(installed):
    """Removal targets the managed hook object, not the entry group around it.

    A user who adds their own hook alongside the managed one must keep it.
    """
    settings_file = installed["claude_home"] / "settings.local.json"
    payload, group, managed_command = _managed_session_start_group(settings_file)
    group["hooks"].append({"type": "command", "command": "echo my-own-hook"})
    settings_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    run_uninstall(installed)

    commands = _session_start_commands(settings_file)
    assert "echo my-own-hook" in commands
    assert managed_command not in commands


def test_report_names_a_recorded_managed_hook_that_is_no_longer_present(installed):
    """An edited managed command is left behind; the report must say so.

    Matching on raw command text cannot recognise the edited hook, so it
    survives as an orphan invoking files uninstall has already removed.
    Silently reporting "no managed entries found" hides that.
    """
    settings_file = installed["claude_home"] / "settings.local.json"
    payload, group, _managed_command = _managed_session_start_group(settings_file)
    group["hooks"][0]["command"] += " --user-edit"
    settings_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    result = run_uninstall(installed)

    commands = _session_start_commands(settings_file)
    assert any("--user-edit" in command for command in commands)
    assert "no longer present" in result.stdout


def _seed_legacy_config_dir_entry(installed: dict[str, Path]) -> Path:
    """Add an old tracked cloud-files config entry so uninstall preserves or purges it correctly."""
    config_dir = installed["home"] / ".config" / "cloud-files"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text('{"remote_llm_root": "assistant"}\n', encoding="utf-8")

    manifest = Manifest(manifest_path(installed["home"]))
    manifest.record("config_dir", path=str(config_dir), purge_only=True)
    manifest.save()
    return config_dir


def test_leaves_credentials_by_default(installed):
    config_dir = _seed_legacy_config_dir_entry(installed)
    config = config_dir / "config.json"
    assert config.exists()
    run_uninstall(installed)
    assert config.exists()


def test_purge_preserves_credentials(installed):
    config_dir = _seed_legacy_config_dir_entry(installed)
    run_uninstall(installed, "--purge")
    assert config_dir.exists()


def test_dry_run_changes_nothing(installed):
    _publish_context_bound_manifest(installed)
    before = {
        str(p): p.is_symlink() for p in installed["home"].rglob("*")
    }
    run_uninstall(installed, "--dry-run")
    after = {
        str(p): p.is_symlink() for p in installed["home"].rglob("*")
    }
    assert before == after


def test_report_lists_actions(installed):
    result = run_uninstall_subprocess(installed)
    assert "Uninstall report:" in result.stdout
    assert "[removed]" in result.stdout


def test_missing_manifest_is_hard_error(installed):
    manifest = (
        installed["home"] / ".local" / "state" / "assistant-tools" / "install-manifest.json"
    )
    assert manifest.exists()
    manifest.unlink()  # simulate hand-deleted manifest

    result = run_uninstall(installed, check=False)
    assert result.returncode != 0
    assert "no install manifest" in (result.stdout + result.stderr).lower()
    # and nothing was touched: installed artifacts are all still present
    assert (installed["claude_home"] / "skills").is_symlink()
    if sys.platform != "win32":  # launcher and rc block are POSIX-only
        assert (installed["bin_dir"] / "dispatcher").is_file()
        assert BLOCK_BEGIN in installed["shell_rc"].read_text(encoding="utf-8")
