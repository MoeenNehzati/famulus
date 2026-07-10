"""Aggregation seam: wires per-host parsers into one generic list.

This is the one place allowed to statically import the host-specific
parser files so that the exported machine interfaces stay host-neutral.
The package is executed as a real module (`python -m scripts.scan` /
`python -m scripts.calibrate`), so normal relative imports work without
path surgery.
"""

from .claude_parser import ClaudeParser
from .codex_parser import CodexParser

PARSERS = [ClaudeParser(), CodexParser()]
parsers = PARSERS
