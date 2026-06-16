#!/usr/bin/env python3
"""
bib-validate-bibtex.py — syntactic and required-field validation for BibTeX/natbib .bib files.

Use for projects with BibTeX+natbib backend. Do NOT use for biblatex projects (use biber --tool instead).

Usage:
  python3 bib-validate-bibtex.py <file.bib>

Output: JSON with errors, warnings, and infos per entry.

Requires bibtexparser (pip install bibtexparser); falls back to a regex parser.
"""

import sys
import json
import re
import argparse

try:
    import bibtexparser
    from bibtexparser.bparser import BibTexParser
    HAS_BIBTEXPARSER = True
except ImportError:
    HAS_BIBTEXPARSER = False

# ---------------------------------------------------------------------------
# BibTeX required fields (standard BibTeX spec, not biblatex)
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {
    "article":      [{"author"}, {"title"}, {"journal"}, {"year"}],
    "book":         [{"author", "editor"}, {"title"}, {"publisher"}, {"year"}],
    "booklet":      [{"title"}],
    "conference":   [{"author"}, {"title"}, {"booktitle"}, {"year"}],
    "inbook":       [{"author", "editor"}, {"title"}, {"chapter", "pages"}, {"publisher"}, {"year"}],
    "incollection": [{"author"}, {"title"}, {"booktitle"}, {"publisher"}, {"year"}],
    "inproceedings":[{"author"}, {"title"}, {"booktitle"}, {"year"}],
    "manual":       [{"title"}],
    "mastersthesis":[{"author"}, {"title"}, {"school"}, {"year"}],
    "misc":         [],
    "phdthesis":    [{"author"}, {"title"}, {"school"}, {"year"}],
    "proceedings":  [{"title"}, {"year"}],
    "techreport":   [{"author"}, {"title"}, {"institution"}, {"year"}],
    "unpublished":  [{"author"}, {"title"}, {"note"}],
}

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def load_bib(path):
    if HAS_BIBTEXPARSER:
        bp = BibTexParser(common_strings=True)
        bp.ignore_nonstandard_types = False
        with open(path, encoding="utf-8", errors="replace") as f:
            db = bibtexparser.load(f, parser=bp)
        return db.entries, []
    else:
        entries, parse_errors = _fallback_parse(path)
        return entries, parse_errors


def _fallback_parse(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    entries = []
    errors = []
    for block in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", text, re.DOTALL):
        entry = {"ENTRYTYPE": block.group(1).lower(), "ID": block.group(2).strip()}
        for fm in re.finditer(r'(\w+)\s*=\s*[\{\"](.*?)[\}\"]\s*[,\n]', block.group(3), re.DOTALL):
            entry[fm.group(1).lower()] = fm.group(2).strip()
        entries.append(entry)
    if not entries and text.strip():
        errors.append({"level": "ERROR", "key": None, "field": None,
                        "message": "No entries parsed — file may be malformed or use unsupported syntax"})
    return entries, errors

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

VALID_ENTRY_TYPES = set(REQUIRED_FIELDS.keys())

PAGE_RANGE_RE = re.compile(r"^\d+\s*-{1,2}\s*\d+$|^[ivxlcdmIVXLCDM]+\s*-{1,2}\s*[ivxlcdmIVXLCDM]+$")
YEAR_RE = re.compile(r"^\d{4}$")
DOI_RE = re.compile(r"^10\.\d{4,}/\S+$")


def validate_entry(entry):
    issues = []
    key = entry.get("ID", "?")
    etype = entry.get("ENTRYTYPE", "").lower()
    fields = {k.lower(): v for k, v in entry.items() if k not in ("ID", "ENTRYTYPE")}

    # Unknown entry type
    if etype not in VALID_ENTRY_TYPES and etype not in ("string", "preamble", "comment"):
        issues.append({"level": "WARNING", "field": "ENTRYTYPE",
                        "message": f"Non-standard entry type '@{etype}'"})

    # Required fields
    for req_set in REQUIRED_FIELDS.get(etype, []):
        if not any(f in fields for f in req_set):
            label = " or ".join(sorted(req_set))
            issues.append({"level": "ERROR", "field": label,
                            "message": f"Missing required field: {label}"})

    # Duplicate fields (can't detect from dict; noted as limitation)

    # Year
    if "year" in fields:
        y = fields["year"].strip()
        if not YEAR_RE.match(y):
            issues.append({"level": "WARNING", "field": "year",
                            "message": f"Suspicious year value: '{y}'"})
    elif "date" in fields:
        issues.append({"level": "INFO", "field": "date",
                        "message": "'date' field found — this is a biblatex field; BibTeX uses 'year'"})

    # journal vs journaltitle
    if "journaltitle" in fields and "journal" not in fields:
        issues.append({"level": "INFO", "field": "journaltitle",
                        "message": "'journaltitle' is a biblatex field; BibTeX uses 'journal'"})

    # DOI
    if "doi" in fields:
        doi = fields["doi"].strip()
        doi_clean = re.sub(r"^https?://doi\.org/", "", doi)
        doi_clean = re.sub(r"^doi:\s*", "", doi_clean)
        if not DOI_RE.match(doi_clean):
            issues.append({"level": "WARNING", "field": "doi",
                            "message": f"Malformed DOI: '{doi}'"})

    # Pages
    if "pages" in fields:
        p = fields["pages"].strip()
        if "-" in p and "--" not in p:
            issues.append({"level": "INFO", "field": "pages",
                            "message": f"Page range uses single hyphen: '{p}' — consider en-dash '--'"})

    # Author
    if "author" in fields:
        a = fields["author"]
        if "{" not in a and re.search(r"\band\b", a, re.IGNORECASE) is None and "," not in a and " " in a.strip():
            issues.append({"level": "INFO", "field": "author",
                            "message": "Author field may be a single name with no 'and' separators — check if multiple authors are missing"})

    return issues


def check_duplicate_keys(entries):
    seen = {}
    dupes = []
    for e in entries:
        k = e.get("ID", "")
        if k in seen:
            dupes.append(k)
        seen[k] = True
    return dupes

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Validate a BibTeX/natbib .bib file for syntax and required fields. "
                    "Do not use for biblatex projects.")
    ap.add_argument("bibfile")
    args = ap.parse_args()

    entries, parse_errors = load_bib(args.bibfile)

    if not HAS_BIBTEXPARSER:
        parse_errors.append({"level": "WARNING", "key": None, "field": None,
                              "message": "bibtexparser not found; using fallback parser — some issues may be missed"})

    dup_keys = check_duplicate_keys(entries)
    results = []

    for e in entries:
        key = e.get("ID", "?")
        entry_issues = validate_entry(e)
        for dk in dup_keys:
            if key == dk:
                entry_issues.insert(0, {"level": "ERROR", "field": "ID",
                                         "message": f"Duplicate citation key '{key}'"})
        results.append({
            "key": key,
            "type": e.get("ENTRYTYPE", "?"),
            "issues": entry_issues,
        })

    counts = {"ERROR": 0, "WARNING": 0, "INFO": 0}
    for r in results:
        for issue in r["issues"]:
            counts[issue["level"]] = counts.get(issue["level"], 0) + 1
    for e in parse_errors:
        counts[e.get("level", "ERROR")] = counts.get(e.get("level", "ERROR"), 0) + 1

    print(json.dumps({
        "bibfile": args.bibfile,
        "backend": "bibtex/natbib",
        "total_entries": len(entries),
        "summary": counts,
        "parse_errors": parse_errors,
        "entries": results,
    }, indent=2))


if __name__ == "__main__":
    main()
