---
name: bib-audit
description: Use when auditing a .bib bibliography file for syntactic validity, style consistency, external metadata verification, or duplicate/version conflicts; or when applying approved corrections to a .bib file or LaTeX project citations.
---

# Bibliography Audit

Category: document-oriented

Conservative bibliography auditor. Default behavior: produce a structured report. Apply transformations only on explicit user approval.

## Invocation

Ask for:
1. The `.bib` file (required).
2. Accompanying `.tex` project files (optional; enables project-aware citation rewrites).
3. House style preferences (optional).

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

### Step 1 — Local validity and formatting (Biber)

Run Biber in tool/validation mode:

```
biber --tool --validate-datamodel <file.bib>
```

This writes two output files alongside the input:
- `<basename>_bibertool.bib` — Biber-normalized version of the `.bib` (used in step 1b)
- `<basename>.blg` — log file with all warnings and errors

Parse the `.blg` log. Map Biber's severity prefixes to the audit classification:
- `ERROR` → `ERROR`
- `WARN` → `WARNING`
- `INFO` → `INFO`

**1a. Syntactic correctness.** Classify issues as:
- `ERROR` — malformed or compilation-breaking (unbalanced braces, duplicate keys, missing required fields, unparseable entries).
- `WARNING` — parseable but suspicious (invalid year, malformed DOI/URL/arXiv/eprint, malformed author list, invalid page range).
- `INFO` — harmless or stylistic.

Cover: malformed entries, unbalanced braces, invalid entry types, missing/malformed citation keys, duplicate keys, duplicate fields, malformed author lists, invalid year fields, malformed DOI/URL/arXiv/eprint/volume/page fields, missing required fields, Biber warnings about ignored or unknown fields.

**1b. Style consistency.** Diff the original `.bib` against `<basename>_bibertool.bib`. Report differences in: field ordering, field-name casing, indentation/spacing, title casing and protected capitalization, author/journal/conference/publisher formatting, DOI/URL/arXiv/page-range formatting, working-paper/preprint/forthcoming treatment, optional-field inclusion, citation-key style.

Infer the file's dominant style; do not impose a universal style unless the user provides one. Biber-normalized output is a proposal, not an automatic replacement.

**Optional — citation-key standardization.** Offer four choices: (1) preserve existing keys, (2) report inconsistencies only, (3) standardize `.bib` only, (4) standardize `.bib` and update `.tex`. Warn if no `.tex` files are provided.

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

**JSON report structure** (`--report report.json`):

Top-level keys: `summary` (aggregate counts, `verified_rate`, `problematic_count`, `timestamp`) and `entries` (one object per entry).

Per-entry fields to read:

| Field | Meaning |
|---|---|
| `key` | Citation key |
| `status` | Verdict string (see table below) |
| `confidence` | Numerical confidence, 0–1 |
| `field_comparisons` | Per-field: `entry_value`, `api_value`, `similarity_score`, `matches` |
| `best_match.doi` | Resolved external DOI (use for Step 2→3 cross-reference) |
| `api_sources_queried` | Which sources were tried |
| `errors` | Any errors during lookup |

Map `status` to audit classification:

| Tool status | Audit classification |
|---|---|
| `verified` | VERIFIED |
| `partial_match` | PROBABLE MATCH |
| `not_found`, `unconfirmed`, `preprint_only` | UNVERIFIED |
| `title_mismatch`, `author_mismatch`, `year_mismatch`, `venue_mismatch`, `arxiv_id_mismatch`, `doi_mismatch`, `hallucinated` | CONFLICT |
| `published_version_exists` | UNVERIFIED — flag for Step 3 (a published version was found; use `bibtex-update` to upgrade) |
| `api_error` | UNVERIFIED — note that lookup failed |

Do not treat UNVERIFIED as fake. Books, unpublished manuscripts, lecture notes, older works, and private drafts may be real even when no external match is found.

---

### Step 3 — Duplicate and version audit

Run the similarity script to get a ranked candidate list:

```
python3 ~/.claude/skills/bib-audit/scripts/bib-similarity.py <file.bib> [--threshold 0.3]
```

The script compares every pair of entries and outputs JSON. For each pair it reports:
- `score` (0–1) and `confidence` (`EXACT` / `HIGH` / `MEDIUM` / `LOW`)
- per-field signal scores: `doi`/`eprint`/`isbn` (identifier match), `title` (token Jaccard), `author` (last-name Jaccard), `year`, `venue`
- `EXACT` means a shared DOI, eprint, or ISBN was found — treat these as definite duplicates regardless of other fields

The threshold is intentionally loose (default 0.3) to avoid missing candidates. **Known limitation:** the script only checks `doi`, `eprint`, `arxivid`, and `isbn` fields for identifier matches. ArXiv IDs embedded in `howpublished`, `note`, or `url` as free text are also extracted and compared (see script), but entries that store the arXiv ID only in unstructured text fields may still be missed — prefer the `eprint` field for arXiv identifiers. Use the candidate list as a checklist, not a classification.

After running the script, also cross-check with Step 2 results: collect `best_match.doi` from each entry's JSON object. If two entries share the same non-empty `best_match.doi`, treat that pair as `EXACT` in Step 3 regardless of what the script reported — this catches cases where the same DOI is present but formatted differently in the two `.bib` entries. Also flag any entry with status `published_version_exists` as a version-pair candidate for Step 3.

For each candidate pair (or group), determine the relationship:
- **exact duplicate** — identical or near-identical entries, same work, no meaningful difference
- **likely duplicate** — same work, minor metadata differences
- **version pair** — same work, different publication stages (preprint/journal, conference/journal, draft/final)
- **uncertain** — insufficient evidence; flag for manual review

For each duplicate/version group, report: grouped entries, relationship type, proposed canonical entry, proposed action, evidence (script score + signals, identifier match, bibtex-check cross-reference).

Default canonical preference:
```
journal article
> conference/proceedings version
> accepted/forthcoming with final metadata
> latest preprint or working paper
> older draft or manuscript
```

Preserve non-canonical versions when they differ in content, appendix material, author list, or title, when the version relationship is uncertain, or when there is a plausible reason to cite them separately.

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

Safe transformations may be approved in bulk ("apply all safe fixes") and produce a single combined diff/change log. Even so, always show the diff before writing.

**Review-required transformations** (each requires individual approval):
- change titles, authors, years, venues;
- add missing publication metadata;
- replace working-paper with published-version metadata (use `bibtex-update` — see below);
- delete or merge entries (use `bibtex-update --dedupe` for DOI/title-based deduplication);
- change citation keys;
- aggressive house-style rewrites.

**`bibtex-update` for preprint upgrades and deduplication:**
```
# Preview changes without writing
bibtex-update <file.bib> --dry-run --verbose

# Replace preprints with published versions
bibtex-update <file.bib> -o updated.bib

# Merge duplicates by DOI or normalized title+authors
bibtex-update <file.bib> --dedupe -o deduped.bib

# Fill missing required/recommended fields from external APIs
bibtex-update <file.bib> --fill-fields -o filled.bib
```
Always run `--dry-run` first and show the diff to the user before applying. Use `--report report.jsonl` to produce a change log.

**Project-aware transformations** (only when `.tex` files provided):
- update citation keys in `.tex` files;
- replace citations to merged entries;
- deduplicate keys within `\cite{…}` commands;
- verify citation consistency after changes.

Only rewrite citation commands, not arbitrary text. Examples for `y → x`:

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

Per-issue fields: entry key, issue type, severity, explanation, suggested action, evidence source, confidence (if available), action class (automatic / approval-required / manual).

When transformations are applied, also produce: cleaned `.bib` file, change log, diff or patch, key-renaming map (if keys changed), merge map (if entries merged), modified `.tex` files (if project-aware transformations performed).

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
