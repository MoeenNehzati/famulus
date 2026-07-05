---
name: pdf-to-markdown
description: Use when converting a research paper PDF to readable text for LLM analysis of technical content.
tools:
  - marker_single
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: document-oriented

Dependencies: none

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Script Interfaces:

Use the installed `dispatcher` command for this skill's script interfaces:
- `scripts-check-marker-models` — Check whether required Marker/Surya models are downloaded and cached locally.
  - `dispatcher --caller-skill pdf-to-markdown pdf-to-markdown scripts-check-marker-models ...`
- `scripts-fetch-arxiv-source` — Download and extract the LaTeX source tarball for a paper from arXiv.
  - `dispatcher --caller-skill pdf-to-markdown pdf-to-markdown scripts-fetch-arxiv-source <arxiv-id> <output-dir>`
<!-- END BLUEPRINT INTERFACES -->
# PDF to Markdown

## Overview

Convert a research paper to LLM-readable text. Prefer LaTeX source over PDF conversion — LaTeX preserves math, structure, and cross-references that PDF-to-text destroys.

## Step 1 — Find LaTeX source (do this first)

**Input required:** paper title and authors. arXiv ID if known.

### arXiv (check first — highest hit rate for CS/math/econ)

1. No arXiv ID? Search `arxiv [title] [authors]` via WebSearch to find one.
2. Run the `scripts-fetch-arxiv-source` interface with `<arxiv-id> <output-dir>`.
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
