# Officina relocation: compact mechanical closure

Status: accepted implementation design

## Purpose

A relocation preflight must return the complete deterministic artifact set that
an approved source move requires, without modifying the real repository. Its
only publication boundary remains `apply_change_set`.

This design closes a deliberately narrow gap: a manifest-generated, README-only
Officina package initializer may need to join the certification basis, and
blueprint-derived artifacts may need canonical synchronization. It does not
decide architecture.

## Authority

The relocation manifest remains authoritative for moves, typed rewrites, source
ownership transfers, caller declarations, package catalogs, and package-boundary
disposition. The closure never infers a module registration, a dependency, an
export, a caller permission, or certification authority for substantive code.

A catalog initializer below `src/officina/` joins the certification basis only
when its AST is exactly one module docstring. Any executable or other
substantive initializer is rejected with its exact path.

## One invocation, one publication

Both read-only preflight and `--apply` execute one plan:

1. Project the manifest into an in-memory `ChangeSet`.
2. Refresh declared standard digests.
3. Close mechanical artifacts in a temporary shadow tree.
4. Validate the ordinary projected-tree invariants.
5. Return the complete `ChangeSet` report.
6. Only `--apply` publishes that already-validated set once.

A closure error never reaches `apply_change_set`; it therefore cannot mutate
the real tree. The normal concurrent-byte guard still protects final
publication.

## Shadow boundary

`closure.py` materializes the projected regular files from `ChangeSet` into a
`TemporaryDirectory`, preserving bytes and modes. It rejects an included
symlink and excludes Git metadata, worktrees, caches, virtual environments,
dependency directories, build output, certificate records, and pooled reviews.

A tiny relocation-mechanics fixture with neither canonical marker has no
closure work. A tree containing either `references/blueprint/` or
`skills/skill-maker/_rtx/_blueprint_syncer.py` is treated as an Officina
closure input: it must contain the other marker and
`references/certification/certification-basis-roots.json`, or preflight fails
with the missing exact path. This preserves narrow engine tests without
silently accepting an incomplete Officina repository.

## Canonical closure sequence

Within the shadow tree, the coordinator:

1. updates the sorted, unique certification-basis JSON list from approved
   README-only Officina catalog initializers;
2. snapshots every included shadow file's bytes and mode;
3. runs the copied canonical blueprint synchronizer once with schema version 6;
4. permits changed bytes only in generated blocks of `skills/*/SKILL.md` and
   `references/blueprint/runtime_dependencies.json`;
5. reconciles those exact allowed bytes and modes into the in-memory
   `ChangeSet`;
6. runs the same synchronizer once with `--check`; and
7. loads the canonical schema-v6 repository graph against the shadow.

The closure neither reproduces synchronizer logic nor performs a fixed-point
loop. One synchronization invocation and one check invocation are the complete
mechanical closure contract.

## Report

`ChangeSet.report()` contains calculated, sorted collections for:

- certification-basis changes;
- generated-artifact changes; and
- successful synchronizer and graph-validation actions.

These are distinct from declared moves, direct writes, blueprint changes, and
standard digest changes. An empty list means the corresponding mechanism had no
change, not an unconditional placeholder.

## Non-goals

The relocation command does not:

- trace imports or infer dependencies;
- change certification, certifier APIs, runtime tracing, repository validators,
  or visualization;
- initialize Git or copy Git metadata;
- issue certificates, sign, install, activate, commit, or push; or
- add compatibility facades or duplicate graph/synchronizer behavior.

Focused relocation tests and a separate certification action remain the
post-publication verification boundary.
