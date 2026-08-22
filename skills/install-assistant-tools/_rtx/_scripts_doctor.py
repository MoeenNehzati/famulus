#!/usr/bin/env python3
"""Render a read-only diagnosis for one explicitly selected install context."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_SRC = REPO_ROOT / "src"
if not __package__ and str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.context import (
    build_development_environment,
    load_active_context,
    resolve_installation_context,
)
from officina.install.doctor import (
    DiagnosticReport,
    diagnose_installation,
    render_diagnostic_json,
    render_diagnostic_text,
)
from officina.runtime.python_machine_interface import PythonArgvMachineInterface


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("standard", "development"), required=True)
    parser.add_argument("--checkout", metavar="ABSOLUTE_PATH")
    parser.add_argument("--home", metavar="DIR")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.mode == "standard" and args.checkout is not None:
        parser.error("--checkout is valid only with --mode development")
    if args.mode == "development" and args.checkout is None:
        parser.error("--mode development requires --checkout")
    return args


def main(
    argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None
) -> int:
    args = parse_args(argv)
    selected_environ = dict(os.environ if environ is None else environ)
    home = Path(args.home).expanduser().resolve() if args.home else Path.home().resolve()
    if args.mode == "development":
        checkout = Path(args.checkout)
        if not checkout.is_absolute():
            raise SystemExit("--checkout must be an absolute path")
        identifier_path = checkout / ".famulus" / "install-id"
        try:
            installation_id = identifier_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SystemExit(f"development context is absent: {identifier_path}: {exc}") from exc
        active_environ = build_development_environment(
            checkout, environ=selected_environ, platform=sys.platform
        )
        isolated_home = checkout / ".famulus" / "home"
        paths = resolve_famulus_paths(
            platform=sys.platform, home=isolated_home, environ=active_environ
        )
        context = load_active_context(
            runtime_root=paths.runtime_root, environ=active_environ
        )
        if context.installation_id != installation_id:
            raise SystemExit("development context identity changed while diagnosing")
        selected_environ = active_environ
    else:
        context = resolve_installation_context(
            mode="standard",
            source_root=REPO_ROOT,
            development_root=None,
            platform=sys.platform,
            home=home,
            environ=selected_environ,
        )
    report = diagnose_installation(
        context=context,
        environ=selected_environ,
        platform=sys.platform,
    )
    print(
        render_diagnostic_json(report) if args.json else render_diagnostic_text(report),
        end="",
    )
    return 0 if report.status == "healthy" else 1


class Interface(PythonArgvMachineInterface):
    prog = "scripts_doctor.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
