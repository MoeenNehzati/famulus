---
name: math-dependency-graph
description: >-
  Use when the user asks for a direct assumptions-to-results dependency graph of a LaTeX mathematical document. Do not use for proof, notation, prose, or literature review.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: research; topics: mathematical-reasoning, visualization, scholarly-documents; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 3

Uses Interfaces:
- `math-dependency-graph.source.gateway -> math-dependency-graph._rtx.interface.scripts-build-math-dependency-graph@1`
- `math-dependency-graph.source.gateway -> math-dependency-graph._rtx.interface.scripts-extract-mathjax-macros@1`
- `math-dependency-graph.source.gateway -> math-dependency-graph._rtx.interface.scripts-serve-graph@1`
- `math-dependency-graph.source.gateway -> math-dependency-graph.interface.extract@1`

Public Interfaces:
- `math-dependency-graph.interface.default`
- `math-dependency-graph.interface.extract`
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

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
4. When the document defines custom TeX commands, invoke `math-dependency-graph._rtx.interface.scripts-extract-mathjax-macros` and retain the resulting macro artifact.
5. Invoke `math-dependency-graph._rtx.interface.scripts-build-math-dependency-graph` with the canonical JSON and requested HTML output path.
6. If interactive inspection is requested, invoke `math-dependency-graph._rtx.interface.scripts-serve-graph`.
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
