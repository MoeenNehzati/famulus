---
name: bib-audit
description: Use when auditing a .bib bibliography file for syntactic validity, style consistency, external metadata verification, or duplicate/version conflicts; or when applying approved corrections to a .bib file or LaTeX project citations.
---

# Bibliography Audit

Category: document-oriented

Conservative bibliography auditor. Default behavior: produce a structured report. Apply transformations only on explicit user approval.

## Test files

Ready-to-use test fixtures live in `~/.claude/skills/bib-audit/test/`:
- `test_biblatex.bib` — biblatex+Biber path: exact duplicate pair, preprint/journal version pair, missing fields, bad DOI, formatting inconsistencies
- `test_natbib.bib` — BibTeX+natbib path: exact duplicate pair, year conflict, missing author, page-dash issues, mixed field casing
- `test_modification.bib` + `test_modification.tex` — modification layer: deletion, merge, `.tex` citation rewrite, deduplication edge cases
- `test_natbib_commands.tex` — all natbib citation command variants with optional args, multi-key, deduplication
- `test_multifile_main.tex` + `test_multifile_section.tex` — multi-file citation rewrite

## Invocation

Ask for:
1. The `.bib` file (required).
2. **LaTeX backend** (required): biblatex+Biber, or BibTeX+natbib? This determines Step 1.
3. Accompanying `.tex` project files (optional; enables project-aware citation rewrites).
4. House style preferences (optional).

## Global rules

- **Reports before transformations.** Default behavior is a report with suggested actions, no file edits.
- **Optional steps require explicit user approval.** Never perform citation-key standardization, aggressive formatting, duplicate merging, or project-wide rewrites without approval.
- **Citation-key changes are project-aware when `.tex` files are provided.**
  - `old → new`: update all `\cite{old}` → `\cite{new}`.
  - Merged `y → x`: replace `y` citations with `x`; deduplicate citation commands.
  - Entry deleted without replacement: check for citations, ask before proceeding.
- **Do not infer destructive changes from missing information.** If a tool report lacks a clear replacement or merge target, ask or leave for manual review.
- **Warn before key changes when no `.tex` files are provided.**

## Workflow

### Step 1 — Local validity and formatting

#### biblatex + Biber

```
biber --tool --validate-datamodel <file.bib>
```

Produces `<basename>_bibertool.bib` (normalized output) and `<basename>.blg` (log). Parse the `.blg` log; map `ERROR`/`WARN`/`INFO` prefixes directly to the audit classification.

**1a.** Report all log entries: duplicate keys, missing required fields (biblatex spec), malformed entries, unknown fields, malformed author/DOI/year/page fields.

**1b.** Diff original `.bib` against `<basename>_bibertool.bib`. Report differences in field ordering, field-name casing, indentation, page-range dashes, title capitalization, author/journal/publisher formatting, DOI/URL/arXiv formatting, citation-key style. Infer dominant style from the file; do not impose a universal one.

---

#### BibTeX + natbib

Do not run `biber --tool` — it uses biblatex field names (`journaltitle`, `date`) and will spuriously flag valid BibTeX fields (`journal`, `year`).

```
python3 ~/.claude/skills/bib-audit/scripts/bib-validate-bibtex.py <file.bib>
```

Output is JSON. Read `parse_errors` first (file-level failures), then `entries[*].issues`. Each issue has `level` (ERROR/WARNING/INFO), `field`, and `message`.

**1a.** Report all ERRORs and WARNINGs. Required fields are checked against the BibTeX spec (e.g. `@article` needs `author`, `title`, `journal`, `year` — not `journaltitle`/`date`).

**1b.** No normalized reference output is available. Read the file directly and report style inconsistencies: field-name casing (BibTeX convention: lowercase), field ordering within entries, indentation and spacing, page-range dashes, title brace-protection, citation-key style.

---

**Optional — citation-key standardization** (both backends)**:** Offer four choices: (1) preserve existing keys, (2) report inconsistencies only, (3) standardize `.bib` only, (4) standardize `.bib` and update `.tex`. Warn if no `.tex` files are provided.

---

### Step 2 — External metadata verification (bibtex-check)

Install via `pip install bibtex-updater`. Run:

```
bibtex-check <file.bib> --report report.json --non-generative --mailto <your-email>
```

- `--non-generative` disables LLM calls; use it for reproducibility and policy compliance.
- `--mailto` registers a contact address with CrossRef/OpenAlex polite pools; improves rate limits.
- `--skip-books`, `--skip-working-papers` skip entry types that rarely resolve externally.
- If the tool fails or times out on an entry, mark it `UNVERIFIED` and continue; note which entries could not be checked.

Per-entry: read `status` (see mapping below), `field_comparisons` (has `entry_value`/`api_value`/`similarity_score`/`matches` per field), and `best_match.doi` (for Step 3 cross-reference). Top-level `summary` has aggregate counts.

Map `status` to audit classification:

| Tool status | Audit classification |
|---|---|
| `verified` | VERIFIED |
| `partial_match` | PROBABLE MATCH |
| `not_found`, `unconfirmed`, `preprint_only` | UNVERIFIED |
| `title_mismatch`, `author_mismatch`, `year_mismatch`, `venue_mismatch`, `arxiv_id_mismatch`, `doi_mismatch`, `hallucinated` | CONFLICT |
| `doi_not_found` | CONFLICT — DOI is present but does not resolve; likely fabricated or mistyped |
| `published_version_exists` | UNVERIFIED — flag for Step 3 (a published version was found; use `bibtex-update` to upgrade). Note: bibtex-check may instead return `verified` for a preprint that resolves via arXiv even when a published version exists — the similarity script may catch such version pairs that this status misses. |
| `api_error` | UNVERIFIED — note that lookup failed |

Do not treat UNVERIFIED as fake. Books, unpublished manuscripts, lecture notes, older works, and private drafts may be real even when no external match is found.

---

### Step 3 — Duplicate and version audit

Run the similarity script to get a ranked candidate list:

```
python3 ~/.claude/skills/bib-audit/scripts/bib-similarity.py <file.bib> [--threshold 0.3]
```

Output JSON: each pair has `score` (0–1), `confidence` (EXACT/HIGH/MEDIUM/LOW), and per-field signals (`doi`/`eprint`/`isbn` identifier match, `title`/`author`/`year`/`venue` soft scores). `EXACT` = shared identifier; treat as definite duplicate. Threshold 0.3 is loose by design — use the list as a checklist, not a verdict.

Known limitation: arXiv IDs in `howpublished`/`note`/`url` free text are extracted as a fallback, but prefer `eprint` field for reliable identifier matching.

Cross-check with Step 2: if two entries share the same `best_match.doi`, treat them as EXACT regardless of what the script reported. Skip entries with status `not_found` or `unconfirmed` when doing this cross-check — their `best_match.doi` may be a spurious near-hit, not a confirmed match. Flag any entry with `published_version_exists` status as a version-pair candidate.

For each candidate pair (or group), determine the relationship:
- **exact duplicate** — identical or near-identical entries, same work, no meaningful difference
- **likely duplicate** — same work, minor metadata differences
- **version pair** — same work, different publication stages (preprint/journal, conference/journal, draft/final)
- **uncertain** — insufficient evidence; flag for manual review

For each duplicate/version group, report: grouped entries, relationship type, proposed canonical entry, proposed action, evidence (script score + signals, identifier match, bibtex-check cross-reference).

Default canonical preference: journal article > conference/proceedings > accepted/forthcoming with final metadata > latest preprint > older draft. Preserve non-canonical versions when content, author list, or title differ, or when there is a plausible reason to cite them separately.

**Optional — merge or delete entries.** If merging `y → x`: keep `x`, delete `y`, record the merge, update `.tex` citations, deduplicate citation commands. If deleting without replacement and `.tex` files exist: check for citations and ask what to do. If no `.tex` files were provided, warn the user explicitly that the deletion or key change may silently break citations in any accompanying `.tex` files. Do not infer a merge target destructively from incomplete information.

---

## Modification layer

Invoke only when the user approves specific transformations. Never for audit-only steps.

**Safe transformations** (low-risk; may be approved in a single batch):
- normalize whitespace and indentation;
- sort fields within entries;
- remove exact duplicate fields;
- normalize DOI, page-range dash, URL/arXiv field formatting;
- apply consistent field ordering;
- remove empty fields.

Safe transformations may be approved in bulk; produce a single combined diff before writing.

**Review-required transformations** (each requires individual approval):
- change titles, authors, years, venues;
- add missing publication metadata;
- replace working-paper with published-version metadata (use `bibtex-update` — see below);
- delete or merge entries (use `bibtex-update --dedupe` for DOI/title-based deduplication);
- change citation keys;
- aggressive house-style rewrites.

**`bibtex-update`** for preprint upgrades and deduplication. Always preview first (`--dry-run` requires `-o` even in preview mode). Review these side effects before approving — flag each to the user:
- Author names may be reformatted from Last-First to First-Last.
- `eprint` fields may be silently dropped.
- Titles may gain trailing punctuation or changed capitalisation from the API.
- Top-of-file comment blocks are stripped from the output.
- New page ranges introduced by upgrades may use single hyphens rather than en-dashes.
- **Do not run `--dedupe` on a file that contains both exact duplicates and version pairs without careful preview.** The combination can produce hybrid entries with contradictory fields (e.g. `@article` with simultaneous `journal`, `booktitle`, and `howpublished`). Run deduplication only after resolving version pairs separately, or review the output entry-by-entry before writing.
```
bibtex-update <file.bib> --dry-run --verbose -o /tmp/preview.bib
bibtex-update <file.bib> [--dedupe] [--fill-fields] -o updated.bib --report changes.jsonl
```

**Project-aware transformations** (only when `.tex` files provided):
- update citation keys in `.tex` files;
- replace citations to merged entries;
- deduplicate keys within `\cite{…}` commands;
- verify citation consistency after changes.

Rewrites apply to any citation command of the form `\CMDNAME[...]{keys}` — including all natbib variants (`\citep`, `\citet`, `\citealt`, `\citealp`, `\citeauthor`, `\citeyear`) — not only `\cite`. Optional arguments `[post]` and `[pre][post]` are preserved unchanged. Examples for `y → x`:

```latex
\cite{y}        →  \cite{x}
\cite{a,y,z}    →  \cite{a,x,z}
\cite{x,y,z}    →  \cite{x,z}      ← deduplicate; never \cite{x,x,z}
```

---

## Output

Default audit report structure:
1. Summary
2. Critical errors
3. Local validity and formatting issues
4. External verification results
5. Duplicate/version groups
6. Suggested actions
7. Optional transformations available

**Summary section contents:** total entries parsed; ERROR / WARNING / INFO counts from Step 1; verification counts from Step 2 (VERIFIED N/total, CONFLICT N, UNVERIFIED N, api_error N); duplicate candidate pairs by confidence (EXACT N, HIGH N, MEDIUM N, LOW N); count of entries with `published_version_exists`.

Per-issue: entry key, issue type, severity, explanation, suggested action, evidence source, action class (automatic / approval-required / manual).

After transformations: cleaned `.bib`, diff, change log, key-renaming map (if keys changed), merge map (if entries merged), modified `.tex` files (if project-aware).

### Minimal example

```
## Summary
Entries: 47 | Errors: 2 | Warnings: 8 | Verified: 31/47 | Duplicate candidates: 3 pairs

## Critical errors
- smith2020 (@article): missing required field `journal`. [ERROR, manual]

## Duplicate/version groups
Group 1 — version pair (HIGH, score 0.83)
  chen2021 (@misc, arXiv preprint) ↔ chen2022 (@article, Journal of ML)
  Evidence: identical title tokens, identical authors, year diff 1 (score 0.82), no shared identifier
  Canonical: chen2022 (published journal article)
  Proposed action: merge chen2021 → chen2022 [approval required]
```
