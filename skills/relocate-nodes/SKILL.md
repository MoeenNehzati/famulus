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
- `relocate-nodes.interface.default` — Require reviewed preflight, accepted application, and an empty second preflight.
<!-- END BLUEPRINT INTERFACES -->
## Workflow

1. Build or receive one exact schema-v3 relocation manifest.
2. Invoke `relocate-nodes._rtx.interface.relocate` without `--apply`.
3. Review every raw semantic occurrence and record a complete `rewrite` or
   `preserve` decision for each occurrence that should be accounted.
4. Invoke the same command with `--apply` only after
   `unaccounted_semantic_occurrences` and every error category are empty.
5. Run the same preflight again and require every change category to be empty.

Never invoke certification or installation. Completed manifests are temporary
inputs and are not retained as repository history.
