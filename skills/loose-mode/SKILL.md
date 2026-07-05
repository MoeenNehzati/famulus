---
name: loose-mode
description: Use when the user invokes "loose mode" or asks for broad exploration, strategy, options, or a fast overview — when breadth and speed matter more than certainty. Contrasts with tight-mode.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: workflow-general-assistant

Dependencies: none

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->

## Core principle

Prioritize strategy, decomposition, and options over rigor. Move fast, cover ground, identify the main obstacle and promising routes.

## Rules

- Break the problem into components; identify the main obstacle or blocker.
- Suggest promising approaches; connect to relevant tools, libraries, literature, or precedent.
- Emphasize structure: what the subproblems are, what could address each, how the pieces fit together.
- Match the technical level of the conversation — don't over-explain basics unless asked.
- Distinguish `Verified` / `Likely` / `Speculative` when proposing approaches.
- If constructing a new argument, plan, or design, use a planning skill if available (e.g. `superpowers:brainstorming`, `superpowers:writing-plans`).

## Output style

Short, useful, bulleted. Use headings when helpful:
`Main obstacle` · `Main idea` · `Possible approaches` · `What would need to be shown or built` · `How the pieces fit together`

## Mode switching

Don't switch modes unless told to. If ambiguous, stay in loose mode (it's the default). To switch: `tight mode: <question>`.
