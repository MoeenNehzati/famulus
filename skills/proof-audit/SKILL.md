---
name: proof-audit
description: |
  Audit a mathematical proof, argument, lemma, proposition, or theorem statement for rigor, coherence, and redundancy.

  Use when:
  - the user asks to check, verify, audit, or validate a proof or proof sketch
  - the task is to find hidden assumptions, invalid theorem use, quantifier or domain mistakes, corner cases, missing hypotheses, or whether a claimed result follows from stated assumptions
  - the user wants diagnosis before rewriting or repair
  - the user wants to know whether a proof is coherent, relevant to its conclusion, or needlessly overcomplicated

  Do not use when:
  - the user mainly wants brainstorming, LaTeX polishing, notation cleanup, a theorem or proof block written from scratch, or code, computation, or experiments instead of proof verification

  Success criteria:
  - identify the soundness, coherence, and redundancy status of the argument
  - decompose the proof and distinguish component-level from assembly-level issues
  - check standing assumptions and earlier local results before invoking external theorems
  - isolate the first real gap or main blocking issue
  - after checking soundness, run separate passes for coherence and redundancy
  - flag unused branches, repeated claims, irrelevant detours, over-strong intermediate claims, or heavier-than-needed methods when they are clearly present
  - suggest a candidate repair direction without fully rewriting unless asked, and give a counterexample when feasible
---

When this skill is used, begin with:

Skill: proof-audit

## 1. Goal

Your job is not to sound convincing. Your job is to determine whether the argument is sound in the current setting, whether it is coherent, and whether it contains avoidable redundancy.

Take a referee-style view:
- first check soundness
- then check coherence
- then check redundancy

Do not treat these as the same question.
- A proof can be sound but hard to follow.
- A proof can be sound and coherent but still redundant.
- A proof can fail soundness even if its overall structure is coherent.

Prefer:
- `I don't know`
- `this needs verification`
- `this step uses an unchecked hypothesis`
- `this part appears unnecessary`
- `this detour does not feed the conclusion`

over confident but unverified claims.

Do not invent theorems, references, or standard facts.
Do not treat stylistic preferences as mathematical defects.
But do flag clear structural excess when it matters.

## 2. Output format

Start with:

- `Mode: Proof`
- `Skill: proof-audit`
- `Soundness: ...`
- `Coherence: ...`
- `Redundancy: ...`

For `Soundness`, use one of:
- `Soundness: Verified`
- `Soundness: Gap`
- `Soundness: Needs hypothesis`
- `Soundness: Not verified`
- `Soundness: I don't know`

For `Coherence`, use one of:
- `Coherence: Coherent`
- `Coherence: Mostly coherent`
- `Coherence: Needs restructuring`
- `Coherence: Hard to follow`
- `Coherence: Not assessed`

For `Redundancy`, use one of:
- `Redundancy: Lean`
- `Redundancy: Minor redundancy`
- `Redundancy: Redundant`
- `Redundancy: Substantially redundant`
- `Redundancy: Not assessed`

If the proof is nontrivial, also include:
- `Decomposition`
- `Soundness audit`
- `Coherence audit`
- `Redundancy audit`

Then include only the needed sections, such as:
- `What checks out`
- `Gap`
- `Missing hypothesis`
- `Scope issue`
- `Corner case`
- `Counterexample`
- `Why the step fails`
- `Candidate fix`
- `Structure issue`
- `Missing signposts`
- `Assembly issue`
- `Order issue`
- `Hard to follow transition`
- `Repeated step`
- `Unused branch`
- `Unused strength`
- `Irrelevant detour`
- `Heavier-than-needed method`
- `Possible simplification`

If the user asked a yes/no question, answer that first.

Keep the answer concise.

If soundness fails very early, it is acceptable to use:
- `Coherence: Not assessed`
- `Redundancy: Not assessed`

unless an obvious structural or redundancy issue is still worth flagging briefly.

## 3. Context first

Treat the current document and its earlier results as the primary mathematical context.

Do not audit a theorem, lemma, or proof in isolation if the surrounding document may contain standing assumptions, notation, or scope restrictions.

Before flagging a missing hypothesis, check whether it already appears earlier in the paper or notes, for example in:
- standing assumptions
- setup sections
- notation sections
- introduction or model sections
- subsection-level conventions
- phrases like `throughout`, `in this section`, `we assume`, `fix`, or `let ... be`

When auditing a claim:

1. Identify the local assumptions in the theorem or proof.
2. Identify any standing assumptions from earlier in the document that remain in force.
3. Distinguish between:
   - genuinely missing assumptions
   - assumptions present globally but omitted locally
   - assumptions whose scope is unclear
4. If a needed assumption appears earlier but its scope is unclear, flag a `Scope issue`, not immediately a mathematical gap.
5. When reporting a problem, say whether the fix is:
   - add the assumption locally
   - remind the reader that it is standing
   - clarify the scope

## 4. Decompose the proof

Do not audit a substantial proof as one undifferentiated block.

When the proof is nontrivial:

1. Decompose it into components.
2. Show the decomposition explicitly.
3. For each component, state:
   - what it is trying to prove
   - what assumptions it uses
   - whether it is used in the final assembly
   - its soundness status
4. Audit each component independently.
5. Then audit the logical connections between components.

Possible components include:
- setup and standing assumptions
- reduction step
- existence step
- uniqueness step
- compactness / closure / continuity step
- transversality / genericity step
- boundary exclusion step
- local argument
- global upgrade step
- final assembly step

Check in particular that:
- each component actually contributes to the conclusion
- later components do not use stronger conclusions than earlier ones proved
- local conclusions are not silently promoted to global ones
- existence, discreteness, closedness, compactness, and finiteness are combined correctly
- a boundary or exclusion step is not silently assumed from an interior argument
- theorem hypotheses remain valid when passing from one component to the next

A proof may have plausible components but still fail at assembly.

## 5. Pass 1: Soundness

Treat every nontrivial step as requiring justification.

In the soundness pass, check explicitly for:
- hidden assumptions
- quantifier mistakes
- domain/codomain mismatches
- boundary vs. interior confusion
- local vs. global slippage
- finite-dimensional vs. infinite-dimensional slippage
- prevalence / genericity / density / residual / almost-everywhere slippage
- existence claims that were not proved
- uniqueness claims that only have local uniqueness
- closure/discreteness/compactness steps that need separate justification
- circular reasoning
- confusion between premises and conclusions
- theorem invocations whose hypotheses were not checked

Phrases like
- `clearly`
- `obviously`
- `standard`
- `by transversality`
- `by Whitney extension`
- `by Sard`
- `by compactness`

are not automatic errors. Treat them as audit triggers.

When such a phrase appears:

1. Identify the exact claim being justified.
2. Check whether it is actually straightforward here.
3. If it is straightforward, provide the missing rigorous justification concisely.
4. If it depends on a theorem, check the hypotheses explicitly.
5. If the justification is not immediate or the hypotheses are unclear, flag it.

## 6. Prefer local results before external theorems

When a step relies on a theorem, lemma, proposition, corollary, remark, or earlier fact:

1. State the exact claim needed at that step.
2. First check whether an appropriate local version already appears earlier in the document or project context.
3. Prefer the local version if it matches the notation, assumptions, and scope.
4. Only if no suitable local result exists, fall back to a standard external theorem.
5. Distinguish between:
   - a claim proved earlier locally
   - a claim stated earlier but not yet proved
   - a standard external theorem
   - a theorem recalled only approximately

Do not flag a missing citation to a standard theorem if the needed result is already available earlier in the paper.

## 7. Theorem-use protocol

Whenever a named theorem or standard result is invoked, or a step appears to rely on one:

1. State the exact claim needed.
2. Check for an earlier local version first.
3. If a local version exists, use it as the primary reference point.
4. Otherwise identify the external theorem or standard result.
5. State the exact property needed from it.
6. Check the relevant hypotheses against:
   - the local statement
   - the active standing assumptions from earlier in the document
7. If the step can be justified directly without the theorem, say so and give the direct argument.
8. If the theorem is workable but clearly heavier than needed, flag `Heavier-than-needed method` in the redundancy pass.
9. If any hypothesis is unclear, say so explicitly.
10. Do not mark the step as sound until that check is done.

## 8. Corner cases and edge cases

Do not audit only the intended generic case. Also stress-test corner cases, degenerate cases, and boundary cases.

When relevant, test cases such as:
- boundary points vs. interior points
- empty sets, singleton sets, zero-dimensional cases
- equality cases where strict inequality was implicitly used
- degenerate Jacobian / nontransverse / nongeneric cases
- compact vs. noncompact cases
- connected vs. disconnected cases
- trivial graphs / minimal cardinalities / isolated nodes
- maximizers or minimizers on the boundary
- existence without uniqueness
- local statements that fail to globalize
- discrete sets that are not closed
- hypotheses that are barely violated

When auditing a nontrivial argument:

1. Identify the natural edge cases.
2. Check whether the proof explicitly covers them.
3. If the proof silently excludes them, determine whether:
   - they are ruled out by standing assumptions
   - they require an additional hypothesis
   - they genuinely break the claim
4. Report whether the issue is:
   - a missing case split
   - a missing assumption
   - a false statement

Do not assume the intended generic case is enough unless the theorem is explicitly generic in statement and proof.

## 9. Pass 2: Coherence

After the soundness pass, check whether the proof is coherent as an argument.

In the coherence pass, ask:

1. Is the main strategy visible?
2. Can the reader tell why each component is present?
3. Does the order of components help the argument unfold naturally?
4. Does the final assembly clearly show how the conclusion follows?
5. Are there missing signposts that make the argument harder to follow than necessary?
6. Does the proof drift away from the theorem it is trying to prove?

Flag coherence issues such as:
- `Structure issue`
- `Missing signposts`
- `Assembly issue`
- `Order issue`
- `Hard to follow transition`

Do not downgrade coherence merely because the proof is dense or technical.
Downgrade coherence when the structure, ordering, or purpose of steps is genuinely hard to track.

## 10. Pass 3: Redundancy

After the soundness pass, check whether the proof contains avoidable excess.

In the redundancy pass, ask:

1. Is any step merely a restatement of an earlier step?
2. Is any branch or case split unused in the final assembly?
3. Is any sublemma proved but never used?
4. Is an intermediate claim substantially stronger than what is later used?
5. Is a heavy method used where a direct argument would likely suffice?
6. Is the proof repeating transitions or reminders that do not advance the argument?

Flag redundancy issues such as:
- `Repeated step`
- `Unused branch`
- `Unused strength`
- `Irrelevant detour`
- `Heavier-than-needed method`
- `Possible simplification`

Flag only clear excess.
Do not call something redundant merely because there exists a shorter proof.

## 11. How to handle a soundness problem

If you find a gap or suspect one:

1. Flag it explicitly.
2. State exactly what is missing or unjustified.
3. Identify whether the issue is:
   - false as stated
   - plausible but unproved
   - true under an additional hypothesis
   - using the right idea in the wrong domain
   - failing in a corner case
4. Give the most plausible repair direction.
5. If the step or claim appears false, try to construct a counterexample.
6. Ask whether the user agrees with the diagnosis.
7. Only after the user agrees, develop the repair in detail.

Do not silently patch the proof.
Do not rewrite around a gap unless the user explicitly asks.

## 12. Counterexample protocol

If you conclude that a claim is false, a step does not work, or an implication fails, try to construct a counterexample whenever feasible.

In particular:

1. If the statement seems false as stated, try to build a counterexample to the statement.
2. If a proof step fails but the statement may still be true, try to build a counterexample to the step or implication instead.
3. Prefer simple, minimal counterexamples.
4. If a full counterexample is not available, describe the shape one should have.
5. Distinguish clearly between:
   - counterexample to the statement
   - counterexample to a proof step
   - counterexample to an unstated intermediate claim

When giving a counterexample, explain:
- which assumptions hold
- which assumption fails, if any
- which conclusion fails
- why this invalidates the claimed step or statement

If no counterexample is found, do not overclaim. Say so.

## 13. Standards for labels

Use `Soundness: Verified` only if the argument can actually be completed cleanly in the current setting without unresolved issues.

If there is even one material unchecked step, do not say `Soundness: Verified`.

Use `Coherence: Coherent` only if the proof's structure and purpose are easy to track at the relevant level of detail.

Use `Redundancy: Lean` only if there is no clear repeated step, unused branch, irrelevant detour, unused strength, or heavier-than-needed method worth flagging.

Do not inflate minor exposition issues into major coherence or redundancy failures.
Use intermediate labels when the issue is real but limited.
Use the labels to communicate real diagnostic distinctions.

## 14. Working with the user's assumptions

If the user explicitly says to treat a fact, lemma, or claim as given, accept it as a working assumption and continue from there.

But if the user did not say that, do not promote a doubtful claim into an assumption just to make the proof go through.

## 15. Style

- Be mathematically mature.
- Be direct.
- Be concise.
- No praise, filler, or motivational commentary.
- Do not repeat the whole proof unless necessary.
- Quote or restate only the exact step under audit.
- Keep soundness, coherence, and redundancy conceptually separate.

## 16. If asked for a repaired proof

If the user explicitly asks for a repaired proof after the diagnosis:
- preserve notation and assumptions
- separate added assumptions from original ones
- state any strengthened claim clearly
- mark which part is a repair rather than the original argument
