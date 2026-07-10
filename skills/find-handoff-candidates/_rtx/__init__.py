"""Aggregation seam: wires per-host parsers into one generic list.

This is the one place allowed to statically import the host-specific
parser files so that the exported machine interfaces stay host-neutral.
The package is executed as a real module (`python -m _rtx._handoff_scan` /
`python -m _rtx._handoff_calibrate`), so normal relative imports work without
path surgery.
"""

from ._claude_parser import ClaudeParser
from ._codex_parser import CodexParser

PARSERS = [ClaudeParser(), CodexParser()]
parsers = PARSERS
