---
name: node-certify
description: >-
  Use when fresh certificates are requested for one or more Officina nodes. Do not use merely to check certificate currentness or canonical node hashes.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `node-certify._rtx.interface.certify` — Certify exact v6 module closures by skipping current nodes and appending signed certificate histories for stale nodes at an explicit reviewed repository commit.
  - Caller: `node-certify`
  - Version: 2
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--allow-non-atomic": true, "--json": true, "--reviewed-commit": "COMMIT", "--reviewed-repository": "ROOT"}, "positionals": ["certify", "target..."], "stdin": null}
    Required options: []; positional arity: 1..unbounded; stdin: forbidden
- `node-certify._rtx.interface.semantic-audit-scheduler` — Initialize or transition one locked semantic-audit run.
  - Caller: `node-certify`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["OPERATION", "PREFIX", "TASK_ID", "options"], "stdin": null}
    Required options: []; positional arity: 2..unbounded; stdin: forbidden
- `node-drift._rtx.interface.drift-status` — Read signed certificate currentness, exact structured drift causes, and the dependency-first stale worklist for exact or installed v6 modules without writing certification state.
  - Caller: `node-certify`
  - Version: 4
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--all": true, "--dag-file": "PATH", "--json": true, "--repo-root": "ROOT", "--skill-root": "ROOT"}, "positionals": ["target..."], "stdin": null}
    Required options: []; positional arity: 0..unbounded; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `node-certify.source.audit-behavioral-source.interface.audit@2` — Audit one behavioral source and return bounded semantic evidence and a verdict.
- `node-certify.source.audit-interface.interface.audit@2` — Audit one source interface and return bounded semantic evidence and a verdict.
- `node-certify.source.audit-module.interface.audit@2` — Audit one module and return bounded semantic evidence and a verdict.
- `setup-dispatcher-runtime.interface.repair-selected-packages@1` — Repair the core or one caller-owned package declaration in the exact dispatcher runtime without MCP.
<!-- END BLUEPRINT INTERFACES -->
Before drift inspection, semantic audit, or certification, follow
`setup-dispatcher-runtime.interface.repair-selected-packages` for this owner's exact
declaration `["cryptography", "keyring", "pyflakes", "pytest", "pytest-xdist"]`.
Complete the full Task 2 fingerprint procedure; on any failure, stop before invoking a
drift, audit, or certify interface.

## Certification algorithm

Resolve the requested target and hold its reviewed repository and commit
stable. Then:

1. Invoke `node-drift._rtx.interface.drift-status@4` once in JSON mode with
   `--dag-file`, and save its JSON result for scheduler initialization.
2. Initialize `node-certify._rtx.interface.semantic-audit-scheduler@1` from
   the DAG and drift result. The
   scheduler, not the LLM, owns dependency traversal and audit readiness.
3. Determine available subagent slots `K`, excluding the orchestrator; use one
   if unknown. Call `claim --capacity K`. For each returned item, spawn one fresh
   subagent and pass `input_file` unchanged. Map `interface` to
   `node-certify.source.audit-interface.interface.audit@2`,
   `behavioral-source` to
   `node-certify.source.audit-behavioral-source.interface.audit@2`, and
   `module` to `node-certify.source.audit-module.interface.audit@2`. Never
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
