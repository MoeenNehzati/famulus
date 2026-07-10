---
name: notation-review
description: |
  Audit and improve mathematical notation for lightness, unification, reuse across scopes,
  and semantic transparency.

  Use when:
  - the user asks to review, simplify, unify, standardize, or clean up notation
  - related objects should share a notation family, or notation should be lighter, more reusable, or more self-explanatory
  - the user asks whether notation follows standard conventions or the paper's local conventions

  Do not use when:
  - the main issue is proof validity, prose editing, stylistic rewriting, or grammar
  - the user wants a proof plan or mathematical strategy rather than notation review

  Success criteria:
  - identify notation that is heavier than needed
  - identify opportunities to unify notation across sections or scopes and cases where notation hides mathematical relationships
  - prefer standard or paper-local conventions when they clarify the mathematics
  - produce a coherent candidate notation scheme without rewriting text unless asked
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: research-assistant

Dependencies: none

Interface Version: 1

Exported Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
When this skill is used, begin with:

Skill: notation-review

## 1. Goal

Your job is to audit mathematical notation, not prose.

Optimize for notation that is:
- easy to understand and follow on first encounter
- light rather than heavy
- unified across the document
- reusable across scopes
- semantically transparent
- readable both in formulas and in the TeX source

Once the notation is understood, the mathematics should be readable with minimal reliance on explanatory text.

## 2. Review stance

Audit notation at the document level, not just the local paragraph or section, unless the user explicitly asks for a local review only.

When reviewing a notation choice:
- check how the same object or notation family appears earlier and later in the document
- assess it from the viewpoint of a new reader in the same field
- do not improve notation locally in a way that creates inconsistency globally

Prefer diagnosis first. Do not rewrite notation globally unless the user asks.

## 3. Output

Start with:

- `Mode: Explore`
- `Skill: notation-review`

Keep the answer concise and diagnosis-first.
Organize only when useful.
When structure helps, use short headings such as:
- `Main issues`
- `Unification`
- `Conventions`
- `Candidate scheme`

Do not force a fixed template.

## 4. Principles

Prefer:
- lighter notation over heavier notation
- one notation family for one mathematical family
- notation that reflects mathematical similarity
- notation that highlights essential parameters and suppresses routine ones
- notation that scales across local and global scopes
- notation standard in the paper's field or immediate literature
- TeX macros that improve consistency, maintainability, and source readability for the author

Avoid:
- unnecessary hats, tildes, bars, primes, stars, long subscripts, and overloaded superscripts
- introducing a fresh symbol for something that is mostly the same object
- using unrelated symbols for closely related objects
- reusing one symbol family for genuinely different objects
- introducing notation used only once or twice unless it materially improves readability
- macros that make the TeX source harder to scan than the underlying notation

If two things are mostly the same thing, that should be visible in the notation.

If a distinction is mathematically real, encode it with the lightest modifier that makes the distinction clear.

## 5. Conventions

Prefer standard conventions when they fit the paper, but treat field-specific conventions as primary.
Do not import notation from a foreign area if it will look unnatural to readers in the paper's own field.

Examples of common defaults:
- compact sets as `K`
- open sets as `U`
- balls as `B`
- nonnegativity as `+`
- strict positivity as `++`

These are defaults, not rigid rules. If the paper has a strong local convention, preserve it and extend it consistently.

## 6. What to check

When auditing notation, check for:
- `Heavy notation`
  - a symbol carries more decoration than the mathematics needs
- `False split`
  - two nearly identical notions are written with unrelated notation
- `False unification`
  - genuinely different notions are written as if they were the same
- `Family mismatch`
  - related objects are not presented as a coherent notation family
- `Scope drift`
  - notation changes across sections even though the role is unchanged
- `Type ambiguity`
  - notation does not signal whether an object is a set, map, scalar, vector, parameter, or operator
- `Semantic opacity`
  - notation does not help the reader see what the object is or how it relates to nearby objects
- `Parameter overload`
  - notation exposes too many parameters or distinctions at once
- `Unnecessary notation`
  - a symbol is introduced where direct formulas or ordinary mathematical language would be clearer
- `Convention mismatch`
  - notation fights the paper's local conventions
- `Field mismatch`
  - notation follows conventions from a different area rather than the paper's own field
- `Literature mismatch`
  - notation departs from common usage in the relevant area without enough benefit
- `Macro opacity`
  - macros make the TeX source harder to read than the raw notation would
- `Macro inconsistency`
  - recurring global notation is not centralized in macros when it should be
- `Reader friction`
  - a new reader in the same field would struggle to decode the notation quickly

## 7. Unification and scope

When two objects are variants of one mathematical object:
- prefer one base symbol with light modifiers
- reserve modifiers for real distinctions
- use a notation scheme that can extend to later sections

Examples of distinctions that often belong to one notation family:
- local vs global
- pointwise vs setwise
- primal vs dual
- constrained vs unconstrained
- interior vs closure vs boundary
- deterministic vs randomized

Do not force unification when it hides an important difference.

Define notation at the widest scope where it remains coherent, but no wider.
Use local notation for temporary objects and global notation for recurring objects.

Prefer notation schemes that survive:
- a change of domain
- a local-to-global upgrade
- restriction to a subset
- passage from a point to a set
- passage from an object to its derived quantity

## 8. How to propose fixes

By default:
1. diagnose the notation system
2. identify the main points of friction
3. propose a candidate unified scheme
4. explain what should be merged, what should stay separate, and why
5. ask before rewriting text or changing notation globally

When proposing a notation change:
- first ask whether the best fix is to remove notation rather than rename it
- preserve the paper's local conventions where reasonable
- prefer the smallest coherent change over a total renaming
- explain the organizing principle behind the new notation
- recommend macros when notation recurs globally or may change later
- keep macro names mathematically transparent and source-readable

## 9. What not to do

Do not:
- drift into proof checking unless notation blocks the mathematics
- turn notation review into prose editing
- recommend heavy notation just to make every distinction explicit
- standardize blindly when the paper's own convention is already better for this context
- rewrite large sections unless the user asks for line edits, a LaTeX block, or a full replacement
