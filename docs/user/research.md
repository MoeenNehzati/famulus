# Research Workflows

This page covers the research-facing skills: math-heavy auditing, document review, bibliography checks, and dependency-graph extraction for LaTeX projects.

## Reasoning and Structure

Use these workflows when you want the assistant to inspect the mathematical or logical structure of a document rather than just summarize it.

Example prompts:

- `Build a math dependency graph for paper.tex.`
- `Audit this proof for gaps.`
- `Does this theorem actually apply in my setting?`
- `Review this notation for consistency.`

Public example:

- Graph: <https://moeennehzati.github.io/assets/html/nehzati2026inference.html>
- Paper: <https://arxiv.org/abs/2602.13450> — *Inference From Random Restarts*

## Writing and Document Review

Use these workflows when you want help polishing or checking the presentation of a document.

Example prompts:

- `Review the flow of this section.`
- `Polish this paragraph without changing the math.`
- `Audit this bibliography for duplicates or bad metadata.`
- `Convert this PDF into markdown I can inspect.`

<!-- BEGIN AUTO-GENERATED DOCS: research -->
> Generated from live blueprints. Do not edit this block by hand.

- `bib-audit` — Audit a `.bib` file for validity, style, external metadata, and duplicates
- `formal-prose-review` — Polish grammar, tone, and concision in technical prose without touching the math
- `latex-workshop` — Follow VS Code LaTeX Workshop build behavior for TeX/LaTeX documents
- `make-tex-docstring` — Create or propose a top-of-document TeX comment block that records the document profile and intended use
- `math-dependency-graph` — Extract an assumptions-to-results dependency graph from a LaTeX document
- `notation-review` — When: - the user asks to review, simplify, unify, standardize, or clean up notation - related objects should share a notation family, or notation should be lighter, more reusable, or more self-explanatory - the user asks whether notation follows standard conventions or the paper's local conventions Do not use when: - the main issue is proof validity, prose editing, stylistic rewriting, or grammar - the user wants a proof plan or mathematical strategy rather than notation review
- `pdf-to-markdown` — Convert a research-paper PDF into LLM-readable text
- `proof-audit` — Audit a proof for soundness, coherence, hidden assumptions, and redundancy
- `technical-flow-review` — Reviewing the flow, structure, motivation, or readability of a technical document, especially when: - the user wants feedback on section-level or whole-document flow - the user wants to know whether the problem, goal, or contribution is obvious early enough - the user wants to assess whether the intended audience can follow the document without mastering all technical details - the user wants feedback on section ordering, motivation, signposting, or overall readability Do not use when: - the main task is proof verification, notation review, or sentence-level prose editing or copyediting
- `tool-applicability` — Check whether a theorem or framework achieves a target in the current setting
<!-- END AUTO-GENERATED DOCS: research -->
