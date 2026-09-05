#!/usr/bin/env python3
"""Drive readiness interfaces for cloud-files setup."""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ and __package__.count(".") >= 1:
    from . import _drive_gateway
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(SCRIPT_DIR))
    import _drive_gateway


def ensure_assistant_root() -> int:
    """Ensure assistant root folder exists."""
    try:
        config = _drive_gateway.load_config()
        if config.remote_llm_root != "assistant/":
            sys.stderr.write(json.dumps({"error": f"wrong root: {config.remote_llm_root}"}) + "\n")
            return 1
        _drive_gateway.resolve_base_id(config, use_llm_root=True, create=True)
        print(json.dumps({"exists": True, "root": "assistant"}))
        return 0
    except _drive_gateway.CloudFilesError as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}) + "\n")
        return 1


def lists_exists(path: str) -> int:
    """Check if a lists path exists without creating."""
    try:
        normalized = _drive_gateway.validate_relpath(path)
        if not normalized.startswith("lists/") or normalized == "lists/":
            sys.stderr.write(json.dumps({"error": f"invalid path: {normalized}"}) + "\n")
            return 1
        config = _drive_gateway.load_config()
        base_id = _drive_gateway.resolve_base_id(config, use_llm_root=True, create=False)
        try:
            _drive_gateway.resolve_entry(config, base_id, normalized)
            print(json.dumps({"exists": True, "path": normalized}))
        except FileNotFoundError:
            print(json.dumps({"exists": False, "path": normalized}))
        return 0
    except ValueError as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}) + "\n")
        return 1
    except _drive_gateway.CloudFilesError as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}) + "\n")
        return 1


def Interface() -> None:
    """Entry point for Famulus interface execution."""
    args = sys.argv[1:]
    if not args or (args[0] == "lists-exists" and len(args) != 2) or (args[0] not in ("ensure-assistant-root", "lists-exists")):
        sys.exit(1)
    if args[0] == "ensure-assistant-root":
        sys.exit(ensure_assistant_root())
    else:
        sys.exit(lists_exists(args[1]))
