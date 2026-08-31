#!/usr/bin/env python3
"""Session-entry dispatcher-context hook built on the shared cross-host scaffold."""

from __future__ import annotations

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


class InjectDispatcherContextHook(CrossHostHook):
    hook_name = "inject-dispatcher-context"

    event = "SessionStart"
    matcher = "startup|clear|compact"

    def build(self, hook_input: HookInput) -> HookResult:
        return HookResult(additional_context=DISPATCHER_CORE)


def main(argv: list[str] | None = None) -> int:
    host = parse_platform_args(argv)
    return InjectDispatcherContextHook().run(host)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"inject_dispatcher_context: error: {exc}", file=sys.stderr)
        sys.exit(0)
