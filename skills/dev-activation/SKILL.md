---
name: dev-activation
description: Use when a developer needs an assistant or editor to run against one Famulus checkout without discovering globally installed skills or plugins.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `dev-activation._rtx.interface.activation` — Create, validate, or report one isolated development activation.
  - Caller: `dev-activation`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--checkout": "absolute-path", "--platform": "platform"}, "positionals": ["create|validate|report"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden

<!-- END BLUEPRINT INTERFACES -->
Skill: dev-activation

Use the runtime activation interface to create or validate the checkout-local home. Report the portable entry commands returned by `report`.

For an assistant session, read the sole host-route file under `instructions/` and use its exact shared-Python route. For another command, use `exec -- <argv>`.

Activation changes `HOME` as well as the assistant-specific homes. It preserves ordinary host facilities and a narrow existing Git global-config path, but removes Python, installer, and feature state selectors. It creates only ignored directories below `<checkout>/.famulus/home`; it never installs or repairs Python, packages, Dispatcher, MCP, launchers, services, credentials, or optional features.

`.envrc` and tracked host wrappers are optional conveniences. The portable contract keeps exact command `python` at the boundary and preserves the shared checkout-home behavior described above.
