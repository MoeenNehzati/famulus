---
name: node-certify
description: >-
  Use when fresh certificates are requested for one or more Officina nodes. Do not use merely to check certificate currentness or canonical node hashes.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `node-certify._rtx.interface.certify@2` — Certify exact v6 module closures by skipping current nodes and appending signed certificate histories for stale nodes at an explicit reviewed repository commit.
  - `dispatcher --caller-skill node-certify node-certify._rtx.interface.certify certify [target ...] --reviewed-repository ROOT --reviewed-commit COMMIT [--json] [--allow-non-atomic]`
- `node-drift._rtx.interface.drift-status@3` — Read signed certificate currentness, exact structured drift causes, and the dependency-first stale worklist for exact or installed v6 modules without writing certification state.
  - `dispatcher --caller-skill node-certify node-drift._rtx.interface.drift-status [target ...] [--all] [--repo-root ROOT | --skill-root ROOT] [--json]`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `node-certify.source.audit-behavioral-source.interface.audit@1` — Audit one behavioral source and return bounded semantic evidence and a verdict.
- `node-certify.source.audit-interface.interface.audit@1` — Audit one source interface and return bounded semantic evidence and a verdict.
- `node-certify.source.audit-module.interface.audit@1` — Audit one module and return bounded semantic evidence and a verdict.
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
