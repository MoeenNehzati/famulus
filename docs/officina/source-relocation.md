# Source relocation

Use the registered `relocate-nodes` route when a registered Officina node or a
graph-owned file must move without changing behavior. Schema v3 derives logical
node IDs from the roots in the selected repository's `officina.toml`, projects
only mechanically proved address changes, and inventories every remaining
retired-address occurrence for review. It never invokes an LLM.

## Workflow

1. Build one exact schema-v3 YAML manifest at a temporary or caller-selected
   path. Each item in `relocations` is one complete physical move; do not split
   a nested path into segment-by-segment moves.
2. Run the first read-only preflight:

   ```console
   dispatcher --caller-skill relocate-nodes \
     relocate-nodes._rtx.interface.relocate \
     --root /absolute/repository \
     --manifest /tmp/relocation.yaml \
     --report /tmp/relocation-report.json
   ```

3. Review every item in `semantic_occurrences`, across every reported file
   type. Ask the user before deciding persisted-state, compatibility-address,
   or behavioral-migration occurrences. Add a complete `rewrite` or `preserve`
   selector with a positive count and nonempty reason to `semantic_decisions`.
   Review `skipped_text_files` separately.
4. Rerun preflight with the reviewed manifest. Require
   `unaccounted_semantic_occurrences` and all error categories to be empty.
   Review all planned `moves`, `writes`, `deletes`, blueprint, certification,
   digest, generated-artifact, and validation categories.
5. Apply the accepted manifest with the identical command plus `--apply`:

   ```console
   dispatcher --caller-skill relocate-nodes \
     relocate-nodes._rtx.interface.relocate \
     --root /absolute/repository \
     --manifest /tmp/relocation.yaml \
     --report /tmp/relocation-report.json \
     --apply
   ```

6. Run the identical manifest without `--apply` as target-side postflight. A
   completed relocation reports no planned writes or deletes, no unaccounted
   occurrences, and no errors. A reviewed preserve occurrence may remain in
   `semantic_occurrences`; its matching decision accounts for it.
7. Run focused tests and repository-supported validation for the moved nodes.

Completed manifests are temporary inputs, not repository history. Remove them
after the empty second preflight and required verification are recorded.

## Migrating v2 manifests

Schema v2 separated `moves` and `renames`. Schema v3 uses one typed
`relocations` collection and derives registered-node identities from configured
roots.

For a registered-node relocation, replace a v2 move such as:

```yaml
schema_version: 2
moves:
  - from: skills/a/b/c
    to: skills/a/d/e
```

with one v3 relocation:

```yaml
schema_version: 3
relocations:
  - from: skills/a/b/c
    to: skills/a/d/e
```

This is one subtree relocation. The runtime derives `a.b.c -> a.d.e` and its
descendant module, source, and interface mappings; `skills/a`, `skills/b`, and
`skills/c` are not separate moves.

For an owned-file relocation, keep one physical entry and let the graph prove
its owner:

```yaml
schema_version: 3
relocations:
  - from: skills/demo/old.py
    to: skills/demo/new.py
```

If ownership moves between modules, add the explicit `ownership_transfers`
record for the behavioral source, optional export, content pattern, and old/new
blueprint owners. The file move itself remains in `relocations`.

`officina.toml` module roots do not prove Python import roots. Add a scoped
mapping only when required:

```yaml
schema_version: 3
relocations:
  - from: skills/demo/old.py
    to: skills/demo/new.py
    python_modules:
      - from: demo.old
        to: demo.new
```

The runtime applies this mapping only to parsed absolute imports and includes
remaining string or prose occurrences in semantic review. It never guesses a
Python module mapping.

## Manifest and report semantics

- `relocations` declares physical old/new endpoints and optional scoped
  `python_modules` mappings.
- `ownership_transfers` updates registered module ownership without inventing
  exports or authority.
- `caller_additions` grants only explicitly declared new callers.
- `semantic_decisions` records complete occurrence selectors, `rewrite` or
  `preserve`, an expected count, exact enclosing text, and a reason. Rewrite
  decisions also record exact replacement text.
- `exact_rewrites` is an exceptional non-address mechanism. It cannot account
  for a semantic occurrence and is not a substitute for reviewed decisions.
- `package_catalogs` regenerates README-only `__init__.py` docstrings and
  accounts for every directly owned file or child package.
- `inventory_exclusions` adds caller-selected roots to the exact defaults
  `.git`, `.claude`, `.codex`, and `.superpowers`. Exclusions do not silently
  expand from blueprint-closure internals.
- `text_exclusions` and `active_address_exclusions` apply only to their
  mechanical validation roles; they do not shrink semantic inventory.
- `standard_digest_roots` refreshes pinned standard-import digests after a
  moved address changes authoritative standard bytes.

The report separates raw `semantic_occurrences`, accepted
`semantic_decisions`, gated `unaccounted_semantic_occurrences`,
`derived_relocations`, and `skipped_text_files`. The semantic scan attempts
strict UTF-8 for every included regular file regardless of suffix. Binary,
non-UTF-8, and symlink entries are reported as skipped; symlinks are never
followed or rewritten.

Apply rechecks a physical pre-projection baseline immediately before publish.
That baseline fingerprints included regular files, skipped binary bytes,
symlinks and their link targets, modes, included membership, exclusion
boundaries, and expected-absent relocation targets. It is distinct from the
projected semantic inventory.

All inputs are prevalidated and each file replacement uses an atomic rename.
The operation is not a repository-wide transaction: a mid-publish failure can
leave already replaced files in place, and automatic repository rollback is
not promised.

The engine is intentionally not a general code refactoring system. It does not
rename functions or classes, infer authority, decompose implementation bodies,
or rewrite arbitrary syntax. Never use blind global substitution to resolve
reported semantic addresses.
