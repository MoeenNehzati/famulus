# Email Triage Personal Preferences Design

Date: 2026-07-13

## Purpose

Split `email-triage` into intent-routed LLM interfaces and let one local user maintain personal triage preferences without committing those preferences as canonical repository behavior.

This is a local pilot for user-level behavior sources. It does not introduce a general optional-overlay feature into the blueprint schemas.

## Goals

- Keep the existing triage workflow and its canonical invariants available to every user.
- Route preference-management requests separately from inbox-triage requests.
- Give the personal preference file an existing, tracked behavior-source node and sidecar so blueprint construction remains file-backed.
- Keep the repository version of the preference file empty.
- Allow this checkout to personalize the tracked file without ordinary Git status or commits including those changes.
- Make the Git and blueprint-audit limitations explicit.

## Non-goals

- Do not change shared blueprint schemas or validators.
- Do not make `skip-worktree` a portable or security-backed user-overlay mechanism.
- Do not add personal preference content during implementation.
- Do not change email account, cutoff, classification logging, list routing, watermark, or failure semantics. Replace only the unsafe pseudo-shell fetch/filter presentation with the declared composite boundary below.
- Do not modify unrelated dirty files in the current worktree.

## LLM Interface Structure

The skill will expose three LLM interfaces, of which two are operational workflows:

1. `email-triage.llm.default` binds `SKILL.md`. It is a thin router plus policy shared by every route.
2. `email-triage.llm.triage` binds `llm_interfaces/triage.md`. It contains the existing triage workflow.
3. `email-triage.llm.update-personal-preferences` binds `llm_interfaces/update-personal-preferences.md`. It reads and updates the user-level preference file.

`SKILL.md` routes requests as follows:

- Requests to add, change, remove, review, or reset personal triage preferences select `update-personal-preferences`.
- Every other request already within the `email-triage` skill's trigger scope selects `triage`.
- The router loads only the selected detailed interface after `SKILL.md`.

Detailed triage and preference-editing procedures must not be duplicated in the router.

## Canonical Triage Behavior

The current hand-authored triage workflow moves from `SKILL.md` to `llm_interfaces/triage.md` without substantive changes. The interface reads the personal preference source before classifying email.

Personal preferences may tune classification judgment, item wording, and report presentation. They may not override canonical invariants, including:

- obtaining the lookback cutoff from the existing cutoff interface;
- reading email and lists only through declared skill interfaces;
- logging every classification decision;
- never creating calendar events automatically;
- preserving deduplication, failure marking, watermark, and log-pruning requirements;
- respecting filesystem ownership and declared cross-skill boundaries.

When a personal preference conflicts with a canonical invariant, the canonical rule wins and the conflict is reported.

## Composite Fetch-And-Filter Boundary

`email-triage.machine.fetch-filtered-envelopes@1` accepts the existing account
nickname and coarse cutoff date. Its private runtime declares
`email-client.machine.mail-list@1` with `DispatchCall`, invokes it only through
`PythonMachineInterface.dispatch()`, and applies the existing exact-watermark
filter before writing stdout. The unfiltered mail-list JSON remains inside the
machine process boundary and never enters the LLM context. The interface emits
only the filtered envelope array or the existing no-new-email message.

This is a data-stream composition boundary, not a new mail client: account
selection, IMAP day-level `--after` cutoff, exact watermark comparison, warning
reporting, and conservative retention of undated envelopes keep their existing
semantics. The triage LLM interface names only the composite interface and does
not embed dispatcher syntax, executable names, or shell pipeline templates.

## Preference-Management Behavior

`update-personal-preferences` is the sole writer of `references/personal-preferences.md`. It must:

1. Read the current preference file before proposing a change.
2. Preserve unrelated preferences.
3. Translate the user's request into concise behavioral instructions rather than storing conversation history or sensitive credentials.
4. Show the proposed preference change and obtain confirmation before destructive reset or removal; ordinary additive or corrective updates may follow the user's explicit request directly.
5. Write only the personal preference file.
6. Report that the bound-file hash changed and the local skill audit is stale until the customized skill is reviewed and certified again.

A review-only request reads and reports the current preferences without
writing. When atomic application is available, a failed write preserves prior
content; every failure is reported and is never described as a saved change.

The implementation leaves the tracked repository version empty. It does not insert examples, headings, or personal content into that file because all content becomes active behavior.

## Blueprint Graph

`email-triage` will be represented with the repository's current typed blueprint graph:

- the root declares the default, triage, and preference-update LLM interfaces plus seven machine interfaces;
- each LLM instruction file has its own hidden sidecar;
- `references/personal-preferences.md` has a behavior-source sidecar;
- `triage` and `update-personal-preferences` both declare the personal preference behavior source;
- `triage` retains the existing `email-client.llm.default@3` and `list-manager.llm.default@1` dependencies;
- the default router declares the two same-skill LLM interfaces it routes to;
- `update-personal-preferences` owns the exact preference-file path and allows `triage` to read it;
- immediate reads and writes are represented in each interface's `direct_io` rather than copied transitively.
- the composite machine interface declares `email-client.machine.mail-list@1`, a private multi-part runtime binding, platform support, direct IO, and filesystem ownership;

The graph contains sidecars for the six existing machine interfaces and the
new composite machine interface. It must not modify shared schema, template,
validator, or audit implementation files.

## Git Lifecycle

The canonical commit includes the complete approved scope: the empty
`references/personal-preferences.md`, its behavior-source sidecar, the
email-triage interface split and typed artifacts, the composite runtime and
tests, `skills/email-client/blueprint.yaml` with the narrow mail-list caller
export, `references/blueprint/runtime_dependencies.json` refreshed by blueprint
sync, and the approved design/plan/lesson artifacts.

The presented diff, approval request, exact-path staging, and canonical commit
must all use that same scope. In particular, none may omit
`skills/email-client/blueprint.yaml` or
`references/blueprint/runtime_dependencies.json` while including the composite
email-triage interface that depends on them.

Only after that commit succeeds, this checkout marks the preference file locally:

```bash
git update-index --skip-worktree skills/email-triage/references/personal-preferences.md
```

Verification uses:

```bash
git ls-files -v -- skills/email-triage/references/personal-preferences.md
```

An uppercase `S` confirms the local flag. Removing the local behavior requires `--no-skip-worktree`.

The flag is local index state. It is not cloned, committed, backed up, or treated as a privacy boundary. Before checkout, reset, merge, or any upstream change to the canonical preference file, the user must back up personal content and verify the flag and file state.

## Audit Behavior

The personal preference file is a real behavior source, so its bound-file content participates in local artifact health. Personalizing it intentionally makes the prior audit stale. `skip-worktree` does not and should not hide the content from blueprint audit or drift checks.

The preference-update interface reports this state but does not automatically certify its own changes. Certification remains a separate review decision.

## Failure Handling

- Missing interface or behavior-source bindings are implementation errors and must fail blueprint validation.
- An empty personal preference file means canonical triage behavior only.
- An unreadable preference file stops triage before email classification and reports the path problem.
- A failed preference write leaves the prior content intact when the editing surface supports atomic application; the interface reports failure and does not claim the preference was saved.
- Blueprint sync or validation rejection is accepted as a blocker; implementation must not bypass the dispatcher or validator contracts.

## Verification

Before presenting the skill diff:

1. Confirm the personal preference file is empty in the index.
2. Run the focused `email-triage` behavior tests.
3. Confirm the contract tests cover preference discovery, canonical precedence
   and scope, destructive confirmation, unreadable-file and failed-write
   behavior, hash/audit reporting, read-only review, ownership/readers, and
   filtered-only composite output.
4. Run blueprint synchronization in check mode through `skill-maker.machine.sync-blueprints`.
5. Run the full repository validator entrypoint and attribute unrelated
   `skill-audit` or `skill-drift` failures separately.
6. Inspect the generated contract and interface blocks for three LLM routes,
   seven machine interfaces, the behavior-source edge, and the composite
   machine dependency.
7. Confirm unrelated dirty files remain untouched.

After user approval of the diff:

1. Commit only the approved email-triage artifacts,
   `skills/email-client/blueprint.yaml`,
   `references/blueprint/runtime_dependencies.json`, and approved
   design/plan/lesson artifacts.
2. Apply `skip-worktree` to the committed empty preference file.
3. Verify the uppercase `S` marker.
4. Do not add personal preference content unless the user separately asks to update it.

## Acceptance Criteria

- Preference-management requests route to `update-personal-preferences`; other email-triage requests route to `triage`.
- The current triage workflow has one canonical detailed home.
- The personal preference file exists, is tracked empty, and has a valid behavior-source sidecar.
- The triage interface loads the preference source while preserving canonical invariants.
- The update interface is the declared writer and the triage interface is an allowed reader.
- The composite dispatches only through `email-client.machine.mail-list@1` and
  exposes only filtered envelopes or the existing no-new-email message.
- The root and generated artifacts describe three LLM and seven machine interfaces.
- Blueprint sync, focused tests, and relevant validators pass.
- No unrelated worktree changes are included.
- After the canonical commit, this checkout shows `S` for the preference file.
