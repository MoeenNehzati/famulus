# Cross-host hook scaffold

Use this reference when implementing a hook that serves one semantic purpose across multiple assistant hosts.

The reusable base scaffold lives at `llmhooks/lib/cross_host.py`. Prefer subclassing it instead of copying ad hoc host-detection code into each hook.

## Architecture

Use this shape:

```text
llmhooks/
├── lib/
│   └── cross_host.py
├── registry.py
├── session_start.py
├── stop_summary.py
└── ...
```

The shared logic must not emit host-shaped JSON. It should return semantic values such as additional context text, decision state, diagnostic messages, or files to write.

The host adapter owns:

- accepted lifecycle event names and matchers
- stdin payload interpretation
- JSON output shape
- stdout vs stderr rules
- exit-code meaning
- truncation or escaping required by that host

The hook class itself should also own installation metadata:

- shared default event and matcher
- per-host event overrides
- per-host matcher overrides
- the exact CLI selector used in installed commands

## Minimal Python structure

For standard hooks, prefer subclassing `llmhooks/lib/cross_host.py:CrossHostHook`.

Use a single file per hook purpose when the logic is small:

```python
from __future__ import annotations

from llmhooks.lib.cross_host import CrossHostHook, HookInput, HookResult, parse_platform_args


class InjectDispatcherContextHook(CrossHostHook):
    hook_name = "inject-dispatcher-context"

    event = "SessionStart"
    matcher = "startup|clear|compact"

    # If a host differs, override only that host.
    host_a_event = None
    host_b_event = None
    host_c_event = None

    host_a_matcher = None
    host_b_matcher = None
    host_c_matcher = None

    def build(self, hook_input: HookInput) -> HookResult:
        return HookResult(additional_context="...")

    def output_for_host_a(self, hook_input: HookInput, result: HookResult) -> dict[str, object]:
        return self.shared_output(hook_input, result)

    def output_for_host_b(self, hook_input: HookInput, result: HookResult) -> dict[str, object]:
        return self.shared_output(hook_input, result)

    def output_for_host_c(self, hook_input: HookInput, result: HookResult) -> dict[str, object]:
        return self.shared_output(hook_input, result)


if __name__ == "__main__":
    host = parse_platform_args()
    raise SystemExit(InjectDispatcherContextHook().run(host))
```

Treat the host-specific method and field names above as placeholders. Adapt them to the exact names exposed by the live scaffold in `llmhooks/lib/cross_host.py`.

The class should be the source of truth for both runtime behavior and installation metadata. Installer code should resolve `event` / `matcher` through the hook class instead of duplicating those values elsewhere.

Installable hooks should also be listed in `llmhooks/registry.py`. The installer should import that registry and install every registered hook for the current host automatically.

Resolution rule:

- if a host-specific event override is not `None`, use it; otherwise use `event`
- if a host-specific matcher override is not `None`, use it; otherwise use `matcher`

Use `None`, not `NaN`, for “inherit the shared default”.

For very small hooks, plain functions are enough. Use the class only when several hooks share lifecycle parsing, result types, or adapter behavior, or when the hook cleanly fits the standard parse/build/emit/install lifecycle.

## Installed-command rule

Installed hook commands must select the host explicitly. The host-specific installer or registration code must write the selector into the host config.

Avoid a bare command with no selector when the script would otherwise guess the host from environment variables alone.

## Lifecycle mapping

Map purpose to host lifecycle independently:

```text
purpose: inject session context
host A binding: session-entry event with startup/clear/compact-style sources
host B binding: session-entry event with startup/clear/compact-style sources
other host: its closest session-entry event
```

For another purpose, the event names may diverge:

```text
purpose: persist transcript metadata
host A binding: host-specific turn/session completion event
host B binding: host-specific stop or transcript event
other host: closest durable-completion event
```

Do not encode the purpose as `SessionStart` unless the purpose truly is host-specific to that lifecycle event. Prefer naming modules by purpose, then binding them to events in the installer.

When the hosts mostly agree, keep the shared values in `event` and `matcher` and override only the differing hosts. This keeps the class readable.

## Test requirements

For each supported host, add golden tests that verify:

- minimal environment produces the expected host output shape
- unrelated host environment variables do not change the selected output shape
- stdin payload is parsed correctly for that host's event
- no platform selector exits nonzero with a clear error
- missing optional shared dependencies still produce valid host JSON when possible
- installer resolution of event, matcher, and command matches the class metadata

A regression test for the current failure class should assert that an explicit host selector still emits the right output shape even when unrelated host variables are present, and that a minimal-environment startup does not fall back to a legacy top-level shape.

## Registration checklist

When adding or changing a cross-host hook, update and verify every active registration path:

- plugin registration for each host that loads plugin hooks
- development-mode or user-config installer for each host
- trust-state or approval documentation, if the host records hook trust separately
- tests for the installer-rendered command strings

Treat these paths as a matched set; updating only one path leaves a host silently stale.

The long-term target is installer automation: installers should be able to import the hook class, ask it for the resolved binding for each host, and register the hook without hardcoding event/matcher/flag triples in multiple places.
