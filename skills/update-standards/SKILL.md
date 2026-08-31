---
name: update-standards
description: >-
  Use when the user asks to create, change, or audit a canonical repository standard. Do not use for generic policy or documentation work.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Used Interfaces: none
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: update-standards

The schema is the field-level authority. Resolve only definitions for fields being
changed; do not load the whole schema, restate it here, or infer fields from older
examples.

## Maintenance workflow

1. Identify the canonical target, its pinned import closure, every direct dependent,
   and whether the repository registers a generated view for it.
2. Read the target, relevant schema definitions, and validator output. Change the
   smallest semantic unit that expresses the requested policy.
3. Keep the operational relationships honest:
   - each violated requirement has an applicable remedy;
   - checks, tests, assurances, reviews, and evidence claims name only mechanisms that
     actually exist and state their limitations;
   - removed or superseded policy leaves no stale links or enforcement.
4. Bump the edited document's revision. Recompute its digest in each direct dependent,
   update the pinned revision, and repeat outward until the import closure is current.
   Change `standard_version` only for a compatibility change.
5. Update source and source-unit digests only when their referenced evidence changed.
   Never change a digest merely to silence validation without inspecting the source.
6. Regenerate registered Markdown views from their YAML authority; never edit a
   generated view as policy. Standards without a registered view remain YAML-only.
7. Run focused validation for every edited document, then the repository standards
   validator and tests for changed enforcement. Inspect the exact diff before claiming
   alignment.

Stop rather than inventing policy when ownership, the intended compatibility boundary,
the correct remedy, or the truth of an evidence claim is unresolved.

## Report

Report semantic changes, revision/digest cascades, regenerated views, evidence or
enforcement changes, validation results, and any unresolved semantic-review remainder.
