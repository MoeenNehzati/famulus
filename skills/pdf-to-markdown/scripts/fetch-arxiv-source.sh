#!/usr/bin/env bash
# Usage: fetch-arxiv-source.sh <arxiv-id> <output-dir>
# Downloads and extracts the LaTeX source tarball from arXiv.
# Output: extracted files in <output-dir>; lists .tex files found.
# Exit 1 if arXiv returns HTML (no source available for this paper).
set -euo pipefail

ID=${1:?Usage: fetch-arxiv-source.sh <arxiv-id> <output-dir>}
OUTDIR=${2:-.}

mkdir -p "$OUTDIR"
TARBALL="$OUTDIR/source.tar.gz"

curl -sL "https://arxiv.org/src/$ID" -o "$TARBALL"

# arXiv returns HTML when no source is available
if ! file "$TARBALL" | grep -q "gzip"; then
    rm "$TARBALL"
    echo "No LaTeX source available on arXiv for $ID (PDF-only submission)." >&2
    exit 1
fi

tar -xzf "$TARBALL" -C "$OUTDIR"
rm "$TARBALL"

echo "Extracted to: $OUTDIR"
echo "TeX files:"
find "$OUTDIR" -name "*.tex" | sort
