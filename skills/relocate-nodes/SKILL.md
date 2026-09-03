---
name: relocate-nodes
description: Use when registered Officina nodes or their owned files must be moved while mechanically updating blueprint ownership, references, generated artifacts, and callers. Do not use for behavioral refactoring or certification.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `relocate-nodes._rtx.interface.build-review-packet` — Group one exhaustive relocation preflight for user-assisted false-positive filtering.
  - Caller: `relocate-nodes`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--output": "OUTPUT", "--report": "REPORT", "--root": "ROOT"}, "positionals": [], "stdin": null}
    Required options: ["--output", "--report", "--root"]; positional arity: 0..0; stdin: forbidden
- `relocate-nodes._rtx.interface.relocate` — Preflight or publish one manifest-driven registered-node relocation as one recovery-backed failure-atomic change set.
  - Caller: `relocate-nodes`
  - Version: 2
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--apply": true, "--manifest": "MANIFEST", "--report": "REPORT", "--root": "ROOT"}, "positionals": [], "stdin": null}
    Required options: ["--manifest"]; positional arity: 0..0; stdin: forbidden

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

   The interface writes the full JSON packet and prints its complete compact
   human rendering. Present that rendering directly; do not spend an LLM turn
   reconstructing it. The packet groups Markdown by heading and other text by
   file, retaining every occurrence and its suggested decision. Historical
   plans and specifications are suggested preserves; everything else is
   suggested rewrite.
4. **Ask the user for help removing false positives.** Before asking for
   decisions, render every review unit with its path, section
   when present, suggested decision, and occurrence count. Never substitute
   aggregate counts or an explanation for the packet itself. Show individual
   occurrence contexts when a unit is ambiguous or the user asks. Then ask
   about whole categories, files, or sections. The default is the mechanical
   rewrite. The user may preserve an irrelevant unit or refine awkward prose.
   Never infer persisted-state, compatibility, or behavioral policy from an
   address change.
5. **Encode the answer compactly.** Prefer one `default_disposition` plus exact
   path-level `disposition_overrides`; the machine expands these rules into the
   occurrence ledger. Use `semantic_decisions` only for individual exceptions
   or a user-supplied exact replacement. Precedence is individual decision,
   path override, then default.

   Put relevant changes that contain no reported old identifier in
   `supplemental_edits`. Each entry names the projected target `path`, one
   nonempty exact `expected` string, and its `replacement`. The precondition
   must identify exactly one old or already-replaced string; ambiguous or
   missing edits fail preflight. Supplemental edits join the same transaction.
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

Use the report's machine timings when discussing performance. An apply report
separates initial planning, transactional writes, blueprint-graph verification,
target-side postflight, and total time; do not attribute later test time to
ledger application.

Never invoke certification or installation. Completed manifests are temporary
inputs and are not retained as repository history.
