"""Generate a refreshed blueprint YAML file under /tmp."""
from __future__ import annotations

import argparse
from pathlib import Path

from officina.common.blueprint_template import write_regenerated_skill_blueprint
from officina.runtime.python_machine_interface import PythonMachineInterface


class Interface(PythonMachineInterface):
    prog = "regenerate-blueprint"
    description = "Write a refreshed blueprint for one existing skill under /tmp."

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("skill_name")
        return parser

    def run(self, args: argparse.Namespace) -> int:
        repo_root = Path(__file__).resolve().parents[3]
        output = write_regenerated_skill_blueprint(
            args.skill_name,
            repo_root=repo_root,
            output_dir=Path("/tmp"),
            doc_mode="compact",
        )
        print(output)
        return 0
