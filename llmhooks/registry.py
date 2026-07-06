"""Registry of installable cross-host hooks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from llmhooks.inject_dispatcher_context import InjectDispatcherContextHook
from llmhooks.lib.cross_host import CrossHostHook, Host, InstallBinding


@dataclass(frozen=True)
class RegisteredHook:
    script_relpath: Path
    hook_class: type[CrossHostHook]
    hosts: tuple[Host, ...]

    def install_binding(self, host: Host, repo_root: Path) -> InstallBinding:
        return self.hook_class().install_binding(host, str(repo_root / self.script_relpath))


REGISTERED_HOOKS: tuple[RegisteredHook, ...] = (
    RegisteredHook(
        script_relpath=Path("llmhooks") / "inject_dispatcher_context.py",
        hook_class=InjectDispatcherContextHook,
        hosts=("claude", "codex"),
    ),
)


def hooks_for_host(host: Host) -> tuple[RegisteredHook, ...]:
    return tuple(hook for hook in REGISTERED_HOOKS if host in hook.hosts)
