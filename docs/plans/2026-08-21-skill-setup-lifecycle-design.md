# Setup Interface Dependency Design

## Goal

Represent setup prerequisites that ordinary interface-use dependencies do not
capture. Keep the mechanism declarative and separate from runtime call authority.

## Blueprint contract

A public setup export is named `<module>.interface.setup`. It aliases an existing
source interface, so adding setup metadata does not change the setup behavior.

Every setup export declares `setup_requires_setup_of`, including an empty list:

```yaml
exports:
  list-manager.interface.setup:
    source_interface: list-manager.source.gateway.interface.default
    access:
      allow_all_modules: true
      allowed_callers: []
    setup_requires_setup_of:
    - interface: connect-google.interface.setup
      version: 1
```

The field is forbidden on non-setup exports. Each entry names another public
setup export and pins its exact interface version. Duplicate prerequisites,
missing targets, version mismatches, and cycles are invalid.

## Ordering

`setup_order(graph, root)` returns explicit prerequisites before the requested
setup interface. It is deterministic, deduplicates shared prerequisites, and is
iterative so long chains do not consume Python recursion depth.

This graph is separate from `uses_interfaces`. A setup prerequisite says what
must already be set up; it neither grants dispatcher authority nor creates a
runtime dependency.

The base Famulus or assistant-tools installation never belongs in this list.
Famulus cannot start without it, so that prerequisite is handled separately by
a session-start hook. This design does not add or modify that hook.

## Generated contract

Generated skill contracts show direct prerequisites in a separate
`Setup Requires Setup Of` section and use `setup_order` to render the complete,
deduplicated, dependency-first `Setup Order`.

## Initial declarations

- `install-assistant-tools.interface.setup` has no prerequisite.
- `connect-google.interface.setup` has no prerequisite.
- `cloud-files.interface.setup`, `online-calendar.interface.setup`, and
  `list-manager.interface.setup` require `connect-google.interface.setup`.

For this initial refactor, each setup export aliases that skill's existing
default interface. Existing functional instructions remain unchanged. This is
not a restriction on future setup interfaces with dedicated behavior.

## Non-goals

- Receipts or setup-state tracking.
- Teardown orchestration.
- Invalidation or recovery protocols.
- Background lifecycle coordinators or Rutter integration.
- Inferring setup dependencies from ordinary interface use.
- A session-start hook.
