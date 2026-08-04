"""MkDocs lifecycle hook for refreshing the bounded staged source tree.

The command wrapper performs the initial assembly, including the expensive
interactive blueprint graph.  Subsequent MkDocs live-reload builds call this
hook to refresh Markdown and curated SVG assets while preserving that graph.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPO_ROOT, REPO_ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from docs_tooling.site import sync_published_docs


def on_pre_build(config: Any) -> None:
    """Refresh published sources before MkDocs renders or live-reloads them."""

    sync_published_docs(REPO_ROOT, Path(config["docs_dir"]))
