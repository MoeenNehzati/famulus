"""Shared scaffold for cross-host assistant hooks.

Use this base when a hook has the standard lifecycle:
read host input -> build a semantic result -> emit host-shaped output.

Concrete hooks should usually subclass ``CrossHostHook`` and override
``build()``. They may also override host-specific output methods when the
default Codex/Claude/Cursor adapters are not enough.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Literal


Host = Literal["codex", "claude", "cursor"]


@dataclass(frozen=True)
class HookInput:
    host: Host
    event_name: str | None
    source: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class HookResult:
    additional_context: str | None = None
    system_message: str | None = None


@dataclass(frozen=True)
class InstallBinding:
    event: str
    matcher: str | None
    argv: tuple[str, ...]


class CrossHostHook:
    """Base class for installable hooks with shared host-adapter logic."""

    hook_name: str = ""
    event: str | None = None
    matcher: str | None = None

    codex_event: str | None = None
    codex_matcher: str | None = None

    claude_event: str | None = None
    claude_matcher: str | None = None

    cursor_event: str | None = None
    cursor_matcher: str | None = None

    def read_input(self, host: Host) -> HookInput:
        text = sys.stdin.read()
        raw = json.loads(text) if text.strip() else {}
        return HookInput(
            host=host,
            event_name=raw.get("hook_event_name"),
            source=raw.get("source"),
            raw=raw,
        )

    def build(self, hook_input: HookInput) -> HookResult:
        raise NotImplementedError

    def resolved_event(self, host: Host) -> str:
        if host == "codex" and self.codex_event is not None:
            return self.codex_event
        if host == "claude" and self.claude_event is not None:
            return self.claude_event
        if host == "cursor" and self.cursor_event is not None:
            return self.cursor_event
        if self.event is None:
            raise ValueError(f"{self.__class__.__name__}: no event configured for host={host}")
        return self.event

    def resolved_matcher(self, host: Host) -> str | None:
        if host == "codex" and self.codex_matcher is not None:
            return self.codex_matcher
        if host == "claude" and self.claude_matcher is not None:
            return self.claude_matcher
        if host == "cursor" and self.cursor_matcher is not None:
            return self.cursor_matcher
        return self.matcher

    def codex_output(self, hook_input: HookInput, result: HookResult) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "hookEventName": hook_input.event_name or self.resolved_event("codex"),
        }
        if result.additional_context is not None:
            payload["additionalContext"] = result.additional_context
        if result.system_message is not None:
            payload["systemMessage"] = result.system_message
        return {"hookSpecificOutput": payload}

    def claude_output(self, hook_input: HookInput, result: HookResult) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "hookEventName": hook_input.event_name or self.resolved_event("claude"),
        }
        if result.additional_context is not None:
            payload["additionalContext"] = result.additional_context
        if result.system_message is not None:
            payload["systemMessage"] = result.system_message
        return {"hookSpecificOutput": payload}

    def cursor_output(self, hook_input: HookInput, result: HookResult) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if result.additional_context is not None:
            payload["additional_context"] = result.additional_context
        if result.system_message is not None:
            payload["system_message"] = result.system_message
        return payload

    def emit(self, hook_input: HookInput, result: HookResult) -> dict[str, Any]:
        if hook_input.host == "codex":
            return self.codex_output(hook_input, result)
        if hook_input.host == "claude":
            return self.claude_output(hook_input, result)
        if hook_input.host == "cursor":
            return self.cursor_output(hook_input, result)
        raise ValueError(f"unsupported host: {hook_input.host}")

    def install_binding(self, host: Host, script_path: str) -> InstallBinding:
        return InstallBinding(
            event=self.resolved_event(host),
            matcher=self.resolved_matcher(host),
            argv=("python3", script_path, f"--{host}"),
        )

    def run(self, host: Host) -> int:
        hook_input = self.read_input(host)
        result = self.build(hook_input)
        print(json.dumps(self.emit(hook_input, result)))
        return 0


def parse_platform_args(argv: list[str] | None = None) -> Host:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--codex", action="store_true")
    group.add_argument("--claude", action="store_true")
    group.add_argument("--cursor", action="store_true")
    args = parser.parse_args(argv)
    if args.codex:
        return "codex"
    if args.claude:
        return "claude"
    return "cursor"
