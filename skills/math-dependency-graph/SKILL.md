---
name: math-dependency-graph
description: |
  Use when a LaTeX math document needs a direct dependency graph of its assumptions-to-results structure, covering standing assumptions, definitions, mathematical results, notation, and evidence, as canonical JSON or interactive HTML.

  Do not use when the main goal is proof validation, notation cleanup, prose review, or a literature map.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: research; topics: mathematical-reasoning, visualization, scholarly-documents; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 3

Uses Interfaces: none

Public Interfaces:
- `math-dependency-graph.interface.default`
- `math-dependency-graph.interface.extract`
- `math-dependency-graph.interface.scripts-build-math-dependency-graph`
- `math-dependency-graph.interface.scripts-extract-mathjax-macros`
- `math-dependency-graph.interface.scripts-serve-graph`
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `math-dependency-graph.interface.scripts-build-math-dependency-graph` — Render an interactive HTML math dependency graph from canonical JSON; the saved document loads ELK and MathJax from jsDelivr when opened.
  - `dispatcher --caller-skill math-dependency-graph math-dependency-graph.interface.scripts-build-math-dependency-graph <source.json> [--tex-entry <entrypoint.tex>] [--html-out <path>] [--macro-file <path>] [--refresh-macros] [--reduce-transitive-edges]`
- `math-dependency-graph.interface.scripts-extract-mathjax-macros` — Extract MathJax macro definitions from a TeX entrypoint, recursively following \input/\include.
  - `dispatcher --caller-skill math-dependency-graph math-dependency-graph.interface.scripts-extract-mathjax-macros <entrypoint.tex> [--out <path>]`
- `math-dependency-graph.interface.scripts-serve-graph` — Serve graph HTML from a local directory with no-cache headers for repeated browser inspection.
  - `dispatcher --caller-skill math-dependency-graph math-dependency-graph.interface.scripts-serve-graph [--directory <path>] [--host <host>] [--port <port>]`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `math-dependency-graph.interface.default` — Primary LLM-facing skill instructions.
- `math-dependency-graph.interface.extract` — Extracts a notation-faithful direct mathematical dependency graph into canonical JSON.
<!-- END BLUEPRINT INTERFACES -->

# Mathematical Dependency Graph

Use this skill to extract the direct mathematical dependency structure of a source document and render it as an interactive graph.

## Workflow

1. Resolve the requested document and scope.
2. Invoke `math-dependency-graph.interface.extract` with the source, scope, and required canonical JSON destination.
3. Receive the completed canonical JSON path and any reported evidence gaps.
4. When the document defines custom TeX commands, invoke `math-dependency-graph.interface.scripts-extract-mathjax-macros` and retain the resulting macro artifact.
5. Invoke `math-dependency-graph.interface.scripts-build-math-dependency-graph` with the canonical JSON and requested HTML output path.
6. If interactive inspection is requested, invoke `math-dependency-graph.interface.scripts-serve-graph`.
7. Report the JSON and HTML artifact paths, the scope represented, and unresolved extraction gaps.

## Responsibility boundary

The `extract` instruction interface owns entity identification, dependency semantics, evidence, uncertainty, and canonical JSON completion. This gateway must not duplicate or improvise those rules.

The build and serve interfaces are deterministic. They validate and adapt canonical graph data, render HTML, and serve local artifacts; they do not infer mathematical dependencies.

## Required behavior

- Use direct dependencies rather than transitive closure.
- Preserve the document's notation and evidentiary scope.
- Treat the canonical JSON artifact as the handoff between semantic extraction and deterministic visualization.
- Fail clearly when extraction does not produce a usable artifact.
- Never fill missing entities or relationships during rendering.
- Report ambiguity and unresolved evidence rather than silently inventing structure.

## Outputs

A successful run identifies:

- the canonical graph JSON path
- the rendered HTML path
- the represented source and scope
- any extraction gaps or confidence limitations
- the local serving address when serving was requested
