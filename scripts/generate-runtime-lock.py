#!/usr/bin/env python3
"""Generate or offline-check the blueprint-derived core runtime lock."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPO_ROOT, REPO_ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from officina.install.install_info import InstallInfoError, load_install_info
from officina.install.runtime_lock import (
    RuntimeLockError,
    generate_runtime_lock,
    validate_runtime_lock,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate checked-in input and lock without network or resolution.",
    )
    parser.add_argument(
        "--uv",
        type=Path,
        help="Path to the exact uv binary pinned by install-info.toml (required to generate).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.check and args.uv is None:
        raise SystemExit("--uv is required unless --check is used")

    manifest_path = REPO_ROOT / "references" / "blueprint-schema" / "runtime_dependencies.json"
    input_path = REPO_ROOT / "references" / "runtime" / "requirements-core.in"
    lock_path = REPO_ROOT / "references" / "runtime" / "requirements-core.lock"
    try:
        info = load_install_info(REPO_ROOT)
        if args.check:
            metadata = validate_runtime_lock(
                manifest_path=manifest_path,
                input_path=input_path,
                lock_path=lock_path,
                expected_uv_version=info.uv_version,
                expected_python_version=info.managed_python,
            )
        else:
            metadata = generate_runtime_lock(
                manifest_path=manifest_path,
                input_path=input_path,
                lock_path=lock_path,
                uv_bin=args.uv,
                expected_uv_version=info.uv_version,
                python_version=info.managed_python,
            )
    except (InstallInfoError, RuntimeLockError) as exc:
        print(f"runtime lock error: {exc}", file=sys.stderr)
        return 1

    print(
        f"runtime core lock verified: uv={metadata.uv_version} "
        f"python={metadata.python_version} sha256={metadata.lock_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
