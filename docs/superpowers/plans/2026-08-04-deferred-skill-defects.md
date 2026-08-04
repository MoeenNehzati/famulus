# Deferred Skill Defects Implementation Plan

> Execute test-first in the isolated `codex/deferred-fixes` worktree.

**Goal:** Repair the seven confirmed deferred defects, audit the result, and commit one verified checkpoint before further refactor iterations.

**Scope:** Minimal implementation, blueprint, instruction, generated-doc, and inventory corrections. No standards expansion or unrelated cleanup.

## Tasks

1. Add a `list-manager` regression for unfiltered deadline sorting; make `cmd_read` sort the full document and complete the route usage.
2. Add a dispatch-contract regression for graph-server arguments; replace its permissive process binding with exact flags and zero positionals.
3. Add a recurring-task ownership regression; register `_jobs_config.py` and link all five importers to it.
4. Add a catalog regression; make the recurring-task summary platform-neutral, remove `skill.mmd` from disk and ownership, and regenerate docs.
5. Pressure-test the `llm-wakeup` reporting instructions; make policy and scheduling routes report their actual outcomes.
6. Add a `skill-drift` diagnostic regression; change only the obsolete user-facing wording.
7. Update the nested-module inventory to the twenty implementation-bearing registered modules.
8. Run focused tests and blueprint synchronization/checks, then the precommit suite.
9. Conduct an independent diff/contract audit, fix any findings, and commit exact paths.
10. On the clean commit, run the inventory test and final verification; correct and recommit if necessary.
