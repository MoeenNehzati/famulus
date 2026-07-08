#!/usr/bin/env python3
"""Serve a local Famulus repo browser with rendered Markdown pages."""
from __future__ import annotations

from pathlib import Path
import argparse
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from docs_tooling.repo_browser import RepoBrowserConfig, run_server


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve a local repo browser for Famulus")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    args = parser.parse_args()
    run_server(RepoBrowserConfig(repo_root=REPO_ROOT, host=args.host, port=args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
