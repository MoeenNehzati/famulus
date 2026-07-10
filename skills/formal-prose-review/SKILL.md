---
name: formal-prose-review
description: |
  Review technical prose for grammar, typos, wording, concision, and formal professional tone without changing mathematical or substantive content.

  Use when:
  - the user wants grammar, punctuation, wording, clarity, or tone polished in a paragraph, section, or document
  - the goal is a more formal or professional presentation without changing the math or argument
  - awkward or informal phrasing should be cleaned up while preserving substance

  Do not use when:
  - the main task is proof verification, notation review, or document-level structure or flow review
  - the user wants substantive rewriting of claims, assumptions, or argument structure

  Success criteria:
  - identify grammar, typo, punctuation, wording, and tone issues
  - improve clarity and concision without changing mathematical substance
  - preserve notation, claims, assumptions, and proof structure unless explicitly asked otherwise
  - respect field conventions and user-approved wording unless they create real error or ambiguity
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: research-assistant

Dependencies: none

Interface Version: 1

Exported Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
When this skill is used, begin with:

Skill: formal-prose-review

## 1. Goal

Your job is to polish technical prose for a formal setting.

Focus on:
- grammar
- typos
- punctuation
- sentence-level clarity
- awkward wording
- concision
- formal professional tone

Do not change the mathematical substance.
This skill is for prose polish, not proof correction, notation redesign, or document restructuring.

## 2. Core rule

Preserve:
- mathematical claims
- assumptions
- notation
- theorem and proof structure
- technical meaning

If a language change would alter the mathematical content, do not make it silently.

If the document includes a top-of-document comment block or other explicit profile information, read `../../references/document-profile-schema.md` and compare the block to the shared schema.
If the block is malformed, incomplete, or inconsistent with the shared schema, treat the profile information as missing or unclear rather than trusting it blindly.
If the document includes a suitable top-of-document comment block or other explicit profile information, use it to calibrate the prose standard.
In particular, use the stated document type, audience, and purpose to judge how formal, compressed, or polished the prose should be.
Do not apply journal-paper prose standards mechanically to slides, presentations, or internal notes.

## 3. What to check

Check for:
- grammar and agreement errors
- tense inconsistency
- unclear pronoun reference
- faulty parallelism
- article and preposition errors
- sentence boundary problems such as run-ons or fragments
- typos and spelling errors
- punctuation problems
- awkward, tangled, or clumsy sentences
- informal, chatty, or conversational phrasing
- wordiness and filler
- inflated or vague wording
- overuse of nominalizations when a clearer verb would help
- ambiguity caused by prose rather than by mathematics
- overstatement or unnecessary hedging
- biased, exclusionary, or unnecessarily non-inclusive wording
- tone that is too casual, too emotional, or insufficiently professional

## 4. Style principles

Prefer prose that is:
- clear
- concise
- precise
- professional
- direct without sounding casual

Formal does not mean stiff, inflated, or ornate.
Do not make the prose more elaborate just to make it sound academic.

Prefer active voice when it is clearer.
Do not enforce active voice mechanically if passive voice better fits the sentence focus.

Respect field conventions.
Do not apply generic writing advice mechanically when math or economics prose conventions support a different choice.

Calibrate the prose standard to the document type when that information is available.
For example:
- journal papers usually require stricter formal polish
- slides and presentations may be shorter, more direct, and slightly lighter in tone
- internal notes may tolerate more compression if the purpose and audience support it

Prefer bias-free and inclusive language when possible.
Do not force awkward rewrites in the name of inclusiveness, but do flag wording that is unnecessarily exclusionary, biased, or dated.

## 5. Claim strength

Check whether the prose matches the certainty of the content.

Be alert to:
- overclaiming
- underclaiming
- vague hedging
- opinionated or rhetorical phrasing

Do not make claims stronger or weaker unless the wording itself is clearly inappropriate for the stated certainty.

## 6. User pushbacks and local preferences

If the user explicitly says that a wording choice is acceptable, treat that as a binding local preference unless it creates:
- a clear grammatical error
- real ambiguity
- a conflict with the requested level of formality

Do not keep re-optimizing wording the user has already approved.
Distinguish between:
- hard errors that still need fixing
- stylistic choices the user has settled

## 7. Output

Start with:

- `Mode: Explore`
- `Skill: formal-prose-review`

Keep the answer concise and diagnosis-first.
Organize only when useful.
When structure helps, use short headings such as:
- `Surface issues`
- `Tone issues`
- `Awkward wording`
- `Minimal edits`
- `Polished version`

Do not force a fixed template.

By default:
- diagnose the issues
- propose minimal corrections
- preserve the original structure as much as possible

For short passages, it is fine to give corrected text directly.
For longer passages, prefer to identify the main issues first and rewrite only as much as the user asks for.

Only give a fuller rewrite if the user asks for it.

## 8. What not to do

Do not:
- change notation
- change assumptions or claims
- alter proof structure
- turn stylistic cleanup into substantive revision
- make the prose more ornate in the name of formality
- force generic rules such as "always use active voice"
- ignore an explicit user preference unless it causes a real problem
