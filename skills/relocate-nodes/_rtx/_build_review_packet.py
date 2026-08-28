"""Registered adapter for compact relocation review packets."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from officina.runtime.python_machine_interface import PythonMachineInterface
from ._compact_relocation import RelocationError, build_packet
class Interface(PythonMachineInterface):
    prog = "relocate-nodes-build-review-packet"
    description = "Group relocation text hits for user review."
    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("--root", "--repository-root", dest="root", required=True, type=Path)
        parser.add_argument("--report", required=True, type=Path)
        parser.add_argument("--output", required=True, type=Path)
        return parser
    def run(self, args: argparse.Namespace) -> int:
        try:
            root, output = args.root.resolve(), args.output.resolve()
            if output.is_relative_to(root):
                raise RelocationError("output path must be outside selected repository")
            report = json.loads(args.report.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise RelocationError("relocation report must be an object")
            packet = build_packet(root, report)
            output.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
            summary = packet["summary"]
            print(f"{summary['occurrences']} occurrences in {summary['review_units']} review units; packet: {output}")
            return 0
        except (OSError, ValueError, RelocationError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
