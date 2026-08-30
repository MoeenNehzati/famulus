---
name: node-certify
description: >-
  Use when fresh certificates are requested for one or more Officina nodes. Do not use merely to check certificate currentness or canonical node hashes.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-assurance, assistant-architecture; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 5

Uses Interfaces:
- `node-certify.source.gateway -> node-certify._rtx.interface.certify@2`
- `node-certify.source.gateway -> node-certify.source.audit-behavioral-source.interface.audit@1`
- `node-certify.source.gateway -> node-certify.source.audit-interface.interface.audit@1`
- `node-certify.source.gateway -> node-certify.source.audit-module.interface.audit@1`
- `node-certify.source.gateway -> node-drift._rtx.interface.drift-status@3`

Public Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `node-certify._rtx.interface.certify` — Certify exact v6 module closures by skipping current nodes and appending signed certificate histories for stale nodes at an explicit reviewed repository commit.
  - Caller: `node-certify`
  - Version: 2
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--allow-non-atomic": true, "--json": true, "--reviewed-commit": "COMMIT", "--reviewed-repository": "ROOT"}, "positionals": ["certify", "target..."], "stdin": null}
    Required options: []; positional arity: 1..unbounded; stdin: forbidden

<!-- END BLUEPRINT INTERFACES -->
## Certification algorithm

Resolve the requested target and hold its reviewed repository and commit
stable. Then:

1. Invoke `node-drift._rtx.interface.drift-status` in JSON mode. Use its stale
   worklist to identify each exact changed file, interface, or dependency cause.
2. Process only that worklist dependency-first and select audits from each exact
   cause. A `certification-basis-mismatch` is unclassified global drift, so
   repeat all required semantic review. With a matching basis, a sole mechanical
   certify `certified-under` cause requires no semantic audit.
3. For remaining semantic causes, interface facets are leaves inside stale source
   nodes; use `audit-interface` for affected facets, then
   `audit-behavioral-source` and `audit-module` only for affected sources and
   module ancestors. Changed files belong to their owning facet.
4. When an audit returns `needs-context`, read the smallest additional evidence
   or context scope it names and repeat that audit. Do not widen otherwise.
   Stop on `reject` or an unresolved evidence gap.
5. Invoke the declared mechanical `certify` interface for the requested target
   and exact reviewed repository and commit. It independently recomputes
   currentness, skips current nodes, route-smokes the stale worklist, and issues
   stale nodes dependency-first.

With a matching basis, reuse authenticated semantic evidence when its facet
local hash or module node hash, input manifest, ordinary dependencies, and
governing semantic `certified-under` claim still match. The mechanical certify
claim remains required for currentness and issuance but does not by itself
require semantic re-audit. A remainder-facet cause belongs to
`audit-behavioral-source`; it does not create a remainder interface.

Schema validity is necessary but does not establish semantic accuracy. The
audit interfaces own semantic judgment. The mechanical interface invokes the
repository validator runner, reconstructs every payload field, computes hashes,
signs, appends certificate history, and performs post-write drift verification.
Never ask it to sign caller-supplied certificate data.

Existing logs must be canonical, schema-valid, signature-valid, unbroken, and
a dependency-first prefix of the exact closure. New certificates require
tracked inputs to match the reviewed commit and included local inputs to remain
byte-stable. Dirty or unready state may be reported but must not be certified.

If synchronization, validation, semantic audit, hashing, signing, or post-write
verification fails, retain earlier valid append-only history, report the exact
failure, and do not claim current certification.
