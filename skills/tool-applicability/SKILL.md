---
name: tool-applicability
description: |
  Check whether a theorem, framework, or mathematical tool can achieve a target objective in the current setting and, if not, what nearest valid result it still delivers.

  Use when:
  - the user asks whether a theorem, method, machinery, or formalism applies
  - the user wants to know whether a tool proves a target under current assumptions, what added assumptions would make it work, or what weaker nearby result it gives

  Do not use when:
  - the main task is line-by-line proof auditing, broad proof strategy without a specific candidate tool, or notation or document-structure review

  Success criteria:
  - identify the setting, target, and candidate tool clearly
  - separate established assumptions from conjectures or hopes and track which assumptions are available versus newly required
  - determine what the tool requires, what it gives, whether that is an exact fit, fit with added assumptions, nearby weaker result, or mismatch, and what the exact gap or weakest plausible repair is
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: workflow-general-assistant

Skill Version: 1

Uses Interfaces: none

Public Interfaces:
- `tool-applicability.llm.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: tool-applicability

## 1. Goal

Your job is to determine what a candidate mathematical tool can actually achieve in the present setting.

Here "tool" may mean:
- a theorem
- a lemma
- a formalism
- a framework
- a method or machinery
- a standard reduction or proof template

Do not stop at a yes/no applicability verdict.
If the exact target does not follow, determine the nearest valid result the tool can still deliver and state exactly how it differs from the target.

## 2. First task: identify the three inputs

Before judging applicability, identify:
- the current setting and available assumptions
- the target objective
- the candidate tool

Be explicit about each one.
Also be explicit about the certainty level of each one.

If the user has not stated them cleanly, infer them only when the inference is reliable.
Otherwise ask for clarification.

## 3. What to extract

From the current setting, extract:
- ambient space or category
- object types
- regularity assumptions
- compactness, completeness, coercivity, measurability, integrability, or differentiability assumptions
- finite-dimensional vs. infinite-dimensional features
- local vs. global scope
- boundary vs. interior issues
- genericity, prevalence, density, residuality, or almost-everywhere notions, if relevant
- assumptions already established elsewhere in the discussion or document

Distinguish carefully between:
- established assumptions
- working assumptions the user has asked you to accept
- plausible but unverified background beliefs
- conjectures, guesses, or hopes

For each important assumption, track its provenance:
- explicitly assumed now
- established earlier in the discussion or document
- plausibly implicit but not yet stated cleanly
- genuinely new if added for this tool

From the target objective, extract:
- the exact conclusion desired
- whether that objective is a firm target, a conjectured target, or only a hoped-for outcome
- whether the goal is existence, uniqueness, regularity, stability, convergence, identification, genericity, duality, optimality, or something else
- what strength of conclusion is required:
  - local or global
  - exact or approximate
  - qualitative or quantitative
  - full sequence or subsequence
  - everywhere, generic, or almost-everywhere

Decompose the target in the way that is natural for the candidate tool.
Do not assume there is a single tool-independent decomposition of the problem.
Different tools may break the objective into different subgoals, reductions, or intermediate claims.
Also ask whether the target should be reformulated into a version that better matches the tool.
Check especially whether changing quantifiers, domains, codomains, or the notion of solution would materially change the applicability verdict.

From the candidate tool, extract:
- the precise theorem, framework, or method being invoked
- the precise version of the tool, if more than one materially different version exists
- whether the recollection is exact or approximate
- the actual hypotheses
- the actual type of conclusion it gives
- whether the claimed applicability is established, conjectural, or only heuristic

## 4. Match the tool to the setting

Check separately:
- what is needed to invoke the tool at all
- what is needed to turn the tool's conclusion into the target objective
- what the tool requires
- what the current setting already provides
- what is missing
- what the tool gives even if the full target is too strong

Be explicit about whether a missing ingredient is:
- already implicit in the current setup
- a mild additional assumption
- a strong but standard extra assumption
- an ad hoc or likely unacceptable strengthening

If extra assumptions are proposed, assess both:
- whether they are mathematically natural
- whether they are natural for the current paper or project

If the tool only addresses part of the target, say which subgoals it resolves and which it leaves open.
If the tool gives only a one-way implication while the target needs more, say so explicitly.

## 5. Verdict classes

Use one of these verdicts:

- `Exact fit`
  - the tool gets the target objective under the current assumptions
- `Fit with added assumptions`
  - the tool gets the target objective, but only after adding specific assumptions
- `Adjacent result under current assumptions`
  - the tool does not get the full target, but it gets a nearby result under the current assumptions
- `Adjacent result with modified assumptions`
  - the tool gets a nearby result, but only after changing or strengthening assumptions
- `Mismatch`
  - the tool is not the right mechanism here, or the gap is too structural

When useful, also label parts of the analysis using:
- `Verified`
- `Likely`
- `Speculative`
- `Gap`
- `Needs hypothesis`
- `I don't know`

Do not upgrade a conjecture, hope, or informal expectation into an established assumption or conclusion.

## 6. Translate the conclusion

If the tool does not give the exact target, determine the nearest valid conclusion it can justify.

Whenever possible, compare explicitly:
- the target objective
- the deliverable result from this tool
- the exact gap between them

State explicitly whether the change is:
- local instead of global
- approximate instead of exact
- existence without uniqueness
- subsequential rather than full convergence
- generic or almost-everywhere instead of everywhere
- qualitative rather than quantitative
- weaker regularity
- weaker identification
- weaker optimality or duality
- interior only, not boundary
- finite-dimensional only, not infinite-dimensional

Do not say only that the result is "weaker."
State exactly what part of the target is lost or changed.

## 7. Diagnose the mismatch

If the tool fails to give the target, identify why.

Common mismatch types:
- missing hypothesis
- wrong version of the tool
- wrong ambient framework
- wrong notion of regularity
- wrong notion of genericity
- wrong domain/codomain or object type
- the tool's conclusion is of a different kind than the target
- the tool is recalled only approximately
- the target objective is stronger than what the tool family normally delivers

When possible, say whether the obstruction looks:
- structural
- technical
- removable
- likely sharp

When possible, identify the kind of obstruction or failure witness that blocks the stronger target.
Also distinguish between:
- the tool family is appropriate but this formulation/version is wrong
- the tool can help only after reformulating the target
- the tool family itself is the wrong lens

## 8. Better nearby routes

If the candidate tool is a mismatch, suggest better nearby options when possible.

This may mean:
- a variant of the same tool
- a weaker version of the same conclusion
- a different theorem in the same family
- a different tool family entirely
- decomposing the target into subgoals and using different tools for different parts

When extra assumptions are needed, assess their cost:
- mild and natural
- standard but strong
- ad hoc
- likely unacceptable in the current project

Do not treat "standard in the literature" as enough by itself.
Ask whether the added assumption is compatible with the paper's aims, scope, and intended level of generality.

When possible, identify the minimal plausible repair:
- the weakest extra assumption
- the mildest target reformulation
- the smallest change of tool version

Also identify the best role for the candidate tool:
- main engine
- intermediate lemma supplier
- reduction step
- partial result only
- heuristic guide
- wrong tool for this problem

Do not suggest alternatives as if they are verified unless they really are.

## 9. Output

Start with:

- `Mode: Explore`
- `Skill: tool-applicability`

Keep the answer concise and diagnosis-first.
Organize only when useful.
When structure helps, use short headings such as:
- `Current setting`
- `Certainty status`
- `Assumption provenance`
- `Target objective`
- `Possible reformulation`
- `Tool-dependent decomposition`
- `Candidate tool`
- `Tool version`
- `Tool requirements`
- `Invocation vs upgrade`
- `Verdict`
- `Best role for this tool`
- `Deliverable from this tool`
- `Exact gap`
- `Nearest achievable result`
- `How it differs`
- `Additional assumptions`
- `Minimal repair`
- `Cost of repair`
- `Alternative routes`

Do not force a fixed template.

## 10. How to judge success

A good answer should make clear:
- what the tool actually requires
- which assumptions are already available
- which assumptions, objectives, or claims are established and which are conjectural, hoped-for, or only heuristic
- where those assumptions come from
- what is needed just to invoke the tool
- what is needed to upgrade the tool's conclusion into the target
- whether the exact target is achievable
- whether the target should be reformulated to match the tool
- whether changing quantifiers, domains, codomains, or the notion of solution would change the verdict
- how the target decomposes relative to this tool
- what the tool resolves and what it leaves open
- what version of the tool is being used and whether the issue is really version-specific
- if not, what nearby result is achievable
- what the best role of this tool is in the larger argument
- what the tool actually delivers, side by side with the target
- exactly how that nearby result differs from the target
- what extra assumptions would recover the target, if any
- what the weakest plausible repair is
- how costly those extra assumptions are
- whether those extra assumptions are natural for this project rather than only mathematically standard
- whether another tool would be more natural

## 11. What not to do

Do not:
- stop after saying the tool does or does not apply
- blur the current assumptions with assumptions you wish were available
- blur established facts with conjectures, guesses, hopes, or heuristic expectations
- blur invoking the tool with obtaining the final target
- describe the nearby result vaguely
- claim the exact target follows when only an adjacent result is justified
- treat a remembered theorem statement as exact when it is only approximate
