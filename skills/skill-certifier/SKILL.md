---
name: skill-certifier
description: >-
  Use when fresh certificates are requested for one or more Officina nodes. Do not use merely to check certificate currentness or canonical node hashes.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-assurance, assistant-architecture; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 7

Uses Interfaces:
- `skill-certifier.source.gateway -> skill-certifier._rtx.interface.certification-voyage@1`
- `skill-certifier.source.gateway -> skill-certifier.source.audit-behavioral-source.interface.audit@3`
- `skill-certifier.source.gateway -> skill-certifier.source.audit-interface.interface.audit@3`
- `skill-certifier.source.gateway -> skill-certifier.source.audit-module.interface.audit@3`

Public Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
## Certification algorithm

Use `skill-certifier._rtx.interface.certification-voyage@1`. Determine available
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
