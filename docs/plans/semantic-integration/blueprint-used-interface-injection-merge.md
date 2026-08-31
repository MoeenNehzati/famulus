# Blueprint Used-Interface Injection Merge Resolution Plan

> **For agentic workers:** Resolve the existing mechanical merge one cluster at a time. Do not commit, abort the merge, or touch unrelated worktree changes without explicit authorization.

**Goal:** Merge `feat/blueprint-used-interface-injection` into `master` while preserving master’s current v6 architecture and the source branch’s direct-used-interface projection.

**Frozen inputs:**

- Target: `master` at `3824c065a07463ccd55f235afffe322b6908719c`
- Source: `feat/blueprint-used-interface-injection` at `03ba03601da5ac5dcbd12d921a51ebda8414a4ee`
- Merge base: `9a16f37bba2c3a759a60ab085ad62d743337f7d8`
- Mechanical merge: open; 61 unresolved paths

## Generated-only `SKILL.md` conflicts: 40 files

### Resolution instruction

These conflict hunks are entirely inside generated blueprint blocks. Do not reconcile their generated prose manually and do not take an entire file from either parent, because that could discard cleanly merged handwritten changes outside the hunk.

For each file below:

1. Resolve only the marked generated-block hunk by retaining one syntactically complete generated alternative and removing the conflict markers. Leave the frontmatter and every handwritten byte outside the hunk unchanged.
2. Do not treat that provisional generated text as final.
3. First resolve the canonical graph, syncer, schema, and validator conflicts listed later in this document.
4. Refresh all generated blocks from the merged canonical blueprints:

   ```text
   dispatcher --caller-skill skill-maker --dry-run skill-maker._rtx.interface.sync-blueprints
   dispatcher --caller-skill skill-maker skill-maker._rtx.interface.sync-blueprints
   dispatcher --caller-skill skill-maker skill-maker._rtx.interface.sync-blueprints --check
   ```

5. Verify that the final blocks describe only direct validated `uses_interfaces` edges, include the target version and description, omit transitive and unused same-module exports, and render the repository’s current invocation format. Verify that handwritten bodies did not change as a consequence of regeneration.

Files:

- `skills/bib-audit/SKILL.md`
- `skills/ci-debug/SKILL.md`
- `skills/cloud-files/SKILL.md`
- `skills/connect-google/SKILL.md`
- `skills/daily-plan/SKILL.md`
- `skills/distill-to-rutters/SKILL.md`
- `skills/email-client/SKILL.md`
- `skills/email-triage/SKILL.md`
- `skills/find-handoff-candidates/SKILL.md`
- `skills/formal-prose-review/SKILL.md`
- `skills/get-weather/SKILL.md`
- `skills/git-workflow/SKILL.md`
- `skills/hook-maker/SKILL.md`
- `skills/initialize-tdd/SKILL.md`
- `skills/latex-workshop/SKILL.md`
- `skills/list-manager/SKILL.md`
- `skills/llm-wakeup/SKILL.md`
- `skills/loose-mode/SKILL.md`
- `skills/make-tex-docstring/SKILL.md`
- `skills/math-dependency-graph/SKILL.md`
- `skills/milestone-logging/SKILL.md`
- `skills/node-drift/SKILL.md`
- `skills/notation-review/SKILL.md`
- `skills/online-calendar/SKILL.md`
- `skills/pdf-to-markdown/SKILL.md`
- `skills/prepare-handoff/SKILL.md`
- `skills/proof-audit/SKILL.md`
- `skills/recurring-tasks/SKILL.md`
- `skills/refactor-node/SKILL.md`
- `skills/regenerate-blueprints/SKILL.md`
- `skills/relocate-nodes/SKILL.md`
- `skills/semantic-integration/SKILL.md`
- `skills/send-feedback/SKILL.md`
- `skills/skill-maker/SKILL.md`
- `skills/technical-flow-review/SKILL.md`
- `skills/tight-mode/SKILL.md`
- `skills/tool-applicability/SKILL.md`
- `skills/update-standards/SKILL.md`
- `skills/using-compass/SKILL.md`
- `skills/wrap-up/SKILL.md`

## Remaining conflicts

There are 19 non-`SKILL.md` conflicts, plus two `SKILL.md` exceptions excluded from the generated-only set.

### Cluster A: projection, injection, and validation pipeline — 7 files

Preserve the source branch’s direct-edge selection semantics, exclusion of transitive and unused exports, domain-error handling, and v6 cleanup. Adapt those behaviors to master’s current graph and invocation architecture; do not restore source-side v5 compatibility or replace master’s current invocation representation wholesale.

- `.githooks/skill/check-blueprints`
- `llmhooks/inject_dispatcher_context.py`
- `skills/skill-maker/_rtx/_blueprint_syncer.py`
- `skills/skill-maker/_rtx/tests/test_blueprint_tools.py`
- `tests/test_officina_blueprint_graph.py`
- `tests/validate_blueprints.py`
- `tests/validate_skill_md_dispatch.py`

Resolution gate: focused graph/syncer/validator tests must prove direct-used-interface rendering in the merged invocation format before regenerating the 40 files.

### Cluster B: current standards and digest closure — 10 files

Master’s current `standard_version: 2.0.0` structure is authoritative. Port the source branch’s interface-only terminology and policy changes into that structure, then recompute revisions and SHA-256 import pins in dependency order. Do not copy the source branch’s older `standard_version: 1.0.0` documents or stale digests.

Treat `authority-disposition.yaml` separately: master deleted it. Keep the deletion unless a still-live v2 migration/fidelity consumer requires equivalent disposition data; if so, move only the required evidence into the current authority rather than restoring the obsolete file automatically.

- `references/node-standards/authority-disposition.yaml`
- `references/node-standards/behavioral-source.standard.yaml`
- `references/node-standards/instruction-behavioral-source.standard.yaml`
- `references/node-standards/instruction-module.standard.yaml`
- `references/node-standards/instruction-node.standard.yaml`
- `references/node-standards/module.standard.yaml`
- `references/node-standards/node.standard.yaml`
- `references/node-standards/python-behavioral-source.standard.yaml`
- `references/node-standards/python-module.standard.yaml`
- `references/node-standards/python-node.standard.yaml`

Resolution gate: validate every changed standard directly and validate the complete pinned import closure after the final digest is known.

### Cluster C: deleted migration evidence — 2 files

Both files were deleted by master and modified by the source branch. Keep master’s deletion if current v2 standards tests already cover the source assertions. Otherwise relocate only the still-relevant interface-only assertions or supersession evidence into the current test/fixture architecture.

- `tests/fixtures/standards/skill-refactoring-source-map.yaml`
- `tests/test_migrated_standards_fidelity.py`

Resolution gate: every source assertion must be mapped to a current test, stronger replacement evidence, or an explicit decision that its legacy-only subject no longer exists.

### Exception D: mixed generated and handwritten certification conflict — 1 file

`skills/node-certify/SKILL.md` changed outside the generated block on master, but its actual conflict hunk is generated-only. Preserve master’s cleanly merged scheduler-based certification algorithm, setup preflight, current interface versions, and audit-pool behavior, then regenerate its blueprint block using the merged canonical blueprint.

- `skills/node-certify/SKILL.md`

### Exception E: removed installer architecture — 1 file

`skills/install-assistant-tools/SKILL.md` is a modify/delete conflict. Master replaced that monolithic skill with current components including `dev-activation` and `install-launchers`. Keep the deletion unless a source-side used-interface assertion remains applicable; relocate any such assertion to the current owning module and its tests rather than restoring the obsolete skill.

- `skills/install-assistant-tools/SKILL.md`

## Proposed resolution order

1. Resolve Cluster A against master’s current runtime and graph architecture.
2. Resolve Cluster B and recompute its complete v2 digest closure.
3. Resolve Cluster C against the resulting standards architecture.
4. Resolve Exceptions D and E without restoring superseded behavior.
5. Resolve the 40 generated hunks provisionally, run blueprint synchronization, and check generated/body invariants.
6. Confirm `git diff --name-only --diff-filter=U` is empty, run focused cluster tests, then run the repository integration gate.

## Executed resolution notes

- The source branch changed only generated regions in the 40 listed `SKILL.md` files. Selecting master provisionally was therefore safe after verifying branch scope; canonical synchronization then replaced those regions.
- Synchronization covered 44 current skills, including master-only `dev-activation`, `install-launchers`, and `setup-python-environment`, and removed their superseded contract blocks.
- Because the two branches reused equal revision numbers for divergent standards, the merged closure advances beyond both parents: refactoring 7, node/module/behavioral-source 16, instruction-node 17, python-node 20, instruction-module 20, instruction-behavioral-source 18, python-module 22, and python-behavioral-source 21.
- Master’s deletions of `authority-disposition.yaml`, the legacy fidelity fixture/test, and `install-assistant-tools` are retained. Current standards tests and the merged direct-use tests cover the applicable interface-only behavior.
