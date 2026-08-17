"""Adapt one manifest-driven node relocation to the transition engine."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Interface(PythonMachineInterface):
    """Expose the temporary relocation engine through the registered route."""

    prog = "relocate-nodes"
    description = "Preflight or atomically apply one manifest-driven node relocation."
    dispatches = {
        "sync-blueprints": DispatchCall(
            caller_module_id="relocate-nodes._rtx",
            target_module_id="skill-maker._rtx",
            interface="sync-blueprints",
            version=1,
            smoke_args=("--check",),
        )
    }

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
        parser.add_argument("--manifest", type=Path, required=True)
        parser.add_argument("--report", type=Path)
        parser.add_argument("--apply", action="store_true")
        return parser

    def _synchronize(self, repository: Path, *, check: bool) -> None:
        """Run the authorized synchronizer against one isolated repository view."""

        result = self.dispatch(
            "sync-blueprints",
            args=["--check"] if check else [],
            repo_root=repository,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() if isinstance(result.stderr, str) else ""
            from ._relocation_engine import RelocationError

            raise RelocationError(
                "blueprint synchronizer failed" + (f": {detail}" if detail else "")
            )

    def run(self, args: argparse.Namespace) -> int:
        from ._relocation_engine import (
            RelocationError,
            apply_change_set,
            load_manifest,
            plan_relocation,
            render_report,
        )

        try:
            root = args.root.resolve()
            if args.report is not None:
                report_path = args.report.resolve()
                try:
                    report_path.relative_to(root)
                except ValueError:
                    pass
                else:
                    raise RelocationError(
                        "report path must be outside selected repository: "
                        f"{report_path} is contained by {root}"
                    )
            manifest = load_manifest(args.manifest.resolve())
            changes = plan_relocation(root, manifest, synchronize=self._synchronize)
            report = render_report(changes)
            if args.report is not None:
                args.report.write_text(report, encoding="utf-8")
            if args.apply:
                apply_change_set(changes)
            sys.stdout.write(report)
        except (OSError, RelocationError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
