---
name: node-certify
description: >-
  Use when fresh certificates are requested for one or more Officina nodes. Do not use merely to check certificate currentness or canonical node hashes.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `node-certify._rtx.interface.certification-voyage` — Initiate and operate one certification Voyage; machines schedule audits, validate raw worker reports and sign exact nodes.
  - Caller: `node-certify`
  - Version: 1
  - Alternative: `discovery`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["help|modes"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
  - Alternative: `list`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--run-prefix": "PREFIX"}, "positionals": ["list"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
  - Alternative: `initiate-default-implicit`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--repository": "PATH", "--retry-interval-seconds": "N", "--run-prefix": "PREFIX", "--targets": "IDS", "--worker-capacity": "K"}, "positionals": ["initiate"], "stdin": null}
    Required options: ["--repository", "--worker-capacity"]; positional arity: 1..1; stdin: forbidden
  - Alternative: `initiate-default-explicit`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--repository": "PATH", "--retry-interval-seconds": "N", "--run-prefix": "PREFIX", "--targets": "IDS", "--worker-capacity": "K"}, "positionals": ["initiate", "default"], "stdin": null}
    Required options: ["--repository", "--worker-capacity"]; positional arity: 2..2; stdin: forbidden
  - Alternative: `operation`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--responding-to": "ENTRY", "--response-file": "PATH"}, "positionals": ["status|validate|advance|next", "VOYAGE_ID"], "stdin": null}
    Required options: []; positional arity: 2..2; stdin: forbidden
  - Alternative: `release`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--force": true}, "positionals": ["release", "RUN_ID"], "stdin": null}
    Required options: []; positional arity: 2..2; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `node-certify.source.audit-behavioral-source.interface.audit@3` — Audit one behavioral source and return bounded semantic evidence and a verdict.
- `node-certify.source.audit-interface.interface.audit@3` — Audit one source interface and return bounded semantic evidence and a verdict.
- `node-certify.source.audit-module.interface.audit@3` — Audit one module and return bounded semantic evidence and a verdict.
<!-- END BLUEPRINT INTERFACES -->
## Certification algorithm

Use `node-certify._rtx.interface.certification-voyage@1`. Determine available
worker slots excluding yourself; use one if unknown. Invoke `initiate` with
`--repository` and `--worker-capacity`. Supply `--targets` only for explicitly
requested exact module or source IDs. Omission selects the whole graph.
The optional `--retry-interval-seconds` overrides the default 10 seconds.
Retain the returned Voyage ID; one live controller owns its workers.

Call `next VOYAGE_ID`, then follow its typed result:

- `message`: spawn one fresh subagent for every supplied packet, using its exact
  instruction interface and version. Pass the packet unchanged. Never reuse a
  subagent for another task. Keep task-to-worker handles; wait when only
  outstanding workers remain. Forward one completion at a time using the exact
  raw final output, even if empty or apparently malformed:
  `{"outcome":"worker-completed","task_id":"ASSIGNED_ID","raw_output":"EXACT_OUTPUT"}`.
  For host spawn failure or worker loss only, forward
  `{"outcome":"worker-failed","task_id":"ASSIGNED_ID","reason":"HOST_FAILURE"}`.
  Write the envelope as JSON and call `next VOYAGE_ID --response-file PATH
  --responding-to ENTRY` with the returned message entrance. Retain other handles
  and raw completions for subsequent messages.
- `retry-later`: wait the supplied positive delay and resubmit the unchanged
  envelope and entrance. This is separate from waiting for worker completion.
- `terminal` or `fault`: cancel and reap remaining workers, then report the exact
  machine result. A stale-envelope error also stops dispatch; report it without
  rewriting the envelope or guessing another entrance.

Do not parse, summarize, repair, or combine worker reports. Do not inspect the
DAG, choose dependencies, judge readiness or evidence currentness, audit content,
or invoke signing. `validate` is diagnostic; ordinary operation uses only
initialization and `next`. Workers own semantic judgment. Machine code owns
selection, authentication, raw JSON/schema validation, dependency checks,
exact-node signing, and terminal certification claims. It skips current nodes.

The Voyage retains its Charter, assignments, reports, and signing receipts in one
Reckoning. Valid earlier certificates remain if a later task fails. Restart an
abandoned host session with a fresh run; do not infer recovery of old worker
handles. Preserve the terminal receipt before optionally releasing its Voyage.
