# Cross-host hook design reference

Use this reference when implementing a hook that serves one semantic purpose across multiple assistant hosts. Before editing, locate and read the repository's live cross-host abstraction, canonical registry, installers, supported-host contracts, and golden tests. Those artifacts own concrete paths, names, and APIs.

## Architecture

Keep shared logic host-neutral. It returns semantic values such as additional context, decision state, diagnostics, or files to write; it does not emit host-shaped JSON.

Each host adapter owns:

- accepted lifecycle event names and matchers
- stdin payload interpretation
- JSON output shape
- stdout versus stderr rules
- exit-code meaning
- host-required truncation and escaping

Keep installation metadata with the hook implementation:

- shared default event and matcher
- per-host event and matcher overrides
- the exact host selector used in installed commands

Prefer the live shared abstraction when the hook fits its standard parse/build/emit/install lifecycle. Use one module per purpose when the logic is small. Plain functions remain appropriate for a very small hook; a class is useful when hooks share lifecycle parsing, semantic result types, adapter behavior, or installation metadata.

The hook implementation is the source of truth for runtime behavior and installation metadata. Installers resolve bindings from it instead of duplicating event, matcher, and selector values. A host-specific override takes precedence over the shared default; use the live abstraction's null sentinel to inherit, never a numeric placeholder.

Register every installable hook in the canonical registry, and make installers install the registered hooks supported by the current host.

## Registration-selection rule

Generated and development registrations must write an explicit host selector into the installed command. The user should not supply it manually.

If multiple hosts necessarily consume one static plugin registration, a compatibility adapter may select the host from one isolated host-owned registration signal. Keep that exception local to the shared registration, define its fallback explicitly, and never infer output shape from unrelated ambient variables.

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

Do not name a hook for one event unless its purpose is genuinely specific to that lifecycle. Name modules by purpose, then bind them to events during installation.

When the hosts mostly agree, keep the shared values in `event` and `matcher` and override only the differing hosts. This keeps the class readable.

## Test requirements

For each supported host, add golden tests that verify:

- minimal environment produces the expected host output shape
- unrelated host environment variables do not change the selected output shape
- stdin payload is parsed correctly for that host's event
- a generated or development entry point with no host selector exits nonzero with a clear error
- a shared static plugin registration selects each supported host from its isolated signal and produces the documented fallback with no signal
- missing optional shared dependencies still produce valid host JSON when possible
- installer resolution of event, matcher, and command matches the class metadata

A regression test for the current failure class should assert that explicit selectors remain stable under unrelated environment noise. When the shared-static-registration exception applies, golden-test both host-signal cases and the no-signal fallback, and verify that unrelated variables cannot select another output shape.

## Registration checklist

When adding or changing a cross-host hook, update and verify every active registration path:

- plugin registration for each host that loads plugin hooks
- development-mode or user-config installer for each host
- trust-state or approval documentation, if the host records hook trust separately
- tests for the installer-rendered command strings

Treat these paths as a matched set; updating only one path leaves a host silently stale.

Installers should obtain each host's resolved binding from the hook implementation and registry rather than hardcoding event, matcher, and selector triples in multiple places.
