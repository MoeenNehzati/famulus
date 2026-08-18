---
name: skill-certifier
description: >-
  Use when fresh certificates are requested for one or more Officina nodes. Do not use merely to check certificate currentness or canonical node hashes.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-assurance, assistant-architecture; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 4

Uses Interfaces:
- `skill-certifier.source.gateway -> skill-certifier._rtx.interface.certify@2`
- `skill-certifier.source.gateway -> skill-certifier.source.audit-behavioral-source.interface.audit@1`
- `skill-certifier.source.gateway -> skill-certifier.source.audit-interface.interface.audit@1`
- `skill-certifier.source.gateway -> skill-certifier.source.audit-module.interface.audit@1`
- `skill-certifier.source.gateway -> skill-drift._rtx.interface.drift-status@2`

Public Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
## Certification algorithm

Resolve the requested target and hold its reviewed repository and commit
stable. Then:

1. Invoke `skill-drift._rtx.interface.drift-status` in JSON mode. Use its stale
   worklist to identify each exact changed file, interface, or dependency cause.
2. Process only that worklist dependency-first. Interface facets are leaves
   inside stale source nodes, not separate worklist nodes. For each stale
   interface facet named by drift, use `audit-interface`; changed files are
   evidence for their owning facet, not separate audit subjects.
3. After affected leaf interfaces pass, use `audit-behavioral-source` only for
   stale sources and affected source ancestors. Use `audit-module` only for
   stale modules and affected module ancestors.
4. When an audit returns `needs-context`, read the smallest additional evidence
   or context scope it names and repeat that audit. Do not widen otherwise.
   Stop on `reject` or an unresolved evidence gap.
5. Invoke the declared mechanical `certify` interface for the requested target
   and exact reviewed repository and commit. It independently recomputes
   currentness, skips current nodes, route-smokes the stale worklist, and issues
   stale nodes dependency-first.

Reuse only unchanged facet evidence whose claim is authenticated by the latest
valid signed certificate and still matches the canonical facet state. A
remainder-facet cause belongs to `audit-behavioral-source`; it does not create a
remainder interface. A non-facet source or module cause starts at that owning
audit rather than forcing unrelated leaf audits.

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
