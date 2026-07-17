# Standard v6 Conversion Assessment

## Verdict

**Assessment: substantively faithful and structurally well positioned, but not
repository-gate clean.** The focused conversion suite, direct validation, and
staged index-mirror validator support accepting the three canonical standards
and their generated views. The full pre-commit suite remains non-green for
typed-health, audit, and schema-snapshot failures outside the migration's
changed implementation.

This assessment includes two validator hardening fixes and their regression
tests. With user authorization, the migration paths were staged and the
accidental hook typo was removed. No commit was created, and the unrelated
certification documents remain unstaged and unmodified by this migration.

## Evidence reviewed

- Design and plan: `docs/superpowers/specs/2026-07-16-standard-v6-migration-design.md`
  and Task 5 of `docs/superpowers/plans/2026-07-16-standard-v6-migration.md`.
- Task reports: `/tmp/task2-skill-refactoring-report.md`,
  `/tmp/task3-guidelines-profile-report.md`, and
  `/tmp/task4-consumer-migration-report.md`.
- Immutable originals and conversion maps under `tests/fixtures/standards/`.
- The three canonical YAML files, their three generated Markdown views, the v6
  schema, validator, renderer, focused tests, repository validator, consumer
  changes, and current Git diff.

## Conversion quality

### Information preservation and item placement

**Verified.** The final migration-focused tests passed all 76 cases. They pin each immutable
source by SHA-256 and exhaustively map every nonblank source unit. Guideline and
document-profile semantic blocks are checked against exact canonical field
values; the guideline blocks remain verbatim. Skill-smell and refactoring units
are checked against exact fields or exact `remedied-by` target sets, including
mutation tests for procedure steps in all three risk families.

The four old documents are placed coherently in three standards:

- skill guidelines remain an independently versioned repository policy;
- skill smells and the refactoring catalog are combined, but diagnosis and
  remediation remain separate under `diagnostic-signals` and
  `refactoring-moves`;
- document-profile material remains independent because it governs research
  document metadata rather than skill refactoring.

The conversion deliberately combines the two refactoring sources, splits some
procedural lines across typed summary/step/invariant/risk fields, and corrects
the accidental phrase `and Move it` to `and move it`. It also adds explicit
procedures for the source-preserving moves `Add/fix blueprint` and `Inline to
Reference`, which had labels in the smell source but no catalog bodies.

Eight refactoring-map units are contextual rather than one-to-one semantic
fields: two document titles, the obsolete catalog-reference framing, and five
Markdown separators. Guideline and document-profile separators/fences are also
classified explicitly by their source maps. This is documented conversion
normalization, not silent omission. Migration-only provenance is retained in
immutable fixtures and source maps rather than in the canonical standards.

### Smell-remedy completeness

**Verified.** The canonical refactoring standard contains 19 typed
`remedied-by` links. The focused tests require the full expected smell set,
remedy set, signals, analogs, procedure content, risk levels, and exact remedy
target sets. Direct validation also enforces that each link originates beneath
`diagnostic-signals` and terminates at a family or procedure beneath
`refactoring-moves`. The generated view renders the inverse relationship as
`Addresses`, so readers can navigate from remedies back to diagnosed smells.

The relationship means “applicable way to address,” not “mandatory or
sufficient”; neither schema nor prose overstates that guarantee.

### Human readability

**Verified with a size limitation.** All views begin with a generated-file
warning and canonical source path. The refactoring view presents familiar smell
headings, signals, analogs, ordered remedies, risks, `Remedies`, and `Addresses`.
The document-profile view is a compact 89 lines with fields, TeX template,
usage rules, and normalization examples.

The 947-line guideline view intentionally uses `source-faithful` rendering. It
preserves original headings, prose, lists, and fenced examples without
synthetic `Requirement NNN` headings; tests also assert stable section order and
a bounded size increase. It remains long because the 799-line source was long.
The migration improves navigation and canonical structure, but does not make
the underlying policy concise.

### Enforcement linkage

**Verified for inventory and repository integration; not every statement has a
direct assurance.** The guideline conversion contains a typed artifact
inventory covering validators, tests, schemas, the validator runner, the
pre-commit entrypoint, libraries, generated artifacts, and semantic-review
instructions. Its test ledger requires every source path reference to be
classified and specifically classifies `.githooks/pre-commit` as a
`validation-entrypoint`, rather than manufacturing a direct assertion-level
assurance.

The new `validators/standard_documents.py` has a fail-closed allowlist of
exactly three canonical standards, invokes the repository-local validator, and
requires each document's `canonical_path` to equal its actual allowlisted
repository-relative path. This prevents two otherwise valid standards from
being swapped between allowlisted locations. It also checks each generated view
byte-for-byte against the renderer. Current LLM
consumers point to generated Markdown; the skill-drift policy root points to
canonical guideline YAML. Direct working-tree validation confirms the linkage
works against the live artifacts. Repository-runner integration is not yet
proved in the current unstaged state, as detailed below.

### Schema complexity and debuggability

**Qualified concern.** There is one shared schema, one semantic validator, and
one renderer, matching the design's simplicity boundary. The focused suite
covers structural and semantic failures, `remedied-by` restrictions,
determinism/freshness, fidelity, and missing/unexpected standards.

The implementation is nevertheless substantial: the schema is 3,999 lines,
the semantic validator 282 lines, and the renderer 180 lines. The schema's
closed objects and typed references produce useful fail-closed behavior, but
the validator is densely formatted with many compound one-line statements.
That density raises maintenance and debugging cost, especially for ancestry,
import, authority, and semantic-review failures. Final review found that
`validate_file` previously accepted absolute artifact paths and could follow
resolved paths outside the supplied repository root. One shared resolver now
rejects absolute paths, lexical escapes, and symlink escapes for imports,
sources, schema authorities, and semantic-review instructions. Regression tests
cover an in-root pass, absolute and `..` import escapes, and a source symlink
escape. Focused passing tests still do not prove every branch of a 3,999-line
schema or every semantic combination.

### Atomicity debt

**Explicit residual debt.** The source-faithful guideline YAML contains 86
`atomicity: pending` markers. They preserve multi-claim normative source blocks
verbatim instead of silently splitting or paraphrasing them. This is an honest
fidelity choice, but those blocks are not yet independently addressable for
claim-level assurance, exceptions, or semantic review. Future splitting needs
reviewed mappings that preserve frozen IDs and exact source meaning.

### Migration reference state

**Verified for deleted source names.** A repository search found no active
consumer of `skill-smells.md`, `skill-refactoring-catalog.md`, or
`document-profile-schema.md`. Remaining occurrences are confined to immutable
fixtures/fidelity tests and the historical migration plan. Live skill and
blueprint consumers use generated views; guideline maintenance and policy
hashing identify canonical YAML where authority matters.

Some current plans still instruct future authors to modify
`references/skill-standards/skill-guidelines.md` directly. Those are not stale
references to deleted files, but they conflict with the new generated-view
ownership rule and are residual authoring-instruction debt. The occurrence in
`docs/plans/migrate_audit_to_certification.md` is part of a user-owned unrelated
edit and was not changed here.

## Fresh verification

Run from the repository root:

| Command | Outcome | What it proves or does not prove |
| --- | --- | --- |
| `pytest -q tests/test_standard_v6.py tests/test_skill_refactoring_standard.py tests/test_migrated_standards_fidelity.py tests/validate_standard_documents.py tests/validate_platform_neutral.py tests/validate_dispatcher_usage.py` | PASS: `76 passed in 5.96s` | Schema, semantic, rendering, artifact containment, canonical identity, fidelity, mapping, repository-validator behavior, and affected platform/dispatcher validation pass. |
| `pytest -q skills/skill-drift/tests/test_drift_check.py -k 'policy_hash_roots or policy_hash'` | PASS: `4 passed, 61 deselected in 0.08s` | The policy-hash migration to canonical guideline YAML passes its focused skill-drift coverage. |
| Full `skills/skill-drift/tests/test_drift_check.py` included in a broader targeted run | FAIL: `25 failed, 116 passed` | The failures are in pre-existing typed-health/schema behavior (`binding`, `blueprint_type`, and missing typed schema snapshots), not the four policy-hash tests changed by this migration. The repository-wide suite is therefore not green. |
| `references/standards/validate_standard_v6.py --root . references/skill-standards/skill-guidelines.standard.yaml` | PASS: `validation passed` | Direct live guideline validation. |
| `references/standards/validate_standard_v6.py --root . references/skill-standards/skill-refactoring.standard.yaml` | PASS: `validation passed` | Direct live refactoring validation. |
| `references/standards/validate_standard_v6.py --root . references/document-standards/document-profile.standard.yaml` | PASS: `validation passed` | Direct live document-profile validation. |
| `python3 validators/runner.py` | PASS, exit 0 | The staged index mirror includes and successfully runs the new fail-closed standards validator. |
| `bash .githooks/skill/check-blueprints` | PASS, exit 0 with clean output | Blueprint validation passes after removal of the authorized accidental hook typo. |
| `git commit -m "feat: migrate reference standards to v6"` | BLOCKED by pre-commit: `133 failed, 1170 passed, 1 skipped` | The failures are concentrated in existing artifact-health, audit, pooled-blueprint, and skill-drift typed-schema behavior. No commit was created. |
| `git diff --cached --check` | PASS, no output | The complete staged migration has no whitespace errors. |

## Exact limitations and residual risks

1. User-owned unrelated changes in `docs/certification_and_drift.md` and
   `docs/plans/migrate_audit_to_certification.md` were not modified. The latter
   contains one residual instruction to modify the generated guideline view.
2. Fidelity tests prove preserved mapped meaning and representative mutation
   detection; they do not prove that every future consumer interprets the new
   typed structure identically to every interpretation of the old prose.
3. The 86 pending atomicity markers and the guideline view's length are
   acknowledged conversion debt, not completed normalization.
4. The broader skill-drift slice has 25 typed-health/schema failures, and the
   full pre-commit suite has 133 failures across the same typed-health/audit/
   schema subsystem. The four policy-hash tests changed here pass, but the
   repository-wide commit gate is not green.
5. No run result is stored in a standard. This report records point-in-time
   evidence only.
