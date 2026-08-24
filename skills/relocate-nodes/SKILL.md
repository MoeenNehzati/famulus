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
## Workflow

1. Build one schema-v3 manifest whose `relocations` entries are complete
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
2. **Preflight mechanical closure before adjudication.** Invoke the private
   route without `--apply`, with the report outside the selected repository:

   ```console
   dispatcher --caller-skill relocate-nodes \
     relocate-nodes._rtx.interface.relocate \
     --root /absolute/repository \
     --manifest /tmp/relocation.yaml \
     --report /tmp/relocation-report.json
   ```

3. Review every occurrence ID across every reported file type, including
   Python strings, Markdown, TeX, extensionless files, and persisted config.
   Also review `skipped_text_files`; binary, non-UTF-8, and symlink entries are
   never silently treated as text.
4. Ask the user before deciding any occurrence involving persisted state,
   compatibility, or behavior. Never infer those policies from a mechanical
   address change.
5. Add a complete `semantic_decisions` selector for each accepted occurrence.
   Choose `rewrite` or `preserve`, retain the reported identity, path, digest,
   span, ordinal, match, and positive count, and write a nonempty reason. A
   rewrite also records the exact enclosing old and replacement text.
6. Never use blind global substitution. `exact_rewrites` cannot account for
   semantic occurrences; it is only an exceptional, preconditioned mechanism
   for non-address text.
7. **Rerun preflight** with the reviewed manifest. Continue only when
   `unaccounted_semantic_occurrences` and all error categories are empty and
   every planned mechanical or decision write is intended.
8. **Apply the reviewed manifest** by rerunning that command with `--apply`.
   Publication uses atomic replacement per file, not a repository-wide
   transaction or rollback.
9. Run the identical manifest as a **target-side postflight** without
   `--apply`. Require no planned writes or deletes, no unaccounted occurrences,
   and no error categories. Preserve decisions may remain in the raw occurrence
   inventory when they are explicitly accounted.

Never invoke certification or installation. Completed manifests are temporary
inputs and are not retained as repository history.
