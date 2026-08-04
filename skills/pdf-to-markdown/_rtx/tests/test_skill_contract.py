from __future__ import annotations

import importlib.util
from pathlib import Path


SOURCE_FETCHER_PATH = Path(__file__).resolve().parents[1] / "_source_fetcher.py"


def _load_source_fetcher():
    spec = importlib.util.spec_from_file_location(
        "pdf_to_markdown_source_fetcher_tests",
        SOURCE_FETCHER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load source fetcher from {SOURCE_FETCHER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_fetcher_parser_defaults_omitted_output_directory() -> None:
    source_fetcher = _load_source_fetcher()

    args = source_fetcher.Interface().build_parser().parse_args(["1234.5678"])

    assert args.arxiv_id == "1234.5678"
    assert args.output_dir == "."


def test_source_fetcher_parser_accepts_explicit_output_directory() -> None:
    source_fetcher = _load_source_fetcher()

    args = source_fetcher.Interface().build_parser().parse_args(
        ["1234.5678", "paper-source"]
    )

    assert args.arxiv_id == "1234.5678"
    assert args.output_dir == "paper-source"
