# Email Triage Personal Preferences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `email-triage` into a default router, canonical triage workflow, and personal-preference updater backed by a tracked empty behavior source that this checkout marks `skip-worktree` after commit.

**Architecture:** `SKILL.md` becomes a thin intent router. Two named LLM interfaces own triage and preference-update behavior, while a tracked empty Markdown behavior source supplies the file-backed blueprint node and local personal content. The typed graph exposes the six existing machine interfaces plus a seventh composite fetch-and-filter interface that dispatches to `email-client.machine.mail-list@1` and filters before any envelope JSON reaches the LLM context.

**Tech Stack:** Markdown instruction files, schema-version-2 YAML blueprints, pytest, the `skill-maker.machine.sync-blueprints` dispatcher interface, Git index flags.

## Global Constraints

- Do not modify shared blueprint schemas, validators, templates, or audit implementation files.
- Preserve all unrelated dirty worktree state; stage files by exact path only.
- Preserve the six existing runtime contracts. Runtime/API inspection and the
  narrowly scoped composite implementation are authorized; do not invoke skill
  implementation files directly during development.
- The repository copy of `skills/email-triage/references/personal-preferences.md` must be zero bytes at commit time.
- Personal preferences may tune classification judgment, item wording, and report presentation but may not override canonical safety, logging, deduplication, failure, watermark, calendar, or interface-boundary rules.
- `skip-worktree` is applied only after the canonical empty file is committed.
- Personal preference changes intentionally stale the existing local audit; the updater reports this and never auto-certifies itself.
- Do not use `--no-verify`. The current unrelated typed-blueprint migration must become validator-clean before the final commit can succeed.
- A pre-plan commit attempt failed only because the existing `skill-audit` and `skill-drift` typed sidecars are incomplete; do not repair those files within this plan.

---

## Typed Blueprint Contract Reference

**Files:**

- Modify: `skills/email-triage/blueprint.yaml`
- Create: `skills/email-triage/.SKILL.md.blueprint.yaml`
- Create: `skills/email-triage/llm_interfaces/.triage.md.blueprint.yaml`
- Create: `skills/email-triage/llm_interfaces/.update-personal-preferences.md.blueprint.yaml`
- Create: `skills/email-triage/references/.personal-preferences.md.blueprint.yaml`
- Create: `skills/email-triage/_rtx/._envelope_gate.py.blueprint.yaml`
- Create: `skills/email-triage/_rtx/._mail_envelope_stream.py.blueprint.yaml`
- Create: `skills/email-triage/_rtx/._watermark_floor.py.blueprint.yaml`
- Create: `skills/email-triage/_rtx/._decision_sink.py.blueprint.yaml`
- Create: `skills/email-triage/_rtx/._log_compactor.py.blueprint.yaml`
- Create: `skills/email-triage/_rtx/._watermark_writer.py.blueprint.yaml`
- Create: `skills/email-triage/_rtx/._failure_sentinel.py.blueprint.yaml`

**Interfaces:**

- Consumes: Ten interface contracts: three LLM interfaces and seven machine interfaces.
- Produces: One typed skill root, three LLM sidecars, seven machine sidecars, and one behavior-source sidecar.

### Root locator contract

Start from `references/blueprint/template.yaml`, retain the current email-triage taxonomy, skill interface, and suggested permissions, and declare these exact locator edges:

```yaml
interfaces:
  - interface: email-triage.llm.default
    version: 1
    blueprint: {base: skill-root, path: .SKILL.md.blueprint.yaml}
  - interface: email-triage.llm.triage
    version: 1
    blueprint: {base: skill-root, path: llm_interfaces/.triage.md.blueprint.yaml}
  - interface: email-triage.llm.update-personal-preferences
    version: 1
    blueprint: {base: skill-root, path: llm_interfaces/.update-personal-preferences.md.blueprint.yaml}
  - interface: email-triage.machine.scripts-filter-envelopes
    version: 1
    blueprint: {base: skill-root, path: _rtx/._envelope_gate.py.blueprint.yaml}
  - interface: email-triage.machine.fetch-filtered-envelopes
    version: 1
    blueprint: {base: skill-root, path: _rtx/._mail_envelope_stream.py.blueprint.yaml}
  - interface: email-triage.machine.scripts-get-cutoff
    version: 1
    blueprint: {base: skill-root, path: _rtx/._watermark_floor.py.blueprint.yaml}
  - interface: email-triage.machine.scripts-log-decision
    version: 1
    blueprint: {base: skill-root, path: _rtx/._decision_sink.py.blueprint.yaml}
  - interface: email-triage.machine.scripts-prune-log
    version: 1
    blueprint: {base: skill-root, path: _rtx/._log_compactor.py.blueprint.yaml}
  - interface: email-triage.machine.scripts-update-watermark
    version: 1
    blueprint: {base: skill-root, path: _rtx/._watermark_writer.py.blueprint.yaml}
  - interface: email-triage.machine.scripts-mark-failure
    version: 1
    blueprint: {base: skill-root, path: _rtx/._failure_sentinel.py.blueprint.yaml}
```

### Default-router sidecar contract

Use `id: email-triage.llm.default`, bind `SKILL.md`, retain `allow_all_skills: true`, and declare:

```yaml
uses_interfaces:
  - interface: email-triage.llm.triage
    version: 1
  - interface: email-triage.llm.update-personal-preferences
    version: 1
behavior_sources: []
direct_io:
  reads:
    - medium: prompt
      access: read
      content: message
      format: text
      sensitivity: user-private
  writes:
    - medium: prompt
      access: write
      content: response
      format: markdown
      sensitivity: derived-private
  network: []
owns_filesystem: []
```

### Triage sidecar contract

Bind `llm_interfaces/triage.md`. Declare `email-client.llm.default@3`, `list-manager.llm.default@1`, and every same-skill machine interface named in the body under `uses_interfaces`. Add:

```yaml
behavior_sources:
  - source: email-triage.source.personal-preferences
    version: 1
    reason: Apply the user's local email-triage classification and presentation preferences.
direct_io:
  reads:
    - medium: local-filesystem
      access: read
      system: filesystem
      content: config
      format: markdown
      path: references/personal-preferences.md
      sensitivity: user-private
  writes:
    - medium: prompt
      access: write
      content: response
      format: markdown
      sensitivity: derived-private
  network: []
owns_filesystem: []
```

Keep cross-skill email/list IO transitive through the declared LLM dependencies rather than duplicating it in `direct_io`.

### Preference-update sidecar contract

Bind `llm_interfaces/update-personal-preferences.md`, declare the same behavior-source edge, and describe its prompt plus exact-file IO. It owns the preference path:

```yaml
owns_filesystem:
  - match: exact
    path: references/personal-preferences.md
    allowed_readers:
      - email-triage.llm.triage
    reason: The preference-update interface is the sole writer of user-level email-triage behavior.
```

Its `direct_io.reads` must include the prompt and preference file; `direct_io.writes` must include the preference file and prompt response. Use `content: config`, `format: markdown`, and `sensitivity: user-private` for the file entries.

### Personal-preferences source contract

Create `references/.personal-preferences.md.blueprint.yaml` with:

```yaml
schema_version: 2
blueprint_type: behavior-source
id: email-triage.source.personal-preferences
version: 1
description: Stores user-level email-triage classification and presentation preferences.
binding:
  kind: file
  path: references/personal-preferences.md
content: config
format: markdown
uses_behavior_sources: []
```

### Machine-interface migration contract

For each mapping below, move the existing inline `version`, `description`, `usage`, `patterns`, `dependencies`, `direct_io`, `owns_filesystem`, and `platform_support` fields into the named sidecar. Replace only the legacy `invocation` object with `binding: {kind: python-entrypoint, path: <runtime path>, symbol: Interface}` and place `behavior_sources: []` at interface level.

| Interface ID | Runtime path | Sidecar |
|---|---|---|
| `email-triage.machine.scripts-filter-envelopes` | `_rtx/_envelope_gate.py` | `_rtx/._envelope_gate.py.blueprint.yaml` |
| `email-triage.machine.scripts-get-cutoff` | `_rtx/_watermark_floor.py` | `_rtx/._watermark_floor.py.blueprint.yaml` |
| `email-triage.machine.scripts-log-decision` | `_rtx/_decision_sink.py` | `_rtx/._decision_sink.py.blueprint.yaml` |
| `email-triage.machine.scripts-prune-log` | `_rtx/_log_compactor.py` | `_rtx/._log_compactor.py.blueprint.yaml` |
| `email-triage.machine.scripts-update-watermark` | `_rtx/_watermark_writer.py` | `_rtx/._watermark_writer.py.blueprint.yaml` |
| `email-triage.machine.scripts-mark-failure` | `_rtx/_failure_sentinel.py` | `_rtx/._failure_sentinel.py.blueprint.yaml` |

### Composite machine-interface contract

Add `email-triage.machine.fetch-filtered-envelopes@1`, bound to the private
multi-part runtime `_rtx/_mail_envelope_stream.py`. It accepts
`-a <account> --after YYYY-MM-DD`, declares
`email-client.machine.mail-list@1` under `uses_interfaces`, and defines the
matching `DispatchCall(caller_skill="email-triage", target_skill="email-client",
interface="mail-list")`. Normal execution calls only
`PythonMachineInterface.dispatch()`, applies the existing watermark filter, and
prints only filtered JSON or the existing no-new-email text. The sidecar must
declare dependencies, cross-platform support, immediate direct IO, empty
ownership, and the root locator shown above. Export mail-list to
`email-triage` through its access-control declaration.

### Typed graph check command

Run:

```bash
dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check
```

Expected for `email-triage`: no missing locator, binding, access-control, ownership, interface-ID, or behavior-source errors. If unrelated `skill-audit` or `skill-drift` errors remain, report them separately and do not change those skills.

---

## File Map

**Create**

- `skills/email-triage/llm_interfaces/triage.md` — canonical inbox-triage workflow.
- `skills/email-triage/llm_interfaces/update-personal-preferences.md` — preference read/update workflow.
- `skills/email-triage/references/personal-preferences.md` — tracked empty user behavior source.
- `skills/email-triage/tests/test_llm_routing.py` — routing, separation, and empty-source contract tests.
- `skills/email-triage/.SKILL.md.blueprint.yaml` — default-router LLM contract.
- `skills/email-triage/llm_interfaces/.triage.md.blueprint.yaml` — triage LLM contract.
- `skills/email-triage/llm_interfaces/.update-personal-preferences.md.blueprint.yaml` — preference-update LLM contract.
- `skills/email-triage/references/.personal-preferences.md.blueprint.yaml` — behavior-source contract.
- Seven hidden machine sidecars under `skills/email-triage/_rtx/`: six existing interfaces and the composite fetch-and-filter interface.
- `skills/email-triage/_rtx/_mail_envelope_stream.py` — composite fetch-and-filter runtime.
- `skills/email-triage/tests/test_fetch_filtered_envelopes.py` — filtered-only composite behavior and declaration tests.

**Modify**

- `skills/email-triage/SKILL.md` — retain frontmatter/generated regions and replace the hand-authored workflow with routing policy.
- `skills/email-triage/blueprint.yaml` — replace the legacy inline graph with the typed root and locators.
- `skills/email-client/blueprint.yaml` — allow only `email-triage` to call the mail-list machine interface.
- `references/blueprint/runtime_dependencies.json` — generated manifest refreshed by blueprint sync.

**Preserve**

- Existing runtime behavior except the focused extraction of reusable watermark-filter helpers needed by the composite.
- Existing watermark/filter tests.
- Unrelated worktree files, including the in-progress shared typed-blueprint migration.

---

### Task 1: Add the LLM routing contract tests

**Files:**

- Create: `skills/email-triage/tests/test_llm_routing.py`

**Interfaces:**

- Consumes: Current `SKILL.md` and the approved design spec.
- Produces: A failing executable contract for the router, named interface files, and empty preference source.

- [ ] **Step 1: Create the failing tests**

```python
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]


def test_default_interface_routes_by_preference_intent() -> None:
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "email-triage.llm.update-personal-preferences" in body
    assert "email-triage.llm.triage" in body
    assert "add, change, remove, review, or reset" in body
    assert "Every other" in body


def test_named_interfaces_are_separate_instruction_files() -> None:
    triage = SKILL_ROOT / "llm_interfaces" / "triage.md"
    update = SKILL_ROOT / "llm_interfaces" / "update-personal-preferences.md"

    assert triage.is_file()
    assert update.is_file()
    assert "## Step 1" in triage.read_text(encoding="utf-8")
    assert "sole writer" in update.read_text(encoding="utf-8")


def test_personal_preferences_source_is_present_and_empty() -> None:
    preferences = SKILL_ROOT / "references" / "personal-preferences.md"

    assert preferences.is_file()
    assert preferences.read_bytes() == b""
```

- [ ] **Step 2: Run the focused test and verify the pre-implementation failure**

Run:

```bash
pytest -q skills/email-triage/tests/test_llm_routing.py
```

Expected: FAIL because the router strings and named interface files do not yet exist.

- [ ] **Step 3: Review the failing assertions**

Confirm every failure corresponds to one approved acceptance criterion. Do not add tests for exact generated block formatting because blueprint sync owns that formatting.

---

### Task 2: Split the hand-authored LLM behavior and add the empty source

**Files:**

- Modify: `skills/email-triage/SKILL.md`
- Create: `skills/email-triage/llm_interfaces/triage.md`
- Create: `skills/email-triage/llm_interfaces/update-personal-preferences.md`
- Create: `skills/email-triage/references/personal-preferences.md`

**Interfaces:**

- Consumes: The current hand-authored `SKILL.md` workflow and Task 1 tests.
- Produces: `email-triage.llm.default`, `email-triage.llm.triage`, `email-triage.llm.update-personal-preferences`, and an empty file for `email-triage.source.personal-preferences`.

- [ ] **Step 1: Replace only the hand-authored `SKILL.md` body with the router**

Retain the YAML frontmatter and generated blueprint regions. Replace the body beginning at `# Email Triage` with:

```markdown
# Email Triage

Route by user intent:

- If the user asks to add, change, remove, review, or reset personal triage
  preferences, use `email-triage.llm.update-personal-preferences`.
- Every other request within this skill's trigger scope uses
  `email-triage.llm.triage`.

Load only the selected interface's detailed instructions after this router.
Personal preferences never override canonical safety, side-effect, logging,
watermark, or declared-interface constraints.
```

- [ ] **Step 2: Move the existing workflow into `llm_interfaces/triage.md`**

Move the current body from `# Email Triage` through Step 7 into the new file. Preserve its operational rules, examples, and order. Add this block immediately after its introductory paragraph:

```markdown
## Personal preferences

Before triage, read `references/personal-preferences.md`. An empty file means
canonical triage behavior only. Apply its instructions only to classification
judgment, item wording, and report presentation. If a personal instruction
conflicts with a canonical rule in this interface, follow the canonical rule
and report the conflict.
```

When naming blueprint interfaces in the migrated body, use canonical IDs. Name same-skill machine interfaces as `email-triage.machine.<name>` and refer to the cross-skill prompt surfaces as `email-client.llm.default` and `list-manager.llm.default`.

- [ ] **Step 3: Create the preference-update instructions**

Create `llm_interfaces/update-personal-preferences.md` with:

```markdown
# Update Personal Email-Triage Preferences

This interface is the sole writer of `references/personal-preferences.md`.
It manages user-level triage preferences; it does not triage email.

1. Read the current preference file before proposing a change.
2. Translate the user's request into concise behavioral instructions. Do not
   store conversation history, passwords, credentials, or unrelated personal
   data.
3. Preserve unrelated preferences.
4. For a reset, removal, or other destructive rewrite, show the proposed
   result and obtain confirmation before writing. An explicit additive or
   corrective request may be applied directly.
5. Write only `references/personal-preferences.md`.
6. Report the exact preference change. Also report that changing this bound
   behavior source makes the prior local skill audit stale until the
   customized skill is separately reviewed and certified again.

An empty file means that only canonical triage behavior applies. Never add
headings, examples, or placeholder prose unless the user requests them,
because every stored line becomes active behavior.
```

- [ ] **Step 4: Create the tracked behavior source as a zero-byte file**

Create `skills/email-triage/references/personal-preferences.md` with no newline and no content. Verify:

```bash
wc -c skills/email-triage/references/personal-preferences.md
```

Expected: `0 skills/email-triage/references/personal-preferences.md`.

- [ ] **Step 5: Run the routing tests**

Run:

```bash
pytest -q skills/email-triage/tests/test_llm_routing.py
```

Expected: all routing and preference contract tests pass.

---

### Task 3: Implement the typed email-triage graph

**Files:**

- Modify: `skills/email-triage/blueprint.yaml`
- Create: the eleven sidecars listed in the Typed Blueprint Contract Reference.

**Interfaces:**

- Consumes: Task 2 instruction files, the current inline machine contracts, and the exact typed contracts above.
- Produces: One typed root, three LLM interfaces, six existing machine interfaces, one composite machine interface, and one personal-preferences behavior source.

- [ ] **Step 1: Create the typed root and LLM sidecars**

Apply the Root locator, Default-router, Triage, and Preference-update contracts above. Use the current schema-version-2 template as the root starting point. Do not copy interface-local fields into the root.

- [ ] **Step 2: Create the behavior-source sidecar**

Apply the Personal-preferences source contract above exactly and verify its binding resolves to the zero-byte file created in Task 2.

- [ ] **Step 3: Extract the six existing machine contracts**

Apply the Machine-interface migration table above. Copy public fields from the existing inline declarations without reading or changing runtime implementation bodies.

- [ ] **Step 4: Add the composite fetch-and-filter interface with TDD**

Write focused tests first for the declared dispatch call, account/cutoff argv,
filtered-only stdout, and existing no-new-email output. Run them and record the
expected missing-runtime/sidecar RED. Then implement the runtime, sidecar,
root locator, mail-list access declaration, and triage dependency; rerun to
GREEN.

- [ ] **Step 5: Run the typed graph check**

```bash
dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check
```

Expected for `email-triage`: no missing locator, binding, ownership, access-control, interface-ID, or behavior-source failures. Report unrelated `skill-audit` or `skill-drift` failures separately.

---

### Task 4: Refresh generated artifacts and verify behavior

**Files:**

- Generated update: `skills/email-triage/SKILL.md`
- Test: `skills/email-triage/tests/test_llm_routing.py`
- Existing tests: `skills/email-triage/tests/test_filter_envelopes.py`
- Existing tests: `skills/email-triage/tests/test_watermark.py`
- New tests: `skills/email-triage/tests/test_fetch_filtered_envelopes.py`

**Interfaces:**

- Consumes: Complete typed email-triage graph from Task 3.
- Produces: Synchronized generated contract blocks and a verified implementation diff.

- [ ] **Step 1: Capture the pre-sync unrelated status**

```bash
git status --short
```

Record existing non-email-triage paths. They are not implementation output.

- [ ] **Step 2: Refresh generated blueprint artifacts**

```bash
dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints
```

Inspect `git status --short` immediately. If sync changes an unrelated clean file, stop and report it rather than silently absorbing the change. Pre-existing modified files remain user-owned.

- [ ] **Step 3: Run the complete focused test suite**

```bash
pytest -q skills/email-triage/tests
```

Expected: all watermark/filter behavior tests plus routing, preference, and
composite contract tests pass.

- [ ] **Step 4: Re-run blueprint check and repository validators**

```bash
dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check
```

Then run the repository validator entrypoint using its established executable surface. Expected: `email-triage` produces no failures. Unrelated failures from the existing typed-blueprint migration block commit and must be reported without repair under this plan.

The verification report must separately enumerate discovery, canonical
precedence/scope, destructive confirmation, unreadable preference,
failed-write preservation/reporting, successful hash/audit reporting,
read-only review, ownership/reader, and composite filtered-output coverage.

- [ ] **Step 5: Inspect the scoped diff**

```bash
git diff -- skills/email-triage skills/email-client/blueprint.yaml references/blueprint/runtime_dependencies.json docs/superpowers/specs/2026-07-13-email-triage-personal-preferences-design.md docs/superpowers/plans/2026-07-13-email-triage-personal-preferences.md lessons/2026-07-13.md
```

Confirm the preference file is empty, the composite runtime is the only new
runtime, the existing filter change is limited to reusable helper extraction,
and no unrelated runtime implementation changed.

---

### Task 5: Approval-gated commit and local personalization flag

**Files:**

- Stage only approved email-triage files,
  `skills/email-client/blueprint.yaml`,
  `references/blueprint/runtime_dependencies.json`, the design spec,
  implementation plan, and lesson if the user includes it in commit scope.
- Local index state: `skills/email-triage/references/personal-preferences.md`.

**Interfaces:**

- Consumes: Passing focused tests, email-triage-clean blueprint validation, and explicit user approval of the final diff.
- Produces: Canonical commit with an empty preference source plus a local uppercase `S` marker.

- [ ] **Step 1: Present the scoped diff and obtain explicit commit approval**

Present the exact diff scope from Task 4 Step 5, including
`skills/email-client/blueprint.yaml` and
`references/blueprint/runtime_dependencies.json`. Do not stage or commit any
implementation file before approval. State any unrelated validator blocker and
whether the lesson is included.

- [ ] **Step 2: Stage exact approved paths and inspect the index**

Use exact `git add <path>` arguments. Then run:

```bash
git diff --cached --name-only
git diff --cached --check
```

Expected: only the approved email-triage paths,
`skills/email-client/blueprint.yaml`,
`references/blueprint/runtime_dependencies.json`, and approved documentation
artifacts are present, with no whitespace errors. Verify the indexed preference
blob is empty:

```bash
git show :skills/email-triage/references/personal-preferences.md
```

Expected: no output.

- [ ] **Step 3: Commit without bypassing hooks**

```bash
git commit -m "Add personal email triage preferences"
```

Expected: commit succeeds only after all repository hooks pass. If unrelated typed-blueprint work still breaks hooks, leave the approved paths staged and report the exact blocker.

- [ ] **Step 4: Apply the local index flag after the commit**

```bash
git update-index --skip-worktree skills/email-triage/references/personal-preferences.md
```

- [ ] **Step 5: Verify the local flag**

```bash
git ls-files -v -- skills/email-triage/references/personal-preferences.md
```

Expected: the line begins with uppercase `S`.

- [ ] **Step 6: Report completion and remaining limitations**

Report the commit hash, focused test results, blueprint/validator results, `skip-worktree` verification, unrelated dirty state, and the requirement to back up personal content and re-certify after future preference changes.
