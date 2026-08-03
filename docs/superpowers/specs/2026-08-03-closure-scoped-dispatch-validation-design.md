# Closure-Scoped Dispatch and Immediate-Caller Authorization

**Date:** 2026-08-03
**Status:** Implemented

## Goal

Allow dispatcher to execute a valid invocation when unrelated repository
blueprints are invalid, and make authorization depend only on the immediate
calling module and target-owned access policies.

Relevant blueprints remain required. Strict repository validation remains
repository-wide.

## Authorization Rule

For an immediate caller module `x` and an access policy owned by module `y`,
the policy admits `x` exactly when:

```text
x == y
or access.allow_all_modules
or ancestry(x) intersects resolved(access.allowed_callers)
```

`ancestry(x)` includes `x` and every registered parent. Each caller reference
resolves to one exact module; naming that module admits its registered subtree.

Consequences:

- self-access is implicit;
- an empty, non-public allowlist is private to the owner;
- naming `A` admits `A` and descendants such as `A/B`;
- naming `A/B` does not admit `A` or sibling `A/C`;
- `A/B` may call `A/C` when `A/C` admits `A`, `A/B`, or all modules;
- only the immediate caller participates; no upstream caller chain is carried
  or evaluated.

The same predicate applies independently to every crossed namespace filter,
facade export, and terminal export. Existing lowest-common-ancestor routing
determines which target-side namespace filters are crossed. The caller is
hop-local: after a namespace owner or facade accepts a call, that owner is the
immediate caller of the next hop. The original upstream caller is not tested
against downstream policies.

## Caller Identity

Runtime permission is module-scoped. `caller_source_id` may remain in metadata
for tracing and certification but is not an authorization input.
`uses_interfaces` remains static dependency and certification metadata; it is
not a runtime permission condition.

Language-native calls declare their immediate caller with
`DispatchCall.caller_module_id`. Static validation must prove this value equals
the deepest registered module owning the Python file containing the
declaration. Dynamic or missing caller IDs fail validation.

Runtime dispatch uses the declaration's immediate caller directly. It does
not inherit identity from the interface that invoked the current module.
Direct host CLI calls remain restricted to discoverable skill modules.

This remains a cooperative same-user boundary: repository validation proves
caller declarations; the architecture does not claim process isolation
against arbitrary hostile local code.

## Invocation Closure

For one request, the required blueprint closure contains:

1. the immediate caller module and its registered ancestry;
2. the requested export, facades, namespace routes, and terminal export;
3. the implementing behavioral source and process binding;
4. modules named by evaluated access policies and the topology needed to
   resolve their subtrees;
5. directly referenced interfaces and behavioral-source dependencies needed
   by selected modules.

The closure is conservative. A blueprint defect is a warning only when the
scoped canonical graph succeeds and therefore proves the defect is outside the
required closure. Ambiguous identities remain fatal.

## Resolution and Diagnostics

Dispatcher first attempts the strict repository graph. If it fails, dispatcher
builds the required closure from tolerant inventory and passes that selected
document set through the existing canonical v5 graph builder and authorization
resolver.

Fatal errors include ambiguity, invalid relevant blueprints, missing routes,
denied target access, unsafe paths, and invalid process bindings. Proven
unrelated blueprint defects are warnings. Missing, stale, malformed, or
unavailable certification is also advisory at runtime.

Warnings are structured in dry-run output and rendered on stderr during
execution. Child stdout, stderr, and exit status remain unchanged.

## Validation

The caller-module validator must inspect every graph-owned Python file, not
only skill `_rtx` and `bin` directories. For every direct `dispatch(...)` or
`DispatchCall(...)` declaration it must:

- require a literal or module-level constant caller ID;
- derive the deepest registered module owning the file;
- reject any mismatch;
- continue excluding test fixtures from production caller declarations.

Repository-wide graph validators remain strict and continue resolving
`uses_interfaces`, dependencies, topology, exports, and access declarations.

## Compatibility

Clean repository graphs retain identical route and process-binding resolution.
Source IDs remain available in result metadata but cease to affect permission.
No direct-script or last-known-good cache fallback is introduced.

## Required Tests

Tests must prove:

1. self, public, exact caller, and ancestor caller admission;
2. parent admission grants descendants but child admission does not grant its
   parent or siblings;
3. `A/B -> A/C` follows `A/C`'s policy and relevant target-side routes;
4. authorization succeeds without `caller_source_id` and regardless of
   source-level `uses_interfaces`;
5. nested `PythonMachineInterface.dispatch()` uses its own declared immediate
   caller without propagated runtime context;
6. wrong or dynamic caller declarations fail for skill and non-skill modules;
7. unrelated invalid blueprints warn while relevant or ambiguous defects fail;
8. all certification failures warn;
9. dry-run and executed diagnostics preserve process behavior;
10. the live list-manager cloud read succeeds through cloud-files.

## Non-Goals

- propagating an upstream caller chain;
- using behavioral-source identity as runtime permission;
- process isolation against hostile local code;
- weakening strict repository validation;
- dispatching without relevant blueprints.
