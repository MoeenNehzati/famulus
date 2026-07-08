---
name: hook-maker
description: Use when designing, creating, installing, or refactoring assistant hooks that must work across multiple hosts or future agent runtimes, especially when a shared hook purpose needs host-specific lifecycle bindings or output schemas.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-development-assistant

Dependencies: none

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
## Core rule

Design hooks by separating:

1. **Purpose** — the semantic action the hook performs.
2. **Binding** — the host lifecycle event that invokes that purpose.
3. **Adapter** — the host-specific stdin parsing, output schema, and exit behavior.

Never make output schema depend on ambient environment variables. Installed hooks must select their host explicitly through the host-specific installer or registration path.

## Workflow

1. Name the shared purpose first, before choosing event names. Examples: inject session context, summarize after stop, persist transcript metadata, block unsafe command.
2. Map that purpose to each host lifecycle separately. Do not assume the same event exists or means the same thing across hosts.
3. Use one hook module per purpose unless the host APIs are so different that separate modules are simpler.
4. Keep shared logic host-neutral. Shared logic returns semantic data, not host-shaped JSON.
5. Put host-specific branches in named adapter functions on the hook class.
6. Prefer subclassing `llmhooks/lib/cross_host.py:CrossHostHook` when the hook follows the standard parse/build/emit lifecycle. Bypass it only when the hook contract materially differs.
7. Put lifecycle binding metadata on the hook class itself. Shared fields like `event` and `matcher` may be overridden by per-host event and matcher fields.
8. Require an explicit host selector in every installed command or wrapper. The user should not type this manually; the installer or host-specific config must force it.
9. Read stdin for lifecycle payload data, but not as the primary source of host identity when the installed config already knows the host.
10. Use environment variables only for host-provided paths or data roots. Do not let environment variables silently change output format.
11. Add golden tests for each host binding and output shape, including a minimal-env case and an env-noise case.
12. Register each installable hook in `llmhooks/registry.py` so installers can install every managed hook automatically.
13. When changing an existing hook, update all registration paths that install that hook for the supported hosts.

## Scaffold reference

Read `references/cross-host-hook-scaffold.md` before designing or editing a cross-host hook. The reusable base scaffold lives at `llmhooks/lib/cross_host.py`.
