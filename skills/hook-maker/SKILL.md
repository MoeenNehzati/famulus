---
name: hook-maker
description: >-
  Use when the user asks to create or change an assistant lifecycle hook that must support multiple hosts or runtimes. Do not use for ordinary Git hooks or single-host automation.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-authoring, assistant-architecture; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces: none

Public Interfaces:
- `hook-maker.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `hook-maker.interface.default` — Design or implement a hook from semantic purpose through explicit host bindings, adapters, registration, and golden verification.
<!-- END BLUEPRINT INTERFACES -->
## Core rule

Design hooks by separating:

1. **Purpose** — the semantic action the hook performs.
2. **Binding** — the host lifecycle event that invokes that purpose.
3. **Adapter** — the host-specific stdin parsing, output schema, and exit behavior.

Unrelated ambient environment variables must never select output shape. Generated and development registrations must select the host explicitly. When hosts necessarily share one static plugin registration, its compatibility adapter may instead use one isolated host-owned registration signal, with an explicit fallback and golden tests for each host case.

## Workflow

1. Name the shared purpose first, before choosing event names. Examples: inject session context, summarize after stop, persist transcript metadata, block unsafe command.
2. Map that purpose to each host lifecycle separately. Do not assume the same event exists or means the same thing across hosts.
3. Use one hook module per purpose unless the host APIs are so different that separate modules are simpler.
4. Keep shared logic host-neutral. Shared logic returns semantic data, not host-shaped JSON.
5. Put host-specific branches in named adapter functions on the hook class.
6. Inspect the repository's live cross-host base abstraction and reuse its standard parse/build/emit lifecycle when the hook fits it. Bypass it only when the hook contract materially differs.
7. Put lifecycle binding metadata on the hook class itself. Shared fields like `event` and `matcher` may be overridden by per-host event and matcher fields.
8. Put an explicit host selector in every generated or development command; the user should not type it manually. Apply the shared-static-registration exception from the core rule only when the host cannot provide distinct registrations.
9. Read stdin for lifecycle payload data, but not as the primary source of host identity when the installed config already knows the host.
10. Use environment variables only for host-provided paths or data roots, including the narrowly permitted shared-registration signal. Unrelated variables must not change output format.
11. Add golden tests for each host binding and output shape, including minimal-env and env-noise cases; when the shared-registration exception applies, test each host signal and the documented fallback.
12. Register each installable hook in the repository's canonical hook registry so installers can install every managed hook automatically.
13. When changing an existing hook, update all registration paths that install that hook for the supported hosts.

## Scaffold reference

Read `references/cross-host-hook-scaffold.md` before designing or editing a cross-host hook, then locate and inspect the live base abstraction, registry, installers, host contracts, and tests rather than assuming their paths or APIs.
