# Research

This domain covers math-heavy auditing, document review, bibliography checks,
LaTeX support, and dependency-graph extraction for research projects.

## Reasoning and Structure

Use these workflows when you want the assistant to inspect the mathematical or
logical structure of a document rather than merely summarize it.

Example prompts:

- `Build a math dependency graph for paper.tex.`
- `Audit this proof for gaps.`
- `Does this theorem actually apply in my setting?`
- `Review this notation for consistency.`

Public example:

- Graph: <https://moeennehzati.github.io/assets/html/nehzati2026inference.html>
- Paper: <https://arxiv.org/abs/2602.13450> — *Inference From Random Restarts*

## Writing and Document Review

Use these workflows when you want help polishing or checking a document's
presentation.

Example prompts:

- `Review the flow of this section.`
- `Polish this paragraph without changing the math.`
- `Audit this bibliography for duplicates or bad metadata.`
- `Convert this PDF into markdown I can inspect.`

<!-- BEGIN AUTO-GENERATED DOCS: research -->
> Generated from live blueprints. Do not edit this block by hand.

- `bib-audit` — Audit a `.bib` file for validity, style, external metadata, and duplicates
- `formal-prose-review` — Polish grammar, tone, and concision in technical prose without touching the math
- `latex-workshop` — Compiling or troubleshooting a LaTeX document inside a VS Code project whose build is governed by LaTeX Workshop
- `make-tex-docstring` — Create or standardize a TeX document-profile comment, or when a selected TeX task requires profile information that the document does not state clearly
- `math-dependency-graph` — Extract an assumptions-to-results dependency graph from a LaTeX document
- `notation-review` — Review, simplify, or standardize mathematical notation
- `pdf-to-markdown` — Convert a research-paper PDF into LLM-readable text
- `proof-audit` — Audit a proof for soundness, coherence, hidden assumptions, and redundancy
- `technical-flow-review` — For document-level review of technical structure, motivation, or reader flow
- `tool-applicability` — Check whether a theorem or framework achieves a target in the current setting
<!-- END AUTO-GENERATED DOCS: research -->
