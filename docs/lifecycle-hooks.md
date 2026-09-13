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

When the dispatcher-context hook runs, its payload tells the assistant to:

- invoke executable skill interfaces through the Famulus MCP server using the
  interface's declared invocation metadata;
- read instruction interfaces directly;
- avoid invoking private scripts directly.

A separate startup-only hook checks the dedicated dispatcher Python runtime.
When that hook runs and the runtime is missing or does not satisfy the MCP
requirements, it adds the launcher's exact diagnosis and bootstrap route to
the initial context and surfaces the same text as a user-visible warning.

These instructions make the public boundary usable. The Dispatcher and other
Officina machinery remain responsible for resolution, authorization, and
bounded execution.

## Current hooks

The hook metadata registry declares two cross-host hook implementations and
output adapters. No installer consumes that registry, so it records shared
metadata rather than attaching either hook to a host.

- `inject-dispatcher-context` handles `SessionStart` with the matcher
  `startup|clear|compact` so the interface protocol is restored after context
  loss.
- `diagnose-dispatcher-runtime` handles `SessionStart` with the matcher
  `startup` so runtime health is checked once per new session, not after clear
  or compaction.

When a host attaches and invokes a shared hook, it sends its event payload to
the hook. The hook builds one host-neutral semantic result, then the cross-host
adapter emits the JSON shape expected by that host. The result is additional
session context, not a request to perform work. The runtime-diagnosis adapter
also supplies the diagnosis as a host-visible system message.

The packaged `hooks/hooks.json` is discovered by Claude, and fresh Claude hook
telemetry has verified both SessionStart hooks. The same file is present in the
installed root-format Codex plugin, but the tested Codex release did not execute
it: a fresh session received neither the dispatcher context nor the startup
diagnosis. Codex therefore has the MCP server but not these automatic hook
effects. This is a current host compatibility gap, not evidence that the shared
hook implementations or Codex output adapter failed.

The background Claude profile has its own explicit binding. Direct tests
validate the declarations, adapters, and entrypoints, but do not replace fresh
host evidence of attachment.

## Sources of truth

- [`llmhooks/inject_dispatcher_context.py`](../llmhooks/inject_dispatcher_context.py)
  owns the injected text, event, and matcher.
- [`llmhooks/diagnose_dispatcher_runtime.py`](../llmhooks/diagnose_dispatcher_runtime.py)
  owns the startup-only dispatcher runtime diagnosis.
- [`llmhooks/lib/cross_host.py`](../llmhooks/lib/cross_host.py) owns input
  parsing, semantic results, host output adapters, and install-binding shape.
- [`llmhooks/registry.py`](../llmhooks/registry.py) declares shared hook and
  host metadata; no current installer consumes it.
- [`hooks/hooks.json`](../hooks/hooks.json) declares the packaged hook bindings.
  Claude currently discovers them; the tested root-format Codex installation
  does not execute them.
- [`profiles/background_run_claude_setting.json`](../profiles/background_run_claude_setting.json)
  declares the background Claude binding.
- [`hooks/tests/test_inject_dispatcher_context.py`](../hooks/tests/test_inject_dispatcher_context.py)
  checks the payload, declared bindings, direct entrypoints, host-shaped output,
  and bounded size. It does not prove host attachment.
- [`hooks/tests/test_diagnose_dispatcher_runtime.py`](../hooks/tests/test_diagnose_dispatcher_runtime.py)
  checks startup-only binding, launcher-error reuse, registrations, and both
  host output shapes.

When adding or changing a cross-host assistant lifecycle hook, use the
[`hook-maker` skill](../skills/hook-maker/SKILL.md). It owns the cross-host
design and validation workflow; this page should remain an orientation to the
live machinery rather than duplicate that workflow.
