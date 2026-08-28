---
name: relocate-nodes
description: Use when registered Officina nodes or their owned files must be moved while mechanically updating blueprint ownership, references, generated artifacts, and callers. Do not use for behavioral refactoring or certification.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-architecture, repository-workflow, assistant-assurance; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 1

Uses Interfaces:
- `relocate-nodes.source.gateway -> relocate-nodes._rtx.interface.relocate@2`

Public Interfaces:
- `relocate-nodes.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `relocate-nodes.interface.default` — Require mechanical preflight, complete semantic occurrence adjudication, accepted application, and an empty target-side postflight.
<!-- END BLUEPRINT INTERFACES -->
## Machine routes

- Review packet: `relocate-nodes._rtx.interface.build-review-packet@1`
- Preflight and atomic apply: `relocate-nodes._rtx.interface.relocate@2`

## Workflow

The relocation has three stages: list the mechanical recipe, let the user
remove false positives and refine relevant prose, then publish the combined
changes through one apply invocation. Stages 1 and 3 are mechanical; do not
turn them into review loops.

1. **List the mechanical recipe.** Build one schema-v3 manifest whose
   `relocations` entries are complete
   physical moves. A nested relocation is one entry, never one move per path
   segment:

   ```yaml
   schema_version: 3
   relocations:
     - from: skills/a/b/c
       to: skills/a/d/e
   ```

   Add scoped `python_modules` only when the Python import mapping cannot be
   proved from repository configuration and parsed imports.
2. Invoke the injected
   relocation interface for a read-only preflight with the exact repository
   root, schema-v3 manifest, and a report path outside the selected repository.
   Leave apply disabled. This report is the exhaustive mechanical replacement
   recipe; do not mutate the repository during Stages 1 or 2.
3. **Build the user review packet.** Invoke the injected review-packet
   interface with the absolute repository root and external report/output paths.

   The packet retains every semantic occurrence but groups Markdown by heading
   and other text by file. Each unit contains the literal mechanical
   replacement, a suggested `rewrite` or `preserve`, and an unset user
   decision. Historical plans and specifications are suggested preserves;
   everything else is suggested rewrite.
4. **Ask the user for help removing false positives.** Show the machine list
   and suggestions compactly. Ask first about whole categories, files, or
   sections; only show individual occurrences where a broader decision is
   ambiguous. The default is the mechanical rewrite. The user may preserve an
   irrelevant unit or refine awkward prose. Never infer persisted-state,
   compatibility, or behavioral policy from an address change.
5. Keep every occurrence ID represented after grouping. Add one
   `semantic_decisions` entry containing its `occurrence_id` and either
   `rewrite` or `preserve`. A rewrite uses the reported candidate unless the
   user supplies a different exact `replacement`.
6. **Rerun preflight** with the reviewed manifest. Continue only when
   `unaccounted_semantic_occurrences` is empty and every planned write and
   delete is intended.
7. **Apply once.** Supply the combined mechanical recipe and all user-assisted
   decisions to the same injected relocation interface with apply enabled.
   Never apply a mechanical subset before user review. Publication is one
   recovery-backed failure-atomic transaction: it locks the repository,
   revalidates the preflight baseline, records external rollback state, and
   restores every touched path if publication or target-side postflight fails.
   An interrupted marker is recovered before the next invocation plans work.
8. Run the identical manifest as a **target-side postflight** without
   `--apply`. Require no planned writes or deletes, no unaccounted occurrences,
   and no errors.

Never invoke certification or installation. Completed manifests are temporary
inputs and are not retained as repository history.
