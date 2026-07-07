#!/usr/bin/env python3
"""Run every host-compatibility-alias helper against a scaffolded project.

Generic and host-neutral by design (see references/skill-guidelines.md,
guideline 13): this script never names a specific host, not even in an
illustrative example. It discovers compat-alias helpers purely by filename
suffix convention (``*_compat_symlink.py``, sitting next to this file),
loads each by file path (not by dotted import, and not via a subprocess/
shell call -- pure Python stdlib keeps this working the same way on every
OS), and calls its ``create_alias(project_dir)`` function.

Each individual helper is free to name a specific host in its own filename
and content -- that filename is what determines what compatibility alias
gets created. Adding support for a new host's compatibility alias later
means adding one more ``<host>_compat_symlink.py`` file here; this script
does not change.

Usage:
    setup_compat_aliases.py <project-dir>
"""
from __future__ import annotations

import glob
import importlib.util
import os
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: setup_compat_aliases.py <project-dir>", file=sys.stderr)
        return 1
    project_dir = sys.argv[1]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    helpers = sorted(glob.glob(os.path.join(script_dir, "*_compat_symlink.py")))
    for helper_path in helpers:
        module_name = os.path.splitext(os.path.basename(helper_path))[0]
        spec = importlib.util.spec_from_file_location(module_name, helper_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.create_alias(project_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
