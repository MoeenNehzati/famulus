from __future__ import annotations

import errno
import json
import stat
import sys
import tomllib
from pathlib import Path

import pytest

from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.assistant_access import resolve_assistant_access_roots
from officina.install.context import InstallationContext

if __package__ and __package__.count(".") >= 1:
    from .. import _assistant_access_config as access_config
    from .. import _state_record as state_record
    from .._assistant_access_config import (
        ACCESS_BEGIN,
        ACCESS_END,
        AssistantAccessConfigError,
        reconcile_assistant_access,
    )
    from .._state_record import Manifest
else:
    import _assistant_access_config as access_config  # noqa: E402
    import _state_record as state_record  # noqa: E402
    from _assistant_access_config import (  # noqa: E402
        ACCESS_BEGIN,
        ACCESS_END,
        AssistantAccessConfigError,
        reconcile_assistant_access,
    )
    from _state_record import Manifest  # noqa: E402


def _context(tmp_path: Path, *, mode: str = "standard") -> InstallationContext:
    if mode == "development":
        checkout = tmp_path / "checkout"
        checkout.mkdir(parents=True)
        selected_home = checkout / ".famulus" / "home"
        return InstallationContext(
            mode="development",
            source_root=checkout,
            development_root=checkout,
            paths=resolve_famulus_paths(platform="linux", home=selected_home, environ={}),
            selected_home=selected_home,
            codex_home=checkout / ".famulus" / "homes" / "codex",
            claude_home=checkout / ".famulus" / "homes" / "claude",
            installation_id="dev-" + "a" * 32,
        )
    return InstallationContext(
        mode="standard",
        source_root=tmp_path,
        development_root=None,
        paths=resolve_famulus_paths(platform="linux", home=tmp_path, environ={}),
        selected_home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_home=tmp_path / ".claude",
        installation_id="standard",
    )


def _manifest(context: InstallationContext) -> Manifest:
    manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
    manifest.bind_context(
        mode=context.mode,
        installation_id=context.installation_id,
        development_root=context.development_root,
        codex_home=context.codex_home if context.mode == "standard" else None,
        claude_home=context.claude_home if context.mode == "standard" else None,
    )
    return manifest


def _codex_roots(context: InstallationContext) -> list[str]:
    payload = tomllib.loads((context.codex_home / "config.toml").read_text(encoding="utf-8"))
    return payload["sandbox_workspace_write"]["writable_roots"]


def _claude_roots(context: InstallationContext) -> list[str]:
    payload = json.loads((context.claude_home / "settings.json").read_text(encoding="utf-8"))
    return payload["permissions"]["additionalDirectories"]


def _between_markers(text: str) -> list[str]:
    block = text.split(ACCESS_BEGIN, 1)[1].split(ACCESS_END, 1)[0]
    return tomllib.loads("values = [\n" + block + "\n]")["values"]


@pytest.mark.parametrize(
    "original",
    [
        "",
        'model = "gpt-5"\n',
        '[sandbox_workspace_write]\nnetwork_access = true\n',
        '[sandbox_workspace_write]\nwritable_roots = ["/foreign/a", "/foreign/b"]\n',
    ],
)
def test_codex_reconcile_creates_or_normalizes_the_one_selected_string_array(
    tmp_path: Path, original: str
) -> None:
    context = _context(tmp_path)
    config = context.codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(original, encoding="utf-8")

    reconcile_assistant_access(context, _manifest(context))

    roots = _codex_roots(context)
    foreign = [root for root in ("/foreign/a", "/foreign/b") if root in original]
    required = [str(path) for path in resolve_assistant_access_roots(context)]
    assert roots == foreign + required
    text = config.read_text(encoding="utf-8")
    assert text.count("[sandbox_workspace_write]") == 1
    assert text.count("writable_roots = [") == 1
    assert text.count(ACCESS_BEGIN) == 1
    assert text.count(ACCESS_END) == 1
    assert _between_markers(text) == required


def test_codex_reconcile_preserves_foreign_order_and_does_not_own_preexisting_roots(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    required = [str(path) for path in resolve_assistant_access_roots(context)]
    config = context.codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "[sandbox_workspace_write]\n"
        f'writable_roots = ["/first", {json.dumps(required[2])}, "/last", {json.dumps(required[2])}]\n',
        encoding="utf-8",
    )

    manifest = _manifest(context)
    reconcile_assistant_access(context, manifest)

    assert _codex_roots(context) == ["/first", required[2], "/last", required[2]] + [
        root for root in required if root != required[2]
    ]
    assert _between_markers(config.read_text(encoding="utf-8")) == [
        root for root in required if root != required[2]
    ]
    entry = next(item for item in manifest.entries if item["kind"] == "codex_access_array_block")
    assert entry["introduced"] == [root for root in required if root != required[2]]


def test_codex_reconcile_preserves_foreign_multiline_comments_and_formatting(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    config = context.codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    foreign = (
        "  # user group\n"
        '  "/first",\n'
        '  "/second", # keep inline\n'
        "  # trailing user note\n"
    )
    config.write_text(
        "[sandbox_workspace_write]\nwritable_roots = [\n" + foreign + "]\n",
        encoding="utf-8",
    )

    reconcile_assistant_access(context, _manifest(context))

    result = config.read_text(encoding="utf-8")
    assert foreign in result
    assert _codex_roots(context) == ["/first", "/second"] + [
        str(path) for path in resolve_assistant_access_roots(context)
    ]


def test_codex_reconcile_preserves_inline_comment_when_multiline_array_lacks_trailing_comma(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    config = context.codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    original_line = '  "/foreign" # keep this user note\n'
    config.write_text(
        "[sandbox_workspace_write]\nwritable_roots = [\n"
        + original_line
        + "]\n",
        encoding="utf-8",
    )

    reconcile_assistant_access(context, _manifest(context))

    result = config.read_text(encoding="utf-8")
    assert '  "/foreign", # keep this user note\n' in result
    assert "# keep this user note" in result


def test_codex_reconcile_preserves_crlf_and_the_existing_hook_marker_block(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    hook = (
        "# >>> famulus-codex-hook >>>\r\n"
        "notify = [\"python\", \"hook.py\"]\r\n"
        "# <<< famulus-codex-hook <<<\r\n"
    )
    config = context.codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_bytes((hook + "[sandbox_workspace_write]\r\nwritable_roots = []\r\n").encode())

    reconcile_assistant_access(context, _manifest(context))

    result = config.read_bytes()
    assert hook.encode() in result
    assert b"\r\n" in result
    assert result.replace(b"\r\n", b"").find(b"\n") == -1


def test_codex_reconcile_keeps_access_key_inside_target_table_before_hook_array_tables(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    hook = (
        "# >>> famulus-codex-hook >>>\n"
        "[[hooks.SessionStart]]\n"
        'matcher = "startup"\n'
        "[[hooks.SessionStart.hooks]]\n"
        'type = "command"\n'
        'command = "python hook.py"\n'
        "# <<< famulus-codex-hook <<<\n"
    )
    config = context.codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[sandbox_workspace_write]\nnetwork_access = true\n" + hook, encoding="utf-8")

    reconcile_assistant_access(context, _manifest(context))

    payload = tomllib.loads(config.read_text(encoding="utf-8"))
    assert payload["sandbox_workspace_write"]["writable_roots"] == [
        str(path) for path in resolve_assistant_access_roots(context)
    ]
    assert hook in config.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "content",
    [
        'description = """\n[sandbox_workspace_write]\nwritable_roots = [\n"""\n'
        "[sandbox_workspace_write]\nwritable_roots = []\n",
        "[sandbox_workspace_write]\n"
        'description = """\n[[hooks.SessionStart]]\nwritable_roots = [\n"""\n'
        "writable_roots = []\n",
    ],
)
def test_codex_reconcile_ignores_target_looking_lines_inside_multiline_strings(
    tmp_path: Path, content: str
) -> None:
    context = _context(tmp_path)
    config = context.codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(content, encoding="utf-8")

    reconcile_assistant_access(context, _manifest(context))

    result = config.read_text(encoding="utf-8")
    fake_multiline_contents = content.split('"""\n', 1)[1].split('\n"""', 1)[0]
    assert fake_multiline_contents in result
    assert _codex_roots(context) == [
        str(path) for path in resolve_assistant_access_roots(context)
    ]


@pytest.mark.parametrize(
    "content",
    [
        '[sandbox_workspace_write]\nwritable_roots = ["/ok", 1]\n',
        '[sandbox_workspace_write]\nwritable_roots = "not-an-array"\n',
        '[sandbox_workspace_write]\nwritable_roots = []\nwritable_roots = []\n',
        '[sandbox_workspace_write]\nwritable_roots = []\n[sandbox_workspace_write]\nnetwork_access = true\n',
    ],
)
def test_codex_reconcile_rejects_non_string_or_ambiguous_target_without_writing(
    tmp_path: Path, content: str
) -> None:
    context = _context(tmp_path)
    config = context.codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(content, encoding="utf-8")

    with pytest.raises(AssistantAccessConfigError):
        reconcile_assistant_access(context, _manifest(context))

    assert config.read_text(encoding="utf-8") == content


@pytest.mark.parametrize(
    "content",
    [
        f"{ACCESS_BEGIN}\n{ACCESS_END}\n",
        f"[sandbox_workspace_write]\nwritable_roots = [\n{ACCESS_BEGIN}\n]\n",
        f"[sandbox_workspace_write]\nwritable_roots = [\n{ACCESS_END}\n]\n",
        f"[sandbox_workspace_write]\nwritable_roots = [\n{ACCESS_END}\n{ACCESS_BEGIN}\n]\n",
        f"[sandbox_workspace_write]\nwritable_roots = [\n{ACCESS_BEGIN}\n{ACCESS_BEGIN}\n{ACCESS_END}\n]\n",
        f"[sandbox_workspace_write]\nwritable_roots = [\n{ACCESS_BEGIN}\n{ACCESS_END}\n{ACCESS_END}\n]\n",
    ],
)
def test_codex_reconcile_rejects_every_ambiguous_marker_arrangement(
    tmp_path: Path, content: str
) -> None:
    context = _context(tmp_path)
    config = context.codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(content, encoding="utf-8")

    with pytest.raises(AssistantAccessConfigError, match="marker"):
        reconcile_assistant_access(context, _manifest(context))

    assert config.read_text(encoding="utf-8") == content


def test_codex_reconcile_rejects_orphaned_access_markers_without_manifest_ownership(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    required = [str(path) for path in resolve_assistant_access_roots(context)]
    config = context.codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    content = (
        "[sandbox_workspace_write]\nwritable_roots = [\n"
        f"  {ACCESS_BEGIN}\n"
        + "".join(f"  {json.dumps(root)},\n" for root in required)
        + f"  {ACCESS_END}\n]\n"
    )
    config.write_text(content, encoding="utf-8")

    with pytest.raises(AssistantAccessConfigError, match="ownership|orphan"):
        reconcile_assistant_access(context, _manifest(context))

    assert config.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("mutation", ["reorder", "delete"])
def test_codex_reconcile_rejects_any_edit_to_a_recorded_marker_block(
    tmp_path: Path, mutation: str
) -> None:
    context = _context(tmp_path)
    manifest = _manifest(context)
    reconcile_assistant_access(context, manifest)
    config = context.codex_home / "config.toml"
    text = config.read_text(encoding="utf-8")
    before, marked = text.split(ACCESS_BEGIN, 1)
    body, after = marked.split(ACCESS_END, 1)
    owned_lines = [line for line in body.splitlines(keepends=True) if line.strip()]
    changed = list(reversed(owned_lines)) if mutation == "reorder" else owned_lines[:-1]
    edited = before + ACCESS_BEGIN + "\n" + "".join(changed) + ACCESS_END + after
    config.write_text(edited, encoding="utf-8")

    with pytest.raises(AssistantAccessConfigError, match="modified"):
        reconcile_assistant_access(context, manifest)

    assert config.read_text(encoding="utf-8") == edited


def test_claude_reconcile_targets_settings_json_and_preserves_local_settings(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    local = context.claude_home / "settings.local.json"
    local.parent.mkdir(parents=True)
    local_bytes = b'{"hooks":{"Notification":[]}}\n'
    local.write_bytes(local_bytes)

    reconcile_assistant_access(context, _manifest(context))

    assert _claude_roots(context) == [str(path) for path in resolve_assistant_access_roots(context)]
    assert local.read_bytes() == local_bytes


def test_claude_reconcile_preserves_unrelated_structure_duplicates_and_preexisting_roots(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    required = [str(path) for path in resolve_assistant_access_roots(context)]
    settings = context.claude_home / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "theme": "dark",
                "permissions": {
                    "deny": ["Read(.env)"],
                    "additionalDirectories": ["/foreign", required[0], required[0]],
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = _manifest(context)

    reconcile_assistant_access(context, manifest)
    first = settings.read_bytes()
    reconcile_assistant_access(context, manifest)

    payload = json.loads(settings.read_text(encoding="utf-8"))
    assert payload["theme"] == "dark"
    assert payload["permissions"]["deny"] == ["Read(.env)"]
    assert _claude_roots(context) == ["/foreign", required[0], required[0]] + required[1:]
    assert settings.read_bytes() == first
    entry = next(item for item in manifest.entries if item["kind"] == "json_array_values")
    assert entry["introduced"] == required[1:]


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "[]",
        '{"permissions": []}',
        '{"permissions": {"additionalDirectories": "wrong"}}',
        '{"permissions": {"additionalDirectories": ["/ok", 3]}}',
    ],
)
def test_claude_reconcile_rejects_malformed_or_wrong_type_json_without_writing(
    tmp_path: Path, content: str
) -> None:
    context = _context(tmp_path)
    codex = context.codex_home / "config.toml"
    codex.parent.mkdir(parents=True)
    codex_before = b'model = "keep"\n'
    codex.write_bytes(codex_before)
    settings = context.claude_home / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(content, encoding="utf-8")

    with pytest.raises(AssistantAccessConfigError):
        reconcile_assistant_access(context, _manifest(context))

    assert settings.read_text(encoding="utf-8") == content
    assert codex.read_bytes() == codex_before


@pytest.mark.parametrize(
    "content",
    [
        '{"permissions": {}, "permissions": {"additionalDirectories": []}}',
        '{"permissions": {"additionalDirectories": [], "additionalDirectories": []}}',
        '{"unrelated": {"nested": 1, "nested": 2}, "permissions": {"additionalDirectories": []}}',
    ],
)
def test_claude_reconcile_rejects_duplicate_object_keys_recursively_without_writing(
    tmp_path: Path, content: str
) -> None:
    context = _context(tmp_path)
    settings = context.claude_home / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(content, encoding="utf-8")

    with pytest.raises(AssistantAccessConfigError, match="duplicate"):
        reconcile_assistant_access(context, _manifest(context))

    assert settings.read_text(encoding="utf-8") == content
    assert not (context.codex_home / "config.toml").exists()


@pytest.mark.parametrize("mode", ["standard", "development"])
@pytest.mark.parametrize("target_name", ["codex", "claude"])
def test_reconcile_rejects_symlinked_config_before_either_target_changes(
    tmp_path: Path, mode: str, target_name: str
) -> None:
    context = _context(tmp_path, mode=mode)
    codex = context.codex_home / "config.toml"
    claude = context.claude_home / "settings.json"
    codex.parent.mkdir(parents=True, exist_ok=True)
    claude.parent.mkdir(parents=True, exist_ok=True)
    codex.write_bytes(b'model = "keep"\n')
    claude.write_bytes(b'{"theme": "keep"}\n')
    target = codex if target_name == "codex" else claude
    external = tmp_path / f"external-{mode}-{target_name}"
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
    manifest = _manifest(context)
    manifest_before = manifest.path.read_bytes()

    with pytest.raises(AssistantAccessConfigError, match="symlink"):
        reconcile_assistant_access(context, manifest)

    assert target.is_symlink()
    assert external.read_bytes() == external_before
    assert other.read_bytes() == other_before
    assert manifest.path.read_bytes() == manifest_before


def test_reconcile_rejects_claude_edit_after_frozen_pair_preflight_without_codex_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    codex = context.codex_home / "config.toml"
    claude = context.claude_home / "settings.json"
    codex.parent.mkdir(parents=True)
    claude.parent.mkdir(parents=True)
    codex.write_bytes(b'model = "keep"\n')
    claude.write_bytes(b'{"theme": "keep"}\n')
    manifest = _manifest(context)
    codex_before = codex.read_bytes()
    manifest_before = manifest.path.read_bytes()
    real_preflight = access_config._preflight_codex

    def edit_after_preflight(*args: object, **kwargs: object):
        plan = real_preflight(*args, **kwargs)
        claude.write_bytes(b"{malformed")
        return plan

    monkeypatch.setattr(access_config, "_preflight_codex", edit_after_preflight)

    with pytest.raises(AssistantAccessConfigError):
        reconcile_assistant_access(context, manifest)

    assert codex.read_bytes() == codex_before
    assert claude.read_bytes() == b"{malformed"
    assert manifest.path.read_bytes() == manifest_before


def test_reconcile_uses_each_frozen_target_plan_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    manifest = _manifest(context)
    calls = 0
    real_plan = access_config.codex_toml.plan_access_roots

    def count_plan(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return real_plan(*args, **kwargs)

    monkeypatch.setattr(access_config.codex_toml, "plan_access_roots", count_plan)

    reconcile_assistant_access(context, manifest)

    assert calls == 1


def test_reconcile_refreshes_stale_manifest_after_context_lock(tmp_path: Path) -> None:
    context = _context(tmp_path)
    first = _manifest(context)
    stale = Manifest(first.path)

    reconcile_assistant_access(context, first)
    codex_before = (context.codex_home / "config.toml").read_bytes()
    claude_before = (context.claude_home / "settings.json").read_bytes()

    reconcile_assistant_access(context, stale)

    assert (context.codex_home / "config.toml").read_bytes() == codex_before
    assert (context.claude_home / "settings.json").read_bytes() == claude_before
    assert {
        entry["kind"]
        for entry in stale.entries
        if entry["kind"] in {"codex_access_array_block", "json_array_values"}
    } == {"codex_access_array_block", "json_array_values"}


def test_reconcile_refuses_disappeared_bound_manifest_after_locked_reload(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    manifest = _manifest(context)
    manifest.path.unlink()

    with pytest.raises(AssistantAccessConfigError, match="binding"):
        reconcile_assistant_access(context, manifest)

    assert not (context.codex_home / "config.toml").exists()
    assert not (context.claude_home / "settings.json").exists()
    assert not manifest.path.exists()


def test_reconcile_recovers_claude_write_completed_before_committed_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    manifest = _manifest(context)
    real_record = manifest.record

    def crash_on_claude_commit(kind: str, *, path: str, **fields: object) -> None:
        if kind == "json_array_values" and fields.get("transaction") == "committed":
            raise RuntimeError("crash after Claude write")
        real_record(kind, path=path, **fields)

    monkeypatch.setattr(manifest, "record", crash_on_claude_commit)
    with pytest.raises(RuntimeError, match="crash after Claude write"):
        reconcile_assistant_access(context, manifest)

    pending = Manifest(manifest.path)
    reconcile_assistant_access(context, pending)

    assert _codex_roots(context) == [str(path) for path in resolve_assistant_access_roots(context)]
    assert _claude_roots(context) == [str(path) for path in resolve_assistant_access_roots(context)]
    assert {entry["transaction"] for entry in pending.entries if entry["kind"] in {"codex_access_array_block", "json_array_values"}} == {"committed"}


@pytest.mark.parametrize(
    ("target_kind", "target_name"),
    [
        ("codex_access_array_block", "codex"),
        ("json_array_values", "claude"),
    ],
)
def test_reconcile_pending_post_state_rejects_mode_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
    target_name: str,
) -> None:
    context = _context(tmp_path)
    manifest = _manifest(context)
    real_record = manifest.record

    def crash_on_target_commit(kind: str, *, path: str, **fields: object) -> None:
        if kind == target_kind and fields.get("transaction") == "committed":
            raise RuntimeError("crash after target write")
        real_record(kind, path=path, **fields)

    monkeypatch.setattr(manifest, "record", crash_on_target_commit)
    with pytest.raises(RuntimeError, match="crash after target write"):
        reconcile_assistant_access(context, manifest)

    target = (
        context.codex_home / "config.toml"
        if target_name == "codex"
        else context.claude_home / "settings.json"
    )
    target.chmod(0o640)
    pending = Manifest(manifest.path)
    manifest_before = pending.path.read_bytes()
    target_before = target.read_bytes()

    with pytest.raises(AssistantAccessConfigError, match="mode"):
        reconcile_assistant_access(context, pending)

    assert target.read_bytes() == target_before
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert pending.path.read_bytes() == manifest_before


@pytest.mark.parametrize("mode", ["standard", "development"])
@pytest.mark.parametrize("kind", ["codex_access_array_block", "json_array_values"])
@pytest.mark.parametrize("edit", ["duplicate", "wrong-path"])
def test_reconcile_rejects_forged_access_ownership_before_target_mutation(
    tmp_path: Path, mode: str, kind: str, edit: str
) -> None:
    context = _context(tmp_path, mode=mode)
    manifest = _manifest(context)
    reconcile_assistant_access(context, manifest)
    entry = next(item for item in manifest.entries if item["kind"] == kind)
    if edit == "duplicate":
        manifest.entries.append(dict(entry))
    else:
        entry["path"] = str(tmp_path / "forged-target")
    manifest.save()
    codex = context.codex_home / "config.toml"
    claude = context.claude_home / "settings.json"
    before = (codex.read_bytes(), claude.read_bytes(), manifest.path.read_bytes())

    with pytest.raises(AssistantAccessConfigError, match="manifest ownership"):
        reconcile_assistant_access(context, manifest)

    assert (codex.read_bytes(), claude.read_bytes(), manifest.path.read_bytes()) == before


def test_reconcile_records_distinct_committed_ownership_kinds(tmp_path: Path) -> None:
    context = _context(tmp_path)
    manifest = _manifest(context)

    reconcile_assistant_access(context, manifest)

    entries = {
        item["kind"]: item
        for item in manifest.entries
        if item["kind"] in {"codex_access_array_block", "json_array_values"}
    }
    assert set(entries) == {"codex_access_array_block", "json_array_values"}
    assert entries["codex_access_array_block"]["path"] == str(context.codex_home / "config.toml")
    assert entries["json_array_values"]["path"] == str(context.claude_home / "settings.json")
    assert {entry["transaction"] for entry in entries.values()} == {"committed"}


def test_reconcile_persists_complete_pending_intent_before_first_config_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    manifest = _manifest(context)

    def stop_before_write(*args: object, **kwargs: object) -> None:
        loaded = Manifest(manifest.path)
        entry = next(item for item in loaded.entries if item["kind"] == "codex_access_array_block")
        assert entry["transaction"] == "pending"
        assert entry["path"] == str(context.codex_home / "config.toml")
        assert entry["created_file"] is True
        assert entry["pre_sha256"] is None
        assert isinstance(entry["post_sha256"], str)
        assert entry["introduced"] == [str(path) for path in resolve_assistant_access_roots(context)]
        assert entry["file_mode"] == 0o600
        raise RuntimeError("stop before write")

    monkeypatch.setattr(access_config.codex_toml, "apply_access_plan", stop_before_write)

    with pytest.raises(RuntimeError, match="stop before write"):
        reconcile_assistant_access(context, manifest)

    assert not (context.codex_home / "config.toml").exists()


def test_reconcile_preserves_an_external_edit_made_immediately_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    config = context.codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    original = b'model = "before"\n'
    external = b'model = "external"\n'
    config.write_bytes(original)
    real_replace = access_config.codex_toml.apply_access_plan

    def edit_then_replace(plan: object) -> None:
        config.write_bytes(external)
        real_replace(plan)

    monkeypatch.setattr(access_config.codex_toml, "apply_access_plan", edit_then_replace)

    with pytest.raises(AssistantAccessConfigError, match="changed before atomic replacement"):
        reconcile_assistant_access(context, _manifest(context))

    assert config.read_bytes() == external


def test_reapply_recovers_write_completed_before_manifest_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    manifest = _manifest(context)
    real_record = manifest.record

    def crash_on_commit(kind: str, *, path: str, **fields: object) -> None:
        if kind == "codex_access_array_block" and fields.get("transaction") == "committed":
            raise RuntimeError("crash before manifest commit")
        real_record(kind, path=path, **fields)

    monkeypatch.setattr(manifest, "record", crash_on_commit)

    with pytest.raises(RuntimeError, match="crash before manifest commit"):
        reconcile_assistant_access(context, manifest)

    config = context.codex_home / "config.toml"
    assert config.exists()
    pending = Manifest(manifest.path)
    assert next(item for item in pending.entries if item["kind"] == "codex_access_array_block")[
        "transaction"
    ] == "pending"

    reconcile_assistant_access(context, pending)

    recovered = Manifest(manifest.path)
    assert {
        item["transaction"]
        for item in recovered.entries
        if item["kind"] in {"codex_access_array_block", "json_array_values"}
    } == {"committed"}


def test_reconcile_preserves_existing_file_modes(tmp_path: Path) -> None:
    context = _context(tmp_path)
    codex = context.codex_home / "config.toml"
    claude = context.claude_home / "settings.json"
    codex.parent.mkdir(parents=True)
    claude.parent.mkdir(parents=True)
    codex.write_text('model = "keep"\n', encoding="utf-8")
    claude.write_text('{"theme": "dark"}\n', encoding="utf-8")
    codex.chmod(0o640)
    claude.chmod(0o640)

    reconcile_assistant_access(context, _manifest(context))

    assert codex.stat().st_mode & 0o777 == 0o640
    assert claude.stat().st_mode & 0o777 == 0o640


def test_reconcile_fails_closed_when_secure_posix_fchmod_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    monkeypatch.delattr(access_config.os, "fchmod")

    with pytest.raises(access_config.AtomicWriteError, match="secure directory-relative replacement"):
        reconcile_assistant_access(context, _manifest(context))

    assert not (context.codex_home / "config.toml").exists()
    assert not (context.claude_home / "settings.json").exists()


def test_directory_sync_tolerates_filesystems_without_directory_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        access_config.os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError(errno.EINVAL, "unsupported")),
    )

    access_config._sync_directory(tmp_path)


def test_directory_sync_tolerates_filesystems_without_directory_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        access_config.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EINVAL, "unsupported")),
    )

    access_config._sync_directory(tmp_path)


def test_manifest_intent_sync_tolerates_filesystems_without_directory_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fsync = state_record.os.fsync

    def fsync(descriptor: int) -> None:
        if stat.S_ISDIR(state_record.os.fstat(descriptor).st_mode):
            raise OSError(errno.EINVAL, "unsupported")
        real_fsync(descriptor)

    monkeypatch.setattr(state_record.os, "fsync", fsync)

    Manifest(tmp_path / "manifest.json").record("file", path=str(tmp_path / "owned"))


def test_manifest_intent_sync_tolerates_filesystems_without_directory_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = state_record.os.open

    def open_path(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if Path(path) == tmp_path and flags & getattr(state_record.os, "O_DIRECTORY", 0):
            raise OSError(errno.EINVAL, "unsupported")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(state_record.os, "open", open_path)

    Manifest(tmp_path / "manifest.json").record("file", path=str(tmp_path / "owned"))


def test_two_development_checkouts_reconcile_only_checkout_local_configuration(
    tmp_path: Path,
) -> None:
    first = _context(tmp_path / "first", mode="development")
    second = _context(tmp_path / "second", mode="development")

    reconcile_assistant_access(first, _manifest(first))
    first_codex = (first.codex_home / "config.toml").read_bytes()
    reconcile_assistant_access(second, _manifest(second))

    assert (first.codex_home / "config.toml").read_bytes() == first_codex
    assert first.codex_home != second.codex_home
    assert all(str(first.development_root / ".famulus") in root for root in _codex_roots(first))
    assert all(str(second.development_root / ".famulus") in root for root in _codex_roots(second))


def test_moved_development_checkout_rebases_only_after_recorded_codex_block_matches(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path / "original", mode="development")
    assert context.development_root is not None
    install_id = context.development_root / ".famulus" / "install-id"
    install_id.parent.mkdir(parents=True, exist_ok=True)
    install_id.write_text(context.installation_id + "\n", encoding="utf-8")
    manifest = _manifest(context)
    reconcile_assistant_access(context, manifest)
    old_root = context.development_root
    moved_root = tmp_path / "moved checkout"
    old_root.rename(moved_root)
    moved_home = moved_root / ".famulus" / "home"
    moved = InstallationContext(
        mode="development",
        source_root=moved_root,
        development_root=moved_root,
        paths=resolve_famulus_paths(platform="linux", home=moved_home, environ={}),
        selected_home=moved_home,
        codex_home=moved_root / ".famulus" / "homes" / "codex",
        claude_home=moved_root / ".famulus" / "homes" / "claude",
        installation_id=context.installation_id,
    )
    moved_manifest_path = moved_root / manifest.path.relative_to(old_root)
    moved_manifest = Manifest(moved_manifest_path)
    moved_manifest.bind_context(
        mode="development",
        installation_id=moved.installation_id,
        development_root=moved_root,
    )

    reconcile_assistant_access(moved, moved_manifest)

    assert _codex_roots(moved) == [
        str(path) for path in resolve_assistant_access_roots(moved)
    ]
    assert (moved.codex_home / "config.toml").read_text(encoding="utf-8").count(
        ACCESS_BEGIN
    ) == 1


def test_moved_development_bind_rejects_modified_codex_block_without_persisting_rebase(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path / "original", mode="development")
    assert context.development_root is not None
    install_id = context.development_root / ".famulus" / "install-id"
    install_id.parent.mkdir(parents=True, exist_ok=True)
    install_id.write_text(context.installation_id + "\n", encoding="utf-8")
    manifest = _manifest(context)
    reconcile_assistant_access(context, manifest)
    config = context.codex_home / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            ".assistant-logs", ".assistant-logs-edited", 1
        ),
        encoding="utf-8",
    )
    old_root = context.development_root
    moved_root = tmp_path / "moved checkout"
    old_root.rename(moved_root)
    moved_manifest_path = moved_root / manifest.path.relative_to(old_root)
    moved_config = moved_root / config.relative_to(old_root)
    manifest_before = moved_manifest_path.read_bytes()
    config_before = moved_config.read_bytes()
    moved_manifest = Manifest(moved_manifest_path)

    with pytest.raises(ValueError, match="Codex|block|modified"):
        moved_manifest.bind_context(
            mode="development",
            installation_id=context.installation_id,
            development_root=moved_root,
        )

    assert moved_manifest_path.read_bytes() == manifest_before
    assert moved_config.read_bytes() == config_before
