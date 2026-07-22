---
name: skill-audit
description: Use when certifying local skill audit state after mechanical checks and blueprint exactness checks should write fresh audit records.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-development-assistant

Skill Version: 1

Uses Interfaces:
- `skill-audit.llm.default -> skill-audit.machine.certify@1`
- `skill-audit.machine.certify -> skill-drift.machine.compute-hashes@1`
- `skill-audit.machine.certify -> skill-drift.machine.drift-status@1`
- `skill-audit.machine.certify -> skill-maker.machine.sync-blueprints@1`

Public Interfaces:
- `skill-audit.llm.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `certify` — Audit exact target closures and incrementally write commit-backed node health records.
  - `dispatcher --caller-skill skill-audit skill-audit.machine.certify certify [target ...] [--json]`

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Audit exact target closures and report semantic and commit-backed stamp outcomes.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
## Audit Rules

Use this skill only after the target skill's blueprint has been reviewed against
its actual behavior. The blueprint must be exact: every declared file root,
runtime dependency, permission, state path, interface call, and callable surface
must correspond to real behavior, and no behavior-relevant dependency may be
omitted.

Mechanical certification runs through `skill-audit.machine.certify`; that
interface owns the downstream sync, hash-computation, and post-write drift
checks.

For a v4 migration candidate, keep semantic review separate from mechanical
finalization:

1. Inspect the committed candidate and collect its completeness findings. This
   step is read-only; it must not synthesize blueprint prose or write
   certificates. Treat immutable legacy claims as review context, not as
   blocking completeness findings.
2. Review every candidate blueprint against its gateway, content, dependencies,
   and actual interface behavior. Edit only claims supported by that evidence,
   then rerun inspection. A structurally complete blueprint is not thereby
   semantically correct.
3. If review would change a mechanically protected fact such as ownership,
   content, gateway, dependency, export, or process binding, stop and return the
   candidate to migration rather than silently changing its projection.
4. The mechanical materializer pins its exact commit under the reserved
   Famulus Git ref. Commit the reviewed candidate and give finalization only
   that exact reviewed commit; a displayed or caller-supplied mechanical commit
   is not authority. Finalization derives the pinned baseline, proves ancestry,
   permits only blueprint-file differences, reconstructs the mechanical graph,
   and rejects changes to its protected projection, including helper bindings.
   The reviewed descendant must contain exactly one
   `Famulus-Legacy-Claims-Reconciled: sha256:<digest>` trailer matching the
   inspection's per-occurrence legacy-claim reconciliation digest.
5. The LLM invocation supplies semantic attestation for the reviewed commit.
   The machine finalizer derives deterministic state, the versioned attestation
   record, and every certificate payload field; it accepts no caller-supplied
   check record or generated semantic default.

Signing never accepts the non-atomic diagnostic fallback. Candidate-owned code
runs from a private exact materialization of the reviewed commit; the caller
rechecks the parent candidate afterward. This remains a cooperative same-UID
contract and does not claim isolation from a malicious same-UID process.

`SKILL.md` may describe user interaction, decision flow, and interface
orchestration. It must not contain direct execution logic. Executable behavior,
whether public or private, belongs behind a declared interface in
`blueprint.yaml`.

Implicit references count. If instructions, docs, docstrings, runtime code, or
tests say to inspect a directory, script family, helper module, generated
artifact, state file, config file, external command surface, or similar
behavioral source without naming a direct path, treat that as a dependency that
must be represented in the blueprint.

Targets may be omitted, named by installed skill name, or given as an exact
skill root path. With no targets, certify every observed blueprint-backed
installed skill reported by the drift hash interface.

An exact target audits only the requested skill root and its reachable
interface and behavior-source closure. Unrelated skills are outside that
target's graph and cannot block it.

Semantic auditing always completes for reachable nodes when their local inputs
are dirty, the target is not in Git, or the certification policy is not backed
by the captured commit. Those nodes report their semantic result without a new
stamp. New stamps are written only for node-local inputs that match the captured
commit; already admitted healthy children are reused without another Git
readiness check.

Each requested target is processed independently. A later target failure does
not remove earlier valid node stamps or successful target results. Generated
health records, pooled reviews, and the local authentication key are ignored
local certification state rather than source inputs.

If mechanical checks fail, semantic exactness fails, hash computation fails, or
post-write verification fails, report the affected target or invocation and do
not treat that result as certified.
