# Skill Drift Runtime Module

This document describes the internal dependency-explorer module for
`skill-drift`. It provides the dependency-exploration and deterministic hashing
machinery used by the current drift status command.

The core idea is:

1. Discover every file that can affect a skill, an interface, or a declared file
   root.
2. Convert those files into deterministic hash entries.
3. Hash the entries in a stable order.
4. Compare the stored audit record against a newly computed hash state.

The module is intentionally split so dependency discovery can be tested
independently from report rendering and installed-skill discovery.

## What Is Matched Recursively

At a high level, the recursive dependency set is:

- a file root includes that file;
- a directory root includes every file and symlink below it;
- the whole-skill hash includes canonical blueprint metadata with `direct_io`
  declarations removed;
- an interface includes a canonical structured metadata entry for its blueprint
  declaration with `direct_io` removed, its binding file, declared behavior
  sources, and discovered Python runtime files;
- an interface includes interfaces declared in `uses_interfaces` by
  recursively including those target interface hashes;
- a PythonMachineInterface includes files loaded by `--route-smoke`, local
  same-skill imports, relevant `src/officina` imports, package `__init__.py`
  files, and declared `DispatchCall` targets;
- a declared dispatch target includes its resolved command-runtime file and, if
  it is another PythonMachineInterface, that target's recursively discovered
  runtime files;
- a skill includes all LLM and machine interfaces.

The explorer deduplicates by repo-relative path, so a file reached through more
than one route appears once in the final dependency set.

## Public Runtime Surface

The main API is `DependencyExplorer`:

- `explore_file(path, base_dir=None, reason="file")`
  returns existing files relevant to one file or directory.
- `explore_interface(skill_dir, interface_spec)`
  returns files relevant to one blueprint interface.
- `explore_skill(skill_dir, blueprint)`
  returns files relevant to the whole skill.

Each method returns `DependencyFile` values:

- `label`: repo-relative stable label, used later by hashing.
- `path`: absolute filesystem path.
- `reason`: diagnostic explanation for why the file was included.

The hashing helpers sit on top of the explorer:

- `hash_declared_roots(skill_dir, repo_root, declared_roots)`
- `hash_interface(skill_dir, repo_root, interface_spec)`
- `hash_skill(skill_dir, repo_root, blueprint)`

These helpers produce `sha256:<hex>` digests from `HashEntry` values. The hash
input includes the entry kind, label, and bytes, separated with NUL bytes and
sorted deterministically.

Blueprint YAML parsing is owned by the shared extractor in
`src/officina/blueprint_search.py`. `skill-drift` uses
`load_blueprint_record()` for exact blueprint paths and uses
`strip_selected_paths()` for selector-based metadata projection before hashing.

Interface hashes also include `uses_interfaces` entries. Each entry stores the
canonical interface name and that target interface's hash. This means an LLM
interface that routes work through a machine interface becomes stale when that
machine interface or its recursive dependencies change.

## Behavior Sources

Declared behavior roots come from `behavior_sources` on LLM interfaces and
`invocation.behavior_sources` on machine interfaces. LLM interfaces also include
their binding file, for example `SKILL.md`.

These are behavior-shaping files and directories: instruction Markdown, schemas,
templates, examples, parser tables, policies, validation rules, and similar
material. They are not ordinary user subject inputs. Python imports and
dispatcher targets are discovered mechanically.

`direct_io` entries are not behavior roots and are not hash inputs. They
describe operational data the interface reads or writes during an invocation,
such as inboxes, calendars, stdout, user documents, remote files, or API
responses. Neither the declaration nor the live resources are content-hashed.

Root resolution is deliberately narrow:

- plain relative paths resolve from the skill directory;
- `$repo/...` paths resolve from the repository root;
- absolute paths are rejected;
- paths containing `..` are rejected;
- paths that escape their base after resolution are rejected.

This keeps the drift hash bounded to the repository and avoids accidentally
hashing arbitrary machine-local state.

## File and Directory Discovery

`explore_file` accepts a file, symlink, or directory.

For a regular file, the explorer includes that file. For a symlink, it includes
the symlink entry. For a directory, it walks the directory recursively and
includes all file and symlink children.

The explorer skips generated or irrelevant local artifacts:

- `__pycache__`
- `.pytest_cache`
- `.DS_Store`
- `.last_audit.json`
- `*.pyc`

The `.last_audit.json` exclusion matters because the audit record must not change
the hash that it is recording.

Missing paths are treated differently by layer:

- `DependencyExplorer` only returns existing files.
- `collect_declared_root_entries` records missing declared behavior sources as
  `missing` hash entries, so adding a formerly missing source changes the hash.

## Prose References

The dependency explorer deliberately does not parse Markdown, prose, inline code,
or other LLM-readable references. `skill-drift` is a mechanical checker: it
trusts the blueprint accepted at certification time and hashes declared roots,
runtime dependencies, used interfaces, and explicit audit-policy modules.

If skill instructions tell the LLM to read another file, editing those
instructions changes the skill hash and makes the audit record stale.
`skill-audit` must then decide whether that referenced file belongs in the
blueprint-declared surface before writing a fresh record.

## Python Runtime Dependencies

Python machine interfaces need more than their entrypoint file. Their behavior
can depend on same-skill modules, shared `officina` modules, package
`__init__.py` files, and other skills reached through dispatch.

`explore_python_runtime_dependency_files` handles this by running a clean Python
subprocess. Inside that subprocess it:

1. loads the PythonMachineInterface entrypoint;
2. runs the interface with `--route-smoke`;
3. inspects `sys.modules` for loaded `.py` and `.pyi` files under:
   - the current skill directory,
   - the repository `skills/` directory,
   - `src/officina`;
4. asks `DispatchDependencyResolver` for declared `DispatchCall` dependencies;
5. follows those dispatch dependencies recursively;
6. includes resolved command-runtime files under `skills/`;
7. route-smokes target PythonMachineInterface dependencies and captures their
   loaded modules too.

This makes dispatch dependencies explicit and checkable. Runtime code should not
call raw `dispatch(...)` directly. Instead, the interface declares a menu of
`DispatchCall` values and calls them through `PythonMachineInterface.dispatch`.
The validators enforce that declared dispatch calls use the correct
`caller_skill` and that raw dispatcher use is not introduced in skill runtime
code.

The route-smoke hook is therefore part of the dependency contract. If a
same-skill import only happens in a normal runtime branch, `route_smoke` must
import the behavior-relevant module cheaply and without real side effects.

## Hash Entries

`entries_for_path` converts filesystem objects into `HashEntry` values:

- missing path: `kind="missing"`, empty bytes;
- symlink: `kind="symlink"`, link target bytes;
- regular file: `kind="file"`, file bytes;
- directory: recursively emits child file and symlink entries;
- other special filesystem object: `kind="special"`, empty bytes.

`interface_metadata_entry` adds the interface blueprint declaration, with
`direct_io` removed through the shared YAML selector projection
`strip_selected_paths(..., "**.direct_io")`, as `kind="json"` with deterministic
JSON bytes. This catches metadata-only changes to fields such as descriptions,
patterns, access control, invocation arguments, runtime dependencies,
behavior-source declarations, ownership, and `uses_interfaces`.

`digest_entries` sorts entries before hashing, so traversal order does not
affect the final digest. The hash uses the entry kind and label as well as the
content bytes, so changing a file path, symlink target, or file content changes
the digest.

The dependency explorer returns files; the hash layer decides how to represent
those files as hash entries. This separation is deliberate: tests can ask
"which files matter?" without also asserting byte-level hash details.

## Skill-Level Hashing

`hash_skill` hashes canonical blueprint metadata with `direct_io` removed by the
same selector projection, then walks all blueprint interfaces under:

- `interfaces.llm`
- `interfaces.machine`

It includes each interface's explored dependencies. Legacy compatibility
sidecars such as `depends_on_skills` and `permissions.json` are intentionally
not hash inputs; dependency and suggested-permission metadata now come from
`blueprint.yaml`.

## Tests

The dependency behavior is covered by
`skills/skill-drift/tests/test_dependency_explorer.py`.

That suite checks:

- absence of Markdown/prose reference chasing;
- interface union of behavior sources and Python dependencies;
- whole-skill union of LLM interfaces, machine interfaces, and compatibility
  files;
- same-skill Python imports;
- package `__init__.py` inclusion;
- shared `officina` imports;
- declared dispatch dependencies;
- command-runtime file inclusion;
- mixed local, shared, and dispatched dependencies;
- deep dispatch chains;
- branching dispatch dependencies.

Hash-specific behavior is covered by
`skills/skill-drift/tests/test_drift_hash.py`.

Runtime dispatch and validator behavior is covered by:

- `tests/test_officina_python_machine_interface.py`
- `tests/validate_dispatcher_usage.py`
- `tests/validate_dispatch_caller_skill.py`

## Known Boundaries

This module computes dependency and hash state. The status checker owns
audit-record reads, status derivation, JSON rendering, and Markdown report
writing.

The main known limitations are:

- Prose and Markdown references are not scanned by design; `skill-audit` owns
  blueprint-completeness judgment.
- Python dependency discovery depends on `route_smoke` importing the relevant
  same-skill modules.
- Declared dispatch menus may conservatively over-include dependencies when a
  class-level dispatch menu is shared by multiple blueprint interfaces.
- The dependency explorer reports existing files; missing-path hash entries are
  added by the declared-root hash layer.

These limits are acceptable for the current first pass because `skill-drift`
raises mechanical audit-stale flags while `skill-audit` owns certification of
blueprint exactness.

## Deferred Correctness Fixes

The current implementation is a first pass. It is good enough to build the
end-to-end drift-check workflow, but it is not yet strong enough to certify
that every relevant skill/interface change is tracked. These are known follow-up
items from the dependency-explorer audit.

1. Behavior-source coverage depends on blueprint accuracy.

   The explorer now follows `uses_interfaces` hashes recursively, so a caller
   can see a target interface's behavior-source changes through the target hash.
   This still depends on each blueprint declaring its non-code behavior sources
   accurately.

2. Symlink handling under explored directories needs tightening.

   The low-level hash path can represent symlinks as symlink entries, but the
   directory exploration path currently resolves directory children before
   hashing. That can turn a symlink into its target file, miss symlink retargets,
   or accidentally include target bytes outside the intended repository boundary.

3. Raw-dispatch validation needs broader negative coverage.

   Drift tracing only follows declared `DispatchCall` values. The validators
   therefore need to reliably block raw dispatcher usage in runtime code. Current
   coverage should be expanded for alternate import shapes such as importing the
   `dispatcher` module through `officina`, star imports, or dynamic imports.

4. `uses_interfaces` should be validated against direct `DispatchCall` targets.

   `skill-drift` hashes the interfaces declared in `uses_interfaces` and should
   continue treating the blueprint as the contract. A validator should
   route-smoke each Python machine interface, inspect its direct `DispatchCall`
   declarations, and compare those direct targets with the interface's direct
   `uses_interfaces` entries. Recursive closure belongs to hashing; local
   declaration agreement belongs to validators and `skill-audit`.

5. Python tracing should derive `src/officina` from the requested `repo_root`.

   The current tracer uses the module-global `SRC_ROOT` from the live checkout.
   For alternate checkouts or synthetic test repositories, it should use
   `repo_root / "src"` so dependency labels and imports correspond to the repo
   being checked.

Before treating audit results as authoritative, add regression tests for these
items.
