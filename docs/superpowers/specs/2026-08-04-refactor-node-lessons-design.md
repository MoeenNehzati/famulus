# Refactor-Node Lessons Design

## Goal

Turn the repeated review failures from the recent refactor passes into a smaller,
more reliable refactoring workflow without copying leaf-standard policy into the
canonical refactoring standard.

## Diagnosis

The imported standards already define behavior, ownership, authorization,
dependency, evidence, and route-specific preservation requirements. The reusable
gap is operational: the shared refactoring rule says only to characterize first,
and the router does not require a concrete preservation analysis before selecting
context, evidence, and remedies.

The observed failures cluster around four omissions:

1. Content was shortened without identifying whether each fact belonged to
   authored guidance, generated metadata, or another canonical owner.
2. Branch-specific outcomes were compressed into false universal statements.
3. Target-local review missed producer, consumer, authorization, and reverse-test
   edges.
4. Machine-observable metadata and structurally invalid test fixtures were treated
   as implementation details.

## Design

1. Strengthen the existing `characterize-first` assertion. Require a compact
   preservation map covering the selected behavior and its affected ownership,
   dependency, authorization, and verification edges. Do not add a new family or
   repeat leaf-standard checklists.
2. Reorder the shared router into five stages:
   - resolve requirements and material unknowns;
   - characterize the actual selected scope;
   - select exact decision-relevant standard references;
   - fetch context, evidence, and remedies only for those references;
   - propose, approve, mutate one move, and verify.
3. Put instruction-specific interpretation in the instruction route: distinguish
   authored from generated authority, account for removed directives, preserve
   branch/outcome distinctions, and trace cross-module producer-to-consumer paths
   through authorization and integration evidence.
4. Put Python-specific interpretation in the Python route: inventory branches and
   outcomes, machine-observable identifiers and dry-run plans, ordering and effects,
   callers, authorization, and reverse integration tests.
5. Test judgment with pressure scenarios and explicit rubrics. Use executable tests
   only for machine-checkable contracts; do not encode prose-token tests or claim
   that pytest proves LLM reasoning.
6. Keep the existing query engine. Exact-reference extraction already supports the
   required narrowing.
7. Treat validator success as a finalization gate. A changed skill may be inspected
   while diagnosing a failure, but it is never final output: fix failures within the
   approved move and rerun, or revert and stop when repair requires new scope.

## Constraints

- Preserve public interfaces, query shapes, ownership boundaries, and mutation
  approval behavior.
- Keep the standard change to two strengthened existing assertions—preservation
  mapping and validator finalization—plus one wording compression and the required
  revision and pinned-digest cascade.
- Prefer reorganization and removal of redundant wording over adding parallel rules.
- Do not generalize instruction-only lessons into universal Python policy.

## Verification

- Run baseline pressure scenarios against the unmodified routes and record concrete
  omissions.
- Repeat the scenarios against the revised routes and score them against the same
  preservation rubric.
- Run focused refactor-node routing/query tests, standards validation, blueprint
  synchronization, repository precommit checks, and `git diff --check`.
- Do not hand off or consume the refactored skill as final unless every relevant
  returned validator passes.
- Obtain a fresh read-only audit against this design and the cited failure reports
  before committing.

## Pressure Evidence

The real refactor rounds supplied the discriminating RED cases: they missed
authored-versus-generated authority, branch outcomes, cross-owner authorization
and tests, observable pattern metadata, exact test identity, and schema-valid
fixtures. Two fresh unmodified-skill controls were too explicit to reproduce those
misses: the instruction scenario covered all seven rubric items and the Python
scenario covered all six. They therefore confirm capability, not improvement, and
did not justify broader rules.

The revised-skill reviews used these fixed rubrics:

- Instruction: affected-only map; removed-directive authority; branch outcomes;
  producer-to-consumer authorization and both-owner evidence; observable generated
  metadata; non-prose and schema-valid evidence; exact-ref retrieval after
  characterization.
- Python: affected-only map; branch observables; machine identifiers or dry-run
  plans; callers, authorization, and reverse evidence; canonical structural
  fixtures; exact-ref retrieval after characterization.

The revised instruction route scored 7/7 and the revised Python route 6/6. Both
reviewers then audited information density: instruction wording was narrowed to
affected directives, and Python wording was reduced to the three nonredundant
additions—machine identifiers/dry-run plans, canonical fixture validity, and
focused/reverse evidence.

## Development Evidence

The reliable RED evidence is the recorded review history from the real refactor
passes: missing cross-owner authorization and reverse tests, authored/generated
authority mistakes, branch overgeneralization, observable pattern renaming, and
invalid structural fixtures. Two fresh synthetic controls named these hazards in
their prompts and therefore covered them without new guidance; they were too
leading to justify additional rules. The implementation is limited to the repeated
historical omissions and the strengthened assertion's failing-first test.
