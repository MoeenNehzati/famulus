# Blueprint-aware source relocation tool

**Status:** Implemented and verified
**Date:** 2026-08-16

## Objective

Provide one reusable mechanical tool for moving sources within or between
Officina modules. A declarative manifest states intended relocations and public
identity changes. The tool updates files, repository references, and blueprint
ownership metadata together, then rejects incomplete migrations.

The current `src/officina` reorganization is the acceptance case. The tool must
perform that relocation in one invocation without compatibility facades or
implementation-body refactoring.

## Interface

The command accepts:

- a repository root;
- a YAML relocation manifest;
- read-only preflight by default;
- `--apply` for one real mutation pass.

The manifest declares:

- file and directory moves;
- old-to-new dotted, filesystem, source, and interface identities;
- registered-module ownership transfers;
- caller-authorization additions required by transferred dependencies;
- packages whose `__init__.py` is a README-only catalog;
- exceptional exact rewrites that cannot be inferred from an address token.

Declarations are typed. Filesystem paths, Python module addresses, behavioral
source IDs, and exported interface IDs are distinct values. One typed rename
generates every unambiguous textual variant; the manifest does not repeat those
derived spellings.

## Blueprint behavior

For each ownership transfer, the engine:

1. removes transferred content, sources, and exports from the old module;
2. creates or updates the target module blueprint;
3. moves and retargets behavioral-source sidecars;
4. rewrites source IDs, exported interface IDs, source-interface links, and
   `uses_interfaces` references;
5. adds only caller permissions explicitly declared by the manifest;
6. preserves contracts, versions, dependencies, and implementation bodies.

The engine does not invent exports, callers, dependencies, or module authority.
The manifest must state any identity or authorization change that cannot be
derived unambiguously.

Ownership transfer is a first-class operation rather than a collection of raw
YAML patches. A transfer names the old module, target module, source IDs,
sidecars, exports, and explicitly authorized callers. The engine derives the
corresponding `content`, `sources`, `exports`, and `uses_interfaces` mutations.

## Package documentation

For each declared package, the engine generates a README-only `__init__.py`
docstring. It summarizes the package and lists every directly owned file or
subpackage with its relevance. Executable package initializers must be declared
as exceptions and receive only a docstring replacement.

No initializer re-exports implementation symbols. Repository callers import
the concrete owning module.

## Safety and validation

Preflight fails before mutation when:

- both or neither endpoints of a required move exist;
- a declared module/source/interface is missing or duplicated;
- a blueprint ownership transfer is ambiguous;
- an exact rewrite precondition is absent;
- a target path escapes the repository.

After application, validation requires:

- every target exists and every old path is absent;
- no active text contains a retired address;
- Python sources parse;
- declared README-only initializers contain only their docstring;
- no Python caller imports through those package initializers;
- registered module and behavioral-source blueprints resolve;
- pinned standard-import digests match their referenced documents.

Application runs through a temporary repository copy first. The real worktree
is mutated only after that acceptance run succeeds.

The engine builds the entire change set in memory before writing. It validates
move endpoints, exact-rewrite preconditions, blueprint identities, generated
initializers, and retired-address closure against that projected tree. Failure
therefore leaves the repository unchanged; a successful `--apply` publishes the
validated change set atomically at file granularity.

Preflight and application emit the same machine-readable report containing all
planned moves, inferred blueprint mutations, exceptional rewrites, generated
package catalogs, refreshed digests, and unresolved references. Human-readable
output is only a rendering of this report.

## Scope limits

- Mechanical relocation only; no implementation decomposition or behavior
  changes.
- No compatibility modules, aliases, or import facades.
- No deletion of useful source or long-form documentation.
- No staging, committing, pushing, or changing unrelated dirty files.
- Historical migration specifications may retain old addresses and are excluded
  from active-address closure checks.
- No automatic architecture inference, function or class renaming, arbitrary
  AST refactoring, compatibility generation, Git workflow, or plugin framework.

## Acceptance case

The first manifest covers the approved `src/officina` reorganization, including
the partially completed moves already present in the worktree. Passing means:

1. the manifest preflight succeeds against the current mixed old/new state;
2. applying it to a temporary copy succeeds;
3. focused standards, visualization, repository-check, validator, and blueprint
   tests pass in that copy;
4. each audited manifest change set is applied to the real worktree;
5. focused and repository-supported verification passes afterward.

The reusable within-module case was also exercised by moving the standards
extractor and its behavioral-source sidecar in a disposable repository copy.
The tool adjusted dependent imports and addresses, 15 focused standards tests
passed, and the second preflight reported no remaining changes.
