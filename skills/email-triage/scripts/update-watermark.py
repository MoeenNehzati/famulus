#!/usr/bin/env python3
"""Write the current datetime to the triage watermark file."""
from datetime import datetime
from pathlib import Path

WATERMARK = Path("last_run").expanduser()
now = datetime.now().astimezone()
WATERMARK.write_text(now.isoformat())
print(f"Watermark updated: {now.isoformat()}")
