# Standard v6 Migration Design

## Goal

Adopt the revised v6 standards format in the repository, add an explicit
smell-to-remedy relationship, and replace four hand-authored Markdown sources
with three canonical standards without reducing their usefulness to LLM and
human consumers.

## Canonical standards

The migration produces three independently versioned standards:

1. `references/skill-standards/skill-guidelines.standard.yaml`
2. `references/skill-standards/skill-refactoring.standard.yaml`
3. `references/document-standards/document-profile.standard.yaml`

`skill-refactoring.standard.yaml` combines the current skill-smell taxonomy and
refactoring catalog. It retains separate `diagnostic-signals` and
`refactoring-moves` families so diagnosis and remediation remain distinct and
readable.

The skill guidelines remain separate because they are repository-wide
conformance policy with independent validators, policy hashing, and consumers.
The document-profile standard remains separate because it governs research
document metadata and has no skill-refactoring ownership relationship.

`references/skill-standards/llm-interface-design.md` is outside this migration.

## Smell-to-remedy relationship

The v6 schema gains the link relation `remedied-by`. Its source is a diagnostic
smell and its target is a refactoring family or procedure. The relationship
means that applying the target is an applicable way to address the source; it
does not claim that the remedy is mandatory or universally sufficient.

Canonical direction:

```yaml
source:
  kind: family
  ref: skill-refactoring.smells.bloated-skill
relation: remedied-by
target:
  kind: procedure
  ref: skill-refactoring.remedies.extract-script
```

The renderer presents the forward relationship as `Remedies` and the reverse
relationship as `Addresses`. Existing generic `recommends` links remain valid
for recommendations that are not diagnostic-remedy mappings.

The validator rejects `remedied-by` links unless the source resolves to the
diagnostic-signals family and the target resolves to the refactoring-moves
family. This semantic restriction is enforced by the validator because JSON
Schema cannot resolve arbitrary item references and inspect their ancestry.

## Canonical data and rendered views

The `.standard.yaml` files are authoritative. A shared renderer generates
human-readable Markdown views for current LLM and documentation consumers.
Generated views are not independently editable and identify their canonical
source at the top.

This preserves the current behavior of `@` includes and explicit instructions
to read Markdown while allowing standards tooling and validation to operate on
structured data.

## Migration behavior

Each source is converted with stable semantic IDs and audited against every
nonblank source unit before the source is removed. Conversion-only provenance
may be retained during fidelity review, but it is removed from the canonical
artifact once the conversion is accepted.

The migration updates live consumers, blueprint behavior sources, policy-hash
roots, validators, tests, and current documentation. Historical completed or
archived plans remain historical unless a broken live link requires a minimal
annotation.

The old filenames are deleted only after a repository-wide zero-reference
check and after generated replacements exist for all active Markdown consumers.

## Validation and tests

Tests cover:

- structural and semantic validation of all three standards;
- valid and invalid `remedied-by` relationships;
- complete smell-to-remedy mappings;
- deterministic Markdown rendering;
- source-to-standard fidelity before source deletion;
- absence of active references to deleted paths;
- existing blueprint, skill, and repository validation paths.

No test or review outcome is stored inside a standard. Standards only identify
relevant checks, tests, and semantic-review instructions.

## Simplicity constraints

- Maintain one schema, validator, and renderer implementation.
- Do not introduce a general documentation-generation framework.
- Do not migrate `llm-interface-design.md` or supporting Blueprint artifacts.
- Do not add relation types beyond `remedied-by` unless another migrated
  standard demonstrates a concrete need.
