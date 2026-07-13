"""Fetch mail envelopes through dispatch and expose only watermark-filtered output."""
from __future__ import annotations

import argparse
import json
import sys

from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface

from . import _envelope_gate as envelope_gate


class Interface(PythonMachineInterface):
    prog = "fetch-filtered-envelopes"
    dispatches = {
        "mail-list": DispatchCall(
            caller_skill="email-triage",
            target_skill="email-client",
            interface="mail-list",
        )
    }

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("-a", "--account", required=True)
        parser.add_argument("--after", required=True)
        return parser

    def run(self, args: argparse.Namespace) -> int:
        result = self.dispatch(
            "mail-list",
            args=["-a", args.account, "--after", args.after],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"error: mail-list failed with exit code {result.returncode}", file=sys.stderr)
            return result.returncode
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")

        try:
            envelopes = json.loads(result.stdout)
        except json.JSONDecodeError:
            print("error: mail-list returned invalid envelope JSON", file=sys.stderr)
            return 1
        if not isinstance(envelopes, list):
            print("error: mail-list returned invalid envelope JSON", file=sys.stderr)
            return 1

        envelope_gate.clear_stale_error()
        cutoff_dt, warning = envelope_gate.load_cutoff()
        if warning:
            print(warning, file=sys.stderr)
        filtered = envelope_gate.filter_envelopes(envelopes, cutoff_dt)
        print(envelope_gate.render_filtered_envelopes(filtered, args.account, cutoff_dt))
        return 0
