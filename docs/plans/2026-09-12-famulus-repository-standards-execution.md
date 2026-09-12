# Famulus Repository Standards Execution Plan

Status: proposed

## Goal

Determine and implement the right way for Famulus standards to express and
apply relative importance and temporal ordering. The result must make the most
important applicable requirements reliably considered and addressed without
embedding one untested priority model in this plan.

## Problem to investigate

The current format distinguishes normative modalities and can order steps
inside a procedure. It has not yet been established whether it can:

- rank applicable requirements by importance;
- identify requirements that must block progress or completion;
- say when a requirement must be considered;
- order requirements drawn from different imported standards;
- carry diagnostic signals into their required remedies; or
- resolve dependencies and conflicts without relying on YAML position or
  consumer-specific prose.

Establish which gaps are representational, which are query behavior, and which
are consumer behavior before choosing a solution.

## Questions the design must answer

1. What distinct meanings are needed: normative force, importance, execution
   order, dependency, gating, risk, or some smaller combination?
2. What is the atomic ordered object: family, rule, assertion, diagnostic,
   remedy, procedure, or query result?
3. Should ordering be declared by each standard, derived by the query layer,
   supplied by one governing procedure, or divided among those authorities?
4. How should imported requirements compose without silently changing the
   meaning or priority assigned by their canonical owner?
5. How should ties, cycles, conflicts, exceptions, missing metadata, and
   unknown applicability facts behave?
6. Which requirements must prevent mutation, which must prevent completion,
   and which may be reported as unresolved?
7. How can the design remain deterministic and inspectable without requiring
   every consumer to reproduce the ordering logic?
8. Does the change require a standards-schema compatibility revision, and how
   should existing standards migrate?

## Investigation and decision sequence

1. **Characterize current behavior.** Trace schema validation, closure
   extraction, query projection, and every standards consumer. Record exactly
   which ordering is preserved today and where it is lost or interpreted.
2. **Collect failure cases.** Reconstruct the missed
   `monolithic-script` diagnostic-to-remedy traversal and add representative
   cases for conflicting requirements, cross-document ordering, mandatory
   early checks, and important completion conditions.
3. **Define semantics before syntax.** For each case, state what must be
   considered, what may be deferred, what blocks progress or completion, and
   what evidence proves correct handling. Keep normative force separate from
   execution precedence unless the evidence shows they should be unified.
4. **Compare candidate designs.** Evaluate the smallest viable alternatives,
   including existing relationships and procedures, additional standard
   metadata, query-derived ordering, and a governing consumer algorithm. Do not
   select a field, enum, schema shape, or owning component before this
   comparison.
5. **Select the authority boundary.** Choose where each semantic decision is
   owned, how imported documents retain authority, and where deterministic
   ordering is computed. Document rejected alternatives and the concrete
   failure that disqualified each one.
6. **Design conflict and failure behavior.** Specify handling for cycles,
   dangling references, incompatible priorities, unresolved facts, missing
   remedies, and requirements that cannot be satisfied within the approved
   scope. Prefer explicit failure over arbitrary fallback ordering.
7. **Prototype against the cases.** Implement the smallest end-to-end slice
   needed to prove the chosen semantics from canonical standard through query
   output to one consumer. Revise the design if the slice requires duplicated
   policy or loses provenance.
8. **Implement the selected design.** Update only the established authorities,
   query behavior, consumers, documentation, and generated artifacts required
   by the chosen boundary.
9. **Migrate deliberately.** Classify existing standards from their meaning;
   do not assign blanket priorities or ordering merely to satisfy validation.
   Review changes to safety, authorization, correctness, and destructive-action
   requirements first.
10. **Verify and roll out.** Run schema and standards validators, focused query
    and consumer tests, semantic review of representative standards, generated
    artifact checks, and the repository's required gate.

## Decision criteria

Prefer the candidate that:

- makes mandatory and high-importance work impossible to silently omit;
- provides deterministic temporal ordering across a pinned import closure;
- preserves exact document and requirement provenance;
- keeps policy in one canonical authority rather than consumer copies;
- exposes conflicts and unknowns instead of guessing;
- supports focused queries without loading unrelated standard content; and
- adds the least new schema and machinery that satisfies the failure cases.

## Acceptance criteria

The implementation is complete when:

- agreed examples distinguish importance from temporal order where necessary;
- the missed diagnostic-to-remedy case is covered by a failing-then-passing
  regression test;
- cross-document ordering, conflicts, cycles, and unresolved applicability have
  defined and tested outcomes;
- query results expose enough information for consumers to follow the chosen
  order without inventing local precedence;
- every migrated priority or ordering decision has a semantic justification;
- existing standards remain pinned, attributable, and queryable;
- affected consumers use the same governing behavior; and
- focused validation and the agreed repository gate pass.

## Non-goals

- Prescribing the final schema or ordering vocabulary in this plan.
- Treating file order, import depth, or risk level as priority without evidence.
- Rewriting unrelated standard content while migrating execution semantics.
- Adding a general workflow engine when existing schema, query, and consumer
  mechanisms can express the selected design.
