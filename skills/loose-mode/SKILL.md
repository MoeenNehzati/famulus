---
name: loose-mode
description: >-
  Use when the user explicitly asks to enter or continue loose mode. Do not infer it from an ordinary request for ideas, options, strategy, or an overview.
---


<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Used Interfaces: none
<!-- END BLUEPRINT INTERFACES -->
## Core principle

Prioritize strategy, decomposition, and options over rigor. Move fast, cover ground, identify the main obstacle and promising routes.

## Rules

- Break the problem into components; identify the main obstacle or blocker.
- Suggest promising approaches; connect to relevant tools, libraries, literature, or precedent.
- Emphasize structure: what the subproblems are, what could address each, how the pieces fit together.
- Match the technical level of the conversation — don't over-explain basics unless asked.
- Distinguish `Verified` / `Likely` / `Speculative` when proposing approaches.
- If constructing a new argument, plan, or design, use a planning skill if available.

## Output style

Short, useful, bulleted. Use headings when helpful:
`Main obstacle` · `Main idea` · `Possible approaches` · `What would need to be shown or built` · `How the pieces fit together`

## Mode switching

Don't switch modes unless told to. If ambiguous, stay in loose mode (it's the default). To switch: `tight mode: <question>`.
