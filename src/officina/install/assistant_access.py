"""Resolve the narrow, durable state roots granted to managed assistants."""
from __future__ import annotations

from pathlib import Path

from officina.install.context import InstallationContext


class AssistantAccessBoundaryError(ValueError):
    """A requested assistant-access root overlaps protected installation state."""


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _canonical(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise AssistantAccessBoundaryError(
            f"assistant access root cannot be canonicalized: {path}"
        ) from exc


def _protected_roots(context: InstallationContext) -> tuple[tuple[str, Path], ...]:
    paths = context.paths
    return (
        ("credential", paths.config_root / "connect-google"),
        ("runtime", paths.runtime_root),
        ("assistant", context.codex_home),
        ("assistant", context.claude_home),
        ("install", paths.install_state_root),
    )


def resolve_assistant_access_roots(context: InstallationContext) -> tuple[Path, ...]:
    """Return canonical writable state roots, independent of process overrides."""
    paths = context.paths
    roots = tuple(
        _canonical(path)
        for path in (
            context.selected_home / ".assistant-logs",
            paths.recurring_config_root,
            paths.recurring_state_root,
            paths.email_triage_state_root,
            paths.state_root / "list-manager" / "locks",
            paths.state_root / "list-manager" / "cache",
            context.selected_home / ".local" / "share" / "llm-wakeup",
        )
    )
    protected = tuple((label, _canonical(path)) for label, path in _protected_roots(context))
    for root in roots:
        for label, blocked in protected:
            if _overlaps(root, blocked):
                raise AssistantAccessBoundaryError(
                    f"assistant access root overlaps {label} root: {root}"
                )
        if context.mode == "development":
            if context.development_root is None:
                raise AssistantAccessBoundaryError("development access requires development_root")
            local_root = _canonical(context.development_root / ".famulus")
            if root != local_root and local_root not in root.parents:
                raise AssistantAccessBoundaryError(
                    f"development assistant access root escapes .famulus: {root}"
                )
    return roots


__all__ = ["AssistantAccessBoundaryError", "resolve_assistant_access_roots"]
