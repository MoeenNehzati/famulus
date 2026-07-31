#!/usr/bin/env python3
"""Parser helpers for extracting docstring metadata from Python modules."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from ...docstring import (
    FunctionSpec,
    PipelineSpec,
    parse_graph_block,
    parse_function_graphs,
    parse_pipeline,
)


def parse_module(module_path: str | Path) -> Any:
    """Parse a Python module into an AST."""
    source_path = Path(module_path)
    source = source_path.read_text(encoding="utf-8")
    return ast.parse(source)


def parse_docstring_module(
    module_path: str | Path,
    *,
    include_undocumented: bool = False,
) -> tuple[PipelineSpec, dict[str, FunctionSpec]]:
    """Parse module-level and callable docstrings for one module."""
    tree = parse_module(module_path)
    module_doc = ast.get_docstring(tree) or ""
    pipeline = parse_pipeline(module_doc)
    if include_undocumented:
        callables = _collect_function_specs(tree)
    else:
        callables = parse_function_graphs(tree)
    return pipeline, callables


def _signature_from_callable_node(node: Any) -> str:
    """Render a function signature for a callable node."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ""

    args = node.args
    parts: list[str] = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults = list(args.defaults)
    pad = max(0, len(positional) - len(defaults))

    def _fmt_arg(argument: ast.arg) -> str:
        text = argument.arg
        if argument.annotation is not None:
            try:
                annotation = ast.unparse(argument.annotation)
            except Exception:
                annotation = None
            if annotation is not None:
                text = f"{text}: {annotation}"
        return text

    for idx, argument in enumerate(positional):
        text = _fmt_arg(argument)
        default = defaults[idx - pad] if idx >= pad else None
        if default is not None:
            try:
                rendered_default = ast.unparse(default)
            except Exception:
                rendered_default = "..."
            text = f"{text}={rendered_default}"
        parts.append(text)

    if args.vararg is not None:
        parts.append(f"*{_fmt_arg(args.vararg)}")

    for idx, argument in enumerate(args.kwonlyargs):
        text = _fmt_arg(argument)
        default = args.kw_defaults[idx]
        if default is not None:
            try:
                rendered_default = ast.unparse(default)
            except Exception:
                rendered_default = "..."
            text = f"{text}={rendered_default}"
        parts.append(text)

    if args.kwarg is not None:
        parts.append(f"**{_fmt_arg(args.kwarg)}")

    signature = f"{node.name}(" + ", ".join(parts) + ")"
    if node.returns is not None:
        try:
            returns = ast.unparse(node.returns)
            signature += f" -> {returns}"
        except Exception:
            pass
    return signature


def _collect_function_specs(tree: Any) -> dict[str, FunctionSpec]:
    """Build specs for all callables, including undocumented ones."""
    specs: dict[str, FunctionSpec] = {}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            if doc:
                spec = parse_graph_block(doc)
            else:
                spec = FunctionSpec()
                spec.summary = "(Undocumented callable)"
            spec.signature = _signature_from_callable_node(node)
            specs[node.name] = spec
            continue

        if isinstance(node, ast.ClassDef):
            # Capture class docs as a top-level class entity even without methods.
            doc = ast.get_docstring(node)
            if doc:
                spec = parse_graph_block(doc)
            else:
                spec = FunctionSpec()
                spec.summary = "(Undocumented class)"
            if not spec.signature:
                spec.signature = f"{node.name}()"
            specs[node.name] = spec

            for child in node.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                doc = ast.get_docstring(child)
                if doc:
                    method_spec = parse_graph_block(doc)
                else:
                    method_spec = FunctionSpec()
                    method_spec.summary = "(Undocumented method)"
                if not method_spec.signature:
                    method_spec.signature = _signature_from_callable_node(child)
                specs[f"{node.name}.{child.name}"] = method_spec

    return specs


def collect_defined_callables(
    module_path: str | Path,
    *,
    tree: Any | None = None,
    function_specs: dict[str, FunctionSpec] | None = None,
) -> tuple[str, ...]:
    """Collect declared callables from a module."""
    if function_specs is not None:
        names = function_specs.keys()
    else:
        parsed_tree = tree if tree is not None else parse_module(module_path)
        names = parse_function_graphs(parsed_tree).keys()
    return tuple(sorted(names))


def infer_call_edges(
    module_path: str | Path,
    *,
    function_specs: dict[str, FunctionSpec] | None = None,
) -> dict[str, set[str]]:
    """Infer local function-to-function references from AST calls."""
    tree = parse_module(module_path)
    parent_by_child: dict[Any, Any] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_by_child[child] = parent

    specs = function_specs if function_specs is not None else parse_function_graphs(tree)
    if not specs:
        return {}

    known = set(specs.keys())
    short_names: dict[str, list[str]] = {}
    for name in known:
        short_names.setdefault(name.rsplit(".", 1)[-1], []).append(name)

    def resolve_called_name(raw_name: str) -> str | None:
        if raw_name in known:
            return raw_name
        matches = short_names.get(raw_name, [])
        if len(matches) == 1:
            return matches[0]
        return None

    edges: dict[str, set[str]] = {name: set() for name in specs}
    callable_nodes: dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            callable_nodes[node.name] = node
        elif isinstance(node, ast.ClassDef):
            callable_nodes[node.name] = node
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    callable_nodes[f"{node.name}.{child.name}"] = child

    class_names = {name for name in specs if "." not in name and name and name[0].isupper()}
    for source_name in specs:
        source_node = callable_nodes.get(source_name)
        if source_node is None:
            continue

        for sub_node in ast.walk(source_node):
            if not isinstance(sub_node, ast.Call):
                continue
            if isinstance(parent_by_child.get(sub_node), ast.Raise):
                continue
            callee = sub_node.func
            called_name: str | None = None
            if isinstance(callee, ast.Name):
                called_name = callee.id
            elif isinstance(callee, ast.Attribute):
                called_name = callee.attr
            if called_name is None:
                continue
            target = resolve_called_name(called_name)
            if target is None:
                continue
            if target in class_names and called_name == target:
                continue
            if target == source_name:
                continue
            edges.setdefault(source_name, set()).add(target)

        if not edges[source_name]:
            edges.pop(source_name, None)

    return edges
