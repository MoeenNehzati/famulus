---
name: skill-certifier
description: Use when mechanical checks and semantic review should issue fresh node certificates for an exact committed repository state.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-assurance, assistant-architecture; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `skill-certifier.source.gateway -> skill-certifier._rtx.interface.certify@1`

Public Interfaces:
- `skill-certifier.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `skill-certifier.interface.default` — Certify exact target closures and report signed certificate outcomes.
<!-- END BLUEPRINT INTERFACES -->
## Certification Rules

Use this skill only after reviewing the selected module and behavioral-source
blueprints against their gateways, content, and actual behavior. Schema
validity is necessary but does not establish semantic accuracy.

The review must establish:

- every operation and argument is independently usable as declared, and the
  process binding accepts exactly the documented invocation;
- outputs, outcomes, errors, lifecycle, interaction mode, and verification
  describe observed behavior;
- reads, writes, network access, effects, helpers, dependencies, filesystem
  authority, and protected-file boundaries are complete and mutually
  consistent;
- sensitivity, preconditions, warnings, platform claims, runtime dependencies,
  and version compatibility are accurate;
- every implicit instruction or implementation dependency is represented by
  direct ownership, a declared interface use, or the certification basis.

Mechanical certification runs only through
`skill-certifier._rtx.interface.certify`. It invokes the repository validator runner
once, then owns hash computation, signing, append-only certificate writes, and
post-write drift verification. It reconstructs every payload field internally
and accepts only the exact reviewed repository and commit; it never signs
caller-supplied certificate data.

The canonical certification view normally requires current certificates. It
also admits exact self-certification of `skill-certifier` when its certification
closure has no history or has appendable canonical, schema-valid,
signature-valid, unbroken history; the final signing key may be inactive.
Existing logs must form a dependency-first prefix of the exact closure. Corrupt
history, a non-prefix gap, a wrong-subject entry, or missing verification
material fails closed.

An exact target includes its certification dependency closure. Omitted targets
select all repository nodes. New certificates require tracked inputs to match
the reviewed commit and included local inputs to remain byte-stable. Dirty or
unready state may be reported but must not be certified.

If synchronization, structural validation, semantic review, hashing, signing,
or post-write verification fails, retain earlier valid append-only history,
report the failure, and do not claim current certification.
