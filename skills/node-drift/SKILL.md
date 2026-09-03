---
name: node-drift
description: >-
  Use when the user asks whether Officina node certificates are current or stale, or asks for canonical node hashes. Do not use to issue certificates.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `node-drift._rtx.interface.compute-hashes` — Compute canonical certification-basis and v6 node hashes for exact or installed modules.
  - Caller: `node-drift`
  - Version: 2
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--json": true, "--repo-root": "ROOT", "--skill-root": "ROOT"}, "positionals": ["target..."], "stdin": null}
    Required options: []; positional arity: 0..unbounded; stdin: forbidden
- `node-drift._rtx.interface.drift-status` — Read signed certificate currentness, exact structured drift causes, and the dependency-first stale worklist for exact or installed v6 modules without writing certification state.
  - Caller: `node-drift`
  - Version: 4
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--all": true, "--dag-file": "PATH", "--json": true, "--repo-root": "ROOT", "--skill-root": "ROOT"}, "positionals": ["target..."], "stdin": null}
    Required options: []; positional arity: 0..unbounded; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `setup-dispatcher-runtime.interface.repair-selected-packages@1` — Repair the core or one caller-owned package declaration in the exact dispatcher runtime without MCP.
<!-- END BLUEPRINT INTERFACES -->
Before computing hashes or reading certificate state, follow
`setup-dispatcher-runtime.interface.repair-selected-packages` for this owner's exact
declaration `["cryptography", "keyring"]`. Complete the full Task 2 fingerprint
procedure; on any failure, stop before invoking either node-drift interface.

Use `node-drift._rtx.interface.drift-status` to read signed certificate
currentness and `node-drift._rtx.interface.compute-hashes` to read the canonical
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

Writing certification state belongs solely to `node-certify`.
