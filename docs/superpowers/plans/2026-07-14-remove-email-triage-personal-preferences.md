# Remove Email-Triage Personal Preferences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove preference management and make every `email-triage` invocation route directly to canonical triage.

**Architecture:** Keep `email-triage.llm.default` as the required entrypoint and route it only to `email-triage.llm.triage`. Delete the updater and preference behavior-source nodes, remove the triage preference dependency, and bump the two changed LLM contracts to version 2.

**Tech Stack:** Markdown instruction files, schema-version-2 YAML blueprints, pytest, blueprint synchronization.

## Global Constraints

- Preserve the canonical email classification, list, logging, failure, and watermark workflow.
- Delete both the preference updater interface and the empty preference source.
- Leave historical design and plan documents unchanged.
- Do not touch unrelated staged or working-tree changes.

---

### Task 1: Collapse Email Triage to One User-Facing Mode

**Files:**
- Modify: `skills/email-triage/tests/test_llm_routing.py`
- Modify: `skills/email-triage/blueprint.yaml`
- Modify: `skills/email-triage/.SKILL.md.blueprint.yaml`
- Modify: `skills/email-triage/llm_interfaces/.triage.md.blueprint.yaml`
- Modify: `skills/email-triage/llm_interfaces/triage.md`
- Modify: `skills/email-triage/SKILL.md` through blueprint synchronization
- Delete: `skills/email-triage/llm_interfaces/update-personal-preferences.md`
- Delete: `skills/email-triage/llm_interfaces/.update-personal-preferences.md.blueprint.yaml`
- Delete: `skills/email-triage/references/personal-preferences.md`
- Delete: `skills/email-triage/references/.personal-preferences.md.blueprint.yaml`

**Interfaces:**
- Consumes: `email-client.llm.default@3`, `list-manager.llm.default@1`, and existing email-triage machine interfaces.
- Produces: `email-triage.llm.default@2` routing only to `email-triage.llm.triage@2`.

- [x] **Step 1: Replace preference-routing tests with the desired single-route contract**

Assert that the root and default sidecar expose only triage at version 2, the updater and preference files are absent, the triage sidecar has no preference behavior source or file read, and the canonical triage body still contains its fetch, classification, logging, list, failure, and watermark instructions.

- [x] **Step 2: Run the routing tests and verify RED**

Run:

```bash
/home/moeen/anaconda3/bin/pytest -q skills/email-triage/tests/test_llm_routing.py
```

Expected: failures showing version 1, the updater locator/files, and preference behavior source still exist.

- [x] **Step 3: Remove the preference subsystem and bump contracts**

Set the root locators and sidecars to:

```yaml
interfaces:
  - interface: email-triage.llm.default
    version: 2
    blueprint: {base: skill-root, path: .SKILL.md.blueprint.yaml}
  - interface: email-triage.llm.triage
    version: 2
    blueprint: {base: skill-root, path: llm_interfaces/.triage.md.blueprint.yaml}
```

Set the default interface dependency to:

```yaml
uses_interfaces:
  - interface: email-triage.llm.triage
    version: 2
```

Set the triage sidecar to version 2 with `behavior_sources: []` and no direct read of `references/personal-preferences.md`. Remove the `## Personal preferences` section from `triage.md`, remove preference-management triggers and routing from `SKILL.md`, and delete the four updater/source files.

- [x] **Step 4: Synchronize generated blueprint content**

Run:

```bash
/home/moeen/Documents/scripts/bin/dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints
```

Expected: generated `SKILL.md` contract and interface sections reference only `email-triage.llm.triage@2`.

- [x] **Step 5: Verify GREEN and repository contracts**

Run:

```bash
/home/moeen/anaconda3/bin/pytest -q skills/email-triage/tests
/home/moeen/Documents/scripts/bin/dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check
/home/moeen/anaconda3/bin/python validators/runner.py
/usr/bin/git diff --check -- skills/email-triage
```

Expected: email-triage tests and blueprint check pass. Report any repository-validator failure proven to originate in unrelated dirty work.

- [x] **Step 6: Verify installed links and forward-test both clients**

Confirm the installed Claude and Codex skill paths resolve directly to this checkout, then invoke `email-triage` once with each client. Confirm neither client presents a mode-selection prompt; use decision-log and watermark changes as evidence of actual email fetching/classification when new email exists. Reinstall only if an installed skill path is a stale copy rather than a live link.

- [x] **Step 7: Commit the implementation**

Stage only the plan and `skills/email-triage` files, then commit with:

```bash
git commit -m "fix(email-triage): remove preference mode"
```
