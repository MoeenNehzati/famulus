#!/usr/bin/env python3
from __future__ import annotations

import sys

import _drive_gateway as cloud_files


def main() -> int:
    return cloud_files.run_entrypoint(
        cloud_files.read_entrypoint,
        sys.argv[1:],
        use_llm_root=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
