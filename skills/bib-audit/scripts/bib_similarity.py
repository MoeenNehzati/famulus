#!/usr/bin/env python3
"""
bib_similarity.py — pairwise duplicate/version candidate detection for .bib files.

Usage:
  python3 bib_similarity.py <file.bib> [--threshold 0.4]

Output: JSON. Pairs sorted by score descending.

Requires bibtexparser (pip install bibtexparser); falls back to a regex parser.
"""

import sys
import json
import re
import math
import argparse
from itertools import combinations

try:
    import bibtexparser
    from bibtexparser.bparser import BibTexParser
    HAS_BIBTEXPARSER = True
except ImportError:
    HAS_BIBTEXPARSER = False

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def load_bib_bibtexparser(path):
    bp = BibTexParser(common_strings=True)
    bp.ignore_nonstandard_types = False
    with open(path, encoding="utf-8", errors="replace") as f:
        db = bibtexparser.load(f, parser=bp)
    return db.entries  # list of dicts; 'ID' and 'ENTRYTYPE' always present


def load_bib_fallback(path):
    """Regex-based fallback. Less accurate for complex entries."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    entries = []
    # Match top-level @TYPE{KEY, ... } blocks
    for block in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", text, re.DOTALL):
    # for block in re.finditer(r'@(\w+)\s*\{([^,]+),(.*?)\n\}', text, re.DOTALL):
        entry = {"ENTRYTYPE": block.group(1).lower(), "ID": block.group(2).strip()}
        body = block.group(3)
        for fm in re.finditer(r'(\w+)\s*=\s*[\{\"](.*?)[\}\"]\s*[,\n]', body, re.DOTALL):
            entry[fm.group(1).lower()] = fm.group(2).strip()
        entries.append(entry)
    return entries


def load_bib(path):
    if HAS_BIBTEXPARSER:
        return load_bib_bibtexparser(path)
    print("Warning: bibtexparser not found; using fallback parser (less accurate for complex entries).", file=sys.stderr)
    return load_bib_fallback(path)

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

STOPWORDS = {"a", "an", "the", "on", "in", "of", "for", "and", "or", "with",
             "to", "from", "by", "at", "is", "are", "via", "using"}


def strip_latex(s):
    """Remove LaTeX braces and simple commands."""
    s = re.sub(r"\\[a-zA-Z]+\s*\{([^}]*)\}", r"\1", s)  # \cmd{arg} → arg
    s = re.sub(r"\\[a-zA-Z]+\s*", " ", s)                # \cmd → space
    s = re.sub(r"[{}]", "", s)
    return s


def normalize_title(s):
    if not s:
        return ""
    s = strip_latex(s).lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = [w for w in s.split() if w not in STOPWORDS and len(w) > 1]
    return " ".join(tokens)


def normalize_authors(s):
    """Return a set of normalized last names."""
    if not s:
        return set()
    parts = re.split(r"\s+and\s+", s, flags=re.IGNORECASE)
    lastnames = set()
    for p in parts:
        p = strip_latex(p).strip()
        if "," in p:
            ln = p.split(",")[0]
        else:
            words = p.split()
            ln = words[-1] if words else p
        ln = re.sub(r"[^a-z]", "", ln.lower())
        if ln:
            lastnames.add(ln)
    return lastnames


def normalize_identifier(field, value):
    v = value.strip().lower()
    if field == "doi":
        v = re.sub(r"^https?://doi\.org/", "", v)
        v = re.sub(r"^doi:\s*", "", v)
    return v


def extract_arxiv_from_text(text):
    """Extract a normalized arXiv ID from free text (howpublished, note, url)."""
    if not text:
        return None
    m = re.search(r'(?:arXiv:|arxiv\.org/abs/)(\d{4}\.\d{4,5})(?:v\d+)?', text, re.IGNORECASE)
    return m.group(1) if m else None


def get_identifiers(entry):
    result = {}
    for field in ("doi", "eprint", "arxivid", "isbn"):
        raw = entry.get(field, "").strip()
        if raw:
            result[field] = normalize_identifier(field, raw)
    # Fall back to free-text arXiv extraction if no structured eprint/arxivid
    if "eprint" not in result and "arxivid" not in result:
        for field in ("howpublished", "note", "url"):
            arxiv_id = extract_arxiv_from_text(entry.get(field, ""))
            if arxiv_id:
                result["eprint"] = arxiv_id
                break
    return result

# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def jaccard(set_a, set_b):
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def token_jaccard(str_a, str_b):
    return jaccard(set(str_a.split()), set(str_b.split()))


def score_pair(ea, eb):
    signals = {}

    # Hard signals: shared identifiers
    ids_a = get_identifiers(ea)
    ids_b = get_identifiers(eb)
    hard_match_fields = []
    for field in ("doi", "eprint", "arxivid", "isbn"):
        if field in ids_a and field in ids_b and ids_a[field] == ids_b[field]:
            hard_match_fields.append(field)
            signals[field] = {"match": True, "value": ids_a[field], "score": 1.0}
        elif field in ids_a or field in ids_b:
            signals[field] = {"match": False, "score": 0.0}

    if hard_match_fields:
        return 1.0, signals, "EXACT"

    # Soft signals
    title_a = normalize_title(ea.get("title", ""))
    title_b = normalize_title(eb.get("title", ""))
    t_score = token_jaccard(title_a, title_b)
    signals["title"] = {"score": round(t_score, 3)}

    auth_a = normalize_authors(ea.get("author", ""))
    auth_b = normalize_authors(eb.get("author", ""))
    a_score = jaccard(auth_a, auth_b)
    signals["author"] = {"score": round(a_score, 3)}

    y_a = ea.get("year", "").strip()
    y_b = eb.get("year", "").strip()
    try:
        diff = abs(int(y_a) - int(y_b)) if (y_a and y_b) else None
        y_score = math.exp(-diff / 5) if diff is not None else 0.0
    except ValueError:
        diff = None
        y_score = 0.0
    signals["year"] = {"a": y_a, "b": y_b, "diff": diff, "score": round(y_score, 3)}

    venue_a = normalize_title(ea.get("journal", ea.get("booktitle", "")))
    venue_b = normalize_title(eb.get("journal", eb.get("booktitle", "")))
    v_score = token_jaccard(venue_a, venue_b) if (venue_a and venue_b) else 0.0
    signals["venue"] = {"score": round(v_score, 3)}

    score = 0.50 * t_score + 0.25 * a_score + 0.10 * y_score + 0.15 * v_score
    score = round(score, 3)

    if score >= 0.7:
        confidence = "HIGH"
    elif score >= 0.3:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return score, signals, confidence

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Detect duplicate/version candidate pairs in a .bib file.")
    ap.add_argument("bibfile")
    ap.add_argument("--threshold", type=float, default=0.3,
                    help="Minimum pair score to report (default: 0.3). "
                         "Set low to avoid missing candidates; LLM filters further.")
    args = ap.parse_args()

    entries = load_bib(args.bibfile)

    pairs = []
    for ea, eb in combinations(entries, 2):
        score, signals, confidence = score_pair(ea, eb)
        if score >= args.threshold:
            pairs.append({
                "key_a": ea["ID"],
                "key_b": eb["ID"],
                "type_a": ea.get("ENTRYTYPE", "?"),
                "type_b": eb.get("ENTRYTYPE", "?"),
                "score": score,
                "confidence": confidence,
                "signals": signals,
            })

    pairs.sort(key=lambda p: p["score"], reverse=True)

    print(json.dumps({
        "bibfile": args.bibfile,
        "threshold": args.threshold,
        "total_entries": len(entries),
        "pairs_found": len(pairs),
        "pairs": pairs,
    }, indent=2))


if __name__ == "__main__":
    main()
