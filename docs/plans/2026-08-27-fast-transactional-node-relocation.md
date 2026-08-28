# Compact Transactional Node Relocation Plan

**Goal:** Keep the complete relocation runtime below 500 physical Python lines
while implementing recipe generation, human-assisted text review, and one
failure-atomic publication.

## Scope

- Reuse repository configuration, Git inventory, YAML parsing, Python AST
  validation, blueprint graph validation, and shared atomic-file helpers.
- Keep schema-v3 manifests with complete `from`/`to` directory moves, optional
  Python module mappings, inventory exclusions, and per-occurrence decisions.
- Do not retain compatibility manifests, package/catalog inference, shadow
  repositories, certification, or general refactoring machinery.

## Implementation

1. Build an in-memory recipe without repository writes.
   - Move every regular file beneath each source directory.
   - Rewrite parsed YAML/JSON scalar values mechanically while preserving
     comments and formatting.
   - Rewrite validated Python import lines mechanically.
   - Rewrite structured `SKILL.md` identifiers mechanically.
   - Report every remaining textual address occurrence with a stable ID and
     suggested replacement.
2. Group reported Markdown hits by heading and other hits by file. Suggest
   preserving historical plans/specifications and rewriting current material.
   The user chooses `rewrite` or `preserve`, optionally supplying exact text.
3. Apply the reviewed recipe once.
   - Acquire the shared repository lock.
   - Revalidate every touched baseline.
   - Persist the rollback ledger outside the repository.
   - Atomically replace files, remove retired paths, and run blueprint graph
     and empty-postflight checks.
   - Restore the baseline on any caught failure; recover an interrupted marker
     before the next preflight.

## Acceptance

- Runtime Python: at most 500 physical lines.
- Focused recipe, review, stale-baseline, mid-publication rollback, directory
  mode, and recovery tests pass.
- The public two-node preflight and review-packet routes complete in seconds.
- `skill-certifier -> node-certifier` and `skill-drift -> node-drift` are
  applied only after the user clears the grouped review packet.
