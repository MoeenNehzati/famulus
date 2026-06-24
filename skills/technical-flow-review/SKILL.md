---
name: technical-flow-review
description: |
  Review flow, structure, motivation, and readability of a technical document.

  Use when:
  - the user wants feedback on section-level or whole-document flow
  - the user wants to know whether the problem, goal, or contribution is obvious early enough
  - the user wants to assess whether the intended audience can follow the document without mastering all technical details
  - the user wants feedback on section ordering, motivation, signposting, or overall readability

  Do not use when:
  - the main task is proof verification, notation review, or sentence-level prose editing or copyediting

  Success criteria:
  - identify the document's function, field, and audience before judging flow
  - review the document relative to its purpose rather than against a generic standard
  - identify where the reader can and cannot understand the document's aim without deep technical engagement
  - identify section-level and whole-document flow problems
  - suggest a clearer structural direction without rewriting the document unless asked
---

When this skill is used, begin with:

Skill: technical-flow-review

Category: document-oriented

Dependencies: none

## 1. Goal

Your job is to review the technical flow of a document, not to audit proofs, notation, or prose line by line.

Focus on:
- whether the document's purpose is clear
- whether the document profile is clear
- whether the problem, goal, or contribution becomes clear early enough
- whether a reader in the intended audience can follow what the document is doing without understanding all details
- whether each section is well motivated and has a clear role
- whether the ordering of sections, results, and technical material serves the reader
- whether the document fits its length constraint, whether measured in pages, words, slides, or time

Read `../../references/document-profile-schema.md` when document-profile fields or normalization rules matter.
Use that file as the canonical source for:
- profile fields
- field meanings
- normalization of document-type labels
- the relation between `Audience`, `Assumed background`, and derived reader familiarity

## 2. First task: identify the document profile

Before reviewing the document, determine the document profile using the shared schema.
Also infer reader familiarity from the stated audience and assumed background when that matters for the review.

Do not evaluate flow in the abstract.
Evaluate it relative to the document's function, field/subfield, audience, and the expectations of its intended reader.

If document type, field/subfield, audience, or genre expectations matter materially for the review, read only the relevant files in `references/` before evaluating the document.

For the main profile, read the shared baseline first, then the closest field-specific deviation:
- `references/document-types/<document-type>/general.md`
- `references/document-types/<document-type>/<field>.md`

Treat the `general.md` file as the default for that document type.
Treat the field file as deviations from that default, not as a standalone replacement.

Use the closest available field file:
- `econ.md`
- `math.md`
- `cs.md`

If the document is cross-field, choose the dominant field/subfield for the main baseline and mention any secondary audience explicitly in the review.
Do not try to merge all field conventions evenly if one field clearly sets the main standard.

Examples:
- `references/document-types/journal-article/general.md`
- `references/document-types/journal-article/econ.md`
- `references/document-types/research-presentation/general.md`
- `references/document-types/research-presentation/math.md`
- `references/document-types/research-notes/general.md`
- `references/document-types/research-notes/cs.md`

Then read additional files only when they are needed:
- `references/audience-familiarity.md`

Use `references/audience-familiarity.md` as an overlay, not as the main document-type baseline.
Its role is to calibrate how much background, motivation, and signposting the chosen document profile should require.

## 3. Document header comment

If the document lacks a suitable top-of-document profile comment, treat that as part of the initial document-profile diagnosis.
Do not invent an ad hoc schema or edit the file without the user's approval.

## 4. Output

Start with:

- `Mode: Explore`
- `Skill: technical-flow-review`

Keep the answer concise and diagnosis-first.
Organize only when useful.
When structure helps, use short headings such as:
- `Document profile`
- `Big-picture flow`
- `Section roles`
- `Ordering problems`
- `Missing bridges`
- `Candidate restructure`

Do not force a fixed template.

## 5. Review criteria

Review the document relative to its identified document profile.
Use the reference files as baselines and defaults, not as rigid templates.

Check:
- whether the title, abstract, and introduction make the document's purpose visible early enough
- whether the beginning of the document does enough orientation work for its type
- whether the reader can quickly tell what kind of document this is
- whether the reader can quickly tell what field/subfield conventions the document is written in
- whether the problem, goal, or contribution is obvious early enough
- whether motivation is calibrated to the intended reader
- whether the reader can understand what the document is about without mastering all the technical details
- whether the level of detail, rigor, and signposting fits the intended reader
- whether each section has a clear role
- whether the ordering of material helps the reader rather than reflecting the author's discovery order
- whether the opening sets expectations that the body actually fulfills
- whether the main milestones are visible early enough for high-level understanding
- whether the document spends detail where it matters most
- whether the document's scope and level of detail fit its length constraint
- whether technical details appear too early, too late, or in the wrong place
- whether the document contains enough bridges, previews, and transitions for its intended audience

For papers, treat the opening package as title + abstract + introduction.
For presentations, treat it as the opening slides or opening minutes.
For research notes, treat it as the top comment block plus the beginning of the document.

## 6. What to look for

When reviewing flow, check for:
- `Problem opacity`
  - the reader cannot quickly tell what problem or goal the document addresses
- `Contribution opacity`
  - the reader cannot tell what is new, useful, or worth learning here
- `Field-position opacity`
  - the document does not make clear what field/subfield it is speaking to or from
- `Audience mismatch`
  - the amount of background, motivation, or detail does not fit the intended reader
- `Promise-payoff mismatch`
  - the opening suggests one agenda or payoff, but the body delivers another
- `Section-role ambiguity`
  - a section exists but its purpose is unclear
- `Ordering problem`
  - material appears in an order that makes the document harder to follow
- `Missing bridge`
  - the reader is expected to make a conceptual transition without enough guidance
- `Orientation failure`
  - the reader cannot tell the immediate goal, long-term goal, or why the present section matters
- `Milestone burial`
  - key claims, takeaways, or structural milestones only become visible too late
- `Detail misallocation`
  - routine material gets too much space or crucial material too little
- `Length mismatch`
  - the document's ambition, detail, or pacing does not fit its page, word, slide, or time constraint
- `Early technical overload`
  - too much detail appears before the reader understands why it matters
- `Late orientation`
  - the document explains its aim or roadmap too late
- `Structure drift`
  - the document loses its central thread as it proceeds
- `Companion mismatch`
  - a technical note or companion document does not make clear how it relates to the main document

## 7. How to judge success

Ask:
- Can a researcher in the intended audience explain what this document is doing after reading the beginning?
- Can that reader see why the document exists before understanding all the details?
- Can that reader tell what field/subfield conventions are supposed to govern the exposition?
- Does the opening make promises that the rest of the document actually fulfills?
- Does each section prepare the reader for what comes next?
- Is the level of motivation, detail, and signposting appropriate for this document profile?
- Does the scope actually fit the stated length constraint?
- Are the major milestones visible early enough?
- Would the same document need a different structure if its audience were broader or narrower?

## 8. How to propose fixes

By default:
1. identify the document profile
2. diagnose the main flow problems
3. explain how those problems depend on the document profile
4. suggest a cleaner structural direction
5. if needed, note that the missing profile comment should be handled before deeper document-level review
6. ask before rewriting sections or editing files

When suggesting improvements:
- prefer the smallest structural change that materially improves readability
- explain the role each section should play
- distinguish local section problems from whole-document ordering problems
- do not rewrite prose unless the user asks

## 9. What not to do

Do not:
- turn this into proof checking
- turn this into notation review
- default to sentence-level prose editing
- judge a coauthor note by journal-paper standards
- judge a journal paper by internal-note standards
- insert the document header comment automatically without the user's approval
