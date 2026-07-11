"""Download and extract arXiv source archives."""
from __future__ import annotations

import argparse
import gzip
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

from officina.runtime.python_machine_interface import PythonMachineInterface


class Interface(PythonMachineInterface):
    prog = "fetch-arxiv-source"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("arxiv_id")
        parser.add_argument("output_dir", nargs="?", default=".")
        return parser

    def run(self, args: argparse.Namespace) -> int:
        outdir = Path(args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        tarball = outdir / "source.tar.gz"
        with urllib.request.urlopen(f"https://arxiv.org/src/{args.arxiv_id}", timeout=60) as response:
            with tarball.open("wb") as handle:
                shutil.copyfileobj(response, handle)

        if not _is_gzip(tarball):
            tarball.unlink(missing_ok=True)
            print(
                f"No LaTeX source available on arXiv for {args.arxiv_id} (PDF-only submission).",
                file=sys.stderr,
            )
            return 1

        with tarfile.open(tarball, "r:gz") as archive:
            archive.extractall(outdir)
        tarball.unlink(missing_ok=True)

        print(f"Extracted to: {outdir}")
        print("TeX files:")
        for tex_file in sorted(outdir.rglob("*.tex")):
            print(tex_file)
        return 0


def _is_gzip(path: Path) -> bool:
    try:
        with gzip.open(path, "rb") as handle:
            handle.peek(1)
        return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    interface = Interface()
    parser = interface.build_parser()
    return interface.run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
