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
  dispatcher --caller-skill daily-plan list-manager read-list /tmp/todo.yaml

For both creating a new skill and updating an existing one, prefer the \
`skill-maker` skill — it applies this repository's skill-writing guideline \
and conformance checks. Do not load it until skill work actually begins.\
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
    # The package is provided by the generated launcher from the repo source
    # ($AI); it is deliberately not pip-installed, so importability in the
    # ambient interpreter is not required — source presence is.
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
            return HookResult(additional_context=CONTEXT_DISPATCHER_AVAILABLE)

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
