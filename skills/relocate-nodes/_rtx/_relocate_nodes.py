"""Adapt one manifest-driven node relocation to the transition engine."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from officina.runtime.python_machine_interface import PythonMachineInterface


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Interface(PythonMachineInterface):
    """Expose the temporary relocation engine through the registered route."""

    prog = "relocate-nodes"
    description = "Preflight or atomically apply one manifest-driven node relocation."

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
        parser.add_argument("--manifest", type=Path, required=True)
        parser.add_argument("--report", type=Path)
        parser.add_argument("--apply", action="store_true")
        return parser

    def run(self, args: argparse.Namespace) -> int:
        from officina.refactor.relocation import (
            RelocationError,
            apply_change_set,
            load_manifest,
            plan_relocation,
            render_report,
        )

        try:
            manifest = load_manifest(args.manifest.resolve())
            changes = plan_relocation(args.root, manifest)
            report = render_report(changes)
            if args.report is not None:
                args.report.write_text(report, encoding="utf-8")
            if args.apply:
                apply_change_set(changes)
            sys.stdout.write(report)
        except (OSError, RelocationError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
