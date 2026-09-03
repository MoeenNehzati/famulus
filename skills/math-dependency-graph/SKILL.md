---
name: math-dependency-graph
description: >-
  Use when the user asks for a direct assumptions-to-results dependency graph of a LaTeX mathematical document. Do not use for proof, notation, prose, or literature review.
---


<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `math-dependency-graph._rtx.interface.scripts-build-math-dependency-graph` — Render an interactive HTML math dependency graph from canonical JSON; the saved document loads ELK and MathJax from jsDelivr when opened.
  - Caller: `math-dependency-graph`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--html-out": "path", "--macro-file": "path", "--reduce-transitive-edges": true, "--refresh-macros": true, "--tex-entry": "entrypoint.tex"}, "positionals": ["source.json"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `math-dependency-graph._rtx.interface.scripts-extract-mathjax-macros` — Extract MathJax macro definitions from a TeX entrypoint, recursively following \input/\include.
  - Caller: `math-dependency-graph`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--out": "path"}, "positionals": ["entrypoint.tex"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `math-dependency-graph._rtx.interface.scripts-read-tex-labels` — Resolve TeX label numbering by compiling the document, so numbers match what the paper prints instead of being derived by inspection.
  - Caller: `math-dependency-graph`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--out": "path"}, "positionals": ["entrypoint.tex"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `math-dependency-graph._rtx.interface.scripts-serve-graph` — Start graph HTML from a local directory in a no-cache background server and return readiness metadata for repeated browser inspection.
  - Caller: `math-dependency-graph`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--directory": "path", "--host": "host", "--port": "port"}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `math-dependency-graph.interface.extract@1` — Extracts a notation-faithful direct mathematical dependency graph into canonical JSON.
<!-- END BLUEPRINT INTERFACES -->

# Mathematical Dependency Graph

Use this skill to extract the direct mathematical dependency structure of a source document and render it as an interactive graph.

## Workflow

1. Resolve the requested document and scope, and identify the document's **root TeX entrypoint**. Steps 2 and 3 both read the root, not the scoped subset being graphed: preambles and counters live in the root, so running them against a fragment silently yields nothing.

2. Resolve label numbering with `math-dependency-graph._rtx.interface.scripts-read-tex-labels` and retain the artifact path. The interface draft-compiles the document to read the numbers TeX itself assigns, and reports the kind of each labelled object alongside its number. It is optional by design: with no LaTeX toolchain, or on a document that does not compile, it reports why and returns an empty map, and the run continues. Never treat an empty map as a failure.

3. Extract MathJax macros with `math-dependency-graph._rtx.interface.scripts-extract-mathjax-macros` and retain the artifact path. Skip only when the source defines no custom commands anywhere.

4. Invoke `math-dependency-graph.interface.extract` with the source, the scope, the required canonical JSON destination, and the label artifact from step 2 when it resolved any labels. Without it the extractor derives numbering from the preamble's counter declarations, which is sound but weaker.

5. Receive the completed canonical JSON path and any reported evidence gaps.

6. Render the graph with `math-dependency-graph._rtx.interface.scripts-build-math-dependency-graph`, passing the canonical JSON, the macro artifact from step 3, the label artifact from step 2, and the HTML output path. Rendering is the final required step of every run; canonical JSON without a rendered HTML is an incomplete result. The interface supplies the skill's edge catalog and relation semantics, and resolves `\ref` and `\eqref` in the graph's text against the label map.

7. Confirm the render merged what it should. A macro count of zero on a document that uses custom commands means step 3 read the wrong entrypoint; unresolved TeX commands are reported in the viewer. Correct the input and render again.

8. If interactive inspection is requested, invoke `math-dependency-graph._rtx.interface.scripts-serve-graph`, then terminate the returned PID when inspection is complete. If the MCP host owns the gateway with a kill-on-close process container, keep that gateway open during inspection; closing it asynchronously terminates the server instead.

9. Report the JSON and HTML artifact paths, the number of macros merged, whether label numbering resolved and from which compiler, the scope represented, and unresolved extraction gaps.

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
- the rendered HTML path, which every run must produce
- the number of MathJax macros merged into the render
- whether label numbering resolved, and any TeX commands the viewer could not render
- the represented source and scope
- any extraction gaps or confidence limitations
- the local serving address when serving was requested
