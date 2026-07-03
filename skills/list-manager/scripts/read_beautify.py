#!/usr/bin/env python3
"""Read a local or cloud-backed structured list file and immediately render it for display."""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
LISTS_PY = SKILL_ROOT / "scripts" / "lists.py"
BEAUTIFY_PY = SKILL_ROOT / "scripts" / "beautify.py"


def get_invoke_skill_export() -> Path:
    """Locate invoke_skill_export.py from repo root."""
    repo_root = SKILL_ROOT.parent.parent.parent
    script = repo_root / "scripts" / "invoke_skill_export.py"
    if not script.exists():
        alt = Path.home() / "Documents" / "AI" / "scripts" / "invoke_skill_export.py"
        if alt.exists():
            return alt
    return script


def download_list(list_name: str, dest_path: Path) -> None:
    """Download list from cloud storage via cloud-files lists-read interface."""
    remote_path = f"lists/{list_name}.yaml"
    cmd = [
        "python3",
        str(get_invoke_skill_export()),
        "--caller-skill", "list-manager",
        "cloud-files", "lists-read",
        remote_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0:
            print(f"error: failed to download {remote_path}: {result.stderr}", file=sys.stderr)
            sys.exit(1)
        with open(dest_path, "w", encoding="utf-8") as f:
            f.write(result.stdout)
    except subprocess.TimeoutExpired:
        print("error: download timed out", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"error: download failed: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(prog="read_beautify.py")
    parser.add_argument("file", help="Path to local YAML list file, or cloud list name with --cloud")
    parser.add_argument("filters", nargs="*", help="key=value (exact/OR) or key~=value (regex) filters")
    parser.add_argument("--cloud", action="store_true", help="Treat the source as a cloud list name and download it")
    parser.add_argument("--sort", metavar="FIELD", help="Sort results by field before rendering")
    parser.add_argument("-D", "--no-descriptions", action="store_true", help="Hide entry descriptions")
    parser.add_argument("--markdown", action="store_true", help="Render markdown instead of diff")
    parser.add_argument("--no-ids", action="store_true", help="Do not append each entry's #id (ids are shown by default)")
    parser.add_argument("-o", "--output", metavar="FILE", help="Write beautified output to file instead of stdout")
    args = parser.parse_args()

    # Cloud mode: the source positional is a list name → download → read → beautify
    file_to_read = args.file
    temp_path = None

    if args.cloud:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
            temp_path = Path(tmp.name)
        download_list(args.file, temp_path)
        file_to_read = str(temp_path)

    try:
        read_cmd = [sys.executable, str(LISTS_PY), "read", file_to_read]
        if args.sort:
            read_cmd.extend(["--sort", args.sort])
        read_cmd.extend(args.filters)

        read_result = subprocess.run(read_cmd, capture_output=True, text=True, check=False)
        if read_result.returncode != 0:
            if read_result.stdout:
                print(read_result.stdout, end="")
            if read_result.stderr:
                print(read_result.stderr, end="", file=sys.stderr)
            return read_result.returncode

        beautify_cmd = [sys.executable, str(BEAUTIFY_PY), "--relative-deadlines"]
        beautify_cmd.append("--markdown" if args.markdown else "--diff")
        if args.no_descriptions:
            beautify_cmd.append("--no-descriptions")
        if not args.no_ids:
            beautify_cmd.append("--ids")

        pretty = subprocess.run(
            beautify_cmd,
            input=read_result.stdout,
            capture_output=True,
            text=True,
            check=False,
        )

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                if pretty.stdout:
                    f.write(pretty.stdout)
        else:
            if pretty.stdout:
                print(pretty.stdout, end="")

        if pretty.stderr:
            print(pretty.stderr, end="", file=sys.stderr)
        return pretty.returncode
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
