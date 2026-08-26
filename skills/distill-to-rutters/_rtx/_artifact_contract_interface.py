"""Dispatcher interface for deterministic artifact validation and routing."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from ._artifact_contract import decide_route


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validate-and-route")
    parser.add_argument("--artifact-path", required=True, type=Path)
    parser.add_argument("--expected-stage", required=True)
    parser.add_argument("--approved-digest", required=True)
    parser.add_argument(
        "--user-decision", required=True, choices=("approve", "reject")
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    outcome = "source-ready" if args.expected_stage == "source-preflight" else None
    decision = decide_route(
        args.expected_stage,
        outcome,
        args.approved_digest,
        args.user_decision,
        args.artifact_path,
    )
    print(json.dumps(decision.as_dict(), sort_keys=False))
    return 0 if decision.status not in {"failed", "blocked"} else 1


class Interface(PythonArgvMachineInterface):
    prog = "validate-and-route"

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(Interface().main())
