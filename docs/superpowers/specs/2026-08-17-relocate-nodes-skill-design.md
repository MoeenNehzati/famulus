# Relocate Nodes Skill Design

## Goal

Create a narrow `relocate-nodes` skill around the existing manifest-driven,
blueprint-aware relocation engine. Move the engine and its schema from
`src/officina/refactor/` into the skill's private `_rtx` module, update every
caller to the canonical dispatcher address, and remove the obsolete standalone
script and historical relocation manifest.

This change preserves the relocation engine's behavior. It does not add
certificate carry-forward, certification, installation, automatic move
inference, or compatibility facades.

## Ownership and layout

The instruction module owns discovery and the human/agent workflow:

```text
skills/relocate-nodes/
  SKILL.md
  blueprint.yaml
  blueprints/gateway.yaml
  _rtx/
    __init__.py
    blueprint.yaml
    blueprints/rtx-relocate-nodes.yaml
    _relocate_nodes.py
    _relocation_engine.py
    _relocation_closure.py
    schemas/relocation.schema.json
    tests/
```

`_rtx/__init__.py` is documentation-only and describes the purpose of every
runtime file; it exports no Python facade. The registered runtime source owns
the dispatcher adapter. `_relocation_engine.py` retains planning, validation,
reporting, and atomic publication. `_relocation_closure.py` retains shadow-tree
synchronization and graph validation. The parser resolves its JSON schema from
the registered `schemas/` child-artifact directory.

The migration removes:

- `src/officina/refactor/`
- `scripts/relocate_officina_sources.py`
- `refactors/officina-source-relocation.yaml`
- the empty `refactors/` directory, if no other files exist

The final historical manifest is saved temporarily before removal. Its two
spent rewrites targeting the deleted bootstrap acceptance test are removed in
a separate temporary final-check fixture; that narrow fixture verifies the
empty postflight without weakening the relocation engine or restoring the
deleted test.

## Interfaces and workflow

The parent exposes one instruction interface, `relocate-nodes.interface.default`.
The private child exposes one machine interface,
`relocate-nodes._rtx.interface.relocate`, used by the parent gateway through the
declared namespace route. No import facade is retained under `officina`.

The machine interface retains the current flags:

- `--root REPOSITORY`
- `--manifest MANIFEST`
- `--report REPORT`
- `--apply`

`--manifest` becomes required because the completed repository manifest is no
longer retained as an implicit default. The other flag semantics are unchanged.

Preflight remains the default. It loads the strict manifest, computes the full
in-memory change set, closes generated blueprint and certification-basis
artifacts in a shadow repository, validates the canonical blueprint graph, and
emits the existing structured JSON report. `--apply` publishes the already
validated change set atomically.

The runtime dispatches shadow synchronization through the authorized
`skill-maker._rtx.interface.sync-blueprints` interface and consumes
`blueprints.interface.graph` for validation. It never invokes another skill's
private runtime file by path.

The skill instructions require an exact manifest and a reviewed preflight
before apply. They report moves, writes, deletes, unresolved references,
blueprint changes, generated artifacts, certification-basis changes, digest
changes, and validation results. They never invoke certification.

Future relocations may use a temporary or caller-selected manifest. The
repository does not retain completed manifests as historical plans.

## Failure behavior

The interface fails closed with exit status `2` for invalid manifests, unsafe
paths or symlinks, ambiguous moves, unresolved references, incomplete package
dispositions, unexpected synchronizer writes, graph failures, or atomic publish
failures. A failed preflight performs no repository writes. Apply does not begin
until the entire projected change set and closure are valid.

## Migration mechanics

Use the relocation engine itself for the code move and address rewrites. The
bootstrap manifest must include the runtime files, schema, tests, blueprints,
certification-basis changes, dispatcher references, documentation references,
and deletion of the old script/package addresses. Apply once, then run the new
dispatcher interface against the migrated tree and require an empty preflight.

All Python imports and process invocations must use the new private module or
dispatcher address directly. No backwards-compatible module, re-export, alias,
or wrapper remains.

## Verification

Acceptance requires:

1. The skill and runtime blueprints satisfy the current instruction- and
   Python-module/source standards.
2. Existing relocation behavior tests pass from the skill-owned test location.
3. A dispatcher-level test proves default preflight, structured reporting,
   apply, and a subsequent empty preflight.
4. Repository search finds no live `officina.refactor`, old script, or historical
   manifest references.
5. Blueprint synchronization check and repository graph loading pass.
6. The focused relocation suite and normal repository commit hook pass.
7. The final feature worktree is clean.

## Deferred work

Certificate carry-forward for proven path-only relocations is explicitly
deferred. It will require a separate design that keeps signing authority in
`skill-certifier` and proves predecessor/current graph equivalence.
