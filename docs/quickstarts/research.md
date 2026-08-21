# Research Quickstart

Research skills inspect different parts of a scholarly artifact. Start from the
exact paper, TeX project, proof, bibliography, or section you want reviewed,
then choose the narrowest skill that matches the question.

## Start here

1. Provide the exact source file or project when it is available.
2. If readable source or extracted text is unavailable for a research-paper
   PDF, use `pdf-to-markdown` first.
3. Choose the review or editing skill from the table below. Each one answers a
   different question and can be requested independently.

## What to use when

| Need | Skill |
|---|---|
| Explore several research directions quickly and broadly | `loose-mode` |
| Prioritize rigor, verification, and confidence over breadth | `tight-mode` |
| Obtain readable source or text from a research-paper PDF | `pdf-to-markdown` |
| Record a TeX document's purpose and working context when another task needs it | `make-tex-docstring` |
| Audit whether a proof or claimed implication is sound | `proof-audit` |
| Decide whether a named theorem, method, or framework applies | `tool-applicability` |
| Review, simplify, or standardize mathematical notation | `notation-review` |
| Map direct dependencies from assumptions to mathematical results | `math-dependency-graph` |
| Review document-level motivation, organization, and reader flow | `technical-flow-review` |
| Edit sentences while preserving their technical substance | `formal-prose-review` |
| Audit or correct bibliography problems | `bib-audit` |
| Compile or troubleshoot a VS Code project governed by LaTeX Workshop | `latex-workshop` |

## A typical document workflow

Use `loose-mode` while generating candidate approaches, interpretations, or
research directions. Use `tight-mode` when checking a conclusion, auditing an
argument, or preparing work that must be reliable. These modes control the
assistant's reasoning style; the task-specific skills below determine what is
being reviewed.

Begin with readable source. Use `make-tex-docstring` only when another TeX task
needs the document's purpose or working context to be stated explicitly.

Review substance before presentation: use `tool-applicability` for a proposed
mathematical tool, `proof-audit` for an existing argument, `notation-review`
for the notation system, or `math-dependency-graph` for the document's
assumptions-to-results structure.

Once the substance is settled, use `technical-flow-review` for the document's
overall exposition and `formal-prose-review` for sentence-level editing. Use
`bib-audit` for the bibliography and `latex-workshop` for builds governed by
the VS Code extension. Each review addresses one layer; request additional
reviews separately when you want them.
