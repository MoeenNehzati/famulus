#!/usr/bin/env python3
"""Session-entry dispatcher-context hook built on the shared cross-host scaffold."""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from llmhooks.lib.cross_host import CrossHostHook, HookInput, HookResult, parse_platform_args


DISPATCHER_CORE = """\
## Skill dispatcher
When SKILL.md has `BEGIN BLUEPRINT CONTRACT`, treat `scripts/` as private; read only with approval. Use injected interfaces, not blueprint.yaml.
Invoke: dispatcher --caller-skill <skill> [--dry-run] <interface-id> <arguments>.
Dry-run prints compiled argv without gateway execution or stdin reads. Supply positionals first in position order, then options/switches in any order with each option beside its values. Dispatcher adds fixed arguments; do not supply them.\
"""

_OPTIONAL_VOCABULARY = {
    "arity:required": "<x> means required.",
    "arity:optional": "[<x>] means optional.",
    "arity:one-or-more": "<x>... means one-or-more.",
    "arity:zero-or-more": "[<x>...] means zero-or-more.",
    "binding:switch": "[--flag] means a valueless switch.",
    "type:enum": "<a|b> means alternatives.",
    "binding:stdin": "`--stdin` forwards declared UTF-8 stdin.",
    "provider-skill-route": "`route: {kind: provider-skill, skill: <id>}` delegates the named LLM interface to that skill.",
}


def render_dispatcher_context(
    vocabulary: Mapping[str, int] | Iterable[str] = (),
) -> str:
    """Render one bounded semantic payload from selected construct counts."""

    if isinstance(vocabulary, Mapping):
        counts = Counter(
            {
                str(name): int(count)
                for name, count in vocabulary.items()
                if isinstance(count, int) and count > 0
            }
        )
    else:
        counts = Counter(str(name) for name in vocabulary)
    parts = [DISPATCHER_CORE]
    used = len(DISPATCHER_CORE)
    for name, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        entry = _OPTIONAL_VOCABULARY.get(name)
        if entry is None:
            continue
        addition = "\n" + entry
        if used + len(addition) <= 750:
            parts.append(addition)
            used += len(addition)
    return "".join(parts)


CONTEXT_DISPATCHER_AVAILABLE = render_dispatcher_context()

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
            vocabulary = hook_input.raw.get("interface_vocabulary", {})
            if not isinstance(vocabulary, (dict, list, tuple, set)):
                vocabulary = {}
            return HookResult(
                additional_context=render_dispatcher_context(vocabulary)
            )

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
