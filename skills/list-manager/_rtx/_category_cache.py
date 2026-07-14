"""Cloud category-path cache for list-manager."""
from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from _rtx import _cloud_transport as cloud_transport


CACHE_DIR = Path(__file__).resolve().parents[1] / "tmp"
DEFAULT_REMAINING_USES = 20


def cache_path(name: str, cache_dir: Path = CACHE_DIR) -> Path:
    return cache_dir / f"categories.{name}.yaml"


def extract_category_paths(categories: list[dict[str, Any]], prefix: str = "") -> list[str]:
    paths: list[str] = []
    for category in categories:
        name = category.get("name")
        if not isinstance(name, str) or not name:
            continue
        path = f"{prefix}/{name}" if prefix else name
        paths.append(path)
        children = category.get("categories", [])
        if isinstance(children, list):
            paths.extend(extract_category_paths(children, path))
    return paths


def read_cache(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("name"), str):
        return None
    if not isinstance(data.get("paths"), list) or not all(isinstance(item, str) for item in data["paths"]):
        return None
    if not isinstance(data.get("remaining_uses"), int):
        return None
    return data


def write_cache(path: Path, name: str, paths: list[str], remaining_uses: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "name": name,
        "paths": paths,
        "remaining_uses": remaining_uses,
        "reset_uses": DEFAULT_REMAINING_USES,
    }
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(data, stream, allow_unicode=True, default_flow_style=False, sort_keys=False)
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def fetch_category_paths(name: str) -> list[str]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        source_path = Path(temporary_directory) / f"{name}.yaml"
        cloud_transport.download_list(name, source_path)
        with source_path.open(encoding="utf-8") as stream:
            document = yaml.safe_load(stream) or {}
    if not isinstance(document, dict):
        raise ValueError(f"cloud list '{name}' is not a YAML object")
    categories = document.get("categories", [])
    if not isinstance(categories, list):
        raise ValueError(f"cloud list '{name}' has invalid categories")
    return extract_category_paths(categories)


def load_paths(
    name: str,
    *,
    refresh: bool = False,
    cache_dir: Path = CACHE_DIR,
) -> tuple[list[str], int]:
    path = cache_path(name, cache_dir)
    cached = read_cache(path)
    if refresh:
        paths = fetch_category_paths(name)
        write_cache(path, name, paths, DEFAULT_REMAINING_USES)
        return paths, DEFAULT_REMAINING_USES
    if cached is None or cached["name"] != name or cached["remaining_uses"] <= 0:
        paths = fetch_category_paths(name)
        remaining_uses = DEFAULT_REMAINING_USES - 1
        write_cache(path, name, paths, remaining_uses)
        return paths, remaining_uses
    remaining_uses = cached["remaining_uses"] - 1
    write_cache(path, name, cached["paths"], remaining_uses)
    return cached["paths"], remaining_uses


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="list-manager cloud-list-categories")
    parser.add_argument("name", help="Cloud list name")
    parser.add_argument("--cloud", action="store_true", required=True)
    parser.add_argument("--refresh", action="store_true", help="Refresh cached category paths")
    return parser


class Interface(PythonArgvMachineInterface):
    dispatches = cloud_transport.DISPATCHES
    prog = "list-manager cloud-list-categories"

    def run(self, argv: list[str]) -> int:
        args = build_parser().parse_args(argv)
        paths, remaining_uses = load_paths(args.name, refresh=args.refresh)
        print(
            yaml.safe_dump(
                {"name": args.name, "paths": paths, "remaining_uses": remaining_uses},
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            ),
            end="",
        )
        return 0
