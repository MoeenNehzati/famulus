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

<!-- BEGIN AUTO-GENERATED DOCS: research-assistant -->
> Generated from live blueprints. Do not edit this block by hand.

- `bib-audit` — Audit a `.bib` file for validity, style, external metadata, and duplicates
- `formal-prose-review` — Polish grammar, tone, and concision in technical prose without touching the math
- `latex-workshop` — Follow VS Code LaTeX Workshop build behavior for TeX/LaTeX documents
- `make-tex-docstring` — Create or propose a top-of-document TeX comment block that records the document profile and intended use
- `math-dependency-graph` — Extract an assumptions-to-results dependency graph from a LaTeX document
- `notation-review` — Audit and improve mathematical notation for lightness, unification, reuse across scopes, and semantic transparency
- `proof-audit` — Audit a proof for soundness, coherence, hidden assumptions, and redundancy
- `technical-flow-review` — Review flow, structure, motivation, and readability of a technical document
<!-- END AUTO-GENERATED DOCS: research-assistant -->
