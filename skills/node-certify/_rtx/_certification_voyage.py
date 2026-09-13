#!/usr/bin/env python3
"""Declare and dispense the certification workflow; machine code owns all decisions."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from officina.rutter import (
    LLMStep, MachineStep, Rutter, RutterRegistry, Terminal,
    VoyageDispenser, voyage_dispenser_cli,
)
from officina.runtime.python_machine_interface import PythonArgvMachineInterface
from officina.rutter.dispenser import InvalidVoyageModeArgumentsError

from . import _certification_support as support

CERTIFICATION_RUTTER = Rutter(
    id="certification", version=1, start="prepare-and-reconcile",
    evolutions={
        "prepare-and-reconcile": MachineStep(
            support.prepare, mode="repeat-safe",
            next_on_outcome={
                "dispatch": "assign-audit", "sign": "accept-audit-and-certify",
                "complete": "complete", "failed": "failed",
            },
        ),
        "assign-audit": LLMStep(
            "Spawn one fresh worker per supplied packet with its exact instruction "
            "interface. Pass the packet unchanged. Wait for one completion or host "
            "failure and forward the exact raw output in the supplied event envelope. "
            "Do not parse, summarize, repair, audit, or make certification decisions. "
            "Keep other worker handles and raw completions for subsequent turns.",
            response_schema=support.EVENT_SCHEMA,
            data=support.dispatch_data, assess_response=support.assess_event,
            next_on_outcome="accept-audit-and-certify",
        ),
        "accept-audit-and-certify": MachineStep(
            support.accept_and_certify, mode="repeat-safe",
            next_on_outcome={"accepted": "prepare-and-reconcile", "failed": "failed"},
        ),
        "complete": Terminal(result_constructor=support.complete),
        "failed": Terminal(result_constructor=support.failed),
    },
)

RUN_ROOT = Path(__file__).resolve().parents[1] / "_build" / "certification-voyages"
RECKONING_NAME = "certification.reckoning.json"


def make_voyage_dispenser(root: Path = RUN_ROOT) -> VoyageDispenser:
    """Confine each certification run to one module-owned Reckoning."""

    root = root.resolve()

    def paths(prefix: str | None = None):
        return {
            path.parent.relative_to(root).as_posix() + "/1": path
            for path in sorted(root.rglob(RECKONING_NAME))
            if prefix is None or path.parent.relative_to(root).as_posix().startswith(prefix + "/")
        }

    def initiate(
        mode: str, *, run_id: str, repository: str, worker_capacity: str,
        targets: str = "", retry_interval_seconds: str = "10",
    ) -> None:
        try:
            charter = support.make_charter(
                Path(repository), targets.split(), int(worker_capacity), run_id,
                int(retry_interval_seconds),
            )
        except (ValueError, OSError, support.certifier.CertificationError) as error:
            raise InvalidVoyageModeArgumentsError(str(error)) from error
        registry = RutterRegistry({"certification": CERTIFICATION_RUTTER}, root / run_id)
        registry.create("certification", Path(RECKONING_NAME), charter)

    def open_voyage(voyage_id: str):
        path = paths()[voyage_id]
        return RutterRegistry(
            {"certification": CERTIFICATION_RUTTER}, path.parent,
        ).open(Path(RECKONING_NAME))

    def release(voyage_id: str) -> None:
        path = paths()[voyage_id]
        path.unlink()
        path.with_name(path.name + ".lock").unlink(missing_ok=True)
        path.parent.rmdir()

    return VoyageDispenser(
        modes={"default": {
            "description": "Audit and certify one repository with dispatch-only LLM control.",
            "arguments": {
                "repository": "Exact repository path.",
                "worker_capacity": "Available positive worker slots, excluding controller.",
            },
            "optional_arguments": {
                "targets": "Optional space-separated registered node IDs; omit for whole graph.",
                "retry_interval_seconds": "Optional positive retry delay; defaults to 10.",
            },
        }},
        initiate_voyages=initiate, get_voyage_ids=lambda prefix: tuple(paths(prefix)),
        open_voyage=open_voyage, release_voyage=release,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the declared certification Voyage process interface."""

    return voyage_dispenser_cli(make_voyage_dispenser(), argv)


class Interface(PythonArgvMachineInterface):
    """Expose certification initiation and the standard atomic next operation."""

    prog = "certification-voyage"

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
