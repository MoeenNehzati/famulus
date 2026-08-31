---
name: pdf-to-markdown
description: >-
  Use when research-paper analysis requires readable source or text that is not already available. Do not use for generic non-research PDFs.
tools:
  - marker_single
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: research; topics: scholarly-documents; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `pdf-to-markdown.source.gateway -> pdf-to-markdown._rtx.interface.scripts-check-marker-models@1`
- `pdf-to-markdown.source.gateway -> pdf-to-markdown._rtx.interface.scripts-fetch-arxiv-source@1`

Public Interfaces:
- `pdf-to-markdown.interface.default`
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `pdf-to-markdown._rtx.interface.scripts-check-marker-models` — Check whether required Marker/Surya models are downloaded and cached locally.
  - Caller: `pdf-to-markdown`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `pdf-to-markdown._rtx.interface.scripts-fetch-arxiv-source` — Download and extract the LaTeX source tarball for a paper from arXiv.
  - Caller: `pdf-to-markdown`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["arxiv-id", "output-dir"], "stdin": null}
    Required options: []; positional arity: 1..2; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `pdf-to-markdown.interface.default` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
# PDF to Markdown

## Overview

Convert a research paper to LLM-readable text. Prefer LaTeX source over PDF conversion — LaTeX preserves math, structure, and cross-references that PDF-to-text destroys.

## Step 1 — Find LaTeX source (do this first)

**Input required:** paper title and authors. arXiv ID if known.

### arXiv (check first — highest hit rate for CS/math/econ)

1. No arXiv ID? Search `arxiv [title] [authors]` via WebSearch to find one.
2. Run the `scripts-fetch-arxiv-source` interface with `<arxiv-id> [<output-dir>]`.
   - Script downloads `arxiv.org/src/<id>`, extracts, lists `.tex` files found.
   - If arXiv returns HTML instead of a tarball, the paper has no source — move on.
3. Root file is usually `main.tex`; if absent, scan for the file that `\begin{document}`.
4. **Done — hand `.tex` files to the LLM.**

### If not on arXiv — check in order

- **Author GitHub:** WebSearch `"[title]" site:github.com` — look for a repo with `.tex` files
- **Author personal/institutional page:** WebSearch `[title] [author] latex source`
- **OpenReview** (`openreview.net/search?term=[title]`) — Attachments tab, look for source zip
- **ACL Anthology** (`aclanthology.org`) — NLP venues; links to arXiv preprint when one exists

If LaTeX source found anywhere: download, extract, done.

## Step 2 — PDF fallback via `marker_single`

If no LaTeX source found, convert the PDF directly.

Only after selecting this PDF/Marker fallback, follow
`setup-python-environment.interface.repair-selected-packages` for this owner's exact
declaration `["marker-pdf"]`. Complete the full Task 2 fingerprint procedure; on any
failure, stop before probing Marker models or running `marker_single`. Source-only success
must not invoke this repair.

**Before running:** check whether models are cached using the `scripts-check-marker-models` interface.
If any models are missing, warn the user: "Running marker will download missing models (~3GB total to `~/.cache/datalab/models/`). Proceed?" Do not run `marker_single` until confirmed.

**Standard invocation** (good typeset PDF, no extra cost):
```bash
marker_single paper.pdf --output_dir ./output --disable_image_extraction
```
Always specify `--output_dir` — the default buries output in the anaconda site-packages directory.

**Math-heavy paper** (ask user before using — slower, better equation/table output):
```bash
marker_single paper.pdf \
  --output_dir ./output \
  --disable_image_extraction \
  --llm_service <configured-marker-llm-service> \
  --use_llm \
  --redo_inline_math \
  --highres_image_dpi 300
```
Requires a configured Marker LLM backend, such as an Anthropic-backed Marker
service with `ANTHROPIC_API_KEY` in the environment or the matching API-key
flag. Uses the selected provider API (pay-per-token where applicable).

**Other useful flags:**
- `--page_range 0,5-10` — convert specific pages only
- `--output_format json` — structured output instead of flat markdown
- `--disable_ocr` — skip OCR for faster conversion (safe if PDF has selectable text)
