#!/usr/bin/env python3
"""Session-entry dispatcher-context hook built on the shared cross-host scaffold."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from llmhooks.lib.cross_host import CrossHostHook, HookInput, HookResult, parse_platform_args


DISPATCHER_CORE = """\
## Skill interfaces
When a skill lists `Executable Interfaces`, invoke them through the `famulus` MCP server using the invocation metadata and `Arguments JSON` shown for that interface; do not invoke private scripts directly. `Instruction Interfaces` are LLM-readable instructions: read and follow them directly, without an MCP call.\
"""


CONTEXT_DISPATCHER_MISSING = """\
## Skill System — Dispatcher Unavailable

The dispatcher is not installed. For blueprint-managed skills (those whose \
SKILL.md contains a `<!-- BEGIN BLUEPRINT CONTRACT -->` block), the normal \
permission enforcement is inactive — calls that would ordinarily be rejected \
will not be caught.

As a fallback you may invoke scripts under a skill's `scripts/` directory \
directly, but proceed carefully: the usual guardrails are not in place.\
"""


def dispatcher_available() -> tuple[bool, list[str]]:
    """Return (ok, missing_components) where missing_components lists what's broken."""
    import shutil

    missing = []
    if shutil.which("dispatcher") is None:
        missing.append("dispatcher CLI not on PATH")
    # The package is provided beside this exact pointer-selected hook resource;
    # it is deliberately not pip-installed, so ambient importability is not
    # required. No repository root or cwd discovery is permitted here.
    package_src = _REPO_ROOT / "script_dispatcher" / "src" / "script_dispatcher"
    if not package_src.is_dir() and importlib.util.find_spec("script_dispatcher") is None:
        missing.append("script_dispatcher source not found in repo")
    return len(missing) == 0, missing


class InjectDispatcherContextHook(CrossHostHook):
    hook_name = "inject-dispatcher-context"

    event = "SessionStart"
    matcher = "startup|clear|compact"

    def build(self, hook_input: HookInput) -> HookResult:
        ok, missing = dispatcher_available()
        if ok:
            return HookResult(additional_context=DISPATCHER_CORE)

        details = "; ".join(missing)
        system_message = (
            f"⚠️ Skill dispatcher not fully installed ({details}) — "
            "dynamic permission checks are inactive. "
            "To restore enforcement: re-run the install-assistant-tools skill "
            "(it generates the dispatcher launcher)"
        )
        return HookResult(
            additional_context=CONTEXT_DISPATCHER_MISSING,
            system_message=system_message,
        )


def main(argv: list[str] | None = None) -> int:
    host = parse_platform_args(argv)
    return InjectDispatcherContextHook().run(host)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"inject_dispatcher_context: error: {exc}", file=sys.stderr)
        sys.exit(0)
