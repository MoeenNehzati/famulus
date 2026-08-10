#!/usr/bin/env python3
"""Isolated default-browser attempt for connect-google authorization.

The OAuth parent sends the user-private authorization URL over stdin and
suppresses this process's stdout/stderr.  Exit status is the only result:
0 means ``webbrowser.open`` returned true, 1 means false, and 2 means it raised.
"""

from __future__ import annotations

import sys
import webbrowser


def main() -> int:
    try:
        url = sys.stdin.buffer.readline(64 * 1024).decode("utf-8").rstrip("\r\n")
        if not url:
            return 2
        return 0 if webbrowser.open(url) else 1
    except Exception:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
