---
name: skill-certifier
description: >-
  Use when fresh certificates are requested for one or more Officina nodes. Do not use merely to check certificate currentness or canonical node hashes.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-assurance, assistant-architecture; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 6

Uses Interfaces:
- `skill-certifier.source.gateway -> skill-certifier._rtx.interface.certify@2`
- `skill-certifier.source.gateway -> skill-certifier._rtx.interface.semantic-audit-scheduler@1`
- `skill-certifier.source.gateway -> skill-certifier.source.audit-behavioral-source.interface.audit@2`
- `skill-certifier.source.gateway -> skill-certifier.source.audit-interface.interface.audit@2`
- `skill-certifier.source.gateway -> skill-certifier.source.audit-module.interface.audit@2`
- `skill-certifier.source.gateway -> skill-drift._rtx.interface.drift-status@4`

Public Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
## Certification algorithm

Resolve the requested target and hold its reviewed repository and commit
stable. Then:

1. Invoke `skill-drift._rtx.interface.drift-status@4` once in JSON mode with
   `--dag-file`, and save its JSON result for scheduler initialization.
2. Initialize `skill-certifier._rtx.interface.semantic-audit-scheduler@1` from
   the DAG and drift result. The
   scheduler, not the LLM, owns dependency traversal and audit readiness.
3. Determine available subagent slots `K`, excluding the orchestrator; use one
   if unknown. Call `claim --capacity K`. For each returned item, spawn one fresh
   subagent and pass `input_file` unchanged. Map `interface` to
   `skill-certifier.source.audit-interface.interface.audit@2`,
   `behavioral-source` to
   `skill-certifier.source.audit-behavioral-source.interface.audit@2`, and
   `module` to `skill-certifier.source.audit-module.interface.audit@2`. Never
   reuse a subagent for another task.
4. Write each exact final JSON result to its own report file and call
   `complete PREFIX TASK_ID --report-file FILE`, then claim again to refill the
   pool. If no task is returned while work remains in progress, wait. On spawn
   failure or worker loss call `fail`. Stop all remaining work on malformed
   output, `reject`, `abort`, or scheduler failure; do not infer readiness or
   recursively audit dependencies.
5. Only after scheduler status is `complete`, invoke the declared mechanical
   `certify` interface for the requested target
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
