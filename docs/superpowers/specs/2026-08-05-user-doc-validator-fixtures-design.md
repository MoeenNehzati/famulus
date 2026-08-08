# User Documentation Validator Fixture Design

## Goal

Reduce `repo/user_docs_cover_blueprints` runtime without changing its
conformance rules. The validator currently constructs and validates the full
skill catalog five times: once for domain coverage and once for each of four
generated coverage blocks. The refactor must construct that immutable catalog
once per validator pytest session and reuse it across independently reported
checks.

## Collector contract

The repository validator collector will support two backward-compatible module
shapes:

1. Existing validator modules expose `validate(...)` and produce one pytest
   item.
2. Refactored validator modules expose `test_*` functions and ordinary pytest
   fixtures. Pytest performs normal module collection, fixture registration,
   parametrization, and fixture-scope management for those items.

Every collected item retains the module's canonical validator ID and the
validator marker. Findings from multiple items are appended under that ID in
collection order. Existing validators require no changes.

The collector-provided `repo_root` fixture becomes session-scoped. It is an
immutable path to the captured staged mirror, so broader scope does not alter
repository isolation and permits module- or session-scoped validator fixtures
to depend on it.

## Validator structure

`validators/user_docs_cover_blueprints.py` will define a module-scoped
`skill_catalog` fixture that calls `load_catalog(repo_root)` exactly once. Four
pytest items consume it:

- one domain-coverage check;
- one parametrized generated-block check for each of the three user documents.

The checks call pure helper functions that return `list[str]`. The existing
`validate(repo_root)` function remains as a direct-call compatibility wrapper;
it prepares one catalog and runs the same helpers in the same deterministic
order.

## Rendering boundary

Documentation rendering functions will accept an already prepared catalog.
The top-level rendering API may prepare a catalog when a caller does not supply
one, but nested coverage-block rendering must never reload it. Consequently,
one top-level document render performs at most one catalog construction, and
the fixture-backed validator performs one construction for all three documents.

## Failure behavior

- Catalog or schema-loading failures remain validator execution failures.
- Missing documentation files and stale generated blocks retain their existing
  messages.
- Multiple pytest items aggregate findings under
  `repo/user_docs_cover_blueprints`; one successful item must not erase an
  earlier item's findings.
- The staged mirror remains the only repository view visible to fixtures and
  validator items.

## Verification

Tests will be added before production changes and must demonstrate:

1. module-local fixtures are discovered and a module-scoped fixture executes
   once across multiple validator items;
2. findings from multiple items aggregate under one canonical validator ID;
3. legacy single-`validate()` modules still execute unchanged;
4. direct `validate(repo_root)` behavior remains equivalent;
5. the user-doc validator calls `load_catalog` once in both pytest-collected and
   direct compatibility paths;
6. generated output and diagnostics remain unchanged.

The validator-only wall time will be measured before and after using the same
root entry point and validator selection. The observed baseline on this branch
is 9.78 seconds.
