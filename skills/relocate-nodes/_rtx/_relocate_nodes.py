"""Registered adapter for compact node relocation."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
from typing import Mapping
import yaml
from officina.blueprints.graph import load_repository_blueprint_graph
from officina.runtime.python_machine_interface import PythonMachineInterface
from ._compact_relocation import RelocationError, apply, plan
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
class Interface(PythonMachineInterface):
    prog = "relocate-nodes"
    description = "Plan or atomically apply one reviewed node relocation."
    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
        parser.add_argument("--manifest", type=Path, required=True)
        parser.add_argument("--report", type=Path)
        parser.add_argument("--apply", action="store_true")
        return parser
    def _verify(self, root: Path, manifest: Mapping[str, object]) -> dict[str, float]:
        started = time.perf_counter()
        load_repository_blueprint_graph(root)
        graph_seconds = time.perf_counter() - started
        started = time.perf_counter()
        if not plan(root, manifest, recover_interrupted=False).empty:
            raise RelocationError("target-side postflight is not empty")
        return {"graph_verification_seconds": graph_seconds, "postflight_seconds": time.perf_counter() - started}
    def run(self, args: argparse.Namespace) -> int:
        try:
            total_started = time.perf_counter()
            root = args.root.resolve()
            if args.report is not None and args.report.resolve().is_relative_to(root):
                raise RelocationError("report path must be outside selected repository")
            manifest = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
            if not isinstance(manifest, Mapping):
                raise RelocationError("manifest must be a YAML mapping")
            started = time.perf_counter()
            recipe = plan(root, manifest)
            timings = {"planning_seconds": time.perf_counter() - started}
            if args.apply:
                timings.update(apply(recipe, verify=lambda: self._verify(root, manifest)))
            timings["total_seconds"] = time.perf_counter() - total_started
            report = recipe.report()
            report["timings"] = timings
            rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
            sys.stdout.write(rendered) if args.report is None else args.report.write_text(rendered, encoding="utf-8")
            return 0
        except (OSError, RelocationError, yaml.YAMLError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
