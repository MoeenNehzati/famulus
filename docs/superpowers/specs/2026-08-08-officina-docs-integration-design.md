# Officina Documentation Integration Design

**Date:** 2026-08-08
**Status:** Approved

## Goal

Integrate `worktree-human-audit-doc` into `master`, preserving its relocation of
Officina framework documentation under `docs/officina/` and closing the one
known live reference to the former `docs/skill-blueprints.md` path.

## Authority boundary

The files under `docs/officina/` explain Officina but are non-normative. Canonical
schemas and standards remain authoritative. Historical plans are records of the
paths that existed when they were written and are not rewritten during this
integration.

`references/node-standards/refactoring.standard.yaml` currently mixes a normative
instruction with an informative documentation pointer:

```text
Use a typed enum value from `references/blueprint/schema.json`; see
`docs/skill-blueprints.md` for the architecture overview.
```

The schema reference is sufficient and names the field-level authority. Remove
the documentation clause without replacing it:

```text
Use a typed enum value from `references/blueprint/schema.json`.
```

No compatibility stub remains at `docs/skill-blueprints.md`.

## Standards closure

Treat the sentence change as a canonical standard update. Bump the refactoring
standard revision, recompute its digest in each direct dependent, and propagate
revision and digest changes through the complete pinned import closure. Update
only fixtures or generated evidence whose exact expected text or pins changed.
Do not change `standard_version`, source digests, or unrelated policy.

## Validation

Validate the edited standard and every changed dependent, then run the repository
standard-document checks, documentation contract tests, Markdown-link checks,
preview-generation checks, and diff hygiene. The integration is complete only
when the resulting branch is clean and the validated content is merged into
`master`.

