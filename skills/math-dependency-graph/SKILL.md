---
name: math-dependency-graph
description: >-
  Use when the user asks for a direct assumptions-to-results dependency graph of a LaTeX mathematical document. Do not use for proof, notation, prose, or literature review.
---


<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `math-dependency-graph._rtx.interface.scripts-build-math-dependency-graph` — Render offline interactive HTML from self-contained canonical graph JSON without reading or rewriting source material.
  - Caller: `math-dependency-graph`
  - Version: 2
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--html-out": "path", "--reduce-transitive-edges": true}, "positionals": ["source.json"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `math-dependency-graph._rtx.interface.scripts-finalize-extraction` — Finalize a semantic graph draft into validated self-contained canonical JSON with embedded MathJax macros and presentation metadata.
  - Caller: `math-dependency-graph`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--draft": "draft.json", "--label-map": "labels.json", "--output": "canonical.json", "--tex-entrypoint": "entrypoint.tex"}, "positionals": [], "stdin": null}
    Required options: ["--draft", "--output", "--tex-entrypoint"]; positional arity: 0..0; stdin: forbidden
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
- `math-dependency-graph.interface.extract@2` — Extract a notation-faithful semantic graph draft, finalize it as self-contained canonical JSON, and optionally render it.
<!-- END BLUEPRINT INTERFACES -->

# Mathematical Dependency Graph

Use this skill to extract the direct mathematical dependency structure of a source document into self-contained canonical JSON and, when requested, render it as an interactive graph.

## Workflow

1. Resolve the **included content** being graphed and the document's **root TeX entrypoint** as separate inputs. The included content controls semantic evidence; the root supplies preambles, counters, and recursively reachable macros during finalization. Never use a scoped fragment as the root. For the bundled example, the included content is `source/appendix.tex`, while the root and macro-traversal start is `source/main.tex`.

2. When exact printed numbering is useful, resolve labels from the root with `math-dependency-graph._rtx.interface.scripts-read-tex-labels` and retain the artifact path. This is optional: an unavailable toolchain, compilation failure, or empty map does not block semantic extraction or finalization.

3. Invoke `math-dependency-graph.interface.extract` with the included content, requested scope, semantic-draft destination, root TeX entrypoint, canonical JSON destination, optional label map, and optional HTML destination. The instruction writes the semantic draft, invokes the registered finalizer, validates the completed canonical JSON, and renders only when requested.

4. Treat the finalizer's schema-valid, self-contained canonical JSON as the required completion artifact. It contains its complete graph-relevant MathJax macro closure and presentation metadata. Do not create a macro sidecar or add either input at render time.

5. For a request that starts from existing canonical JSON, or when optional rendering was not already performed by extraction, invoke `math-dependency-graph._rtx.interface.scripts-build-math-dependency-graph` with only the canonical JSON plus the optional HTML path and rendered-view transitive-reduction flag. The builder must not read TeX or rewrite canonical JSON.

6. Distinguish `render-ready/self-contained` from `browser-verified render-clean`. Finalization establishes the former. When browser validation is requested and available, inspect the optional HTML with `window.officinaMathDiagnostics()` and report whether diagnostics are clean, including any unresolved direct or nested TeX commands. Without that check, do not claim browser verification.

7. If interactive inspection is requested, invoke `math-dependency-graph._rtx.interface.scripts-serve-graph` for the generated HTML.

8. If interactive inspection is requested, terminate the returned PID when inspection is complete. If the MCP host owns the gateway with a kill-on-close process container, keep that gateway open during inspection; closing it asynchronously terminates the server instead.

9. Report the semantic draft and canonical paths, the optional HTML and serving address, whether the canonical artifact is render-ready, browser-diagnostics status only when checked, label resolution, embedded macro count, represented scope, and unresolved extraction gaps.

## Responsibility boundary

The `extract` instruction interface owns entity identification, dependency semantics, evidence, uncertainty, draft creation, and the required finalization sequence. This gateway must not duplicate or improvise those rules.

The finalizer owns label application, graph-relevant recursive macro closure, presentation defaults, schema validation, and atomic canonical publication. The builder accepts only self-contained canonical JSON and owns optional HTML rendering without canonical rewrites. The serve interface owns only local presentation. None of these boundaries infer mathematical dependencies.

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
- the semantic draft path
- the optional rendered HTML path
- the number of MathJax macros embedded in canonical JSON
- whether label numbering resolved
- `render-ready/self-contained` status, and `browser-verified render-clean` status only when browser diagnostics ran
- any unresolved TeX commands reported by browser diagnostics
- the represented source and scope
- any extraction gaps or confidence limitations
- the local serving address when serving was requested
