---
name: setup-interface-manager
description: Use only when a generated managed-setup gate, a Famulus setup-required result, or an exact manager lifecycle route directs the agent here.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `setup-interface-manager._rtx.interface.authorize` — Resume only unmanaged or ready targets and atomically claim every ready managed receipt.
  - Caller: `setup-interface-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["TARGET_INTERFACE", "ORIGINAL_CALLER", "ORIGINAL_INTERFACE", "ORIGINAL_VERSION"], "stdin": null}
    Required options: []; positional arity: 4..4; stdin: forbidden
- `setup-interface-manager._rtx.interface.begin` — Begin exactly one setup or teardown flow for a managed root and redacted continuation.
  - Caller: `setup-interface-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["OPERATION", "ROOT_SETUP", "ORIGINAL_CALLER", "ORIGINAL_INTERFACE", "ORIGINAL_VERSION"], "stdin": null}
    Required options: []; positional arity: 5..5; stdin: forbidden
- `setup-interface-manager._rtx.interface.invalidate` — Invalidate one setup receipt and its live managed dependents only while idle.
  - Caller: `setup-interface-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["SETUP_INTERFACE"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `setup-interface-manager._rtx.interface.recover` — Retry by verifying first, or cancel without guessing the current external action's completion.
  - Caller: `setup-interface-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["FLOW_ID", "ACTION"], "stdin": null}
    Required options: []; positional arity: 2..2; stdin: forbidden
- `setup-interface-manager._rtx.interface.run-markdown` — Return only the finite map's exact Markdown instructions and await independent settlement.
  - Caller: `setup-interface-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["FLOW_ID", "INTERFACE"], "stdin": null}
    Required options: []; positional arity: 2..2; stdin: forbidden
- `setup-interface-manager._rtx.interface.run-python` — Run the exact current Python action, its verifier, and the receipt transition in one call.
  - Caller: `setup-interface-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["FLOW_ID", "INTERFACE"], "stdin": null}
    Required options: []; positional arity: 2..2; stdin: permitted
- `setup-interface-manager._rtx.interface.settle` — Independently verify and settle only the exact current Markdown action.
  - Caller: `setup-interface-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["FLOW_ID", "INTERFACE"], "stdin": null}
    Required options: []; positional arity: 2..2; stdin: forbidden
- `setup-interface-manager._rtx.interface.status` — Return unmanaged, ready, setup-required, or setup-busy without mutating claims.
  - Caller: `setup-interface-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["TARGET_INTERFACE"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden

<!-- END BLUEPRINT INTERFACES -->
Skill: setup-interface-manager

This hidden workflow controller is entered only from a generated managed-setup
gate, a structured Famulus setup result, an explicit status request, or an exact
managed setup or teardown invocation. Generic discussion about setup,
installation, configuration, readiness, or teardown does not activate it.

Preserve the original caller, interface, version, arguments, and stdin outside
the manager. Pass only the non-sensitive continuation identity accepted by the
exact manager route. Follow `current_step` exactly, never substitute an
interface or ledger path, and resume the original request only when
`resume_original` is true.
