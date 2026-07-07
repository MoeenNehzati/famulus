"""Aggregation seam: wires per-host parsers into one generic list.

Exempt from validators/platform_neutral.py unconditionally (see
references/skill-guidelines.md, guideline 13) -- this is the one place
allowed to statically import the host-specific parser files, so that
scan.py and calibrate.py can stay fully generic and never name a host
themselves.

The sys.path.insert below uses this file's OWN __file__, not whatever
sys.path state the caller happened to have -- so the plain imports below
resolve correctly regardless of how this file itself got loaded (direct
script execution, spec_from_file_location from a test, etc.). See
scan.py's _load_parsers() for why this file can't just be reached via a
normal relative import.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from claude_parser import ClaudeParser
from codex_parser import CodexParser

parsers = [ClaudeParser(), CodexParser()]
