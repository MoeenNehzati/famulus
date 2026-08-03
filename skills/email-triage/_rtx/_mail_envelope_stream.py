"""Fetch mail envelopes through dispatch and expose only watermark-filtered output."""
from __future__ import annotations

import argparse
import json
import sys

import yaml

from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface

from . import _envelope_gate as envelope_gate
from . import _rescan_filter


class Interface(PythonMachineInterface):
    prog = "fetch-filtered-envelopes"
    dispatches = {
        "mail-list": DispatchCall(
            caller_module_id="email-triage-rtx",
            target_module_id="email-client",
            interface="mail-list",
        ),
        "list-read": DispatchCall(
            caller_module_id="email-triage-rtx",
            target_module_id="list-manager",
            interface="cloud-read",
        ),
    }

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("-a", "--account", required=True)
        parser.add_argument("--after", required=True)
        parser.add_argument(
            "--rescan-after",
            help=(
                "Explicit ISO cutoff (date, or datetime as stored in the watermark "
                "file) that replaces the stored triage watermark for this call only. "
                "Use this to manually re-fetch mail from an arbitrary earlier point "
                "(e.g. to backfill after a bug, or bootstrap onto an account) without "
                "editing the watermark file. Combine with --dedup-against so already-"
                "triaged messages are not re-added."
            ),
        )
        parser.add_argument(
            "--dedup-against",
            choices=("todo", "triage"),
            help=(
                "Destination list name to filter rescan candidates against by "
                "source.message_id before returning them: envelopes whose message_id "
                "already appears on an entry in that list are dropped. Reads the "
                "destination list via list-manager.interface.cloud-read; does not "
                "mutate it. Primarily useful with --rescan-after."
            ),
        )
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
        rescan_after = getattr(args, "rescan_after", None)
        cutoff_dt, warning = envelope_gate.load_cutoff(override=rescan_after)
        if warning:
            print(warning, file=sys.stderr)
        filtered = envelope_gate.filter_envelopes(envelopes, cutoff_dt)

        dedup_against = getattr(args, "dedup_against", None)
        if dedup_against:
            list_result = self.dispatch(
                "list-read",
                args=[dedup_against, "--cloud"],
                capture_output=True,
                text=True,
            )
            if list_result.returncode != 0:
                print(
                    f"error: cloud-read of '{dedup_against}' failed with exit code "
                    f"{list_result.returncode}",
                    file=sys.stderr,
                )
                return list_result.returncode
            try:
                existing_entries = yaml.safe_load(list_result.stdout) or {}
            except yaml.YAMLError:
                print(
                    f"error: cloud-read of '{dedup_against}' returned invalid YAML",
                    file=sys.stderr,
                )
                return 1
            filtered = _rescan_filter.filter_destination_duplicates(
                filtered, existing_entries=existing_entries
            )

        print(envelope_gate.render_filtered_envelopes(filtered, args.account, cutoff_dt))
        return 0
