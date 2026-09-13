"""Report deduplicated pytest failures between consecutive green CI runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._ci_history import HistoryError, build_failure_report, canonical_json_bytes, csv_bytes, html_bytes, load_json_object, load_published_tree, publish_tree, require_sibling_destination, reserve_private_root
except ImportError:
    from _ci_history import HistoryError, build_failure_report, canonical_json_bytes, csv_bytes, html_bytes, load_json_object, load_published_tree, publish_tree, require_sibling_destination, reserve_private_root


def _rows(report: dict) -> list[dict]:
    """Flatten episode incidence and recurrence bounds into table rows.

    Intent
    ------
    Produce one stable tabular representation of first failure observations.

    Rationale
    ---------
    Joining recurrence once keeps CSV and HTML fields consistent.

    Pseudocode
    ----------
    - set operation = `index recurrence records by canonical test`
    - set operation = `join each episode incidence to recurrence bounds`
    - return rows in deterministic order

    Wraps
    -----
    - none
    """
    recurrence = {item["canonical_test_id"]: item for item in report["tests"]}
    rows: list[dict] = []
    for episode in report["episodes"]:
        for item in episode["incidence"]:
            test = recurrence[item["canonical_test_id"]]
            observation = item["first_observation"]
            rows.append({
                "episode_id": episode["episode_id"], "canonical_test_id": item["canonical_test_id"],
                "exact_node": observation.get("exact", ""), "first_run_id": observation.get("run_id", ""),
                "first_attempt": observation.get("attempt_number", ""), "k": test["k"], "u": test["u"], "E": test["E"],
                "lower_numerator": test["lower_numerator"], "upper_numerator": test["upper_numerator"],
                "evidence_complete": episode["evidence_complete"],
            })
    return sorted(rows, key=lambda item: (item["episode_id"], item["canonical_test_id"]))


def main(argv: list[str] | None = None) -> int:
    """Build and publish green-to-green failure episode reports.

    Intent
    ------
    Transform one verified snapshot into JSON, CSV, and HTML failure output.

    Rationale
    ---------
    Frozen Git evidence and immutable publication keep claims reproducible.

    InstantiationsFromRepo
    ----------------------
      ._rows:
        why:
          transforms: "Carries flattened episode incidence into both tabular report formats."

    Pseudocode
    ----------
    - set operation = `parse snapshot and report destinations`
    - set operation = `verify publication and load run and Git evidence`
    - set operation = `build failure report and table rows`
    - set operation = `publish report tree and print receipt`
    - set operation = `classify failures and print incomplete receipt`
    - rows = _rows(report)
    - return rows

    Wraps
    -----
    - none
    """
    parser = argparse.ArgumentParser(prog="report-test-failures-between-green-runs")
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    args = parser.parse_args(argv)
    report_root: Path | None = None
    try:
        require_sibling_destination(Path(args.snapshot_dir), Path(args.report_dir), "failure-episodes")
        manifest, members = load_published_tree(Path(args.snapshot_dir))
        snapshot = load_json_object(members, "runs.json")
        snapshot["snapshot_digest"] = manifest["snapshot_digest"]
        git_evidence = load_json_object(members, "git-evidence.json")
        report = build_failure_report(snapshot, git_evidence)
        rows = _rows(report)
        headers = ["episode_id", "canonical_test_id", "exact_node", "first_run_id", "first_attempt", "k", "u", "E", "lower_numerator", "upper_numerator", "evidence_complete"]
        report_root = reserve_private_root(Path(args.report_dir))
        publication = publish_tree(
            report_root,
            manifest_fields={"report_type": "failure-episodes", "source_snapshot_digest": manifest["snapshot_digest"]},
            members={"failure-episodes.json": canonical_json_bytes(report), "failure-episodes.csv": csv_bytes(headers, rows), "failure-episodes.html": html_bytes("CI failure episodes", headers, rows)},
            outcome="reported" if not manifest.get("gaps") else "reported-with-gaps",
            gaps=list(manifest.get("gaps", [])),
        )
        print(json.dumps({"schema_version": 1, "state": publication["outcome"], "snapshot_digest": manifest["snapshot_digest"], "report_dir": str(report_root), "episode_count": len(report["episodes"]), "canonical_test_count": len(report["tests"]), "gap_count": len(manifest.get("gaps", []))}, sort_keys=True))
        return 0
    except (HistoryError, KeyError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        code = exc.code if isinstance(exc, HistoryError) else "report_failed"
        print(json.dumps({"schema_version": 1, "state": "failed", "error": code, "incomplete_path": str(report_root) if report_root else None}, sort_keys=True))
        print(str(exc), file=sys.stderr)
        return 2


class Interface(PythonArgvMachineInterface):
    """Expose deterministic green-to-green failure analysis.

    Intent
    ------
    Present episode reporting through the Python machine interface protocol.

    Rationale
    ---------
    A narrow adapter preserves the reporter's command contract.

    Pseudocode
    ----------
    - set program_name = `failure reporter`
    - set operation = `forward interface arguments to reporter command`

    Wraps
    -----
    - none
    """

    prog = "report-test-failures-between-green-runs"

    def run(self, argv: list[str]) -> int:
        """Forward machine-interface arguments to failure reporting.

        Intent
        ------
        Preserve reporter parsing, output, and exit status.

        Rationale
        ---------
        Dispatcher adaptation should not duplicate reporting logic.

        Pseudocode
        ----------
        - return failure reporter status for input arguments

        Wraps
        -----
        main -> preprocess: pass argv unchanged; postprocess: return status unchanged; fixed_arguments: none
        """
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
