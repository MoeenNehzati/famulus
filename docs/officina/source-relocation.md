# Source relocation

Use the manifest-driven relocation command when an Officina source, sidecar,
or package must move without changing behavior. The command updates declared
paths and typed identities across active repository text, regenerates declared
package catalogs, validates the projected tree, and only writes when
`--apply` is supplied.

## Workflow

1. Add a YAML manifest under `refactors/`. Declare file moves and any changed
   Python modules, source IDs, interface IDs, blueprint ownership, caller
   authorization, or exact location-dependent text.
2. Run a read-only preflight:

   ```console
   python3 scripts/relocate_officina_sources.py \
     --manifest refactors/example.yaml \
     --report /tmp/example-relocation-report.json
   ```

3. Apply the same manifest to a disposable repository copy. Run the focused
   tests and validators for the package being moved, then rerun preflight. A
   completed relocation must report empty `moves`, `writes`, `deletes`,
   `blueprint_changes`, `digest_changes`, and `unresolved_references`.
4. Apply the audited manifest to the real worktree and repeat focused and
   repository-supported verification.

The current full-tree manifest is
[`refactors/officina-source-relocation.yaml`](../../refactors/officina-source-relocation.yaml).

## Manifest responsibilities

- `moves` relocates files or directories.
- `renames` distinguishes filesystem paths, Python module addresses,
  behavioral-source IDs, and interface IDs.
- `ownership_transfers` updates registered module ownership without inventing
  exports or authority.
- `caller_additions` grants only explicitly declared new callers.
- `exact_rewrites` handles location-dependent text with an exact occurrence
  precondition.
- `package_catalogs` regenerates README-only `__init__.py` docstrings and
  accounts for every directly owned file or child package.
- `text_exclusions` and `active_address_exclusions` protect immutable or
  deliberately historical records from active-address rewriting.
- `standard_digest_roots` refreshes pinned standard-import digests after a
  moved address changes authoritative standard bytes.

For a file-only refactor inside one module, a `move`, a `python_modules`
rename, the affected sidecar move, exact blueprint path/content rewrites, and
the package catalog are normally sufficient. Use direct implementation-module
imports; the tool does not create compatibility aliases or package facades.

The engine is intentionally not a general code refactoring system. It does not
rename functions or classes, infer authority, decompose implementation bodies,
or rewrite arbitrary syntax. If a required replacement cannot be represented
as a typed identity, declare it as an exact rewrite so preflight can fail
closed when the expected text is absent or ambiguous.
