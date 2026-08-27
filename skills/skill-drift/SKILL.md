---
name: skill-drift
description: >-
  Use when the user asks whether Officina node certificates are current or stale, or asks for canonical node hashes. Do not use to issue certificates.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-assurance, assistant-architecture; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 5

Uses Interfaces:
- `skill-drift.source.gateway -> skill-drift._rtx.interface.compute-hashes@2`
- `skill-drift.source.gateway -> skill-drift._rtx.interface.drift-status@4`

Public Interfaces:
- `skill-drift.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `skill-drift.interface.default` — Instructions for exact-target certificate drift, stale worklists, and canonical node-hash checks.
<!-- END BLUEPRINT INTERFACES -->
Use `skill-drift._rtx.interface.drift-status` to read signed certificate
currentness and `skill-drift._rtx.interface.compute-hashes` to read the canonical
certification-basis and node hashes.

Both routes accept only canonical version-6 repository graphs. They derive state through the
shared certification view and never create keys, sign payloads, append
certificates, run validators, or execute target code.

An exact `--repo-root` request selects the repository's complete canonical module
graph. The route supplies its own `status` or `compute-hashes` subcommand; callers
must not pass it.

An exact `--skill-root` request selects the dependency closure rooted at that
module's owned nodes. Named requests resolve matching installed copies across
supported hosts. With no target, the checker scans observed installed module
roots.

JSON status is certification-state read-only. Human-readable status also saves
a derived report under `_build/certificate-drift-<timestamp>.md`; that report
has no certification authority. Hash output never writes a report.

For one exact repository, JSON status may receive `--dag-file PATH`. It writes
the complete neutral dependency DAG as
`officina.certification-dependency-dag/v1` and adds `dag_file` plus the sorted
`stale_vertices` projection to stdout. The DAG and projection describe drift;
they contain no audit state and do not authorize certification.

Report `certificate-current` only when every selected node has a valid signed
certificate matching its current node hash, dependencies, certification basis,
certifier functional identity, and expected checks. The signed `source_commit`
is issuance provenance for restoring the issued snapshot; it is not required
to equal current `HEAD`. Otherwise report `certificate-stale` with a stale
worklist that maps exact changed file, interface, or dependency causes to the
affected facets and nodes. Manifest mismatches name added, removed, or changed
files. Dependency deltas cover all direct facet dependencies: they use the
interface id when present, or relation and target otherwise. Broad certificate,
basis, or graph concerns remain node-scoped rather than being misreported as a
narrow facet change.

Writing certification state belongs solely to `skill-certifier`.
