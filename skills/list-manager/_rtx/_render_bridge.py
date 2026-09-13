#!/usr/bin/env python3
"""Read a local or cloud-backed structured list file and immediately render it for display."""
from __future__ import annotations

import argparse
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_configure_stdio()

try:
    from . import _cloud_transport as cloud_transport
    from . import _list_beautify, _yaml_store
except ImportError:
    import _cloud_transport as cloud_transport
    import _list_beautify
    import _yaml_store


def download_list(list_name: str, dest_path: Path) -> None:
    """Download list from cloud storage via cloud-files lists-read interface."""
    try:
        cloud_transport.download_list(list_name, dest_path)
    except cloud_transport.CloudTransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _run_entrypoint(main, argv: list[str], *, stdin: str = "") -> tuple[int, str, str]:
    """Run one sibling CLI entrypoint with isolated text streams."""

    stdout, stderr = StringIO(), StringIO()
    saved_stdin = sys.stdin
    try:
        sys.stdin = StringIO(stdin)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                exit_code = main(argv)
            except SystemExit as exc:
                exit_code = exc.code if type(exc.code) is int else 1
    finally:
        sys.stdin = saved_stdin
    return int(exit_code or 0), stdout.getvalue(), stderr.getvalue()


class Interface(PythonArgvMachineInterface):
    dispatches = cloud_transport.DISPATCHES
    prog = "read_beautify.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="read_beautify.py")
    parser.add_argument("file", help="Path to local YAML list file, or cloud list name with --cloud")
    parser.add_argument("filters", nargs="*", help="key=value (exact/OR) or key~=value (regex) filters")
    parser.add_argument("--cloud", action="store_true", help="Treat the source as a cloud list name and download it")
    parser.add_argument("--sort", metavar="FIELD", help="Sort results by field before rendering")
    parser.add_argument("-D", "--no-descriptions", action="store_true", help="Hide entry descriptions")
    parser.add_argument("--markdown", action="store_true", help="Render bullet-list markdown (already the default; explicit form for when schema info may be stripped)")
    parser.add_argument("--table", action="store_true", help="Render a flat GFM table instead of the default nested bullet list")
    parser.add_argument("--diff", action="store_true", help="Render the legacy ```diff```-fenced view instead of the default bullet list")
    parser.add_argument("--no-ids", action="store_true", help="Do not append each entry's #id (ids are shown by default)")
    parser.add_argument("-o", "--output", metavar="FILE", help="Write beautified output to file instead of stdout")
    args = parser.parse_args(argv)

    # Cloud mode: the source positional is a list name → download → read → beautify
    file_to_read = args.file
    temp_path = None

    if args.cloud:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
            temp_path = Path(tmp.name)
        download_list(args.file, temp_path)
        file_to_read = str(temp_path)

    try:
        read_args = ["read", file_to_read]
        if args.sort:
            read_args.extend(["--sort", args.sort])
        read_args.extend(args.filters)
        read_code, read_stdout, read_stderr = _run_entrypoint(
            _yaml_store.main, read_args
        )
        if read_code:
            if read_stdout:
                print(read_stdout, end="")
            if read_stderr:
                print(read_stderr, end="", file=sys.stderr)
            return read_code

        beautify_args = ["--relative-deadlines"]
        if args.diff:
            beautify_args.append("--diff")
        elif args.table:
            beautify_args.append("--table")
        else:
            # Force the bullet-list renderer explicitly rather than relying on
            # beautify.py's schema-based auto-detection: filtered `lists.py
            # read` output is only a `schema`-bearing dict when the source
            # file was a full document. If the source itself was already a
            # bare entry list (e.g. a caller re-reading an intermediate file),
            # there's no `schema` key to detect from, so force it here.
            beautify_args.append("--markdown")
        if args.no_descriptions:
            beautify_args.append("--no-descriptions")
        if not args.no_ids:
            beautify_args.append("--ids")
        pretty_code, pretty_stdout, pretty_stderr = _run_entrypoint(
            _list_beautify.main, beautify_args, stdin=read_stdout
        )

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                if pretty_stdout:
                    f.write(pretty_stdout)
        else:
            if pretty_stdout:
                print(pretty_stdout, end="")

        if pretty_stderr:
            print(pretty_stderr, end="", file=sys.stderr)
        return pretty_code
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
