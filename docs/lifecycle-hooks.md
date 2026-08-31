# LLM Lifecycle Hooks

> **Status:** Nonnormative orientation.

Lifecycle hooks give an LLM the small amount of stable context it needs to
operate inside Famulus's bounded Officina system. They do not teach the model
the whole architecture or bypass an Officina boundary. They tell it how to use
the public interfaces that expose the bounded system.

This page covers assistant-session hooks. Git pre-commit and pre-push hooks are
documented separately in [Repository Testing](testing.md#local-hook).

## Why the context is injected

An assistant starts without necessarily knowing the repository's interface
protocol, and context can be lost when a session is cleared or compacted. A
lifecycle hook restores the minimum protocol at those boundaries. Keeping the
payload short and stable avoids making session startup depend on a large copy
of repository documentation.

The current payload tells the assistant to:

- invoke executable skill interfaces through the Famulus MCP server using the
  interface's declared invocation metadata;
- read instruction interfaces directly;
- avoid invoking private scripts directly; and
- report an unavailable Famulus MCP server and request the repository's
  recovery workflow.

These instructions make the public boundary usable. The Dispatcher and other
Officina machinery remain responsible for resolution, authorization, and
bounded execution.

## Current hook

The hook metadata registry declares one hook, `inject-dispatcher-context`, for
Claude and Codex. No installer currently consumes that registry, so it records
shared metadata rather than proving that either host attached the hook. The
hook handles the `SessionStart` event with the matcher
`startup|clear|compact`.

When a host attaches and invokes the shared hook, it sends its event payload to
the hook. The hook builds one host-neutral semantic result, then the cross-host
adapter emits the JSON shape expected by that host. The result is additional
session context, not a user message and not a request to perform work.

The repository contains explicit bindings for the packaged Claude plugin and
the background Claude profile. The implementation also has output adapters for
Codex and Cursor, while the metadata registry lists Claude and Codex. Direct
tests validate those declarations, adapters, and entrypoints; they do not prove
that a host attached the hook, and there is no host-observed Codex telemetry.

## Sources of truth

- [`llmhooks/inject_dispatcher_context.py`](../llmhooks/inject_dispatcher_context.py)
  owns the injected text, event, and matcher.
- [`llmhooks/lib/cross_host.py`](../llmhooks/lib/cross_host.py) owns input
  parsing, semantic results, host output adapters, and install-binding shape.
- [`llmhooks/registry.py`](../llmhooks/registry.py) declares shared hook and
  host metadata; no current installer consumes it.
- [`hooks/hooks.json`](../hooks/hooks.json) declares the packaged Claude plugin
  binding.
- [`profiles/background_run_claude_setting.json`](../profiles/background_run_claude_setting.json)
  declares the background Claude binding.
- [`hooks/tests/test_inject_dispatcher_context.py`](../hooks/tests/test_inject_dispatcher_context.py)
  checks the payload, declared bindings, direct entrypoints, host-shaped output,
  and bounded size. It does not prove host attachment.

When adding or changing a cross-host assistant lifecycle hook, use the
[`hook-maker` skill](../skills/hook-maker/SKILL.md). It owns the cross-host
design and validation workflow; this page should remain an orientation to the
live machinery rather than duplicate that workflow.
