---
name: skill-drift
description: Use when reading signed certificate currentness or canonical node hashes for Famulus modules.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-assurance, assistant-architecture; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces: none

Public Interfaces:
- `skill-drift.interface.compute-hashes`
- `skill-drift.interface.default`
- `skill-drift.interface.drift-status`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `skill-drift.interface.compute-hashes` — Compute canonical certification-basis and v5 node hashes for exact or installed modules.
  - `dispatcher --caller-skill skill-drift skill-drift.interface.compute-hashes compute-hashes [target ...] [--skill-root ROOT] [--json]`
- `skill-drift.interface.drift-status` — Read signed certificate currentness for exact or installed v5 modules without writing certification state.
  - `dispatcher --caller-skill skill-drift skill-drift.interface.drift-status status [target ...] [--all] [--skill-root ROOT] [--json]`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `skill-drift.interface.default` — Instructions for exact-target certificate drift and canonical node-hash checks.
<!-- END BLUEPRINT INTERFACES -->
Use `skill-drift.interface.drift-status` to read signed certificate
currentness and `skill-drift.interface.compute-hashes` to read the canonical
certification-basis and node hashes.

Both routes accept only canonical v5 repository graphs. They derive state through the
shared certification view and never create keys, sign payloads, append
certificates, run validators, or execute target code.

An exact `--skill-root` request selects only nodes owned by that module. Named
requests resolve matching installed copies across supported hosts. With no
target, the checker scans observed installed module roots.

JSON status is certification-state read-only. Human-readable status also saves
a derived report under `_build/certificate-drift-<timestamp>.md`; that report
has no certification authority. Hash output never writes a report.

Report `certificate-current` only when every selected node has a valid signed
certificate matching its current node hash, dependencies, certification basis,
certifier functional identity, and expected checks. The signed `source_commit`
is issuance provenance for restoring the audited snapshot; it is not required
to equal current `HEAD`. Otherwise report `certificate-stale` with the exact
node concerns.

Writing certification state belongs solely to `skill-certifier`.
