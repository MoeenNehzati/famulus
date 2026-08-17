# Documentation Completeness Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public README and documentation accurately describe installation, prerequisites, launchers, updates, removal, security boundaries, and the current managed dependency inventory.

**Architecture:** Keep the README as the concise public entry point and place operational detail in the installation, launcher, security, and dependency-audit pages. Protect the most important README claims with the existing user-contract validator, then run the repository documentation validators and strict site build.

**Tech Stack:** Markdown, Python documentation validators, pytest, MkDocs.

## Global Constraints

- Work only in the isolated `codex/docs-publication-fix` worktree.
- Do not touch the main checkout's unrelated dirty files.
- Keep every regular file below `docs/` public except the `docs/plans/` subtree.
- Do not claim current green cross-platform CI while the published Python Tests workflow is failing.
- Do not describe local credential deletion as server-side OAuth revocation.
- Do not commit or push without separate user authorization.

---

### Task 1: Protect the public entry-point contract

**Files:**
- Modify: `validators/readme_user_contract.py`
- Modify: `tests/validate_documentation_validators.py`

**Interfaces:**
- Consumes: root `README.md`.
- Produces: validator errors when the README omits the supported Phase 1 entry point, project status, or public issue route, or promotes the scaffold-only repair as a fresh install.

- [x] **Step 1: Add failing validator tests**

Add fixture content for `_phase_entry.py`, `No promoted stable release`, and the public issue tracker. Add a test replacing `_phase_entry.py` with `_install_scaffold.py` and require a validation error.

- [x] **Step 2: Run the focused validator tests and confirm failure**

Run: `python3 repo_checks.py --task tests:shared --selector tests/validate_documentation_validators.py --jobs 1`

Expected: failure until the validator contract is updated.

- [x] **Step 3: Update the validator contract**

Require the three public-readiness snippets and forbid the scaffold-only fresh-install command.

- [x] **Step 4: Run the focused validator tests**

Expected: all tests pass.

### Task 2: Correct installation and lifecycle documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/officina/installation.md`
- Modify: `docs/launchers.md`

**Interfaces:**
- Consumes: `_phase_entry.py`, `_fs_links.py`, `_agent_launchers.py`, platform path resolver, Claude/Codex plugin CLI help.
- Produces: a working fresh-install command, accurate platform paths and worker locations, explicit `background_run` behavior, and copy-paste update/removal sequences.

- [x] **Step 1: Replace the scaffold-only Quick Start**

Use `_phase_entry.py --non-interactive --no-dev-mode --no-optional-deps` and explain that it builds and activates the managed runtime before writing launchers.

- [x] **Step 2: Add project status, prerequisites, and support**

State the pre-stable `0.1.0` status, Python/host/network prerequisites, provisional platform support, and public issue route.

- [x] **Step 3: Correct installer terminology and defaults**

Use actual script names, list platform-specific bin/state paths, distinguish plugin and development worker roots, and explain that `background_run` is installed because `invoke-skill` depends on it.

- [x] **Step 4: Document update and removal ordering**

Document Claude and Codex marketplace refresh commands, rerunning Phase 1 after updates, disabling automation first, manifest-based workstation uninstall before plugin removal, and separate credential revocation/purge.

- [x] **Step 5: Document the unattended launcher boundary**

Describe `background_run`, its non-interactive purpose, and the approval/sandbox bypass used only when an enabled scheduled job invokes `invoke-skill`.

### Task 3: Refresh public audit records

**Files:**
- Modify: `docs/dependency-and-bootstrap-audit.md`
- Modify: `docs/security-and-privacy.md`

**Interfaces:**
- Consumes: current runtime manifest/lock and security-relevant changes through `e74b8ad7`.
- Produces: a complete direct-dependency table and an audit snapshot that discloses current unattended execution behavior.

- [x] **Step 1: Add missing direct dependencies**

Add `pyflakes==3.2.0`, `pytest==8.3.4`, and `pytest-xdist==3.8.0`, with MIT license metadata and their release roles.

- [x] **Step 2: Record the current review boundary**

State that the original trust-boundary audit was reviewed against `e74b8ad7` on 2026-08-17 and identify the reviewed credential relocation, current-source installer bootstrap, and unattended agent changes.

- [x] **Step 3: Add the unattended-execution security boundary**

Document that installation creates no scheduled job, but enabled jobs run `background_run` without interactive approval prompts or the Codex sandbox.

### Task 4: Verify the final documentation surface

**Files:**
- Verify all files modified in Tasks 1-3 plus this plan.

**Interfaces:**
- Consumes: final documentation and validators.
- Produces: focused tests, repository validators, strict site build, link checks, and an exact-scope diff.

- [x] **Step 1: Run focused tests**

Run the documentation validator tests and documentation-site tests through `repo_checks.py`.

- [x] **Step 2: Run security-relevant tests**

Run the installer, recurring-task, Google credential, Drive, Calendar, and email test selections needed to support the refreshed audit boundary.

- [x] **Step 3: Run repository validators and strict site build**

Run `python3 repo_checks.py --suite validators` and `./scripts/docs-site.py build`.

- [x] **Step 4: Inspect exact scope**

Review `git diff --check`, changed paths, and the final diff. Do not stage, commit, or push.
