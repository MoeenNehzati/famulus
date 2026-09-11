"""Report descriptive timing and repeated-step hotspots from a CI snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._ci_history import HistoryError, build_runtime_report, canonical_json_bytes, csv_bytes, html_bytes, load_json_object, load_published_tree, publish_tree, require_sibling_destination, reserve_private_root
except ImportError:
    from _ci_history import HistoryError, build_runtime_report, canonical_json_bytes, csv_bytes, html_bytes, load_json_object, load_published_tree, publish_tree, require_sibling_destination, reserve_private_root


def _rows(report: dict) -> list[dict]:
    """Flatten runtime metrics and heuristic groups into table rows.

    Intent
    ------
    Produce one stable tabular shape for CSV and HTML renderers.

    Rationale
    ---------
    Explicit row kinds preserve distinctions among metrics, jobs, and steps.

    Pseudocode
    ----------
    - set operation = `convert metrics to metric rows`
    - set operation = `append job and step heuristic rows`
    - return rows in deterministic order

    Wraps
    -----
    - none
    """
    rows = [{"kind": "metric", "name": name, "labels": "", "ordinal": "", "runner_hosting_classification": "", "runner_os_classification": "", "runner_classification_basis": "", **summary} for name, summary in report["metrics"].items()]
    rows.extend({"kind": "job-heuristic", "name": group["job_name"], "labels": ",".join(group["labels"]), "ordinal": "", **group, "runner_classification_basis": json.dumps(group["runner_classification_basis"], sort_keys=True, separators=(",", ":"))} for group in report["job_groups"])
    rows.extend({"kind": "step-heuristic", "name": group["step_name"], "labels": ",".join(group["labels"]), "ordinal": group["step_ordinal"], **group, "runner_classification_basis": json.dumps(group["runner_classification_basis"], sort_keys=True, separators=(",", ":"))} for group in report["step_groups"])
    return sorted(rows, key=lambda item: (item["kind"], item["name"], item["labels"], str(item["ordinal"])))


def main(argv: list[str] | None = None) -> int:
    """Build and publish descriptive CI runtime reports.

    Intent
    ------
    Transform one verified snapshot into JSON, CSV, and HTML timing output.

    Rationale
    ---------
    Read verification and immutable publication tie reports to exact evidence.

    InstantiationsFromRepo
    ----------------------
      ._rows:
        why:
          transforms: "Carries flattened timing metrics into both tabular report formats."

    Pseudocode
    ----------
    - set operation = `parse snapshot and report destinations`
    - set operation = `verify publication and load run records`
    - set operation = `build timing report and table rows`
    - set operation = `publish report tree and print receipt`
    - set operation = `classify failures and print incomplete receipt`
    - rows = _rows(report)
    - return rows

    Wraps
    -----
    - none
    """
    parser = argparse.ArgumentParser(prog="report-ci-runtime-hotspots")
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    args = parser.parse_args(argv)
    report_root: Path | None = None
    try:
        require_sibling_destination(Path(args.snapshot_dir), Path(args.report_dir), "runtime-hotspots")
        manifest, members = load_published_tree(Path(args.snapshot_dir))
        snapshot = load_json_object(members, "runs.json")
        snapshot["snapshot_digest"] = manifest["snapshot_digest"]
        report = build_runtime_report(snapshot)
        rows = _rows(report)
        headers = ["kind", "name", "labels", "ordinal", "runner_hosting_classification", "runner_os_classification", "runner_classification_basis", "n", "missing", "total_ms", "median_numerator_ms", "median_denominator", "p90_ms", "maximum_ms"]
        report_root = reserve_private_root(Path(args.report_dir))
        publication = publish_tree(
            report_root,
            manifest_fields={"report_type": "runtime-hotspots", "source_snapshot_digest": manifest["snapshot_digest"]},
            members={"runtime-hotspots.json": canonical_json_bytes(report), "runtime-hotspots.csv": csv_bytes(headers, rows), "runtime-hotspots.html": html_bytes("CI runtime hotspots", headers, rows)},
            outcome="reported" if not manifest.get("gaps") else "reported-with-gaps",
            gaps=list(manifest.get("gaps", [])),
        )
        print(json.dumps({"schema_version": 1, "state": publication["outcome"], "snapshot_digest": manifest["snapshot_digest"], "report_dir": str(report_root), "analyzed_run_count": len(snapshot.get("runs", [])), "gap_count": len(manifest.get("gaps", []))}, sort_keys=True))
        return 0
    except (HistoryError, KeyError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        code = exc.code if isinstance(exc, HistoryError) else "report_failed"
        print(json.dumps({"schema_version": 1, "state": "failed", "error": code, "incomplete_path": str(report_root) if report_root else None}, sort_keys=True))
        print(str(exc), file=sys.stderr)
        return 2


class Interface(PythonArgvMachineInterface):
    """Expose deterministic descriptive CI timing analysis.

    Intent
    ------
    Present runtime reporting through the Python machine interface protocol.

    Rationale
    ---------
    A narrow adapter preserves the reporter's command contract.

    Pseudocode
    ----------
    - set program_name = `runtime reporter`
    - set operation = `forward interface arguments to reporter command`

    Wraps
    -----
    - none
    """

    prog = "report-ci-runtime-hotspots"

    def run(self, argv: list[str]) -> int:
        """Forward machine-interface arguments to runtime reporting.

        Intent
        ------
        Preserve reporter parsing, output, and exit status.

        Rationale
        ---------
        Dispatcher adaptation should not duplicate reporting logic.

        Pseudocode
        ----------
        - return runtime reporter status for input arguments

        Wraps
        -----
        main -> preprocess: pass argv unchanged; postprocess: return status unchanged; fixed_arguments: none
        """
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
