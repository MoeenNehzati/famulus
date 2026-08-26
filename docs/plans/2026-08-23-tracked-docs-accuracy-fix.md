# Tracked Documentation Accuracy Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every tracked hand-written document state what the repository actually contains.

**Architecture:** Each fact has one owning document. The seventeen defects distribute across the framework overview and utility map, the contributor pages, the two audit records, the milestone-logging reference, the testing guide, the public README, and the quickstarts. One is a generated artifact whose generator enumerates half of what it derives.

**Tech Stack:** Markdown, Python documentation validators, pytest, MkDocs.

## Global Constraints

- Work in a branch or worktree dedicated to this plan; do not work on `master`.
- Do not touch the main checkout's unrelated dirty files.
- Repair each fact in the document that owns it. Do not restate it in a second document to avoid correcting the first.
- Where two documents disagree, correct the one that does not match the code.
- Do not add a validator for a contract this plan does not also make true.
- Do not commit or push without separate user authorization.

## Audit Record

Tracked-documentation audit, 2026-08-22, against `a7d2fb28`: the doc set is
mechanically clean. All 32 validators pass, and every markdown link and
repository path in the tracked set resolves. The two exceptions,
`runtime/current.json` in `docs/launchers.md` and `docs/officina/dispatcher.md`,
are install-root-relative rather than repository-relative and are correct as
written. Nineteen factual defects were found; two are already repaired.

## Already Applied

`docs/superpowers/` is untracked as of `5466ea64`. Commit `6dd5d0ce` added
`docs/superpowers/**` to `.gitignore` but left the six files already in the
index tracked, since `.gitignore` does not apply to tracked paths. The subtree
kept growing under a rule that could not catch it, reaching fifteen tracked
files, all of which reached the published site carrying references to dozens of
paths that no longer exist.

`docs_tooling/site.py` now excludes both private subtrees through
`_PRIVATE_SUBTREES`. Previously the walk skipped only `plans` and read the
working tree, so a local `./scripts/docs-site.py serve` published
`docs/superpowers/` from disk whether or not Git tracked it. A local build and
GitHub Pages now stage the same set.

---

### Task 1: Complete the framework inventories

**Files:**
- Modify: `docs/officina/README.md`
- Modify: `docs/officina/utility-map.md`

**Interfaces:**
- Consumes: the directory listing of `src/officina/` and `references/`.
- Produces: inventories that account for every package and contract directory.

- [x] **Step 1: Add the missing packages to the Officina overview**

Add `launchers/` and `recurring/` to "Shared code"; the list carries sixteen
bullets for eighteen packages. `launchers/` owns the durable backend selection
that `docs/launchers.md` describes, and is the only place in `src/` that reads
`launchers.json`. Decide whether `recurring/` is Officina or Famulus by the
page's own membership rule — what the component is *for* — and if it is Famulus,
say so in the paragraph that already excludes `references/document-standards/`
rather than leaving it unmentioned. While editing the `install/` bullet, name
`assistant_access.py`, which the bullet's list of responsibilities predates.

- [x] **Step 2: Add `references/runtime/` to the contracts list**

It holds `requirements-core.in` and `requirements-core.lock`, and
`docs/dependency-and-bootstrap-audit.md` already treats the lock as the
authoritative release pin, so the contracts list must not omit it.

- [x] **Step 3: Map the seven unmapped packages in the utility map**

Add a row per package naming its owning modules and what a reader would come to
it for. Eleven of the eighteen packages under `src/officina/` have rows; the
remainder are `install`, `launchers`, `recurring`, `wakeup`, `runtime`,
`dispatcher`, and `validators`. The page opens by promising to locate shared
repository mechanics, so a maintainer looking for the runtime pointer or the
launcher resolver currently finds nothing.

- [x] **Step 4: Verify the inventories against the tree**

Run: `ls -d src/officina/*/ | grep -v __pycache__` and `ls references/`

Expected: every entry appears in the overview, and every package appears in the
utility map or is excluded by a stated rule.

### Task 2: Correct the dispatcher performance budget

**Files:**
- Modify: `docs/officina/dispatcher.md`

**Interfaces:**
- Consumes: `tests/test_dispatcher_performance.py`.
- Produces: a budget statement a maintainer can check against the assertions.

- [x] **Step 1: Replace the fresh-CLI budget with the enforced one**

State the three platform medians `_fresh_cli_budget_ms` returns — 125 on Linux,
150 on macOS, 175 on Windows — and drop the p95 claim. The page currently
promises "below 100 ms median and 150 ms p95"; neither number is enforced, and
`test_fresh_checkout_cli_meets_reference_budget` and
`test_live_inventory_fresh_cli_meets_reference_budget` compare a median only.
No p95 gate exists anywhere in the suite. The warm in-process figure below
50 ms is correct and stays.

- [x] **Step 2: Confirm the corrected numbers**

Run: `python3 -m pytest -q tests/test_dispatcher_performance.py -k budgets`

Expected: `test_fresh_cli_budgets_are_platform_specific` passes and its three
constants match the prose.

### Task 3: Route blueprint sync through the dispatcher

**Files:**
- Modify: `docs/contributors/README.md`
- Modify: `validators/contributor_docs_contract.py`
- Modify: `tests/validate_documentation_validators.py`
- Modify: `skills/skill-maker/_rtx/_blueprint_syncer.py`

**Interfaces:**
- Consumes: `skills/skill-maker/_rtx/blueprint.yaml`.
- Produces: one blueprint-sync invocation consistent with the dispatcher boundary.

- [x] **Step 1: Replace the direct script invocation**

Use the `dispatcher --caller-skill … skill-maker._rtx.interface.sync-blueprints
--check` form that `docs/officina/scaffolding/README.md` already gives, and say
that the bare form refreshes artifacts. The page currently prints
`python3 skills/skill-maker/_rtx/_blueprint_syncer.py`, then states three lines
later that cross-skill calls go through the dispatcher boundary rather than
direct script reach-through. The printed command also fails as written:
bare `python3` runs outside the managed runtime, so the import of `officina`
raises `ModuleNotFoundError`.

`contributor_docs_contract` required the broken form verbatim, so the wording
was a codified contract rather than an oversight. Point the contract at the
dispatcher form instead, and follow it into the two other places that repeat
it: the fixture in `tests/validate_documentation_validators.py`, and the
syncer's own `--check` failure message, which told the reader to run the
command that does not work.

### Task 4: Correct the documentation-system contract

**Files:**
- Modify: `docs/contributors/documentation-system.md`

**Interfaces:**
- Consumes: `docs_tooling/site.py`, the `AUTO-GENERATED` markers under `docs/`.
- Produces: an accurate account of what is hand-written and what is private.

- [x] **Step 1: Close the hand-written list**

The list reads as closed, so anything absent from it is generated by
implication — and twenty-three hand-written documents are absent: the six
top-level pages (`testing.md`, `ci-handbook.md`, `security-and-privacy.md`,
`dependency-and-bootstrap-audit.md`, `launchers.md`,
`agent-milestone-logging.md`), `docs/README.md`, and all sixteen files under
`docs/officina/`. A contributor following this page would look for generators
that do not exist. Do not close it with a bare `docs/*.md` glob: that would
sweep in `docs/skills.md`, which the next section correctly names as the
flagship generated artifact. Enumerate the directories instead, and state the
rule the reader can apply — a file is generated when it carries an
`AUTO-GENERATED` marker or is named in "What Is Generated".

- [x] **Step 2: Name both private subtrees**

The page calls `docs/plans/` *the* private subtree, in two places.
`_PRIVATE_SUBTREES` now holds `plans` and `superpowers`. Name both, and say
that `superpowers` is additionally gitignored, so the published site and a
local build cannot diverge.

- [x] **Step 3: Confirm the publication boundary**

Run: `./scripts/docs-site.py build`

Expected: `find _build/docs-site -path "*plans*" -o -path "*superpowers*"`
returns nothing.

### Task 5: Correct the dependency audit

**Files:**
- Modify: `docs/dependency-and-bootstrap-audit.md`

**Interfaces:**
- Consumes: `references/blueprint/runtime_dependencies.json`.
- Produces: a dependency inventory matching the generated manifest.

- [x] **Step 1: Remove the `setuptools` row**

The manifest's `all.python-package` holds eleven entries and no longer declares
it; the table's other eleven rows match one-for-one. The manifest is the
generated authority, so an audit that disagrees with it is the thing that is
wrong.

- [x] **Step 2: Add the non-Python dependency classes**

The section covers `python-package` alone. The manifest also declares six
binaries — `crontab`, `git`, `journalctl`, `launchctl`, `schtasks`,
`systemctl` — and three system services — `launchd`, `systemd-user`,
`task-scheduler` — none of which appear anywhere in the file. The heading scopes
the section to packages, so this is a coverage gap rather than a false
statement, but for a bootstrap audit these are the parts that reach outside the
managed runtime.

- [x] **Step 3: Re-pin the review header**

Re-pin to the reviewed commit, or add a delta paragraph in the form the header
already uses. It is pinned to `e74b8ad7` on 2026-08-17; since then `75d08d30`
moved optional-dependency selection into the blueprints and `51c06606` unified
the installation contexts.

### Task 6: Bring the security boundary up to the installer

**Files:**
- Modify: `docs/security-and-privacy.md`

**Interfaces:**
- Consumes: `src/officina/install/assistant_access.py`, `skills/install-assistant-tools/_rtx/_assistant_access_config.py`.
- Produces: an account of the assistant filesystem authority the installer now grants.

- [x] **Step 1: Document the managed assistant access roots**

Commit `a68a6389` gave assistants managed Famulus roots: the installer writes
`permissions.additionalDirectories` into the Claude user config and the
equivalent Codex access roots. The document's roots discussion covers only the
config and state platform roots, so the filesystem authority granted to a
launched assistant — which is what this document exists to describe — is
absent. This is a content gap, not a header edit.

- [x] **Step 2: Re-pin the review header**

The header is delta-reviewed through `e74b8ad7` on 2026-08-17, which predates
the change in Step 1.

### Task 7: Correct the milestone-logging reference

**Files:**
- Modify: `docs/agent-milestone-logging.md`

**Interfaces:**
- Consumes: `skills/milestone-logging/_rtx/_milestone_writer.py`, root `CLAUDE.md`.
- Produces: examples that follow the root instruction and an accurate evidence contract.

- [x] **Step 1: Add `--role` to every recording example**

Six examples omit it: two under "Calling It", three in the `--run` block, and
one in "One Run, Start To Finish". The shortest of them is the one an agent is
most likely to copy, so it is the one that must not contradict the root
instruction. Leave `milestone --path` alone — `CLAUDE.md` requires `--role`
when recording progress, and `--path` records nothing.

- [x] **Step 2: State the evidence cap**

Say that at most twenty evidence paths are kept and that `evidence_dropped`
counts only what the size budget removed. The page promises the record never
shrinks silently, but the writer truncates to `args.evidence[:20]` before the
budget loop runs, so evidence beyond twenty entries disappears with no
accounting — the one case the sentence promises cannot happen.

### Task 8: Reconcile the skill test layout

**Files:**
- Modify: `docs/testing.md`
- Modify: `docs/officina/scaffolding/README.md`

**Interfaces:**
- Consumes: `pytest.ini`, the `skills/*/tests` and `skills/*/_rtx/tests` directories.
- Produces: one stated layout, in both documents, that matches the tree.

- [x] **Step 1: State which location takes which kind of test**

`docs/testing.md` places skill tests under `skills/<skill>/_rtx/tests/`;
`docs/officina/scaffolding/README.md` shows a top-level `skills/<name>/tests/`
as the standard layout for a new skill. Both are collected, because
`pytest.ini` lists bare `skills` in `testpaths`. Twenty skills use `_rtx/tests`
and six use the top-level directory — `ci-debug`, `email-triage`,
`recurring-tasks`, `refactor-node`, `relocate-nodes`, `using-compass` — so the
scaffolding page presents the minority practice as the default. Correct both
pages together; correcting one leaves the contradiction standing.

### Task 9: Repair the public entry point

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `validators/readme_user_contract.py`, `skills/recurring-tasks/blueprint.yaml`.
- Produces: a removal sequence a reader can act on and a coherent Quick Start.

- [x] **Step 1: Make the removal step actionable**

Say to ask the assistant to remove the context, the way the installation step
already defers. Do not print the dispatcher invocation: `readme_user_contract`
lists `_rtx` in `FORBIDDEN_SNIPPETS`, so naming
`recurring-tasks._rtx.interface.scripts-remove-context` in the README fails the
validator this task runs in Step 3. The interface is unauthorized for
cross-module callers in any case — `allow_all_modules: false` with an empty
`allowed_callers`. The current text names `scripts-remove-context` as though it
were a command, and it is not one.

- [x] **Step 2: Fix the Quick Start heading sequence**

Number all four Quick Start headings or none. "Step 1: install the plugin" is
followed by "Apply an installation context", "Choose a workflow", and "Update
or remove". There is no Step 2.

- [x] **Step 3: Run the entry-point contract validator**

Run: `python3 repo_checks.py --suite validators`

Expected: `readme_user_contract` passes with the edited README.

### Task 10: Route the unrouted skills

**Files:**
- Modify: `docs/quickstarts/skill-development.md`
- Modify: `docs/quickstarts/automation.md`

**Interfaces:**
- Consumes: the skill inventory under `skills/`.
- Produces: quickstart coverage for every skill a user can reach.

- [x] **Step 1: Add the three unrouted skills**

Add `relocate-nodes` and `using-compass` to the "What to use when" table in the
skill-development quickstart, which the contributor guide presents as the
compact map of which authoring, refactoring, blueprint, standards, and
certification skill to use. Place `milestone-logging` where a reader would look
for it. All three appear in no quickstart at all.

- [x] **Step 2: Confirm coverage**

Run: `for s in $(ls skills); do grep -qr -- "$s" docs/quickstarts/ || echo "unrouted: $s"; done`

Expected: no output.

### Task 11: Regenerate the profiles table

**Files:**
- Modify: `PROFILES.md`

**Interfaces:**
- Consumes: `profiles/*.config.toml`, `scripts/generate-settings-table.sh`.
- Produces: a profiles table covering every shipped profile.

- [x] **Step 1: Rerun the settings-table generator**

`PROFILES.md` carries three rows and has no row for `background_run`, though
`profiles/background_run.config.toml` and its Claude settings both exist.
Rerunning the generator changes nothing: its collection loop reads every
`profiles/*.config.toml`, but the loop that emits rows is hardcoded to
`assistant collab coauthor`, so the profile is gathered and then dropped. Add
`background_run` to the emit loop and give it a description, then rerun.

Run: `./scripts/generate-settings-table.sh`

Expected: a `background_run` row appears and the existing three are unchanged.

- [x] **Step 2: Note the gap in the pre-commit chain**

`scripts/generate-doc-artifacts.py` does not invoke
`generate-settings-table.sh`, and the pre-commit hook regenerates `PROFILES.md`
only when configuration files changed in the same commit. Neither would have
caught this one anyway, since the generator's output was correct for the rows
it was told to emit. The hardcoded list is the real gap: a generated file whose
contents are half-derived and half-enumerated will drift again. Record this
where the documentation system describes regeneration, or close it.

### Task 12: Verify the documentation surface

**Files:**
- Verify: every file modified in Tasks 1-11.

**Interfaces:**
- Consumes: the repository validators, the documentation generators, the site build.
- Produces: an exact-scope diff and a green validator suite.

- [x] **Step 1: Regenerate and diff**

Run: `python3 scripts/generate-doc-artifacts.py` then `git diff --stat`

Expected: no generated artifact changes beyond the edits above.
`generate-doc-artifacts.py` has no check mode, so the diff is the verification.

- [x] **Step 2: Validate and build**

Run: `python3 repo_checks.py --suite validators` then `./scripts/docs-site.py build`

Expected: 32 validators pass and the site builds without staging a private
subtree.

## Completion Criteria

- Every one of the seventeen defects is closed in the document that owns it, not moved to another document.
- No hand-written document enumerates repository contents that the tree contradicts.
- `docs/testing.md` and `docs/officina/scaffolding/README.md` agree on the skill test layout.
- `README.md` passes `readme_user_contract` without naming a private interface.
- Re-running the 2026-08-22 audit finds no broken links or repository paths in the tracked set.
