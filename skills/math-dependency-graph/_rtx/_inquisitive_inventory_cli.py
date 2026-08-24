#!/usr/bin/env python3
"""Expose one inventory-diagnosis voyage as finite JSON operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

from officina.rutter import MachineInstruction, Message
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from ._inquisitive_inventory_rutter import (
    open_experiment,
    setup_bundled_frozen_gold_experiment,
    setup_experiment,
    setup_frozen_gold_experiment,
    setup_iterator_experiment,
    validated_inventory_ledger,
)


class _InvalidResponse(Exception):
    def __init__(self, report: object) -> None:
        super().__init__("response validation failed")
        self.report = report


class _UsageError(Exception):
    pass


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(stream: object, value: object) -> None:
    json.dump(
        _plain_json(value), stream, sort_keys=True, separators=(",", ":")
    )
    stream.write("\n")


def _instruction(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, Message):
        return {"kind": "message", **_plain_json(value.to_json())}
    if isinstance(value, MachineInstruction):
        return {
            "kind": "machine",
            "machine_id": value.machine_id,
            "mode": value.mode,
            "answer_format": _plain_json(value.answer_format),
        }
    raise TypeError("unsupported Rutter instruction")


def _show(voyage: object) -> dict[str, object]:
    status = voyage.get_status()
    evolution = status.current_evolution
    fault = status.fault
    return {
        "evolution": {
            "rutter_id": evolution.rutter_id,
            "definition_version": evolution.definition_version,
            "evolution_id": evolution.evolution_id,
            "evolution_entry_id": evolution.evolution_entry_id,
            "depth": evolution.depth,
            "condition": evolution.condition,
        },
        "instruction": _instruction(status.instruction),
        "active_result": (
            None
            if status.active_result is None
            else _plain_json(status.active_result.to_json())
        ),
        "fault": (
            None
            if fault is None
            else {
                "category": fault.category,
                "evolution_id": fault.evolution_id,
                "evolution_entry_id": fault.evolution_entry_id,
                "target_evolution_id": fault.target_evolution_id,
                "transition_hook_ids": list(fault.transition_hook_ids),
            }
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="inquisitive_inventory_cli.py")
    modes = parser.add_subparsers(dest="mode", required=True)
    setup = modes.add_parser("setup")
    source = setup.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-cases-file")
    source.add_argument("--iterator-state-dir")
    setup.add_argument("--worker-index", type=int)
    gold = setup.add_mutually_exclusive_group()
    gold.add_argument("--gold-cases-file")
    gold.add_argument("--gold-annotation-file")
    setup.add_argument("--gold-overlay-file")
    setup.add_argument("--inventory-fragment-file", action="append")
    setup.add_argument("--experiment-dir", required=True)
    show = modes.add_parser("show")
    show.add_argument("--experiment-dir", required=True)
    next_ = modes.add_parser("next")
    next_.add_argument("--experiment-dir", required=True)
    next_.add_argument("--response-file")
    ledger = modes.add_parser("ledger")
    ledger.add_argument("--experiment-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
        if args.mode == "ledger":
            _write_json(
                sys.stdout,
                {"rows": validated_inventory_ledger(Path(args.experiment_dir))},
            )
            return 0
        if args.mode == "setup":
            frozen_gold = bool(
                args.inventory_fragment_file
                or args.gold_annotation_file is not None
                or args.gold_overlay_file is not None
            )
            if frozen_gold:
                if (
                    args.iterator_state_dir is None
                    or args.worker_index is None
                    or not args.inventory_fragment_file
                ):
                    raise _UsageError(
                        "full-gold setup requires iterator state, worker index, "
                        "and inventory fragments"
                    )
                explicit_gold = (
                    args.gold_annotation_file is not None,
                    args.gold_overlay_file is not None,
                )
                if explicit_gold == (True, True):
                    voyage = setup_frozen_gold_experiment(
                        Path(args.iterator_state_dir),
                        args.worker_index,
                        [Path(path) for path in args.inventory_fragment_file],
                        Path(args.gold_annotation_file),
                        Path(args.gold_overlay_file),
                        Path(args.experiment_dir),
                    )
                elif explicit_gold == (False, False):
                    voyage = setup_bundled_frozen_gold_experiment(
                        Path(args.iterator_state_dir),
                        args.worker_index,
                        [Path(path) for path in args.inventory_fragment_file],
                        Path(args.experiment_dir),
                    )
                else:
                    raise _UsageError(
                        "gold annotation and overlay overrides must be supplied together"
                    )
            elif args.gold_cases_file is None:
                raise _UsageError(
                    "setup requires gold cases or authenticated inventory fragments"
                )
            elif args.iterator_state_dir is not None:
                if args.worker_index is None:
                    raise _UsageError(
                        "setup --iterator-state-dir requires --worker-index"
                    )
                voyage = setup_iterator_experiment(
                    Path(args.iterator_state_dir),
                    args.worker_index,
                    _read_json(Path(args.gold_cases_file)),
                    Path(args.experiment_dir),
                )
            else:
                if args.worker_index is not None:
                    raise _UsageError(
                        "setup --worker-index requires --iterator-state-dir"
                    )
                voyage = setup_experiment(
                    _read_json(Path(args.source_cases_file)),
                    _read_json(Path(args.gold_cases_file)),
                    Path(args.experiment_dir),
                )
        else:
            voyage = open_experiment(Path(args.experiment_dir))
            if args.mode == "next":
                if args.response_file is None:
                    voyage.next(continue_=True)
                else:
                    response = _read_json(Path(args.response_file))
                    report = voyage.validate(response)
                    if not report.valid:
                        raise _InvalidResponse(report)
                    voyage.next(response, continue_=True)
    except _UsageError as error:
        payload = {
            "error": {
                "code": "usage-error",
                "message": str(error),
            }
        }
        _write_json(sys.stdout, payload)
        _write_json(sys.stderr, payload)
        return 2
    except _InvalidResponse as error:
        payload = {
            "error": {
                "code": "invalid-response",
                "message": str(error),
                "report": error.report.to_json(),
            }
        }
        _write_json(sys.stdout, payload)
        _write_json(sys.stderr, payload)
        return 4
    except json.JSONDecodeError:
        payload = {
            "error": {
                "code": "input-error",
                "message": "input is not valid JSON",
            }
        }
        _write_json(sys.stdout, payload)
        _write_json(sys.stderr, payload)
        return 3
    except FileExistsError:
        payload = {
            "error": {
                "code": "state-error",
                "message": "experiment state already exists",
            }
        }
        _write_json(sys.stdout, payload)
        _write_json(sys.stderr, payload)
        return 5
    except OSError:
        payload = {
            "error": {
                "code": "input-error",
                "message": "filesystem input could not be read",
            }
        }
        _write_json(sys.stdout, payload)
        _write_json(sys.stderr, payload)
        return 3
    except ValueError as error:
        payload = {
            "error": {
                "code": "input-error",
                "message": str(error),
            }
        }
        _write_json(sys.stdout, payload)
        _write_json(sys.stderr, payload)
        return 3
    except Exception as error:
        payload = {
            "error": {
                "code": "internal-error",
                "message": f"unexpected {type(error).__name__}",
            }
        }
        _write_json(sys.stdout, payload)
        _write_json(sys.stderr, payload)
        return 1
    _write_json(sys.stdout, _show(voyage))
    return 0


class Interface(PythonArgvMachineInterface):
    prog = "inquisitive_inventory_cli.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
