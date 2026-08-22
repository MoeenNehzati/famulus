from __future__ import annotations

from pathlib import Path

import pytest

from officina.install.assistant_access import (
    AssistantAccessBoundaryError,
    resolve_assistant_access_roots,
)
from officina.install.context import InstallationContext, resolve_installation_context


def _standard_context(
    tmp_path: Path, *, platform: str, environ: dict[str, str]
) -> InstallationContext:
    home = tmp_path / "home with spaces 雪"
    source = tmp_path / "source"
    source.mkdir(parents=True)
    return resolve_installation_context(
        mode="standard",
        source_root=source,
        development_root=None,
        platform=platform,
        home=home,
        environ=environ,
    )


@pytest.mark.parametrize(
    ("platform", "environ", "expected_suffixes"),
    [
        (
            "linux",
            {
                "XDG_CONFIG_HOME": "/xdg config",
                "XDG_STATE_HOME": "/xdg state",
                "ASSISTANT_LOGS": "/hostile/logs",
                "EMAIL_TRIAGE_STATE_DIR": "/hostile/triage",
                "LIST_MANAGER_CLOUD_LOCK_DIR": "/hostile/locks",
                "LLM_WAKEUP_HOME": "/hostile/wakeup",
            },
            (
                ".assistant-logs",
                "/xdg config/famulus/recurring-tasks",
                "/xdg state/famulus/recurring-tasks",
                "/xdg state/famulus/email-triage",
                "/xdg state/famulus/list-manager/locks",
                "/xdg state/famulus/list-manager/cache",
                ".local/share/llm-wakeup",
            ),
        ),
        (
            "darwin",
            {},
            (
                ".assistant-logs",
                "Library/Application Support/Famulus/config/recurring-tasks",
                "Library/Application Support/Famulus/state/recurring-tasks",
                "Library/Application Support/Famulus/state/email-triage",
                "Library/Application Support/Famulus/state/list-manager/locks",
                "Library/Application Support/Famulus/state/list-manager/cache",
                ".local/share/llm-wakeup",
            ),
        ),
        (
            "win32",
            {
                "LOCALAPPDATA": "/local app data",
                "APPDATA": "/roaming app data",
            },
            (
                ".assistant-logs",
                "/roaming app data/Famulus/recurring-tasks",
                "/local app data/Famulus/state/recurring-tasks",
                "/local app data/Famulus/state/email-triage",
                "/local app data/Famulus/state/list-manager/locks",
                "/local app data/Famulus/state/list-manager/cache",
                ".local/share/llm-wakeup",
            ),
        ),
    ],
)
def test_resolver_returns_only_the_canonical_ordered_roots(
    tmp_path: Path,
    platform: str,
    environ: dict[str, str],
    expected_suffixes: tuple[str, ...],
) -> None:
    context = _standard_context(tmp_path, platform=platform, environ=environ)

    roots = resolve_assistant_access_roots(context)

    assert roots == tuple(
        (context.selected_home / suffix).resolve(strict=False)
        if not suffix.startswith("/")
        else Path(suffix).resolve(strict=False)
        for suffix in expected_suffixes
    )
    assert context.paths.config_root not in roots
    assert context.paths.data_root not in roots
    assert context.paths.state_root not in roots
    assert context.paths.runtime_root not in roots
    assert context.paths.install_state_root not in roots
    assert context.paths.config_root / "connect-google" not in roots
    assert context.codex_home not in roots
    assert context.claude_home not in roots


@pytest.mark.parametrize(
    "excluded",
    (
        "credential",
        "runtime",
        "assistant",
        "install",
    ),
)
@pytest.mark.parametrize("mode", ("standard", "development"))
def test_resolver_rejects_root_symlinked_into_an_excluded_tree(
    tmp_path: Path, excluded: str, mode: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    if mode == "standard":
        context = resolve_installation_context(
            mode="standard",
            source_root=source,
            development_root=None,
            platform="linux",
            home=tmp_path / "home",
            environ={},
        )
    else:
        local_root = source / ".famulus"
        local_root.mkdir()
        installation_id = "dev-0123456789abcdef0123456789abcdef"
        (local_root / "install-id").write_text(installation_id + "\n", encoding="utf-8")
        context = resolve_installation_context(
            mode="development",
            source_root=source,
            development_root=source,
            platform="linux",
            home=tmp_path / "ambient-home",
            environ={},
            installation_id=installation_id,
        )
    targets = {
        "credential": context.paths.config_root / "connect-google",
        "runtime": context.paths.runtime_root,
        "assistant": context.codex_home,
        "install": context.paths.install_state_root,
    }
    target = targets[excluded]
    target.mkdir(parents=True)
    context.paths.email_triage_state_root.parent.mkdir(parents=True, exist_ok=True)
    context.paths.email_triage_state_root.symlink_to(target, target_is_directory=True)

    with pytest.raises(AssistantAccessBoundaryError, match=excluded):
        resolve_assistant_access_roots(context)


def test_resolver_requires_every_development_root_to_stay_in_famulus(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    local_root = checkout / ".famulus"
    local_root.mkdir()
    installation_id = "dev-0123456789abcdef0123456789abcdef"
    (local_root / "install-id").write_text(installation_id + "\n", encoding="utf-8")
    context = resolve_installation_context(
        mode="development",
        source_root=checkout,
        development_root=checkout,
        platform="linux",
        home=tmp_path / "ambient-home",
        environ={},
        installation_id=installation_id,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    context.paths.email_triage_state_root.parent.mkdir(parents=True)
    context.paths.email_triage_state_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(AssistantAccessBoundaryError, match=".famulus"):
        resolve_assistant_access_roots(context)
