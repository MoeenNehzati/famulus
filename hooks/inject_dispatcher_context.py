#!/usr/bin/env python3
"""
inject_dispatcher_context.py — SessionStart hook for the skill system.

Checks whether the dispatcher is available (CLI on PATH + Python package
importable). Injects one of two contexts:

  - Dispatcher available: normal module-boundary rules, use dispatcher for all
    script calls.
  - Dispatcher unavailable: warns the user that dynamic permission checks are
    inactive, and tells the LLM it may invoke scripts/ directly as a fallback.

Works in both plugin mode (CLAUDE_PLUGIN_ROOT set by the platform) and
direct/dev mode (invoked via absolute path from settings or config).

Output format is platform-detected:
  Cursor                       -> additional_context (snake_case)
  Claude Code / Codex          -> hookSpecificOutput.additionalContext (nested)
  Copilot CLI / unknown        -> additionalContext (top-level, SDK standard)
"""

import importlib.util
import json
import os
import shutil
import sys

CONTEXT_DISPATCHER_AVAILABLE = """\
## Skill System — Module Boundaries

This applies to skills whose SKILL.md contains a \
`<!-- BEGIN BLUEPRINT CONTRACT -->` block after the frontmatter. \
Other skills may follow different conventions.

Each skill has a `scripts/` directory containing its implementation. \
Do not invoke these scripts directly. Do not read them unless absolutely \
necessary and only after getting the user's approval. Use the dispatcher instead.

The blueprint contract block in SKILL.md specifies which interfaces a skill \
exposes. Call them through the dispatcher with `--caller-skill` set to the \
skill making the call. The relevant parts of `blueprint.yaml` are already \
injected into SKILL.md — you do not need to read `blueprint.yaml` directly.

Dispatcher invocation:
  dispatcher --caller-skill <caller> <callee> <interface-id> [args...]

Use --dry-run to preview without executing.

The dispatcher enforces that only pre-specified calls are allowed. If it \
rejects a call, accept the rejection and report back to the user. \
Do not attempt to work around it.

Example:
  dispatcher --caller-skill daily-plan list-manager read-list /tmp/todo.yaml\
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

SYSTEM_MESSAGE_DISPATCHER_MISSING = (
    "⚠️ Skill dispatcher not found — dynamic permission checks are inactive. "
    "Blueprint-managed skills cannot enforce call restrictions. "
    "To restore enforcement: pip install -e $AI/script_dispatcher"
)


def dispatcher_available() -> tuple[bool, list[str]]:
    """Return (ok, missing_components) where missing_components lists what's broken."""
    missing = []
    if shutil.which("dispatcher") is None:
        missing.append("dispatcher CLI not on PATH")
    if importlib.util.find_spec("script_dispatcher") is None:
        missing.append("script_dispatcher Python package not importable")
    return len(missing) == 0, missing


def detect_platform() -> str:
    """Return 'cursor', 'claude', or 'sdk'."""
    if os.environ.get("CURSOR_PLUGIN_ROOT"):
        return "cursor"
    if os.environ.get("CLAUDE_PLUGIN_ROOT") and not os.environ.get("COPILOT_CLI"):
        return "claude"
    return "sdk"


def build_output(context: str, platform: str, system_message: str | None = None) -> dict:
    output: dict = {}

    if system_message:
        output["systemMessage"] = system_message

    if platform == "cursor":
        output["additional_context"] = context
    elif platform == "claude":
        output["hookSpecificOutput"] = {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    else:
        # sdk / Copilot CLI / unknown
        output["additionalContext"] = context

    return output


def main() -> None:
    platform = detect_platform()
    ok, missing = dispatcher_available()
    if ok:
        output = build_output(CONTEXT_DISPATCHER_AVAILABLE, platform)
    else:
        details = "; ".join(missing)
        system_message = (
            f"⚠️ Skill dispatcher not fully installed ({details}) — "
            "dynamic permission checks are inactive. "
            "To restore enforcement: pip install -e $AI/script_dispatcher"
        )
        output = build_output(CONTEXT_DISPATCHER_MISSING, platform, system_message=system_message)
    print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        # Never crash the session — emit empty output and let the platform continue.
        print(json.dumps({}), file=sys.stderr)
        print(f"inject_dispatcher_context: error: {exc}", file=sys.stderr)
        sys.exit(0)
