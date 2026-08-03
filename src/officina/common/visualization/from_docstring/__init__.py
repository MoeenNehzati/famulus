"""Docstring-driven graph extraction, rendering, and orchestration."""

from .json_extractor import (
    DEFAULT_FORMATS,
    DocstringJsonExtractor,
    collect_defined_callables,
    default_out_dir,
    extract_docstring_dependency_json,
    gather_modules,
    gather_modules_in_directory,
    infer_call_edges,
    parse_docstring_module,
    parse_module,
    to_dependency_json,
)
from .renderer import (
    DocstringRenderer,
)
from .visualizer import (
    DocstringVisualizer,
    build_docstring_graph,
    main,
    render_module_artifacts,
)

__all__ = [
    "DEFAULT_FORMATS",
    "DocstringVisualizer",
    "DocstringJsonExtractor",
    "DocstringRenderer",
    "build_docstring_graph",
    "collect_defined_callables",
    "default_out_dir",
    "gather_modules",
    "gather_modules_in_directory",
    "infer_call_edges",
    "main",
    "render_module_artifacts",
    "parse_docstring_module",
    "parse_module",
    "to_dependency_json",
    "extract_docstring_dependency_json",
]
